import json
from importlib import resources
import os
import socket
import stat
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
from visiox_db.models import ComputeNode, EdgeSshCredential, NodeEvent, Task
from visiox_edge_executor_worker.bootstrap_server import (
    MAX_FRAME_BYTES,
    BootstrapOperations,
    BootstrapProtocolError,
    BootstrapProtocolTimeout,
    BootstrapServer,
    decode_frame,
    encode_frame,
    main,
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
        self.workspace_count = 0

    def create_private_directory(self, *, timeout_seconds: float):
        assert 0 < timeout_seconds <= 60
        self.workspace_count += 1
        return type(
            "Workspace",
            (),
            {"path": f"/home/operator/.visiox-{self.workspace_count:032x}", "owner_uid": 1000},
        )()

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
        assert remote_path not in {path for path, _ in self.uploads}
        self.events.append(f"upload:{Path(remote_path).name}")
        self.uploads.append((remote_path, data))

    def validate_remote_file(
        self,
        remote_path: str,
        *,
        expected_owner_uid: int | None,
        timeout_seconds: float,
    ) -> None:
        assert expected_owner_uid == 1000
        assert 0 < timeout_seconds <= 60
        assert remote_path in {path for path, _ in self.uploads}

    def cleanup_private_directory(
        self,
        directory: Any,
        remote_paths: tuple[str, ...],
        *,
        timeout_seconds: float,
    ) -> None:
        assert 0 < timeout_seconds <= 60
        assert all(Path(path).parent.as_posix() == directory.path for path in remote_paths)
        self.events.append("workspace-cleanup")

    def upload_bytes(self, data: bytes, remote_path: str, timeout_seconds: float) -> None:
        assert timeout_seconds <= 60
        self.events.append(f"upload:{remote_path}")
        self.uploads.append((remote_path, data))

    def run(self, command: str, *, timeout_seconds: float) -> CommandResult:
        assert 0 < timeout_seconds <= 60
        if command == "/usr/bin/id -u":
            self.events.append("id-u")
            return CommandResult(exit_status=0, stdout=b"0\n", stderr=b"")
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
        assert 0 < kwargs["timeout_seconds"] <= 60
        self.events.append("connect_password")
        self.connect_calls.append(kwargs)
        return self.password_session

    def connect(self, **kwargs: Any) -> FakeSshSession:
        assert 0 < kwargs["timeout_seconds"] <= 60
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
            platform_kind="ssh_edge",
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


def test_api_channel_uses_one_decreasing_deadline_for_all_framing_steps(tmp_path) -> None:
    now = [100.0]
    fake_socket = FakeChannelSocket(
        {
            "request_id": "request",
            "status": "ok",
            "host_key_type": "ssh-ed25519",
            "fingerprint": FINGERPRINT,
        }
    )
    timeouts: list[float] = []
    original_settimeout = fake_socket.settimeout

    def settimeout(value: float) -> None:
        timeouts.append(value)
        original_settimeout(value)
        now[0] += 1.0

    fake_socket.settimeout = settimeout  # type: ignore[method-assign]
    channel = EdgeBootstrapChannel(
        tmp_path / "bootstrap.sock",
        socket_factory=lambda: fake_socket,
        clock=lambda: now[0],
    )

    response = channel.request({"request_id": "request", "operation": "scan_host_key"})

    assert response["status"] == "ok"
    assert timeouts == sorted(timeouts, reverse=True)
    assert timeouts[0] <= 60
    sent = decode_frame(FragmentedSocket([fake_socket.sent]))
    assert 100.0 < sent["_deadline_monotonic"] <= 160.0


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


def test_server_refuses_to_unlink_non_socket_entry(tmp_path) -> None:
    socket_path = tmp_path / "bootstrap.sock"
    socket_path.write_text("not-a-socket", encoding="ascii")
    server = BootstrapServer(
        Settings(
            _env_file=None,
            edge_bootstrap_socket=socket_path,
            edge_credential_master_key_file=tmp_path / "unused",
        ),
        session_factory=lambda: pytest.fail("session should not be opened while binding"),
        security_initializer=lambda settings: _security(FakeSshClient()),
    )

    with pytest.raises(RuntimeError, match="refusing unsafe bootstrap socket entry"):
        server.start()

    assert socket_path.read_text(encoding="ascii") == "not-a-socket"


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="AF_UNIX is unavailable")
def test_server_refuses_active_socket_and_only_unlinks_its_own_inode(tmp_path) -> None:
    socket_path = tmp_path / "bootstrap.sock"
    active = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    active.bind(str(socket_path))
    active.listen(1)
    server = BootstrapServer(
        Settings(
            _env_file=None,
            edge_bootstrap_socket=socket_path,
            edge_credential_master_key_file=tmp_path / "unused",
        ),
        session_factory=lambda: pytest.fail("session should not be opened while binding"),
        security_initializer=lambda settings: _security(FakeSshClient()),
    )
    try:
        with pytest.raises(RuntimeError, match="already active"):
            server.start()
        assert socket_path.exists()
    finally:
        active.close()
        socket_path.unlink(missing_ok=True)


