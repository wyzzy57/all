import base64
import hashlib

import pytest

from visiox_edge_executor_worker.ssh import (
    HostKeyMismatchError,
    StrictSshClient,
    scan_host_key,
)


class FakeSocket:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeHostKey:
    def __init__(self, name: str, value: bytes) -> None:
        self.name = name
        self.value = value

    def get_name(self) -> str:
        return self.name

    def asbytes(self) -> bytes:
        return self.value


class FakeChannel:
    def __init__(self) -> None:
        self.command: str | None = None
        self.closed = False

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def exec_command(self, command: str) -> None:
        self.command = command

    def recv(self, size: int) -> bytes:
        return b"command output"

    def recv_stderr(self, size: int) -> bytes:
        return b"command error"

    def recv_exit_status(self) -> int:
        return 7

    def close(self) -> None:
        self.closed = True


class FakeTransport:
    def __init__(self, socket: FakeSocket, remote_key: FakeHostKey) -> None:
        self.socket = socket
        self.remote_key = remote_key
        self.authenticated = False
        self.auth_publickey_called = False
        self.closed = False
        self.banner_timeout: float | None = None
        self.auth_timeout: float | None = None
        self.channel = FakeChannel()

    def start_client(self, timeout: float) -> None:
        self.start_timeout = timeout

    def get_remote_server_key(self) -> FakeHostKey:
        return self.remote_key

    def auth_publickey(self, username: str, key: object) -> None:
        self.auth_publickey_called = True
        self.authenticated = True

    def is_authenticated(self) -> bool:
        return self.authenticated

    def open_session(self) -> FakeChannel:
        return self.channel

    def close(self) -> None:
        self.closed = True


def _fingerprint(key: FakeHostKey) -> str:
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def test_scan_host_key_returns_a_structured_sha256_fingerprint() -> None:
    remote_key = FakeHostKey("ssh-ed25519", b"host-key-a")
    socket = FakeSocket()

    scanned = scan_host_key(
        "edge.example",
        22,
        timeout=3,
        socket_factory=lambda address, timeout: socket,
        transport_factory=lambda opened_socket: FakeTransport(opened_socket, remote_key),
    )

    assert scanned.host_key_type == "ssh-ed25519"
    assert scanned.fingerprint == _fingerprint(remote_key)
    assert socket.closed


def test_strict_client_rejects_changed_host_key_before_authentication() -> None:
    expected_key = FakeHostKey("ssh-ed25519", b"host-key-a")
    changed_key = FakeHostKey("ssh-ed25519", b"host-key-b")
    socket = FakeSocket()
    transport = FakeTransport(socket, changed_key)
    client = StrictSshClient(
        connect_timeout_seconds=3,
        auth_timeout_seconds=4,
        banner_timeout_seconds=5,
        socket_factory=lambda address, timeout: socket,
        transport_factory=lambda opened_socket: transport,
    )

    with pytest.raises(HostKeyMismatchError):
        client.connect(
            host="edge.example",
            port=22,
            username="edge",
            private_key=object(),
            expected_fingerprint=_fingerprint(expected_key),
        )

    assert not transport.auth_publickey_called
    assert transport.closed


def test_strict_client_caps_timeouts_and_returns_structured_command_result() -> None:
    remote_key = FakeHostKey("ssh-ed25519", b"host-key-a")
    socket = FakeSocket()
    transport = FakeTransport(socket, remote_key)
    client = StrictSshClient(
        connect_timeout_seconds=3,
        auth_timeout_seconds=4,
        banner_timeout_seconds=5,
        socket_factory=lambda address, timeout: socket,
        transport_factory=lambda opened_socket: transport,
    )

    session = client.connect(
        host="edge.example",
        port=22,
        username="edge",
        private_key=object(),
        expected_fingerprint=_fingerprint(remote_key),
    )
    result = session.run("uname -a", timeout=2)
    session.close()

    assert transport.banner_timeout == 5
    assert transport.auth_timeout == 4
    assert transport.start_timeout == 3
    assert result.exit_status == 7
    assert result.stdout == b"command output"
    assert result.stderr == b"command error"
    assert transport.channel.command == "uname -a"
    assert transport.channel.closed
    assert transport.closed
