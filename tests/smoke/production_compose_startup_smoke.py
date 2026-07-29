"""Run a disposable production Compose startup against an empty PostgreSQL volume."""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.x509.oid import NameOID


ROOT = Path(__file__).resolve().parents[2]
BASE_COMPOSE = ROOT / "infra" / "compose" / "docker-compose.yml"
PRODUCTION_COMPOSE = ROOT / "infra" / "compose" / "docker-compose.production-mtls.yml"


class SmokeFailure(RuntimeError):
    pass


def _run(
    args: list[str],
    *,
    env: dict[str, str],
    check: bool = True,
    timeout: int = 1_800,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=timeout,
    )
    if check and result.returncode:
        output = result.stdout[-8_000:]
        raise SmokeFailure(
            f"command failed ({result.returncode}): {' '.join(args)}\n{output}"
        )
    return result


def _compose_args(project_name: str, override_path: Path) -> list[str]:
    return [
        "docker",
        "compose",
        "--ansi",
        "never",
        "-p",
        project_name,
        "-f",
        str(BASE_COMPOSE),
        "-f",
        str(PRODUCTION_COMPOSE),
        "-f",
        str(override_path),
    ]


def _write_agent_ca(directory: Path) -> tuple[Path, Path]:
    key = ed25519.Ed25519PrivateKey.generate()
    now = datetime.now(UTC)
    subject = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "Visiox Smoke Agent CA")]
    )
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                key_cert_sign=True,
                crl_sign=True,
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, algorithm=None)
    )
    cert_path = directory / "agent-ca.crt"
    key_path = directory / "agent-ca.key"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


