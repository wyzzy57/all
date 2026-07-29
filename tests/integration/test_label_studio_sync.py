from collections.abc import Generator
import json
from types import SimpleNamespace

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from visiox_api.main import create_app
from visiox_api.dependencies.auth import get_current_user
from visiox_api.routes.datasets import get_dataset_session
from visiox_api.routes.label_projects import (
    get_label_project_session,
    get_label_studio_client,
    get_label_sync_stream_producer,
)
from visiox_common.settings import Settings, get_settings
from visiox_common.tasks import TaskStatus, TaskType
from visiox_db.models import Annotation, Dataset, DatasetSample, LabelProject, Task
from visiox_storage.client import InMemoryObjectStorageClient
from visiox_yolo26.labelstudio.client import LabelStudioClient, LabelStudioError
from visiox_yolo26.labelstudio.importer import normalize_label_studio_task
from visiox_yolo26.labelstudio.templates import build_label_config
from visiox_label_sync_worker.main import import_label_project_annotations, sync_label_project_samples
from visiox_label_sync_worker.runner import run_pending_label_sync_tasks
from tests.integration.ownership_test_support import install_legacy_ownership


LEGACY_TEST_ACTOR = SimpleNamespace(id="legacy-admin", organization_id="legacy-org", role="admin")


class FakeStreamProducer:
    def __init__(self) -> None:
        self.commands = []

    async def enqueue(self, command):
        self.commands.append(command)
        return "1-0"


class FailingStreamProducer:
    async def enqueue(self, command):
        raise RuntimeError("redis unavailable")


class FakeLabelStudioClient:
    def __init__(self) -> None:
        self.created_projects = []
        self.imported_tasks = []
        self.export_payload = []

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


class FailingLabelStudioClient(FakeLabelStudioClient):
    def import_tasks(self, project_id: str | int, tasks: list[dict[str, object]]) -> dict[str, object]:
        del project_id, tasks
        raise LabelStudioError("Label Studio request failed: 500 server error")


class UnavailableLabelStudioClient(FakeLabelStudioClient):
    def create_project(self, title: str, label_config: str) -> dict[str, object]:
        del title, label_config
        raise LabelStudioError("Label Studio service unavailable: connection refused")


@pytest.fixture()
def session_factory(tmp_path):
    database_path = tmp_path / "visiox-label-studio.db"
    database_url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    install_legacy_ownership(factory)
    return factory


@pytest.fixture()
def label_client() -> FakeLabelStudioClient:
    return FakeLabelStudioClient()


@pytest.fixture()
def stream_producer() -> FakeStreamProducer:
    return FakeStreamProducer()


@pytest.fixture()
def client(
    session_factory,
    label_client: FakeLabelStudioClient,
    stream_producer: FakeStreamProducer,
) -> Generator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: LEGACY_TEST_ACTOR

    def override_session() -> Generator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_dataset_session] = override_session
    app.dependency_overrides[get_label_project_session] = override_session
    app.dependency_overrides[get_label_studio_client] = lambda: label_client
    app.dependency_overrides[get_label_sync_stream_producer] = lambda: stream_producer
    app.dependency_overrides[get_settings] = lambda: Settings(
        label_studio_token="test-token",
        label_studio_public_url="",
    )

    with TestClient(app) as test_client:
        yield test_client


def create_dataset_row(session_factory, task: str = "detect") -> str:
    with session_factory() as session:
        dataset = Dataset(
            name=f"{task}-dataset",
            task=task,
            status="created",
            class_schema={"names": ["ok", "defect"]},
            source="upload",
        )
        session.add(dataset)
        session.commit()
        return dataset.id


def create_dataset_with_sample(session_factory) -> tuple[str, str]:
    with session_factory() as session:
        dataset = Dataset(
            name="sync-dataset",
            task="detect",
            status="created",
            class_schema={"names": ["ok", "defect"]},
            sample_count=1,
            source="upload",
        )
        session.add(dataset)
        session.flush()
        sample = DatasetSample(
            dataset_id=dataset.id,
            file_uri="memory://datasets/sample-one.png",
            width=640,
            height=480,
            checksum="sample-one",
            split="train",
            annotation_status="unlabeled",
        )
        session.add(sample)
        session.commit()
        return dataset.id, sample.id


