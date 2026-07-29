from __future__ import annotations

from collections.abc import Generator
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session, sessionmaker

import visiox_api.main as api_main
from visiox_api.dependencies.auth import get_current_user
from visiox_api.routes.llm_datasets import get_llm_dataset_session, get_llm_dataset_storage
from visiox_common.settings import Settings, get_settings
from visiox_db.models import DatasetSample
from visiox_storage.client import InMemoryObjectStorageClient


LEGACY_TEST_ACTOR = SimpleNamespace(id="legacy-admin", organization_id="legacy-org", role="admin")


@pytest.fixture()
def llm_dataset_client(
    agent_session_factory: sessionmaker[Session],
) -> Generator[TestClient]:
    def override_session() -> Generator[Session]:
        with agent_session_factory() as session:
            yield session

    app = api_main.create_app()
    app.dependency_overrides[get_current_user] = lambda: LEGACY_TEST_ACTOR
    storage = InMemoryObjectStorageClient()
    app.dependency_overrides[get_llm_dataset_session] = override_session
    app.dependency_overrides[get_llm_dataset_storage] = lambda: storage
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None,
        max_dataset_upload_bytes=1024 * 1024,
    )
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def test_upload_validate_and_preview_llm_dataset(llm_dataset_client: TestClient):
    payload = "\n".join(
        [
            json.dumps({"instruction": "问候", "input": "", "output": "你好"}, ensure_ascii=False),
            json.dumps({"instruction": "坏样本", "input": "", "output": ""}, ensure_ascii=False),
        ]
    ).encode("utf-8")

    response = llm_dataset_client.post(
        "/datasets/llm/upload",
        data={"name": "llm-sft-demo", "format": "auto"},
        files={"file": ("train.jsonl", payload, "application/x-ndjson")},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["valid_count"] == 1
    assert body["invalid_count"] == 1
    assert body["dataset"]["task"] == "llm"
    assert body["dataset"]["format"] == "alpaca"
    assert body["dataset"]["status"] == "validated"
    assert body["dataset"]["asset_role"] == "working"
    assert body["dataset"]["annotation_count"] == 0
    dataset_id = body["dataset"]["id"]

    session_factory = llm_dataset_client.app.dependency_overrides[get_llm_dataset_session]
    session_generator = session_factory()
    session = next(session_generator)
    try:
        samples = session.query(DatasetSample).filter_by(dataset_id=dataset_id).all()
        assert [sample.annotation_status for sample in samples] == ["unlabeled"]
    finally:
        session_generator.close()

    preview = llm_dataset_client.post(
        "/datasets/llm/preview",
        json={"dataset_id": dataset_id, "limit": 5},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["samples"][0]["messages"][-1]["content"] == "你好"
    assert preview.json()["token_analysis"]["exact"] is False

    validation = llm_dataset_client.post(
        "/datasets/llm/validate",
        json={"dataset_id": dataset_id},
    )
    assert validation.status_code == 200, validation.text
    assert validation.json()["dataset"]["manifest_checksum"] == body["dataset"]["manifest_checksum"]
    assert validation.json()["dataset"]["annotation_count"] == 0
