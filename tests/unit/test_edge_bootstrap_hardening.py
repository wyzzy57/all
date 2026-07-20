import json
import threading
from pathlib import PurePosixPath
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from visiox_db.base import Base
from visiox_db.models import ComputeNode, EdgeSshCredential
from visiox_edge_executor_worker.bootstrap_server import BootstrapOperations
from visiox_edge_executor_worker.crypto import CredentialCipher
from visiox_edge_executor_worker.ssh import CommandResult, RemotePrivateDirectory, ScannedHostKey
from visiox_edge_executor_worker.startup import EdgeExecutorSecurityContext


FINGERPRINT = "SHA256:confirmed-host-key"


class RecordingSshSession:
    def __init__(
        self,
        actions: list[str],
        *,
        block_action: str | None = None,
        entered: threading.Event | None = None,
        release: threading.Event | None = None,
        login_uid: int = 0,
        payloads: list[dict[str, Any]] | None = None,
        commands: list[str] | None = None,
    ) -> None:
        self.actions = actions
        self.block_action = block_action
        self.entered = entered
        self.release = release
        self.login_uid = login_uid
        self.payloads = payloads if payloads is not None else []
        self.commands = commands if commands is not None else []
        self.uploads: dict[str, bytes] = {}
        self.workspace_count = 0

    def create_private_directory(self, *, timeout_seconds: float) -> RemotePrivateDirectory:
        assert 0 < timeout_seconds <= 60
        self.workspace_count += 1
        return RemotePrivateDirectory(
            path=f"/home/operator/.visiox-{self.workspace_count:032x}",
            owner_uid=1000,
        )

    def upload_bytes_exclusive(
        self,
        data: bytes,
        remote_path: str,
        *,
        expected_owner_uid: int | None,
        timeout_seconds: float,
    ) -> None:
        assert expected_owner_uid == 1000
        assert 0 < timeout_seconds <= 60
        assert remote_path not in self.uploads
        self.uploads[remote_path] = data

    def validate_remote_file(
        self,
        remote_path: str,
        *,
        expected_owner_uid: int | None,
        timeout_seconds: float,
    ) -> None:
        assert expected_owner_uid == 1000
        assert 0 < timeout_seconds <= 60
        assert remote_path in self.uploads

    def cleanup_private_directory(
        self,
        directory: RemotePrivateDirectory,
        remote_paths: tuple[str, ...],
        *,
        timeout_seconds: float,
    ) -> None:
        assert 0 < timeout_seconds <= 60
        assert all(PurePosixPath(path).parent == PurePosixPath(directory.path) for path in remote_paths)
        for path in remote_paths:
            self.uploads.pop(path, None)

    def run(self, command: str, *, timeout_seconds: float) -> CommandResult:
        assert 0 < timeout_seconds <= 60
        self.commands.append(command)
        if command == "/usr/bin/id -u":
            return CommandResult(
                exit_status=0,
                stdout=f"{self.login_uid}\n".encode("ascii"),
                stderr=b"",
            )
        if command == "command -v sudo >/dev/null 2>&1 && sudo -n true":
            self.actions.append("sudo_preflight")
            return CommandResult(exit_status=0, stdout=b"", stderr=b"")
        payload = json.loads(next(data for path, data in self.uploads.items() if path.endswith("request.json")))
        self.payloads.append(payload)
        action = payload["operation"]
        self.actions.append(action)
        if action == self.block_action:
            assert self.entered is not None and self.release is not None
            self.entered.set()
            assert self.release.wait(5)
        return CommandResult(exit_status=0, stdout=b"", stderr=b"")

    def close(self) -> None:
        self.actions.append("close")


class RecordingSshClient:
    def __init__(
        self,
        *,
        block_action: str | None = None,
        entered: threading.Event | None = None,
        release: threading.Event | None = None,
        login_uid: int = 0,
    ) -> None:
        self.actions: list[str] = []
        self.connect_password_calls = 0
        self.connect_calls = 0
        self.block_action = block_action
        self.entered = entered
        self.release = release
        self.login_uid = login_uid
        self.payloads: list[dict[str, Any]] = []
        self.commands: list[str] = []
        self.timeouts: list[float] = []

    def _session(self) -> RecordingSshSession:
        return RecordingSshSession(
            self.actions,
            block_action=self.block_action,
            entered=self.entered,
            release=self.release,
            login_uid=self.login_uid,
            payloads=self.payloads,
            commands=self.commands,
        )

    def connect_password(self, **kwargs: Any) -> RecordingSshSession:
        assert 0 < kwargs["timeout_seconds"] <= 60
        self.timeouts.append(kwargs["timeout_seconds"])
        self.connect_password_calls += 1
        return self._session()

    def connect(self, **kwargs: Any) -> RecordingSshSession:
        assert 0 < kwargs["timeout_seconds"] <= 60
        self.timeouts.append(kwargs["timeout_seconds"])
        self.connect_calls += 1
        return self._session()


