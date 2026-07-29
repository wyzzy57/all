import base64
import hashlib
import os
from pathlib import Path
import socket as socket_module
import stat
import threading
import time

import paramiko
import pytest

import visiox_edge_executor_worker.ssh as ssh_module
from visiox_edge_executor_worker.ssh import (
    HostKeyMismatchError,
    RemotePrivateDirectory,
    SftpTransferError,
    SftpTransferTimeoutError,
    SshAuthenticationError,
    SshCommandError,
    SshCommandOutputLimitError,
    SshCommandTimeoutError,
    SshSessionClosedError,
    StrictSshClient,
    scan_host_key,
)


def test_bounded_operation_cleanup_never_extends_total_deadline() -> None:
    release = threading.Event()
    cleanup_started = threading.Event()
    cleanup_release = threading.Event()
    timeout_seconds = 0.05
    started = time.monotonic()

    def blocking_cleanup() -> None:
        cleanup_started.set()
        cleanup_release.wait()

    with pytest.raises(TimeoutError, match=r"^operation timed out$"):
        ssh_module._run_with_deadline(
            release.wait,
            deadline=started + timeout_seconds,
            timeout_error=TimeoutError,
            timeout_message="operation timed out",
            on_timeout=blocking_cleanup,
            scheduler_guard_seconds=0.01,
            cleanup_join_seconds=1.0,
        )

    elapsed = time.monotonic() - started
    release.set()
    cleanup_release.set()
    assert cleanup_started.is_set()
    assert timeout_seconds * 0.75 <= elapsed < timeout_seconds * 1.5


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


class SlowBannerTransport(FakeTransport):
    def start_client(self, timeout: float) -> None:
        time.sleep(0.078)
        super().start_client(timeout)


class SlowAuthenticationTransport(FakeTransport):
    def auth_publickey(self, username: str, key: object) -> None:
        time.sleep(0.078)
        super().auth_publickey(username, key)


class BlockingParamikoRequestTransport(FakeTransport):
    """Attach a real Paramiko channel whose request event is never acknowledged."""

    def __init__(self, socket: FakeSocket, remote_key: FakeHostKey) -> None:
        super().__init__(socket, remote_key)
        self.channel = paramiko.Channel(17)
        self.channel.active = True
        self.channel.transport = self
        self.request_started = threading.Event()

    def _send_user_message(self, message) -> None:
        self.request_started.set()

    def get_exception(self):
        return None

    def _unlink_channel(self, channel_id: int) -> None:
        pass

    def close(self) -> None:
        self.closed = True
        self.channel._unlink()


class BlockingOpenTransport(FakeTransport):
    """Model Paramiko's registered-but-not-yet-returned channel-open wait."""

    def __init__(self, socket: FakeSocket, remote_key: FakeHostKey) -> None:
        super().__init__(socket, remote_key)
        self.open_started = threading.Event()
        self.open_released = threading.Event()
        self.registered_channel: paramiko.Channel | None = None

    def open_session(self, timeout: float) -> paramiko.Channel:
        self.open_timeouts.append(timeout)
        channel = paramiko.Channel(23)
        channel.active = True
        channel.transport = self
        self.registered_channel = channel
        self.open_started.set()
        self.open_released.wait()
        raise paramiko.SSHException("channel open interrupted")

    def _unlink_channel(self, channel_id: int) -> None:
        pass

    def close(self) -> None:
        self.closed = True
        if self.registered_channel is not None:
            self.registered_channel._unlink()
        self.open_released.set()


class AcknowledgedParamikoRequestTransport(BlockingParamikoRequestTransport):
    def _send_user_message(self, message) -> None:
        self.request_started.set()
        self.channel.event_ready = True
        self.channel.event.set()


class SlowChunkSftpClient:
    def __init__(self, channel: paramiko.Channel) -> None:
        self.channel = channel
        self.closed = False
        self.chunk_count = 0

    def putfo(self, source, remote_path: str) -> None:
        while source.read(1):
            if self.channel.closed:
                raise socket_module.timeout("transport closed")
            self.chunk_count += 1
            time.sleep(0.005)

    def close(self) -> None:
        self.closed = True


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


