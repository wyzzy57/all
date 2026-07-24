from io import BytesIO
from collections.abc import Generator

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from visiox_api.main import create_app
from visiox_api.routes.base_models import get_base_model_session, get_base_model_storage
from visiox_db.models import BaseModel
from visiox_storage.client import InMemoryObjectStorageClient


@pytest.fixture()
def session_factory(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'visiox-model-upload.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    return sessionmaker(bind=create_engine(database_url), autoflush=False, expire_on_commit=False)


@pytest.fixture()
def client(session_factory) -> Generator[TestClient]:
    app = create_app()
    storage = InMemoryObjectStorageClient()

    def override_session() -> Generator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_base_model_session] = override_session
    app.dependency_overrides[get_base_model_storage] = lambda: storage
    with TestClient(app) as test_client:
        yield test_client


def _pt_payload() -> bytes:
    return b"PK\x03\x04" + b"pytorch-weight" * 128


def test_upload_trainable_pt_weight_persists_ready_base_model(client, session_factory):
    response = client.post(
        "/base-models:upload",
        data={"task": "detect", "scale": "n"},
        files={"file": ("pepper-best.pt", BytesIO(_pt_payload()), "application/octet-stream")},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["filename"].endswith("-pepper-best.pt")
    assert body["source_path"] == "upload://pepper-best.pt"
    assert body["family"].startswith("custom-")
    assert body["task"] == "detect"
    assert body["scale"] == "n"
    assert body["status"] == "ready"
    assert body["local_uri"].startswith("memory://models/custom/")
    assert len(body["checksum"]) == 64
    with session_factory() as session:
        assert session.scalar(select(BaseModel).where(BaseModel.id == body["id"])) is not None


def test_upload_accepts_repeated_user_filenames(client):
    responses = [
        client.post(
            "/base-models:upload",
            data={"task": "detect", "scale": "n"},
            files={"file": ("best.pt", BytesIO(_pt_payload()), "application/octet-stream")},
        )
        for _ in range(2)
    ]

    assert [response.status_code for response in responses] == [201, 201]
    filenames = {response.json()["filename"] for response in responses}
    assert len(filenames) == 2
    assert all(filename.endswith("-best.pt") for filename in filenames)


def test_upload_rejects_non_trainable_or_invalid_weight(client):
    onnx = client.post(
        "/base-models:upload",
        data={"task": "detect", "scale": "n"},
        files={"file": ("model.onnx", BytesIO(b"onnx" * 300), "application/octet-stream")},
    )
    invalid_pt = client.post(
        "/base-models:upload",
        data={"task": "detect", "scale": "n"},
        files={"file": ("model.pt", BytesIO(b"not-a-weight" * 300), "application/octet-stream")},
    )

    assert onnx.status_code == 415
    assert invalid_pt.status_code == 400
