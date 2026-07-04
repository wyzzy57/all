from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from visiox_api.main import create_app as create_api_app
from visiox_api.routes.deployments import get_deployment_session, get_deployment_stream_producer
from visiox_api.routes.devices import get_agent_client, get_device_session
from visiox_common.tasks import TaskStatus, TaskType
from visiox_db.models import Deployment, Device, EdgeApp, EdgeAppVersion, Task, TrainedModel
from visiox_edge_agent.main import create_app as create_edge_agent_app
from visiox_storage.client import InMemoryObjectStorageClient
from visiox_deployment_worker.dispatcher import DeploymentWorkerClients, dispatch_deployment_worker_task
from visiox_deployment_worker.main import run_deployment_task


class FakeStreamProducer:
    def __init__(self) -> None:
        self.commands = []

    async def enqueue(self, command):
        self.commands.append(command)
        return "1-0"


class FailingStreamProducer:
    async def enqueue(self, command):
        raise RuntimeError("redis unavailable")


class FakeAgentClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def health(self, endpoint_url: str) -> dict[str, Any]:
        self.calls.append(("health", endpoint_url, None))
        return {
            "service": "edge-agent",
            "status": "ok",
            "device": {"cpu": "test", "memory_mb": 1024},
        }

    def camera_test(self, endpoint_url: str, rtsp_url: str) -> dict[str, Any]:
        self.calls.append(("camera_test", endpoint_url, {"rtsp_url": rtsp_url}))
        return {"ok": True, "status": "reachable", "rtsp_url": rtsp_url}

    def deploy_app(self, endpoint_url: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("deploy", endpoint_url, payload))
        return {"success": True, "status": "deployed", "app": payload}

    def start_app(self, endpoint_url: str, app_id: str) -> dict[str, Any]:
        self.calls.append(("start", endpoint_url, {"app_id": app_id}))
        return {"success": True, "status": "running"}

    def stop_app(self, endpoint_url: str, app_id: str) -> dict[str, Any]:
        self.calls.append(("stop", endpoint_url, {"app_id": app_id}))
        return {"success": True, "status": "stopped"}

    def rollback_app(self, endpoint_url: str, app_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("rollback", endpoint_url, {"app_id": app_id, **payload}))
        return {"success": True, "status": "rolled_back", "app": {"app_id": app_id, "version": payload["version"]}}


class FailingAgentClient(FakeAgentClient):
    def deploy_app(self, endpoint_url: str, payload: dict[str, Any]) -> dict[str, Any]:
        del endpoint_url, payload
        raise RuntimeError("agent unavailable")


class UnsuccessfulAgentClient(FakeAgentClient):
    def deploy_app(self, endpoint_url: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("deploy", endpoint_url, payload))
        return {"success": False, "status": "deployed", "app": payload}


@pytest.fixture()
def session_factory(tmp_path):
    database_path = tmp_path / "visiox-deployment.db"
    database_url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture()
def stream_producer() -> FakeStreamProducer:
    return FakeStreamProducer()


@pytest.fixture()
def agent_client() -> FakeAgentClient:
    return FakeAgentClient()


@pytest.fixture()
def api_client(session_factory, stream_producer: FakeStreamProducer, agent_client: FakeAgentClient) -> Generator[TestClient]:
    app = create_api_app()

    def override_session() -> Generator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_device_session] = override_session
    app.dependency_overrides[get_deployment_session] = override_session
    app.dependency_overrides[get_deployment_stream_producer] = lambda: stream_producer
    app.dependency_overrides[get_agent_client] = lambda: agent_client

    with TestClient(app) as test_client:
        yield test_client


def seed_edge_app_version(
    session_factory,
    storage: InMemoryObjectStorageClient | None = None,
    tmp_path: Path | None = None,
    *,
    version_name: str = "v1",
    edge_app_id: str | None = None,
) -> str:
    with session_factory() as session:
        model = TrainedModel(
            name=f"trained-{version_name}",
            version=version_name,
            task="detect",
            artifact_uri="memory://models/trained/model/best.pt",
            status="ready",
        )
        app = session.get(EdgeApp, edge_app_id) if edge_app_id is not None else None
        if app is None:
            app = EdgeApp(name=f"edge-app-{version_name}", status="ready")
            session.add(app)
        session.add(model)
        session.flush()
        version = EdgeAppVersion(
            edge_app_id=app.id,
            trained_model_id=model.id,
            version=version_name,
            package_uri=f"memory://packages/edge-apps/{version_name}/package.tar.gz",
            manifest={"edge_app_id": app.id, "task": "detect"},
            checksum="abc",
            status="ready",
        )
        session.add(version)
        session.commit()
        version_id = version.id

    if storage is not None and tmp_path is not None:
        package_path = tmp_path / f"{version_name}.tar.gz"
        package_path.write_bytes(b"edge package")
        storage.put_file("packages", f"edge-apps/{version_name}/package.tar.gz", package_path)

    return version_id