@pytest.mark.parametrize(
    ("task", "expected"),
    [
        ("detect", "RectangleLabels"),
        ("segment", "PolygonLabels"),
        ("semantic", "BrushLabels"),
        ("pose", "KeyPointLabels"),
        ("obb", "PolygonLabels"),
        ("classify", "Choices"),
    ],
)
def test_label_config_templates_cover_supported_tasks(task: str, expected: str):
    config = build_label_config(task, {"names": ["ok", "defect"]})

    assert expected in config
    assert 'value="ok"' in config
    assert 'value="defect"' in config


def test_label_studio_client_uses_token_and_raises_on_http_errors():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/projects":
            assert request.headers["Authorization"] == "Token secret"
            return httpx.Response(201, json={"id": 12})
        return httpx.Response(500, json={"detail": "boom"})

    client = LabelStudioClient(
        base_url="http://label-studio.local/",
        token="secret",
        transport=httpx.MockTransport(handler),
    )

    assert client.create_project("dataset", "<View />") == {"id": 12}
    with pytest.raises(LabelStudioError, match="500"):
        client.get_project(12)
    client.close()


def test_label_studio_client_supports_context_manager():
    closed = False

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"id": 12})

    with LabelStudioClient(
        base_url="http://label-studio.local/",
        token="secret",
        transport=httpx.MockTransport(handler),
    ) as client:
        assert client.get_project(12) == {"id": 12}
        original_close = client.close

        def close_once() -> None:
            nonlocal closed
            closed = True
            original_close()

        client.close = close_once

    assert closed is True


def test_create_label_project_calls_label_studio_and_is_idempotent(
    client: TestClient,
    session_factory,
    label_client: FakeLabelStudioClient,
):
    dataset_id = create_dataset_row(session_factory)

    first = client.post(f"/datasets/{dataset_id}/label-projects")
    second = client.post(f"/datasets/{dataset_id}/label-projects")
    list_response = client.get(f"/datasets/{dataset_id}/label-projects")

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["provider"] == "label_studio"
    assert first.json()["external_project_id"] == "9001"
    assert first.json()["project_url"] == "http://label-studio:8080/projects/9001/data"
    assert first.json()["sync_status"] == "pending"
    assert list_response.json()["total"] == 1
    assert list_response.json()["items"][0]["project_url"] == "http://label-studio:8080/projects/9001/data"
    assert len(label_client.created_projects) == 1

    with session_factory() as session:
        saved = session.scalar(select(LabelProject).where(LabelProject.dataset_id == dataset_id))
        assert saved.provider == "label_studio"


def test_link_existing_label_project_does_not_create_external_project(
    client: TestClient,
    session_factory,
    label_client: FakeLabelStudioClient,
):
    dataset_id = create_dataset_row(session_factory)

    response = client.post(
        f"/datasets/{dataset_id}/label-projects",
        json={"external_project_id": "12345"},
    )

    assert response.status_code == 201
    assert response.json()["external_project_id"] == "12345"
    assert response.json()["project_url"] == "http://label-studio:8080/projects/12345/data"
    assert label_client.created_projects == []


def test_label_project_response_uses_public_url_when_configured(
    session_factory,
    label_client: FakeLabelStudioClient,
    stream_producer: FakeStreamProducer,
):
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: LEGACY_TEST_ACTOR

    def override_session() -> Generator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_dataset_session] = override_session
    app.dependency_overrides[get_label_project_session] = override_session
    app.dependency_overrides[get_label_studio_client] = lambda: label_client
    app.dependency_overrides[get_label_sync_stream_producer] = lambda: stream_producer
    app.dependency_overrides[get_settings] = lambda: Settings(
        label_studio_token="test-token",
        label_studio_public_url="http://127.0.0.1:8080",
    )

    with TestClient(app) as test_client:
        dataset_id = create_dataset_row(session_factory)
        response = test_client.post(f"/datasets/{dataset_id}/label-projects")

    assert response.status_code == 201
    assert response.json()["project_url"] == "http://127.0.0.1:8080/visiox-auth?next=/projects/9001/data"


