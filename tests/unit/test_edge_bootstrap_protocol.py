import json
import socket
import struct
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from visiox_api.services.edge_bootstrap import (
    BootstrapChannelError,
    BootstrapRequestRejected,
    EdgeBootstrapChannel,
)
from visiox_common.settings import Settings
from visiox_db.base import Base
from visiox_db.models import ComputeNode, EdgeSshCredential
from visiox_edge_executor_worker.bootstrap_server import (
    BOOTSTRAP_COMMAND,
    BOOTSTRAP_DATA_PATH,
    BOOTSTRAP_SCRIPT_PATH,
    MAX_FRAME_BYTES,
    BootstrapOperations,
    BootstrapProtocolError,
    BootstrapProtocolTimeout,
    BootstrapServer,
    decode_frame,
    encode_frame,
)
from visiox_edge_executor_worker.crypto import CredentialCipher
from visiox_edge_executor_worker.ssh import CommandResult, HostKeyMismatchError, ScannedHostKey
from visiox_edge_executor_worker.startup import EdgeExecutorSecurityContext


FINGERPRINT = "SHA256:confirmed-host-key"


class FragmentedSocket:
    def __init__(self, chunks: list[bytes | BaseException]) -> None:
        self.chunks = list(chunks)

    def recv(self, size: int) -> bytes:
        if not self.chunks:
            return b""
        item = self.chunks.pop(0)
        if isinstance(item, BaseException):
            raise item
        chunk = item[:size]
        if len(item) > size:
            self.chunks.insert(0, item[size:])
        return chunk


class FakeChannelSocket(FragmentedSocket):
    def __init__(self, response: dict[str, Any]) -> None:
        super().__init__([encode_frame(response), b""])
        self.sent = b""
        self.timeout: float | None = None
        self.connected_to: str | None = None
        self.shutdown_how: int | None = None
        self.closed = False

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def connect(self, path: str) -> None:
        self.connected_to = path

    def sendall(self, data: bytes) -> None:
        self.sent += data

    def shutdown(self, how: int) -> None:
        self.shutdown_how = how

    def close(self) -> None:
        self.closed = True


class FakeSshSession:
    def __init__(self, events: list[str], *, run_status: int = 0) -> None:
        self.events = events
        self.run_status = run_status
        self.uploads: list[tuple[str, bytes]] = []

    def upload_bytes(self, data: bytes, remote_path: str, timeout_seconds: float) -> None:
        assert timeout_seconds <= 60
        self.events.append(f"upload:{remote_path}")
        self.uploads.append((remote_path, data))

    def run(self, command: str, *, timeout_seconds: float) -> CommandResult:
        assert timeout_seconds <= 60
        self.events.append(f"run:{command}")
        return CommandResult(exit_status=self.run_status, stdout=b"", stderr=b"")

    def close(self) -> None:
        self.events.append("close")


class FakeSshClient:
    def __init__(
        self,
        *,
        key_connect_error: Exception | None = None,
        key_connect_error_at: int = 1,
        rotation_cleanup_status: int = 0,
    ) -> None:
        self.events: list[str] = []
        self.password_session = FakeSshSession(self.events)
        self.old_key_session = FakeSshSession(self.events)
        self.new_key_session = FakeSshSession(self.events)
        self.cleanup_session = FakeSshSession(self.events, run_status=rotation_cleanup_status)
        self.key_connect_error = key_connect_error
        self.key_connect_error_at = key_connect_error_at
        self.key_connect_count = 0
        self.connect_calls: list[dict[str, Any]] = []

    def connect_password(self, **kwargs: Any) -> FakeSshSession:
        self.events.append("connect_password")
        self.connect_calls.append(kwargs)
        return self.password_session

    def connect(self, **kwargs: Any) -> FakeSshSession:
        self.connect_calls.append(kwargs)
        self.key_connect_count += 1
        if (
            self.key_connect_error is not None
            and self.key_connect_count == self.key_connect_error_at
        ):
            raise self.key_connect_error
        private_key = kwargs["private_key"]
        if private_key == b"old-private-key":
            self.events.append("connect_old_key")
            return self.old_key_session
        if self.key_connect_count == 1:
            self.events.append("connect_key")
            return self.new_key_session
        self.events.append("connect_key_cleanup")
        return self.cleanup_session


