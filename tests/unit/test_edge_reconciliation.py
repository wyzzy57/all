from __future__ import annotations

import json
import threading
from types import SimpleNamespace

from sqlalchemy import create_engine

from visiox_common.settings import Settings
from visiox_db.base import Base
from visiox_db.models import EdgeSshCredential, RemoteExecution
from visiox_db.session import create_session_factory
from visiox_edge_executor_worker.crypto import CredentialCipher
from visiox_edge_executor_worker.reconciliation import DockerLabels, RemoteRuntimeReconciler
from visiox_edge_executor_worker.runner import build_application
from visiox_edge_executor_worker.scripts import load_packaged_script
from visiox_edge_executor_worker.ssh import CommandResult, RemotePrivateDirectory
from visiox_edge_executor_worker.startup import EdgeExecutorSecurityContext
from visiox_edge_executor_worker.state import ExecutionResult, RemoteExecutionRepository


class RecordingSshSession:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.uploads: list[tuple[str, bytes]] = []
        self.validated: list[str] = []
        self.commands: list[str] = []
        self.cleaned: list[tuple[str, ...]] = []
        self.closed = False

    def create_private_directory(self, *, timeout_seconds: float) -> RemotePrivateDirectory:
        assert timeout_seconds > 0
        return RemotePrivateDirectory("/home/visiox-edge/.visiox-0123456789abcdef0123456789abcdef", 1000)

    def upload_bytes_exclusive(
        self,
        data: bytes,
        remote_path: str,
        *,
        expected_owner_uid: int | None,
        timeout_seconds: float,
    ) -> None:
        assert expected_owner_uid == 1000
        assert timeout_seconds > 0
        self.uploads.append((remote_path, data))

    def validate_remote_file(
        self,
        remote_path: str,
        *,
        expected_owner_uid: int | None,
        timeout_seconds: float,
    ) -> None:
        assert expected_owner_uid == 1000
        self.validated.append(remote_path)

    def run(self, command: str, *, timeout_seconds: float) -> CommandResult:
        self.commands.append(command)
        return CommandResult(
            exit_status=0,
            stdout=json.dumps(self.response).encode("ascii"),
            stderr=b"",
        )

    def cleanup_private_directory(
        self,
        directory: RemotePrivateDirectory,
        remote_paths: tuple[str, ...],
        *,
        timeout_seconds: float,
    ) -> None:
        self.cleaned.append(remote_paths)

    def close(self) -> None:
        self.closed = True


class RecordingSshClient:
    def __init__(self, ssh_session: RecordingSshSession) -> None:
        self.ssh_session = ssh_session
        self.connections: list[dict[str, object]] = []

    def connect(self, **request):
        self.connections.append(request)
        return self.ssh_session


def _runtime(
    response: dict[str, object],
) -> tuple[RemoteRuntimeReconciler, RemoteExecutionRepository, RecordingSshClient]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    cipher = CredentialCipher(b"a" * 32, key_version=1)
    encrypted = cipher.encrypt(b"private-key")
    with session_factory() as session:
        session.add_all(
            [
                RemoteExecution(
                    id="exec-1",
                    node_id="node-1",
                    operation="deploy",
                    status="running",
                    resource_type="deployment_service",
                    resource_id="service-1",
                    idempotency_key="deploy-service-1-attempt-1",
                ),
                EdgeSshCredential(
                    node_id="node-1",
                    ssh_host="10.0.0.10",
                    ssh_port=22,
                    ssh_user="visiox-edge",
                    host_key_type="ssh-ed25519",
                    host_key_fingerprint="SHA256:pinned",
                    public_key="ssh-ed25519 public",
                    encrypted_private_key=encrypted.ciphertext,
                    encryption_nonce=encrypted.nonce,
                    key_version=encrypted.key_version,
                ),
            ]
        )
        session.commit()
    ssh_session = RecordingSshSession(response)
    ssh_client = RecordingSshClient(ssh_session)
    repository = RemoteExecutionRepository(session_factory)
    reconciler = RemoteRuntimeReconciler(
        session_factory,
        repository,
        EdgeExecutorSecurityContext(credential_cipher=cipher, ssh_client=ssh_client),
    )
    return reconciler, repository, ssh_client


def test_reconciler_strictly_inspects_remote_docker_by_stable_labels() -> None:
    reconciler, repository, ssh_client = _runtime({"containers": []})

    assert reconciler.reconcile_startup() == 1

    execution = repository.load("exec-1")
    assert execution is not None
    assert execution.status == "queued"
    assert execution.phase == "reconciliation_pending"
    assert ssh_client.connections == [
        {
            "host": "10.0.0.10",
            "port": 22,
            "username": "visiox-edge",
            "private_key": b"private-key",
            "expected_fingerprint": "SHA256:pinned",
            "timeout_seconds": 30.0,
        }
    ]
    ssh_session = ssh_client.ssh_session
    assert [path.rsplit("/", 1)[-1] for path, _ in ssh_session.uploads] == [
        "inspect_runtime.sh",
        "request.json",
    ]
    request = json.loads(ssh_session.uploads[1][1])
    assert request == {
        "labels": {
            DockerLabels.MANAGED: "true",
            DockerLabels.REMOTE_EXECUTION_ID: "exec-1",
            DockerLabels.NODE_ID: "node-1",
            DockerLabels.RESOURCE_TYPE: "deployment_service",
            DockerLabels.RESOURCE_ID: "service-1",
        }
    }
    assert "exec-1" not in ssh_session.commands[0]
    assert ssh_session.cleaned
    assert ssh_session.closed is True


