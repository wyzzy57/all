from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import visiox_api.main as api_main
from visiox_api.main import create_app
from visiox_api.routes.training_observability import (
    get_training_observability_service,
    get_training_observability_session,
)
from visiox_common.settings import Settings
from visiox_db.models import Task, TrainingJob, TrainingPipeline


class FakeObservabilityService:
    def __init__(self, *, source_failed: bool = False) -> None:
        self.source_failed = source_failed
        self.scalar_keys: list[str] = []

    @property
    def availability(self) -> dict[str, dict[str, bool | str | None]]:
        return {
            "mlflow": {"available": not self.source_failed, "reason": "offline" if self.source_failed else None},
            "tensorboard": {"available": True, "reason": None},
            "progress": {"available": True, "reason": None},
            "artifacts": {"available": True, "reason": None},
        }

    def get_summary(self, job: TrainingJob, pipeline: TrainingPipeline, task: Task | None) -> dict[str, Any]:
        return {
            "job_id": job.id,
            "status": job.status,
            "pipeline": {"id": pipeline.id, "name": pipeline.name, "status": pipeline.status},
            "task": {"id": task.id if task else None, "status": task.status if task else None},
            "progress": {"current_epoch": 4, "total_epochs": 40, "percent": 10.0},
            "timing": {"started_at": "2026-07-13T00:00:00Z", "elapsed_seconds": 213.4, "eta_seconds": 1920.6},
            "environment": {"device": "cpu"},
            "latest_metrics": {"metrics.map50": 0.51},
            "available_scalar_keys": ["train.box_loss", "metrics.map50"],
            "available_histograms": {"weight": ["weights/model.0.conv.weight"], "gradient": []},
            "availability": self.availability,
        }

    def get_scalars(
        self,
        job: TrainingJob,
        keys: list[str],
        start_step: int | None,
        end_step: int | None,
        max_points: int | None,
    ) -> dict[str, Any]:
        del job, start_step, end_step, max_points
        self.scalar_keys = list(keys)
        return {"series": {key: [] for key in keys}, "availability": self.availability}

    def get_resources(
        self,
        job: TrainingJob,
        start_step: int | None,
        end_step: int | None,
        max_points: int | None,
    ) -> dict[str, Any]:
        del job, start_step, end_step, max_points
        return {"series": {"system.cpu_percent": []}, "availability": self.availability}

    def get_graph(self, job: TrainingJob) -> dict[str, Any]:
        del job
        return {"nodes": [], "edges": [], "availability": self.availability}

    def get_histogram(self, job: TrainingJob, kind: str, tag: str, step: int) -> dict[str, Any]:
        del job
        return {"kind": kind, "tag": tag, "step": step, "buckets": [], "availability": self.availability}


@pytest.fixture()
def session_factory(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'visiox-observability.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture()
def seeded_training_job(session_factory) -> TrainingJob:
    with session_factory() as session:
        pipeline = TrainingPipeline(
            id="pipeline-observability",
            name="native-observability",
            task="detect",
            scale="n",
            status="running",
        )
        task = Task(id="task-observability", task_type="TRAIN_MODEL", status="RUNNING", progress=10)
        job = TrainingJob(
            id="job-observability",
            pipeline_id=pipeline.id,
            task_id=task.id,
            status="running",
            params={},
            metrics={},
            started_at=datetime(2026, 7, 13, tzinfo=UTC),
        )
        session.add_all([pipeline, task, job])
        session.commit()
        return job


@pytest.fixture()
def client(session_factory, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient]:
    monkeypatch.setattr(api_main, "get_settings", lambda: Settings(seed_base_models_on_startup=False))
    app = create_app()

    def override_session() -> Generator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_training_observability_session] = override_session
    app.dependency_overrides[get_training_observability_service] = FakeObservabilityService
    with TestClient(app) as test_client:
        yield test_client


def test_observability_summary_returns_pipeline_job_and_availability(client, seeded_training_job) -> None:
    response = client.get(f"/training-jobs/{seeded_training_job.id}/observability/summary")

    assert response.status_code == 200
    assert response.json() == {
        "job_id": "job-observability",
        "pipeline_id": "pipeline-observability",
        "pipeline_name": "native-observability",
        "status": "running",
        "progress": {"current_epoch": 4, "total_epochs": 40, "percent": 10.0},
        "timing": {"started_at": "2026-07-13T00:00:00Z", "elapsed_seconds": 213.4, "eta_seconds": 1920.6},
        "environment": {"device": "cpu"},
        "latest_metrics": {"metrics.map50": 0.51},
        "available_scalar_keys": ["train.box_loss", "metrics.map50"],
        "available_histograms": {"weight": ["weights/model.0.conv.weight"], "gradient": []},
        "availability": {
            "mlflow": {"available": True, "reason": None},
            "tensorboard": {"available": True, "reason": None},
            "progress": {"available": True, "reason": None},
            "artifacts": {"available": True, "reason": None},
        },
    }


def test_observability_scalars_validates_max_points(client, seeded_training_job) -> None:
    response = client.get(
        f"/training-jobs/{seeded_training_job.id}/observability/scalars?keys=train.box_loss&max_points=9"
    )

    assert response.status_code == 422


def test_observability_scalars_splits_comma_separated_and_repeated_keys(client, seeded_training_job) -> None:
    service = FakeObservabilityService()
    client.app.dependency_overrides[get_training_observability_service] = lambda: service

    response = client.get(
        f"/training-jobs/{seeded_training_job.id}/observability/scalars?keys=%20train.box_loss%20,%20&keys=metrics.map50&max_points=10"
    )

    assert response.status_code == 200
    assert service.scalar_keys == ["train.box_loss", "metrics.map50"]


def test_observability_scalars_rejects_blank_keys(client, seeded_training_job) -> None:
    response = client.get(f"/training-jobs/{seeded_training_job.id}/observability/scalars?keys=%20,%20&max_points=10")

    assert response.status_code == 422
    assert response.json()["detail"] == "At least one scalar key is required"


def test_observability_graph_returns_404_for_missing_job(client) -> None:
    response = client.get("/training-jobs/missing/observability/graph")

    assert response.status_code == 404
    assert response.json()["detail"] == "Training job not found"


def test_observability_summary_returns_404_for_missing_pipeline(client, session_factory) -> None:
    with session_factory() as session:
        job = TrainingJob(
            id="job-missing-pipeline",
            pipeline_id="pipeline-missing",
            status="running",
            params={},
            metrics={},
        )
        session.add(job)
        session.commit()

    response = client.get(f"/training-jobs/{job.id}/observability/summary")

    assert response.status_code == 404
    assert response.json()["detail"] == "Pipeline not found"


def test_observability_histogram_requires_weight_or_gradient_kind(client, seeded_training_job) -> None:
    response = client.get(
        f"/training-jobs/{seeded_training_job.id}/observability/histograms?kind=activation&tag=weights.layer&step=1"
    )

    assert response.status_code == 422


def test_observability_source_failure_remains_http_200(client, seeded_training_job) -> None:
    client.app.dependency_overrides[get_training_observability_service] = lambda: FakeObservabilityService(source_failed=True)

    response = client.get(
        f"/training-jobs/{seeded_training_job.id}/observability/scalars?keys=train.box_loss&max_points=10"
    )

    assert response.status_code == 200
    assert response.json()["series"] == {"train.box_loss": []}
    assert response.json()["availability"]["mlflow"] == {"available": False, "reason": "offline"}