def test_link_existing_label_project_rejects_unsafe_external_project_id(
    client: TestClient,
    session_factory,
    label_client: FakeLabelStudioClient,
):
    dataset_id = create_dataset_row(session_factory)

    response = client.post(
        f"/datasets/{dataset_id}/label-projects",
        json={"external_project_id": "123/../../bad"},
    )

    assert response.status_code == 422
    assert label_client.created_projects == []


def test_create_label_project_returns_503_when_label_studio_is_unavailable(
    client: TestClient,
    session_factory,
):
    dataset_id = create_dataset_row(session_factory)
    client.app.dependency_overrides[get_label_studio_client] = lambda: UnavailableLabelStudioClient()

    response = client.post(f"/datasets/{dataset_id}/label-projects")

    assert response.status_code == 503
    assert "Label Studio 服务不可用" in response.json()["detail"]


def test_sync_samples_endpoint_creates_task_and_enqueues_command(
    client: TestClient,
    session_factory,
    stream_producer: FakeStreamProducer,
):
    dataset_id = create_dataset_row(session_factory)
    project = client.post(f"/datasets/{dataset_id}/label-projects").json()

    response = client.post(f"/label-projects/{project['id']}/sync-samples")

    assert response.status_code == 201
    body = response.json()
    assert body["task_type"] == TaskType.SYNC_LABEL_STUDIO_DATA.value
    assert body["status"] == TaskStatus.QUEUED.value
    assert body["payload"] == {"dataset_id": dataset_id, "label_project_id": project["id"]}
    assert len(stream_producer.commands) == 1
    assert stream_producer.commands[0].task_type == TaskType.SYNC_LABEL_STUDIO_DATA


def test_sync_samples_endpoint_rejects_user_without_dataset_edit_permission(
    client: TestClient,
    session_factory,
    stream_producer: FakeStreamProducer,
):
    dataset_id = create_dataset_row(session_factory)
    project = client.post(f"/datasets/{dataset_id}/label-projects").json()
    stream_producer.commands.clear()
    client.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id="other-user",
        organization_id="legacy-org",
        role="member",
    )

    response = client.post(f"/label-projects/{project['id']}/sync-samples")

    assert response.status_code == 403
    assert stream_producer.commands == []


def test_database_rejects_duplicate_label_project_for_dataset_provider(session_factory):
    dataset_id = create_dataset_row(session_factory)
    with session_factory() as session:
        session.add_all(
            [
                LabelProject(
                    dataset_id=dataset_id,
                    provider="label_studio",
                    external_project_id="1",
                    sync_status="pending",
                ),
                LabelProject(
                    dataset_id=dataset_id,
                    provider="label_studio",
                    external_project_id="2",
                    sync_status="pending",
                ),
            ]
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_sync_samples_endpoint_marks_task_failed_when_enqueue_fails(session_factory, label_client):
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: LEGACY_TEST_ACTOR

    def override_session() -> Generator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_dataset_session] = override_session
    app.dependency_overrides[get_label_project_session] = override_session
    app.dependency_overrides[get_label_studio_client] = lambda: label_client
    app.dependency_overrides[get_label_sync_stream_producer] = lambda: FailingStreamProducer()
    app.dependency_overrides[get_settings] = lambda: Settings(
        label_studio_token="test-token",
        label_studio_public_url="",
    )

    with TestClient(app) as test_client:
        dataset_id = create_dataset_row(session_factory)
        project = test_client.post(f"/datasets/{dataset_id}/label-projects").json()
        response = test_client.post(f"/label-projects/{project['id']}/sync-samples")

    assert response.status_code == 201
    assert response.json()["status"] == TaskStatus.FAILED.value
    assert response.json()["error_code"] == "ENQUEUE_FAILED"


