import asyncio
from typing import Any

from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
import logging
import threading
import time

import pytest

from visiox_api.main import create_app
from visiox_api.routes import edge_ssh
from visiox_api.routes.edge_ssh import get_edge_bootstrap_service


BOOTSTRAP_REQUEST = {
    "host": "10.0.0.8",
    "port": 22,
    "administrator": "ubuntu",
    "password": "one-time-password",
    "confirmed_fingerprint": "SHA256:confirmed-host-key",
    "node_name": "edge-a",
}


class FakeBootstrapService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def scan_host_key(
        self,
        *,
        host: str,
        port: int,
        deadline: float | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            ("scan_host_key", {"host": host, "port": port, "deadline": deadline})
        )
        return {
            "status": "ok",
            "host_key_type": "ssh-ed25519",
            "fingerprint": "SHA256:confirmed-host-key",
        }

    def bootstrap(self, **request: Any) -> dict[str, Any]:
        self.calls.append(("bootstrap", request))
        return {"status": "ok", "node_id": "node-1"}

    def test_connection(
        self,
        *,
        node_id: str,
        deadline: float | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            ("test_connection", {"node_id": node_id, "deadline": deadline})
        )
        return {"status": "ok", "node_id": node_id}

    def rotate_key(
        self,
        *,
        node_id: str,
        deadline: float | None = None,
    ) -> dict[str, Any]:
        self.calls.append(("rotate_key", {"node_id": node_id, "deadline": deadline}))
        return {"status": "ok", "node_id": node_id}


def _client(fake_service: FakeBootstrapService) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_edge_bootstrap_service] = lambda: fake_service
    return TestClient(app)


def test_scan_host_key_route_never_accepts_or_forwards_password() -> None:
    service = FakeBootstrapService()
    client = _client(service)

    response = client.post(
        "/edge-nodes/scan-host-key",
        json={"host": "10.0.0.8", "port": 22, "password": "must-not-pass"},
    )

    assert response.status_code == 422
    assert service.calls == []
    assert "must-not-pass" not in response.text


def test_scan_host_key_malformed_password_returns_fixed_redacted_422() -> None:
    service = FakeBootstrapService()
    client = _client(service)

    response = client.post(
        "/edge-nodes/scan-host-key",
        json={
            "host": "10.0.0.8",
            "port": 22,
            "password": {"secret": "scan-secret"},
        },
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "Host-key scan request is invalid"}
    assert "scan-secret" not in response.text
    assert "input" not in response.text
    assert service.calls == []


def test_bootstrap_request_forwards_password_only_to_private_worker_service() -> None:
    service = FakeBootstrapService()
    client = _client(service)

    response = client.post("/edge-nodes/bootstrap", json=BOOTSTRAP_REQUEST)

    assert response.status_code == 201
    assert response.json() == {"status": "ok", "node_id": "node-1"}
    assert "one-time-password" not in response.text
    assert service.calls[0][0] == "bootstrap"
    assert service.calls[0][1]["password"] == "one-time-password"


@pytest.mark.parametrize(
    "body",
    [
        {**BOOTSTRAP_REQUEST, "password": {"secret": "object-secret"}},
        {**BOOTSTRAP_REQUEST, "password": 12345},
        {**BOOTSTRAP_REQUEST, "extra": "extra-secret"},
        [BOOTSTRAP_REQUEST],
    ],
    ids=["object-password", "wrong-password-type", "extra-field", "non-object"],
)
def test_bootstrap_validation_returns_fixed_redacted_422(
    body: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = FakeBootstrapService()
    client = _client(service)
    caplog.set_level(logging.DEBUG)

    response = client.post("/edge-nodes/bootstrap", json=body)

    assert response.status_code == 422
    assert response.json() == {"detail": "Bootstrap request is invalid"}
    combined = response.text + caplog.text
    for secret in ("object-secret", "extra-secret", "12345", "one-time-password"):
        assert secret not in combined
    assert "input" not in response.text
    assert service.calls == []


def test_bootstrap_malformed_json_returns_fixed_redacted_422() -> None:
    service = FakeBootstrapService()
    client = _client(service)

    response = client.post(
        "/edge-nodes/bootstrap",
        content=b'{"password":"malformed-secret"',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "Bootstrap request is invalid"}
    assert "malformed-secret" not in response.text
    assert service.calls == []


def test_bootstrap_oversized_body_returns_fixed_redacted_422() -> None:
    service = FakeBootstrapService()
    client = _client(service)

    response = client.post(
        "/edge-nodes/bootstrap",
        content=b'{"password":"' + (b"oversized-secret" * 5000) + b'"}',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "Bootstrap request is invalid"}
    assert "oversized-secret" not in response.text
    assert service.calls == []


def test_bootstrap_thread_scheduling_is_bounded_by_http_deadline() -> None:
    started = time.monotonic()
    deadline = started + 0.05

    def slow_call() -> dict[str, Any]:
        time.sleep(0.078)
        return {"status": "ok", "node_id": "too-late"}

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(edge_ssh._invoke_with_deadline(slow_call, deadline))
    elapsed = time.monotonic() - started

    assert exc_info.value.status_code == 504
    assert exc_info.value.detail == "Edge bootstrap request timed out"
    assert elapsed < 0.078


def test_bootstrap_raw_body_read_is_inside_http_deadline() -> None:
    async def delayed_receive():
        await asyncio.sleep(0.078)
        return {"type": "http.request", "body": b"{}", "more_body": False}

    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/edge-nodes/bootstrap",
            "raw_path": b"/edge-nodes/bootstrap",
            "query_string": b"",
            "headers": [],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        },
        delayed_receive,
    )
    started = time.monotonic()
    deadline = started + 0.05
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            edge_ssh._read_request_model(
                request,
                edge_ssh.BootstrapRequest,
                invalid_detail="Bootstrap request is invalid",
                deadline=deadline,
            )
        )
    elapsed = time.monotonic() - started

    assert exc_info.value.status_code == 504
    assert exc_info.value.detail == "Edge bootstrap request timed out"
    assert elapsed < 0.078


