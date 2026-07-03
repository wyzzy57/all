from collections.abc import Generator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from visiox_api.main import create_app
from visiox_api.routes.base_models import (
    ensure_base_model_ready,
    get_base_model_session,
    get_stream_producer,
)
from visiox_common.tasks import TaskType
from visiox_db.models import BaseModel, ModelSource, Task
from visiox_model_worker.main import BaseModelDownloadError, download_base_model
from visiox_storage.checksum import sha256_bytes
from visiox_storage.client import InMemoryObjectStorageClient, MinioObjectStorageClient


class FakeStreamProducer:
    def __init__(self) -> None:
        self.commands = []

    async def enqueue(self, command):
        self.commands.append(command)
        return "1-0"


@pytest.fixture()
def session_factory(tmp_path):
    database_path = tmp_path / "visiox-base-models.db"
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
def client(session_factory, stream_producer: FakeStreamProducer) -> Generator[TestClient]:
    app = create_app()

    def override_session() -> Generator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_base_model_session] = override_session
    app.dependency_overrides[get_stream_producer] = lambda: stream_producer

    with TestClient(app) as test_client:
        yield test_client


def seed_source_and_models(session: Session, source_dir: Path | None = None) -> tuple[ModelSource, list[BaseModel]]:
    source = ModelSource(
        name="local-fixtures",
        type="local_mount",
        mount_path=str(source_dir) if source_dir else None,
        enabled=True,
    )
    session.add(source)
    session.flush()
    models = [
        BaseModel(
            family="yolo26",
            task="detect",
            scale="n",
            filename="yolo26n.pt",
            source_path="yolo26n.pt",
            status="remote_available",
            model_source_id=source.id,
        ),
        BaseModel(
            family="yolo26",
            task="segment",
            scale="s",
            filename="yolo26s-seg.pt",
            source_path="yolo26s-seg.pt",
            status="ready",
            local_uri="memory://models/base/ready/yolo26s-seg.pt",
            checksum=sha256_bytes(b"ready"),
            size_bytes=5,
            model_source_id=source.id,
        ),
    ]
    session.add_all(models)
    session.commit()
    return source, models


def test_list_base_models_supports_task_and_status_filters(client: TestClient, session_factory):
    with session_factory() as session:
        _, models = seed_source_and_models(session)
        detect_id = models[0].id

    response = client.get("/base-models?task=detect&status=remote_available")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["limit"] == 50
    assert body["offset"] == 0
    assert [item["id"] for item in body["items"]] == [detect_id]
    assert body["items"][0]["task"] == "detect"
    assert body["items"][0]["status"] == "remote_available"


def test_get_base_model_returns_detail_and_404(client: TestClient, session_factory):
    with session_factory() as session:
        _, models = seed_source_and_models(session)
        model_id = models[1].id

    response = client.get(f"/base-models/{model_id}")
    missing_response = client.get("/base-models/missing")

    assert response.status_code == 200
    assert response.json()["id"] == model_id
    assert response.json()["local_uri"] == "memory://models/base/ready/yolo26s-seg.pt"
    assert missing_response.status_code == 404


def test_post_base_model_download_creates_task_and_enqueues_command(
    client: TestClient,
    session_factory,
    stream_producer: FakeStreamProducer,
):
    with session_factory() as session:
        _, models = seed_source_and_models(session)
        model_id = models[0].id

    response = client.post(f"/base-models/{model_id}/download")

    assert response.status_code == 201
    body = response.json()
    assert body["base_model_id"] == model_id
    assert body["status"] == "downloading"
    assert body["task_id"]

    with session_factory() as session:
        task = session.get(Task, body["task_id"])
        model = session.get(BaseModel, model_id)

    assert task is not None
    assert task.task_type == "DOWNLOAD_BASE_MODEL"
    assert task.status == "QUEUED"
    assert task.resource_type == "base_model"
    assert task.resource_id == model_id
    assert task.payload == {"base_model_id": model_id}
    assert model.status == "downloading"

    assert len(stream_producer.commands) == 1
    command = stream_producer.commands[0]
    assert command.task_id == body["task_id"]
    assert command.task_type == TaskType.DOWNLOAD_BASE_MODEL
    assert command.resource_refs == {"base_model_id": model_id}