class LocalFilesystemSftp:
    """Exercise Paramiko-style SFTP calls against real local filesystem semantics."""

    def __init__(self, channel: FakeChannel, home: Path) -> None:
        self.channel = channel
        self.home = home
        self.closed = False

    def normalize(self, path: str) -> str:
        assert path == "."
        return self.home.as_posix()

    def lstat(self, path: str):
        return os.lstat(path)

    def mkdir(self, path: str, mode: int) -> None:
        os.mkdir(path, mode)

    def chmod(self, path: str, mode: int) -> None:
        os.chmod(path, mode)

    def file(self, path: str, mode: str):
        return open(path, mode + "b")

    def remove(self, path: str) -> None:
        os.remove(path)

    def rmdir(self, path: str) -> None:
        os.rmdir(path)

    def close(self) -> None:
        self.closed = True


class ExistingFileSftp:
    def __init__(self, channel: FakeChannel) -> None:
        self.channel = channel
        self.removed: list[str] = []
        self.closed = False

    def file(self, path: str, mode: str):
        assert mode == "wx"
        raise FileExistsError(path)

    def remove(self, path: str) -> None:
        self.removed.append(path)

    def close(self) -> None:
        self.closed = True


class InvalidPrivateDirectorySftp:
    def __init__(self, channel: FakeChannel) -> None:
        self.channel = channel
        self.created_path: str | None = None
        self.removed_directories: list[str] = []
        self.closed = False

    def normalize(self, path: str) -> str:
        assert path == "."
        return "/home/operator"

    def lstat(self, path: str):
        if path == "/home/operator":
            mode = stat.S_IFDIR | 0o700
        else:
            assert path == self.created_path
            mode = stat.S_IFDIR | 0o770
        return type("Attributes", (), {"st_mode": mode, "st_uid": 1000})()

    def mkdir(self, path: str, mode: int) -> None:
        assert mode == 0o700
        self.created_path = path

    def chmod(self, path: str, mode: int) -> None:
        assert path == self.created_path
        assert mode == 0o700

    def rmdir(self, path: str) -> None:
        self.removed_directories.append(path)

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


def _session_for_transport(transport, fake_socket, *, sftp_client_factory=None):
    client = StrictSshClient(
        connect_timeout_seconds=3,
        auth_timeout_seconds=4,
        banner_timeout_seconds=5,
        socket_factory=lambda address, timeout: fake_socket,
        transport_factory=lambda opened_socket: transport,
        sftp_client_factory=sftp_client_factory,
    )
    return client.connect(
        host="edge.example",
        port=22,
        username="edge",
        private_key=object(),
        expected_fingerprint=_fingerprint(transport.remote_key),
    )


def _call_with_hard_test_guard(call, cleanup, hard_limit_seconds: float):
    outcome: dict[str, object] = {}

    def invoke() -> None:
        try:
            outcome["result"] = call()
        except BaseException as error:
            outcome["error"] = error

    started = time.monotonic()
    caller = threading.Thread(target=invoke, daemon=True)
    caller.start()
    caller.join(hard_limit_seconds)
    elapsed = time.monotonic() - started
    if caller.is_alive():
        cleanup()
        caller.join(0.1)
        pytest.fail(f"operation exceeded hard test guard of {hard_limit_seconds:.3f}s")
    return outcome, elapsed


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


def test_authenticated_connect_accepts_a_caller_total_deadline() -> None:
    remote_key = FakeHostKey("ssh-ed25519", b"host-key-a")
    fake_socket = FakeSocket()
    transport = FakeTransport(fake_socket, remote_key)
    socket_timeouts: list[float] = []
    client = StrictSshClient(
        connect_timeout_seconds=10,
        auth_timeout_seconds=10,
        banner_timeout_seconds=10,
        socket_factory=lambda address, timeout: socket_timeouts.append(timeout) or fake_socket,
        transport_factory=lambda opened_socket: transport,
    )

    session = client.connect(
        host="edge.example",
        port=22,
        username="edge",
        private_key=object(),
        expected_fingerprint=_fingerprint(remote_key),
        timeout_seconds=0.5,
    )
    session.close()

    assert socket_timeouts and 0 < socket_timeouts[0] <= 0.5
    assert 0 < transport.start_timeout <= 0.5
    assert 0 < transport.auth_timeout <= 0.5