def _security(ssh_client: RecordingSshClient) -> EdgeExecutorSecurityContext:
    return EdgeExecutorSecurityContext(
        credential_cipher=CredentialCipher(b"m" * 32, key_version=1),
        ssh_client=ssh_client,
    )


def _session_factory(tmp_path) -> sessionmaker[Session]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'bootstrap.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _request(*, host: str = "EDGE.EXAMPLE.COM.", node_name: str = "edge-a") -> dict[str, Any]:
    return {
        "request_id": "request-bootstrap",
        "operation": "bootstrap",
        "host": host,
        "port": 22,
        "administrator": "root",
        "password": "one-time-secret",
        "confirmed_fingerprint": FINGERPRINT,
        "node_name": node_name,
    }


def _seed_credential(
    factory: sessionmaker[Session],
    *,
    name: str = "edge-a",
    host: str = "edge.example.com",
    platform_kind: str = "ssh_edge",
) -> str:
    cipher = CredentialCipher(b"m" * 32, key_version=1)
    encrypted = cipher.encrypt(b"old-private-key")
    with factory() as session:
        node = ComputeNode(
            name=name,
            status="online",
            architecture="unknown",
            platform_kind=platform_kind,
            capabilities={},
            resources={},
            fingerprint={"existing": "identity"},
            agent_version="existing-agent",
            certificate_serial="serial-1" if platform_kind != "ssh_edge" else None,
            certificate_fingerprint="certificate-1" if platform_kind != "ssh_edge" else None,
        )
        session.add(node)
        session.flush()
        session.add(
            EdgeSshCredential(
                node_id=node.id,
                ssh_host=host,
                ssh_port=22,
                ssh_user="visiox-edge",
                host_key_type="ssh-ed25519",
                host_key_fingerprint=FINGERPRINT,
                public_key="ssh-ed25519 old-key",
                encrypted_private_key=encrypted.ciphertext,
                encryption_nonce=encrypted.nonce,
                key_version=1,
            )
        )
        session.commit()
        return node.id


def _operations(factory: sessionmaker[Session], ssh_client: RecordingSshClient) -> BootstrapOperations:
    return BootstrapOperations(
        _security(ssh_client),
        factory,
        host_key_scanner=lambda host, port, timeout: ScannedHostKey("ssh-ed25519", FINGERPRINT),
        bootstrap_script=b"static-script",
    )


@pytest.mark.parametrize("platform_kind", ["ssh_edge", "ssh-edge"])
def test_duplicate_bootstrap_rejects_existing_credential_before_any_ssh(
    tmp_path,
    platform_kind: str,
) -> None:
    factory = _session_factory(tmp_path)
    _seed_credential(factory, platform_kind=platform_kind)
    ssh_client = RecordingSshClient()

    response = _operations(factory, ssh_client).handle(_request())

    assert response == {
        "request_id": "request-bootstrap",
        "status": "error",
        "error_code": "NODE_ALREADY_BOOTSTRAPPED",
        "error_message": "Edge node is already bootstrapped; use rotate-key",
    }
    assert ssh_client.connect_password_calls == 0
    assert ssh_client.connect_calls == 0


def test_bootstrap_rejects_non_ssh_edge_name_without_changing_agent_identity(tmp_path) -> None:
    factory = _session_factory(tmp_path)
    with factory() as session:
        node = ComputeNode(
            name="edge-a",
            status="offline",
            architecture="arm64",
            platform_kind="agent",
            capabilities={"gpu": True},
            resources={"memory": 1},
            fingerprint={"existing": "identity"},
            agent_version="1.2.3",
            certificate_serial="serial-1",
            certificate_fingerprint="certificate-1",
        )
        session.add(node)
        session.commit()
        node_id = node.id
    ssh_client = RecordingSshClient()

    response = _operations(factory, ssh_client).handle(_request())

    assert response["error_code"] == "NODE_NAME_CONFLICT"
    assert ssh_client.connect_password_calls == 0
    with factory() as session:
        unchanged = session.get(ComputeNode, node_id)
        assert unchanged is not None
        assert unchanged.platform_kind == "agent"
        assert unchanged.status == "offline"
        assert unchanged.fingerprint == {"existing": "identity"}
        assert unchanged.agent_version == "1.2.3"
        assert unchanged.certificate_serial == "serial-1"
        assert unchanged.certificate_fingerprint == "certificate-1"


def test_bootstrap_rejects_canonical_host_owned_by_another_node_before_ssh(tmp_path) -> None:
    factory = _session_factory(tmp_path)
    _seed_credential(factory, name="edge-existing", host="edge.example.com")
    ssh_client = RecordingSshClient()

    response = _operations(factory, ssh_client).handle(_request(node_name="edge-new"))

    assert response["error_code"] == "SSH_HOST_IN_USE"
    assert ssh_client.connect_password_calls == 0
    with factory() as session:
        assert session.scalar(select(ComputeNode).where(ComputeNode.name == "edge-new")) is None


