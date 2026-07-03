from collections.abc import Generator

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from visiox_api.main import create_app
from visiox_api.routes.tasks import get_stream_producer, get_task_session, update_task_progress
from visiox_api.ws.tasks import get_progress_broker
from visiox_common.tasks import TaskProgressEvent, TaskStatus, TaskType
from visiox_db.models import Task
from visiox_messaging.pubsub import InMemoryTaskProgressBroker


class FakeStreamProducer:
    def __init__(self) -> None:
        self.commands = []

    async def enqueue(self, command):
        self.commands.append(command)
        return "1-0"


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


def test_get_tasks_and_get_task_read_persisted_tasks(
    client: TestClient,
    session_factory,
):
    with session_factory() as session:
        task = Task(task_type="DEPLOY_APP", status="QUEUED", payload={"target": "edge-1"})
        session.add(task)
        session.commit()
        task_id = task.id

    list_response = client.get("/tasks")
    detail_response = client.get(f"/tasks/{task_id}")

    assert list_response.status_code == 200
    assert [item["id"] for item in list_response.json()["items"]] == [task_id]
    assert detail_response.status_code == 200
    assert detail_response.json()["id"] == task_id
    assert detail_response.json()["task_type"] == "DEPLOY_APP"


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
        task_type=TaskType.CAPTURE_CAMERA_SAMPLE,
        resource_refs={"camera_id": "camera-1"},
        payload={"count": 3},
    )

    assert command.to_stream_fields() == {
        "task_id": "task-1",
        "task_type": "CAPTURE_CAMERA_SAMPLE",
        "resource_refs": '{"camera_id":"camera-1"}',
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