@pytest.fixture()
def session_factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _security(ssh_client: Any) -> EdgeExecutorSecurityContext:
    return EdgeExecutorSecurityContext(
        credential_cipher=CredentialCipher(b"m" * 32, key_version=1),
        ssh_client=ssh_client,
    )


def _bootstrap_request(password: str = "one-time-secret") -> dict[str, Any]:
    return {
        "request_id": "request-bootstrap",
        "operation": "bootstrap",
        "host": "10.0.0.8",
        "port": 22,
        "administrator": "ubuntu",
        "password": password,
        "confirmed_fingerprint": FINGERPRINT,
        "node_name": "edge-a",
    }


def _seed_credential(
    session_factory: sessionmaker[Session],
    *,
    private_key: bytes = b"old-private-key",
) -> tuple[str, bytes]:
    cipher = CredentialCipher(b"m" * 32, key_version=1)
    encrypted = cipher.encrypt(private_key)
    with session_factory() as session:
        node = ComputeNode(
            name="edge-a",
            status="online",
            architecture="unknown",
            platform_kind="ssh-edge",
            capabilities={},
            resources={},
            fingerprint={"ssh": FINGERPRINT},
            agent_version="ssh-bootstrap",
        )
        session.add(node)
        session.flush()
        session.add(
            EdgeSshCredential(
                node_id=node.id,
                ssh_host="10.0.0.8",
                ssh_port=22,
                ssh_user="visiox-edge",
                host_key_type="ssh-ed25519",
                host_key_fingerprint=FINGERPRINT,
                public_key="ssh-ed25519 old",
                encrypted_private_key=encrypted.ciphertext,
                encryption_nonce=encrypted.nonce,
                key_version=encrypted.key_version,
            )
        )
        session.commit()
        return node.id, private_key


def test_decode_frame_reads_fragmented_header_and_payload() -> None:
    message = {"request_id": "fragmented", "operation": "scan_host_key"}
    frame = encode_frame(message)
    fragmented = FragmentedSocket([frame[:1], frame[1:3], frame[3:8], frame[8:]])

    assert decode_frame(fragmented) == message


def test_decode_frame_rejects_oversized_payload_before_reading_body() -> None:
    fragmented = FragmentedSocket([struct.pack(">I", MAX_FRAME_BYTES + 1)])

    with pytest.raises(BootstrapProtocolError, match=r"^Bootstrap frame is invalid$"):
        decode_frame(fragmented)


def test_decode_frame_converts_socket_timeout_to_fixed_error() -> None:
    fragmented = FragmentedSocket([socket.timeout("contains transport detail")])

    with pytest.raises(BootstrapProtocolTimeout, match=r"^Bootstrap request timed out$") as exc_info:
        decode_frame(fragmented)

    assert exc_info.value.__cause__ is None


@pytest.mark.parametrize(
    "payload",
    [
        b"not-json",
        b'{"request_id":"one"}{"request_id":"two"}',
        b'{"request_id":"one"} ',
        b'[]',
    ],
)
def test_decode_frame_rejects_invalid_or_trailing_json(payload: bytes) -> None:
    fragmented = FragmentedSocket([struct.pack(">I", len(payload)), payload])

    with pytest.raises(BootstrapProtocolError, match=r"^Bootstrap frame is invalid$"):
        decode_frame(fragmented)


