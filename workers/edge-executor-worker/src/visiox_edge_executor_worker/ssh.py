import base64
from dataclasses import dataclass
import hashlib
import hmac
import io
import socket
import time
from typing import Any, Callable

import paramiko


_MAX_TIMEOUT_SECONDS = 60
_CHANNEL_READ_BYTES = 64 * 1024
_DEFAULT_MAX_OUTPUT_BYTES = 4 * 1024 * 1024
_POLL_INTERVAL_SECONDS = 0.01


class HostKeyMismatchError(Exception):
    """Raised before authentication when the remote host key is not pinned."""


class SshAuthenticationError(Exception):
    """Raised when the transport cannot authenticate with the supplied credential."""


class SshCommandTimeoutError(Exception):
    """Raised when a command exceeds its deadline."""


class SshCommandOutputLimitError(Exception):
    """Raised instead of silently truncating command output."""


class SftpTransferTimeoutError(Exception):
    """Raised when an SFTP upload exceeds its deadline."""


class SftpTransferError(Exception):
    """Raised with a fixed message when an SFTP upload fails."""


@dataclass(frozen=True)
class ScannedHostKey:
    host_key_type: str
    fingerprint: str


@dataclass(frozen=True)
class CommandResult:
    exit_status: int
    stdout: bytes
    stderr: bytes


@dataclass(frozen=True)
class SftpTransferResult:
    remote_path: str
    bytes_transferred: int


def _fingerprint_sha256(host_key: Any) -> str:
    digest = hashlib.sha256(host_key.asbytes()).digest()
    encoded = base64.b64encode(digest).decode("ascii").rstrip("=")
    return f"SHA256:{encoded}"


def _scanned_host_key(host_key: Any) -> ScannedHostKey:
    return ScannedHostKey(host_key_type=host_key.get_name(), fingerprint=_fingerprint_sha256(host_key))


def _validate_timeout(timeout: float) -> float:
    if not 0 < timeout <= _MAX_TIMEOUT_SECONDS:
        raise ValueError(f"SSH timeout must be between 0 and {_MAX_TIMEOUT_SECONDS} seconds")
    return timeout


def _deadline(timeout: float) -> float:
    return time.monotonic() + _validate_timeout(timeout)


def _remaining(deadline: float, timeout_error: type[Exception], message: str) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise timeout_error(message)
    return remaining


def _close_quietly(resource: Any | None) -> None:
    if resource is None:
        return
    try:
        resource.close()
    except Exception:
        pass


def _close_transport_and_socket(transport: Any | None, opened_socket: Any) -> None:
    try:
        _close_quietly(transport)
    finally:
        _close_quietly(opened_socket)


def _is_timeout_error(error: Exception) -> bool:
    return isinstance(error, (socket.timeout, TimeoutError)) or (
        isinstance(error, paramiko.SSHException)
        and str(error).lower().startswith("timeout opening channel")
    )


def scan_host_key(
    host: str,
    port: int,
    timeout: float,
    *,
    socket_factory: Callable[[tuple[str, int], float], Any] = socket.create_connection,
    transport_factory: Callable[[Any], Any] = paramiko.Transport,
) -> ScannedHostKey:
    """Read a remote host key without attempting any user authentication."""
    timeout = _validate_timeout(timeout)
    opened_socket = socket_factory((host, port), timeout)
    transport = None
    try:
        transport = transport_factory(opened_socket)
        transport.banner_timeout = timeout
        transport.start_client(timeout=timeout)
        return _scanned_host_key(transport.get_remote_server_key())
    finally:
        _close_transport_and_socket(transport, opened_socket)


