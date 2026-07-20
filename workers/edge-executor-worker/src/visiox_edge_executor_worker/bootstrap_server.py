from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import hmac
import json
from pathlib import Path
import socket
import struct
import threading
from typing import Any, Callable

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from visiox_common.settings import Settings
from visiox_db.models import ComputeNode, EdgeSshCredential

from .crypto import EncryptedSecret
from .ssh import HostKeyMismatchError, ScannedHostKey, scan_host_key
from .startup import EdgeExecutorSecurityContext, initialize_security


MAX_FRAME_BYTES = 64 * 1024
REQUEST_TIMEOUT_SECONDS = 60.0
BOOTSTRAP_SCRIPT_PATH = "/tmp/visiox-bootstrap-user.sh"
BOOTSTRAP_DATA_PATH = "/tmp/visiox-bootstrap-data.json"
BOOTSTRAP_COMMAND = (
    "/bin/bash /tmp/visiox-bootstrap-user.sh /tmp/visiox-bootstrap-data.json"
)
_SSH_USER = "visiox-edge"
_REMOTE_SCRIPT = Path(__file__).resolve().parents[2] / "remote" / "bootstrap_user.sh"
_AF_UNIX = getattr(socket, "AF_UNIX", 1)


class BootstrapProtocolError(Exception):
    """Raised for malformed or out-of-bounds private-channel frames."""


class BootstrapProtocolTimeout(Exception):
    """Raised when a private-channel operation exceeds its deadline."""


class BootstrapOperationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _read_exact(connection: Any, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    try:
        while remaining:
            chunk = connection.recv(remaining)
            if not chunk:
                raise BootstrapProtocolError("Bootstrap frame is invalid")
            chunks.append(chunk)
            remaining -= len(chunk)
    except (socket.timeout, TimeoutError):
        raise BootstrapProtocolTimeout("Bootstrap request timed out") from None
    return b"".join(chunks)


def _strict_json_object(payload: bytes) -> dict[str, Any]:
    try:
        text = payload.decode("utf-8")
        value, end = json.JSONDecoder().raw_decode(text)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BootstrapProtocolError("Bootstrap frame is invalid") from None
    if end != len(text) or not isinstance(value, dict):
        raise BootstrapProtocolError("Bootstrap frame is invalid")
    return value


def decode_frame(connection: Any) -> dict[str, Any]:
    header = _read_exact(connection, 4)
    length = struct.unpack(">I", header)[0]
    if length == 0 or length > MAX_FRAME_BYTES:
        raise BootstrapProtocolError("Bootstrap frame is invalid")
    return _strict_json_object(_read_exact(connection, length))


def encode_frame(message: dict[str, Any]) -> bytes:
    try:
        payload = json.dumps(
            message,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise BootstrapProtocolError("Bootstrap frame is invalid") from None
    if not payload or len(payload) > MAX_FRAME_BYTES:
        raise BootstrapProtocolError("Bootstrap frame is invalid")
    return struct.pack(">I", len(payload)) + payload


def _required_string(request: dict[str, Any], name: str) -> str:
    value = request.get(name)
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise BootstrapOperationError("INVALID_REQUEST", "Bootstrap request is invalid")
    return value


def _required_port(request: dict[str, Any]) -> int:
    port = request.get("port")
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise BootstrapOperationError("INVALID_REQUEST", "Bootstrap request is invalid")
    return port


def _generate_key_pair() -> tuple[bytes, str]:
    private_key = Ed25519PrivateKey.generate()
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    ).decode("ascii")
    return private_bytes, public_key


def _remote_data(operation: str, **values: str) -> bytes:
    return json.dumps(
        {"operation": operation, **values},
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")


class BootstrapOperations:
    def __init__(
        self,
        security: EdgeExecutorSecurityContext,
        session_factory: sessionmaker[Session] | Callable[[], Session],
        *,
        host_key_scanner: Callable[[str, int, float], ScannedHostKey] = scan_host_key,
        bootstrap_script: bytes | None = None,
    ) -> None:
        self._security = security
        self._session_factory = session_factory
        self._host_key_scanner = host_key_scanner
        self._bootstrap_script = (
            bootstrap_script
            if bootstrap_script is not None
            else _REMOTE_SCRIPT.read_bytes()
        )

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        request_id = request.get("request_id")
        if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
            return self._error("", "INVALID_REQUEST", "Bootstrap request is invalid")
        operation = request.get("operation")
        try:
            if operation == "scan_host_key":
                return self._scan(request_id, request)
            if operation == "bootstrap":
                return self._bootstrap(request_id, request)
            if operation == "test_connection":
                return self._test_connection(request_id, request)
            if operation == "rotate_key":
                return self._rotate_key(request_id, request)
            raise BootstrapOperationError("INVALID_REQUEST", "Bootstrap request is invalid")
        except HostKeyMismatchError:
            return self._error(
                request_id,
                "HOST_KEY_MISMATCH",
                "SSH host fingerprint changed",
            )
        except BootstrapOperationError as error:
            return self._error(request_id, error.code, error.message)
        except Exception:
            return self._error(
                request_id,
                "BOOTSTRAP_FAILED",
                "Edge node bootstrap failed",
            )

    def _scan(self, request_id: str, request: dict[str, Any]) -> dict[str, Any]:
        self._require_keys(request, {"request_id", "operation", "host", "port"})
        scanned = self._host_key_scanner(
            _required_string(request, "host"),
            _required_port(request),
            REQUEST_TIMEOUT_SECONDS,
        )
        return {
            "request_id": request_id,
            "status": "ok",
            "host_key_type": scanned.host_key_type,
            "fingerprint": scanned.fingerprint,
        }

    def _bootstrap(self, request_id: str, request: dict[str, Any]) -> dict[str, Any]:
        self._require_keys(
            request,
            {
                "request_id",
                "operation",
                "host",
                "port",
                "administrator",
                "password",
                "confirmed_fingerprint",
                "node_name",
            },
        )
        host = _required_string(request, "host")
        port = _required_port(request)
        administrator = _required_string(request, "administrator")
        password = _required_string(request, "password")
        confirmed_fingerprint = _required_string(request, "confirmed_fingerprint")
        node_name = _required_string(request, "node_name")
        scanned = self._host_key_scanner(host, port, REQUEST_TIMEOUT_SECONDS)
        if not hmac.compare_digest(scanned.fingerprint, confirmed_fingerprint):
            raise HostKeyMismatchError("SSH host key did not match")

        private_key, public_key = _generate_key_pair()
        password_session = self._security.ssh_client.connect_password(
            host=host,
            port=port,
            username=administrator,
            password=password,
            expected_fingerprint=confirmed_fingerprint,
        )
        password = ""
        try:
            self._run_remote_script(
                password_session,
                _remote_data("bootstrap", public_key=public_key),
            )
        finally:
            password_session.close()

        key_session = self._security.ssh_client.connect(
            host=host,
            port=port,
            username=_SSH_USER,
            private_key=private_key,
            expected_fingerprint=confirmed_fingerprint,
        )
        key_session.close()

        encrypted = self._security.credential_cipher.encrypt(private_key)
        with self._session_factory() as session:
            try:
                node = session.scalar(select(ComputeNode).where(ComputeNode.name == node_name))
                if node is None:
                    node = ComputeNode(
                        name=node_name,
                        architecture="unknown",
                        platform_kind="ssh-edge",
                        capabilities={},
                        resources={},
                        agent_version="ssh-bootstrap",
                    )
                    session.add(node)
                    session.flush()
                node.status = "online"
                node.fingerprint = {
                    "ssh_host_key_type": scanned.host_key_type,
                    "ssh_host_key_fingerprint": confirmed_fingerprint,
                }
                credential = session.scalar(
                    select(EdgeSshCredential).where(EdgeSshCredential.node_id == node.id)
                )
                if credential is None:
                    credential = EdgeSshCredential(node_id=node.id)
                    session.add(credential)
                self._set_credential(
                    credential,
                    host=host,
                    port=port,
                    host_key_type=scanned.host_key_type,
                    host_key_fingerprint=confirmed_fingerprint,
                    public_key=public_key,
                    encrypted=encrypted,
                )
                session.commit()
                return {"request_id": request_id, "status": "ok", "node_id": node.id}
            except Exception:
                session.rollback()
                raise

    def _test_connection(self, request_id: str, request: dict[str, Any]) -> dict[str, Any]:
        self._require_keys(request, {"request_id", "operation", "node_id"})
        node_id = _required_string(request, "node_id")
        with self._session_factory() as session:
            credential = self._credential_or_error(session, node_id)
            private_key = self._decrypt(credential)
            ssh_session = self._security.ssh_client.connect(
                host=credential.ssh_host,
                port=credential.ssh_port,
                username=credential.ssh_user,
                private_key=private_key,
                expected_fingerprint=credential.host_key_fingerprint,
            )
            ssh_session.close()
        return {"request_id": request_id, "status": "ok", "node_id": node_id}

    def _rotate_key(self, request_id: str, request: dict[str, Any]) -> dict[str, Any]:
        self._require_keys(request, {"request_id", "operation", "node_id"})
        node_id = _required_string(request, "node_id")
        with self._session_factory() as session:
            credential = self._credential_or_error(session, node_id)
            old_private_key = self._decrypt(credential)
            new_private_key, new_public_key = _generate_key_pair()

            old_session = self._security.ssh_client.connect(
                host=credential.ssh_host,
                port=credential.ssh_port,
                username=credential.ssh_user,
                private_key=old_private_key,
                expected_fingerprint=credential.host_key_fingerprint,
            )
            try:
                self._run_remote_script(
                    old_session,
                    _remote_data(
                        "rotate_add",
                        old_public_key=credential.public_key,
                        public_key=new_public_key,
                    ),
                )
            finally:
                old_session.close()

            verified_session = self._security.ssh_client.connect(
                host=credential.ssh_host,
                port=credential.ssh_port,
                username=credential.ssh_user,
                private_key=new_private_key,
                expected_fingerprint=credential.host_key_fingerprint,
            )
            verified_session.close()

            encrypted = self._security.credential_cipher.encrypt(new_private_key)
            self._set_credential(
                credential,
                host=credential.ssh_host,
                port=credential.ssh_port,
                host_key_type=credential.host_key_type,
                host_key_fingerprint=credential.host_key_fingerprint,
                public_key=new_public_key,
                encrypted=encrypted,
            )
            credential.rotated_at = datetime.now(UTC)
            try:
                session.commit()
            except Exception:
                session.rollback()
                raise

            cleanup_session = self._security.ssh_client.connect(
                host=credential.ssh_host,
                port=credential.ssh_port,
                username=credential.ssh_user,
                private_key=new_private_key,
                expected_fingerprint=credential.host_key_fingerprint,
            )
            try:
                self._run_remote_script(
                    cleanup_session,
                    _remote_data("rotate_commit", public_key=new_public_key),
                )
            except Exception:
                raise BootstrapOperationError(
                    "ROTATE_CLEANUP_FAILED",
                    "SSH key rotation cleanup failed",
                ) from None
            finally:
                cleanup_session.close()
        return {"request_id": request_id, "status": "ok", "node_id": node_id}

    def _run_remote_script(self, ssh_session: Any, data: bytes) -> None:
        ssh_session.upload_bytes(
            self._bootstrap_script,
            BOOTSTRAP_SCRIPT_PATH,
            timeout_seconds=REQUEST_TIMEOUT_SECONDS,
        )
        ssh_session.upload_bytes(
            data,
            BOOTSTRAP_DATA_PATH,
            timeout_seconds=REQUEST_TIMEOUT_SECONDS,
        )
        result = ssh_session.run(
            BOOTSTRAP_COMMAND,
            timeout_seconds=REQUEST_TIMEOUT_SECONDS,
        )
        if result.exit_status != 0:
            raise BootstrapOperationError("REMOTE_SETUP_FAILED", "Remote SSH setup failed")

    def _decrypt(self, credential: EdgeSshCredential) -> bytes:
        return self._security.credential_cipher.decrypt(
            EncryptedSecret(
                ciphertext=credential.encrypted_private_key,
                nonce=credential.encryption_nonce,
                key_version=credential.key_version,
            )
        )

    @staticmethod
    def _set_credential(
        credential: EdgeSshCredential,
        *,
        host: str,
        port: int,
        host_key_type: str,
        host_key_fingerprint: str,
        public_key: str,
        encrypted: EncryptedSecret,
    ) -> None:
        credential.ssh_host = host
        credential.ssh_port = port
        credential.ssh_user = _SSH_USER
        credential.host_key_type = host_key_type
        credential.host_key_fingerprint = host_key_fingerprint
        credential.public_key = public_key
        credential.encrypted_private_key = encrypted.ciphertext
        credential.encryption_nonce = encrypted.nonce
        credential.key_version = encrypted.key_version

    @staticmethod
    def _credential_or_error(session: Session, node_id: str) -> EdgeSshCredential:
        credential = session.scalar(
            select(EdgeSshCredential).where(EdgeSshCredential.node_id == node_id)
        )
        if credential is None:
            raise BootstrapOperationError("NODE_NOT_FOUND", "Edge node was not found")
        return credential

    @staticmethod
    def _require_keys(request: dict[str, Any], expected: set[str]) -> None:
        if set(request) != expected:
            raise BootstrapOperationError("INVALID_REQUEST", "Bootstrap request is invalid")

    @staticmethod
    def _error(request_id: str, code: str, message: str) -> dict[str, Any]:
        return {
            "request_id": request_id,
            "status": "error",
            "error_code": code,
            "error_message": message,
        }


class BootstrapServer:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session] | Callable[[], Session],
        *,
        security_initializer: Callable[[Settings], EdgeExecutorSecurityContext] = initialize_security,
        socket_factory: Callable[[int, int], Any] = socket.socket,
        max_concurrency: int = 4,
    ) -> None:
        if not 1 <= max_concurrency <= 64:
            raise ValueError("bootstrap concurrency must be between 1 and 64")
        self._settings = settings
        self._session_factory = session_factory
        self._security_initializer = security_initializer
        self._socket_factory = socket_factory
        self._max_concurrency = max_concurrency
        self._listener: Any | None = None
        self._operations: BootstrapOperations | None = None
        self._stopping = threading.Event()

    def start(self) -> None:
        security = self._security_initializer(self._settings)
        operations = BootstrapOperations(security, self._session_factory)
        socket_path = self._settings.edge_bootstrap_socket
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        socket_path.unlink(missing_ok=True)
        listener = self._socket_factory(_AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(socket_path))
            socket_path.chmod(0o600)
            listener.listen(self._max_concurrency)
            listener.settimeout(1.0)
        except Exception:
            listener.close()
            socket_path.unlink(missing_ok=True)
            raise
        self._operations = operations
        self._listener = listener

    def serve_forever(self) -> None:
        self.start()
        assert self._listener is not None
        slots = threading.BoundedSemaphore(self._max_concurrency)
        with ThreadPoolExecutor(
            max_workers=self._max_concurrency,
            thread_name_prefix="visiox-edge-bootstrap",
        ) as executor:
            while not self._stopping.is_set():
                slots.acquire()
                try:
                    connection, _ = self._listener.accept()
                except socket.timeout:
                    slots.release()
                    continue
                except OSError:
                    slots.release()
                    if self._stopping.is_set():
                        break
                    raise
                future = executor.submit(self._handle_connection, connection)
                future.add_done_callback(lambda _: slots.release())

    def stop(self) -> None:
        self._stopping.set()
        if self._listener is not None:
            self._listener.close()
        self._settings.edge_bootstrap_socket.unlink(missing_ok=True)

    def _handle_connection(self, connection: Any) -> None:
        try:
            connection.settimeout(REQUEST_TIMEOUT_SECONDS)
            request = decode_frame(connection)
            if connection.recv(1) != b"":
                raise BootstrapProtocolError("Bootstrap frame is invalid")
            assert self._operations is not None
            response = self._operations.handle(request)
            connection.sendall(encode_frame(response))
        except (BootstrapProtocolError, BootstrapProtocolTimeout):
            try:
                connection.sendall(
                    encode_frame(
                        {
                            "request_id": "",
                            "status": "error",
                            "error_code": "INVALID_FRAME",
                            "error_message": "Bootstrap frame is invalid",
                        }
                    )
                )
            except Exception:
                pass
        finally:
            connection.close()
