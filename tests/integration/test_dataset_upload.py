from collections.abc import Generator
from io import BytesIO
import json
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.orm import Session, sessionmaker

import visiox_api.main as api_main
from visiox_api.dependencies.auth import get_current_user
from visiox_api.main import create_app
from visiox_api.routes.dataset_samples import (
    _validate_zip_entry,
    get_dataset_sample_session,
    get_object_storage_client,
)
from visiox_api.routes.datasets import get_dataset_object_storage_client, get_dataset_session
from visiox_api.routes.tasks import get_task_session
from visiox_common.settings import Settings, get_settings
from visiox_common.tasks import TaskStatus, TaskType
from visiox_db.models import Annotation, Dataset, DatasetSample, LabelProject, Task, TrainingPipeline
from visiox_storage.checksum import sha256_bytes
from visiox_storage.client import InMemoryObjectStorageClient, MinioObjectStorageClient
from visiox_yolo26.datasets.analysis import analyze_dataset
from visiox_yolo26.datasets.validation import validate_dataset_format
from tests.integration.ownership_test_support import install_legacy_ownership


LEGACY_TEST_ACTOR = SimpleNamespace(id="legacy-admin", organization_id="legacy-org", role="admin")


@pytest.fixture()
def session_factory(tmp_path):
    database_path = tmp_path / "visiox-datasets.db"
    database_url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    install_legacy_ownership(factory)
    return factory


@pytest.fixture()
def storage() -> InMemoryObjectStorageClient:
    return InMemoryObjectStorageClient()


@pytest.fixture()
def client(session_factory, storage: InMemoryObjectStorageClient) -> Generator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: LEGACY_TEST_ACTOR

    def override_session() -> Generator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_dataset_session] = override_session
    app.dependency_overrides[get_dataset_object_storage_client] = lambda: storage
    app.dependency_overrides[get_dataset_sample_session] = override_session
    app.dependency_overrides[get_object_storage_client] = lambda: storage
    app.dependency_overrides[get_task_session] = override_session
    app.dependency_overrides[get_settings] = lambda: Settings()

    with TestClient(app) as test_client:
        yield test_client


def make_image_bytes(size: tuple[int, int] = (8, 6), image_format: str = "PNG") -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, color=(20, 80, 140)).save(buffer, format=image_format)
    return buffer.getvalue()


def make_zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def create_dataset(client: TestClient, name: str = "factory-dataset", task: str = "detect") -> dict[str, object]:
    response = client.post(
        "/datasets",
        json={"name": name, "task": task, "class_schema": {"names": ["part", "defect"]}},
    )
    assert response.status_code == 201
    return response.json()


def test_create_and_get_datasets_support_defaults_and_filters(client: TestClient):
    first = create_dataset(client, "detect-dataset", "detect")
    second = create_dataset(client, "classify-dataset", "classify")

    assert first["source"] == "upload"
    assert first["status"] == "created"
    assert first["sample_count"] == 0
    assert first["class_schema"] == {"names": ["part", "defect"]}

    list_response = client.get("/datasets?task=detect&status=created")
    detail_response = client.get(f"/datasets/{second['id']}")
    missing_response = client.get("/datasets/missing")

    assert list_response.status_code == 200
    body = list_response.json()
    assert body["total"] == 1
    assert [item["id"] for item in body["items"]] == [first["id"]]
    assert detail_response.status_code == 200
    assert detail_response.json()["id"] == second["id"]
    assert missing_response.status_code == 404


