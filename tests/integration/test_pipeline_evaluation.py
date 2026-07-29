from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from visiox_api.dependencies.auth import get_current_user
from visiox_api.main import create_app
from visiox_api.routes import pipeline_evaluation
from visiox_api.routes.pipeline_evaluation import (
    PipelineEvaluationResult,
    get_pipeline_evaluation_session,
    get_pipeline_evaluation_storage,
    get_pipeline_evaluator,
)
from visiox_db.models import BaseModel, Dataset, TrainedModel, TrainingPipeline
from visiox_storage.client import InMemoryObjectStorageClient
from tests.integration.ownership_test_support import install_legacy_ownership


LEGACY_TEST_ACTOR = SimpleNamespace(id="legacy-admin", organization_id="legacy-org", role="admin")


class FakePipelineEvaluator:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def evaluate(self, *, model_path: Path, data_yaml_path: Path, environment: str, split: str) -> PipelineEvaluationResult:
        self.calls.append(
            {
                "model_bytes": model_path.read_bytes(),
                "data_yaml": data_yaml_path.read_text(encoding="utf-8"),
                "environment": environment,
                "split": split,
            }
        )
        return PipelineEvaluationResult(score=13.57, metrics={"metrics/mAP50(B)": 13.57})


def test_pipeline_evaluation_uses_custom_dataset_weight_and_environment(tmp_path, monkeypatch):
    database_path = tmp_path / "visiox-evaluation.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database_path}")
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    install_legacy_ownership(session_factory)
    storage = InMemoryObjectStorageClient()
    evaluator = FakePipelineEvaluator()

    weight_path = tmp_path / "last.pt"
    weight_path.write_bytes(b"last-weight")
    artifact_uri = storage.put_file("models", "trained/model-1/last.pt", weight_path)

    with session_factory() as session:
        base = BaseModel(
            family="yolo26",
            task="detect",
            scale="n",
            filename="yolo26n.pt",
            source_path="yolo26n.pt",
            local_uri="memory://models/base/yolo26n.pt",
            status="ready",
        )
        train_dataset = Dataset(
            name="train-dataset",
            task="detect",
            status="validated",
            class_schema={"names": ["defect"]},
            sample_count=1,
            annotation_count=1,
            source="upload",
        )
        custom_dataset = Dataset(
            name="custom-test",
            task="detect",
            status="created",
            class_schema={"names": ["defect"]},
            sample_count=1,
            annotation_count=1,
            source="upload",
        )
        session.add_all([base, train_dataset, custom_dataset])
        session.flush()
        pipeline = TrainingPipeline(
            name="detect-pipeline",
            task="detect",
            scale="n",
            base_model_id=base.id,
            dataset_id=train_dataset.id,
            params_template={},
            default_environment={},
            status="success",
        )
        session.add(pipeline)
        session.flush()
        trained = TrainedModel(
            pipeline_id=pipeline.id,
            training_job_id="job-1",
            name="last.pt",
            version="last.pt",
            task="detect",
            artifact_uri=artifact_uri,
            metrics={},
            status="ready",
        )
        session.add(trained)
        session.commit()
        pipeline_id = pipeline.id
        custom_dataset_id = custom_dataset.id

    def fake_export_dataset(session, storage, dataset_id: str, output_dir: Path):
        del session, storage, dataset_id
        (output_dir / "images" / "test").mkdir(parents=True)
        (output_dir / "labels" / "test").mkdir(parents=True)
        (output_dir / "data.yaml").write_text(
            "path: .\ntrain: images/train\nval: images/val\ntest: images/test\nnames:\n  0: defect\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(pipeline_evaluation, "export_yolo26_dataset", fake_export_dataset)

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: LEGACY_TEST_ACTOR

    def override_session() -> Generator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_pipeline_evaluation_session] = override_session
    app.dependency_overrides[get_pipeline_evaluation_storage] = lambda: storage
    app.dependency_overrides[get_pipeline_evaluator] = lambda: evaluator

    with TestClient(app) as client:
        response = client.post(
            f"/pipelines/{pipeline_id}/evaluate",
            json={
                "evaluation_set": "custom",
                "dataset_id": custom_dataset_id,
                "model_weight": "last.pt",
                "environment": "cpu",
            },
        )
        history_response = client.get(f"/pipelines/{pipeline_id}/evaluations")

    assert response.status_code == 200
    body = response.json()
    assert body["id"]
    assert body["dataset_id"] == custom_dataset_id
    assert body["model_weight"] == "last.pt"
    assert body["score"] == 13.57
    assert body["metrics"] == {"metrics/mAP50(B)": 13.57}
    assert history_response.status_code == 200
    history = history_response.json()
    assert history["total"] == 1
    assert history["items"][0]["id"] == body["id"]
    assert history["items"][0]["status"] == "completed"
    assert history["items"][0]["evaluation_set"] == "custom"
    assert history["items"][0]["dataset_id"] == custom_dataset_id
    assert history["items"][0]["model_weight"] == "last.pt"
    assert history["items"][0]["environment"] == "cpu"
    assert history["items"][0]["score"] == 13.57
    assert history["items"][0]["metrics"] == {"metrics/mAP50(B)": 13.57}
    assert len(evaluator.calls) == 1
    assert evaluator.calls[0]["model_bytes"] == b"last-weight"
    assert "test: images/test" in str(evaluator.calls[0]["data_yaml"])
    assert evaluator.calls[0]["split"] == "test"
    assert evaluator.calls[0]["environment"] == "cpu"