def test_post_base_model_download_returns_ready_model_without_task(
    client: TestClient,
    session_factory,
    stream_producer: FakeStreamProducer,
):
    with session_factory() as session:
        _, models = seed_source_and_models(session)
        model_id = models[1].id

    response = client.post(f"/base-models/{model_id}/download")

    assert response.status_code == 200
    assert response.json()["base_model_id"] == model_id
    assert response.json()["status"] == "ready"
    assert response.json()["task_id"] is None
    assert stream_producer.commands == []


def test_post_base_model_download_is_idempotent_while_downloading(
    client: TestClient,
    session_factory,
    stream_producer: FakeStreamProducer,
):
    with session_factory() as session:
        _, models = seed_source_and_models(session)
        model_id = models[0].id

    first_response = client.post(f"/base-models/{model_id}/download")
    second_response = client.post(f"/base-models/{model_id}/download")

    assert first_response.status_code == 201
    assert second_response.status_code == 200
    first_body = first_response.json()
    second_body = second_response.json()
    assert second_body["status"] == "downloading"
    assert second_body["task_id"] == first_body["task_id"]

    with session_factory() as session:
        tasks = session.scalars(select(Task).where(Task.resource_id == model_id)).all()

    assert len(tasks) == 1
    assert len(stream_producer.commands) == 1


def test_ensure_base_model_ready_returns_ready_model_and_rejects_other_statuses(session_factory):
    with session_factory() as session:
        _, models = seed_source_and_models(session)
        remote_id = models[0].id
        ready_id = models[1].id

        ready = ensure_base_model_ready(session, ready_id)

        assert ready.id == ready_id

        with pytest.raises(HTTPException) as not_ready_error:
            ensure_base_model_ready(session, remote_id)
        assert not_ready_error.value.status_code == 409

        with pytest.raises(HTTPException) as missing_error:
            ensure_base_model_ready(session, "missing")
        assert missing_error.value.status_code == 404


def test_download_base_model_copies_local_mount_to_storage_and_marks_ready(tmp_path, session_factory):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    model_bytes = b"downloaded model bytes"
    (source_dir / "yolo26n.pt").write_bytes(model_bytes)

    with session_factory() as session:
        _, models = seed_source_and_models(session, source_dir)
        model_id = models[0].id
        task = Task(
            task_type="DOWNLOAD_BASE_MODEL",
            status="QUEUED",
            resource_type="base_model",
            resource_id=model_id,
            payload={"base_model_id": model_id},
        )
        session.add(task)
        session.commit()
        task_id = task.id

        storage = InMemoryObjectStorageClient()
        result = download_base_model(session, task_id, model_id, storage)

        assert result.base_model_id == model_id
        assert result.local_uri == f"memory://models/base/{model_id}/yolo26n.pt"
        assert result.checksum == sha256_bytes(model_bytes)
        assert result.size_bytes == len(model_bytes)
        assert storage.objects[("models", f"base/{model_id}/yolo26n.pt")] == model_bytes

    with session_factory() as session:
        saved_model = session.get(BaseModel, model_id)
        saved_task = session.get(Task, task_id)

    assert saved_model.status == "ready"
    assert saved_model.local_uri == f"memory://models/base/{model_id}/yolo26n.pt"
    assert saved_model.checksum == sha256_bytes(model_bytes)
    assert saved_model.size_bytes == len(model_bytes)
    assert saved_task.status == "SUCCESS"
    assert saved_task.progress == 100