def test_connection_and_rotate_routes_forward_only_node_identifier() -> None:
    service = FakeBootstrapService()
    client = _client(service)

    test_response = client.post("/edge-nodes/node-7/test-connection")
    rotate_response = client.post("/edge-nodes/node-7/rotate-key")

    assert test_response.status_code == 200
    assert rotate_response.status_code == 200
    assert [call[0] for call in service.calls] == ["test_connection", "rotate_key"]
    for _, payload in service.calls:
        assert payload["node_id"] == "node-7"
        assert isinstance(payload["deadline"], float)


@pytest.mark.parametrize(
    ("path", "operation"),
    [
        ("/edge-nodes/node-7/test-connection", "test_connection"),
        ("/edge-nodes/node-7/rotate-key", "rotate_key"),
    ],
)
def test_node_operation_route_enforces_fifty_millisecond_ingress_deadline(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    operation: str,
) -> None:
    class SlowService(FakeBootstrapService):
        def __init__(self) -> None:
            super().__init__()
            self.entered: list[float] = []
            self.finished = threading.Event()

        def test_connection(self, **request: Any) -> dict[str, Any]:
            self.calls.append(("test_connection", request))
            self.entered.append(time.monotonic())
            try:
                time.sleep(0.08)
                return {"status": "ok", "node_id": request["node_id"]}
            finally:
                self.finished.set()

        def rotate_key(self, **request: Any) -> dict[str, Any]:
            self.calls.append(("rotate_key", request))
            self.entered.append(time.monotonic())
            try:
                time.sleep(0.08)
                return {"status": "ok", "node_id": request["node_id"]}
            finally:
                self.finished.set()

    monkeypatch.setattr(edge_ssh, "REQUEST_TIMEOUT_SECONDS", 0.05)
    service = SlowService()
    client = _client(service)
    started = time.monotonic()

    response = client.post(path)
    elapsed = time.monotonic() - started
    assert service.finished.wait(0.2)

    assert response.status_code == 504
    assert response.json() == {"detail": "Edge bootstrap request timed out"}
    assert elapsed < 0.094
    assert service.calls[0][0] == operation
    assert service.entered[0] < service.calls[0][1]["deadline"]
    assert service.calls[0][1]["deadline"] <= service.entered[0] + 0.05


def test_edge_ssh_background_calls_are_bounded_without_queued_work() -> None:
    started_calls = 0
    started_lock = threading.Lock()
    release = threading.Event()

    def blocking_call() -> dict[str, Any]:
        nonlocal started_calls
        with started_lock:
            started_calls += 1
        release.wait(1.0)
        return {"status": "ok", "node_id": "node-7"}

    async def run_calls() -> list[dict[str, Any] | HTTPException]:
        deadline = time.monotonic() + 1.0
        tasks = [
            asyncio.create_task(edge_ssh._invoke_with_deadline(blocking_call, deadline))
            for _ in range(5)
        ]
        await asyncio.sleep(0.05)
        release.set()
        return await asyncio.gather(*tasks, return_exceptions=True)

    results = asyncio.run(run_calls())

    overloads = [
        result
        for result in results
        if isinstance(result, HTTPException) and result.status_code == 503
    ]
    assert len(overloads) == 1
    assert overloads[0].detail == "Edge SSH service is busy"
    assert started_calls == 4


def test_edge_ssh_expired_background_deadline_returns_fixed_timeout() -> None:
    called = False

    def call() -> dict[str, Any]:
        nonlocal called
        called = True
        return {"status": "ok", "node_id": "node-7"}

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(edge_ssh._invoke_with_deadline(call, time.monotonic() - 1.0))

    assert exc_info.value.status_code == 504
    assert exc_info.value.detail == "Edge bootstrap request timed out"
    assert called is False


def test_api_openapi_exposes_exact_edge_ssh_routes() -> None:
    schema = create_app().openapi()
    edge_paths = sorted(path for path in schema["paths"] if path.startswith("/edge-nodes"))

    assert edge_paths == [
        "/edge-nodes/bootstrap",
        "/edge-nodes/scan-host-key",
        "/edge-nodes/{id}/rotate-key",
        "/edge-nodes/{id}/test-connection",
    ]
