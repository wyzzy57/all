from __future__ import annotations

import json
from pathlib import Path
import socket
import struct
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
    "REMOTE_SETUP_FAILED": "Remote SSH setup failed",
    "ROTATE_CLEANUP_FAILED": "SSH key rotation cleanup failed",
    "BOOTSTRAP_FAILED": "Edge node bootstrap failed",
}


class BootstrapChannelError(Exception):
    """Fixed-message private channel failure safe for API responses."""


class BootstrapRequestRejected(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


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


def _read_exact(connection: Any, size: int) -> bytes:
    chunks: list[bytes] = []
    try:
        while size:
            chunk = connection.recv(size)
            if not chunk:
                raise BootstrapChannelError("Edge bootstrap channel failed")
            chunks.append(chunk)
            size -= len(chunk)
    except (socket.timeout, TimeoutError):
        raise BootstrapChannelError("Edge bootstrap request timed out") from None
    return b"".join(chunks)


def _decode(connection: Any) -> dict[str, Any]:
    length = struct.unpack(">I", _read_exact(connection, 4))[0]
    if length == 0 or length > MAX_FRAME_BYTES:
        raise BootstrapChannelError("Edge bootstrap channel failed")
    try:
        text = _read_exact(connection, length).decode("utf-8")
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
    ) -> None:
        self._socket_path = socket_path
        self._socket_factory = socket_factory or (
            lambda: socket.socket(_AF_UNIX, socket.SOCK_STREAM)
        )

    def request(self, request: dict[str, Any]) -> dict[str, Any]:
        request_id = request.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            raise BootstrapChannelError("Edge bootstrap channel failed")
        frame = _encode(request)
        connection = self._socket_factory()
        try:
            connection.settimeout(REQUEST_TIMEOUT_SECONDS)
            connection.connect(str(self._socket_path))
            connection.sendall(frame)
            connection.shutdown(socket.SHUT_WR)
            response = _decode(connection)
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
            if set(response) != {
                "request_id",
                "status",
                "error_code",
                "error_message",
            }:
                raise BootstrapChannelError("Edge bootstrap channel failed")
            code = response.get("error_code")
            if not isinstance(code, str) or code not in _REJECTION_MESSAGES:
                raise BootstrapChannelError("Edge bootstrap channel failed")
            raise BootstrapRequestRejected(code, _REJECTION_MESSAGES[code])
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

    def scan_host_key(self, *, host: str, port: int) -> dict[str, Any]:
        return self._send("scan_host_key", host=host, port=port)

    def bootstrap(
        self,
        *,
        host: str,
        port: int,
        administrator: str,
        password: str,
        confirmed_fingerprint: str,
        node_name: str,
    ) -> dict[str, Any]:
        return self._send(
            "bootstrap",
            host=host,
            port=port,
            administrator=administrator,
            password=password,
            confirmed_fingerprint=confirmed_fingerprint,
            node_name=node_name,
        )

    def test_connection(self, *, node_id: str) -> dict[str, Any]:
        return self._send("test_connection", node_id=node_id)

    def rotate_key(self, *, node_id: str) -> dict[str, Any]:
        return self._send("rotate_key", node_id=node_id)

    def _send(self, operation: str, **payload: Any) -> dict[str, Any]:
        response = self._channel.request(
            {"request_id": str(uuid4()), "operation": operation, **payload}
        )
        response.pop("request_id", None)
        return response
