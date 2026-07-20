from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
import errno
import hmac
import ipaddress
import json
import math
import os
from pathlib import Path, PurePosixPath
import shlex
import socket
import stat
import struct
import threading
import time
from typing import Any, Callable
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from visiox_common.settings import Settings, get_settings
from visiox_db.models import ComputeNode, EdgeSshCredential
from visiox_db.session import create_session_factory

from .crypto import EncryptedSecret
from .inventory import parse_inventory
from .scripts import load_packaged_script
from .ssh import HostKeyMismatchError, ScannedHostKey, scan_host_key
from .startup import EdgeExecutorSecurityContext, initialize_security


MAX_FRAME_BYTES = 64 * 1024
REQUEST_TIMEOUT_SECONDS = 60.0
_SSH_USER = "visiox-edge"
_SSH_PLATFORM_KIND = "ssh_edge"
_REMOTE_SCRIPT_NAME = "bootstrap_user.sh"
_INVENTORY_SCRIPT_NAME = "probe_inventory.sh"
_REMOTE_DATA_NAME = "request.json"
_SUDO_PREFLIGHT_COMMAND = "command -v sudo >/dev/null 2>&1 && sudo -n true"
_AF_UNIX = getattr(socket, "AF_UNIX", 1)
_CLIENT_DEADLINE_FIELD = "_deadline_monotonic"


class BootstrapProtocolError(Exception):
    """Raised for malformed or out-of-bounds private-channel frames."""


class BootstrapProtocolTimeout(Exception):
    """Raised when a private-channel operation exceeds its deadline."""


class BootstrapOperationError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        cleanup_required: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.cleanup_required = cleanup_required