def test_api_channel_rejects_request_id_mismatch_and_uses_sixty_second_timeout(tmp_path) -> None:
    fake_socket = FakeChannelSocket({"request_id": "wrong", "status": "ok"})
    channel = EdgeBootstrapChannel(tmp_path / "bootstrap.sock", socket_factory=lambda: fake_socket)

    with pytest.raises(BootstrapChannelError, match=r"^Edge bootstrap channel failed$"):
        channel.request({"request_id": "expected", "operation": "scan_host_key"})

    assert fake_socket.timeout == 60
    assert fake_socket.shutdown_how == socket.SHUT_WR
    assert fake_socket.closed


def test_api_channel_rejects_oversized_request_without_connecting(tmp_path) -> None:
    fake_socket = FakeChannelSocket({"request_id": "request", "status": "ok"})
    channel = EdgeBootstrapChannel(tmp_path / "bootstrap.sock", socket_factory=lambda: fake_socket)

    with pytest.raises(BootstrapChannelError, match=r"^Edge bootstrap channel failed$"):
        channel.request(
            {"request_id": "request", "operation": "bootstrap", "password": "x" * MAX_FRAME_BYTES}
        )

    assert fake_socket.connected_to is None


def test_api_channel_does_not_trust_worker_error_text(tmp_path) -> None:
    fake_socket = FakeChannelSocket(
        {
            "request_id": "request",
            "status": "error",
            "error_code": "BOOTSTRAP_FAILED",
            "error_message": "one-time-password leaked by peer",
        }
    )
    channel = EdgeBootstrapChannel(tmp_path / "bootstrap.sock", socket_factory=lambda: fake_socket)

    with pytest.raises(BootstrapRequestRejected) as exc_info:
        channel.request({"request_id": "request", "operation": "bootstrap"})

    assert str(exc_info.value) == "Edge node bootstrap failed"
    assert "one-time-password" not in str(exc_info.value)


def test_api_channel_rejects_response_with_secret_or_unexpected_fields(tmp_path) -> None:
    fake_socket = FakeChannelSocket(
        {
            "request_id": "request",
            "status": "ok",
            "node_id": "node-1",
            "password": "must-not-cross-back",
        }
    )
    channel = EdgeBootstrapChannel(tmp_path / "bootstrap.sock", socket_factory=lambda: fake_socket)

    with pytest.raises(BootstrapChannelError, match=r"^Edge bootstrap channel failed$"):
        channel.request({"request_id": "request", "operation": "bootstrap"})


def test_server_initializes_security_before_unlinking_or_binding(tmp_path, monkeypatch) -> None:
    events: list[str] = []
    socket_path = tmp_path / "bootstrap.sock"
    socket_path.write_text("stale", encoding="ascii")

    class FakeListener:
        def bind(self, path: str) -> None:
            assert not socket_path.exists()
            events.append(f"bind:{path}")

        def listen(self, backlog: int) -> None:
            events.append(f"listen:{backlog}")

        def settimeout(self, timeout: float) -> None:
            events.append(f"timeout:{timeout}")

        def close(self) -> None:
            events.append("listener-close")

    fake_security = _security(FakeSshClient())

    def initialize(settings: Settings) -> EdgeExecutorSecurityContext:
        assert socket_path.exists()
        events.append("initialize_security")
        return fake_security

    monkeypatch.setattr(Path, "chmod", lambda self, mode: events.append(f"chmod:{mode:o}"))
    server = BootstrapServer(
        Settings(
            _env_file=None,
            edge_bootstrap_socket=socket_path,
            edge_credential_master_key_file=tmp_path / "unused",
        ),
        session_factory=lambda: pytest.fail("session should not be opened while binding"),
        security_initializer=initialize,
        socket_factory=lambda family, kind: FakeListener(),
        max_concurrency=3,
    )

    server.start()

    assert events[0] == "initialize_security"
    assert events[1].startswith("bind:")
    assert "listen:3" in events