def test_download_base_model_checksum_mismatch_marks_model_and_task_failed(
    tmp_path,
    session_factory,
):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "yolo26n.pt").write_bytes(b"unexpected")

    with session_factory() as session:
        _, models = seed_source_and_models(session, source_dir)
        model_id = models[0].id
        model = session.get(BaseModel, model_id)
        model.checksum = sha256_bytes(b"expected")
        task = Task(
            task_type="DOWNLOAD_BASE_MODEL",
            status="QUEUED",
            resource_type="base_model",
            resource_id=model_id,
            payload={"base_model_id": model_id},
        )
        session.add(task)
        session.commit()
        task_id = task.id

        storage = InMemoryObjectStorageClient()

        with pytest.raises(Exception, match="checksum"):
            download_base_model(session, task_id, model_id, storage)

        assert storage.objects == {}

    with session_factory() as session:
        saved_model = session.get(BaseModel, model_id)
        saved_task = session.scalar(select(Task).where(Task.id == task_id))

    assert saved_model.status == "failed"
    assert saved_model.local_uri is None
    assert saved_task.status == "FAILED"
    assert saved_task.error_code == "BASE_MODEL_DOWNLOAD_FAILED"
    assert "expected" in saved_task.error_message
    assert "actual" in saved_task.error_message
    assert saved_task.retryable is True


def test_download_base_model_rejects_task_for_different_base_model(tmp_path, session_factory):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "yolo26n.pt").write_bytes(b"model")

    with session_factory() as session:
        _, models = seed_source_and_models(session, source_dir)
        model_id = models[0].id
        task = Task(
            task_type="DOWNLOAD_BASE_MODEL",
            status="QUEUED",
            resource_type="base_model",
            resource_id="other-model",
            payload={"base_model_id": "other-model"},
        )
        session.add(task)
        session.commit()
        task_id = task.id

        with pytest.raises(BaseModelDownloadError, match="does not match"):
            download_base_model(session, task_id, model_id, InMemoryObjectStorageClient())

    with session_factory() as session:
        assert session.get(BaseModel, model_id).status == "remote_available"


def test_download_base_model_does_not_rerun_terminal_task_or_fail_model(tmp_path, session_factory):
    source_dir = tmp_path / "source"
    source_dir.mkdir()

    with session_factory() as session:
        _, models = seed_source_and_models(session, source_dir)
        model_id = models[0].id
        task = Task(
            task_type="DOWNLOAD_BASE_MODEL",
            status="SUCCESS",
            progress=100,
            resource_type="base_model",
            resource_id=model_id,
            payload={"base_model_id": model_id},
        )
        session.add(task)
        session.commit()
        task_id = task.id

        with pytest.raises(BaseModelDownloadError, match="terminal"):
            download_base_model(session, task_id, model_id, InMemoryObjectStorageClient())

    with session_factory() as session:
        model = session.get(BaseModel, model_id)
        saved_task = session.get(Task, task_id)

    assert model.status == "remote_available"
    assert saved_task.status == "SUCCESS"


def test_download_base_model_ready_model_marks_task_success_without_redownloading(
    session_factory,
):
    with session_factory() as session:
        _, models = seed_source_and_models(session)
        model_id = models[1].id
        task = Task(
            task_type="DOWNLOAD_BASE_MODEL",
            status="QUEUED",
            resource_type="base_model",
            resource_id=model_id,
            payload={"base_model_id": model_id},
        )
        session.add(task)
        session.commit()
        task_id = task.id
        storage = InMemoryObjectStorageClient()

        result = download_base_model(session, task_id, model_id, storage)

        assert result.local_uri == "memory://models/base/ready/yolo26s-seg.pt"
        assert storage.objects == {}

    with session_factory() as session:
        saved_task = session.get(Task, task_id)
        saved_model = session.get(BaseModel, model_id)

    assert saved_task.status == "SUCCESS"
    assert saved_task.progress == 100
    assert saved_model.status == "ready"