def seed_edge_app_versions_pair(
    session_factory,
    storage: InMemoryObjectStorageClient | None = None,
    tmp_path: Path | None = None,
) -> tuple[str, str]:
    with session_factory() as session:
        app = EdgeApp(name="edge-app-pair", status="ready")
        session.add(app)
        session.commit()
        edge_app_id = app.id
    first = seed_edge_app_version(session_factory, storage, tmp_path, version_name="v1", edge_app_id=edge_app_id)
    second = seed_edge_app_version(session_factory, storage, tmp_path, version_name="v2", edge_app_id=edge_app_id)
    return first, second


def seed_device(session_factory) -> str:
    with session_factory() as session:
        device = Device(name="edge-1", endpoint_url="http://edge-agent.local", status="online", resource_info={})
        session.add(device)
        session.commit()
        return device.id


def create_deployment_rows(session_factory, storage: InMemoryObjectStorageClient, tmp_path: Path) -> tuple[str, str]:
    device_id = seed_device(session_factory)
    version_id = seed_edge_app_version(session_factory, storage, tmp_path)
    with session_factory() as session:
        deployment = Deployment(device_id=device_id, edge_app_version_id=version_id, status="pending", active=False)
        task = Task(
            task_type=TaskType.DEPLOY_APP.value,
            status=TaskStatus.QUEUED.value,
            progress=0,
            resource_type="deployment",
            payload={},
        )
        session.add_all([deployment, task])
        session.flush()
        deployment.task_id = task.id
        task.resource_id = deployment.id
        task.payload = {
            "action": "deploy",
            "deployment_id": deployment.id,
            "device_id": device_id,
            "edge_app_version_id": version_id,
        }
        session.commit()
        return task.id, deployment.id


def test_edge_agent_control_plane_apis():
    with TestClient(create_edge_agent_app()) as client:
        health = client.get("/health")
        info = client.get("/device/info")
        deploy = client.post(
            "/apps/deploy",
            json={"app_id": "app-1", "version": "v1", "package_uri": "memory://packages/app.tar.gz", "manifest": {"task": "detect"}},
        )
        start = client.post("/apps/app-1/start")
        stop = client.post("/apps/app-1/stop")
        client.post(
            "/apps/deploy",
            json={"app_id": "app-1", "version": "v2", "package_uri": "memory://packages/app-v2.tar.gz", "manifest": {"task": "detect"}},
        )
        rollback = client.post(
            "/apps/app-1/rollback",
            json={"version": "v1", "package_uri": "memory://packages/app.tar.gz", "manifest": {"task": "detect"}},
        )
        apps = client.get("/apps")
        camera = client.post("/camera/test", json={"rtsp_url": "rtsp://example"})

    assert health.json()["status"] == "ok"
    assert info.json()["capabilities"]["yolo_inference"] is False
    assert deploy.json()["status"] == "deployed"
    assert start.json()["status"] == "running"
    assert stop.json()["status"] == "stopped"
    assert rollback.json()["app"]["version"] == "v1"
    assert apps.json()["count"] == 1
    assert camera.json()["status"] == "reachable"


def test_devices_api_registers_checks_and_tests_camera(api_client: TestClient, agent_client: FakeAgentClient):
    response = api_client.post("/devices", json={"name": "edge-1", "endpoint_url": "http://edge-agent.local/"})
    device_id = response.json()["id"]
    check = api_client.post(f"/devices/{device_id}:check")
    camera = api_client.post(f"/devices/{device_id}/cameras", json={"name": "line-1", "rtsp_url": "rtsp://example"})
    camera_test = api_client.post(f"/cameras/{camera.json()['id']}:test")

    assert response.status_code == 201
    assert check.json()["status"] == "online"
    assert camera.status_code == 201
    assert camera_test.json()["status"] == "active"
    assert [call[0] for call in agent_client.calls] == ["health", "camera_test"]


