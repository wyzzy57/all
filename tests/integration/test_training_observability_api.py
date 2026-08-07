from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
import json
from pathlib import Path
from types import SimpleNamespace
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
    _parse_minio_uri,
    get_training_observability_service,
    get_training_observability_session,
)
from visiox_api.dependencies.auth import get_current_user
from visiox_common.settings import Settings
from visiox_api.services.training_observability import TrainingObservabilityService
from visiox_db.models import Task, TrainingJob, TrainingJobAttempt, TrainingPipeline
from tests.integration.ownership_test_support import install_legacy_ownership


class FakeObservabilityService:
    def __init__(self, *, source_failed: bool = False) -> None:
        self.source_failed = source_failed
        self.scalar_keys: list[str] = []
        self.observed_attempts: dict[str, int | None] = {}

    def _observe_attempt(self, endpoint: str, job: TrainingJob) -> None:
        self.observed_attempts[endpoint] = getattr(job, "attempt_number", None)

    def for_engine(self, engine: str):
        service = self

        class Adapter:
            def summary(self, job, pipeline, task):
                return service.get_summary(job, pipeline, task)

            def scalars(self, job, keys, start_step, end_step, max_points):
                return service.get_scalars(job, keys, start_step, end_step, max_points, engine=engine)

            def resources(self, job, start_step, end_step, max_points):
                return service.get_resources(job, start_step, end_step, max_points, engine=engine)

            def analysis(self, job):
                return service.get_analysis(job, engine=engine)

            def artifacts(self, job):
                return service.get_artifacts(job)

        return Adapter()

    def for_pipeline(self, pipeline: TrainingPipeline):
        return self.for_engine(pipeline.framework or pipeline.engine)

    @property
    def availability(self) -> dict[str, dict[str, bool | str | None]]:
        return {
            "mlflow": {"available": not self.source_failed, "reason": "offline" if self.source_failed else None},
            "tensorboard": {"available": True, "reason": None},
            "visualdl": {"available": False, "reason": "not configured"},
            "progress": {"available": True, "reason": None},
            "resources": {"available": True, "reason": None},
            "logs": {"available": True, "reason": None},
            "artifacts": {"available": True, "reason": None},
        }

    def get_summary(self, job: TrainingJob, pipeline: TrainingPipeline, task: Task | None) -> dict[str, Any]:
        self._observe_attempt("summary", job)
        return {
            "job_id": job.id,
            "engine": pipeline.engine,
            "status": job.status,
            "pipeline": {"id": pipeline.id, "name": pipeline.name, "status": pipeline.status},
            "task": {"id": task.id if task else None, "status": task.status if task else None},
            "progress": {"current_epoch": 4, "total_epochs": 40, "percent": 10.0},
            "timing": {"started_at": "2026-07-13T00:00:00Z", "elapsed_seconds": 213.4, "eta_seconds": 1920.6},
            "environment": {"device": "cpu"},
            "latest_metrics": {"metrics.map50": 0.51},
            "available_scalar_keys": ["train.box_loss", "metrics.map50"],
            "secondary_actions": [
                {"source": "tensorboard", "url": "http://tensorboard.test"}
            ],
            "availability": self.availability,
        }

    def get_scalars(
        self,
        job: TrainingJob,
        keys: list[str],
        start_step: int | None,
        end_step: int | None,
        max_points: int | None,
        *,
        engine: str = "yolo26",
    ) -> dict[str, Any]:
        self._observe_attempt("scalars", job)
        del start_step, end_step, max_points, engine
        self.scalar_keys = list(keys)
        return {"series": {key: [] for key in keys}, "availability": self.availability}

    def get_resources(
        self,
        job: TrainingJob,
        start_step: int | None,
        end_step: int | None,
        max_points: int | None,
        *,
        engine: str = "yolo26",
    ) -> dict[str, Any]:
        self._observe_attempt("resources", job)
        del start_step, end_step, max_points, engine
        return {"series": {"system.cpu_percent": []}, "availability": self.availability}

    def get_analysis(self, job: TrainingJob, *, engine: str = "yolo26") -> dict[str, Any]:
        self._observe_attempt("analysis", job)
        del engine
        return {"findings": [], "availability": self.availability}

    def get_artifacts(self, job: TrainingJob) -> dict[str, Any]:
        self._observe_attempt("artifacts", job)
        return {"items": [], "availability": {"artifacts": {"available": True, "reason": None}}}


