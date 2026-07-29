from __future__ import annotations

from collections.abc import Generator
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session, sessionmaker

import visiox_api.main as api_main
from visiox_api.dependencies.auth import get_current_user
from visiox_api.routes.label_projects import get_label_launch_cache, get_label_project_session
from visiox_common.settings import Settings, get_settings
from visiox_db.models import AuditLog, Dataset, LabelProject


ACTOR = SimpleNamespace(id="legacy-admin", organization_id="legacy-org", role="admin")


class FakeAsyncCache:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(self, key: str, value: str, *, ex: int, nx: bool) -> bool:
        assert ex == 60
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def getdel(self, key: str) -> str | None:
        return self.values.pop(key, None)


@pytest.fixture()
def launch_client(
    agent_session_factory: sessionmaker[Session],
) -> Generator[TestClient]:
    def override_session() -> Generator[Session]:
        with agent_session_factory() as session:
            yield session

    app = api_main.create_app()
    cache = FakeAsyncCache()
    app.dependency_overrides[get_current_user] = lambda: ACTOR
    app.dependency_overrides[get_label_project_session] = override_session
    app.dependency_overrides[get_label_launch_cache] = lambda: cache
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None,
        label_studio_public_url="http://127.0.0.1:8081",
    )
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _seed_label_project(session_factory: sessionmaker[Session]) -> str:
    with session_factory() as session:
        dataset = Dataset(
            name="managed-launch",
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
            external_project_id="77",
            sync_status="synced",
        )
        session.add(project)
        session.commit()
        return project.id


def test_launch_token_is_project_bound_and_one_time(
    launch_client: TestClient,
    agent_session_factory: sessionmaker[Session],
):
    project_id = _seed_label_project(agent_session_factory)

    launch = launch_client.post(f"/label-projects/{project_id}/launch")

    assert launch.status_code == 200, launch.text
    assert "admin" not in launch.text.lower()
    token = parse_qs(urlsplit(launch.json()["launch_url"]).query)["launch_token"][0]
    exchanged = launch_client.post(
        "/internal/label-studio/launch/exchange",
        json={"token": token},
    )
    replay = launch_client.post(
        "/internal/label-studio/launch/exchange",
        json={"token": token},
    )

    assert exchanged.status_code == 200
    assert exchanged.json() == {
        "destination": "/projects/77/data",
        "external_project_id": "77",
    }
    assert replay.status_code == 401

    with agent_session_factory() as session:
        audit = session.query(AuditLog).filter_by(action="label_project.launch").one()
        assert audit.actor_user_id == "legacy-admin"
        assert audit.resource_id == project_id
        assert audit.metadata_json["external_project_id"] == "77"


def test_private_project_launch_rejects_a_different_user(
    launch_client: TestClient,
    agent_session_factory: sessionmaker[Session],
):
    project_id = _seed_label_project(agent_session_factory)
    launch_client.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id="other-user",
        organization_id="legacy-org",
        role="member",
    )

    response = launch_client.post(f"/label-projects/{project_id}/launch")

    assert response.status_code == 403
