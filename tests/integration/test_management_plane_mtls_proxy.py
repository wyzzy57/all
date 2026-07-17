"""Real nginx mTLS integration test for the management-plane boundary.

Run directly so it can create and remove disposable Docker resources:
    python tests/integration/test_management_plane_mtls_proxy.py
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import ssl
import subprocess
import tempfile
import time
import unittest
import uuid
from http.client import HTTPSConnection
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
PROXY_CONFIG = ROOT / "infra" / "proxy" / "nginx.conf"
PRODUCTION_OVERLAY = ROOT / "infra" / "compose" / "docker-compose.production-mtls.yml"
TEST_COMPOSE = ROOT / "tests" / "integration" / "management_plane_mtls" / "docker-compose.test.yml"

MANAGEMENT_REQUESTS = (
    ("POST", "/agent/v1/enrollment-tokens"),
    ("GET", "/nodes"),
    ("GET", "/nodes/node-123"),
    ("POST", "/nodes/node-123/drain"),
    ("GET", "/resource-pools"),
)


class CommandError(AssertionError):
    pass


def run_command(
    args: list[str],
    *,
    cwd: Path = ROOT,
    env: dict[str, str] | None = None,
    check: bool = True,
    redact_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if check and result.returncode:
        detail = "output redacted" if redact_output else result.stdout.strip()
        raise CommandError(f"command failed ({result.returncode}): {' '.join(args)}\n{detail}")
    return result


class ManagementPlaneMtlsProxyIntegrationTest(unittest.TestCase):
    temp_dir: Path
    project_name: str
    compose_env: dict[str, str]
    proxy_port: int
    created_compose_env_file = False

    @classmethod
    def setUpClass(cls) -> None:
        cls._assert_production_assets_exist()
        cls._ensure_compose_env_file()
        cls.addClassCleanup(cls._remove_created_compose_env_file)
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="visiox-mtls-proxy-"))
        cls.project_name = f"visioxmtls{uuid.uuid4().hex[:12]}"
        cls.compose_env = os.environ.copy()
        cls.compose_env["VISIOX_MTLS_TEST_CERT_DIR"] = str(cls.temp_dir)
        cls.addClassCleanup(cls._cleanup_resources)
        cls._generate_certificates()
        cls._validate_production_compose()
        cls._start_proxy()
        cls.proxy_port = cls._published_proxy_port()
        cls._wait_until_ready()

    @classmethod
    def _assert_production_assets_exist(cls) -> None:
        for path in (PROXY_CONFIG, PRODUCTION_OVERLAY, TEST_COMPOSE):
            if not path.is_file():
                raise AssertionError(f"required management-plane mTLS asset is missing: {path}")

    @classmethod
    def _ensure_compose_env_file(cls) -> None:
        compose_env_file = ROOT / ".env"
        if compose_env_file.exists():
            return
        compose_env_file.write_text("", encoding="utf-8")
        cls.created_compose_env_file = True

    @classmethod
    def _remove_created_compose_env_file(cls) -> None:
        if cls.created_compose_env_file:
            (ROOT / ".env").unlink(missing_ok=True)

    @classmethod
    def _generate_certificates(cls) -> None:
        certificate_script = """
set -eu
apk add --no-cache openssl >/dev/null
cd /certs

make_ca() {
  name=$1
  openssl genrsa -out "${name}.key" 2048 >/dev/null 2>&1
  openssl req -x509 -new -sha256 -key "${name}.key" -days 1 -out "${name}.crt" \\
    -subj "/CN=${name}" \\
    -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \\
    -addext "keyUsage=critical,keyCertSign,cRLSign" >/dev/null 2>&1
}