class RouteMlflowClient:
    def search_experiments(self) -> list[object]:
        return [SimpleNamespace(experiment_id="1", lifecycle_stage="active")]

    def search_runs(self, *, experiment_ids: list[str], filter_string: str) -> list[object]:
        assert experiment_ids == ["1"]
        assert "job-observability" in filter_string
        return [
            SimpleNamespace(
                info=SimpleNamespace(run_id="run-1"),
                data=SimpleNamespace(metrics={"train/box_loss": 0.8}),
            )
        ]

    def get_metric_history(self, run_id: str, key: str) -> list[object]:
        assert run_id == "run-1"
        return [SimpleNamespace(step=2, value=0.8, timestamp=2_000)] if key == "train/box_loss" else []


class RouteEventAccumulator:
    def __init__(self) -> None:
        self.reload_count = 0

    def Reload(self) -> RouteEventAccumulator:
        self.reload_count += 1
        return self

    def Tags(self) -> dict[str, list[str]]:
        return {
            "scalars": ["val/box_loss", "system.cpu_percent", "train/images_per_second"],
            "histograms": ["weights/model.0.conv.weight"],
        }

    def Scalars(self, tag: str) -> list[object]:
        assert tag == "val/box_loss"
        return [SimpleNamespace(step=2, value=0.7, wall_time=2.0)]

    def Graph(self) -> object:
        return SimpleNamespace(node=[])


@pytest.fixture()
def session_factory(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'visiox-observability.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    install_legacy_ownership(factory)
    return factory


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
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id="legacy-admin", organization_id="legacy-org", role="admin"
    )
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def real_service_client(
    session_factory,
    seeded_training_job: TrainingJob,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[tuple[TestClient, TrainingObservabilityService, list[RouteEventAccumulator]]]:
    settings = Settings(
        _env_file=None,
        seed_base_models_on_startup=False,
        training_runs_root=tmp_path,
        mlflow_tracking_uri=(tmp_path / "mlruns").as_uri(),
        observability_event_cache_size=2,
    )
    (tmp_path / "mlruns").mkdir()
    run_path = tmp_path / "runs" / f"job-{seeded_training_job.id}"
    run_path.mkdir(parents=True)
    (run_path / "events.out.tfevents.1").touch()
    (run_path / "visiox-progress.json").write_text(
        json.dumps(
            {
                "progress": {"current_epoch": 2, "total_epochs": 10, "percent": 20.0},
                "timing": {"elapsed_seconds": 12.5, "eta_seconds": 50.0},
                "environment": {"device": "cpu"},
                "latest_metrics": {"metrics/mAP50(B)": 0.55},
                "resources": [
                    {"step": 2, "timestamp": 2.0, "system.cpu_percent": 35.0},
                ],
            }
        ),
        encoding="utf-8",
    )
    accumulators: list[RouteEventAccumulator] = []

    def create_accumulator(_: str) -> RouteEventAccumulator:
        accumulator = RouteEventAccumulator()
        accumulators.append(accumulator)
        return accumulator

    service = TrainingObservabilityService(
        settings,
        mlflow_client_factory=lambda _: RouteMlflowClient(),
        event_accumulator_factory=create_accumulator,
    )
    monkeypatch.setattr(api_main, "get_settings", lambda: settings)
    app = create_app()

    def override_session() -> Generator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_training_observability_session] = override_session
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id="legacy-admin", organization_id="legacy-org", role="admin"
    )
    app.state.training_observability_service = service
    with TestClient(app) as test_client:
        test_client.app.state.training_observability_service = service
        yield test_client, service, accumulators


def test_observability_summary_returns_pipeline_job_and_availability(client, seeded_training_job) -> None:
    response = client.get(f"/training-jobs/{seeded_training_job.id}/observability/summary")

    assert response.status_code == 200
    assert response.json() == {
        "job_id": "job-observability",
        "engine": "yolo26",
        "pipeline_id": "pipeline-observability",
        "pipeline_name": "native-observability",
        "status": "running",
        "progress": {"current_epoch": 4, "total_epochs": 40, "percent": 10.0},
        "timing": {"started_at": "2026-07-13T00:00:00Z", "elapsed_seconds": 213.4, "eta_seconds": 1920.6},
        "environment": {"device": "cpu"},
        "latest_metrics": {"metrics.map50": 0.51},
        "available_scalar_keys": ["train.box_loss", "metrics.map50"],
        "secondary_actions": [
            {"source": "tensorboard", "url": "http://tensorboard.test"}
        ],
        "availability": {
            "mlflow": {"available": True, "reason": None},
            "tensorboard": {"available": True, "reason": None},
            "visualdl": {"available": False, "reason": "not configured"},
            "progress": {"available": True, "reason": None},
            "resources": {"available": True, "reason": None},
            "logs": {"available": True, "reason": None},
            "artifacts": {"available": True, "reason": None},
        },
    }