@pytest.mark.parametrize(
    ("transport_type", "expected_error"),
    [
        (SlowBannerTransport, TimeoutError),
        (SlowAuthenticationTransport, SshAuthenticationError),
    ],
    ids=["banner", "authentication"],
)
def test_authenticated_connect_enforces_fifty_millisecond_wall_clock_deadline(
    transport_type,
    expected_error,
) -> None:
    remote_key = FakeHostKey("ssh-ed25519", b"host-key-a")
    fake_socket = FakeSocket()
    transport = transport_type(fake_socket, remote_key)
    client = StrictSshClient(
        connect_timeout_seconds=10,
        auth_timeout_seconds=10,
        banner_timeout_seconds=10,
        socket_factory=lambda address, timeout: fake_socket,
        transport_factory=lambda opened_socket: transport,
    )

    started = time.monotonic()
    with pytest.raises(expected_error):
        client.connect(
            host="edge.example",
            port=22,
            username="edge",
            private_key=object(),
            expected_fingerprint=_fingerprint(remote_key),
            timeout_seconds=0.05,
        )
    elapsed = time.monotonic() - started

    assert elapsed < 0.078
    assert transport.closed
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


def test_command_allows_bounded_long_running_deployment_deadline() -> None:
    channel = FakeChannel(stdout_chunks=[b"deployment-complete"])
    session, transport, _ = _connected_session(channel=channel)

    result = session.run("deploy-static-script", timeout_seconds=7200)
    session.close()

    assert result.stdout == b"deployment-complete"
    assert 60 < transport.open_timeouts[0] <= 7200


def test_command_that_never_exits_times_out_and_closes_channel() -> None:
    channel = FakeChannel(never_exits=True)
    session, _, _ = _connected_session(channel=channel)

    with pytest.raises(SshCommandTimeoutError, match=r"^SSH command timed out$"):
        session.run("hang", timeout_seconds=0.01)

    assert channel.closed


def test_command_deadline_remains_active_while_output_is_continuously_ready() -> None:
    class ContinuouslyReadyChannel(FakeChannel):
        def __init__(self) -> None:
            super().__init__(never_exits=True)

        def recv_ready(self) -> bool:
            return True

        def recv(self, size: int) -> bytes:
            time.sleep(0.003)
            return b"chunk"

    channel = ContinuouslyReadyChannel()
    session, transport, fake_socket = _connected_session(channel=channel)
    timeout_seconds = 0.04
    started = time.monotonic()

    with pytest.raises(SshCommandTimeoutError, match=r"^SSH command timed out$"):
        session.run("continuous-output", timeout_seconds=timeout_seconds)

    elapsed = time.monotonic() - started
    assert timeout_seconds * 0.75 <= elapsed < timeout_seconds * 2.75
    assert channel.closed
    assert transport.closed
    assert fake_socket.closed


def test_command_timeout_does_not_wait_for_blocking_channel_close() -> None:
    close_started = threading.Event()
    close_release = threading.Event()

    class BlockingCloseChannel(FakeChannel):
        def close(self) -> None:
            close_started.set()
            close_release.wait()
            super().close()

    channel = BlockingCloseChannel(never_exits=True)
    session, _, _ = _connected_session(channel=channel)
    timeout_seconds = 0.05
    started = time.monotonic()

    with pytest.raises(SshCommandTimeoutError, match=r"^SSH command timed out$"):
        session.run("blocking-close", timeout_seconds=timeout_seconds)

    elapsed = time.monotonic() - started
    assert close_started.is_set()
    assert timeout_seconds * 0.75 <= elapsed < timeout_seconds * 1.5
    with pytest.raises(SshSessionClosedError, match=r"^SSH session is closed$"):
        session.run("must-not-run", timeout_seconds=1)
    close_release.set()


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


def test_exclusive_upload_never_removes_a_preexisting_remote_file() -> None:
    channel = FakeChannel()
    existing_sftp = ExistingFileSftp(channel)
    session, _, _ = _connected_session(
        channel=channel,
        sftp_client_factory=lambda opened_channel: existing_sftp,
    )

    with pytest.raises(SftpTransferError, match=r"^SFTP upload failed$"):
        session.upload_bytes_exclusive(
            b"replacement",
            "/home/operator/.visiox-safe/request.json",
            expected_owner_uid=1000,
            timeout_seconds=2,
        )

    assert existing_sftp.removed == []


def test_private_workspace_validation_failure_removes_only_new_directory() -> None:
    channel = FakeChannel()
    invalid_sftp = InvalidPrivateDirectorySftp(channel)
    session, _, _ = _connected_session(
        channel=channel,
        sftp_client_factory=lambda opened_channel: invalid_sftp,
    )

    with pytest.raises(SftpTransferError, match=r"^SFTP directory creation failed$"):
        session.create_private_directory(timeout_seconds=2)

    assert invalid_sftp.created_path is not None
    assert invalid_sftp.removed_directories == [invalid_sftp.created_path]


