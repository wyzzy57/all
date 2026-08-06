from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timezone
import hashlib
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
    get_pipeline_evaluation_runtime,
)
from visiox_api.services.framework_adapters import (
    FrameworkAdapterCatalog,
    get_framework_adapter_catalog,
)
from visiox_api.services.pipeline_evaluation import PaddleXEvaluationRuntimeRequest
from visiox_common.settings import Settings
from visiox_db.models import BaseModel, Dataset, DatasetVersion, TrainedModel, TrainingPipeline
from visiox_storage.client import InMemoryObjectStorageClient
from tests.integration.ownership_test_support import install_legacy_ownership


LEGACY_TEST_ACTOR = SimpleNamespace(id="legacy-admin", organization_id="legacy-org", role="admin")


class NoDownloadStorage(InMemoryObjectStorageClient):
    def __init__(self) -> None:
        super().__init__()
        self.downloads = 0

    def get_file(self, bucket, object_name, destination):
        self.downloads += 1
        raise AssertionError("preflight failure must not download artifacts")


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
            framework="ultralytics",
            adapter_key="ultralytics.object_detection.v1",
            model_family="yolo26",
            model_format="pt",
            artifact_role="last_weights",
            artifact_uri=artifact_uri,
            artifact_manifest={
                "adapter_key": "ultralytics.object_detection.v1",
                "adapter_version": "1.0.0",
            },
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


class FakePaddleXEvaluationRuntime:
    def __init__(self) -> None:
        self.requests: list[PaddleXEvaluationRuntimeRequest] = []

    def run(self, request: PaddleXEvaluationRuntimeRequest) -> None:
        self.requests.append(request)
        request.result_path.write_text(
            '{"bbox_mAP":0.42,"bbox_mAP_50":0.71,"bbox_mAP_75":0.39,'
            '"bbox_mAP_s":0.12,"bbox_mAP_m":0.31,"bbox_mAP_l":0.58,'
            '"bbox_AR_100":0.66}',
            encoding="utf-8",
        )


