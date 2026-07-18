"""Deterministic HTTP and WebSocket-upgrade backend for proxy boundary tests."""

from __future__ import annotations

import hmac
import json
import os
from pathlib import Path
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


MANAGEMENT_PROXY_AUTH_TOKEN_HEADER = "X-Visiox-Management-Proxy-Token"
MANAGEMENT_PATH = re.compile(r"^/nodes/[^/]+(?:/drain)?$")


def _management_proxy_token() -> str:
    token_file = os.environ.get("VISIOX_TEST_PROXY_TOKEN_FILE", "")
    if not token_file:
        raise RuntimeError("management proxy token file is required")
    token = Path(token_file).read_text(encoding="ascii").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token):
        raise RuntimeError("management proxy token is invalid")
    return token


MANAGEMENT_PROXY_TOKEN = _management_proxy_token()


class BackendHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        if not self._authorize_management_request():
            return
        if self.path == "/agent/v1/connect":
            self._handle_websocket_upgrade()
            return
        self._send_json()

    def do_POST(self) -> None:
        if not self._authorize_management_request():
            return
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length:
            self.rfile.read(content_length)
        self._send_json()

    def _authorize_management_request(self) -> bool:
        if not self._is_management_request():
            return True
        supplied = self.headers.get(MANAGEMENT_PROXY_AUTH_TOKEN_HEADER, "")
        if hmac.compare_digest(MANAGEMENT_PROXY_TOKEN, supplied):
            return True
        self.send_response(403)
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def _is_management_request(self) -> bool:
        if self.command == "POST" and self.path == "/agent/v1/enrollment-tokens":
            return True
        if self.command == "GET" and self.path in {"/nodes", "/resource-pools"}:
            return True
        if self.command == "GET" and MANAGEMENT_PATH.fullmatch(self.path):
            return True
        return self.command == "POST" and bool(re.fullmatch(r"/nodes/[^/]+/drain", self.path))

    def _handle_websocket_upgrade(self) -> None:
        upgrade = self.headers.get("Upgrade", "")
        connection = self.headers.get("Connection", "")
        if upgrade.lower() != "websocket" or "upgrade" not in connection.lower():
            self.send_error(400, "WebSocket forwarding headers missing")
            return

        self.send_response(101, "Switching Protocols")
        self.send_header("Connection", "Upgrade")
        self.send_header("Upgrade", "websocket")
        self.send_header("X-Backend-Upgrade", upgrade)
        self.send_header("X-Backend-Connection", connection)
        self.send_header("X-Backend-Forwarded-Proto", self.headers.get("X-Forwarded-Proto", ""))
        self.send_header("X-Backend-Forwarded-For", self.headers.get("X-Forwarded-For", ""))
        self.send_header("X-Backend-Host", self.headers.get("Host", ""))
        self.end_headers()

    def _send_json(self) -> None:
        payload = {
            "method": self.command,
            "path": self.path,
            "headers": {
                "host": self.headers.get("Host", ""),
                "x-forwarded-for": self.headers.get("X-Forwarded-For", ""),
                "x-forwarded-proto": self.headers.get("X-Forwarded-Proto", ""),
            },
            "management_proxy_authenticated": self._is_management_request(),
        }
        encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *_args: object) -> None:
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8000), BackendHandler).serve_forever()
