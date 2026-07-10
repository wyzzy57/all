from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from visiox_api.main import create_app
from visiox_api.routes.pipeline_inference import (
    PipelineInferenceResult,
    _normalize_ultralytics_device,
    _normalize_weight_name,
    get_pipeline_inference_session,
    get_pipeline_inference_storage,
    get_pipeline_predictor,
)
from visiox_db.models import BaseModel, Dataset, TrainedModel, TrainingPipeline
from visiox_storage.client import InMemoryObjectStorageClient


class FakePipelinePredictor:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def predict(self, *, model_path: Path, image_path: Path, environment: str) -> PipelineInferenceResult:
        self.calls.append(
            {
                "model_bytes": model_path.read_bytes(),
                "image_exists": image_path.exists(),
                "environment": environment,
            }
        )
        return PipelineInferenceResult(
            predictions=[{"label": "defect", "score": 0.91, "box": [1, 2, 30, 40]}],
            annotated_image_bytes=b"fake-png",
            content_type="image/png",
        )


def test_pipeline_predict_image_uses_selected_trained_weight_and_environment(tmp_path):
    database_path = tmp_path / "visiox-inference.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database_path}")
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    storage = InMemoryObjectStorageClient()
    predictor = FakePipelinePredictor()

    image_path = tmp_path / "sample.png"
    Image.new("RGB", (16, 16), "white").save(image_path)
    model_path = tmp_path / "best.pt"
    model_path.write_bytes(b"trained-weight")
    artifact_uri = storage.put_file("models", "trained/model-1/best.pt", model_path)

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
        dataset = Dataset(
            name="detect-dataset",
            task="detect",
            status="validated",
            class_schema={"names": ["defect"]},
            sample_count=1,
            annotation_count=1,
            source="upload",
        )
        session.add_all([base, dataset])
        session.flush()
        pipeline = TrainingPipeline(
            name="trained-pipeline",
            task="detect",
            scale="n",
            base_model_id=base.id,
            dataset_id=dataset.id,
            params_template={},
            default_environment={},
            status="success",
        )
        session.add(pipeline)
        session.flush()
        trained = TrainedModel(
            pipeline_id=pipeline.id,
            training_job_id="job-1",
            name="trained-pipeline-best",
            version="best",
            task="detect",
            artifact_uri=artifact_uri,
            metrics={},
            status="ready",
        )
        session.add(trained)
        session.commit()
        pipeline_id = pipeline.id
        trained_id = trained.id

    app = create_app()

    def override_session() -> Generator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_pipeline_inference_session] = override_session
    app.dependency_overrides[get_pipeline_inference_storage] = lambda: storage
    app.dependency_overrides[get_pipeline_predictor] = lambda: predictor

    with TestClient(app) as client:
        with image_path.open("rb") as image_file:
            response = client.post(
                f"/pipelines/{pipeline_id}/predict/image",
                data={"model_weight": trained_id, "environment": "gpu-node-1"},
                files={"file": ("sample.png", image_file, "image/png")},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["model_weight"] == trained_id
    assert body["environment"] == "gpu-node-1"
    assert body["predictions"] == [{"label": "defect", "score": 0.91, "box": [1, 2, 30, 40]}]
    assert body["result_image"].startswith("data:image/png;base64,")
    assert predictor.calls == [
        {
            "model_bytes": b"trained-weight",
            "image_exists": True,
            "environment": "gpu-node-1",
        }
    ]


def test_normalize_ultralytics_device_accepts_cpu_and_gpu_aliases():
    assert _normalize_ultralytics_device("") == "cpu"
    assert _normalize_ultralytics_device("cpu") == "cpu"
    assert _normalize_ultralytics_device("0") == "0"
    assert _normalize_ultralytics_device("0,1") == "0,1"
    assert _normalize_ultralytics_device("gpu-node-1") == "1"
    assert _normalize_ultralytics_device("CUDA:0") == "0"


def test_normalize_weight_name_accepts_ultralytics_weight_filenames():
    assert _normalize_weight_name("best") == "best.pt"
    assert _normalize_weight_name("last") == "last.pt"
    assert _normalize_weight_name("best.pt") == "best.pt"
    assert _normalize_weight_name("base") == "base"
