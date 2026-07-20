from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from visiox_api.main import create_app
from visiox_api.routes.edge_ssh import get_edge_bootstrap_service
from visiox_db.base import Base
from visiox_db.models import NodeEvent, Task


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

    def scan_host_key(self, *, host: str, port: int) -> dict[str, Any]:
        self.calls.append(("scan_host_key", {"host": host, "port": port}))
        return {
            "status": "ok",
            "host_key_type": "ssh-ed25519",
            "fingerprint": "SHA256:confirmed-host-key",
        }

    def bootstrap(self, **request: Any) -> dict[str, Any]:
        self.calls.append(("bootstrap", request))
        return {"status": "ok", "node_id": "node-1"}

    def test_connection(self, *, node_id: str) -> dict[str, Any]:
        self.calls.append(("test_connection", {"node_id": node_id}))
        return {"status": "ok", "node_id": node_id}

    def rotate_key(self, *, node_id: str) -> dict[str, Any]:
        self.calls.append(("rotate_key", {"node_id": node_id}))
        return {"status": "ok", "node_id": node_id}


def _session_factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


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


def test_bootstrap_request_never_persists_password_to_task_event_or_response() -> None:
    service = FakeBootstrapService()
    client = _client(service)
    session_factory = _session_factory()

    response = client.post("/edge-nodes/bootstrap", json=BOOTSTRAP_REQUEST)

    assert response.status_code == 201
    assert response.json() == {"status": "ok", "node_id": "node-1"}
    assert "one-time-password" not in response.text
    assert service.calls[0][0] == "bootstrap"
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Task)) == 0
        assert session.scalar(select(func.count()).select_from(NodeEvent)) == 0


def test_connection_and_rotate_routes_forward_only_node_identifier() -> None:
    service = FakeBootstrapService()
    client = _client(service)

    test_response = client.post("/edge-nodes/node-7/test-connection")
    rotate_response = client.post("/edge-nodes/node-7/rotate-key")

    assert test_response.status_code == 200
    assert rotate_response.status_code == 200
    assert service.calls == [
        ("test_connection", {"node_id": "node-7"}),
        ("rotate_key", {"node_id": "node-7"}),
    ]


def test_api_openapi_exposes_exact_edge_ssh_routes() -> None:
    schema = create_app().openapi()
    edge_paths = sorted(path for path in schema["paths"] if path.startswith("/edge-nodes"))

    assert edge_paths == [
        "/edge-nodes/bootstrap",
        "/edge-nodes/scan-host-key",
        "/edge-nodes/{id}/rotate-key",
        "/edge-nodes/{id}/test-connection",
    ]
