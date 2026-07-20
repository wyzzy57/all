from __future__ import annotations

import json
from pathlib import Path
import socket
import struct
import time
from typing import Any, Callable
from uuid import uuid4


MAX_FRAME_BYTES = 64 * 1024
REQUEST_TIMEOUT_SECONDS = 60.0
_AF_UNIX = getattr(socket, "AF_UNIX", 1)
_REJECTION_MESSAGES = {
    "INVALID_FRAME": "Bootstrap frame is invalid",
    "INVALID_REQUEST": "Bootstrap request is invalid",
    "HOST_KEY_MISMATCH": "SSH host fingerprint changed",
    "NODE_NOT_FOUND": "Edge node was not found",
    "NODE_ALREADY_BOOTSTRAPPED": "Edge node is already bootstrapped; use rotate-key",
    "NODE_NAME_CONFLICT": "Node name is already in use",
    "SSH_HOST_IN_USE": "SSH host and port are already assigned to another node",
    "REMOTE_SETUP_FAILED": "Remote SSH setup failed",
    "SUDO_UNAVAILABLE": "Passwordless sudo is required for SSH bootstrap",
    "REQUEST_TIMEOUT": "Edge bootstrap request timed out",
    "BOOTSTRAP_CLEANUP_FAILED": "Edge node bootstrap cleanup failed",
    "ROTATE_CLEANUP_FAILED": "SSH key rotation cleanup failed",
    "BOOTSTRAP_FAILED": "Edge node bootstrap failed",
}
_CLIENT_DEADLINE_FIELD = "_deadline_monotonic"


class BootstrapChannelError(Exception):
    """Fixed-message private channel failure safe for API responses."""