def test_bootstrap_uses_strict_fingerprint_and_reconnects_before_persistence(
    session_factory: sessionmaker[Session],
) -> None:
    ssh_client = FakeSshClient()
    operations = BootstrapOperations(
        _security(ssh_client),
        session_factory,
        host_key_scanner=lambda host, port, timeout: ScannedHostKey("ssh-ed25519", FINGERPRINT),
    )

    response = operations.handle(_bootstrap_request())

    assert response["status"] == "ok"
    assert ssh_client.events[:6] == [
        "connect_password",
        f"upload:{BOOTSTRAP_SCRIPT_PATH}",
        f"upload:{BOOTSTRAP_DATA_PATH}",
        f"run:{BOOTSTRAP_COMMAND}",
        "close",
        "connect_key",
    ]
    assert ssh_client.connect_calls[0]["expected_fingerprint"] == FINGERPRINT
    assert ssh_client.connect_calls[1]["expected_fingerprint"] == FINGERPRINT
    assert "one-time-secret" not in json.dumps(response)
    uploaded_paths = [path for path, _ in ssh_client.password_session.uploads]
    assert uploaded_paths == [BOOTSTRAP_SCRIPT_PATH, BOOTSTRAP_DATA_PATH]
    script = ssh_client.password_session.uploads[0][1]
    data = json.loads(ssh_client.password_session.uploads[1][1])
    assert b"one-time-secret" not in script
    assert "one-time-secret" not in json.dumps(data)
    assert data["public_key"].startswith("ssh-ed25519 ")

    with session_factory() as session:
        credential = session.scalar(select(EdgeSshCredential))
        node = session.scalar(select(ComputeNode))
        assert credential is not None
        assert node is not None
        assert node.id == response["node_id"]
        assert node.status == "online"
        assert CredentialCipher(b"m" * 32, key_version=1).decrypt(
            type("Secret", (), {
                "ciphertext": credential.encrypted_private_key,
                "nonce": credential.encryption_nonce,
                "key_version": credential.key_version,
            })()
        ).startswith(b"-----BEGIN OPENSSH PRIVATE KEY-----")


def test_bootstrap_key_reconnect_failure_rolls_back_node_and_credential(
    session_factory: sessionmaker[Session],
) -> None:
    ssh_client = FakeSshClient(key_connect_error=RuntimeError("private secret in failure"))
    operations = BootstrapOperations(
        _security(ssh_client),
        session_factory,
        host_key_scanner=lambda host, port, timeout: ScannedHostKey("ssh-ed25519", FINGERPRINT),
    )

    response = operations.handle(_bootstrap_request())

    assert response == {
        "request_id": "request-bootstrap",
        "status": "error",
        "error_code": "BOOTSTRAP_FAILED",
        "error_message": "Edge node bootstrap failed",
    }
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ComputeNode)) == 0
        assert session.scalar(select(func.count()).select_from(EdgeSshCredential)) == 0


def test_bootstrap_commit_failure_rolls_back_node_and_credential() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)

    class FailingCommitSession(Session):
        def commit(self) -> None:
            self.flush()
            raise RuntimeError("database failure with sensitive context")

    failing_factory = sessionmaker(
        bind=engine,
        class_=FailingCommitSession,
        autoflush=False,
        expire_on_commit=False,
    )
    inspection_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    ssh_client = FakeSshClient()
    operations = BootstrapOperations(
        _security(ssh_client),
        failing_factory,
        host_key_scanner=lambda host, port, timeout: ScannedHostKey("ssh-ed25519", FINGERPRINT),
    )

    response = operations.handle(_bootstrap_request())

    assert response["status"] == "error"
    assert response["error_message"] == "Edge node bootstrap failed"
    with inspection_factory() as session:
        assert session.scalar(select(func.count()).select_from(ComputeNode)) == 0
        assert session.scalar(select(func.count()).select_from(EdgeSshCredential)) == 0