def test_worker_syncs_samples_to_label_studio(session_factory, label_client: FakeLabelStudioClient):
    dataset_id, sample_id = create_dataset_with_sample(session_factory)
    with session_factory() as session:
        project = LabelProject(
            dataset_id=dataset_id,
            provider="label_studio",
            external_project_id="9001",
            sync_status="pending",
        )
        task = Task(
            task_type=TaskType.SYNC_LABEL_STUDIO_DATA.value,
            status=TaskStatus.QUEUED.value,
            resource_type="label_project",
            payload={},
        )
        session.add_all([project, task])
        session.commit()
        project_id = project.id
        task_id = task.id
        task.payload = {"label_project_id": project_id}
        session.add(task)
        session.commit()

    with session_factory() as session:
        sync_label_project_samples(session, label_client, task_id, project_id)

    assert label_client.imported_tasks == [
        {
            "project_id": "9001",
            "tasks": [
                {
                    "data": {
                        "image": "memory://datasets/sample-one.png",
                        "visiox_dataset_id": dataset_id,
                        "visiox_sample_id": sample_id,
                    }
                }
            ],
        }
    ]
    with session_factory() as session:
        assert session.get(Task, task_id).status == TaskStatus.SUCCESS.value
        assert session.get(LabelProject, project_id).sync_status == "synced"


def test_label_sync_runner_processes_queued_sync_tasks(session_factory, label_client: FakeLabelStudioClient):
    dataset_id, sample_id = create_dataset_with_sample(session_factory)
    storage = InMemoryObjectStorageClient()
    with session_factory() as session:
        project = LabelProject(
            dataset_id=dataset_id,
            provider="label_studio",
            external_project_id="9001",
            sync_status="pending",
        )
        task = Task(
            task_type=TaskType.SYNC_LABEL_STUDIO_DATA.value,
            status=TaskStatus.QUEUED.value,
            resource_type="label_project",
            payload={},
        )
        session.add_all([project, task])
        session.commit()
        project_id = project.id
        task_id = task.id
        task.payload = {"label_project_id": project_id}
        session.add(task)
        session.commit()

    with session_factory() as session:
        processed = run_pending_label_sync_tasks(session, storage, label_client)

    assert [task.id for task in processed] == [task_id]
    assert label_client.imported_tasks[0]["tasks"][0]["data"]["visiox_sample_id"] == sample_id
    with session_factory() as session:
        assert session.get(Task, task_id).status == TaskStatus.SUCCESS.value
        assert session.get(LabelProject, project_id).sync_status == "synced"