def _wait_for_running_services(
    compose: list[str],
    env: dict[str, str],
    services: tuple[str, ...],
    timeout_seconds: int = 120,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    missing = set(services)
    while time.monotonic() < deadline:
        running = set(
            _run(
                [*compose, "ps", "--status", "running", "--services"],
                env=env,
            ).stdout.splitlines()
        )
        missing = set(services) - running
        if not missing:
            return
        time.sleep(1)
    raise SmokeFailure(f"services did not remain running: {sorted(missing)}")


def _assert_migration_completed(compose: list[str], env: dict[str, str]) -> None:
    container_id = _run(
        [*compose, "ps", "--all", "--quiet", "api-migrate"], env=env
    ).stdout.strip()
    if not container_id:
        raise SmokeFailure("api-migrate container was not created")
    state = _run(
        [
            "docker",
            "inspect",
            "--format",
            "{{.State.Status}} {{.State.ExitCode}}",
            container_id,
        ],
        env=env,
    ).stdout.strip()
    if state != "exited 0":
        raise SmokeFailure(f"api-migrate state is {state!r}, want 'exited 0'")


def _wait_for_api_health(
    compose: list[str],
    env: dict[str, str],
    timeout_seconds: int = 120,
) -> None:
    command = [
        *compose,
        "exec",
        "-T",
        "api-service",
        "python",
        "-c",
        (
            "import json, urllib.request; "
            "body=json.load(urllib.request.urlopen('http://127.0.0.1:8000/health')); "
            "assert body['status']=='ok'"
        ),
    ]
    deadline = time.monotonic() + timeout_seconds
    last_output = ""
    while time.monotonic() < deadline:
        result = _run(command, env=env, check=False)
        if result.returncode == 0:
            return
        last_output = result.stdout[-2_000:]
        time.sleep(1)
    raise SmokeFailure(f"api-service did not become healthy:\n{last_output}")


def _wait_for_gateway_dependencies(
    compose: list[str],
    env: dict[str, str],
    timeout_seconds: int = 120,
) -> None:
    command = [
        *compose,
        "exec",
        "-T",
        "label-studio-gateway",
        "python",
        "-c",
        (
            "import socket, urllib.request; "
            "socket.create_connection(('label-studio', 8080), timeout=5).close(); "
            "response=urllib.request.urlopen('http://api-service:8000/health', timeout=5); "
            "assert response.status == 200"
        ),
    ]
    deadline = time.monotonic() + timeout_seconds
    last_output = ""
    while time.monotonic() < deadline:
        result = _run(command, env=env, check=False)
        if result.returncode == 0:
            return
        last_output = result.stdout[-2_000:]
        time.sleep(1)
    raise SmokeFailure(
        "label-studio-gateway could not reach its internal dependencies:\n"
        f"{last_output}"
    )


def run_smoke() -> None:
    project_name = f"visioxproduction{uuid.uuid4().hex[:12]}"
    env = os.environ.copy()
    with tempfile.TemporaryDirectory(prefix="visiox-production-smoke-") as temp_value:
        temp_dir = Path(temp_value)
        cert_path, key_path = _write_agent_ca(temp_dir)
        token_path = temp_dir / "management-proxy-token"
        token_path.write_text(f"{'a' * 64}\n", encoding="ascii")
        edge_key_path = temp_dir / "edge-credential-master-key"
        edge_key_path.write_bytes(b"b" * 32)
        override_path = temp_dir / "docker-compose.smoke.yml"
        override_path.write_text(
            f"""services:
  api-migrate:
    environment:
      VISIOX_POSTGRES_DSN: postgresql+psycopg://visiox:visiox@postgres:5432/visiox
  api-service:
    environment:
      VISIOX_POSTGRES_DSN: postgresql+psycopg://visiox:visiox@postgres:5432/visiox
      VISIOX_SEED_BASE_MODELS_ON_STARTUP: "false"
  label-sync-worker:
    environment:
      VISIOX_POSTGRES_DSN: postgresql+psycopg://visiox:visiox@postgres:5432/visiox
  training-worker:
    environment:
      VISIOX_POSTGRES_DSN: postgresql+psycopg://visiox:visiox@postgres:5432/visiox
  edge-executor-worker:
    environment:
      VISIOX_POSTGRES_DSN: postgresql+psycopg://visiox:visiox@postgres:5432/visiox
      VISIOX_REDIS_URL: redis://redis:6379/0
secrets:
  edge_credential_master_key:
    file: "{edge_key_path.as_posix()}"
""",
            encoding="utf-8",
        )
        env.update(
            {
                "VISIOX_MANAGEMENT_TLS_CERT_PATH": str(cert_path),
                "VISIOX_MANAGEMENT_TLS_KEY_PATH": str(key_path),
                "VISIOX_MANAGEMENT_OPERATOR_CA_PATH": str(cert_path),
                "VISIOX_AGENT_CA_CERT_HOST_PATH": str(cert_path),
                "VISIOX_AGENT_CA_KEY_HOST_PATH": str(key_path),
                "VISIOX_MANAGEMENT_PROXY_AUTH_TOKEN_HOST_PATH": str(token_path),
                "VISIOX_AGENT_PUBLIC_WS_URL": (
                    "wss://visiox-production-smoke.invalid/agent/v1/connect"
                ),
            }
        )
        compose = _compose_args(project_name, override_path)
        try:
            _run(
                [
                    *compose,
                    "up",
                    "--detach",
                    "--build",
                    "api-service",
                    "label-sync-worker",
                    "training-worker",
                    "edge-executor-worker",
                    "label-studio-gateway",
                ],
                env=env,
            )
            _assert_migration_completed(compose, env)
            _wait_for_running_services(
                compose,
                env,
                (
                    "postgres",
                    "redis",
                    "api-service",
                    "label-sync-worker",
                    "training-worker",
                    "edge-executor-worker",
                    "label-studio",
                    "label-studio-gateway",
                ),
            )
            _wait_for_api_health(compose, env)
            _wait_for_gateway_dependencies(compose, env)
            for worker_name in (
                "label-sync-worker",
                "training-worker",
                "edge-executor-worker",
            ):
                _run(
                    [
                        *compose,
                        "exec",
                        "-T",
                        worker_name,
                        "python",
                        "-c",
                        (
                            "from visiox_common.settings import get_settings; "
                            "settings=get_settings(); "
                            "assert settings.environment=='production'; "
                            "assert settings.management_proxy_auth_token_file is None"
                        ),
                    ],
                    env=env,
                )
            _run(
                [
                    *compose,
                    "run",
                    "--rm",
                    "api-migrate",
                    "alembic",
                    "current",
                    "--check-heads",
                ],
                env=env,
            )
        finally:
            _run(
                [*compose, "down", "--volumes", "--remove-orphans"],
                env=env,
                check=False,
                timeout=300,
            )


if __name__ == "__main__":
    run_smoke()
    print("production Compose empty-volume startup smoke passed")
