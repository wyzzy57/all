"""Deterministic HTTP and WebSocket-upgrade backend for proxy boundary tests."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class BackendHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        if self.path == "/agent/v1/connect":
            self._handle_websocket_upgrade()
            return
        self._send_json()

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length:
            self.rfile.read(content_length)
        self._send_json()

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