def test_worker_round_trips_llm_samples_and_marks_invalid_assistant(session_factory):
    storage = InMemoryObjectStorageClient()
    label_client = FakeLabelStudioClient()
    with session_factory() as session:
        dataset = Dataset(
            name="llm-label-loop",
            task="llm",
            status="created",
            format="openai_messages",
            class_schema={},
            sample_count=2,
            source="llm_upload",
        )
        session.add(dataset)
        session.flush()
        samples = []
        for index in (1, 2):
            object_name = f"{dataset.id}/llm/samples/row-{index}.json"
            storage.objects[("datasets", object_name)] = json.dumps(
                {
                    "source_row_id": f"row-{index}",
                    "messages": [
                        {"role": "user", "content": f"问题 {index}"},
                        {"role": "assistant", "content": f"草稿 {index}"},
                    ],
                },
                ensure_ascii=False,
            ).encode("utf-8")
            sample = DatasetSample(
                dataset_id=dataset.id,
                file_uri=f"memory://datasets/{object_name}",
                checksum=f"llm-{index}",
                annotation_status="unlabeled",
            )
            session.add(sample)
            samples.append(sample)
        project = LabelProject(
            dataset_id=dataset.id,
            provider="label_studio",
            external_project_id="9001",
            sync_status="pending",
        )
        sync_task = Task(
            task_type=TaskType.SYNC_LABEL_STUDIO_DATA.value,
            status=TaskStatus.QUEUED.value,
            resource_type="label_project",
            payload={},
        )
        session.add_all([project, sync_task])
        session.commit()
        project_id = project.id
        dataset_id = dataset.id
        sample_ids = [sample.id for sample in samples]
        sync_task.payload = {"label_project_id": project_id}
        session.add(sync_task)
        session.commit()
        sync_task_id = sync_task.id

    with session_factory() as session:
        sync_label_project_samples(session, label_client, sync_task_id, project_id, storage)

    imported = {
        task["data"]["source_row_id"]: task["data"]
        for task in label_client.imported_tasks[0]["tasks"]
    }
    assert imported["row-1"]["user"] == "问题 1"
    assert imported["row-1"]["assistant"] == "草稿 1"

    label_client.export_payload = [
        {
            "data": {
                "visiox_sample_id": sample_ids[0],
                "source_row_id": "row-1",
                "system": "",
                "user": "问题 1",
            },
            "annotations": [
                {"result": [{"from_name": "assistant", "value": {"text": ["最终答案"]}}]}
            ],
        },
        {
            "data": {
                "visiox_sample_id": sample_ids[1],
                "source_row_id": "row-2",
                "system": "",
                "user": "问题 2",
            },
            "annotations": [
                {"result": [{"from_name": "assistant", "value": {"text": [""]}}]}
            ],
        },
    ]
    with session_factory() as session:
        import_task = Task(
            task_type=TaskType.IMPORT_LABEL_STUDIO_ANNOTATION.value,
            status=TaskStatus.QUEUED.value,
            resource_type="label_project",
            payload={"label_project_id": project_id},
        )
        session.add(import_task)
        session.commit()
        import_task_id = import_task.id

    with session_factory() as session:
        import_label_project_annotations(session, storage, label_client, import_task_id, project_id)

    with session_factory() as session:
        first = session.get(DatasetSample, sample_ids[0])
        second = session.get(DatasetSample, sample_ids[1])
        valid_annotation = session.scalar(
            select(Annotation).where(Annotation.dataset_sample_id == sample_ids[0])
        )
        invalid_annotation = session.scalar(
            select(Annotation).where(Annotation.dataset_sample_id == sample_ids[1])
        )
        assert first.annotation_status == "labeled"
        assert second.annotation_status == "invalid"
        assert valid_annotation.validation_status == "valid"
        assert valid_annotation.internal_payload["messages"][-1]["content"] == "最终答案"
        assert invalid_annotation.validation_status == "invalid"
        assert session.get(Dataset, dataset_id).annotation_count == 1