class StrictSshClient:
    def __init__(
        self,
        *,
        connect_timeout_seconds: float,
        auth_timeout_seconds: float,
        banner_timeout_seconds: float,
        max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES,
        socket_factory: Callable[[tuple[str, int], float], Any] = socket.create_connection,
        transport_factory: Callable[[Any], Any] = paramiko.Transport,
        sftp_client_factory: Callable[[Any], Any] | None = None,
    ) -> None:
        self._connect_timeout_seconds = _validate_timeout(connect_timeout_seconds)
        self._auth_timeout_seconds = _validate_timeout(auth_timeout_seconds)
        self._banner_timeout_seconds = _validate_timeout(banner_timeout_seconds)
        if max_output_bytes <= 0:
            raise ValueError("SSH max output bytes must be positive")
        self._max_output_bytes = max_output_bytes
        self._socket_factory = socket_factory
        self._transport_factory = transport_factory
        self._sftp_client_factory = sftp_client_factory or paramiko.SFTPClient

    def connect(
        self,
        *,
        host: str,
        port: int,
        username: str,
        private_key: paramiko.PKey | bytes,
        expected_fingerprint: str,
    ) -> "SshSession":
        def authenticate(transport: Any) -> None:
            transport.auth_publickey(username, _load_private_key(private_key))

        return self._connect_authenticated(
            host=host,
            port=port,
            expected_fingerprint=expected_fingerprint,
            authenticate=authenticate,
        )

    def connect_password(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        expected_fingerprint: str,
    ) -> "SshSession":
        def authenticate(transport: Any) -> None:
            transport.auth_password(username, password)

        return self._connect_authenticated(
            host=host,
            port=port,
            expected_fingerprint=expected_fingerprint,
            authenticate=authenticate,
        )

    def _connect_authenticated(
        self,
        *,
        host: str,
        port: int,
        expected_fingerprint: str,
        authenticate: Callable[[Any], None],
    ) -> "SshSession":
        opened_socket = self._socket_factory((host, port), self._connect_timeout_seconds)
        transport = None
        try:
            transport = self._transport_factory(opened_socket)
            transport.banner_timeout = self._banner_timeout_seconds
            transport.auth_timeout = self._auth_timeout_seconds
            transport.start_client(timeout=self._connect_timeout_seconds)
            actual_fingerprint = _fingerprint_sha256(transport.get_remote_server_key())
            if not hmac.compare_digest(actual_fingerprint, expected_fingerprint):
                raise HostKeyMismatchError("SSH host key did not match the configured fingerprint")

            try:
                authenticate(transport)
                authenticated = transport.is_authenticated()
            except Exception:
                raise SshAuthenticationError("SSH authentication failed") from None
            if not authenticated:
                raise SshAuthenticationError("SSH authentication failed")
            return SshSession(
                transport,
                opened_socket,
                max_output_bytes=self._max_output_bytes,
                sftp_client_factory=self._sftp_client_factory,
            )
        except Exception:
            _close_transport_and_socket(transport, opened_socket)
            raise


def _load_private_key(private_key: paramiko.PKey | bytes) -> paramiko.PKey:
    if not isinstance(private_key, bytes):
        return private_key
    try:
        text_key = private_key.decode("utf-8")
    except UnicodeDecodeError:
        raise SshAuthenticationError("SSH authentication failed") from None

    for key_type in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
        try:
            return key_type.from_private_key(io.StringIO(text_key))
        except (paramiko.SSHException, ValueError, TypeError):
            continue
    raise SshAuthenticationError("SSH authentication failed")