def test_reconciler_persists_deterministic_terminal_remote_failure() -> None:
    reconciler, repository, _ = _runtime(
        {
            "containers": [
                {
                    "id": "a" * 64,
                    "status": "exited",
                    "exit_code": 137,
                    "oom_killed": True,
                    "health": None,
                }
            ]
        }
    )

    assert reconciler.reconcile_startup() == 1

    execution = repository.load("exec-1")
    assert execution is not None
    assert execution.status == "failed"
    assert execution.error_code == "REMOTE_RUNTIME_FAILED"
    assert execution.error_message == "Remote runtime exited unsuccessfully"


def test_reconciler_contains_three_or_more_matching_remote_runtimes() -> None:
    reconciler, repository, _ = _runtime(
        {
            "containers": [
                {
                    "id": character * 64,
                    "status": "running",
                    "exit_code": 0,
                    "oom_killed": False,
                    "health": "healthy",
                }
                for character in ("a", "b", "c")
            ]
        }
    )

    assert reconciler.reconcile_startup() == 1

    execution = repository.load("exec-1")
    assert execution is not None
    assert execution.status == "failed"
    assert execution.phase == "reconciliation_failed"
    assert execution.error_code == "REMOTE_STATE_AMBIGUOUS"
    assert execution.error_message == "Multiple remote runtimes matched one execution"


def test_reconciler_persists_recoverable_state_for_invalid_remote_response() -> None:
    reconciler, repository, _ = _runtime({"unexpected": "password=secret"})

    assert reconciler.reconcile_startup() == 1

    execution = repository.load("exec-1")
    assert execution is not None
    assert execution.status == "queued"
    assert execution.phase == "reconciliation_retry"
    assert execution.error_code == "REMOTE_INSPECTION_RETRY"
    assert execution.error_message == "Remote runtime inspection must be retried"


def test_reconciler_redacts_corrupt_credential_failures(monkeypatch) -> None:
    reconciler, repository, _ = _runtime({"containers": []})

    def fail_to_decrypt(node_id: str):
        raise ValueError("client_secret=credential-secret")

    monkeypatch.setattr(reconciler, "_load_target", fail_to_decrypt)

    assert reconciler.reconcile_startup() == 1

    execution = repository.load("exec-1")
    assert execution is not None
    assert execution.status == "queued"
    assert execution.error_code == "REMOTE_INSPECTION_RETRY"
    assert execution.error_message == "Remote runtime inspection must be retried"
    assert "secret" not in execution.error_message.casefold()


def test_reconciler_observes_shutdown_between_remote_executions(monkeypatch) -> None:
    reconciler, repository, _ = _runtime({"containers": []})
    executions = [SimpleNamespace(id=f"exec-{index}") for index in range(3)]
    stopped = threading.Event()
    visited: list[str] = []

    monkeypatch.setattr(repository, "list_reconcilable", lambda: executions)

    def reconcile_one(execution):
        visited.append(execution.id)
        stopped.set()
        return ExecutionResult.failed(
            error_code="STOP_TEST",
            error_message="fixed",
            phase="test",
        )

    monkeypatch.setattr(reconciler, "_reconcile", reconcile_one)
    monkeypatch.setattr(repository, "finalize", lambda *args: None)

    assert reconciler.reconcile_startup(stop_requested=stopped.is_set) == 1
    assert visited == ["exec-0"]


def test_build_application_installs_concrete_reconciler(tmp_path, monkeypatch) -> None:
    secret_path = tmp_path / "master-key"
    secret_path.write_bytes(b"a" * 32)
    settings = Settings(_env_file=None, edge_credential_master_key_file=secret_path)
    fake_redis = SimpleNamespace()

    monkeypatch.setattr(
        "visiox_edge_executor_worker.runner.create_session_factory",
        lambda: lambda: None,
    )

    application = build_application(
        settings,
        redis_factory=lambda url: fake_redis,
        server_factory=lambda *args, **kwargs: SimpleNamespace(),
    )

    assert isinstance(application.reconciler, RemoteRuntimeReconciler)


def test_static_reconciliation_script_is_packaged_and_uses_structured_docker_calls() -> None:
    script = load_packaged_script("inspect_runtime.sh").decode("utf-8")

    assert "subprocess.run" in script
    assert "shell=True" not in script
    assert "docker inspect" not in script
    assert "request_path" in script
    assert "len(container_ids) >" not in script
