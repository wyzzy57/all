from __future__ import annotations

from io import StringIO
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
import paramiko
import pytest

from visiox_edge_executor_worker.ssh import (
    HostKeyMismatchError,
    StrictSshClient,
    scan_host_key,
)


ROOT = Path(__file__).resolve().parents[3]
FIXTURE_ROOT = Path(__file__).parent
COMPOSE_FILE = FIXTURE_ROOT / "docker-compose.test.yml"
REMOTE_ROOT = ROOT / "workers" / "edge-executor-worker"
RUN_INTEGRATION = os.getenv("VISIOX_RUN_EDGE_SSH_INTEGRATION") == "1"


def _compose(
    *arguments: str, check: bool = True, timeout: float = 180
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), *arguments],
        check=check,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _published_port(service: str) -> int:
    output = _compose("port", service, "22").stdout.strip()
    return int(output.rsplit(":", 1)[1])


def _wait_for_fingerprint(port: int):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            return scan_host_key("127.0.0.1", port, 3)
        except (OSError, TimeoutError):
            time.sleep(0.25)
    raise AssertionError(f"SSH target on port {port} did not become ready")


def _private_key_pair() -> tuple[paramiko.Ed25519Key, str]:
    generated_key = ed25519.Ed25519PrivateKey.generate()
    private_key = generated_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    key = paramiko.Ed25519Key.from_private_key(StringIO(private_key))
    public_key = f"{key.get_name()} {key.get_base64()}"
    return key, public_key


def _upload_and_run(session, script: bytes, request: dict[str, object]):
    workspace = session.create_private_directory(timeout_seconds=5)
    script_path = str(PurePosixPath(workspace.path) / "operation.sh")
    request_path = str(PurePosixPath(workspace.path) / "request.json")
    try:
        session.upload_bytes_exclusive(
            script,
            script_path,
            expected_owner_uid=workspace.owner_uid,
            timeout_seconds=5,
        )
        session.upload_bytes_exclusive(
            json.dumps(request, separators=(",", ":"), sort_keys=True).encode(),
            request_path,
            expected_owner_uid=workspace.owner_uid,
            timeout_seconds=5,
        )
        result = session.run(
            f"/bin/bash {script_path} {request_path}",
            timeout_seconds=30,
        )
        return result
    finally:
        session.cleanup_private_directory(
            workspace,
            (script_path, request_path),
            timeout_seconds=5,
        )