def test_worker_converts_minio_sample_uri_to_public_url(
    session_factory,
    label_client: FakeLabelStudioClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("VISIOX_MINIO_PUBLIC_URL", "http://127.0.0.1:9000")
    get_settings.cache_clear()
    dataset_id, sample_id = create_dataset_with_sample(session_factory)
    with session_factory() as session:
        sample = session.get(DatasetSample, sample_id)
        sample.file_uri = "minio://datasets/folder with spaces/sample-one.png"
        project = LabelProject(
            dataset_id=dataset_id,
            provider="label_studio",
            external_project_id="9001",
            sync_status="pending",
        )
        task = Task(
            task_type=TaskType.SYNC_LABEL_STUDIO_DATA.value,
            status=TaskStatus.QUEUED.value,
            resource_type="label_project",
            payload={},
        )
        session.add_all([sample, project, task])
        session.commit()
        project_id = project.id
        task_id = task.id
        task.payload = {"label_project_id": project_id}
        session.add(task)
        session.commit()

    try:
        with session_factory() as session:
            sync_label_project_samples(session, label_client, task_id, project_id)
    finally:
        get_settings.cache_clear()

    imported_task = label_client.imported_tasks[0]["tasks"][0]
    assert imported_task["data"]["image"] == "http://127.0.0.1:9000/datasets/folder%20with%20spaces/sample-one.png"


def test_worker_syncs_existing_annotations_to_label_studio(session_factory, label_client: FakeLabelStudioClient):
    dataset_id, sample_id = create_dataset_with_sample(session_factory)
    with session_factory() as session:
        session.add(
            Annotation(
                dataset_sample_id=sample_id,
                source="coco",
                internal_payload={
                    "annotations": [
                        {
                            "source_annotation_id": "coco-one",
                            "results": [
                                {
                                    "shape": "rectangle",
                                    "class_name": "defect",
                                    "x": 10,
                                    "y": 20,
                                    "width": 30,
                                    "height": 40,
                                }
                            ],
                        }
                    ]
                },
                validation_status="valid",
            )
        )
        project = LabelProject(
            dataset_id=dataset_id,
            provider="label_studio",
            external_project_id="9001",
            sync_status="pending",
        )
        task = Task(
            task_type=TaskType.SYNC_LABEL_STUDIO_DATA.value,
            status=TaskStatus.QUEUED.value,
            resource_type="label_project",
            payload={},
        )
        session.add_all([project, task])
        session.commit()
        project_id = project.id
        task_id = task.id
        task.payload = {"label_project_id": project_id}
        session.add(task)
        session.commit()

    with session_factory() as session:
        sync_label_project_samples(session, label_client, task_id, project_id)

    imported_task = label_client.imported_tasks[0]["tasks"][0]
    assert imported_task["annotations"][0]["result"] == [
        {
            "from_name": "label",
            "to_name": "image",
            "type": "rectanglelabels",
            "value": {
                "x": 10,
                "y": 20,
                "width": 30,
                "height": 40,
                "rectanglelabels": ["defect"],
            },
        }
    ]


def test_importer_normalizes_label_studio_rectangle_payload():
    task_payload = json.loads((__import__("pathlib").Path("tests/fixtures/labelstudio/detect_export.json")).read_text())[0]

    normalized = normalize_label_studio_task(task_payload)

    assert normalized.sample_id == "sample-one"
    assert normalized.payload["source_task_id"] == 101
    assert normalized.payload["annotations"][0]["results"][0] == {
        "source_result_id": "rect-1",
        "class_name": "defect",
        "shape": "rectangle",
        "x": 10,
        "y": 20,
        "width": 30,
        "height": 40,
    }


def test_worker_imports_annotations_and_writes_raw_payload_to_storage(session_factory):
    dataset_id, sample_id = create_dataset_with_sample(session_factory)
    storage = InMemoryObjectStorageClient()
    label_client = FakeLabelStudioClient()
    label_client.export_payload = json.loads(
        (__import__("pathlib").Path("tests/fixtures/labelstudio/detect_export.json")).read_text()
    )
    label_client.export_payload[0]["data"]["visiox_sample_id"] = sample_id

    with session_factory() as session:
        project = LabelProject(
            dataset_id=dataset_id,
            provider="label_studio",
            external_project_id="9001",
            sync_status="synced",
        )
        task = Task(
            task_type=TaskType.IMPORT_LABEL_STUDIO_ANNOTATION.value,
            status=TaskStatus.QUEUED.value,
            resource_type="label_project",
            payload={},
        )
        session.add_all([project, task])
        session.commit()
        project_id = project.id
        task_id = task.id
        task.payload = {"label_project_id": project_id}
        session.add(task)
        session.commit()

    with session_factory() as session:
        import_label_project_annotations(session, storage, label_client, task_id, project_id)

    with session_factory() as session:
        annotation = session.scalar(select(Annotation).where(Annotation.dataset_sample_id == sample_id))
        dataset = session.get(Dataset, dataset_id)
        sample = session.get(DatasetSample, sample_id)
        task = session.get(Task, task_id)

    assert annotation is not None
    assert annotation.raw_payload_uri.startswith("memory://label-studio/")
    assert annotation.internal_payload["annotations"][0]["results"][0]["shape"] == "rectangle"
    assert dataset.annotation_count == 1
    assert sample.annotation_status == "labeled"
    assert task.status == TaskStatus.SUCCESS.value
    assert len(storage.objects) == 1


def test_worker_does_not_treat_unannotated_label_studio_tasks_as_annotations(session_factory):
    dataset_id, sample_id = create_dataset_with_sample(session_factory)
    storage = InMemoryObjectStorageClient()
    label_client = FakeLabelStudioClient()
    label_client.export_payload = [
        {
            "id": 101,
            "data": {"visiox_sample_id": sample_id},
            "annotations": [{"id": 1, "result": []}],
        }
    ]
    with session_factory() as session:
        project = LabelProject(
            dataset_id=dataset_id,
            provider="label_studio",
            external_project_id="9001",
            sync_status="synced",
        )
        task = Task(
            task_type=TaskType.IMPORT_LABEL_STUDIO_ANNOTATION.value,
            status=TaskStatus.QUEUED.value,
            resource_type="label_project",
            payload={},
        )
        session.add_all([project, task])
        session.commit()
        project_id = project.id
        task_id = task.id
        task.payload = {"label_project_id": project_id}
        session.add(task)
        session.commit()

    with session_factory() as session:
        import_label_project_annotations(session, storage, label_client, task_id, project_id)

    with session_factory() as session:
        assert session.scalar(select(Annotation).where(Annotation.dataset_sample_id == sample_id)) is None
        assert session.get(Dataset, dataset_id).annotation_count == 0
        assert session.get(DatasetSample, sample_id).annotation_status == "pending"
        assert session.get(Task, task_id).status == TaskStatus.SUCCESS.value
    assert storage.objects == {}


def test_worker_cleans_raw_payload_when_later_annotation_import_fails(session_factory):
    dataset_id, sample_id = create_dataset_with_sample(session_factory)
    storage = InMemoryObjectStorageClient()
    label_client = FakeLabelStudioClient()
    label_client.export_payload = [
        {
            "id": 101,
            "data": {"visiox_sample_id": sample_id},
            "annotations": [{"id": 1, "result": []}],
        },
        {
            "id": 102,
            "data": {"visiox_sample_id": "missing-sample"},
            "annotations": [{"id": 2, "result": []}],
        },
    ]

    with session_factory() as session:
        project = LabelProject(
            dataset_id=dataset_id,
            provider="label_studio",
            external_project_id="9001",
            sync_status="synced",
        )
        task = Task(
            task_type=TaskType.IMPORT_LABEL_STUDIO_ANNOTATION.value,
            status=TaskStatus.QUEUED.value,
            resource_type="label_project",
            resource_id=None,
            payload={},
        )
        session.add_all([project, task])
        session.commit()
        project_id = project.id
        task_id = task.id
        task.payload = {"label_project_id": project_id}
        session.add(task)
        session.commit()

    with session_factory() as session:
        import_label_project_annotations(session, storage, label_client, task_id, project_id)

    with session_factory() as session:
        task = session.get(Task, task_id)
        assert task.status == TaskStatus.FAILED.value
        assert task.error_code == "LABEL_STUDIO_IMPORT_FAILED"

    assert storage.objects == {}


def test_worker_marks_task_and_project_failed_when_label_studio_errors(session_factory):
    dataset_id, _sample_id = create_dataset_with_sample(session_factory)
    with session_factory() as session:
        project = LabelProject(
            dataset_id=dataset_id,
            provider="label_studio",
            external_project_id="9001",
            sync_status="pending",
        )
        task = Task(
            task_type=TaskType.SYNC_LABEL_STUDIO_DATA.value,
            status=TaskStatus.QUEUED.value,
            resource_type="label_project",
            payload={},
        )
        session.add_all([project, task])
        session.commit()
        project_id = project.id
        task_id = task.id
        task.payload = {"label_project_id": project_id}
        session.add(task)
        session.commit()

    with session_factory() as session:
        sync_label_project_samples(session, FailingLabelStudioClient(), task_id, project_id)

    with session_factory() as session:
        assert session.get(Task, task_id).status == TaskStatus.FAILED.value
        assert session.get(Task, task_id).error_code == "LABEL_STUDIO_SYNC_FAILED"
        assert "500" in session.get(Task, task_id).error_message
        assert session.get(LabelProject, project_id).sync_status == "failed"