def test_create_deployment_creates_task_and_enqueues_command(
    api_client: TestClient,
    session_factory,
    stream_producer: FakeStreamProducer,
):
    device_id = seed_device(session_factory)
    version_id = seed_edge_app_version(session_factory)

    response = api_client.post("/deployments", json={"device_id": device_id, "edge_app_version_id": version_id})

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending"
    assert body["task_id"]
    assert len(stream_producer.commands) == 1
    command = stream_producer.commands[0]
    assert command.task_type == TaskType.DEPLOY_APP
    assert command.resource_refs == {"deployment_id": body["id"]}
    assert command.payload["edge_app_version_id"] == version_id


def test_create_deployment_rejects_active_operation_on_same_device(api_client: TestClient, session_factory):
    device_id = seed_device(session_factory)
    first_version_id = seed_edge_app_version(session_factory, version_name="v1")
    second_version_id = seed_edge_app_version(session_factory, version_name="v2")
    first = api_client.post("/deployments", json={"device_id": device_id, "edge_app_version_id": first_version_id})
    second = api_client.post("/deployments", json={"device_id": device_id, "edge_app_version_id": second_version_id})

    assert first.status_code == 201
    assert second.status_code == 409


def test_stop_and_rollback_deployment_create_tasks(
    api_client: TestClient,
    session_factory,
    stream_producer: FakeStreamProducer,
):
    device_id = seed_device(session_factory)
    v1_id, v2_id = seed_edge_app_versions_pair(session_factory)
    with session_factory() as session:
        deployment = Deployment(device_id=device_id, edge_app_version_id=v2_id, status="running", active=True)
        session.add(deployment)
        session.commit()
        deployment_id = deployment.id

    stop = api_client.post(f"/deployments/{deployment_id}:stop")
    with session_factory() as session:
        deployment = session.get(Deployment, deployment_id)
        deployment.status = "running"
        deployment.active = True
        session.add(deployment)
        session.commit()
    rollback = api_client.post(f"/deployments/{deployment_id}:rollback", json={"target_edge_app_version_id": v1_id})

    assert stop.status_code == 200
    assert stop.json()["status"] == "stopping"
    assert rollback.status_code == 200
    assert rollback.json()["status"] == "rolling_back"
    assert [command.task_type for command in stream_producer.commands] == [TaskType.STOP_APP, TaskType.ROLLBACK_APP]


def test_rollback_rejects_target_from_different_edge_app(api_client: TestClient, session_factory):
    device_id = seed_device(session_factory)
    current_version_id = seed_edge_app_version(session_factory, version_name="current")
    target_version_id = seed_edge_app_version(session_factory, version_name="other")
    with session_factory() as session:
        deployment = Deployment(device_id=device_id, edge_app_version_id=current_version_id, status="running", active=True)
        session.add(deployment)
        session.commit()
        deployment_id = deployment.id

    response = api_client.post(f"/deployments/{deployment_id}:rollback", json={"target_edge_app_version_id": target_version_id})

    assert response.status_code == 409


@pytest.mark.parametrize("operation", ["stop", "rollback"])
def test_stop_and_rollback_reject_non_running_deployment(api_client: TestClient, session_factory, operation: str):
    device_id = seed_device(session_factory)
    current_version_id = seed_edge_app_version(session_factory, version_name="current")
    target_version_id = seed_edge_app_version(session_factory, version_name="target", edge_app_id=None)
    with session_factory() as session:
        deployment = Deployment(device_id=device_id, edge_app_version_id=current_version_id, status="pending", active=False)
        session.add(deployment)
        session.commit()
        deployment_id = deployment.id

    if operation == "stop":
        response = api_client.post(f"/deployments/{deployment_id}:stop")
    else:
        response = api_client.post(f"/deployments/{deployment_id}:rollback", json={"target_edge_app_version_id": target_version_id})

    assert response.status_code == 409


def test_create_deployment_marks_failed_when_enqueue_fails(session_factory):
    app = create_api_app()

    def override_session() -> Generator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_deployment_session] = override_session
    app.dependency_overrides[get_device_session] = override_session
    app.dependency_overrides[get_deployment_stream_producer] = lambda: FailingStreamProducer()
    with TestClient(app) as client:
        device_id = seed_device(session_factory)
        version_id = seed_edge_app_version(session_factory)
        response = client.post("/deployments", json={"device_id": device_id, "edge_app_version_id": version_id})

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "failed"
    with session_factory() as session:
        task = session.get(Task, body["task_id"])
        device = session.get(Device, device_id)

    assert task.status == TaskStatus.FAILED.value
    assert task.error_code == "ENQUEUE_FAILED"
    assert device.status == "error"


