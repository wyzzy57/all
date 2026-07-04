from __future__ import annotations

import json
from collections.abc import Generator
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from visiox_api.main import create_app
from visiox_api.routes.dataset_samples import get_dataset_sample_session, get_object_storage_client
from visiox_api.routes.datasets import get_dataset_session
from visiox_api.routes.label_projects import (
    get_label_project_session,
    get_label_studio_client,
    get_label_sync_stream_producer,
)
from visiox_api.routes.tasks import get_task_session
from visiox_common.settings import Settings, get_settings
from visiox_common.tasks import TaskStatus, TaskType
from visiox_db.models import Annotation, Dataset, DatasetSample, LabelProject, Task
from visiox_label_sync_worker.main import import_label_project_annotations, sync_label_project_samples
from visiox_storage.client import InMemoryObjectStorageClient


class FakeStreamProducer:
    def __init__(self) -> None:
        self.commands = []

    async def enqueue(self, command):
        self.commands.append(command)
        return "1-0"


class FakeLabelStudioClient:
    def __init__(self) -> None:
        self.created_projects: list[dict[str, object]] = []
        self.imported_tasks: list[dict[str, object]] = []
        self.export_payload: list[dict[str, object]] = []

    def create_project(self, title: str, label_config: str) -> dict[str, object]:
        self.created_projects.append({"title": title, "label_config": label_config})
        return {"id": 9001, "title": title}

    def get_project(self, project_id: str | int) -> dict[str, object]:
        return {"id": int(project_id), "title": "existing"}

    def import_tasks(self, project_id: str | int, tasks: list[dict[str, object]]) -> dict[str, object]:
        self.imported_tasks.append({"project_id": str(project_id), "tasks": tasks})
        return {"task_count": len(tasks)}

    def export_annotations(self, project_id: str | int) -> list[dict[str, object]]:
        del project_id
        return self.export_payload

    def close(self) -> None:
        pass


def test_mvp_labelstudio_project_sync_and_annotation_import(tmp_path):
    session_factory = _session_factory(tmp_path)
    storage = InMemoryObjectStorageClient()
    label_client = FakeLabelStudioClient()
    stream_producer = FakeStreamProducer()

    with _client(session_factory, storage, label_client, stream_producer) as client:
        dataset, sample_id, sample_uri = _create_dataset_with_sample(client)
        project = client.post(f"/datasets/{dataset['id']}/label-projects")
        sync_task = client.post(f"/label-projects/{project.json()['id']}/sync-samples")

        assert project.status_code == 201
        assert project.json()["external_project_id"] == "9001"
        assert sync_task.status_code == 201
        assert sync_task.json()["task_type"] == TaskType.SYNC_LABEL_STUDIO_DATA.value
        assert stream_producer.commands[-1].task_type == TaskType.SYNC_LABEL_STUDIO_DATA

        with session_factory() as session:
            sync_label_project_samples(session, label_client, sync_task.json()["id"], project.json()["id"])

        assert len(label_client.created_projects) == 1
        assert label_client.imported_tasks == [
            {
                "project_id": "9001",
                "tasks": [
                    {
                        "data": {
                            "image": sample_uri,
                            "visiox_dataset_id": dataset["id"],
                            "visiox_sample_id": sample_id,
                        }
                    }
                ],
            }
        ]

        import_task_id = _create_import_task(session_factory, project.json()["id"])
        label_client.export_payload = _label_studio_export(sample_id)
        with session_factory() as session:
            import_label_project_annotations(session, storage, label_client, import_task_id, project.json()["id"])

    with session_factory() as session:
        annotation = session.scalar(select(Annotation).join(DatasetSample).where(DatasetSample.id == sample_id))
        dataset_row = session.get(Dataset, dataset["id"])
        sample = session.get(DatasetSample, sample_id)
        project_row = session.get(LabelProject, project.json()["id"])
        import_task = session.get(Task, import_task_id)

    assert annotation is not None
    assert annotation.internal_payload["annotations"][0]["results"][0]["class_name"] == "defect"
    assert dataset_row.annotation_count == 1
    assert sample.annotation_status == "labeled"
    assert project_row.sync_status == "imported"
    assert import_task.status == TaskStatus.SUCCESS.value
    assert any(bucket == "label-studio" for bucket, _object_name in storage.objects)


def _session_factory(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'visiox-mvp-labelstudio.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@contextmanager
def _client(
    session_factory,
    storage: InMemoryObjectStorageClient,
    label_client: FakeLabelStudioClient,
    stream_producer: FakeStreamProducer,
) -> Generator[TestClient]:
    app = create_app()

    def override_session() -> Generator[Session]:
        with session_factory() as session:
            yield session

    for dependency in [
        get_dataset_session,
        get_dataset_sample_session,
        get_task_session,
        get_label_project_session,
    ]:
        app.dependency_overrides[dependency] = override_session
    app.dependency_overrides[get_object_storage_client] = lambda: storage
    app.dependency_overrides[get_label_studio_client] = lambda: label_client
    app.dependency_overrides[get_label_sync_stream_producer] = lambda: stream_producer
    app.dependency_overrides[get_settings] = lambda: Settings(label_studio_token="test-token")

    with TestClient(app) as test_client:
        yield test_client


def _create_dataset_with_sample(client: TestClient) -> tuple[dict[str, Any], str, str]:
    dataset = client.post(
        "/datasets",
        json={"name": "mvp-labelstudio-dataset", "task": "detect", "class_schema": {"names": ["ok", "defect"]}},
    ).json()
    upload = client.post(
        f"/datasets/{dataset['id']}/samples:upload",
        files={"file": ("part.png", _image_bytes(), "image/png")},
    )
    assert upload.status_code == 201
    sample = upload.json()["samples"][0]
    return dataset, sample["id"], sample["file_uri"]


def _create_import_task(session_factory, label_project_id: str) -> str:
    with session_factory() as session:
        project = session.get(LabelProject, label_project_id)
        task = Task(
            task_type=TaskType.IMPORT_LABEL_STUDIO_ANNOTATION.value,
            status=TaskStatus.QUEUED.value,
            progress=0,
            resource_type="label_project",
            resource_id=label_project_id,
            payload={"label_project_id": label_project_id},
        )
        session.add(task)
        project.sync_status = "synced"
        session.add(project)
        session.commit()
        return task.id


def _label_studio_export(sample_id: str) -> list[dict[str, object]]:
    payload = json.loads(Path("tests/fixtures/labelstudio/detect_export.json").read_text(encoding="utf-8"))
    payload[0]["data"]["visiox_sample_id"] = sample_id
    return payload


def _image_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (64, 48), color=(80, 40, 120)).save(buffer, format="PNG")
    return buffer.getvalue()
