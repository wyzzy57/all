import base64
import hashlib
import socket as socket_module

import paramiko
import pytest

from visiox_edge_executor_worker.ssh import (
    HostKeyMismatchError,
    SftpTransferError,
    SftpTransferTimeoutError,
    SshAuthenticationError,
    SshCommandOutputLimitError,
    SshCommandTimeoutError,
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
    def __init__(
        self,
        *,
        stdout_chunks: list[bytes] | None = None,
        stderr_chunks: list[bytes] | None = None,
        never_exits: bool = False,
        exec_error: Exception | None = None,
        invoke_error: Exception | None = None,
    ) -> None:
        self.stdout_chunks = list(stdout_chunks or [])
        self.stderr_chunks = list(stderr_chunks or [])
        self.never_exits = never_exits
        self.exec_error = exec_error
        self.invoke_error = invoke_error
        self.command: str | None = None
        self.closed = False
        self.exit_status_read = False
        self.subsystem: str | None = None
        self.timeouts: list[float] = []

    def settimeout(self, timeout: float) -> None:
        self.timeouts.append(timeout)

    def exec_command(self, command: str) -> None:
        self.command = command
        if self.exec_error is not None:
            raise self.exec_error

    def recv_ready(self) -> bool:
        return bool(self.stdout_chunks)

    def recv(self, size: int) -> bytes:
        return self._take_chunk(self.stdout_chunks, size)

    def recv_stderr_ready(self) -> bool:
        return bool(self.stderr_chunks)

    def recv_stderr(self, size: int) -> bytes:
        return self._take_chunk(self.stderr_chunks, size)

    def exit_status_ready(self) -> bool:
        return not self.never_exits

    def recv_exit_status(self) -> int:
        assert not self.stdout_chunks
        assert not self.stderr_chunks
        self.exit_status_read = True
        return 7

    def invoke_subsystem(self, subsystem: str) -> None:
        self.subsystem = subsystem
        if self.invoke_error is not None:
            raise self.invoke_error

    def close(self) -> None:
        self.closed = True

    @staticmethod
    def _take_chunk(chunks: list[bytes], size: int) -> bytes:
        chunk = chunks.pop(0)
        if len(chunk) > size:
            chunks.insert(0, chunk[size:])
            return chunk[:size]
        return chunk


class FakeTransport:
    def __init__(
        self,
        socket: FakeSocket,
        remote_key: FakeHostKey,
        *,
        channel: FakeChannel | None = None,
        open_error: Exception | None = None,
        close_error: Exception | None = None,
    ) -> None:
        self.socket = socket
        self.remote_key = remote_key
        self.channel = channel or FakeChannel()
        self.open_error = open_error
        self.close_error = close_error
        self.authenticated = False
        self.auth_publickey_called = False
        self.auth_password_called = False
        self.closed = False
        self.banner_timeout: float | None = None
        self.auth_timeout: float | None = None
        self.events: list[str] = []
        self.open_timeouts: list[float] = []

    def start_client(self, timeout: float) -> None:
        self.start_timeout = timeout
        self.events.append("start_client")

    def get_remote_server_key(self) -> FakeHostKey:
        self.events.append("get_remote_server_key")
        return self.remote_key

    def auth_publickey(self, username: str, key: object) -> None:
        self.events.append("auth_publickey")
        self.auth_publickey_called = True
        self.authenticated = True

    def auth_password(self, username: str, password: str) -> None:
        self.events.append("auth_password")
        self.auth_password_called = True
        self.authenticated = True

    def is_authenticated(self) -> bool:
        return self.authenticated

    def open_session(self, timeout: float) -> FakeChannel:
        self.open_timeouts.append(timeout)
        if self.open_error is not None:
            raise self.open_error
        return self.channel

    def close(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


class FakeSftpClient:
    def __init__(self, channel: FakeChannel, *, put_error: Exception | None = None) -> None:
        self.channel = channel
        self.put_error = put_error
        self.closed = False
        self.written = b""
        self.remote_path: str | None = None

    def putfo(self, source, remote_path: str) -> None:
        self.written = source.read()
        self.remote_path = remote_path
        if self.put_error is not None:
            raise self.put_error

    def close(self) -> None:
        self.closed = True


def _fingerprint(key: FakeHostKey) -> str:
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def _connected_session(
    *,
    channel: FakeChannel | None = None,
    open_error: Exception | None = None,
    max_output_bytes: int = 4 * 1024 * 1024,
    sftp_client_factory=None,
):
    remote_key = FakeHostKey("ssh-ed25519", b"host-key-a")
    fake_socket = FakeSocket()
    transport = FakeTransport(fake_socket, remote_key, channel=channel, open_error=open_error)
    client = StrictSshClient(
        connect_timeout_seconds=3,
        auth_timeout_seconds=4,
        banner_timeout_seconds=5,
        max_output_bytes=max_output_bytes,
        socket_factory=lambda address, timeout: fake_socket,
        transport_factory=lambda opened_socket: transport,
        sftp_client_factory=sftp_client_factory,
    )
    session = client.connect(
        host="edge.example",
        port=22,
        username="edge",
        private_key=object(),
        expected_fingerprint=_fingerprint(remote_key),
    )
    return session, transport, fake_socket


def test_scan_host_key_returns_a_structured_sha256_fingerprint() -> None:
    remote_key = FakeHostKey("ssh-ed25519", b"host-key-a")
    fake_socket = FakeSocket()

    scanned = scan_host_key(
        "edge.example",
        22,
        timeout=3,
        socket_factory=lambda address, timeout: fake_socket,
        transport_factory=lambda opened_socket: FakeTransport(opened_socket, remote_key),
    )

    assert scanned.host_key_type == "ssh-ed25519"
    assert scanned.fingerprint == _fingerprint(remote_key)
    assert fake_socket.closed


@pytest.mark.parametrize("operation", ["scan", "connect"])
def test_transport_factory_failure_always_closes_socket(operation: str) -> None:
    remote_key = FakeHostKey("ssh-ed25519", b"host-key-a")
    fake_socket = FakeSocket()

    def fail_transport_factory(opened_socket):
        raise RuntimeError("transport construction failed")

    with pytest.raises(RuntimeError, match="transport construction failed"):
        if operation == "scan":
            scan_host_key(
                "edge.example",
                22,
                timeout=3,
                socket_factory=lambda address, timeout: fake_socket,
                transport_factory=fail_transport_factory,
            )
        else:
            StrictSshClient(
                connect_timeout_seconds=3,
                auth_timeout_seconds=4,
                banner_timeout_seconds=5,
                socket_factory=lambda address, timeout: fake_socket,
                transport_factory=fail_transport_factory,
            ).connect(
                host="edge.example",
                port=22,
                username="edge",
                private_key=object(),
                expected_fingerprint=_fingerprint(remote_key),
            )

    assert fake_socket.closed


@pytest.mark.parametrize("operation", ["scan", "connect"])
def test_transport_close_failure_still_closes_socket(operation: str) -> None:
    remote_key = FakeHostKey("ssh-ed25519", b"host-key-a")
    fake_socket = FakeSocket()
    transport = FakeTransport(fake_socket, remote_key, close_error=RuntimeError("close failed"))

    if operation == "scan":
        scanned = scan_host_key(
            "edge.example",
            22,
            timeout=3,
            socket_factory=lambda address, timeout: fake_socket,
            transport_factory=lambda opened_socket: transport,
        )
        assert scanned.fingerprint == _fingerprint(remote_key)
    else:
        session = StrictSshClient(
            connect_timeout_seconds=3,
            auth_timeout_seconds=4,
            banner_timeout_seconds=5,
            socket_factory=lambda address, timeout: fake_socket,
            transport_factory=lambda opened_socket: transport,
        ).connect(
            host="edge.example",
            port=22,
            username="edge",
            private_key=object(),
            expected_fingerprint=_fingerprint(remote_key),
        )
        session.close()

    assert fake_socket.closed


def test_strict_client_rejects_changed_host_key_before_authentication() -> None:
    expected_key = FakeHostKey("ssh-ed25519", b"host-key-a")
    changed_key = FakeHostKey("ssh-ed25519", b"host-key-b")
    fake_socket = FakeSocket()
    transport = FakeTransport(fake_socket, changed_key)
    client = StrictSshClient(
        connect_timeout_seconds=3,
        auth_timeout_seconds=4,
        banner_timeout_seconds=5,
        socket_factory=lambda address, timeout: fake_socket,
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
    assert fake_socket.closed


def test_password_authentication_occurs_only_after_strict_host_key_check() -> None:
    remote_key = FakeHostKey("ssh-ed25519", b"host-key-a")
    fake_socket = FakeSocket()
    transport = FakeTransport(fake_socket, remote_key)
    client = StrictSshClient(
        connect_timeout_seconds=3,
        auth_timeout_seconds=4,
        banner_timeout_seconds=5,
        socket_factory=lambda address, timeout: fake_socket,
        transport_factory=lambda opened_socket: transport,
    )

    session = client.connect_password(
        host="edge.example",
        port=22,
        username="edge",
        password="sensitive-password",
        expected_fingerprint=_fingerprint(remote_key),
    )
    session.close()

    assert transport.events[:3] == ["start_client", "get_remote_server_key", "auth_password"]
    assert transport.auth_password_called


def test_password_authentication_error_is_fixed_and_redacted() -> None:
    remote_key = FakeHostKey("ssh-ed25519", b"host-key-a")
    fake_socket = FakeSocket()
    transport = FakeTransport(fake_socket, remote_key)

    def leak_password(username: str, password: str) -> None:
        raise RuntimeError(f"rejected {password}")

    transport.auth_password = leak_password
    client = StrictSshClient(
        connect_timeout_seconds=3,
        auth_timeout_seconds=4,
        banner_timeout_seconds=5,
        socket_factory=lambda address, timeout: fake_socket,
        transport_factory=lambda opened_socket: transport,
    )

    with pytest.raises(SshAuthenticationError, match=r"^SSH authentication failed$") as exc_info:
        client.connect_password(
            host="edge.example",
            port=22,
            username="edge",
            password="sensitive-password",
            expected_fingerprint=_fingerprint(remote_key),
        )

    assert "sensitive-password" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None


def test_command_drains_large_stdout_and_stderr_before_reading_exit_status() -> None:
    stdout = b"o" * (192 * 1024)
    stderr = b"e" * (160 * 1024)
    channel = FakeChannel(
        stdout_chunks=[stdout[:70_000], stdout[70_000:]],
        stderr_chunks=[stderr[:80_000], stderr[80_000:]],
    )
    session, transport, _ = _connected_session(channel=channel)

    result = session.run("produce-output", timeout_seconds=2)
    session.close()

    assert result.stdout == stdout
    assert result.stderr == stderr
    assert result.exit_status == 7
    assert channel.exit_status_read
    assert transport.open_timeouts and transport.open_timeouts[0] <= 2
    assert channel.closed


def test_command_that_never_exits_times_out_and_closes_channel() -> None:
    channel = FakeChannel(never_exits=True)
    session, _, _ = _connected_session(channel=channel)

    with pytest.raises(SshCommandTimeoutError, match=r"^SSH command timed out$"):
        session.run("hang", timeout_seconds=0.01)

    assert channel.closed


def test_command_deadline_remains_active_while_output_is_continuously_ready(monkeypatch) -> None:
    channel = FakeChannel(stdout_chunks=[b"chunk"] * 10)
    session, _, _ = _connected_session(channel=channel)
    clock_values = iter([0.0, 0.1, 0.2, 0.3, 1.1])
    monkeypatch.setattr(
        "visiox_edge_executor_worker.ssh.time.monotonic",
        lambda: next(clock_values),
    )

    with pytest.raises(SshCommandTimeoutError, match=r"^SSH command timed out$"):
        session.run("continuous-output", timeout_seconds=1)

    assert channel.closed


def test_command_open_timeout_is_fixed_and_redacted() -> None:
    session, _, _ = _connected_session(
        open_error=paramiko.SSHException("Timeout opening channel.")
    )

    with pytest.raises(SshCommandTimeoutError, match=r"^SSH command timed out$"):
        session.run("secret-command", timeout_seconds=1)


def test_command_exec_timeout_is_fixed_and_closes_channel() -> None:
    channel = FakeChannel(exec_error=socket_module.timeout("exec timeout"))
    session, _, _ = _connected_session(channel=channel)

    with pytest.raises(SshCommandTimeoutError, match=r"^SSH command timed out$"):
        session.run("secret-command", timeout_seconds=1)

    assert channel.closed


def test_command_output_limit_is_explicit_and_never_silently_truncates() -> None:
    channel = FakeChannel(stdout_chunks=[b"a" * 65], stderr_chunks=[b"b" * 64])
    session, _, _ = _connected_session(channel=channel, max_output_bytes=128)

    with pytest.raises(SshCommandOutputLimitError, match=r"^SSH command output exceeded limit$"):
        session.run("too-loud", timeout_seconds=1)

    assert channel.closed


def test_sftp_upload_uses_bounded_explicit_channel_and_closes_resources() -> None:
    channel = FakeChannel()
    created: list[FakeSftpClient] = []

    def create_sftp(opened_channel: FakeChannel) -> FakeSftpClient:
        client = FakeSftpClient(opened_channel)
        created.append(client)
        return client

    session, transport, _ = _connected_session(channel=channel, sftp_client_factory=create_sftp)

    result = session.upload_bytes(b"payload", "/tmp/payload", timeout_seconds=2)
    session.close()

    assert transport.open_timeouts and transport.open_timeouts[0] <= 2
    assert channel.subsystem == "sftp"
    assert channel.timeouts
    assert created[0].written == b"payload"
    assert created[0].remote_path == "/tmp/payload"
    assert created[0].closed
    assert channel.closed
    assert result.bytes_transferred == 7


@pytest.mark.parametrize("failure_stage", ["open", "invoke", "write"])
def test_sftp_timeout_is_fixed_and_closes_every_created_resource(failure_stage: str) -> None:
    channel = FakeChannel(
        invoke_error=socket_module.timeout("invoke timeout") if failure_stage == "invoke" else None
    )
    open_error = (
        paramiko.SSHException("Timeout opening channel.") if failure_stage == "open" else None
    )
    created: list[FakeSftpClient] = []

    def create_sftp(opened_channel: FakeChannel) -> FakeSftpClient:
        client = FakeSftpClient(
            opened_channel,
            put_error=socket_module.timeout("write timeout") if failure_stage == "write" else None,
        )
        created.append(client)
        return client

    session, _, _ = _connected_session(
        channel=channel,
        open_error=open_error,
        sftp_client_factory=create_sftp,
    )

    with pytest.raises(SftpTransferTimeoutError, match=r"^SFTP upload timed out$"):
        session.upload_bytes(b"payload", "/tmp/payload", timeout_seconds=1)

    if failure_stage != "open":
        assert channel.closed
    if failure_stage == "write":
        assert created[0].closed


def test_sftp_non_timeout_failure_is_fixed_and_closes_sftp_and_channel() -> None:
    channel = FakeChannel()
    created: list[FakeSftpClient] = []

    def create_sftp(opened_channel: FakeChannel) -> FakeSftpClient:
        client = FakeSftpClient(opened_channel, put_error=RuntimeError("leaked transport detail"))
        created.append(client)
        return client

    session, _, _ = _connected_session(channel=channel, sftp_client_factory=create_sftp)

    with pytest.raises(SftpTransferError, match=r"^SFTP upload failed$") as exc_info:
        session.upload_bytes(b"payload", "/tmp/payload", timeout_seconds=1)

    assert exc_info.value.__cause__ is None
    assert created[0].closed
    assert channel.closed


def test_sftp_client_construction_failure_closes_channel() -> None:
    channel = FakeChannel()

    def fail_sftp_construction(opened_channel: FakeChannel):
        raise RuntimeError("construction failed")

    session, _, _ = _connected_session(
        channel=channel,
        sftp_client_factory=fail_sftp_construction,
    )

    with pytest.raises(SftpTransferError, match=r"^SFTP upload failed$"):
        session.upload_bytes(b"payload", "/tmp/payload", timeout_seconds=1)

    assert channel.closed