def test_preparation_dataset_is_explicit_and_promotes_only_annotated_samples(
    client: TestClient,
    session_factory,
) -> None:
    preparation = client.post(
        "/datasets",
        json={
            "name": "label-studio-source",
            "task": "detect",
            "class_schema": {"names": ["defect"]},
            "preparation": True,
        },
    ).json()
    with session_factory() as session:
        labeled = DatasetSample(
            dataset_id=preparation["id"],
            file_uri="memory://datasets/source/labeled.png",
            checksum="labeled",
            annotation_status="labeled",
        )
        unlabeled = DatasetSample(
            dataset_id=preparation["id"],
            file_uri="memory://datasets/source/unlabeled.png",
            checksum="unlabeled",
            annotation_status="unlabeled",
        )
        session.add_all([labeled, unlabeled])
        session.flush()
        session.add(
            Annotation(
                dataset_sample_id=labeled.id,
                source="label_studio",
                internal_payload={"annotations": [{"results": [{"class_name": "defect"}]}]},
                validation_status="pending",
            )
        )
        source = session.get(Dataset, preparation["id"])
        source.sample_count = 2
        source.annotation_count = 1
        session.commit()

    response = client.post(f"/datasets/{preparation['id']}/promote")

    assert preparation["status"] == "preparing"
    assert response.status_code == 201
    promoted = response.json()
    assert promoted["status"] == "created"
    assert promoted["source"] == "label_studio"
    assert promoted["sample_count"] == 1
    assert promoted["annotation_count"] == 1
    with session_factory() as session:
        samples = session.scalars(select(DatasetSample).where(DatasetSample.dataset_id == promoted["id"])).all()
        assert [sample.file_uri for sample in samples] == ["memory://datasets/source/labeled.png"]


def test_preparation_without_annotations_cannot_be_promoted(client: TestClient) -> None:
    preparation = client.post(
        "/datasets",
        json={
            "name": "empty-label-source",
            "task": "detect",
            "class_schema": {"names": ["defect"]},
            "preparation": True,
        },
    ).json()

    response = client.post(f"/datasets/{preparation['id']}/promote")

    assert response.status_code == 409