@pytest.mark.skipif(os.name == "nt", reason="POSIX socket ownership and inode semantics required")
def test_server_initializes_security_before_unlinking_or_binding(tmp_path, monkeypatch) -> None:
    events: list[str] = []
    socket_path = tmp_path / "bootstrap.sock"
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(socket_path))
    stale.close()
    backing_listener: socket.socket | None = None

    class FakeListener:
        def bind(self, path: str) -> None:
            nonlocal backing_listener
            assert not socket_path.exists()
            backing_listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            backing_listener.bind(path)
            events.append(f"bind:{path}")

        def listen(self, backlog: int) -> None:
            events.append(f"listen:{backlog}")

        def settimeout(self, timeout: float) -> None:
            events.append(f"timeout:{timeout}")

        def close(self) -> None:
            if backing_listener is not None:
                backing_listener.close()
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
    server.stop()


def test_server_stop_preserves_replacement_socket_entry(tmp_path, monkeypatch) -> None:
    socket_path = tmp_path / "bootstrap.sock"
    current_uid = os.getuid() if hasattr(os, "getuid") else 0
    replacement = type(
        "SocketAttributes",
        (),
        {
            "st_mode": stat.S_IFSOCK | 0o600,
            "st_dev": 1,
            "st_ino": 11,
            "st_uid": current_uid,
        },
    )()
    unlinked: list[Path] = []

    monkeypatch.setattr(os, "lstat", lambda path: replacement)
    monkeypatch.setattr(Path, "unlink", lambda self, missing_ok=False: unlinked.append(self))
    server = BootstrapServer(
        Settings(
            _env_file=None,
            edge_bootstrap_socket=socket_path,
            edge_credential_master_key_file=tmp_path / "unused",
        ),
        session_factory=lambda: pytest.fail("session should not be opened"),
    )
    server._socket_identity = (1, 10)

    server.stop()

    assert unlinked == []


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
    assert ssh_client.events[:4] == [
        "connect_password",
        "upload:bootstrap_user.sh",
        "upload:request.json",
        "id-u",
    ]
    assert ssh_client.events[4].startswith("run:/bin/bash ")
    assert ssh_client.events[5:7] == ["workspace-cleanup", "connect_key"]
    assert ssh_client.connect_calls[0]["expected_fingerprint"] == FINGERPRINT
    assert ssh_client.connect_calls[1]["expected_fingerprint"] == FINGERPRINT
    assert "one-time-secret" not in json.dumps(response)
    uploaded_paths = [path for path, _ in ssh_client.password_session.uploads]
    assert all(path.startswith("/home/operator/.visiox-") for path in uploaded_paths)
    assert [Path(path).name for path in uploaded_paths] == ["bootstrap_user.sh", "request.json"]
    script = ssh_client.password_session.uploads[0][1]
    data = json.loads(ssh_client.password_session.uploads[1][1])
    assert b"one-time-secret" not in script
    assert "one-time-secret" not in json.dumps(data)
    assert data["public_key"].startswith("ssh-ed25519 ")
    assert data["operation"] == "bootstrap_add"
    assert data["node_id"] == response["node_id"]
    assert data["key_version"] == 1

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
        assert session.scalar(select(func.count()).select_from(Task)) == 0
        assert session.scalar(select(func.count()).select_from(NodeEvent)) == 0


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
    assert any(
        json.loads(data)["operation"] == "remove_key"
        for path, data in ssh_client.password_session.uploads
        if path.endswith("request.json")
    )
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
    assert any(
        json.loads(data)["operation"] == "remove_key"
        for path, data in ssh_client.password_session.uploads
        if path.endswith("request.json")
    )
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
    compensation_data = json.loads(ssh_client.old_key_session.uploads[3][1])
    assert rotation_data["operation"] == "rotate_add"
    assert compensation_data["operation"] == "remove_key"
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
        "cleanup_required": True,
    }
    add_data = json.loads(ssh_client.old_key_session.uploads[1][1])
    cleanup_data = json.loads(ssh_client.cleanup_session.uploads[1][1])
    assert add_data["operation"] == "rotate_add"
    assert cleanup_data["operation"] == "rotate_commit"
    with session_factory() as session:
        credential = session.scalar(select(EdgeSshCredential))
        assert credential is not None
        assert credential.public_key != "ssh-ed25519 old"


