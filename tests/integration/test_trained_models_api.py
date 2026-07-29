from collections.abc import Generator
from types import SimpleNamespace

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from visiox_api.dependencies.auth import get_current_user
from visiox_api.main import create_app
from visiox_api.routes.trained_models import get_trained_model_session
from visiox_db.models import TrainedModel
from tests.integration.ownership_test_support import install_legacy_ownership


LEGACY_TEST_ACTOR = SimpleNamespace(id="legacy-admin", organization_id="legacy-org", role="admin")


def test_trained_models_list_supports_filters_and_cors_preflight(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'visiox-trained-models.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    install_legacy_ownership(session_factory)
    with session_factory() as session:
        session.add_all(
            [
                TrainedModel(
                    name="detect-ready",
                    version="v1",
                    task="detect",
                    artifact_uri="memory://models/trained/detect/best.pt",
                    metrics={"mAP50": 0.9},
                    status="ready",
                ),
                TrainedModel(
                    name="segment-failed",
                    version="v1",
                    task="segment",
                    artifact_uri="memory://models/trained/segment/best.pt",
                    metrics={},
                    status="failed",
                ),
            ]
        )
        session.commit()

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: LEGACY_TEST_ACTOR

    def override_session() -> Generator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_trained_model_session] = override_session
    with TestClient(app) as client:
        preflight = client.options(
            "/trained-models",
            headers={
                "Origin": "http://127.0.0.1:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        response = client.get("/trained-models?task=detect&status=ready")

    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "detect-ready"


def test_trained_model_deployment_name_can_be_marked_without_renaming_weight(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'visiox-trained-model-mark.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    install_legacy_ownership(session_factory)
    with session_factory() as session:
        model = TrainedModel(
            id="model-1",
            name="best.pt",
            version="best.pt",
            task="detect",
            artifact_uri="memory://models/trained/detect/best.pt",
            metrics={"mAP50": 0.9},
            status="ready",
        )
        session.add(model)
        session.commit()

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: LEGACY_TEST_ACTOR

    def override_session() -> Generator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_trained_model_session] = override_session
    with TestClient(app) as client:
        response = client.patch("/trained-models/model-1", json={"deployment_name": "best_model"})
        invalid = client.patch("/trained-models/model-1", json={"deployment_name": "bad/name"})

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "best.pt"
    assert body["version"] == "best.pt"
    assert body["metrics"]["mAP50"] == 0.9
    assert body["metrics"]["deployment_name"] == "best_model"
    assert invalid.status_code == 422