class SshSession:
    def __init__(
        self,
        transport: Any,
        opened_socket: Any,
        *,
        max_output_bytes: int,
        sftp_client_factory: Callable[[Any], Any],
    ) -> None:
        self._transport = transport
        self._opened_socket = opened_socket
        self._max_output_bytes = max_output_bytes
        self._sftp_client_factory = sftp_client_factory

    def run(
        self,
        command: str,
        *,
        timeout_seconds: float | None = None,
        timeout: float | None = None,
    ) -> CommandResult:
        operation_timeout = self._resolve_timeout(timeout_seconds, timeout)
        deadline = _deadline(operation_timeout)
        channel = None
        try:
            channel = self._transport.open_session(
                timeout=_remaining(deadline, SshCommandTimeoutError, "SSH command timed out")
            )
            channel.settimeout(_remaining(deadline, SshCommandTimeoutError, "SSH command timed out"))
            channel.exec_command(command)
            return self._collect_command_result(channel, deadline)
        except SshCommandTimeoutError:
            raise
        except Exception as error:
            if _is_timeout_error(error):
                raise SshCommandTimeoutError("SSH command timed out") from None
            raise
        finally:
            _close_quietly(channel)

    def _collect_command_result(self, channel: Any, deadline: float) -> CommandResult:
        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        output_bytes = 0

        while True:
            remaining = _remaining(deadline, SshCommandTimeoutError, "SSH command timed out")
            channel.settimeout(remaining)
            drained = False

            while channel.recv_ready():
                channel.settimeout(
                    _remaining(deadline, SshCommandTimeoutError, "SSH command timed out")
                )
                chunk = channel.recv(_CHANNEL_READ_BYTES)
                stdout_chunks.append(chunk)
                output_bytes += len(chunk)
                self._enforce_output_limit(output_bytes)
                drained = True

            while channel.recv_stderr_ready():
                channel.settimeout(
                    _remaining(deadline, SshCommandTimeoutError, "SSH command timed out")
                )
                chunk = channel.recv_stderr(_CHANNEL_READ_BYTES)
                stderr_chunks.append(chunk)
                output_bytes += len(chunk)
                self._enforce_output_limit(output_bytes)
                drained = True

            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                return CommandResult(
                    exit_status=channel.recv_exit_status(),
                    stdout=b"".join(stdout_chunks),
                    stderr=b"".join(stderr_chunks),
                )

            if not drained:
                time.sleep(min(_POLL_INTERVAL_SECONDS, remaining))

    def _enforce_output_limit(self, output_bytes: int) -> None:
        if output_bytes > self._max_output_bytes:
            raise SshCommandOutputLimitError("SSH command output exceeded limit")

    def upload_bytes(
        self,
        data: bytes,
        remote_path: str,
        timeout_seconds: float,
    ) -> SftpTransferResult:
        deadline = _deadline(timeout_seconds)
        channel = None
        sftp = None
        try:
            channel = self._transport.open_session(
                timeout=_remaining(deadline, SftpTransferTimeoutError, "SFTP upload timed out")
            )
            channel.settimeout(_remaining(deadline, SftpTransferTimeoutError, "SFTP upload timed out"))
            channel.invoke_subsystem("sftp")
            channel.settimeout(_remaining(deadline, SftpTransferTimeoutError, "SFTP upload timed out"))
            sftp = self._sftp_client_factory(channel)
            channel.settimeout(_remaining(deadline, SftpTransferTimeoutError, "SFTP upload timed out"))
            sftp.putfo(io.BytesIO(data), remote_path)
            _remaining(deadline, SftpTransferTimeoutError, "SFTP upload timed out")
            return SftpTransferResult(remote_path=remote_path, bytes_transferred=len(data))
        except SftpTransferTimeoutError:
            raise
        except Exception as error:
            if _is_timeout_error(error):
                raise SftpTransferTimeoutError("SFTP upload timed out") from None
            raise SftpTransferError("SFTP upload failed") from None
        finally:
            _close_quietly(sftp)
            _close_quietly(channel)

    def close(self) -> None:
        _close_transport_and_socket(self._transport, self._opened_socket)

    @staticmethod
    def _resolve_timeout(timeout_seconds: float | None, timeout: float | None) -> float:
        if timeout_seconds is not None and timeout is not None:
            raise ValueError("provide only one SSH command timeout")
        if timeout_seconds is None and timeout is None:
            raise ValueError("SSH command timeout is required")
        return timeout_seconds if timeout_seconds is not None else timeout  # type: ignore[return-value]
