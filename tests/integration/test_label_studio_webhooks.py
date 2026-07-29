from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import json

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

import visiox_api.main as api_main
from visiox_api.routes.label_webhooks import get_label_webhook_session
from visiox_common.settings import Settings, get_settings
from visiox_common.tasks import TaskStatus, TaskType
from visiox_db.models import Dataset, LabelProject, LabelSyncEvent, Task
from visiox_label_sync_worker.runner import queue_stale_label_projects


SECRET = "label-webhook-test-secret"


@pytest.fixture()
def webhook_client(
    agent_session_factory: sessionmaker[Session],
) -> Generator[TestClient]:
    def override_session() -> Generator[Session]:
        with agent_session_factory() as session:
            yield session

    app = api_main.create_app()
    app.dependency_overrides[get_label_webhook_session] = override_session
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None,
        label_studio_webhook_secret=SECRET,
    )
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _seed_project(session_factory: sessionmaker[Session], *, external_id: str = "42") -> str:
    with session_factory() as session:
        dataset = Dataset(
            name=f"webhook-{external_id}",
            task="llm",
            status="created",
            organization_id="legacy-org",
            owner_user_id="legacy-admin",
            visibility="private",
        )
        session.add(dataset)
        session.flush()
        project = LabelProject(
            dataset_id=dataset.id,
            provider="label_studio",
            external_project_id=external_id,
            sync_status="synced",
            last_sync_at=datetime.now(UTC),
        )
        session.add(project)
        session.commit()
        return project.id


def _signed_payload(payload: dict) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return body, {"x-label-studio-signature": f"sha256={signature}", "content-type": "application/json"}


def test_webhook_validates_signature_and_deduplicates_event(
    webhook_client: TestClient,
    agent_session_factory: sessionmaker[Session],
):
    project_id = _seed_project(agent_session_factory)
    body, headers = _signed_payload({"event_id": "event-1", "action": "ANNOTATION_UPDATED", "project": 42})

    first = webhook_client.post("/webhooks/label-studio", content=body, headers=headers)
    duplicate = webhook_client.post("/webhooks/label-studio", content=body, headers=headers)

    assert first.status_code == 202, first.text
    assert first.json()["duplicate"] is False
    assert duplicate.status_code == 202
    assert duplicate.json()["duplicate"] is True
    with agent_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(LabelSyncEvent)) == 1
        task = session.scalar(select(Task).where(Task.resource_id == project_id))
        assert task.task_type == TaskType.IMPORT_LABEL_STUDIO_ANNOTATION.value
        assert task.status == TaskStatus.QUEUED.value


def test_webhook_rejects_invalid_signature(
    webhook_client: TestClient,
    agent_session_factory: sessionmaker[Session],
):
    _seed_project(agent_session_factory)
    response = webhook_client.post(
        "/webhooks/label-studio",
        content=b'{"event_id":"event-2","project":42}',
        headers={"x-label-studio-signature": "sha256=bad", "content-type": "application/json"},
    )
    assert response.status_code == 401


def test_compensation_scan_queues_only_stale_projects_without_active_task(
    agent_session_factory: sessionmaker[Session],
):
    project_id = _seed_project(agent_session_factory, external_id="84")
    with agent_session_factory() as session:
        project = session.get(LabelProject, project_id)
        project.last_sync_at = datetime.now(UTC) - timedelta(hours=2)
        session.commit()
    with agent_session_factory() as session:
        assert queue_stale_label_projects(session, stale_after_seconds=300) == 1
        assert queue_stale_label_projects(session, stale_after_seconds=300) == 0
        task = session.scalar(select(Task).where(Task.resource_id == project_id))
        assert task.payload["compensation"] is True
