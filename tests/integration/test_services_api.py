from collections.abc import Generator

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from visiox_api.routes.services import get_service_session, router
from visiox_db.models import TrainedModel, TrainingPipeline


def test_service_deployment_crud(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'visiox-services.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with session_factory() as session:
        pipeline = TrainingPipeline(
            id="pipeline-1",
            name="pepper-detect",
            task="detect",
            scale="n",
            params_template={},
            default_environment={},
            status="success",
        )
        model = TrainedModel(
            id="model-1",
            pipeline_id=pipeline.id,
            name="best.pt",
            version="best.pt",
            task="detect",
            artifact_uri="memory://models/trained/detect/best.pt",
            metrics={},
            status="ready",
        )
        session.add_all([pipeline, model])
        session.commit()

    app = FastAPI()
    app.include_router(router)

    def override_session() -> Generator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_service_session] = override_session
    payload = {
        "name": "pepper-online",
        "pipeline_id": "pipeline-1",
        "trained_model_id": "model-1",
        "model_name": "yolo26n.pt",
        "model_weight": "best.pt",
        "environment": "gpu-node-1",
        "instance_name": "pepper-prod-01",
        "resource_summary": "gpu节点_1 显卡1（3698.5M/12288.0M）",
        "config": {"pipeline_name": "pepper-detect"},
    }
    with TestClient(app) as client:
        created = client.post("/services", json=payload)
        listed = client.get("/services?pipeline_id=pipeline-1")
        service_id = created.json()["id"]
        stopped = client.patch(f"/services/{service_id}", json={"status": "stopped"})
        deleted = client.delete(f"/services/{service_id}")

    assert created.status_code == 201
    assert created.json()["instance_name"] == "pepper-prod-01"
    assert created.json()["status"] == "running"
    assert created.json()["endpoint"] == f"/services/{service_id}/predict/image"
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "stopped"
    assert deleted.status_code == 204


def test_service_deployment_rejects_model_from_another_pipeline(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'visiox-services-invalid.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with session_factory() as session:
        session.add_all(
            [
                TrainingPipeline(id="pipeline-1", name="one", task="detect", scale="n", status="success"),
                TrainingPipeline(id="pipeline-2", name="two", task="detect", scale="n", status="success"),
                TrainedModel(
                    id="model-2",
                    pipeline_id="pipeline-2",
                    name="best.pt",
                    version="best.pt",
                    task="detect",
                    artifact_uri="memory://models/trained/detect/best.pt",
                    status="ready",
                ),
            ]
        )
        session.commit()

    app = FastAPI()
    app.include_router(router)

    def override_session() -> Generator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_service_session] = override_session
    with TestClient(app) as client:
        response = client.post(
            "/services",
            json={
                "name": "invalid",
                "pipeline_id": "pipeline-1",
                "trained_model_id": "model-2",
                "model_name": "yolo26n.pt",
                "model_weight": "best.pt",
                "environment": "cpu",
                "instance_name": "invalid-test",
            },
        )

    assert response.status_code == 409
