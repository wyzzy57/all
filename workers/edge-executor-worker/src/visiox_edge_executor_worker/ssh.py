import base64
from dataclasses import dataclass
import hashlib
import hmac
import io
import socket
from typing import Any, Callable

import paramiko


_MAX_TIMEOUT_SECONDS = 60
_COMMAND_READ_BYTES = 64 * 1024


class HostKeyMismatchError(Exception):
    """Raised before authentication when the remote host key is not pinned."""


class SshAuthenticationError(Exception):
    """Raised when the transport cannot authenticate with the supplied key."""


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
    transport = transport_factory(opened_socket)
    try:
        transport.banner_timeout = timeout
        transport.start_client(timeout=timeout)
        return _scanned_host_key(transport.get_remote_server_key())
    finally:
        transport.close()
        opened_socket.close()


class StrictSshClient:
    def __init__(
        self,
        *,
        connect_timeout_seconds: float,
        auth_timeout_seconds: float,
        banner_timeout_seconds: float,
        socket_factory: Callable[[tuple[str, int], float], Any] = socket.create_connection,
        transport_factory: Callable[[Any], Any] = paramiko.Transport,
    ) -> None:
        self._connect_timeout_seconds = _validate_timeout(connect_timeout_seconds)
        self._auth_timeout_seconds = _validate_timeout(auth_timeout_seconds)
        self._banner_timeout_seconds = _validate_timeout(banner_timeout_seconds)
        self._socket_factory = socket_factory
        self._transport_factory = transport_factory

    def connect(
        self,
        *,
        host: str,
        port: int,
        username: str,
        private_key: paramiko.PKey | bytes,
        expected_fingerprint: str,
    ) -> "SshSession":
        opened_socket = self._socket_factory((host, port), self._connect_timeout_seconds)
        transport = self._transport_factory(opened_socket)
        try:
            transport.banner_timeout = self._banner_timeout_seconds
            transport.auth_timeout = self._auth_timeout_seconds
            transport.start_client(timeout=self._connect_timeout_seconds)
            actual_fingerprint = _fingerprint_sha256(transport.get_remote_server_key())
            if not hmac.compare_digest(actual_fingerprint, expected_fingerprint):
                raise HostKeyMismatchError("SSH host key did not match the configured fingerprint")

            transport.auth_publickey(username, _load_private_key(private_key))
            if not transport.is_authenticated():
                raise SshAuthenticationError("SSH authentication was rejected")
            return SshSession(transport)
        except Exception:
            transport.close()
            opened_socket.close()
            raise


def _load_private_key(private_key: paramiko.PKey | bytes) -> paramiko.PKey:
    if not isinstance(private_key, bytes):
        return private_key
    try:
        text_key = private_key.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SshAuthenticationError("SSH private key could not be loaded") from error

    for key_type in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
        try:
            return key_type.from_private_key(io.StringIO(text_key))
        except (paramiko.SSHException, ValueError, TypeError):
            continue
    raise SshAuthenticationError("SSH private key could not be loaded")


class SshSession:
    def __init__(self, transport: Any) -> None:
        self._transport = transport

    def run(self, command: str, *, timeout: float) -> CommandResult:
        channel = self._transport.open_session()
        try:
            channel.settimeout(_validate_timeout(timeout))
            channel.exec_command(command)
            return CommandResult(
                exit_status=channel.recv_exit_status(),
                stdout=channel.recv(_COMMAND_READ_BYTES),
                stderr=channel.recv_stderr(_COMMAND_READ_BYTES),
            )
        finally:
            channel.close()

    def upload_bytes(self, content: bytes, *, remote_path: str) -> SftpTransferResult:
        sftp = paramiko.SFTPClient.from_transport(self._transport)
        try:
            sftp.putfo(io.BytesIO(content), remote_path)
        finally:
            sftp.close()
        return SftpTransferResult(remote_path=remote_path, bytes_transferred=len(content))

    def close(self) -> None:
        self._transport.close()
