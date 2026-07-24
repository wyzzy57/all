import base64
from dataclasses import dataclass
import hashlib
import hmac
import io
from pathlib import PurePosixPath
import posixpath
import secrets
import socket
import stat
import threading
import time
from typing import Any, Callable

import paramiko


_MAX_TIMEOUT_SECONDS = 60
_MAX_COMMAND_TIMEOUT_SECONDS = 2 * 60 * 60
_CHANNEL_READ_BYTES = 64 * 1024
_DEFAULT_MAX_OUTPUT_BYTES = 4 * 1024 * 1024
_MIN_POLL_INTERVAL_SECONDS = 0.01
_MAX_POLL_INTERVAL_SECONDS = 0.1
_DEADLINE_SCHEDULER_GUARD_SECONDS = 0.01
_SSH_HANDSHAKE_SCHEDULER_GUARD_SECONDS = 0.02
_OPERATION_JOIN_SECONDS = 0.001


class HostKeyMismatchError(Exception):
    """Raised before authentication when the remote host key is not pinned."""


class SshAuthenticationError(Exception):
    """Raised when the transport cannot authenticate with the supplied credential."""


class SshCommandTimeoutError(Exception):
    """Raised when a command exceeds its deadline."""


class SshCommandError(Exception):
    """Raised with a fixed message when a command fails."""


class SshCommandOutputLimitError(Exception):
    """Raised instead of silently truncating command output."""


class SftpTransferTimeoutError(Exception):
    """Raised when an SFTP upload exceeds its deadline."""


class SftpTransferError(Exception):
    """Raised with a fixed message when an SFTP upload fails."""


class SshSessionClosedError(Exception):
    """Raised when an operation is attempted on a closed SSH session."""


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


@dataclass(frozen=True)
class RemotePrivateDirectory:
    path: str
    owner_uid: int | None


@dataclass
class _OperationOutcome:
    value: Any = None
    error: BaseException | None = None


def _fingerprint_sha256(host_key: Any) -> str:
    digest = hashlib.sha256(host_key.asbytes()).digest()
    encoded = base64.b64encode(digest).decode("ascii").rstrip("=")
    return f"SHA256:{encoded}"


def _scanned_host_key(host_key: Any) -> ScannedHostKey:
    return ScannedHostKey(host_key_type=host_key.get_name(), fingerprint=_fingerprint_sha256(host_key))


def _validate_timeout(
    timeout: float,
    *,
    maximum_seconds: float = _MAX_TIMEOUT_SECONDS,
) -> float:
    if not 0 < timeout <= maximum_seconds:
        raise ValueError(
            f"SSH timeout must be between 0 and {maximum_seconds} seconds"
        )
    return timeout


def _deadline(
    timeout: float,
    *,
    maximum_seconds: float = _MAX_TIMEOUT_SECONDS,
) -> float:
    return time.monotonic() + _validate_timeout(
        timeout,
        maximum_seconds=maximum_seconds,
    )


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