def test_bootstrap_rejects_changed_fingerprint_before_password_authentication(
    session_factory: sessionmaker[Session],
) -> None:
    ssh_client = FakeSshClient()
    operations = BootstrapOperations(
        _security(ssh_client),
        session_factory,
        host_key_scanner=lambda host, port, timeout: ScannedHostKey(
            "ssh-ed25519", "SHA256:changed"
        ),
    )

    response = operations.handle(_bootstrap_request())

    assert response["error_code"] == "HOST_KEY_MISMATCH"
    assert "connect_password" not in ssh_client.events


def test_rotate_failure_keeps_old_key_and_old_persisted_credential(
    session_factory: sessionmaker[Session],
) -> None:
    node_id, old_private_key = _seed_credential(session_factory)
    ssh_client = FakeSshClient(
        key_connect_error=HostKeyMismatchError("changed"),
        key_connect_error_at=2,
    )
    operations = BootstrapOperations(_security(ssh_client), session_factory)

    response = operations.handle(
        {"request_id": "rotate", "operation": "rotate_key", "node_id": node_id}
    )

    assert response["status"] == "error"
    assert response["error_code"] == "HOST_KEY_MISMATCH"
    rotation_data = json.loads(ssh_client.old_key_session.uploads[1][1])
    assert rotation_data["operation"] == "rotate_add"
    assert rotation_data["old_public_key"] == "ssh-ed25519 old"
    assert f"run:{BOOTSTRAP_COMMAND}" in ssh_client.events
    with session_factory() as session:
        credential = session.scalar(select(EdgeSshCredential))
        assert credential is not None
        encrypted = type("Secret", (), {
            "ciphertext": credential.encrypted_private_key,
            "nonce": credential.encryption_nonce,
            "key_version": credential.key_version,
        })()
        assert CredentialCipher(b"m" * 32, key_version=1).decrypt(encrypted) == old_private_key
        assert credential.public_key == "ssh-ed25519 old"


def test_rotate_cleanup_failure_is_not_success_and_leaves_both_keys_installed(
    session_factory: sessionmaker[Session],
) -> None:
    node_id, _ = _seed_credential(session_factory)
    ssh_client = FakeSshClient(rotation_cleanup_status=1)
    operations = BootstrapOperations(_security(ssh_client), session_factory)

    response = operations.handle(
        {"request_id": "rotate", "operation": "rotate_key", "node_id": node_id}
    )

    assert response == {
        "request_id": "rotate",
        "status": "error",
        "error_code": "ROTATE_CLEANUP_FAILED",
        "error_message": "SSH key rotation cleanup failed",
    }
    add_data = json.loads(ssh_client.old_key_session.uploads[1][1])
    cleanup_data = json.loads(ssh_client.cleanup_session.uploads[1][1])
    assert add_data["operation"] == "rotate_add"
    assert add_data["old_public_key"] == "ssh-ed25519 old"
    assert cleanup_data["operation"] == "rotate_commit"
    with session_factory() as session:
        credential = session.scalar(select(EdgeSshCredential))
        assert credential is not None
        assert credential.public_key != "ssh-ed25519 old"


def test_bootstrap_script_is_idempotent_and_keeps_dynamic_data_out_of_commands() -> None:
    script_path = (
        Path(__file__).parents[2]
        / "workers"
        / "edge-executor-worker"
        / "remote"
        / "bootstrap_user.sh"
    )
    script = script_path.read_text(encoding="utf-8")

    assert "id -u visiox-edge" in script
    assert "useradd" in script
    assert "usermod -aG docker visiox-edge" in script
    assert "install -d -m 0700" in script
    assert "chmod 0600" in script
    assert "authorized_keys" in script
    assert script.index('if [ "${OPERATION}" = "bootstrap" ]') < script.index("sudo useradd")
    assert 'if [ "$(id -un)" = "${EDGE_USER}" ]' in script
    assert "one-time" not in script.lower()
    assert "password" not in script.lower()
    assert "eval " not in script
    assert "echo " not in script
    assert BOOTSTRAP_COMMAND == (
        "/bin/bash /tmp/visiox-bootstrap-user.sh /tmp/visiox-bootstrap-data.json"
    )
