from collections.abc import Generator
from io import BytesIO
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import visiox_api.main as api_main
from visiox_api.main import create_app
from visiox_api.routes.dataset_samples import (
    _validate_zip_entry,
    get_dataset_sample_session,
    get_object_storage_client,
)
from visiox_api.routes.datasets import get_dataset_session
from visiox_api.routes.tasks import get_task_session
from visiox_db.models import Annotation, Dataset, DatasetSample
from visiox_storage.checksum import sha256_bytes
from visiox_storage.client import InMemoryObjectStorageClient, MinioObjectStorageClient
from visiox_yolo26.datasets.analysis import analyze_dataset
from visiox_yolo26.datasets.validation import validate_dataset_format


@pytest.fixture()
def session_factory(tmp_path):
    database_path = tmp_path / "visiox-datasets.db"
    database_url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture()
def storage() -> InMemoryObjectStorageClient:
    return InMemoryObjectStorageClient()


@pytest.fixture()
def client(session_factory, storage: InMemoryObjectStorageClient) -> Generator[TestClient]:
    app = create_app()

    def override_session() -> Generator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_dataset_session] = override_session
    app.dependency_overrides[get_dataset_sample_session] = override_session
    app.dependency_overrides[get_object_storage_client] = lambda: storage
    app.dependency_overrides[get_task_session] = override_session

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
                    internal_payload={"class_name": "scratch"},
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
    assert result["class_distribution"] == {"scratch": 1, "1": 1}
    assert result["image_size_distribution"] == {
        "total_with_dimensions": 1,
        "by_size": {"20x10": 1},
    }
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
    assert "ANALYZE_DATASET" in task_types
    assert "VALIDATE_DATASET_FORMAT" in task_types