def test_real_service_summary_flows_through_route_and_reuses_event_cache_across_requests(
    real_service_client,
    seeded_training_job: TrainingJob,
) -> None:
    client, _, accumulators = real_service_client

    summary_response = client.get(f"/training-jobs/{seeded_training_job.id}/observability/summary")
    graph_response = client.get(f"/training-jobs/{seeded_training_job.id}/observability/graph")
    resources_response = client.get(f"/training-jobs/{seeded_training_job.id}/observability/resources?max_points=10")
    scalar_response = client.get(
        f"/training-jobs/{seeded_training_job.id}/observability/scalars?keys=train.box_loss,val.box_loss&max_points=10"
    )

    assert summary_response.status_code == 200
    assert summary_response.json()["progress"] == {"current_epoch": 2, "total_epochs": 10, "percent": 20.0}
    timing = summary_response.json()["timing"]
    assert timing["started_at"].startswith("2026-07-13T00:00:00")
    assert timing["elapsed_seconds"] == 12.5
    assert timing["eta_seconds"] == 50.0
    assert summary_response.json()["latest_metrics"] == {
        "train/box_loss": 0.8,
        "metrics/mAP50(B)": 0.55,
    }
    assert summary_response.json()["available_scalar_keys"] == ["metrics.map50", "train.box_loss", "val.box_loss"]
    assert "available_histograms" not in summary_response.json()
    assert graph_response.status_code == 404
    assert resources_response.status_code == 200
    assert resources_response.json()["series"]["system.cpu_percent"] == [
        {
            "canonical_name": "system.cpu.utilization",
            "raw_name": "system.cpu_percent",
            "unit": "percent",
            "split": None,
            "step": 2.0,
            "epoch": None,
            "value": 35.0,
            "timestamp": 2.0,
            "source": "progress",
        },
    ]
    assert scalar_response.status_code == 200
    train_point = scalar_response.json()["series"]["train.box_loss"][0]
    assert train_point["canonical_name"] == "loss.box"
    assert train_point["raw_name"] == "train/box_loss"
    assert train_point["source"] == "mlflow"
    assert len(accumulators) == 1
    assert accumulators[0].reload_count == 1


def test_application_service_cache_evicts_across_requests_at_configured_size(
    real_service_client,
    seeded_training_job: TrainingJob,
    session_factory,
    tmp_path: Path,
) -> None:
    client, service, accumulators = real_service_client
    service.settings.observability_event_cache_size = 1
    second_job_id = "job-observability-2"
    with session_factory() as session:
        pipeline = TrainingPipeline(
            id="pipeline-observability-2",
            name="native-observability-2",
            task="detect",
            scale="n",
            status="running",
        )
        job = TrainingJob(
            id=second_job_id,
            pipeline_id=pipeline.id,
            status="running",
            params={},
            metrics={"observability": {"mlflow_run_name": "job-observability"}},
        )
        session.add_all([pipeline, job])
        session.commit()
    second_run_path = tmp_path / "runs" / f"job-{second_job_id}"
    second_run_path.mkdir(parents=True)
    (second_run_path / "events.out.tfevents.1").touch()
    (second_run_path / "visiox-progress.json").write_text(json.dumps({"resources": []}), encoding="utf-8")

    assert client.get(f"/training-jobs/{seeded_training_job.id}/observability/summary").status_code == 200
    assert client.get(f"/training-jobs/{second_job_id}/observability/summary").status_code == 200
    assert client.get(f"/training-jobs/{seeded_training_job.id}/observability/summary").status_code == 200

    assert len(accumulators) == 3
    assert len(service._event_accumulators) == 1


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
    assert response.json()["detail"] == "Not Found"


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


def test_observability_unknown_framework_returns_explicit_422(
    real_service_client, seeded_training_job, session_factory
) -> None:
    client, _, _ = real_service_client
    with session_factory() as session:
        pipeline = session.get(TrainingPipeline, seeded_training_job.pipeline_id)
        assert pipeline is not None
        pipeline.framework = "mystery"
        pipeline.adapter_key = "mystery.object_detection.v1"
        session.commit()

    response = client.get(
        f"/training-jobs/{seeded_training_job.id}/observability/summary"
    )

    assert response.status_code == 422
    assert "Unknown adapter" in response.json()["detail"]