def test_upload_image_stores_object_and_indexes_sample(
    client: TestClient,
    session_factory,
    storage: InMemoryObjectStorageClient,
):
    dataset = create_dataset(client)
    image_bytes = make_image_bytes((11, 7))

    response = client.post(
        f"/datasets/{dataset['id']}/samples:upload",
        files={"file": ("part.png", image_bytes, "image/png")},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["created_count"] == 1
    assert body["duplicate_count"] == 0
    assert body["skipped_count"] == 0
    sample = body["samples"][0]
    checksum = sha256_bytes(image_bytes)
    assert sample["width"] == 11
    assert sample["height"] == 7
    assert sample["checksum"] == checksum
    assert sample["split"] == "unassigned"
    assert sample["annotation_status"] == "unlabeled"
    assert sample["file_uri"] == f"memory://datasets/{dataset['id']}/samples/{checksum}-part.png"
    assert storage.objects[("datasets", f"{dataset['id']}/samples/{checksum}-part.png")] == image_bytes

    with session_factory() as session:
        saved_dataset = session.get(Dataset, dataset["id"])
        saved_sample = session.get(DatasetSample, sample["id"])

    assert saved_dataset.sample_count == 1
    assert saved_sample.checksum == checksum


def test_upload_zip_indexes_images_skips_non_images_and_rejects_zip_slip(client: TestClient):
    dataset = create_dataset(client)
    first_image = make_image_bytes((3, 4))
    second_image = make_image_bytes((5, 6))
    zip_bytes = make_zip_bytes(
        {
            "nested/first.png": first_image,
            "second.jpg": second_image,
            "notes/readme.txt": b"not an image",
        }
    )

    response = client.post(
        f"/datasets/{dataset['id']}/samples:upload",
        files={"file": ("batch.zip", zip_bytes, "application/zip")},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["created_count"] == 2
    assert body["duplicate_count"] == 0
    assert body["skipped_count"] == 1
    assert {sample["width"] for sample in body["samples"]} == {3, 5}

    unsafe_zip = make_zip_bytes({"../outside.png": first_image})
    unsafe_response = client.post(
        f"/datasets/{dataset['id']}/samples:upload",
        files={"file": ("unsafe.zip", unsafe_zip, "application/zip")},
    )

    assert unsafe_response.status_code == 400
    assert "unsafe" in unsafe_response.json()["detail"].lower()


def test_upload_yolo_detect_folder_batch_indexes_images_and_labels(client: TestClient, session_factory):
    dataset = create_dataset(client, name="yolo-folder", task="detect")
    image_bytes = make_image_bytes((20, 10))

    response = client.post(
        f"/datasets/{dataset['id']}/samples:upload-batch",
        files=[
            ("files", ("dataset/data.yaml", b"names: [ok, defect]\n", "text/yaml")),
            ("files", ("dataset/images/train/part.png", image_bytes, "image/png")),
            ("files", ("dataset/labels/train/part.txt", b"1 0.5 0.5 0.4 0.2\n", "text/plain")),
        ],
        data={
            "relative_paths": [
                "dataset/data.yaml",
                "dataset/images/train/part.png",
                "dataset/labels/train/part.txt",
            ]
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["created_count"] == 1
    assert body["skipped_count"] == 2

    with session_factory() as session:
        saved_dataset = session.get(Dataset, dataset["id"])
        sample = session.get(DatasetSample, body["samples"][0]["id"])
        annotation = session.scalar(select(Annotation).where(Annotation.dataset_sample_id == sample.id))

    assert saved_dataset.sample_count == 1
    assert saved_dataset.annotation_count == 1
    assert sample.split == "train"
    assert sample.annotation_status == "labeled"
    assert annotation.source == "yolo"
    assert annotation.internal_payload["annotations"][0]["results"][0]["class_name"] == "defect"


def test_validate_dataset_marks_labeled_dataset_validated(client: TestClient, session_factory):
    dataset = create_dataset(client, name="validated-yolo-folder", task="detect")
    image_bytes = make_image_bytes((20, 10))
    upload_response = client.post(
        f"/datasets/{dataset['id']}/samples:upload-batch",
        files=[
            ("files", ("dataset/data.yaml", b"names: [ok, defect]\n", "text/yaml")),
            ("files", ("dataset/images/train/part.png", image_bytes, "image/png")),
            ("files", ("dataset/labels/train/part.txt", b"1 0.5 0.5 0.4 0.2\n", "text/plain")),
        ],
        data={
            "relative_paths": [
                "dataset/data.yaml",
                "dataset/images/train/part.png",
                "dataset/labels/train/part.txt",
            ]
        },
    )
    assert upload_response.status_code == 201

    validate_response = client.post(f"/datasets/{dataset['id']}/validate")

    assert validate_response.status_code == 201
    assert validate_response.json()["status"] == TaskStatus.SUCCESS.value
    with session_factory() as session:
        saved_dataset = session.get(Dataset, dataset["id"])
    assert saved_dataset.status == "validated"


def test_upload_coco_detect_folder_batch_converts_boxes_to_internal_annotations(client: TestClient, session_factory):
    dataset = create_dataset(client, name="coco-folder", task="detect")
    image_bytes = make_image_bytes((100, 50))
    coco_payload = {
        "images": [{"id": 10, "file_name": "seed_001.jpg", "width": 100, "height": 50}],
        "annotations": [{"id": 99, "image_id": 10, "category_id": 7, "bbox": [10, 5, 40, 20], "area": 800, "iscrowd": 0}],
        "categories": [
            {"id": 5, "name": "ok", "supercategory": "none"},
            {"id": 7, "name": "defect", "supercategory": "none"},
        ],
    }

    response = client.post(
        f"/datasets/{dataset['id']}/samples:upload-batch",
        files=[
            ("files", ("dataset/annotations/instance_train.json", json.dumps(coco_payload).encode("utf-8"), "application/json")),
            ("files", ("dataset/images/seed_001.jpg", image_bytes, "image/jpeg")),
        ],
        data={
            "relative_paths": [
                "dataset/annotations/instance_train.json",
                "dataset/images/seed_001.jpg",
            ]
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["created_count"] == 1
    assert body["skipped_count"] == 1

    with session_factory() as session:
        saved_dataset = session.get(Dataset, dataset["id"])
        sample = session.get(DatasetSample, body["samples"][0]["id"])
        annotation = session.scalar(select(Annotation).where(Annotation.dataset_sample_id == sample.id))

    assert saved_dataset.class_schema == {"names": ["ok", "defect"]}
    assert saved_dataset.annotation_count == 1
    assert sample.split == "train"
    result = annotation.internal_payload["annotations"][0]["results"][0]
    assert result["source_result_id"] == "99"
    assert result["class_id"] == 1
    assert result["class_name"] == "defect"
    assert result["x"] == 10
    assert result["y"] == 10
    assert result["width"] == 40
    assert result["height"] == 40


@pytest.mark.parametrize(
    "unsafe_entry",
    [
        "C:/outside.png",
        "C:\\outside.png",
        "\\\\server\\share\\outside.png",
        "/outside.png",
    ],
)
def test_upload_zip_rejects_windows_absolute_and_backslash_entries(
    client: TestClient,
    unsafe_entry: str,
):
    dataset = create_dataset(client)
    unsafe_zip = make_zip_bytes({unsafe_entry: make_image_bytes()})

    response = client.post(
        f"/datasets/{dataset['id']}/samples:upload",
        files={"file": ("unsafe.zip", unsafe_zip, "application/zip")},
    )

    assert response.status_code == 400
    assert "unsafe" in response.json()["detail"].lower()


def test_zip_entry_validator_rejects_relative_backslash_path():
    with pytest.raises(Exception) as error:
        _validate_zip_entry("nested\\outside.png")

    assert getattr(error.value, "status_code") == 400
    assert "unsafe" in error.value.detail.lower()


def test_get_object_storage_client_requires_app_state_storage():
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

    with pytest.raises(Exception) as error:
        get_object_storage_client(request)

    assert getattr(error.value, "status_code") == 500
    assert "object storage" in error.value.detail.lower()


def test_create_app_lifespan_configures_minio_object_storage(monkeypatch):
    created_clients = []

    class FakeMinioObjectStorageClient:
        def __init__(
            self,
            endpoint: str,
            access_key: str,
            secret_key: str,
            secure: bool = False,
        ) -> None:
            self.endpoint = endpoint
            self.access_key = access_key
            self.secret_key = secret_key
            self.secure = secure
            created_clients.append(self)

    monkeypatch.setattr(api_main, "MinioObjectStorageClient", FakeMinioObjectStorageClient, raising=False)

    app = create_app()

    with TestClient(app):
        storage = app.state.object_storage

    assert storage is created_clients[0]
    assert isinstance(storage, FakeMinioObjectStorageClient)
    assert storage.endpoint == "minio:9000"
    assert storage.access_key == "visiox"
    assert storage.secret_key == "visiox123"
    assert storage.secure is False
    assert not isinstance(storage, InMemoryObjectStorageClient)
    assert not isinstance(storage, MinioObjectStorageClient)


def test_upload_duplicate_checksum_returns_existing_sample(client: TestClient):
    dataset = create_dataset(client)
    image_bytes = make_image_bytes()

    first_response = client.post(
        f"/datasets/{dataset['id']}/samples:upload",
        files={"file": ("first.png", image_bytes, "image/png")},
    )
    duplicate_response = client.post(
        f"/datasets/{dataset['id']}/samples:upload",
        files={"file": ("second.png", image_bytes, "image/png")},
    )

    assert first_response.status_code == 201
    assert duplicate_response.status_code == 200
    assert duplicate_response.json()["created_count"] == 0
    assert duplicate_response.json()["duplicate_count"] == 1
    assert duplicate_response.json()["samples"][0]["id"] == first_response.json()["samples"][0]["id"]


def test_delete_dataset_removes_related_rows_and_uploaded_objects(
    client: TestClient,
    session_factory,
    storage: InMemoryObjectStorageClient,
):
    dataset = create_dataset(client)
    upload_response = client.post(
        f"/datasets/{dataset['id']}/samples:upload",
        files={"file": ("part.png", make_image_bytes(), "image/png")},
    )
    sample_id = upload_response.json()["samples"][0]["id"]
    assert storage.objects

    with session_factory() as session:
        session.add(
            Annotation(
                dataset_sample_id=sample_id,
                source="test",
                internal_payload={"annotations": []},
                validation_status="valid",
            )
        )
        label_project = LabelProject(
            dataset_id=dataset["id"],
            provider="label_studio",
            external_project_id="99",
            sync_status="synced",
        )
        dataset_task = Task(
            task_type=TaskType.ANALYZE_DATASET.value,
            status=TaskStatus.SUCCESS.value,
            resource_type="dataset",
            resource_id=dataset["id"],
            payload={"dataset_id": dataset["id"]},
        )
        label_task = Task(
            task_type=TaskType.SYNC_LABEL_STUDIO_DATA.value,
            status=TaskStatus.SUCCESS.value,
            resource_type="label_project",
            payload={"dataset_id": dataset["id"]},
        )
        pipeline = TrainingPipeline(
            name="uses-deleted-dataset",
            task="detect",
            scale="n",
            dataset_id=dataset["id"],
            params_template={},
            default_environment={},
            status="draft",
        )
        session.add_all([label_project, dataset_task, label_task, pipeline])
        session.commit()
        label_task.resource_id = label_project.id
        session.add(label_task)
        session.commit()
        pipeline_id = pipeline.id
        label_project_id = label_project.id
        dataset_task_id = dataset_task.id
        label_task_id = label_task.id

    response = client.delete(f"/datasets/{dataset['id']}")

    assert response.status_code == 204
    assert storage.objects == {}
    with session_factory() as session:
        assert session.get(Dataset, dataset["id"]) is None
        assert session.get(DatasetSample, sample_id) is None
        assert session.scalar(select(Annotation).where(Annotation.dataset_sample_id == sample_id)) is None
        assert session.get(LabelProject, label_project_id) is None
        assert session.get(Task, dataset_task_id) is None
        assert session.get(Task, label_task_id) is None
        assert session.get(TrainingPipeline, pipeline_id).dataset_id is None


def test_dataset_sample_checksum_unique_index_exists(session_factory):
    bind = session_factory.kw["bind"]
    indexes = inspect(bind).get_indexes("dataset_samples")

    assert any(index["name"] == "uq_dataset_samples_dataset_checksum" and index["unique"] for index in indexes)


def test_upload_rejects_oversized_file_before_storage(
    session_factory,
    storage: InMemoryObjectStorageClient,
):
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: LEGACY_TEST_ACTOR

    def override_session() -> Generator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_dataset_session] = override_session
    app.dependency_overrides[get_dataset_sample_session] = override_session
    app.dependency_overrides[get_object_storage_client] = lambda: storage
    app.dependency_overrides[get_task_session] = override_session
    app.dependency_overrides[get_settings] = lambda: Settings(max_dataset_upload_bytes=8)

    with TestClient(app) as test_client:
        dataset = create_dataset(test_client)
        response = test_client.post(
            f"/datasets/{dataset['id']}/samples:upload",
            files={"file": ("part.png", make_image_bytes(), "image/png")},
        )

    assert response.status_code == 413
    assert storage.objects == {}


def test_upload_zip_rejects_too_many_entries(
    session_factory,
    storage: InMemoryObjectStorageClient,
):
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: LEGACY_TEST_ACTOR

    def override_session() -> Generator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_dataset_session] = override_session
    app.dependency_overrides[get_dataset_sample_session] = override_session
    app.dependency_overrides[get_object_storage_client] = lambda: storage
    app.dependency_overrides[get_task_session] = override_session
    app.dependency_overrides[get_settings] = lambda: Settings(max_dataset_zip_entries=1)

    with TestClient(app) as test_client:
        dataset = create_dataset(test_client)
        response = test_client.post(
            f"/datasets/{dataset['id']}/samples:upload",
            files={"file": ("batch.zip", make_zip_bytes({"a.txt": b"a", "b.txt": b"b"}), "application/zip")},
        )

    assert response.status_code == 413
    assert storage.objects == {}


def test_upload_zip_rejects_uncompressed_size_before_read(
    session_factory,
    storage: InMemoryObjectStorageClient,
):
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: LEGACY_TEST_ACTOR

    def override_session() -> Generator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_dataset_session] = override_session
    app.dependency_overrides[get_dataset_sample_session] = override_session
    app.dependency_overrides[get_object_storage_client] = lambda: storage
    app.dependency_overrides[get_task_session] = override_session
    app.dependency_overrides[get_settings] = lambda: Settings(max_dataset_zip_uncompressed_bytes=1)

    with TestClient(app) as test_client:
        dataset = create_dataset(test_client)
        response = test_client.post(
            f"/datasets/{dataset['id']}/samples:upload",
            files={"file": ("batch.zip", make_zip_bytes({"a.txt": b"abc"}), "application/zip")},
        )

    assert response.status_code == 413
    assert storage.objects == {}


def test_upload_rejects_truncated_image_and_keeps_storage_empty(
    client: TestClient,
    storage: InMemoryObjectStorageClient,
):
    dataset = create_dataset(client)
    response = client.post(
        f"/datasets/{dataset['id']}/samples:upload",
        files={"file": ("broken.png", make_image_bytes()[:12], "image/png")},
    )

    assert response.status_code == 400
    assert storage.objects == {}


def test_upload_zip_cleans_previously_stored_objects_when_later_entry_fails(
    client: TestClient,
    storage: InMemoryObjectStorageClient,
):
    dataset = create_dataset(client)
    response = client.post(
        f"/datasets/{dataset['id']}/samples:upload",
        files={
            "file": (
                "batch.zip",
                make_zip_bytes({"first.png": make_image_bytes(), "broken.png": b"not an image"}),
                "application/zip",
            )
        },
    )

    assert response.status_code == 400
    assert storage.objects == {}


def test_list_samples_supports_split_and_annotation_status_filters(client: TestClient):
    dataset = create_dataset(client)
    image_bytes = make_image_bytes()
    upload_response = client.post(
        f"/datasets/{dataset['id']}/samples:upload",
        files={"file": ("part.png", image_bytes, "image/png")},
    )
    sample_id = upload_response.json()["samples"][0]["id"]
    split_response = client.post(
        f"/datasets/{dataset['id']}/samples/splits",
        json={"assignments": [{"sample_id": sample_id, "split": "train"}]},
    )

    assert split_response.status_code == 200
    response = client.get(f"/datasets/{dataset['id']}/samples?split=train&annotation_status=unlabeled")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == sample_id
    assert body["items"][0]["split"] == "train"


def test_split_ratio_assigns_train_val_and_test_samples(client: TestClient):
    dataset = create_dataset(client)
    for index in range(10):
        upload_response = client.post(
            f"/datasets/{dataset['id']}/samples:upload",
            files={"file": (f"part-{index}.png", make_image_bytes((8 + index, 6)), "image/png")},
        )
        assert upload_response.status_code == 201

    response = client.post(
        f"/datasets/{dataset['id']}/samples/splits:ratio",
        json={"train_ratio": 50, "val_ratio": 30, "test_ratio": 20},
    )

    assert response.status_code == 200
    splits = [sample["split"] for sample in response.json()["items"]]
    assert splits.count("train") == 5
    assert splits.count("val") == 3
    assert splits.count("test") == 2


def test_split_ratio_requires_percentages_to_add_to_100(client: TestClient):
    dataset = create_dataset(client)

    response = client.post(
        f"/datasets/{dataset['id']}/samples/splits:ratio",
        json={"train_ratio": 80, "val_ratio": 10, "test_ratio": 5},
    )

    assert response.status_code == 422
    assert "100" in response.json()["detail"]


def test_get_sample_content_returns_uploaded_image(client: TestClient):
    dataset = create_dataset(client)
    image_bytes = make_image_bytes((11, 7))
    upload_response = client.post(
        f"/datasets/{dataset['id']}/samples:upload",
        files={"file": ("preview.png", image_bytes, "image/png")},
    )
    sample_id = upload_response.json()["samples"][0]["id"]

    response = client.get(f"/datasets/{dataset['id']}/samples/{sample_id}/content")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert response.content == image_bytes


def test_export_dataset_downloads_images_annotations_and_metadata(client: TestClient, session_factory):
    dataset = create_dataset(client, name="exportable")
    image_bytes = make_image_bytes((11, 7))
    upload_response = client.post(
        f"/datasets/{dataset['id']}/samples:upload",
        files={"file": ("preview.png", image_bytes, "image/png")},
    )
    sample_id = upload_response.json()["samples"][0]["id"]
    with session_factory() as session:
        session.add(
            Annotation(
                dataset_sample_id=sample_id,
                source="test",
                internal_payload={"annotations": [{"results": [{"shape": "rectangle", "class_name": "part"}]}]},
                validation_status="valid",
            )
        )
        saved_dataset = session.get(Dataset, dataset["id"])
        saved_dataset.annotation_count = 1
        session.add(saved_dataset)
        session.commit()

    response = client.get(f"/datasets/{dataset['id']}/export")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/zip")
    with ZipFile(BytesIO(response.content)) as archive:
        assert "metadata.json" in archive.namelist()
        assert "annotations.json" in archive.namelist()
        image_names = [name for name in archive.namelist() if name.startswith("images/")]
        assert len(image_names) == 1
        assert archive.read(image_names[0]) == image_bytes
        metadata = json.loads(archive.read("metadata.json").decode("utf-8"))
        annotations = json.loads(archive.read("annotations.json").decode("utf-8"))

    assert metadata["name"] == "exportable"
    assert annotations["samples"][0]["annotations"][0]["source"] == "test"


def test_split_assignment_rejects_unknown_or_cross_dataset_sample_ids(client: TestClient):
    first_dataset = create_dataset(client, "first")
    second_dataset = create_dataset(client, "second")
    image_bytes = make_image_bytes()
    other_sample = client.post(
        f"/datasets/{second_dataset['id']}/samples:upload",
        files={"file": ("part.png", image_bytes, "image/png")},
    ).json()["samples"][0]

    missing_response = client.post(
        f"/datasets/{first_dataset['id']}/samples/splits",
        json={"assignments": [{"sample_id": "missing", "split": "train"}]},
    )
    cross_dataset_response = client.post(
        f"/datasets/{first_dataset['id']}/samples/splits",
        json={"assignments": [{"sample_id": other_sample["id"], "split": "val"}]},
    )

    assert missing_response.status_code == 400
    assert cross_dataset_response.status_code == 400


def test_analyze_dataset_reports_distribution_and_invalid_samples(session_factory):
    with session_factory() as session:
        dataset = Dataset(name="analysis", task="detect", status="created", source="upload")
        session.add(dataset)
        session.flush()
        first = DatasetSample(
            dataset_id=dataset.id,
            file_uri="memory://datasets/one.png",
            width=20,
            height=10,
            checksum="one",
            split="train",
            annotation_status="labeled",
        )
        invalid = DatasetSample(
            dataset_id=dataset.id,
            file_uri="memory://datasets/invalid.png",
            width=None,
            height=None,
            checksum=None,
            split="unassigned",
            annotation_status="unlabeled",
        )
        session.add_all([first, invalid])
        session.flush()
        session.add_all(
            [
                Annotation(
                    dataset_sample_id=first.id,
                    source="test",
                    internal_payload={
                        "annotations": [
                            {
                                "results": [
                                    {
                                        "shape": "rectangle",
                                        "class_name": "scratch",
                                    }
                                ]
                            }
                        ]
                    },
                    validation_status="valid",
                ),
                Annotation(
                    dataset_sample_id=first.id,
                    source="test",
                    internal_payload={"class_id": 1},
                    validation_status="valid",
                ),
            ]
        )
        dataset.sample_count = 2
        dataset.annotation_count = 2
        session.commit()
        dataset_id = dataset.id

        result = analyze_dataset(session, dataset_id)

    assert result["sample_count"] == 2
    assert result["annotation_count"] == 2
    assert result["class_distribution"] == {"1": 1, "scratch": 1}
    assert result["class_distribution_by_split"] == {"train": {"1": 1, "scratch": 1}}
    assert result["image_size_distribution"] == {
        "total_with_dimensions": 1,
        "by_size": {"20x10": 1},
    }
    assert result["split_distribution"] == {"train": 1, "unassigned": 1}
    assert result["empty_annotation_ratio"] == 0.5
    assert result["invalid_samples"] == [invalid.id]


def test_validate_dataset_format_reports_empty_missing_labels_and_missing_annotations(
    session_factory,
):
    with session_factory() as session:
        empty_dataset = Dataset(name="empty", task="detect", status="created", source="upload")
        classify_dataset = Dataset(name="classify", task="classify", status="created", source="upload")
        detect_dataset = Dataset(name="detect", task="detect", status="created", source="upload")
        session.add_all([empty_dataset, classify_dataset, detect_dataset])
        session.flush()
        session.add_all(
            [
                DatasetSample(
                    dataset_id=classify_dataset.id,
                    file_uri="memory://datasets/classify.png",
                    width=1,
                    height=1,
                    checksum="classify",
                    split="unassigned",
                    annotation_status="unlabeled",
                ),
                DatasetSample(
                    dataset_id=detect_dataset.id,
                    file_uri="memory://datasets/detect.png",
                    width=1,
                    height=1,
                    checksum="detect",
                    split="unassigned",
                    annotation_status="unlabeled",
                ),
            ]
        )
        classify_dataset.sample_count = 1
        detect_dataset.sample_count = 1
        session.commit()

        empty_result = validate_dataset_format(session, empty_dataset.id)
        classify_result = validate_dataset_format(session, classify_dataset.id)
        detect_result = validate_dataset_format(session, detect_dataset.id)

    assert empty_result["valid"] is False
    assert empty_result["errors"][0]["code"] == "DATASET_EMPTY"
    assert classify_result["valid"] is True
    assert classify_result["warnings"][0]["code"] == "CLASSIFY_LABELS_MISSING"
    assert detect_result["valid"] is False
    assert detect_result["errors"][0]["code"] == "ANNOTATIONS_MISSING"


def test_validate_dataset_format_rejects_task_shape_mismatch(session_factory):
    with session_factory() as session:
        dataset = Dataset(name="segment-with-boxes", task="segment", status="created", source="upload")
        session.add(dataset)
        session.flush()
        sample = DatasetSample(
            dataset_id=dataset.id,
            file_uri="memory://datasets/segment.png",
            width=100,
            height=100,
            checksum="segment",
            split="unassigned",
            annotation_status="labeled",
        )
        session.add(sample)
        session.flush()
        session.add(
            Annotation(
                dataset_sample_id=sample.id,
                source="test",
                internal_payload={
                    "annotations": [
                        {
                            "source_annotation_id": "box-1",
                            "results": [
                                {
                                    "class_name": "defect",
                                    "shape": "rectangle",
                                    "x": 10,
                                    "y": 10,
                                    "width": 20,
                                    "height": 20,
                                }
                            ],
                        }
                    ]
                },
                validation_status="valid",
            )
        )
        dataset.sample_count = 1
        dataset.annotation_count = 1
        session.commit()

        result = validate_dataset_format(session, dataset.id)

    assert result["valid"] is False
    assert result["errors"][0]["code"] == "ANNOTATION_SHAPE_MISMATCH"
    assert "rectangle" in result["errors"][0]["message"]


def test_process_dataset_runs_albumentations_and_cleanvision_rules(client: TestClient, session_factory):
    dataset = create_dataset(client, "process-dataset", "detect")
    upload_response = client.post(
        f"/datasets/{dataset['id']}/samples:upload",
        files={"file": ("solid.png", make_image_bytes((32, 32)), "image/png")},
    )
    assert upload_response.status_code == 201

    response = client.post(
        f"/datasets/{dataset['id']}/process",
        json={
            "augment": {"horizontalFlip": True},
            "clean": {"lowInformation": True},
            "max_samples": 20,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["task_type"] == TaskType.PROCESS_DATASET.value
    assert body["status"] == TaskStatus.SUCCESS.value
    result = body["payload"]["result"]
    assert result["augmented_count"] == 1
    assert result["cleaning_issue_count"] >= 1
    assert any(issue["rule"] == "low_information" for issue in result["cleaning_issues"])

    with session_factory() as session:
        saved_dataset = session.get(Dataset, dataset["id"])
        sample_count = session.scalar(
            select(func.count()).select_from(DatasetSample).where(DatasetSample.dataset_id == dataset["id"])
        )

    assert saved_dataset is not None
    assert saved_dataset.sample_count == 2
    assert sample_count == 2


def test_analyze_and_validate_endpoints_create_terminal_tasks(client: TestClient):
    dataset = create_dataset(client)
    image_bytes = make_image_bytes()
    client.post(
        f"/datasets/{dataset['id']}/samples:upload",
        files={"file": ("part.png", image_bytes, "image/png")},
    )

    analyze_response = client.post(f"/datasets/{dataset['id']}/analyze")
    validate_response = client.post(f"/datasets/{dataset['id']}/validate")

    assert analyze_response.status_code == 201
    analyze_body = analyze_response.json()
    assert analyze_body["task_type"] == "ANALYZE_DATASET"
    assert analyze_body["status"] == "SUCCESS"
    assert analyze_body["payload"]["result"]["sample_count"] == 1

    assert validate_response.status_code == 201
    validate_body = validate_response.json()
    assert validate_body["task_type"] == "VALIDATE_DATASET_FORMAT"
    assert validate_body["status"] == "FAILED"
    assert validate_body["error_code"] == "ANNOTATIONS_MISSING"
    assert validate_body["payload"]["result"]["valid"] is False

    tasks_response = client.get("/tasks")
    assert tasks_response.status_code == 200
    task_types = [task["task_type"] for task in tasks_response.json()["items"]]
    # The task center intentionally exposes training tasks only. Dataset jobs
    # remain queryable by their returned task IDs but do not appear in this list.
    assert "ANALYZE_DATASET" not in task_types
    assert "VALIDATE_DATASET_FORMAT" not in task_types