@pytest.mark.skipif(os.name == "nt", reason="POSIX ownership and mode semantics required")
def test_private_sftp_workspace_uses_random_directory_exclusive_files_and_strict_modes(
    tmp_path: Path,
) -> None:
    channel = FakeChannel()
    session, _, _ = _connected_session(
        channel=channel,
        sftp_client_factory=lambda opened_channel: LocalFilesystemSftp(opened_channel, tmp_path),
    )

    workspace = session.create_private_directory(timeout_seconds=2)
    remote_file = f"{workspace.path}/bootstrap_user.sh"
    session.upload_bytes_exclusive(
        b"payload",
        remote_file,
        expected_owner_uid=workspace.owner_uid,
        timeout_seconds=2,
    )
    session.validate_remote_file(
        remote_file,
        expected_owner_uid=workspace.owner_uid,
        timeout_seconds=2,
    )

    assert isinstance(workspace, RemotePrivateDirectory)
    assert Path(workspace.path).parent == tmp_path
    assert Path(workspace.path).name.startswith(".visiox-")
    assert len(Path(workspace.path).name.removeprefix(".visiox-")) == 32
    assert stat.S_IMODE(os.lstat(workspace.path).st_mode) == 0o700
    assert stat.S_IMODE(os.lstat(remote_file).st_mode) == 0o600
    with pytest.raises(SftpTransferError, match=r"^SFTP upload failed$"):
        session.upload_bytes_exclusive(
            b"replacement",
            remote_file,
            expected_owner_uid=workspace.owner_uid,
            timeout_seconds=2,
        )
    assert Path(remote_file).read_bytes() == b"payload"

    session.cleanup_private_directory(
        workspace,
        (remote_file,),
        timeout_seconds=2,
    )
    assert not Path(workspace.path).exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX ownership and mode semantics required")
def test_remote_file_validation_rejects_group_or_world_writable_file(tmp_path: Path) -> None:
    channel = FakeChannel()
    session, _, _ = _connected_session(
        channel=channel,
        sftp_client_factory=lambda opened_channel: LocalFilesystemSftp(opened_channel, tmp_path),
    )
    workspace = session.create_private_directory(timeout_seconds=2)
    remote_file = f"{workspace.path}/request.json"
    session.upload_bytes_exclusive(
        b"{}",
        remote_file,
        expected_owner_uid=workspace.owner_uid,
        timeout_seconds=2,
    )
    os.chmod(remote_file, 0o620)

    with pytest.raises(SftpTransferError, match=r"^SFTP validation failed$"):
        session.validate_remote_file(
            remote_file,
            expected_owner_uid=workspace.owner_uid,
            timeout_seconds=2,
        )


def test_exec_request_wait_obeys_wall_clock_deadline_and_permanently_closes_session() -> None:
    remote_key = FakeHostKey("ssh-ed25519", b"host-key-a")
    fake_socket = FakeSocket()
    transport = BlockingParamikoRequestTransport(fake_socket, remote_key)
    session = _session_for_transport(transport, fake_socket)
    timeout_seconds = 0.04

    outcome, elapsed = _call_with_hard_test_guard(
        lambda: session.run("secret-command", timeout_seconds=timeout_seconds),
        session.close,
        hard_limit_seconds=timeout_seconds * 2.75,
    )

    assert isinstance(outcome.get("error"), SshCommandTimeoutError)
    assert str(outcome["error"]) == "SSH command timed out"
    assert timeout_seconds * 0.75 <= elapsed < timeout_seconds * 2.75
    assert transport.request_started.is_set()
    assert transport.channel.event.is_set()
    assert transport.channel.closed
    assert transport.closed
    assert fake_socket.closed

    with pytest.raises(SshSessionClosedError, match=r"^SSH session is closed$"):
        session.run("must-not-run", timeout_seconds=1)


def test_sftp_subsystem_request_wait_obeys_wall_clock_deadline_and_closes_everything() -> None:
    remote_key = FakeHostKey("ssh-ed25519", b"host-key-a")
    fake_socket = FakeSocket()
    transport = BlockingParamikoRequestTransport(fake_socket, remote_key)
    session = _session_for_transport(
        transport,
        fake_socket,
        sftp_client_factory=lambda channel: pytest.fail("SFTP construction must not be reached"),
    )
    timeout_seconds = 0.04

    outcome, elapsed = _call_with_hard_test_guard(
        lambda: session.upload_bytes(b"secret-data", "/secret/path", timeout_seconds),
        session.close,
        hard_limit_seconds=timeout_seconds * 2.75,
    )

    assert isinstance(outcome.get("error"), SftpTransferTimeoutError)
    assert str(outcome["error"]) == "SFTP upload timed out"
    assert timeout_seconds * 0.75 <= elapsed < timeout_seconds * 2.75
    assert transport.request_started.is_set()
    assert transport.channel.event.is_set()
    assert transport.channel.closed
    assert transport.closed
    assert fake_socket.closed