def test_download_base_model_ready_model_keeps_success_terminal_task(session_factory):
    with session_factory() as session:
        _, models = seed_source_and_models(session)
        model_id = models[1].id
        task = Task(
            task_type="DOWNLOAD_BASE_MODEL",
            status="SUCCESS",
            progress=100,
            resource_type="base_model",
            resource_id=model_id,
            payload={"base_model_id": model_id},
        )
        session.add(task)
        session.commit()
        task_id = task.id
        storage = InMemoryObjectStorageClient()

        result = download_base_model(session, task_id, model_id, storage)

        assert result.local_uri == "memory://models/base/ready/yolo26s-seg.pt"
        assert storage.objects == {}

    with session_factory() as session:
        saved_task = session.get(Task, task_id)
        saved_model = session.get(BaseModel, model_id)

    assert saved_task.status == "SUCCESS"
    assert saved_model.status == "ready"


@pytest.mark.parametrize("unsafe_source_path", [r"..\outside.pt", "../outside.pt"])
def test_download_base_model_rejects_local_mount_path_traversal(
    tmp_path,
    session_factory,
    unsafe_source_path: str,
):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (tmp_path / "outside.pt").write_bytes(b"outside")

    with session_factory() as session:
        _, models = seed_source_and_models(session, source_dir)
        model_id = models[0].id
        model = session.get(BaseModel, model_id)
        model.source_path = unsafe_source_path
        task = Task(
            task_type="DOWNLOAD_BASE_MODEL",
            status="QUEUED",
            resource_type="base_model",
            resource_id=model_id,
            payload={"base_model_id": model_id},
        )
        session.add(task)
        session.commit()

        with pytest.raises(BaseModelDownloadError, match="unsafe"):
            download_base_model(session, task.id, model_id, InMemoryObjectStorageClient())


@pytest.mark.parametrize(
    "unsafe_source_path",
    ["https://example.com/model.pt", "//example.com/model.pt", "../model.pt"],
)
def test_download_base_model_rejects_unsafe_http_source_paths(
    session_factory,
    unsafe_source_path: str,
):
    with session_factory() as session:
        source = ModelSource(
            name=f"http-{unsafe_source_path}",
            type="http",
            base_url="https://models.internal/base/",
            enabled=True,
        )
        session.add(source)
        session.flush()
        model = BaseModel(
            family="yolo26",
            task="detect",
            scale="x",
            filename="unsafe.pt",
            source_path=unsafe_source_path,
            status="remote_available",
            model_source_id=source.id,
        )
        task = Task(
            task_type="DOWNLOAD_BASE_MODEL",
            status="QUEUED",
            resource_type="base_model",
            payload={},
        )
        session.add_all([model, task])
        session.flush()
        task.resource_id = model.id
        task.payload = {"base_model_id": model.id}
        session.commit()

        with pytest.raises(BaseModelDownloadError, match="unsafe"):
            download_base_model(session, task.id, model.id, InMemoryObjectStorageClient())


def test_minio_put_file_treats_bucket_already_exists_race_as_success(tmp_path):
    from minio.error import S3Error

    class FakeMinio:
        def __init__(self) -> None:
            self.puts = []

        def bucket_exists(self, bucket: str) -> bool:
            assert bucket == "models"
            return False

        def make_bucket(self, bucket: str) -> None:
            raise S3Error(
                None,
                "BucketAlreadyOwnedByYou",
                "bucket exists",
                bucket,
                "request-id",
                "host-id",
                bucket,
            )

        def fput_object(
            self,
            bucket: str,
            object_name: str,
            path: str,
            content_type: str | None = None,
        ) -> None:
            self.puts.append((bucket, object_name, path, content_type))

    model_file = tmp_path / "model.pt"
    model_file.write_bytes(b"model")
    client = MinioObjectStorageClient.__new__(MinioObjectStorageClient)
    client._client = FakeMinio()

    uri = client.put_file("models", "base/model.pt", model_file)

    assert uri == "minio://models/base/model.pt"
    assert client._client.puts == [("models", "base/model.pt", str(model_file), None)]