def test_concurrent_duplicate_bootstrap_mutates_remote_once_and_releases_locks(tmp_path) -> None:
    factory = _session_factory(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    ssh_client = RecordingSshClient(block_action="bootstrap_add", entered=entered, release=release)
    operations = _operations(factory, ssh_client)
    responses: list[dict[str, Any]] = []

    first = threading.Thread(target=lambda: responses.append(operations.handle(_request())))
    second = threading.Thread(target=lambda: responses.append(operations.handle(_request())))
    first.start()
    assert entered.wait(5)
    second.start()
    assert ssh_client.connect_password_calls == 1
    release.set()
    first.join(5)
    second.join(5)

    assert sorted(response["status"] for response in responses) == ["error", "ok"]
    assert {response.get("error_code") for response in responses} == {
        None,
        "NODE_ALREADY_BOOTSTRAPPED",
    }
    assert ssh_client.connect_password_calls == 1
    assert operations.lock_entry_count == 0


def test_rotate_and_test_connection_are_serialized_for_same_node(tmp_path) -> None:
    factory = _session_factory(tmp_path)
    node_id = _seed_credential(factory)
    entered = threading.Event()
    release = threading.Event()
    ssh_client = RecordingSshClient(block_action="rotate_add", entered=entered, release=release)
    operations = _operations(factory, ssh_client)
    responses: list[dict[str, Any]] = []

    rotate = threading.Thread(
        target=lambda: responses.append(
            operations.handle({"request_id": "rotate", "operation": "rotate_key", "node_id": node_id})
        )
    )
    test = threading.Thread(
        target=lambda: responses.append(
            operations.handle({"request_id": "test", "operation": "test_connection", "node_id": node_id})
        )
    )
    rotate.start()
    assert entered.wait(5)
    test.start()
    assert ssh_client.connect_calls == 1
    release.set()
    rotate.join(5)
    test.join(5)

    assert {response["status"] for response in responses} == {"ok"}
    assert operations.lock_entry_count == 0


def test_remote_managed_key_comment_contains_node_id_and_incrementing_version(tmp_path) -> None:
    factory = _session_factory(tmp_path)
    node_id = _seed_credential(factory)
    ssh_client = RecordingSshClient()
    operations = _operations(factory, ssh_client)

    response = operations.handle(
        {"request_id": "rotate", "operation": "rotate_key", "node_id": node_id}
    )

    assert response["status"] == "ok"
    assert any(
        action == "rotate_add" for action in ssh_client.actions
    )
    rotate_add = next(payload for payload in ssh_client.payloads if payload["operation"] == "rotate_add")
    assert rotate_add["node_id"] == node_id
    assert rotate_add["key_version"] == 2
    with factory() as session:
        credential = session.scalar(select(EdgeSshCredential))
        assert credential is not None
        assert credential.key_version == 2


def test_non_root_bootstrap_requires_passwordless_sudo_preflight(tmp_path) -> None:
    factory = _session_factory(tmp_path)
    ssh_client = RecordingSshClient(login_uid=1000)

    response = _operations(factory, ssh_client).handle(_request())

    assert response["status"] == "ok"
    assert ssh_client.actions.index("sudo_preflight") < ssh_client.actions.index("bootstrap_add")
    assert all("one-time-secret" not in command for command in ssh_client.commands)
    assert "sudo -S" not in "\n".join(ssh_client.commands)


def test_root_bootstrap_does_not_require_sudo_preflight(tmp_path) -> None:
    factory = _session_factory(tmp_path)
    ssh_client = RecordingSshClient(login_uid=0)

    response = _operations(factory, ssh_client).handle(_request())

    assert response["status"] == "ok"
    assert "sudo_preflight" not in ssh_client.actions


def test_database_rejects_duplicate_canonical_ssh_host_and_port(tmp_path) -> None:
    factory = _session_factory(tmp_path)
    _seed_credential(factory, name="edge-a")
    cipher = CredentialCipher(b"m" * 32, key_version=1)
    encrypted = cipher.encrypt(b"another-private-key")
    with factory() as session:
        node = ComputeNode(
            name="edge-b",
            status="online",
            architecture="unknown",
            platform_kind="ssh_edge",
            capabilities={},
            resources={},
            fingerprint={},
            agent_version="ssh-bootstrap",
        )
        session.add(node)
        session.flush()
        session.add(
            EdgeSshCredential(
                node_id=node.id,
                ssh_host="edge.example.com",
                ssh_port=22,
                ssh_user="visiox-edge",
                host_key_type="ssh-ed25519",
                host_key_fingerprint=FINGERPRINT,
                public_key="ssh-ed25519 another",
                encrypted_private_key=encrypted.ciphertext,
                encryption_nonce=encrypted.nonce,
                key_version=1,
            )
        )

        with pytest.raises(IntegrityError):
            session.commit()