def test_observability_unknown_adapter_version_returns_explicit_422(
    real_service_client, seeded_training_job, session_factory
) -> None:
    client, _, _ = real_service_client
    with session_factory() as session:
        pipeline = session.get(TrainingPipeline, seeded_training_job.pipeline_id)
        assert pipeline is not None
        pipeline.framework = "ultralytics"
        pipeline.adapter_key = "ultralytics.object_detection.v1"
        pipeline.adapter_version = "9.9.9"
        session.commit()

    response = client.get(
        f"/training-jobs/{seeded_training_job.id}/observability/summary"
    )

    assert response.status_code == 422
    assert "Unknown version" in response.json()["detail"]


def test_observability_histogram_is_not_part_of_native_contract(client, seeded_training_job) -> None:
    response = client.get(
        f"/training-jobs/{seeded_training_job.id}/observability/histograms?kind=activation&tag=weights.layer&step=1"
    )

    assert response.status_code == 404


def test_observability_source_failure_remains_http_200(client, seeded_training_job) -> None:
    client.app.dependency_overrides[get_training_observability_service] = lambda: FakeObservabilityService(source_failed=True)

    response = client.get(
        f"/training-jobs/{seeded_training_job.id}/observability/scalars?keys=train.box_loss&max_points=10"
    )

    assert response.status_code == 200
    assert response.json()["series"] == {"train.box_loss": []}
    assert response.json()["availability"]["mlflow"] == {"available": False, "reason": "offline"}


def test_llm_analysis_and_artifacts_routes_use_authorized_job_context(client, seeded_training_job) -> None:
    analysis = client.get(f"/training-jobs/{seeded_training_job.id}/observability/analysis")
    artifacts = client.get(f"/training-jobs/{seeded_training_job.id}/observability/artifacts")

    assert analysis.status_code == 200
    assert analysis.json()["findings"] == []
    assert artifacts.status_code == 200
    assert artifacts.json()["items"] == []


def test_observability_attempt_parameter_scopes_every_endpoint_and_rejects_foreign_attempt(
    client,
    session_factory,
    seeded_training_job: TrainingJob,
) -> None:
    service = FakeObservabilityService()
    client.app.dependency_overrides[get_training_observability_service] = lambda: service
    with session_factory() as session:
        selected = TrainingJobAttempt(
            id="attempt-selected",
            training_job_id=seeded_training_job.id,
            attempt_number=2,
            status="failed",
            launch_spec={},
            launch_spec_checksum="a" * 64,
        )
        foreign_job = TrainingJob(
            id="job-foreign-attempt",
            pipeline_id=seeded_training_job.pipeline_id,
            status="running",
            params={},
            metrics={},
        )
        foreign_attempt = TrainingJobAttempt(
            id="attempt-foreign",
            training_job_id=foreign_job.id,
            attempt_number=1,
            status="running",
            launch_spec={},
            launch_spec_checksum="b" * 64,
        )
        session.add_all([selected, foreign_job, foreign_attempt])
        session.commit()

    base = f"/training-jobs/{seeded_training_job.id}/observability"
    summary_response = client.get(f"{base}/summary?attempt_id=attempt-selected")
    assert summary_response.status_code == 200
    assert summary_response.json()["status"] == "failed"
    assert client.get(f"{base}/scalars?attempt_id=attempt-selected&keys=train.box_loss").status_code == 200
    assert client.get(f"{base}/resources?attempt_id=attempt-selected").status_code == 200
    assert client.get(f"{base}/analysis?attempt_id=attempt-selected").status_code == 200
    assert client.get(f"{base}/artifacts?attempt_id=attempt-selected").status_code == 200

    assert service.observed_attempts == {
        "summary": 2,
        "scalars": 2,
        "resources": 2,
        "analysis": 2,
        "artifacts": 2,
    }
    assert client.get(f"{base}/summary?attempt_id=attempt-foreign").status_code == 404


def test_observability_rejects_same_organization_user_without_view_permission(
    client,
    seeded_training_job,
) -> None:
    client.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id="member-without-grant",
        organization_id="legacy-org",
        role="member",
    )

    response = client.get(f"/training-jobs/{seeded_training_job.id}/observability/summary")

    assert response.status_code == 403


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        ("minio://training-artifacts/jobs/job-1/best.pt", ("training-artifacts", "jobs/job-1/best.pt")),
        ("minio://missing-object", None),
        ("minio:///missing-bucket", None),
        ("minio://training-artifacts/", None),
        ("https://example.test/best.pt", None),
        (None, None),
    ],
)
def test_parse_minio_uri_rejects_incomplete_artifact_locations(uri, expected) -> None:
    assert _parse_minio_uri(uri) == expected
