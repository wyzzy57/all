from __future__ import annotations

from collections.abc import Generator
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

import visiox_api.main as api_main
from visiox_api.dependencies.auth import get_current_user
from visiox_api.routes.datasets import get_dataset_object_storage_client, get_dataset_session
from visiox_api.services.llm_training import resolve_llm_dataset_version
from visiox_db.models import Annotation, Dataset, DatasetSample, DatasetVersion
from visiox_storage.client import InMemoryObjectStorageClient


ACTOR = SimpleNamespace(id="legacy-admin", organization_id="legacy-org", role="admin")


@pytest.fixture()
def conversion_client(
    agent_session_factory: sessionmaker[Session],
) -> Generator[tuple[TestClient, InMemoryObjectStorageClient]]:
    def override_session() -> Generator[Session]:
        with agent_session_factory() as session:
            yield session

    storage = InMemoryObjectStorageClient()
    app = api_main.create_app()
    app.dependency_overrides[get_current_user] = lambda: ACTOR
    app.dependency_overrides[get_dataset_session] = override_session
    app.dependency_overrides[get_dataset_object_storage_client] = lambda: storage
    try:
        with TestClient(app) as client:
            yield client, storage
    finally:
        app.dependency_overrides.clear()


def _create_labeled_dataset(session_factory: sessionmaker[Session]) -> tuple[str, list[str]]:
    with session_factory() as session:
        dataset = Dataset(
            name="llm-conversion",
            task="llm",
            status="created",
            format="openai_messages",
            class_schema={},
            source="llm_upload",
            organization_id="legacy-org",
            owner_user_id="legacy-admin",
            visibility="private",
        )
        session.add(dataset)
        session.flush()
        samples = []
        for index, annotation_status in enumerate(("labeled", "invalid", "unlabeled"), start=1):
            sample = DatasetSample(
                dataset_id=dataset.id,
                file_uri=f"memory://datasets/{dataset.id}/row-{index}.json",
                checksum=f"conversion-{index}",
                annotation_status=annotation_status,
            )
            session.add(sample)
            session.flush()
            samples.append(sample)
        session.add_all(
            [
                Annotation(
                    dataset_sample_id=samples[0].id,
                    source="label_studio",
                    internal_payload={
                        "source_row_id": "row-1",
                        "messages": [
                            {"role": "user", "content": "问题"},
                            {"role": "assistant", "content": "有效答案"},
                        ],
                    },
                    validation_status="valid",
                ),
                Annotation(
                    dataset_sample_id=samples[1].id,
                    source="label_studio",
                    internal_payload={"issue": "assistant response must be non-empty"},
                    validation_status="invalid",
                ),
            ]
        )
        session.commit()
        return dataset.id, [sample.id for sample in samples]


def test_converts_only_valid_labels_and_reuses_stable_version(
    conversion_client,
    agent_session_factory: sessionmaker[Session],
):
    client, storage = conversion_client
    dataset_id, _ = _create_labeled_dataset(agent_session_factory)

    first = client.post(f"/datasets/{dataset_id}/convert-labeled")
    repeated = client.post(f"/datasets/{dataset_id}/convert-labeled")

    assert first.status_code == 200, first.text
    body = first.json()
    assert body["version"]["version"] == 1
    assert body["valid_count"] == 1
    assert body["invalid_count"] == 1
    assert body["skipped_count"] == 1
    assert repeated.status_code == 200
    assert repeated.json()["reused"] is True
    assert repeated.json()["version"]["id"] == body["version"]["id"]
    assert len(storage.objects) == 2

    with agent_session_factory() as session:
        dataset = session.get(Dataset, dataset_id)
        versions = session.scalars(select(DatasetVersion).where(DatasetVersion.dataset_id == dataset_id)).all()
        assert dataset.asset_role == "published"
        assert dataset.status == "validated"
        assert len(versions) == 1


def test_creates_new_version_after_valid_annotation_changes(
    conversion_client,
    agent_session_factory: sessionmaker[Session],
):
    client, _ = conversion_client
    dataset_id, sample_ids = _create_labeled_dataset(agent_session_factory)
    assert client.post(f"/datasets/{dataset_id}/convert-labeled").status_code == 200
    with agent_session_factory() as session:
        annotation = session.scalar(select(Annotation).where(Annotation.dataset_sample_id == sample_ids[0]))
        annotation.internal_payload = {
            **annotation.internal_payload,
            "messages": [
                {"role": "user", "content": "问题"},
                {"role": "assistant", "content": "修订后的答案"},
            ],
        }
        session.add(annotation)
        session.commit()

    second = client.post(f"/datasets/{dataset_id}/convert-labeled")
    versions = client.get(f"/datasets/{dataset_id}/versions")

    assert second.status_code == 200
    assert second.json()["version"]["version"] == 2
    assert second.json()["reused"] is False
    assert [item["version"] for item in versions.json()] == [2, 1]


