from collections.abc import Generator

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from visiox_api.main import create_app
from visiox_api.routes.trained_models import get_trained_model_session
from visiox_db.models import TrainedModel


def test_trained_models_list_supports_filters_and_cors_preflight(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'visiox-trained-models.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
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