make_leaf() {
  name=$1
  issuer=$2
  usage=$3
  sans=$4
  openssl genrsa -out "${name}.key" 2048 >/dev/null 2>&1
  openssl req -new -key "${name}.key" -out "${name}.csr" -subj "/CN=${name}" >/dev/null 2>&1
  {
    printf '%s\\n' 'basicConstraints=critical,CA:FALSE'
    printf '%s\\n' 'keyUsage=critical,digitalSignature,keyEncipherment'
    printf 'extendedKeyUsage=%s\\n' "${usage}"
    if [ -n "${sans}" ]; then printf 'subjectAltName=%s\\n' "${sans}"; fi
  } > "${name}.ext"
  openssl x509 -req -in "${name}.csr" -CA "${issuer}.crt" -CAkey "${issuer}.key" \\
    -CAcreateserial -days 1 -sha256 -out "${name}.crt" -extfile "${name}.ext" >/dev/null 2>&1
  rm -f "${name}.csr" "${name}.ext" "${issuer}.srl"
}

make_ca server-ca
make_ca operator-ca
make_ca untrusted-ca
make_leaf server server-ca serverAuth 'DNS:localhost,IP:127.0.0.1'
make_leaf trusted-client operator-ca clientAuth ''
make_leaf untrusted-client untrusted-ca clientAuth ''
"""
        run_command(
            [
                "docker",
                "run",
                "--rm",
                "--mount",
                f"type=bind,source={cls.temp_dir},target=/certs",
                "alpine:3.20",
                "sh",
                "-ec",
                certificate_script,
            ],
            redact_output=True,
        )

    @classmethod
    def _validate_production_compose(cls) -> None:
        config_env = cls.compose_env.copy()
        config_env.update(
            {
                "VISIOX_MANAGEMENT_PROXY_PORT": "8443",
                "VISIOX_MANAGEMENT_TLS_CERT_PATH": str(cls.temp_dir / "server.crt"),
                "VISIOX_MANAGEMENT_TLS_KEY_PATH": str(cls.temp_dir / "server.key"),
                "VISIOX_MANAGEMENT_OPERATOR_CA_PATH": str(cls.temp_dir / "operator-ca.crt"),
                "VISIOX_AGENT_PUBLIC_WS_URL": "wss://visiox-control.test/agent/v1/connect",
            }
        )
        missing_wss_env = config_env.copy()
        missing_wss_env["VISIOX_AGENT_PUBLIC_WS_URL"] = ""
        missing_wss_result = run_command(
            [
                "docker",
                "compose",
                "-f",
                "infra/compose/docker-compose.yml",
                "-f",
                "infra/compose/docker-compose.production-mtls.yml",
                "config",
                "--format",
                "json",
            ],
            env=missing_wss_env,
            check=False,
        )
        if missing_wss_result.returncode == 0:
            raise AssertionError("production overlay must require VISIOX_AGENT_PUBLIC_WS_URL")
        result = run_command(
            [
                "docker",
                "compose",
                "-f",
                "infra/compose/docker-compose.yml",
                "-f",
                "infra/compose/docker-compose.production-mtls.yml",
                "config",
                "--format",
                "json",
            ],
            env=config_env,
        )
        try:
            resolved = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise AssertionError("production Compose did not render JSON configuration") from error

        services = resolved["services"]
        api_service = services["api-service"]
        if api_service.get("ports"):
            raise AssertionError("api-service still publishes a host port")
        published_ports = {
            service_name: service.get("ports", [])
            for service_name, service in services.items()
            if service.get("ports")
        }
        if set(published_ports) != {"management-proxy"}:
            raise AssertionError(f"production merge publishes non-proxy ports: {published_ports}")
        if len(published_ports["management-proxy"]) != 1:
            raise AssertionError("management-proxy must publish exactly one port")
        target = published_ports["management-proxy"][0].get("target")
        if target != 8443:
            raise AssertionError("proxy must publish its TLS listener")
        api_networks = set(api_service.get("networks", {}))
        expected_api_networks = {"api-dependencies", "management-backend"}
        if api_networks != expected_api_networks:
            raise AssertionError(
                f"api-service must be limited to its dependencies and proxy network: {api_networks}"
            )
        api_dependency_services = {
            service_name
            for service_name, service in services.items()
            if "api-dependencies" in set(service.get("networks", {}))
        }
        expected_api_dependency_services = {
            "api-service",
            "postgres",
            "redis",
            "minio",
            "registry",
            "label-studio",
            "mlflow",
        }
        if api_dependency_services != expected_api_dependency_services:
            raise AssertionError(
                f"api dependency network has unexpected members: {api_dependency_services}"
            )
        proxy_volumes = {
            volume["target"]: volume
            for volume in services["management-proxy"].get("volumes", [])
        }
        for target_path in (
            "/etc/nginx/tls/server.crt",
            "/etc/nginx/tls/server.key",
            "/etc/nginx/mtls/operator-ca.crt",
        ):
            volume = proxy_volumes.get(target_path)
            if volume is None or not volume.get("read_only"):
                raise AssertionError(f"required read-only proxy mount missing: {target_path}")
        networks = resolved.get("networks", {})
        if not networks.get("management-backend", {}).get("internal"):
            raise AssertionError("management-backend must be an internal network")
        if not networks.get("api-dependencies", {}).get("internal"):
            raise AssertionError("api-dependencies must be an internal network")
        if networks.get("management-public", {}).get("internal"):
            raise AssertionError("management-public must allow the TLS proxy to accept edge traffic")

    @classmethod
    def _start_proxy(cls) -> None:
        run_command(
            [
                "docker",
                "compose",
                "-p",
                cls.project_name,
                "-f",
                str(TEST_COMPOSE),
                "up",
                "-d",
                "--wait",
            ],
            env=cls.compose_env,
        )

    @classmethod
    def _published_proxy_port(cls) -> int:
        result = run_command(
            [
                "docker",
                "compose",
                "-p",
                cls.project_name,
                "-f",
                str(TEST_COMPOSE),
                "port",
                "management-proxy",
                "8443",
            ],
            env=cls.compose_env,
        )
        published = result.stdout.strip()
        try:
            return int(published.rsplit(":", 1)[1])
        except (IndexError, ValueError) as error:
            raise AssertionError(f"could not parse published proxy port: {published}") from error

    @classmethod
    def _wait_until_ready(cls) -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                status, _ = cls._request("POST", "/agent/v1/enroll")
                if status == 200:
                    return
            except OSError:
                pass
            time.sleep(0.25)
        raise AssertionError("nginx proxy did not become ready within 30 seconds")

    @classmethod
    def _tls_context(cls, client_name: str | None = None) -> ssl.SSLContext:
        context = ssl.create_default_context(cafile=cls.temp_dir / "server-ca.crt")
        if client_name:
            context.load_cert_chain(
                certfile=cls.temp_dir / f"{client_name}.crt",
                keyfile=cls.temp_dir / f"{client_name}.key",
            )
        return context

    @classmethod
    def _request(
        cls,
        method: str,
        path: str,
        client_name: str | None = None,
    ) -> tuple[int, dict[str, Any] | None]:
        body = b"{}" if method == "POST" else None
        headers = {"Host": "visiox-control.test"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        connection = HTTPSConnection(
            "127.0.0.1",
            cls.proxy_port,
            context=cls._tls_context(client_name),
            timeout=5,
        )
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            payload = response.read()
            if response.getheader("Content-Type", "").startswith("application/json"):
                return response.status, json.loads(payload)
            return response.status, None
        finally:
            connection.close()

    @classmethod
    def _cleanup_resources(cls) -> None:
        compose_command = [
            "docker",
            "compose",
            "-p",
            cls.project_name,
            "-f",
            str(TEST_COMPOSE),
            "down",
            "--volumes",
            "--remove-orphans",
        ]
        cleanup_errors: list[str] = []
        for _ in range(2):
            result = run_command(compose_command, env=cls.compose_env, check=False)
            if result.returncode:
                cleanup_errors.append(result.stdout.strip())
        for network in (f"{cls.project_name}_public", f"{cls.project_name}_control-plane"):
            remaining = run_command(["docker", "network", "inspect", network], check=False)
            if remaining.returncode == 0:
                cleanup_errors.append(f"test network remains: {network}")
        remaining_containers = run_command(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                f"label=com.docker.compose.project={cls.project_name}",
                "--format",
                "{{.ID}}",
            ],
            check=False,
        )
        if remaining_containers.returncode:
            cleanup_errors.append("could not inspect remaining test containers")
        elif remaining_containers.stdout.strip():
            cleanup_errors.append("test container remains")
        shutil.rmtree(cls.temp_dir, ignore_errors=True)
        if cls.temp_dir.exists():
            cleanup_errors.append("ephemeral certificate directory remains")
        if cleanup_errors:
            raise AssertionError("; ".join(error for error in cleanup_errors if error))

    def _assert_reached_backend(self, status: int, payload: dict[str, Any] | None, method: str, path: str) -> None:
        self.assertEqual(status, 200)
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["method"], method)
        self.assertEqual(payload["path"], path)
        self.assertEqual(payload["headers"]["host"], "visiox-control.test")
        self.assertEqual(payload["headers"]["x-forwarded-proto"], "https")
        self.assertTrue(payload["headers"]["x-forwarded-for"])

    def test_protected_routes_require_trusted_operator_certificate(self) -> None:
        for method, path in MANAGEMENT_REQUESTS:
            with self.subTest(method=method, path=path, certificate="none"):
                status, _ = self._request(method, path)
                self.assertIn(status, {401, 403})
            with self.subTest(method=method, path=path, certificate="trusted"):
                status, payload = self._request(method, path, "trusted-client")
                self._assert_reached_backend(status, payload, method, path)

    def test_untrusted_operator_certificate_fails_before_backend(self) -> None:
        try:
            status, payload = self._request("GET", "/nodes", "untrusted-client")
        except ssl.SSLError:
            return
        self.assertNotEqual(status, 200)
        self.assertIsNone(payload)

    def test_public_enrollment_reaches_backend_without_operator_certificate(self) -> None:
        status, payload = self._request("POST", "/agent/v1/enroll")
        self._assert_reached_backend(status, payload, "POST", "/agent/v1/enroll")

    def test_websocket_upgrade_is_forwarded_without_operator_certificate(self) -> None:
        request = (
            "GET /agent/v1/connect HTTP/1.1\r\n"
            "Host: visiox-control.test\r\n"
            "Connection: Upgrade\r\n"
            "Upgrade: websocket\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n"
        ).encode("ascii")
        with socket.create_connection(("127.0.0.1", self.proxy_port), timeout=5) as raw_socket:
            with self._tls_context().wrap_socket(raw_socket, server_hostname="localhost") as tls_socket:
                tls_socket.sendall(request)
                response = tls_socket.recv(4096).decode("iso-8859-1")

        lowered = response.lower()
        self.assertTrue(response.startswith("HTTP/1.1 101"), response)
        self.assertIn("x-backend-upgrade: websocket", lowered)
        self.assertIn("x-backend-connection: upgrade", lowered)
        self.assertIn("x-backend-forwarded-proto: https", lowered)
        self.assertIn("x-backend-forwarded-for:", lowered)
        self.assertIn("x-backend-host: visiox-control.test", lowered)

    def test_unknown_management_variants_are_denied(self) -> None:
        for method, path in (
            ("POST", "/agent/v1/enrollment-tokens/extra"),
            ("GET", "/nodes/node-123/extra"),
            ("GET", "/admin/metrics"),
        ):
            with self.subTest(method=method, path=path, certificate="none"):
                status, _ = self._request(method, path)
                self.assertIn(status, {401, 403})
            with self.subTest(method=method, path=path, certificate="trusted"):
                status, payload = self._request(method, path, "trusted-client")
                self.assertIn(status, {401, 403})
                self.assertIsNone(payload)

    def test_public_network_cannot_resolve_backend_service(self) -> None:
        result = run_command(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                f"{self.project_name}_public",
                "python:3.12-alpine",
                "python",
                "-c",
                "import socket; socket.gethostbyname('api-service')",
            ],
            check=False,
        )
        self.assertNotEqual(result.returncode, 0, result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