def test_paddlex_pipeline_evaluation_uses_pinned_runtime_and_canonical_metrics(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "visiox-paddlex-evaluation.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database_path}")
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    install_legacy_ownership(session_factory)
    storage = InMemoryObjectStorageClient()
    runtime = FakePaddleXEvaluationRuntime()
    digest = f"registry.example/visiox/paddlex-training@sha256:{'c' * 64}"
    catalog = FrameworkAdapterCatalog(Settings(paddlex_training_image_digest=digest))
    weight_path = tmp_path / "best_model.pdparams"
    weight_path.write_bytes(b"paddlex-dynamic")
    weight_checksum = hashlib.sha256(weight_path.read_bytes()).hexdigest()
    artifact_uri = storage.put_file("models", "trained/job-1/best_model.pdparams", weight_path)

    with session_factory() as session:
        dataset = Dataset(
            name="paddlex-eval-dataset",
            task="detect",
            format="coco",
            status="validated",
            class_schema={"names": ["defect"]},
            sample_count=1,
            annotation_count=1,
            source="upload",
        )
        session.add(dataset)
        session.flush()
        session.add(
            DatasetVersion(
                dataset_id=dataset.id,
                version=1,
                status="published",
                format="coco",
                object_uri="memory://datasets/paddlex-eval",
                manifest_uri="memory://datasets/paddlex-eval/manifest.json",
                manifest_checksum="a" * 64,
                total_count=1,
                valid_count=1,
                size_bytes=1,
                published_at=datetime.now(timezone.utc),
            )
        )
        pipeline = TrainingPipeline(
            name="paddlex-eval-pipeline",
            engine="paddlex",
            task="detect",
            scale="s",
            task_kind="object_detection",
            framework="paddlex",
            adapter_key="paddlex.object_detection.v1",
            adapter_version="1.0.0",
            model_family="PP-YOLOE",
            recipe={"model": {"runtime_id": "PP-YOLOE_plus-S"}},
            dataset_id=dataset.id,
            params_template={},
            default_environment={},
            status="success",
        )
        session.add(pipeline)
        session.flush()
        trained = TrainedModel(
            pipeline_id=pipeline.id,
            name="best_model.pdparams",
            display_name="Best dynamic weights",
            version="attempt-1",
            task="detect",
            framework="paddlex",
            adapter_key="paddlex.object_detection.v1",
            model_family="PP-YOLOE",
            model_format="pdparams",
            artifact_role="best_dynamic_weights",
            artifact_uri=artifact_uri,
            checksum=weight_checksum,
            size_bytes=weight_path.stat().st_size,
            artifact_manifest={
                "adapter_key": "paddlex.object_detection.v1",
                "adapter_version": "1.0.0",
                "model_identity": {
                    "model_family": "PP-YOLOE",
                    "runtime_model_id": "PP-YOLOE_plus-S",
                },
                "role_artifacts": [
                    {"path": "best_model/best_model.pdparams", "artifact_type": "model_weight"}
                ],
            },
            metrics={},
            status="ready",
        )
        session.add(trained)
        session.commit()
        pipeline_id = pipeline.id

    def fake_export(
        session, storage, dataset_id, dataset_version_id, output_dir, *, runtime_model_id
    ):
        del session, storage, dataset_id, dataset_version_id
        assert runtime_model_id == "PP-YOLOE_plus-S"
        output_dir.mkdir(parents=True)
        (output_dir / "dataset-manifest.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        "visiox_api.routes.pipeline_evaluation.export_paddlex_detection_dataset",
        fake_export,
    )
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: LEGACY_TEST_ACTOR

    def override_session() -> Generator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_pipeline_evaluation_session] = override_session
    app.dependency_overrides[get_pipeline_evaluation_storage] = lambda: storage
    app.dependency_overrides[get_pipeline_evaluation_runtime] = lambda: runtime
    app.dependency_overrides[get_framework_adapter_catalog] = lambda: catalog

    with TestClient(app) as client:
        response = client.post(
            f"/pipelines/{pipeline_id}/evaluate",
            json={"model_weight": "latest", "environment": "gpu-node-0"},
        )

    assert response.status_code == 200, response.text
    assert response.json()["score"] == 0.71
    assert response.json()["metrics"] == {
        "detection.map_50_95": 0.42,
        "detection.map_50": 0.71,
        "detection.map_75": 0.39,
        "detection.map_small": 0.12,
        "detection.map_medium": 0.31,
        "detection.map_large": 0.58,
        "detection.mar_100": 0.66,
    }
    assert len(runtime.requests) == 1
    request = runtime.requests[0]
    assert request.image_digest == digest
    assert "Global.mode=evaluate" in request.overrides
    assert "Global.dataset_dir=/workspace/dataset" in request.overrides
    assert "Evaluate.weight_path=/workspace/model/best_model.pdparams" in request.overrides
    assert request.config_path.endswith("PP-YOLOE_plus-S.yaml")
    assert request.device == "gpu:0"
    assert ("--gpus", "device=0") == request.command[
        request.command.index("--gpus") : request.command.index("--gpus") + 2
    ]
    assert "/tmp:rw,noexec,nosuid,size=1g" in request.command


def test_paddlex_evaluation_cpu_runtime_command_has_no_gpu_flag(tmp_path):
    request = PaddleXEvaluationRuntimeRequest(
        image_digest=f"registry.example/paddlex@sha256:{'a' * 64}",
        workspace=tmp_path,
        config_path="paddlex/config.yaml",
        overrides=("Global.mode=evaluate",),
        result_path=tmp_path / "output" / "evaluate_result.json",
        device="cpu",
    )

    assert "--gpus" not in request.command
    assert "/tmp:rw,noexec,nosuid,size=1g" in request.command


def test_evaluation_rejects_dataset_task_mismatch_without_download(tmp_path):
    database_path = tmp_path / "visiox-eval-task-mismatch.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database_path}")
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    install_legacy_ownership(session_factory)
    storage = NoDownloadStorage()
    with session_factory() as session:
        dataset = Dataset(
            name="classification-only",
            task="classify",
            status="validated",
            class_schema={"names": ["ok"]},
            sample_count=1,
            annotation_count=1,
            source="upload",
        )
        session.add(dataset)
        session.flush()
        pipeline = TrainingPipeline(
            name="detect-with-wrong-eval-data",
            task="detect",
            scale="n",
            dataset_id=dataset.id,
            status="success",
        )
        session.add(pipeline)
        session.commit()
        pipeline_id = pipeline.id

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: LEGACY_TEST_ACTOR

    def override_session() -> Generator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_pipeline_evaluation_session] = override_session
    app.dependency_overrides[get_pipeline_evaluation_storage] = lambda: storage
    with TestClient(app) as client:
        response = client.post(f"/pipelines/{pipeline_id}/evaluate", json={})

    assert response.status_code == 409
    assert storage.downloads == 0