class BootstrapRequestRejected(Exception):
    def __init__(self, code: str, message: str, *, cleanup_required: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.cleanup_required = cleanup_required


def _encode(message: dict[str, Any]) -> bytes:
    try:
        payload = json.dumps(
            message,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise BootstrapChannelError("Edge bootstrap channel failed") from None
    if not payload or len(payload) > MAX_FRAME_BYTES:
        raise BootstrapChannelError("Edge bootstrap channel failed")
    return struct.pack(">I", len(payload)) + payload


def _remaining(deadline: float, clock: Callable[[], float]) -> float:
    remaining = deadline - clock()
    if remaining <= 0:
        raise BootstrapChannelError("Edge bootstrap request timed out")
    return remaining


def _set_timeout(connection: Any, deadline: float, clock: Callable[[], float]) -> None:
    connection.settimeout(_remaining(deadline, clock))


def _read_exact(
    connection: Any,
    size: int,
    *,
    deadline: float,
    clock: Callable[[], float],
) -> bytes:
    chunks: list[bytes] = []
    try:
        while size:
            _set_timeout(connection, deadline, clock)
            chunk = connection.recv(size)
            if not chunk:
                raise BootstrapChannelError("Edge bootstrap channel failed")
            chunks.append(chunk)
            size -= len(chunk)
    except (socket.timeout, TimeoutError):
        raise BootstrapChannelError("Edge bootstrap request timed out") from None
    return b"".join(chunks)


def _decode(
    connection: Any,
    *,
    deadline: float,
    clock: Callable[[], float],
) -> dict[str, Any]:
    length = struct.unpack(
        ">I", _read_exact(connection, 4, deadline=deadline, clock=clock)
    )[0]
    if length == 0 or length > MAX_FRAME_BYTES:
        raise BootstrapChannelError("Edge bootstrap channel failed")
    try:
        text = _read_exact(
            connection,
            length,
            deadline=deadline,
            clock=clock,
        ).decode("utf-8")
        value, end = json.JSONDecoder().raw_decode(text)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BootstrapChannelError("Edge bootstrap channel failed") from None
    if end != len(text) or not isinstance(value, dict):
        raise BootstrapChannelError("Edge bootstrap channel failed")
    return value


class EdgeBootstrapChannel:
    def __init__(
        self,
        socket_path: Path,
        *,
        socket_factory: Callable[[], Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._socket_path = socket_path
        self._socket_factory = socket_factory or (
            lambda: socket.socket(_AF_UNIX, socket.SOCK_STREAM)
        )
        self._clock = clock

    def request(
        self,
        request: dict[str, Any],
        *,
        deadline: float | None = None,
    ) -> dict[str, Any]:
        request_id = request.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            raise BootstrapChannelError("Edge bootstrap channel failed")
        request_deadline = min(
            deadline if deadline is not None else float("inf"),
            self._clock() + REQUEST_TIMEOUT_SECONDS,
        )
        _remaining(request_deadline, self._clock)
        wire_request = {**request, _CLIENT_DEADLINE_FIELD: request_deadline}
        frame = _encode(wire_request)
        connection = self._socket_factory()
        try:
            _set_timeout(connection, request_deadline, self._clock)
            connection.connect(str(self._socket_path))
            _set_timeout(connection, request_deadline, self._clock)
            connection.sendall(frame)
            _set_timeout(connection, request_deadline, self._clock)
            connection.shutdown(socket.SHUT_WR)
            response = _decode(
                connection,
                deadline=request_deadline,
                clock=self._clock,
            )
            _set_timeout(connection, request_deadline, self._clock)
            if connection.recv(1) != b"":
                raise BootstrapChannelError("Edge bootstrap channel failed")
        except BootstrapChannelError:
            raise
        except (socket.timeout, TimeoutError):
            raise BootstrapChannelError("Edge bootstrap request timed out") from None
        except Exception:
            raise BootstrapChannelError("Edge bootstrap channel failed") from None
        finally:
            connection.close()
        if response.get("request_id") != request_id:
            raise BootstrapChannelError("Edge bootstrap channel failed")
        if response.get("status") == "error":
            expected_fields = {
                "request_id",
                "status",
                "error_code",
                "error_message",
            }
            cleanup_required = response.get("cleanup_required", False)
            if cleanup_required is True:
                expected_fields.add("cleanup_required")
            if set(response) != expected_fields:
                raise BootstrapChannelError("Edge bootstrap channel failed")
            code = response.get("error_code")
            if not isinstance(code, str) or code not in _REJECTION_MESSAGES:
                raise BootstrapChannelError("Edge bootstrap channel failed")
            raise BootstrapRequestRejected(
                code,
                _REJECTION_MESSAGES[code],
                cleanup_required=cleanup_required is True,
            )
        if response.get("status") != "ok":
            raise BootstrapChannelError("Edge bootstrap channel failed")
        operation = request.get("operation")
        if operation == "scan_host_key":
            if set(response) != {
                "request_id",
                "status",
                "host_key_type",
                "fingerprint",
            } or not all(
                isinstance(response.get(name), str)
                for name in ("host_key_type", "fingerprint")
            ):
                raise BootstrapChannelError("Edge bootstrap channel failed")
        elif operation in {"bootstrap", "test_connection", "rotate_key"}:
            if set(response) != {"request_id", "status", "node_id"} or not isinstance(
                response.get("node_id"), str
            ):
                raise BootstrapChannelError("Edge bootstrap channel failed")
        else:
            raise BootstrapChannelError("Edge bootstrap channel failed")
        return response


class EdgeBootstrapService:
    def __init__(self, socket_path: Path, *, channel: EdgeBootstrapChannel | None = None) -> None:
        self._channel = channel or EdgeBootstrapChannel(socket_path)

    def scan_host_key(
        self,
        *,
        host: str,
        port: int,
        deadline: float | None = None,
    ) -> dict[str, Any]:
        return self._send("scan_host_key", host=host, port=port, deadline=deadline)

    def bootstrap(
        self,
        *,
        host: str,
        port: int,
        administrator: str,
        password: str,
        confirmed_fingerprint: str,
        node_name: str,
        deadline: float | None = None,
    ) -> dict[str, Any]:
        return self._send(
            "bootstrap",
            host=host,
            port=port,
            administrator=administrator,
            password=password,
            confirmed_fingerprint=confirmed_fingerprint,
            node_name=node_name,
            deadline=deadline,
        )

    def test_connection(
        self,
        *,
        node_id: str,
        deadline: float | None = None,
    ) -> dict[str, Any]:
        return self._send("test_connection", node_id=node_id, deadline=deadline)

    def rotate_key(
        self,
        *,
        node_id: str,
        deadline: float | None = None,
    ) -> dict[str, Any]:
        return self._send("rotate_key", node_id=node_id, deadline=deadline)

    def _send(
        self,
        operation: str,
        *,
        deadline: float | None = None,
        **payload: Any,
    ) -> dict[str, Any]:
        response = self._channel.request(
            {"request_id": str(uuid4()), "operation": operation, **payload},
            deadline=deadline,
        )
        response.pop("request_id", None)
        return response