def _remaining(
    deadline: float,
    error_type: type[Exception],
    message: str,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> float:
    remaining = deadline - clock()
    if remaining <= 0:
        raise error_type(message)
    return remaining


def _set_connection_timeout(
    connection: Any,
    deadline: float,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    connection.settimeout(
        _remaining(
            deadline,
            BootstrapProtocolTimeout,
            "Bootstrap request timed out",
            clock=clock,
        )
    )


def _read_exact(
    connection: Any,
    size: int,
    *,
    deadline: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> bytes:
    chunks: list[bytes] = []
    remaining_size = size
    try:
        while remaining_size:
            if deadline is not None:
                _set_connection_timeout(connection, deadline, clock=clock)
            chunk = connection.recv(remaining_size)
            if not chunk:
                raise BootstrapProtocolError("Bootstrap frame is invalid")
            chunks.append(chunk)
            remaining_size -= len(chunk)
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


def decode_frame(
    connection: Any,
    *,
    deadline: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    header = _read_exact(connection, 4, deadline=deadline, clock=clock)
    length = struct.unpack(">I", header)[0]
    if length == 0 or length > MAX_FRAME_BYTES:
        raise BootstrapProtocolError("Bootstrap frame is invalid")
    return _strict_json_object(
        _read_exact(connection, length, deadline=deadline, clock=clock)
    )


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


def _canonical_host(host: str) -> str:
    if host != host.strip() or any(character.isspace() for character in host):
        raise BootstrapOperationError("INVALID_REQUEST", "Bootstrap request is invalid")
    candidate = host.rstrip(".").lower()
    if not candidate:
        raise BootstrapOperationError("INVALID_REQUEST", "Bootstrap request is invalid")
    try:
        return ipaddress.ip_address(candidate).compressed
    except ValueError:
        try:
            canonical = candidate.encode("idna").decode("ascii")
        except UnicodeError:
            raise BootstrapOperationError(
                "INVALID_REQUEST", "Bootstrap request is invalid"
            ) from None
        if len(canonical) > 255 or any(not label for label in canonical.split(".")):
            raise BootstrapOperationError("INVALID_REQUEST", "Bootstrap request is invalid")
        return canonical


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


def _remote_data(operation: str, **values: Any) -> bytes:
    return json.dumps(
        {"operation": operation, **values},
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


class _KeyedLockPool:
    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._entries: dict[str, tuple[threading.Lock, int]] = {}

    @property
    def entry_count(self) -> int:
        with self._guard:
            return len(self._entries)

    @contextmanager
    def acquire(
        self,
        keys: set[str],
        deadline: float,
        *,
        clock: Callable[[], float],
    ) -> Iterator[None]:
        ordered_keys = sorted(keys)
        locks: list[threading.Lock] = []
        with self._guard:
            for key in ordered_keys:
                lock, references = self._entries.get(key, (threading.Lock(), 0))
                self._entries[key] = (lock, references + 1)
                locks.append(lock)

        acquired: list[threading.Lock] = []
        try:
            for lock in locks:
                timeout = _remaining(
                    deadline,
                    TimeoutError,
                    "Edge bootstrap request timed out",
                    clock=clock,
                )
                if not lock.acquire(timeout=timeout):
                    raise BootstrapOperationError(
                        "REQUEST_TIMEOUT", "Edge bootstrap request timed out"
                    )
                acquired.append(lock)
            yield
        except TimeoutError:
            raise BootstrapOperationError(
                "REQUEST_TIMEOUT", "Edge bootstrap request timed out"
            ) from None
        finally:
            for lock in reversed(acquired):
                lock.release()
            with self._guard:
                for key in ordered_keys:
                    lock, references = self._entries[key]
                    if references == 1:
                        del self._entries[key]
                    else:
                        self._entries[key] = (lock, references - 1)


class BootstrapOperations:
    def __init__(
        self,
        security: EdgeExecutorSecurityContext,
        session_factory: sessionmaker[Session] | Callable[[], Session],
        *,
        host_key_scanner: Callable[[str, int, float], ScannedHostKey] = scan_host_key,
        bootstrap_script: bytes | None = None,
        inventory_script: bytes | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._security = security
        self._session_factory = session_factory
        self._host_key_scanner = host_key_scanner
        self._bootstrap_script = (
            bootstrap_script
            if bootstrap_script is not None
            else load_packaged_script(_REMOTE_SCRIPT_NAME)
        )
        self._inventory_script = (
            inventory_script
            if inventory_script is not None
            else load_packaged_script(_INVENTORY_SCRIPT_NAME)
        )
        self._clock = clock
        self._locks = _KeyedLockPool()

    @property
    def lock_entry_count(self) -> int:
        return self._locks.entry_count

    def handle(
        self,
        request: dict[str, Any],
        *,
        deadline: float | None = None,
    ) -> dict[str, Any]:
        request_id = request.get("request_id")
        if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
            return self._error("", "INVALID_REQUEST", "Bootstrap request is invalid")
        operation = request.get("operation")
        request_deadline = min(
            deadline if deadline is not None else math.inf,
            self._clock() + REQUEST_TIMEOUT_SECONDS,
        )
        try:
            self._operation_timeout(request_deadline)
            if operation == "scan_host_key":
                return self._scan(request_id, request, request_deadline)
            if operation == "bootstrap":
                return self._bootstrap(request_id, request, request_deadline)
            if operation == "test_connection":
                return self._test_connection(request_id, request, request_deadline)
            if operation == "probe":
                return self._probe(request_id, request, request_deadline)
            if operation == "rotate_key":
                return self._rotate_key(request_id, request, request_deadline)
            raise BootstrapOperationError("INVALID_REQUEST", "Bootstrap request is invalid")
        except HostKeyMismatchError:
            return self._error(
                request_id,
                "HOST_KEY_MISMATCH",
                "SSH host fingerprint changed",
            )
        except BootstrapOperationError as error:
            return self._error(
                request_id,
                error.code,
                error.message,
                cleanup_required=error.cleanup_required,
            )
        except Exception:
            if operation == "probe":
                return self._error(
                    request_id,
                    "PROBE_FAILED",
                    "Edge inventory probe failed",
                )
            return self._error(
                request_id,
                "BOOTSTRAP_FAILED",
                "Edge node bootstrap failed",
            )

    def _scan(
        self,
        request_id: str,
        request: dict[str, Any],
        deadline: float,
    ) -> dict[str, Any]:
        self._require_keys(request, {"request_id", "operation", "host", "port"})
        scanned = self._host_key_scanner(
            _canonical_host(_required_string(request, "host")),
            _required_port(request),
            self._operation_timeout(deadline),
        )
        return {
            "request_id": request_id,
            "status": "ok",
            "host_key_type": scanned.host_key_type,
            "fingerprint": scanned.fingerprint,
        }

    def _bootstrap(
        self,
        request_id: str,
        request: dict[str, Any],
        deadline: float,
    ) -> dict[str, Any]:
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
        host = _canonical_host(_required_string(request, "host"))
        port = _required_port(request)
        administrator = _required_string(request, "administrator")
        password = _required_string(request, "password")
        confirmed_fingerprint = _required_string(request, "confirmed_fingerprint")
        node_name = _required_string(request, "node_name")

        outer_keys = {f"host:{host}:{port}", f"name:{node_name}"}
        with self._locks.acquire(outer_keys, deadline, clock=self._clock):
            node_id = self._bootstrap_preflight(node_name, host, port)
            with self._locks.acquire({f"node:{node_id}"}, deadline, clock=self._clock):
                return self._bootstrap_locked(
                    request_id=request_id,
                    request=request,
                    host=host,
                    port=port,
                    administrator=administrator,
                    password=password,
                    confirmed_fingerprint=confirmed_fingerprint,
                    node_name=node_name,
                    node_id=node_id,
                    deadline=deadline,
                )

    def _bootstrap_locked(
        self,
        *,
        request_id: str,
        request: dict[str, Any],
        host: str,
        port: int,
        administrator: str,
        password: str,
        confirmed_fingerprint: str,
        node_name: str,
        node_id: str,
        deadline: float,
    ) -> dict[str, Any]:
        self._bootstrap_preflight(node_name, host, port, expected_node_id=node_id)
        scanned = self._host_key_scanner(
            host,
            port,
            self._operation_timeout(deadline),
        )
        if not hmac.compare_digest(scanned.fingerprint, confirmed_fingerprint):
            raise HostKeyMismatchError("SSH host key did not match")

        private_key, public_key = _generate_key_pair()
        password_session = None
        key_session = None
        key_may_be_installed = False
        try:
            password_session = self._security.ssh_client.connect_password(
                host=host,
                port=port,
                username=administrator,
                password=password,
                expected_fingerprint=confirmed_fingerprint,
                timeout_seconds=self._operation_timeout(deadline),
            )
            password = ""
            request["password"] = ""
            key_may_be_installed = True
            self._run_remote_script(
                password_session,
                _remote_data(
                    "bootstrap_add",
                    public_key=public_key,
                    node_id=node_id,
                    key_version=1,
                ),
                deadline=deadline,
                require_privilege=True,
            )

            key_session = self._security.ssh_client.connect(
                host=host,
                port=port,
                username=_SSH_USER,
                private_key=private_key,
                expected_fingerprint=confirmed_fingerprint,
                timeout_seconds=self._operation_timeout(deadline),
            )
            key_session.close()
            key_session = None

            encrypted = self._security.credential_cipher.encrypt(private_key)
            self._persist_bootstrap(
                node_id=node_id,
                node_name=node_name,
                host=host,
                port=port,
                scanned=scanned,
                confirmed_fingerprint=confirmed_fingerprint,
                public_key=public_key,
                encrypted=encrypted,
                deadline=deadline,
            )
            key_may_be_installed = False
            return {"request_id": request_id, "status": "ok", "node_id": node_id}
        except Exception:
            if key_may_be_installed and password_session is not None:
                try:
                    self._run_remote_script(
                        password_session,
                        _remote_data(
                            "remove_key",
                            node_id=node_id,
                            key_version=1,
                        ),
                        deadline=deadline,
                        require_privilege=True,
                    )
                except Exception:
                    raise BootstrapOperationError(
                        "BOOTSTRAP_CLEANUP_FAILED",
                        "Edge node bootstrap cleanup failed",
                        cleanup_required=True,
                    ) from None
            raise
        finally:
            password = ""
            if key_session is not None:
                key_session.close()
            if password_session is not None:
                password_session.close()

    def _bootstrap_preflight(
        self,
        node_name: str,
        host: str,
        port: int,
        *,
        expected_node_id: str | None = None,
    ) -> str:
        with self._session_factory() as session:
            node = session.scalar(select(ComputeNode).where(ComputeNode.name == node_name))
            if node is not None:
                credential = session.scalar(
                    select(EdgeSshCredential).where(EdgeSshCredential.node_id == node.id)
                )
                if credential is not None:
                    raise BootstrapOperationError(
                        "NODE_ALREADY_BOOTSTRAPPED",
                        "Edge node is already bootstrapped; use rotate-key",
                    )
                if node.platform_kind != _SSH_PLATFORM_KIND:
                    raise BootstrapOperationError(
                        "NODE_NAME_CONFLICT",
                        "Node name is already used by another node type",
                    )
                if expected_node_id is not None and node.id != expected_node_id:
                    raise BootstrapOperationError(
                        "NODE_NAME_CONFLICT", "Node name is already in use"
                    )

            host_credential = session.scalar(
                select(EdgeSshCredential).where(
                    EdgeSshCredential.ssh_host == host,
                    EdgeSshCredential.ssh_port == port,
                )
            )
            if host_credential is not None and (
                node is None or host_credential.node_id != node.id
            ):
                raise BootstrapOperationError(
                    "SSH_HOST_IN_USE",
                    "SSH host and port are already assigned to another node",
                )
            return node.id if node is not None else (expected_node_id or str(uuid4()))

    def _persist_bootstrap(
        self,
        *,
        node_id: str,
        node_name: str,
        host: str,
        port: int,
        scanned: ScannedHostKey,
        confirmed_fingerprint: str,
        public_key: str,
        encrypted: EncryptedSecret,
        deadline: float,
    ) -> None:
        with self._session_factory() as session:
            try:
                node = session.scalar(select(ComputeNode).where(ComputeNode.name == node_name))
                if node is None:
                    node = ComputeNode(
                        id=node_id,
                        name=node_name,
                        status="online",
                        architecture="unknown",
                        platform_kind=_SSH_PLATFORM_KIND,
                        capabilities={},
                        resources={},
                        fingerprint={},
                        agent_version="ssh-bootstrap",
                    )
                    session.add(node)
                    session.flush()
                elif node.id != node_id or node.platform_kind != _SSH_PLATFORM_KIND:
                    raise BootstrapOperationError(
                        "NODE_NAME_CONFLICT", "Node name is already in use"
                    )
                if session.scalar(
                    select(EdgeSshCredential).where(EdgeSshCredential.node_id == node.id)
                ) is not None:
                    raise BootstrapOperationError(
                        "NODE_ALREADY_BOOTSTRAPPED",
                        "Edge node is already bootstrapped; use rotate-key",
                    )
                if session.scalar(
                    select(EdgeSshCredential).where(
                        EdgeSshCredential.ssh_host == host,
                        EdgeSshCredential.ssh_port == port,
                    )
                ) is not None:
                    raise BootstrapOperationError(
                        "SSH_HOST_IN_USE",
                        "SSH host and port are already assigned to another node",
                    )

                node.status = "online"
                node.fingerprint = {
                    "ssh_host_key_type": scanned.host_key_type,
                    "ssh_host_key_fingerprint": confirmed_fingerprint,
                }
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
                    key_version=1,
                )
                self._operation_timeout(deadline)
                session.commit()
            except Exception:
                session.rollback()
                raise

    def _test_connection(
        self,
        request_id: str,
        request: dict[str, Any],
        deadline: float,
    ) -> dict[str, Any]:
        self._require_keys(request, {"request_id", "operation", "node_id"})
        node_id = _required_string(request, "node_id")
        with self._locks.acquire({f"node:{node_id}"}, deadline, clock=self._clock):
            with self._session_factory() as session:
                credential = self._credential_or_error(session, node_id)
                private_key = self._decrypt(credential)
                ssh_session = self._security.ssh_client.connect(
                    host=credential.ssh_host,
                    port=credential.ssh_port,
                    username=credential.ssh_user,
                    private_key=private_key,
                    expected_fingerprint=credential.host_key_fingerprint,
                    timeout_seconds=self._operation_timeout(deadline),
                )
                ssh_session.close()
        return {"request_id": request_id, "status": "ok", "node_id": node_id}

    def _probe(
        self,
        request_id: str,
        request: dict[str, Any],
        deadline: float,
    ) -> dict[str, Any]:
        self._require_keys(request, {"request_id", "operation", "node_id"})
        node_id = _required_string(request, "node_id")
        with self._locks.acquire({f"node:{node_id}"}, deadline, clock=self._clock):
            with self._session_factory() as session:
                credential = self._credential_or_error(session, node_id)
                private_key = self._decrypt(credential)
                ssh_session = self._security.ssh_client.connect(
                    host=credential.ssh_host,
                    port=credential.ssh_port,
                    username=credential.ssh_user,
                    private_key=private_key,
                    expected_fingerprint=credential.host_key_fingerprint,
                    timeout_seconds=self._operation_timeout(deadline),
                )
                try:
                    output = self._run_inventory_script(ssh_session, deadline=deadline)
                    snapshot = parse_inventory(output)
                finally:
                    ssh_session.close()
        return {
            "request_id": request_id,
            "status": "ok",
            "node_id": node_id,
            "inventory": snapshot.model_dump(mode="json"),
        }

    def _rotate_key(
        self,
        request_id: str,
        request: dict[str, Any],
        deadline: float,
    ) -> dict[str, Any]:
        self._require_keys(request, {"request_id", "operation", "node_id"})
        node_id = _required_string(request, "node_id")
        with self._locks.acquire({f"node:{node_id}"}, deadline, clock=self._clock):
            return self._rotate_key_locked(request_id, node_id, deadline)

    def _rotate_key_locked(
        self,
        request_id: str,
        node_id: str,
        deadline: float,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            credential = self._credential_or_error(session, node_id)
            old_private_key = self._decrypt(credential)
            new_key_version = credential.key_version + 1
            new_private_key, new_public_key = _generate_key_pair()
            old_session = None
            verified_session = None
            new_key_may_be_installed = False
            try:
                old_session = self._security.ssh_client.connect(
                    host=credential.ssh_host,
                    port=credential.ssh_port,
                    username=credential.ssh_user,
                    private_key=old_private_key,
                    expected_fingerprint=credential.host_key_fingerprint,
                    timeout_seconds=self._operation_timeout(deadline),
                )
                new_key_may_be_installed = True
                self._run_remote_script(
                    old_session,
                    _remote_data(
                        "rotate_add",
                        public_key=new_public_key,
                        node_id=node_id,
                        key_version=new_key_version,
                    ),
                    deadline=deadline,
                )

                verified_session = self._security.ssh_client.connect(
                    host=credential.ssh_host,
                    port=credential.ssh_port,
                    username=credential.ssh_user,
                    private_key=new_private_key,
                    expected_fingerprint=credential.host_key_fingerprint,
                    timeout_seconds=self._operation_timeout(deadline),
                )

                encrypted = self._security.credential_cipher.encrypt(new_private_key)
                self._set_credential(
                    credential,
                    host=credential.ssh_host,
                    port=credential.ssh_port,
                    host_key_type=credential.host_key_type,
                    host_key_fingerprint=credential.host_key_fingerprint,
                    public_key=new_public_key,
                    encrypted=encrypted,
                    key_version=new_key_version,
                )
                credential.rotated_at = datetime.now(UTC)
                self._operation_timeout(deadline)
                session.commit()
                new_key_may_be_installed = False

                try:
                    self._run_remote_script(
                        verified_session,
                        _remote_data(
                            "rotate_commit",
                            node_id=node_id,
                            key_version=new_key_version,
                        ),
                        deadline=deadline,
                    )
                except Exception:
                    raise BootstrapOperationError(
                        "ROTATE_CLEANUP_FAILED",
                        "SSH key rotation cleanup failed",
                        cleanup_required=True,
                    ) from None
            except Exception:
                if new_key_may_be_installed and old_session is not None:
                    session.rollback()
                    try:
                        self._run_remote_script(
                            old_session,
                            _remote_data(
                                "remove_key",
                                node_id=node_id,
                                key_version=new_key_version,
                            ),
                            deadline=deadline,
                        )
                    except Exception:
                        raise BootstrapOperationError(
                            "ROTATE_CLEANUP_FAILED",
                            "SSH key rotation cleanup failed",
                            cleanup_required=True,
                        ) from None
                raise
            finally:
                if verified_session is not None:
                    verified_session.close()
                if old_session is not None:
                    old_session.close()

        return {"request_id": request_id, "status": "ok", "node_id": node_id}

    def _run_remote_script(
        self,
        ssh_session: Any,
        data: bytes,
        *,
        deadline: float,
        require_privilege: bool = False,
    ) -> None:
        workspace = ssh_session.create_private_directory(
            timeout_seconds=self._operation_timeout(deadline)
        )
        script_path = str(PurePosixPath(workspace.path) / _REMOTE_SCRIPT_NAME)
        data_path = str(PurePosixPath(workspace.path) / _REMOTE_DATA_NAME)
        remote_paths = (script_path, data_path)
        completed = False
        try:
            ssh_session.upload_bytes_exclusive(
                self._bootstrap_script,
                script_path,
                expected_owner_uid=workspace.owner_uid,
                timeout_seconds=self._operation_timeout(deadline),
            )
            ssh_session.upload_bytes_exclusive(
                data,
                data_path,
                expected_owner_uid=workspace.owner_uid,
                timeout_seconds=self._operation_timeout(deadline),
            )
            for remote_path in remote_paths:
                ssh_session.validate_remote_file(
                    remote_path,
                    expected_owner_uid=workspace.owner_uid,
                    timeout_seconds=self._operation_timeout(deadline),
                )

            if require_privilege:
                identity = ssh_session.run(
                    "/usr/bin/id -u",
                    timeout_seconds=self._operation_timeout(deadline),
                )
                try:
                    user_id = int(identity.stdout.strip())
                except (TypeError, ValueError):
                    user_id = -1
                if identity.exit_status != 0 or user_id < 0:
                    raise BootstrapOperationError(
                        "REMOTE_SETUP_FAILED", "Remote SSH setup failed"
                    )
                if user_id != 0:
                    preflight = ssh_session.run(
                        _SUDO_PREFLIGHT_COMMAND,
                        timeout_seconds=self._operation_timeout(deadline),
                    )
                    if preflight.exit_status != 0:
                        raise BootstrapOperationError(
                            "SUDO_UNAVAILABLE",
                            "Passwordless sudo is required for SSH bootstrap",
                        )

            command = f"/bin/bash {shlex.quote(script_path)} {shlex.quote(data_path)}"
            result = ssh_session.run(
                command,
                timeout_seconds=self._operation_timeout(deadline),
            )
            if result.exit_status != 0:
                raise BootstrapOperationError(
                    "REMOTE_SETUP_FAILED", "Remote SSH setup failed"
                )
            completed = True
        finally:
            try:
                ssh_session.cleanup_private_directory(
                    workspace,
                    remote_paths,
                    timeout_seconds=self._operation_timeout(deadline),
                )
            except Exception:
                if completed:
                    raise

    def _run_inventory_script(self, ssh_session: Any, *, deadline: float) -> bytes:
        workspace = ssh_session.create_private_directory(
            timeout_seconds=self._operation_timeout(deadline)
        )
        script_path = str(PurePosixPath(workspace.path) / _INVENTORY_SCRIPT_NAME)
        remote_paths = (script_path,)
        completed = False
        try:
            ssh_session.upload_bytes_exclusive(
                self._inventory_script,
                script_path,
                expected_owner_uid=workspace.owner_uid,
                timeout_seconds=self._operation_timeout(deadline),
            )
            ssh_session.validate_remote_file(
                script_path,
                expected_owner_uid=workspace.owner_uid,
                timeout_seconds=self._operation_timeout(deadline),
            )
            result = ssh_session.run(
                f"/bin/bash {shlex.quote(script_path)}",
                timeout_seconds=self._operation_timeout(deadline),
            )
            if result.exit_status != 0:
                raise BootstrapOperationError(
                    "PROBE_FAILED", "Edge inventory probe failed"
                )
            completed = True
            return result.stdout
        finally:
            try:
                ssh_session.cleanup_private_directory(
                    workspace,
                    remote_paths,
                    timeout_seconds=self._operation_timeout(deadline),
                )
            except Exception:
                if completed:
                    raise

    def _operation_timeout(self, deadline: float) -> float:
        try:
            return _remaining(
                deadline,
                TimeoutError,
                "Edge bootstrap request timed out",
                clock=self._clock,
            )
        except TimeoutError:
            raise BootstrapOperationError(
                "REQUEST_TIMEOUT", "Edge bootstrap request timed out"
            ) from None

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
        key_version: int,
    ) -> None:
        credential.ssh_host = host
        credential.ssh_port = port
        credential.ssh_user = _SSH_USER
        credential.host_key_type = host_key_type
        credential.host_key_fingerprint = host_key_fingerprint
        credential.public_key = public_key
        credential.encrypted_private_key = encrypted.ciphertext
        credential.encryption_nonce = encrypted.nonce
        credential.key_version = key_version

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
    def _error(
        request_id: str,
        code: str,
        message: str,
        *,
        cleanup_required: bool = False,
    ) -> dict[str, Any]:
        response: dict[str, Any] = {
            "request_id": request_id,
            "status": "error",
            "error_code": code,
            "error_message": message,
        }
        if cleanup_required:
            response["cleanup_required"] = True
        return response


def _current_uid() -> int | None:
    getuid = getattr(os, "getuid", None)
    return getuid() if getuid is not None else None


def _validate_owned_socket(attributes: Any, current_uid: int | None) -> None:
    if not stat.S_ISSOCK(attributes.st_mode) or (
        current_uid is not None
        and getattr(attributes, "st_uid", None) is not None
        and attributes.st_uid != current_uid
    ):
        raise RuntimeError("refusing unsafe bootstrap socket entry")


def _socket_identity(attributes: Any) -> tuple[int, int]:
    return attributes.st_dev, attributes.st_ino


class BootstrapServer:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session] | Callable[[], Session],
        *,
        security_context: EdgeExecutorSecurityContext | None = None,
        security_initializer: Callable[[Settings], EdgeExecutorSecurityContext] | None = None,
        socket_factory: Callable[[int, int], Any] = socket.socket,
        probe_socket_factory: Callable[[int, int], Any] = socket.socket,
        max_concurrency: int = 4,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 1 <= max_concurrency <= 64:
            raise ValueError("bootstrap concurrency must be between 1 and 64")
        self._settings = settings
        self._session_factory = session_factory
        self._security_context = security_context
        self._security_initializer = security_initializer or initialize_security
        self._socket_factory = socket_factory
        self._probe_socket_factory = probe_socket_factory
        self._max_concurrency = max_concurrency
        self._clock = clock
        self._listener: Any | None = None
        self._operations: BootstrapOperations | None = None
        self._socket_identity: tuple[int, int] | None = None
        self._stopping = threading.Event()

    def start(self) -> None:
        security = self._security_context or self._security_initializer(self._settings)
        operations = BootstrapOperations(security, self._session_factory, clock=self._clock)
        socket_path = self._settings.edge_bootstrap_socket
        self._prepare_parent(socket_path.parent)
        self._remove_stale_socket(socket_path)

        listener = self._socket_factory(_AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(socket_path))
            attributes = os.lstat(socket_path)
            _validate_owned_socket(attributes, _current_uid())
            self._socket_identity = _socket_identity(attributes)
            socket_path.chmod(0o600)
            listener.listen(self._max_concurrency)
            listener.settimeout(1.0)
        except Exception:
            listener.close()
            self._unlink_socket_if_unchanged()
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
        self._unlink_socket_if_unchanged()

    def _handle_connection(self, connection: Any) -> None:
        deadline = self._clock() + REQUEST_TIMEOUT_SECONDS
        try:
            request = decode_frame(connection, deadline=deadline, clock=self._clock)
            client_deadline = request.pop(_CLIENT_DEADLINE_FIELD, deadline)
            if (
                not isinstance(client_deadline, (int, float))
                or isinstance(client_deadline, bool)
                or not math.isfinite(client_deadline)
            ):
                raise BootstrapProtocolError("Bootstrap frame is invalid")
            deadline = min(deadline, float(client_deadline))
            _set_connection_timeout(connection, deadline, clock=self._clock)
            if connection.recv(1) != b"":
                raise BootstrapProtocolError("Bootstrap frame is invalid")
            assert self._operations is not None
            response = self._operations.handle(request, deadline=deadline)
            _set_connection_timeout(connection, deadline, clock=self._clock)
            connection.sendall(encode_frame(response))
        except (BootstrapProtocolError, BootstrapProtocolTimeout):
            try:
                _set_connection_timeout(connection, deadline, clock=self._clock)
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

    @staticmethod
    def _prepare_parent(parent: Path) -> None:
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        attributes = os.lstat(parent)
        current_uid = _current_uid()
        if not stat.S_ISDIR(attributes.st_mode) or (
            current_uid is not None
            and getattr(attributes, "st_uid", None) is not None
            and attributes.st_uid != current_uid
        ):
            raise RuntimeError("bootstrap socket parent is unsafe")
        parent.chmod(0o700)
        if os.name != "nt" and stat.S_IMODE(os.lstat(parent).st_mode) != 0o700:
            raise RuntimeError("bootstrap socket parent is unsafe")

    def _remove_stale_socket(self, socket_path: Path) -> None:
        try:
            before = os.lstat(socket_path)
        except FileNotFoundError:
            return
        _validate_owned_socket(before, _current_uid())

        probe = self._probe_socket_factory(_AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.settimeout(0.2)
            probe.connect(str(socket_path))
        except OSError as error:
            if error.errno not in {errno.ECONNREFUSED, errno.ENOENT} and getattr(
                error, "winerror", None
            ) != 10061:
                raise RuntimeError("bootstrap socket could not be safely probed") from None
        else:
            raise RuntimeError("bootstrap socket server is already active")
        finally:
            probe.close()

        try:
            after = os.lstat(socket_path)
        except FileNotFoundError:
            return
        _validate_owned_socket(after, _current_uid())
        if _socket_identity(after) != _socket_identity(before):
            raise RuntimeError("bootstrap socket changed during stale cleanup")
        socket_path.unlink()

    def _unlink_socket_if_unchanged(self) -> None:
        if self._socket_identity is None:
            return
        socket_path = self._settings.edge_bootstrap_socket
        try:
            attributes = os.lstat(socket_path)
        except FileNotFoundError:
            self._socket_identity = None
            return
        try:
            _validate_owned_socket(attributes, _current_uid())
        except RuntimeError:
            return
        if _socket_identity(attributes) != self._socket_identity:
            return
        socket_path.unlink()
        self._socket_identity = None


def main() -> None:
    settings = get_settings()
    security = initialize_security(settings)
    session_factory = create_session_factory()
    server = BootstrapServer(
        settings,
        session_factory,
        security_context=security,
    )
    try:
        server.serve_forever()
    finally:
        server.stop()


if __name__ == "__main__":
    main()
