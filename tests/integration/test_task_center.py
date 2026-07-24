from collections.abc import Generator

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from visiox_api.main import create_app
from visiox_api.routes.tasks import get_stream_producer, get_task_session, update_task_progress
from visiox_api.ws.tasks import get_progress_broker
from visiox_common.tasks import EDGE_EXECUTOR_TASK_TYPES, TaskProgressEvent, TaskStatus, TaskType
from visiox_db.models import Task
from visiox_messaging.pubsub import InMemoryTaskProgressBroker


class FakeStreamProducer:
    def __init__(self) -> None:
        self.commands = []

    async def enqueue(self, command):
        self.commands.append(command)
        return "1-0"


class FailingStreamProducer:
    async def enqueue(self, command):
        raise RuntimeError("redis unavailable")


@pytest.fixture()
def session_factory(tmp_path):
    database_path = tmp_path / "visiox-task-center.db"
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
def progress_broker() -> InMemoryTaskProgressBroker:
    return InMemoryTaskProgressBroker()


@pytest.fixture()
def client(
    session_factory,
    stream_producer: FakeStreamProducer,
    progress_broker: InMemoryTaskProgressBroker,
) -> Generator[TestClient]:
    app = create_app()

    def override_session() -> Generator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_task_session] = override_session
    app.dependency_overrides[get_stream_producer] = lambda: stream_producer
    app.dependency_overrides[get_progress_broker] = lambda: progress_broker

    with TestClient(app) as test_client:
        yield test_client