def test_deployment_worker_deploys_starts_and_records_logs(session_factory, tmp_path):
    storage = InMemoryObjectStorageClient()
    task_id, deployment_id = create_deployment_rows(session_factory, storage, tmp_path)
    agent = FakeAgentClient()

    with session_factory() as session:
        result = run_deployment_task(session, storage, agent, task_id, deployment_id, tmp_path / "work")

    assert result.status == "running"
    assert [call[0] for call in agent.calls] == ["deploy", "start"]
    with session_factory() as session:
        deployment = session.get(Deployment, deployment_id)
        task = session.get(Task, task_id)
        device = session.get(Device, deployment.device_id)

    assert deployment.status == "running"
    assert deployment.active is True
    assert deployment.logs_uri.startswith("memory://deployment-logs/")
    assert task.status == TaskStatus.SUCCESS.value
    assert device.status == "online"


def test_deployment_worker_marks_failed_when_agent_errors(session_factory, tmp_path):
    storage = InMemoryObjectStorageClient()
    task_id, deployment_id = create_deployment_rows(session_factory, storage, tmp_path)

    with session_factory() as session:
        with pytest.raises(RuntimeError, match="agent unavailable"):
            run_deployment_task(session, storage, FailingAgentClient(), task_id, deployment_id, tmp_path / "work")

    with session_factory() as session:
        deployment = session.get(Deployment, deployment_id)
        task = session.get(Task, task_id)
        device = session.get(Device, deployment.device_id)

    assert deployment.status == "failed"
    assert deployment.active is False
    assert task.status == TaskStatus.FAILED.value
    assert task.error_code == "DEPLOYMENT_FAILED"
    assert device.status == "error"


def test_deployment_worker_marks_failed_when_agent_returns_unsuccessful_response(session_factory, tmp_path):
    storage = InMemoryObjectStorageClient()
    task_id, deployment_id = create_deployment_rows(session_factory, storage, tmp_path)

    with session_factory() as session:
        with pytest.raises(RuntimeError, match="unsuccessful"):
            run_deployment_task(session, storage, UnsuccessfulAgentClient(), task_id, deployment_id, tmp_path / "work")

    with session_factory() as session:
        task = session.get(Task, task_id)

    assert task.status == TaskStatus.FAILED.value
    assert task.error_code == "DEPLOYMENT_FAILED"


def test_deployment_worker_stops_running_deployment(session_factory, tmp_path):
    storage = InMemoryObjectStorageClient()
    task_id, deployment_id = create_deployment_rows(session_factory, storage, tmp_path)
    agent = FakeAgentClient()
    with session_factory() as session:
        task = session.get(Task, task_id)
        deployment = session.get(Deployment, deployment_id)
        task.task_type = TaskType.STOP_APP.value
        task.payload = {
            "action": "stop",
            "deployment_id": deployment.id,
            "device_id": deployment.device_id,
        }
        deployment.status = "running"
        deployment.active = True
        session.add_all([task, deployment])
        session.commit()

    with session_factory() as session:
        result = run_deployment_task(session, storage, agent, task_id, deployment_id, tmp_path / "work")

    assert result.status == "stopped"
    assert [call[0] for call in agent.calls] == ["stop"]


def test_deployment_worker_rolls_back_to_same_edge_app_version(session_factory, tmp_path):
    storage = InMemoryObjectStorageClient()
    device_id = seed_device(session_factory)
    v1_id, v2_id = seed_edge_app_versions_pair(session_factory, storage, tmp_path)
    agent = FakeAgentClient()
    with session_factory() as session:
        deployment = Deployment(device_id=device_id, edge_app_version_id=v2_id, status="rolling_back", active=True)
        task = Task(
            task_type=TaskType.ROLLBACK_APP.value,
            status=TaskStatus.QUEUED.value,
            progress=0,
            resource_type="deployment",
            payload={},
        )
        session.add_all([deployment, task])
        session.flush()
        deployment.task_id = task.id
        task.resource_id = deployment.id
        task.payload = {
            "action": "rollback",
            "deployment_id": deployment.id,
            "device_id": device_id,
            "edge_app_version_id": v2_id,
            "target_edge_app_version_id": v1_id,
        }
        session.commit()
        deployment_id = deployment.id
        task_id = task.id

    with session_factory() as session:
        result = run_deployment_task(session, storage, agent, task_id, deployment_id, tmp_path / "work")

    assert result.status == "rolled_back"
    assert [call[0] for call in agent.calls] == ["rollback"]
    with session_factory() as session:
        deployment = session.get(Deployment, deployment_id)
    assert deployment.edge_app_version_id == v1_id