def _run_with_deadline(
    operation: Callable[[], Any],
    *,
    deadline: float,
    timeout_error: type[Exception],
    timeout_message: str,
    on_timeout: Callable[[], None],
    scheduler_guard_seconds: float = _DEADLINE_SCHEDULER_GUARD_SECONDS,
) -> Any:
    outcome = _OperationOutcome()
    completed = threading.Event()

    def invoke() -> None:
        try:
            outcome.value = operation()
        except BaseException as error:
            outcome.error = error
        finally:
            completed.set()

    worker = threading.Thread(
        target=invoke,
        name="visiox-ssh-bounded-operation",
        daemon=True,
    )
    worker.start()
    remaining = deadline - time.monotonic()
    wait_seconds = max(0.0, remaining - scheduler_guard_seconds)
    if remaining <= 0 or not completed.wait(wait_seconds):
        on_timeout()
        worker.join(_OPERATION_JOIN_SECONDS)
        raise timeout_error(timeout_message)
    if outcome.error is not None:
        raise outcome.error
    return outcome.value


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
    deadline = _deadline(timeout)
    opened_socket = socket_factory(
        (host, port),
        _remaining(deadline, TimeoutError, "SSH host-key scan timed out"),
    )
    transport = None
    try:
        transport = transport_factory(opened_socket)
        def start_client() -> None:
            remaining = _remaining(
                deadline,
                TimeoutError,
                "SSH host-key scan timed out",
            )
            transport.banner_timeout = remaining
            transport.start_client(timeout=remaining)

        _run_with_deadline(
            start_client,
            deadline=deadline,
            timeout_error=TimeoutError,
            timeout_message="SSH host-key scan timed out",
            on_timeout=lambda: _close_transport_and_socket(transport, opened_socket),
            scheduler_guard_seconds=_SSH_HANDSHAKE_SCHEDULER_GUARD_SECONDS,
        )
        _remaining(deadline, TimeoutError, "SSH host-key scan timed out")
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
        if (
            not isinstance(max_output_bytes, int)
            or isinstance(max_output_bytes, bool)
            or not 1 <= max_output_bytes <= _DEFAULT_MAX_OUTPUT_BYTES
        ):
            raise ValueError("SSH max output bytes must be between 1 and 4194304")
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
        timeout_seconds: float | None = None,
    ) -> "StrictSshSession":
        def authenticate(transport: Any) -> None:
            transport.auth_publickey(username, _load_private_key(private_key))

        return self._connect_authenticated(
            host=host,
            port=port,
            expected_fingerprint=expected_fingerprint,
            authenticate=authenticate,
            timeout_seconds=timeout_seconds,
        )

    def connect_password(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        expected_fingerprint: str,
        timeout_seconds: float | None = None,
    ) -> "StrictSshSession":
        def authenticate(transport: Any) -> None:
            transport.auth_password(username, password)

        return self._connect_authenticated(
            host=host,
            port=port,
            expected_fingerprint=expected_fingerprint,
            authenticate=authenticate,
            timeout_seconds=timeout_seconds,
        )

    def _connect_authenticated(
        self,
        *,
        host: str,
        port: int,
        expected_fingerprint: str,
        authenticate: Callable[[Any], None],
        timeout_seconds: float | None,
    ) -> "StrictSshSession":
        deadline = _deadline(timeout_seconds) if timeout_seconds is not None else None

        def phase_timeout(configured: float, message: str) -> float:
            if deadline is None:
                return configured
            return min(configured, _remaining(deadline, TimeoutError, message))

        opened_socket = self._socket_factory(
            (host, port),
            phase_timeout(self._connect_timeout_seconds, "SSH connection timed out"),
        )
        transport = None
        try:
            transport = self._transport_factory(opened_socket)
            def close_connection() -> None:
                _close_transport_and_socket(transport, opened_socket)

            def start_client() -> None:
                remaining = phase_timeout(
                    self._connect_timeout_seconds,
                    "SSH connection timed out",
                )
                transport.banner_timeout = min(
                    self._banner_timeout_seconds,
                    remaining,
                )
                transport.start_client(timeout=remaining)

            _run_with_deadline(
                start_client,
                deadline=deadline or _deadline(self._connect_timeout_seconds),
                timeout_error=TimeoutError,
                timeout_message="SSH connection timed out",
                on_timeout=close_connection,
                scheduler_guard_seconds=_SSH_HANDSHAKE_SCHEDULER_GUARD_SECONDS,
            )
            if deadline is not None:
                _remaining(deadline, TimeoutError, "SSH connection timed out")
            actual_fingerprint = _fingerprint_sha256(transport.get_remote_server_key())
            if not hmac.compare_digest(actual_fingerprint, expected_fingerprint):
                raise HostKeyMismatchError("SSH host key did not match the configured fingerprint")

            try:
                def authenticate_and_check() -> bool:
                    transport.auth_timeout = phase_timeout(
                        self._auth_timeout_seconds,
                        "SSH authentication timed out",
                    )
                    authenticate(transport)
                    return transport.is_authenticated()

                authenticated = _run_with_deadline(
                    authenticate_and_check,
                    deadline=deadline or _deadline(self._auth_timeout_seconds),
                    timeout_error=SshAuthenticationError,
                    timeout_message="SSH authentication timed out",
                    on_timeout=close_connection,
                    scheduler_guard_seconds=_SSH_HANDSHAKE_SCHEDULER_GUARD_SECONDS,
                )
                if deadline is not None:
                    _remaining(
                        deadline,
                        SshAuthenticationError,
                        "SSH authentication timed out",
                    )
            except SshAuthenticationError:
                raise
            except Exception:
                raise SshAuthenticationError("SSH authentication failed") from None
            if not authenticated:
                raise SshAuthenticationError("SSH authentication failed")
            return StrictSshSession(
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


class StrictSshSession:
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
        self._state_lock = threading.Lock()
        self._closed = False

    def run(
        self,
        command: str,
        *,
        timeout_seconds: float | None = None,
        timeout: float | None = None,
    ) -> CommandResult:
        self._ensure_open()
        operation_timeout = self._resolve_timeout(timeout_seconds, timeout)
        deadline = _deadline(
            operation_timeout,
            maximum_seconds=_MAX_COMMAND_TIMEOUT_SECONDS,
        )
        try:
            return _run_with_deadline(
                lambda: self._run_command(command, deadline),
                deadline=deadline,
                timeout_error=SshCommandTimeoutError,
                timeout_message="SSH command timed out",
                on_timeout=self.close,
            )
        except SshSessionClosedError:
            raise
        except SshCommandOutputLimitError:
            raise
        except SshCommandTimeoutError:
            self.close()
            raise SshCommandTimeoutError("SSH command timed out") from None
        except Exception as error:
            if _is_timeout_error(error):
                self.close()
                raise SshCommandTimeoutError("SSH command timed out") from None
            raise SshCommandError("SSH command failed") from None

    def _run_command(self, command: str, deadline: float) -> CommandResult:
        channel = None
        try:
            channel = self._transport.open_session(
                timeout=_remaining(deadline, SshCommandTimeoutError, "SSH command timed out")
            )
            channel.settimeout(_remaining(deadline, SshCommandTimeoutError, "SSH command timed out"))
            channel.exec_command(command)
            return self._collect_command_result(channel, deadline)
        finally:
            _close_quietly(channel)

    def _collect_command_result(self, channel: Any, deadline: float) -> CommandResult:
        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        output_bytes = 0
        poll_interval = _MIN_POLL_INTERVAL_SECONDS

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

            if drained:
                poll_interval = _MIN_POLL_INTERVAL_SECONDS

            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                return CommandResult(
                    exit_status=channel.recv_exit_status(),
                    stdout=b"".join(stdout_chunks),
                    stderr=b"".join(stderr_chunks),
                )

            if not drained:
                time.sleep(min(poll_interval, remaining))
                poll_interval = min(poll_interval * 2, _MAX_POLL_INTERVAL_SECONDS)

    def _enforce_output_limit(self, output_bytes: int) -> None:
        if output_bytes > self._max_output_bytes:
            raise SshCommandOutputLimitError("SSH command output exceeded limit")

    def upload_bytes(
        self,
        data: bytes,
        remote_path: str,
        timeout_seconds: float,
    ) -> SftpTransferResult:
        self._ensure_open()
        deadline = _deadline(timeout_seconds)
        try:
            return _run_with_deadline(
                lambda: self._upload_bytes(data, remote_path, deadline),
                deadline=deadline,
                timeout_error=SftpTransferTimeoutError,
                timeout_message="SFTP upload timed out",
                on_timeout=self.close,
            )
        except SshSessionClosedError:
            raise
        except SftpTransferTimeoutError:
            self.close()
            raise SftpTransferTimeoutError("SFTP upload timed out") from None
        except Exception as error:
            if _is_timeout_error(error):
                self.close()
                raise SftpTransferTimeoutError("SFTP upload timed out") from None
            raise SftpTransferError("SFTP upload failed") from None

    def create_private_directory(self, *, timeout_seconds: float) -> RemotePrivateDirectory:
        return self._bounded_sftp_operation(
            self._create_private_directory,
            timeout_seconds=timeout_seconds,
            timeout_message="SFTP directory creation timed out",
            error_message="SFTP directory creation failed",
        )

    def _create_private_directory(self, sftp: Any, deadline: float) -> RemotePrivateDirectory:
        home = sftp.normalize(".")
        _remaining(deadline, SftpTransferTimeoutError, "SFTP directory creation timed out")
        home_attributes = sftp.lstat(home)
        owner_uid = getattr(home_attributes, "st_uid", None)
        directory = posixpath.join(home, f".visiox-{secrets.token_hex(16)}")
        created = False
        try:
            sftp.mkdir(directory, mode=0o700)
            created = True
            sftp.chmod(directory, 0o700)
            attributes = sftp.lstat(directory)
            if (
                not stat.S_ISDIR(attributes.st_mode)
                or stat.S_IMODE(attributes.st_mode) != 0o700
                or (
                    owner_uid is not None
                    and getattr(attributes, "st_uid", None) is not None
                    and attributes.st_uid != owner_uid
                )
            ):
                raise SftpTransferError("SFTP directory creation failed")
            return RemotePrivateDirectory(path=directory, owner_uid=owner_uid)
        except Exception:
            if created:
                try:
                    sftp.rmdir(directory)
                except Exception:
                    pass
            raise

    def upload_bytes_exclusive(
        self,
        data: bytes,
        remote_path: str,
        *,
        expected_owner_uid: int | None,
        timeout_seconds: float,
    ) -> SftpTransferResult:
        def upload(sftp: Any, deadline: float) -> SftpTransferResult:
            opened_file = None
            created = False
            try:
                opened_file = sftp.file(remote_path, "wx")
                created = True
                sftp.chmod(remote_path, 0o600)
                self._validate_remote_file_attributes(
                    sftp.lstat(remote_path),
                    expected_owner_uid=expected_owner_uid,
                    error_message="SFTP upload failed",
                )
                _remaining(deadline, SftpTransferTimeoutError, "SFTP upload timed out")
                opened_file.write(data)
                if hasattr(opened_file, "flush"):
                    opened_file.flush()
                _remaining(deadline, SftpTransferTimeoutError, "SFTP upload timed out")
                self._validate_remote_file_attributes(
                    sftp.lstat(remote_path),
                    expected_owner_uid=expected_owner_uid,
                    error_message="SFTP upload failed",
                )
                return SftpTransferResult(
                    remote_path=remote_path,
                    bytes_transferred=len(data),
                )
            except Exception:
                if opened_file is not None:
                    _close_quietly(opened_file)
                    opened_file = None
                if created:
                    try:
                        sftp.remove(remote_path)
                    except Exception:
                        pass
                raise
            finally:
                _close_quietly(opened_file)

        return self._bounded_sftp_operation(
            upload,
            timeout_seconds=timeout_seconds,
            timeout_message="SFTP upload timed out",
            error_message="SFTP upload failed",
        )

    def validate_remote_file(
        self,
        remote_path: str,
        *,
        expected_owner_uid: int | None,
        timeout_seconds: float,
    ) -> None:
        def validate(sftp: Any, deadline: float) -> None:
            attributes = sftp.lstat(remote_path)
            _remaining(deadline, SftpTransferTimeoutError, "SFTP validation timed out")
            self._validate_remote_file_attributes(
                attributes,
                expected_owner_uid=expected_owner_uid,
                error_message="SFTP validation failed",
            )

        self._bounded_sftp_operation(
            validate,
            timeout_seconds=timeout_seconds,
            timeout_message="SFTP validation timed out",
            error_message="SFTP validation failed",
        )

    def cleanup_private_directory(
        self,
        directory: RemotePrivateDirectory,
        remote_paths: tuple[str, ...],
        *,
        timeout_seconds: float,
    ) -> None:
        directory_path = PurePosixPath(directory.path)
        if any(PurePosixPath(path).parent != directory_path for path in remote_paths):
            raise ValueError("cleanup paths must be direct children of the private directory")

        def cleanup(sftp: Any, deadline: float) -> None:
            for remote_path in remote_paths:
                try:
                    sftp.remove(remote_path)
                except OSError:
                    pass
                _remaining(deadline, SftpTransferTimeoutError, "SFTP cleanup timed out")
            sftp.rmdir(directory.path)

        self._bounded_sftp_operation(
            cleanup,
            timeout_seconds=timeout_seconds,
            timeout_message="SFTP cleanup timed out",
            error_message="SFTP cleanup failed",
        )

    def _bounded_sftp_operation(
        self,
        operation: Callable[[Any, float], Any],
        *,
        timeout_seconds: float,
        timeout_message: str,
        error_message: str,
    ) -> Any:
        self._ensure_open()
        deadline = _deadline(timeout_seconds)

        def invoke() -> Any:
            channel = None
            sftp = None
            try:
                channel = self._transport.open_session(
                    timeout=_remaining(deadline, SftpTransferTimeoutError, timeout_message)
                )
                channel.settimeout(_remaining(deadline, SftpTransferTimeoutError, timeout_message))
                channel.invoke_subsystem("sftp")
                channel.settimeout(_remaining(deadline, SftpTransferTimeoutError, timeout_message))
                sftp = self._sftp_client_factory(channel)
                return operation(sftp, deadline)
            finally:
                _close_quietly(sftp)
                _close_quietly(channel)

        try:
            return _run_with_deadline(
                invoke,
                deadline=deadline,
                timeout_error=SftpTransferTimeoutError,
                timeout_message=timeout_message,
                on_timeout=self.close,
            )
        except SshSessionClosedError:
            raise
        except SftpTransferTimeoutError:
            self.close()
            raise SftpTransferTimeoutError(timeout_message) from None
        except Exception as error:
            if _is_timeout_error(error):
                self.close()
                raise SftpTransferTimeoutError(timeout_message) from None
            raise SftpTransferError(error_message) from None

    @staticmethod
    def _validate_remote_file_attributes(
        attributes: Any,
        *,
        expected_owner_uid: int | None,
        error_message: str,
    ) -> None:
        actual_uid = getattr(attributes, "st_uid", None)
        if (
            not stat.S_ISREG(attributes.st_mode)
            or stat.S_IMODE(attributes.st_mode) != 0o600
            or attributes.st_mode & 0o022
            or (
                expected_owner_uid is not None
                and actual_uid is not None
                and actual_uid != expected_owner_uid
            )
        ):
            raise SftpTransferError(error_message)

    def _upload_bytes(
        self,
        data: bytes,
        remote_path: str,
        deadline: float,
    ) -> SftpTransferResult:
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
        finally:
            _close_quietly(sftp)
            _close_quietly(channel)

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
        _close_transport_and_socket(self._transport, self._opened_socket)

    def _ensure_open(self) -> None:
        with self._state_lock:
            if self._closed:
                raise SshSessionClosedError("SSH session is closed")

    @staticmethod
    def _resolve_timeout(timeout_seconds: float | None, timeout: float | None) -> float:
        if timeout_seconds is not None and timeout is not None:
            raise ValueError("provide only one SSH command timeout")
        if timeout_seconds is None and timeout is None:
            raise ValueError("SSH command timeout is required")
        return timeout_seconds if timeout_seconds is not None else timeout  # type: ignore[return-value]


SshSession = StrictSshSession