def test_bootstrap_script_is_idempotent_and_keeps_dynamic_data_out_of_commands() -> None:
    script_resource = resources.files("visiox_edge_executor_worker").joinpath(
        "remote", "bootstrap_user.sh"
    )
    script = script_resource.read_text(encoding="utf-8")

    assert "id -u visiox-edge" in script
    assert "useradd" in script
    assert "usermod -p x visiox-edge" in script
    assert "usermod -aG docker visiox-edge" in script
    assert "install -d -m 0700" in script
    assert "chmod 0600" in script
    assert "authorized_keys" in script
    assert "bootstrap_add" in script
    assert "remove_key" in script
    assert "visiox-edge:" in script
    assert "managed_pattern" in script
    assert "managed_line not in lines" in script
    assert "sudo -n" in script
    assert "$(id -u)" in script
    assert 'if [ "${CURRENT_USER}" = "${EDGE_USER}" ]' in script
    assert "one-time" not in script.lower()
    assert "password" not in script.lower()
    assert "eval " not in script
    assert "echo " not in script
    assert "/tmp/visiox-bootstrap" not in script


def test_bootstrap_script_elevates_managed_key_operations_only_when_required() -> None:
    script = resources.files("visiox_edge_executor_worker").joinpath(
        "remote", "bootstrap_user.sh"
    ).read_text(encoding="utf-8")

    assert 'CURRENT_USER="$(id -un)"' in script
    assert 'if [ "${CURRENT_USER}" != "${EDGE_USER}" ]' in script
    assert "KEY_PRIVILEGE=(sudo -n)" in script
    assert '"${KEY_PRIVILEGE[@]}" python3 -' in script
    assert '"${KEY_PRIVILEGE[@]}" mktemp' in script
    assert 'elif [ "$(id -un)" != "${EDGE_USER}" ]; then\n    exit 2' not in script


def test_bootstrap_cli_initializes_security_before_server_start(monkeypatch, tmp_path) -> None:
    events: list[str] = []
    settings = Settings(
        _env_file=None,
        edge_bootstrap_socket=tmp_path / "bootstrap.sock",
        edge_credential_master_key_file=tmp_path / "unused",
    )
    security = _security(FakeSshClient())

    monkeypatch.setattr(
        "visiox_edge_executor_worker.bootstrap_server.get_settings",
        lambda: settings,
    )
    monkeypatch.setattr(
        "visiox_edge_executor_worker.bootstrap_server.initialize_security",
        lambda configured: events.append("initialize_security") or security,
    )
    monkeypatch.setattr(
        "visiox_edge_executor_worker.bootstrap_server.create_session_factory",
        lambda: events.append("session_factory") or object(),
    )

    class FakeServer:
        def __init__(self, configured, factory, *, security_context):
            assert security_context is security
            events.append("server")

        def serve_forever(self) -> None:
            events.append("serve")

        def stop(self) -> None:
            events.append("stop")

    monkeypatch.setattr(
        "visiox_edge_executor_worker.bootstrap_server.BootstrapServer",
        FakeServer,
    )

    main()

    assert events == ["initialize_security", "session_factory", "server", "serve", "stop"]