def test_post_tasks_persists_queued_task_and_enqueues_command(
    client: TestClient,
    session_factory,
    stream_producer: FakeStreamProducer,
):
    response = client.post(
        "/tasks",
        json={
            "task_type": "TRAIN_MODEL",
            "resource_refs": {"pipeline_id": "pipeline-1"},
            "payload": {"epochs": 1},
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["task_type"] == "TRAIN_MODEL"
    assert body["status"] == "QUEUED"
    assert body["progress"] == 0

    with session_factory() as session:
        task = session.get(Task, body["id"])

    assert task is not None
    assert task.status == "QUEUED"
    assert task.task_type == "TRAIN_MODEL"
    assert task.payload == {"epochs": 1}

    assert len(stream_producer.commands) == 1
    command = stream_producer.commands[0]
    assert command.task_id == body["id"]
    assert command.task_type == TaskType.TRAIN_MODEL
    assert command.resource_refs == {"pipeline_id": "pipeline-1"}
    assert command.payload_version == 1


def test_post_tasks_marks_task_failed_when_enqueue_fails(session_factory):
    app = create_app()

    def override_session() -> Generator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_task_session] = override_session
    app.dependency_overrides[get_stream_producer] = lambda: FailingStreamProducer()

    with TestClient(app) as client:
        response = client.post(
            "/tasks",
            json={
                "task_type": "TRAIN_MODEL",
                "resource_refs": {"pipeline_id": "pipeline-1"},
                "payload": {"epochs": 1},
            },
        )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "FAILED"
    assert body["error_code"] == "ENQUEUE_FAILED"
    assert "redis unavailable" in body["error_message"]
    assert body["finished_at"] is not None

    with session_factory() as session:
        saved = session.get(Task, body["id"])
        assert saved.status == "FAILED"
        assert saved.error_code == "ENQUEUE_FAILED"
        assert saved.finished_at is not None


def test_post_tasks_rejects_every_edge_task_type_without_persisting(
    client: TestClient,
    session_factory,
    stream_producer: FakeStreamProducer,
):
    valid_refs = {
        task_type: {"remote_execution_id": "exec-1"}
        for task_type in EDGE_EXECUTOR_TASK_TYPES
    }
    with session_factory() as session:
        before = session.scalar(select(func.count()).select_from(Task))

    assert set(valid_refs) == EDGE_EXECUTOR_TASK_TYPES
    for task_type, resource_refs in valid_refs.items():
        response = client.post(
            "/tasks",
            json={
                "task_type": task_type.value,
                "resource_refs": resource_refs,
                "payload": {},
            },
        )
        assert response.status_code == 422
        assert "dedicated" in response.json()["detail"].lower()

    with session_factory() as session:
        after = session.scalar(select(func.count()).select_from(Task))
    assert after == before
    assert stream_producer.commands == []


def test_get_tasks_and_get_task_read_persisted_tasks(
    client: TestClient,
    session_factory,
):
    with session_factory() as session:
        task = Task(task_type="TRAIN_MODEL", status="QUEUED", payload={"training_job_id": "job-1"})
        session.add(task)
        session.commit()
        task_id = task.id

    list_response = client.get("/tasks")
    detail_response = client.get(f"/tasks/{task_id}")

    assert list_response.status_code == 200
    assert [item["id"] for item in list_response.json()["items"]] == [task_id]
    assert detail_response.status_code == 200
    assert detail_response.json()["id"] == task_id
    assert detail_response.json()["task_type"] == "TRAIN_MODEL"


def test_task_center_lists_only_training_tasks(client: TestClient, session_factory):
    with session_factory() as session:
        training = Task(task_type="EDGE_TRAIN", status="SUCCESS", progress=100, payload={})
        dataset = Task(task_type="ANALYZE_DATASET", status="SUCCESS", progress=100, payload={})
        session.add_all([training, dataset])
        session.commit()

    response = client.get("/tasks")

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert [item["task_type"] for item in response.json()["items"]] == ["EDGE_TRAIN"]


def test_delete_terminal_training_task_and_reject_active_task(client: TestClient, session_factory):
    with session_factory() as session:
        completed = Task(task_type="TRAIN_MODEL", status="SUCCESS", progress=100, payload={})
        running = Task(task_type="EDGE_TRAIN", status="RUNNING", progress=50, payload={})
        session.add_all([completed, running])
        session.commit()
        completed_id = completed.id
        running_id = running.id

    assert client.delete(f"/tasks/{completed_id}").status_code == 204
    active_response = client.delete(f"/tasks/{running_id}")
    assert active_response.status_code == 409

    with session_factory() as session:
        assert session.get(Task, completed_id) is None
        assert session.get(Task, running_id) is not None


def test_cancel_queued_task_and_reject_finished_task(client: TestClient, session_factory):
    with session_factory() as session:
        queued = Task(task_type="TRAIN_MODEL", status="QUEUED", payload={})
        finished = Task(task_type="TRAIN_MODEL", status="SUCCESS", progress=100, payload={})
        session.add_all([queued, finished])
        session.commit()
        queued_id = queued.id
        finished_id = finished.id

    queued_response = client.post(f"/tasks/{queued_id}/cancel")
    finished_response = client.post(f"/tasks/{finished_id}/cancel")

    assert queued_response.status_code == 200
    assert queued_response.json()["status"] == "CANCELED"
    assert finished_response.status_code == 409

    with session_factory() as session:
        assert session.get(Task, queued_id).status == "CANCELED"
        assert session.get(Task, queued_id).finished_at is not None


def test_update_task_progress_persists_database_state_and_publishes_event(
    session_factory,
    progress_broker: InMemoryTaskProgressBroker,
):
    with session_factory() as session:
        task = Task(task_type="TRAIN_MODEL", status="QUEUED", payload={})
        session.add(task)
        session.commit()
        task_id = task.id

        event = TaskProgressEvent(
            task_id=task_id,
            status=TaskStatus.RUNNING,
            progress=25,
            stage="prepare",
            message="preparing dataset",
        )
        updated = update_task_progress(session, task_id, event, progress_broker)

        assert updated.status == "RUNNING"
        assert updated.progress == 25
        assert updated.stage == "prepare"

    with session_factory() as session:
        saved = session.get(Task, task_id)
        assert saved.status == "RUNNING"
        assert saved.progress == 25
        assert saved.stage == "prepare"

    assert progress_broker.published[-1].task_id == task_id


def test_update_task_progress_does_not_revive_canceled_task(
    session_factory,
    progress_broker: InMemoryTaskProgressBroker,
):
    with session_factory() as session:
        task = Task(task_type="TRAIN_MODEL", status="CANCELED", progress=10, payload={})
        session.add(task)
        session.commit()
        task_id = task.id

        updated = update_task_progress(
            session,
            task_id,
            TaskProgressEvent(
                task_id=task_id,
                status=TaskStatus.SUCCESS,
                progress=100,
                stage="complete",
            ),
            progress_broker,
        )

        assert updated.status == "CANCELED"
        assert updated.progress == 10
        assert updated.stage is None

    with session_factory() as session:
        saved = session.get(Task, task_id)
        assert saved.status == "CANCELED"
        assert saved.progress == 10
        assert saved.stage is None

    assert progress_broker.published == []


def test_task_progress_websocket_receives_matching_task_events(
    client: TestClient,
    session_factory,
    progress_broker: InMemoryTaskProgressBroker,
):
    with session_factory() as session:
        task = Task(task_type="TRAIN_MODEL", status="QUEUED", payload={})
        session.add(task)
        session.commit()
        task_id = task.id

    with client.websocket_connect(f"/ws/tasks/{task_id}") as websocket:
        progress_broker.publish(
            TaskProgressEvent(
                task_id=task_id,
                status=TaskStatus.RUNNING,
                progress=50,
                stage="train",
                message="halfway",
            )
        )

        assert websocket.receive_json() == {
            "task_id": task_id,
            "status": "RUNNING",
            "progress": 50,
            "stage": "train",
            "message": "halfway",
            "error_code": None,
            "error_message": None,
        }


def test_task_command_serializes_to_stream_fields():
    from visiox_common.tasks import TaskCommand

    command = TaskCommand(
        task_id="task-1",
        task_type=TaskType.TRAIN_MODEL,
        resource_refs={"training_job_id": "job-1"},
        payload={"count": 3},
    )

    assert command.to_stream_fields() == {
        "task_id": "task-1",
        "task_type": "TRAIN_MODEL",
        "resource_refs": '{"training_job_id":"job-1"}',
        "payload": '{"count":3}',
        "payload_version": "1",
    }


def test_progress_event_rejects_invalid_progress():
    with pytest.raises(ValueError):
        TaskProgressEvent(task_id="task-1", status=TaskStatus.RUNNING, progress=101)


def test_get_tasks_uses_limit_and_offset(client: TestClient, session_factory):
    with session_factory() as session:
        session.add_all(
            [
                Task(task_type="TRAIN_MODEL", status="QUEUED", payload={"index": index})
                for index in range(3)
            ]
        )
        session.commit()

    response = client.get("/tasks?limit=1&offset=1")

    assert response.status_code == 200
    assert len(response.json()["items"]) == 1
    assert response.json()["total"] == 3


def test_missing_task_returns_404(client: TestClient):
    assert client.get("/tasks/missing").status_code == 404
    assert client.post("/tasks/missing/cancel").status_code == 404
    assert client.delete("/tasks/missing").status_code == 404


def test_update_task_progress_sets_finished_at_for_terminal_status(session_factory):
    with session_factory() as session:
        task = Task(task_type="TRAIN_MODEL", status="RUNNING", payload={})
        session.add(task)
        session.commit()

        updated = update_task_progress(
            session,
            task.id,
            TaskProgressEvent(task_id=task.id, status=TaskStatus.SUCCESS, progress=100),
        )

        assert updated.finished_at is not None
        assert session.scalar(select(Task).where(Task.id == task.id)).status == "SUCCESS"


def test_app_lifespan_reuses_and_closes_redis_client(monkeypatch):
    from redis import asyncio as redis

    class FakeRedis:
        def __init__(self) -> None:
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True

    redis_clients: list[FakeRedis] = []

    def fake_from_url(url: str, decode_responses: bool):
        assert url == "redis://redis:6379/0"
        assert decode_responses is True
        client = FakeRedis()
        redis_clients.append(client)
        return client

    monkeypatch.setattr(redis, "from_url", fake_from_url)

    app = create_app()
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert len(redis_clients) == 1
        assert app.state.redis is redis_clients[0]

    assert redis_clients[0].closed is True