def test_fixture_defines_two_isolated_ssh_targets_and_fake_runtime() -> None:
    compose = COMPOSE_FILE.read_text(encoding="utf-8")
    dockerfile = (FIXTURE_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "edge-a:" in compose and "edge-b:" in compose
    assert "edge-a-host-keys:" in compose and "edge-b-host-keys:" in compose
    assert compose.count("/etc/ssh/host-keys") >= 2
    assert "openssh-server" in dockerfile
    assert "fake-docker.py" in dockerfile
    assert "nvidia-smi" in dockerfile
    assert "PasswordAuthentication yes" in dockerfile


def test_smoke_assets_expose_failure_and_recovery_contracts() -> None:
    gloo = (ROOT / "tests" / "smoke" / "distributed_gloo_smoke.py").read_text(
        encoding="utf-8"
    )
    powershell = (ROOT / "scripts" / "smoke-edge-runtime.ps1").read_text(
        encoding="utf-8"
    )

    assert "torch.distributed" in gloo
    assert "all_reduce" in gloo
    assert "inject_failure" in gloo
    assert "peer_stop_observed" in gloo
    assert "recovery_succeeded" in gloo
    assert "VISIOX_RUN_EDGE_SSH_INTEGRATION" in powershell
    assert "ConvertTo-Json" in powershell
    assert '"PASS"' in powershell
    assert '"PENDING"' in powershell
    assert '"FAIL"' in powershell


@pytest.mark.skipif(
    not RUN_INTEGRATION,
    reason="set VISIOX_RUN_EDGE_SSH_INTEGRATION=1 to run disposable SSH targets",
)
def test_real_ssh_bootstrap_probe_transfer_rollback_and_stop() -> None:
    try:
        _compose("up", "--build", "--detach", "--wait", timeout=90)
    except (OSError, subprocess.SubprocessError) as error:
        pytest.skip(f"disposable SSH environment unavailable: {error}")

    client = StrictSshClient(
        connect_timeout_seconds=5,
        auth_timeout_seconds=5,
        banner_timeout_seconds=5,
    )
    try:
        port_a = _published_port("edge-a")
        port_b = _published_port("edge-b")
        fingerprint_a = _wait_for_fingerprint(port_a)
        fingerprint_b = _wait_for_fingerprint(port_b)
        assert fingerprint_a.fingerprint != fingerprint_b.fingerprint

        key, public_key = _private_key_pair()
        password_session = client.connect_password(
            host="127.0.0.1",
            port=port_a,
            username="root",
            password="visiox-smoke",
            expected_fingerprint=fingerprint_a.fingerprint,
        )
        try:
            bootstrap = _upload_and_run(
                password_session,
                (
                    REMOTE_ROOT
                    / "src"
                    / "visiox_edge_executor_worker"
                    / "remote"
                    / "bootstrap_user.sh"
                ).read_bytes(),
                {
                    "operation": "bootstrap_add",
                    "node_id": "smoke-node-a",
                    "key_version": 1,
                    "public_key": public_key,
                },
            )
            assert bootstrap.exit_status == 0, bootstrap.stderr.decode(errors="replace")
        finally:
            password_session.close()

        key_session = client.connect(
            host="127.0.0.1",
            port=port_a,
            username="visiox-edge",
            private_key=key,
            expected_fingerprint=fingerprint_a.fingerprint,
        )
        try:
            probe = key_session.run(
                "id -u",
                timeout_seconds=20,
            )
            assert probe.exit_status == 0
            assert probe.stdout.strip().isdigit()

            inventory_workspace = key_session.create_private_directory(timeout_seconds=5)
            inventory_path = str(PurePosixPath(inventory_workspace.path) / "probe.sh")
            try:
                key_session.upload_bytes_exclusive(
                    (REMOTE_ROOT / "remote" / "probe_inventory.sh").read_bytes(),
                    inventory_path,
                    expected_owner_uid=inventory_workspace.owner_uid,
                    timeout_seconds=5,
                )
                inventory_result = key_session.run(
                    f"/bin/bash {inventory_path}", timeout_seconds=30
                )
                inventory = json.loads(inventory_result.stdout)
                assert inventory_result.exit_status == 0
                assert inventory["docker"]["available"] is True
                assert inventory["nvidia"]["gpus"][0]["uuid"] == "GPU-fixture-0"
            finally:
                key_session.cleanup_private_directory(
                    inventory_workspace, (inventory_path,), timeout_seconds=5
                )

            runtime_result = _upload_and_run(
                key_session,
                (REMOTE_ROOT / "remote" / "inspect_runtime.sh").read_bytes(),
                {
                    "labels": {
                        "com.visiox.managed": "true",
                        "com.visiox.remote-execution-id": "exec-smoke",
                        "com.visiox.node-id": "smoke-node-a",
                    }
                },
            )
            runtime = json.loads(runtime_result.stdout)
            assert runtime_result.exit_status == 0
            assert len(runtime["containers"]) == 2

            rollback_request = {
                "action": "rollback",
                "current_container_id": "a" * 64,
                "labels": {
                    "com.visiox.managed": "true",
                    "com.visiox.deployment-instance-id": "instance-smoke",
                    "com.visiox.deployment-service-id": "service-smoke",
                    "com.visiox.node-id": "smoke-node-a",
                    "com.visiox.restart-policy": "unless-stopped",
                    "com.visiox.health-path": "/health",
                    "com.visiox.warmup-path": "/predict/image",
                },
                "target": {
                    "container_id": "b" * 64,
                    "image_digest": "registry.local/visiox@sha256:" + "c" * 64,
                    "model_checksum": "d" * 64,
                    "engine": "engine",
                    "engine_digest": "e" * 64,
                    "port": 18080,
                },
            }
            rollback_result = _upload_and_run(
                key_session,
                (REMOTE_ROOT / "remote" / "deploy_inference.sh").read_bytes(),
                rollback_request,
            )
            assert rollback_result.exit_status == 0, rollback_result.stderr.decode(
                errors="replace"
            )
            rollback = json.loads(rollback_result.stdout)
            assert rollback["container_id"] == "b" * 64
            assert rollback["health_status"] == "healthy"

            stop_result = _upload_and_run(
                key_session,
                (REMOTE_ROOT / "remote" / "stop_deployment.sh").read_bytes(),
                {"labels": {"com.visiox.deployment-instance-id": "instance-smoke"}},
            )
            assert stop_result.exit_status == 0
            assert json.loads(stop_result.stdout)["stopped_container_ids"] == ["b" * 64]
        finally:
            key_session.close()

        with pytest.raises(HostKeyMismatchError):
            client.connect(
                host="127.0.0.1",
                port=port_b,
                username="visiox-edge",
                private_key=key,
                expected_fingerprint=fingerprint_a.fingerprint,
            )
    finally:
        try:
            _compose(
                "down",
                "--volumes",
                "--remove-orphans",
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            pass