def test_rejects_conversion_without_valid_labels(
    conversion_client,
    agent_session_factory: sessionmaker[Session],
):
    client, _ = conversion_client
    dataset_id, sample_ids = _create_labeled_dataset(agent_session_factory)
    with agent_session_factory() as session:
        annotation = session.scalar(select(Annotation).where(Annotation.dataset_sample_id == sample_ids[0]))
        annotation.validation_status = "invalid"
        session.get(DatasetSample, sample_ids[0]).annotation_status = "invalid"
        session.commit()

    response = client.post(f"/datasets/{dataset_id}/convert-labeled")

    assert response.status_code == 409
    assert "no valid labeled" in response.json()["detail"]


def test_eight_row_labeling_loop_publishes_new_versions_without_moving_pinned_jobs(
    conversion_client,
    agent_session_factory: sessionmaker[Session],
):
    client, _ = conversion_client
    with agent_session_factory() as session:
        dataset = Dataset(
            name="llm-eight-row-loop",
            task="llm",
            status="created",
            format="openai_messages",
            class_schema={},
            source="llm_upload",
            organization_id="legacy-org",
            owner_user_id="legacy-admin",
            visibility="private",
            asset_role="working",
        )
        session.add(dataset)
        session.flush()
        samples: list[DatasetSample] = []
        for index in range(1, 9):
            status_value = "labeled" if index <= 6 else "invalid" if index == 7 else "unlabeled"
            sample = DatasetSample(
                dataset_id=dataset.id,
                file_uri=f"memory://datasets/{dataset.id}/row-{index}.json",
                checksum=f"eight-row-{index}",
                annotation_status=status_value,
            )
            session.add(sample)
            session.flush()
            samples.append(sample)
            if index <= 6:
                session.add(
                    Annotation(
                        dataset_sample_id=sample.id,
                        source="label_studio",
                        internal_payload={
                            "source_row_id": f"row-{index}",
                            "messages": [
                                {"role": "user", "content": f"问题 {index}"},
                                {"role": "assistant", "content": f"答案 {index}"},
                            ],
                        },
                        validation_status="valid",
                    )
                )
            elif index == 7:
                session.add(
                    Annotation(
                        dataset_sample_id=sample.id,
                        source="label_studio",
                        internal_payload={"issue": "assistant response must be non-empty"},
                        validation_status="invalid",
                    )
                )
        session.commit()
        dataset_id = dataset.id
        sample_ids = [sample.id for sample in samples]

    first = client.post(f"/datasets/{dataset_id}/convert-labeled")
    assert first.status_code == 200, first.text
    assert first.json()["valid_count"] == 6
    assert first.json()["invalid_count"] == 1
    assert first.json()["skipped_count"] == 1
    version_one_id = first.json()["version"]["id"]

    with agent_session_factory() as session:
        for index in (7, 8):
            sample = session.get(DatasetSample, sample_ids[index - 1])
            sample.annotation_status = "labeled"
            annotation = session.scalar(select(Annotation).where(Annotation.dataset_sample_id == sample.id))
            payload = {
                "source_row_id": f"row-{index}",
                "messages": [
                    {"role": "user", "content": f"问题 {index}"},
                    {"role": "assistant", "content": f"修正答案 {index}"},
                ],
            }
            if annotation is None:
                annotation = Annotation(
                    dataset_sample_id=sample.id,
                    source="label_studio",
                    internal_payload=payload,
                    validation_status="valid",
                )
            else:
                annotation.internal_payload = payload
                annotation.validation_status = "valid"
            session.add_all([sample, annotation])
        session.commit()

    second = client.post(f"/datasets/{dataset_id}/convert-labeled")
    assert second.status_code == 200, second.text
    assert second.json()["version"]["version"] == 2
    assert second.json()["valid_count"] == 8

    with agent_session_factory() as session:
        pinned = resolve_llm_dataset_version(session, dataset_id, version_one_id)
        latest = resolve_llm_dataset_version(session, dataset_id, None)
        assert pinned.id == version_one_id
        assert pinned.version == 1
        assert latest.version == 2