def test_sftp_open_wait_closes_registered_channel_that_was_never_returned() -> None:
    remote_key = FakeHostKey("ssh-ed25519", b"host-key-a")
    fake_socket = FakeSocket()
    transport = BlockingOpenTransport(fake_socket, remote_key)
    session = _session_for_transport(transport, fake_socket)
    timeout_seconds = 0.04

    outcome, elapsed = _call_with_hard_test_guard(
        lambda: session.upload_bytes(b"secret-data", "/secret/path", timeout_seconds),
        session.close,
        hard_limit_seconds=timeout_seconds * 2.75,
    )

    assert isinstance(outcome.get("error"), SftpTransferTimeoutError)
    assert str(outcome["error"]) == "SFTP upload timed out"
    assert timeout_seconds * 0.75 <= elapsed < timeout_seconds * 2.75
    assert transport.open_started.is_set()
    assert transport.registered_channel is not None
    assert transport.registered_channel.closed
    assert transport.closed
    assert fake_socket.closed


def test_sftp_continuous_small_chunks_cannot_extend_total_deadline() -> None:
    remote_key = FakeHostKey("ssh-ed25519", b"host-key-a")
    fake_socket = FakeSocket()
    transport = AcknowledgedParamikoRequestTransport(fake_socket, remote_key)
    created: list[SlowChunkSftpClient] = []

    def create_sftp(channel: paramiko.Channel) -> SlowChunkSftpClient:
        client = SlowChunkSftpClient(channel)
        created.append(client)
        return client

    session = _session_for_transport(
        transport,
        fake_socket,
        sftp_client_factory=create_sftp,
    )
    timeout_seconds = 0.05

    outcome, elapsed = _call_with_hard_test_guard(
        lambda: session.upload_bytes(b"x" * 100, "/secret/path", timeout_seconds),
        session.close,
        hard_limit_seconds=timeout_seconds * 2.6,
    )

    assert isinstance(outcome.get("error"), SftpTransferTimeoutError)
    assert str(outcome["error"]) == "SFTP upload timed out"
    assert timeout_seconds * 0.75 <= elapsed < timeout_seconds * 2.6
    assert created[0].chunk_count > 1
    assert created[0].closed
    assert transport.channel.closed
    assert transport.closed
    assert fake_socket.closed


def test_command_failure_is_fixed_and_redacts_remote_command() -> None:
    command = "echo top-secret-command"
    channel = FakeChannel(exec_error=RuntimeError(f"server rejected {command}"))
    session, _, _ = _connected_session(channel=channel)

    with pytest.raises(SshCommandError, match=r"^SSH command failed$") as exc_info:
        session.run(command, timeout_seconds=1)

    assert command not in str(exc_info.value)
    assert exc_info.value.__cause__ is None


@pytest.mark.parametrize("max_output_bytes", [0, 4 * 1024 * 1024 + 1])
def test_strict_client_rejects_output_limits_outside_hard_bounds(max_output_bytes: int) -> None:
    with pytest.raises(ValueError, match=r"^SSH max output bytes must be between 1 and 4194304$"):
        StrictSshClient(
            connect_timeout_seconds=3,
            auth_timeout_seconds=4,
            banner_timeout_seconds=5,
            max_output_bytes=max_output_bytes,
        )


def test_idle_command_polling_uses_bounded_backoff_and_timeout_closes_session(monkeypatch) -> None:
    channel = FakeChannel(never_exits=True)
    session, transport, fake_socket = _connected_session(channel=channel)
    real_sleep = time.sleep
    sleeps: list[float] = []

    def record_sleep(duration: float) -> None:
        sleeps.append(duration)
        real_sleep(duration)

    monkeypatch.setattr("visiox_edge_executor_worker.ssh.time.sleep", record_sleep)

    with pytest.raises(SshCommandTimeoutError, match=r"^SSH command timed out$"):
        session.run("idle", timeout_seconds=0.075)

    assert sleeps[:2] == pytest.approx([0.01, 0.02], abs=0.005)
    assert len(sleeps) >= 3
    assert 0 < sleeps[2] <= 0.045
    assert all(0 < duration <= 0.1 for duration in sleeps)
    assert transport.closed
    assert fake_socket.closed