def test_deployment_worker_rejects_cross_app_rollback_payload(session_factory, tmp_path):
    storage = InMemoryObjectStorageClient()
    task_id, deployment_id = create_deployment_rows(session_factory, storage, tmp_path)
    target_version_id = seed_edge_app_version(session_factory, storage, tmp_path, version_name="other-app")
    with session_factory() as session:
        task = session.get(Task, task_id)
        deployment = session.get(Deployment, deployment_id)
        task.task_type = TaskType.ROLLBACK_APP.value
        task.payload = {
            "action": "rollback",
            "deployment_id": deployment.id,
            "device_id": deployment.device_id,
            "edge_app_version_id": deployment.edge_app_version_id,
            "target_edge_app_version_id": target_version_id,
        }
        deployment.status = "rolling_back"
        session.add_all([task, deployment])
        session.commit()

    with session_factory() as session:
        with pytest.raises(RuntimeError, match="does not match deployment edge app"):
            run_deployment_task(session, storage, FakeAgentClient(), task_id, deployment_id, tmp_path / "work")


def test_deployment_worker_rejects_payload_mismatch(session_factory, tmp_path):
    storage = InMemoryObjectStorageClient()
    task_id, deployment_id = create_deployment_rows(session_factory, storage, tmp_path)
    with session_factory() as session:
        task = session.get(Task, task_id)
        task.payload = {**task.payload, "device_id": "wrong-device"}
        session.add(task)
        session.commit()

    with session_factory() as session:
        with pytest.raises(RuntimeError, match="device_id"):
            run_deployment_task(session, storage, FakeAgentClient(), task_id, deployment_id, tmp_path / "work")

    with session_factory() as session:
        task = session.get(Task, task_id)

    assert task.status == TaskStatus.FAILED.value
    assert task.error_code == "INVALID_DEPLOYMENT_PAYLOAD"


def test_deployment_worker_does_not_run_already_claimed_task(session_factory, tmp_path):
    storage = InMemoryObjectStorageClient()
    task_id, deployment_id = create_deployment_rows(session_factory, storage, tmp_path)
    agent = FakeAgentClient()
    with session_factory() as session:
        task = session.get(Task, task_id)
        task.status = TaskStatus.RUNNING.value
        session.add(task)
        session.commit()

    with session_factory() as session:
        with pytest.raises(RuntimeError, match="already claimed"):
            run_deployment_task(session, storage, agent, task_id, deployment_id, tmp_path / "work")

    assert agent.calls == []


def test_deployment_worker_reclaims_stale_running_task(session_factory, tmp_path):
    storage = InMemoryObjectStorageClient()
    task_id, deployment_id = create_deployment_rows(session_factory, storage, tmp_path)
    agent = FakeAgentClient()
    with session_factory() as session:
        task = session.get(Task, task_id)
        task.status = TaskStatus.RUNNING.value
        task.started_at = datetime.now(UTC) - timedelta(hours=2)
        session.add(task)
        session.commit()

    with session_factory() as session:
        result = run_deployment_task(
            session,
            storage,
            agent,
            task_id,
            deployment_id,
            tmp_path / "work",
            stale_after_seconds=60,
        )

    assert result.status == "running"
    assert [call[0] for call in agent.calls] == ["deploy", "start"]


def test_deployment_worker_dispatcher_routes_tasks(session_factory, tmp_path):
    storage = InMemoryObjectStorageClient()
    task_id, deployment_id = create_deployment_rows(session_factory, storage, tmp_path)
    agent = FakeAgentClient()

    with session_factory() as session:
        result = dispatch_deployment_worker_task(
            session,
            storage,
            DeploymentWorkerClients(agent=agent),
            task_id=task_id,
            task_type=TaskType.DEPLOY_APP.value,
            resource_refs={"deployment_id": deployment_id},
            work_dir=tmp_path / "dispatch",
        )

    assert result.status == "running"
