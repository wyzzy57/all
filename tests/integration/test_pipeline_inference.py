from __future__ import annotations

import base64
from collections.abc import Generator
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from alembic import command
from alembic.config import Config
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from visiox_api.dependencies.auth import get_current_user
from visiox_api.main import create_app
from visiox_api.routes.pipeline_inference import (
    PipelineInferenceResult,
    _normalize_ultralytics_device,
    _normalize_weight_name,
    get_pipeline_inference_session,
    get_pipeline_inference_storage,
    get_pipeline_predictor,
    get_pipeline_inference_runtime,
)
from visiox_api.services.framework_adapters import (
    FrameworkAdapterCatalog,
    get_framework_adapter_catalog,
)
from visiox_api.services.pipeline_inference import PaddleXInferenceRuntimeRequest
from visiox_api.services.pipeline_inference import (
    ResolvedPipelineModel,
    download_artifact,
    download_paddlex_static_bundle,
    resolve_pipeline_model,
)
from visiox_common.settings import Settings
from visiox_db.models import BaseModel, Dataset, TrainedModel, TrainingPipeline
from visiox_storage.client import InMemoryObjectStorageClient
from visiox_paddlex.results import (
    PaddleXResultError,
    read_evaluation_result,
    read_inference_result,
)
from tests.integration.ownership_test_support import install_legacy_ownership


LEGACY_TEST_ACTOR = SimpleNamespace(id="legacy-admin", organization_id="legacy-org", role="admin")


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
    install_legacy_ownership(session_factory)
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
            framework="ultralytics",
            adapter_key="ultralytics.object_detection.v1",
            model_family="yolo26",
            model_format="pt",
            artifact_role="best_weights",
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
        trained_id = trained.id

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: LEGACY_TEST_ACTOR

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


class FakePaddleXInferenceRuntime:
    def __init__(self) -> None:
        self.requests: list[PaddleXInferenceRuntimeRequest] = []
        self.model_files: dict[str, bytes] = {}

    def run(self, request: PaddleXInferenceRuntimeRequest) -> None:
        self.requests.append(request)
        self.model_files = {
            path.name: path.read_bytes() for path in request.model_dir.iterdir()
        }
        request.result_path.write_text(
            '{"boxes":[{"label":"defect","score":0.93,"coordinate":[1,2,15,16]}],'
            '"annotated_image":"annotated.jpg"}',
            encoding="utf-8",
        )
        Image.new("RGB", (16, 16), "white").save(
            request.output_dir / "annotated.jpg", format="JPEG"
        )


def test_paddlex_image_inference_uses_static_bundle_and_normalizes_result(tmp_path):
    database_path = tmp_path / "visiox-paddlex-inference.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database_path}")
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    install_legacy_ownership(session_factory)
    storage = InMemoryObjectStorageClient()
    runtime = FakePaddleXInferenceRuntime()
    digest = f"registry.example/visiox/paddlex-inference@sha256:{'d' * 64}"
    catalog = FrameworkAdapterCatalog(Settings(paddlex_inference_image_digest=digest))
    graph = tmp_path / "inference.json"
    params = tmp_path / "inference.pdiparams"
    graph.write_text("{}", encoding="utf-8")
    params.write_bytes(b"static-params")
    graph_checksum = hashlib.sha256(graph.read_bytes()).hexdigest()
    params_checksum = hashlib.sha256(params.read_bytes()).hexdigest()
    graph_uri = storage.put_file(
        "models", "trained/job-1/best_model/inference/inference.json", graph
    )
    storage.put_file(
        "models", "trained/job-1/best_model/inference/inference.pdiparams", params
    )
    image_path = tmp_path / "sample.jpg"
    Image.new("RGB", (16, 16), "white").save(image_path, format="JPEG")

    with session_factory() as session:
        pipeline = TrainingPipeline(
            name="paddlex-infer-pipeline",
            engine="paddlex",
            task="detect",
            scale="s",
            task_kind="object_detection",
            framework="paddlex",
            adapter_key="paddlex.object_detection.v1",
            adapter_version="1.0.0",
            model_family="PP-YOLOE",
            recipe={"model": {"runtime_id": "PP-YOLOE_plus-S"}},
            params_template={},
            default_environment={},
            status="success",
        )
        session.add(pipeline)
        session.flush()
        trained = TrainedModel(
            pipeline_id=pipeline.id,
            name="Best static inference",
            version="attempt-1",
            task="detect",
            framework="paddlex",
            adapter_key="paddlex.object_detection.v1",
            model_family="PP-YOLOE",
            model_format="json",
            artifact_role="best_static_inference",
            artifact_uri=graph_uri,
            checksum=graph_checksum,
            size_bytes=graph.stat().st_size,
            artifact_manifest={
                "adapter_key": "paddlex.object_detection.v1",
                "adapter_version": "1.0.0",
                "model_identity": {
                    "model_family": "PP-YOLOE",
                    "runtime_model_id": "PP-YOLOE_plus-S",
                },
                "role_artifacts": [
                    {
                        "path": "best_model/inference/inference.json",
                        "artifact_type": "metrics",
                        "checksum_sha256": graph_checksum,
                        "size_bytes": graph.stat().st_size,
                    },
                    {
                        "path": "best_model/inference/inference.pdiparams",
                        "artifact_type": "training_output",
                        "checksum_sha256": params_checksum,
                        "size_bytes": params.stat().st_size,
                    },
                ],
            },
            metrics={},
            status="ready",
        )
        session.add(trained)
        session.commit()
        pipeline_id = pipeline.id

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: LEGACY_TEST_ACTOR

    def override_session() -> Generator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_pipeline_inference_session] = override_session
    app.dependency_overrides[get_pipeline_inference_storage] = lambda: storage
    app.dependency_overrides[get_pipeline_inference_runtime] = lambda: runtime
    app.dependency_overrides[get_framework_adapter_catalog] = lambda: catalog

    with TestClient(app) as client, image_path.open("rb") as image_file:
        response = client.post(
            f"/pipelines/{pipeline_id}/predict/image",
            data={"model_weight": "latest", "environment": "gpu-node-0"},
            files={"file": ("sample.jpg", image_file, "image/jpeg")},
        )

    assert response.status_code == 200, response.text
    assert response.json()["predictions"] == [
        {"label": "defect", "score": 0.93, "box": [1.0, 2.0, 15.0, 16.0]}
    ]
    assert response.json()["result_image"].startswith("data:image/png;base64,")
    encoded_image = response.json()["result_image"].split(",", 1)[1]
    assert base64.b64decode(encoded_image).startswith(b"\x89PNG\r\n\x1a\n")
    request = runtime.requests[0]
    assert request.image_digest == digest
    assert runtime.model_files == {
        "inference.json": b"{}",
        "inference.pdiparams": b"static-params",
    }
    assert request.api_contract == "paddlex.create_model(model_name, model_dir=...)"
    assert request.model_name == "PP-YOLOE_plus-S"
    runtime_command = request.create_command("test-volume")
    assert runtime_command[runtime_command.index("--gpus") + 1] == "device=0"
    assert runtime_command[-3] == "gpu:0"
    assert request.image_path.suffix == ".jpg"
    assert runtime_command[-2] == "/workspace/io/input/image.jpg"
    assert runtime_command[-1] == "PP-YOLOE_plus-S"
    assert "/tmp:rw,noexec,nosuid,size=1g" in runtime_command


def test_inference_rejects_persisted_framework_mismatch_before_artifact_download(tmp_path):
    database_path = tmp_path / "visiox-inference-mismatch.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database_path}")
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    install_legacy_ownership(session_factory)

    class NoDownloadStorage(InMemoryObjectStorageClient):
        def get_file(self, bucket, object_name, destination):
            raise AssertionError("mismatched artifacts must be rejected before download")

    storage = NoDownloadStorage()
    with session_factory() as session:
        pipeline = TrainingPipeline(
            name="identity-mismatch",
            task="detect",
            scale="n",
            framework="paddlex",
            adapter_key="paddlex.object_detection.v1",
            adapter_version="1.0.0",
            model_family="PP-YOLOE",
            params_template={},
            default_environment={},
            status="success",
        )
        session.add(pipeline)
        session.flush()
        model = TrainedModel(
            pipeline_id=pipeline.id,
            name="wrong-framework",
            version="attempt-1",
            task="detect",
            framework="ultralytics",
            adapter_key="ultralytics.object_detection.v1",
            model_family="yolo26",
            model_format="pt",
            artifact_role="best_weights",
            artifact_uri="memory://models/wrong.pt",
            artifact_manifest={
                "adapter_key": "ultralytics.object_detection.v1",
                "adapter_version": "1.0.0",
            },
            metrics={},
            status="ready",
        )
        session.add(model)
        session.commit()
        pipeline_id, model_id = pipeline.id, model.id

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: LEGACY_TEST_ACTOR

    def override_session() -> Generator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_pipeline_inference_session] = override_session
    app.dependency_overrides[get_pipeline_inference_storage] = lambda: storage
    with TestClient(app) as client:
        response = client.post(
            f"/pipelines/{pipeline_id}/predict/image",
            data={"model_weight": model_id},
            files={"file": ("sample.png", b"image", "image/png")},
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "Selected model framework does not match pipeline"


class NoDownloadStorage(InMemoryObjectStorageClient):
    def __init__(self) -> None:
        super().__init__()
        self.downloads = 0

    def get_file(self, bucket, object_name, destination):
        self.downloads += 1
        raise AssertionError("invalid bundle must not start downloads")


def test_paddlex_model_family_and_runtime_identity_mismatch_is_rejected(tmp_path):
    database_path = tmp_path / "visiox-paddlex-identity.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database_path}")
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    install_legacy_ownership(session_factory)
    catalog = FrameworkAdapterCatalog(Settings())
    storage = NoDownloadStorage()
    with session_factory() as session:
        pipeline = TrainingPipeline(
            name="paddlex-family-mismatch",
            engine="paddlex",
            task="detect",
            scale="l",
            task_kind="object_detection",
            framework="paddlex",
            adapter_key="paddlex.object_detection.v1",
            adapter_version="1.0.0",
            model_family="RT-DETR",
            recipe={"model": {"runtime_id": "RT-DETR-L"}},
            status="success",
        )
        session.add(pipeline)
        session.flush()
        model = TrainedModel(
            pipeline_id=pipeline.id,
            name="inference.json",
            version="attempt-1",
            task="detect",
            framework="paddlex",
            adapter_key="paddlex.object_detection.v1",
            model_family="PP-YOLOE",
            model_format="json",
            artifact_role="best_static_inference",
            artifact_uri="memory://models/job/inference.json",
            checksum="0" * 64,
            size_bytes=2,
            artifact_manifest={
                "adapter_key": "paddlex.object_detection.v1",
                "adapter_version": "1.0.0",
                "model_identity": {
                    "model_family": "PP-YOLOE",
                    "runtime_model_id": "PP-YOLOE_plus-S",
                },
                "role_artifacts": [
                    {"path": "best_model/inference/inference.json"},
                    {"path": "best_model/inference/inference.pdiparams"},
                ],
            },
            metrics={},
            status="ready",
        )
        session.add(model)
        session.commit()

        with pytest.raises(HTTPException, match="model family"):
            resolve_pipeline_model(
                session, catalog, pipeline, model.id, operation="image_inference"
            )

    assert storage.downloads == 0


@pytest.mark.parametrize(
    ("role", "model_format", "manifest"),
    [
        ("best_dynamic_weights", "json", {"adapter_key": "paddlex.object_detection.v1", "adapter_version": "1.0.0"}),
        ("best_static_inference", "pdparams", {"adapter_key": "paddlex.object_detection.v1", "adapter_version": "1.0.0"}),
        ("best_static_inference", "json", {"adapter_key": "paddlex.object_detection.v1", "adapter_version": "9.0.0"}),
    ],
)
def test_paddlex_role_format_or_manifest_mismatch_is_rejected_without_download(
    tmp_path, role, model_format, manifest
):
    database_path = tmp_path / f"visiox-paddlex-contract-{role}-{model_format}.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database_path}")
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    install_legacy_ownership(session_factory)
    catalog = FrameworkAdapterCatalog(Settings())
    storage = NoDownloadStorage()
    with session_factory() as session:
        pipeline = TrainingPipeline(
            name=f"contract-{role}-{model_format}", engine="paddlex", task="detect", scale="s",
            task_kind="object_detection", framework="paddlex",
            adapter_key="paddlex.object_detection.v1", adapter_version="1.0.0",
            model_family="PP-YOLOE", recipe={"model": {"runtime_id": "PP-YOLOE_plus-S"}},
            status="success",
        )
        session.add(pipeline)
        session.flush()
        model = TrainedModel(
            pipeline_id=pipeline.id, name="artifact", version="attempt-1", task="detect",
            framework="paddlex", adapter_key="paddlex.object_detection.v1",
            model_family="PP-YOLOE", model_format=model_format, artifact_role=role,
            artifact_uri="memory://models/artifact", artifact_manifest=manifest,
            metrics={}, status="ready",
        )
        session.add(model)
        session.commit()
        with pytest.raises(HTTPException):
            resolve_pipeline_model(
                session, catalog, pipeline, model.id, operation="image_inference"
            )
    assert storage.downloads == 0


@pytest.mark.parametrize(
    "paths",
    [
        ["best_model/inference/inference.json"],
        ["../inference.json", "best_model/inference/inference.pdiparams"],
    ],
)
def test_invalid_or_incomplete_static_bundle_is_rejected_before_download(tmp_path, paths):
    storage = NoDownloadStorage()
    model = SimpleNamespace(
        name="inference.json",
        model_format="json",
        artifact_uri="memory://models/job/best_model/inference/inference.json",
        artifact_manifest={
            "role_artifacts": [
                {
                    "path": path,
                    "checksum_sha256": "0" * 64,
                    "size_bytes": 1,
                }
                for path in paths
            ]
        },
    )
    resolved = ResolvedPipelineModel(
        adapter=SimpleNamespace(), model=model, artifact_uri=model.artifact_uri,
        artifact_role="best_static_inference", model_format="json",
    )
    with pytest.raises(HTTPException):
        download_paddlex_static_bundle(storage, resolved, tmp_path / "model")
    assert storage.downloads == 0


@pytest.mark.parametrize(
    ("graph_checksum", "model_checksum", "model_size"),
    [
        ("not-a-sha256", "0" * 64, 2),
        ("1" * 64, "0" * 64, 2),
        ("0" * 64, "0" * 64, 99),
    ],
)
def test_static_bundle_integrity_preflight_rejects_before_download(
    tmp_path, graph_checksum, model_checksum, model_size
):
    storage = NoDownloadStorage()
    model = SimpleNamespace(
        name="inference.json",
        model_format="json",
        artifact_uri="memory://models/job/best_model/inference/inference.json",
        checksum=model_checksum,
        size_bytes=model_size,
        artifact_manifest={
            "role_artifacts": [
                {
                    "path": "best_model/inference/inference.json",
                    "checksum_sha256": graph_checksum,
                    "size_bytes": 2,
                },
                {
                    "path": "best_model/inference/inference.pdiparams",
                    "checksum_sha256": "2" * 64,
                    "size_bytes": 10,
                },
            ]
        },
    )
    resolved = ResolvedPipelineModel(
        adapter=SimpleNamespace(),
        model=model,
        artifact_uri=model.artifact_uri,
        artifact_role="best_static_inference",
        model_format="json",
    )

    with pytest.raises(HTTPException):
        download_paddlex_static_bundle(storage, resolved, tmp_path / "model")
    assert storage.downloads == 0


def test_paddlex_inference_cpu_runtime_command_has_no_gpu_flag(tmp_path):
    request = PaddleXInferenceRuntimeRequest(
        image_digest=f"registry.example/paddlex@sha256:{'b' * 64}",
        model_name="PP-YOLOE_plus-S",
        workspace=tmp_path,
        model_dir=tmp_path / "model",
        image_path=tmp_path / "input" / "image",
        output_dir=tmp_path / "output",
        result_path=tmp_path / "output" / "inference_result.json",
        environment="cpu",
    )

    runtime_command = request.create_command("test-volume")
    assert "--gpus" not in runtime_command
    assert runtime_command[-3] == "cpu"
    assert runtime_command[-2] == "/workspace/io/input/image.png"
    assert runtime_command[-1] == "PP-YOLOE_plus-S"
    assert "/tmp:rw,noexec,nosuid,size=1g" in runtime_command


def test_ultralytics_latest_without_trained_weight_falls_back_to_base_model(tmp_path):
    database_path = tmp_path / "visiox-ultralytics-base-fallback.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database_path}")
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    install_legacy_ownership(session_factory)
    catalog = FrameworkAdapterCatalog(Settings())

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
        session.add(base)
        session.flush()
        pipeline = TrainingPipeline(
            name="base-fallback",
            task="detect",
            scale="n",
            task_kind="object_detection",
            framework="ultralytics",
            adapter_key="ultralytics.object_detection.v1",
            adapter_version="1.0.0",
            model_family="yolo26",
            base_model_id=base.id,
            status="success",
        )
        session.add(pipeline)
        session.commit()

        resolved = resolve_pipeline_model(
            session, catalog, pipeline, "latest", operation="image_inference"
        )

    assert resolved.model is None
    assert resolved.artifact_role == "official"
    assert resolved.artifact_uri == "memory://models/base/yolo26n.pt"


def test_explicit_model_must_be_ready(tmp_path):
    database_path = tmp_path / "visiox-explicit-model-status.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database_path}")
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    install_legacy_ownership(session_factory)
    catalog = FrameworkAdapterCatalog(Settings())

    with session_factory() as session:
        pipeline = TrainingPipeline(
            name="not-ready-model",
            task="detect",
            scale="n",
            task_kind="object_detection",
            framework="ultralytics",
            adapter_key="ultralytics.object_detection.v1",
            adapter_version="1.0.0",
            model_family="yolo26",
            status="success",
        )
        session.add(pipeline)
        session.flush()
        model = TrainedModel(
            pipeline_id=pipeline.id,
            name="best.pt",
            version="attempt-1",
            task="detect",
            framework="ultralytics",
            adapter_key="ultralytics.object_detection.v1",
            model_family="yolo26",
            model_format="pt",
            artifact_role="best_weights",
            artifact_uri="memory://models/best.pt",
            artifact_manifest={
                "adapter_key": "ultralytics.object_detection.v1",
                "adapter_version": "1.0.0",
            },
            metrics={},
            status="pending",
        )
        session.add(model)
        session.commit()

        with pytest.raises(HTTPException, match="not ready"):
            resolve_pipeline_model(
                session, catalog, pipeline, model.id, operation="image_inference"
            )


def test_download_artifact_rejects_checksum_mismatch(tmp_path):
    storage = InMemoryObjectStorageClient()
    source = tmp_path / "source.pdparams"
    source.write_bytes(b"tampered")
    uri = storage.put_file("models", "source.pdparams", source)

    with pytest.raises(HTTPException, match="checksum"):
        download_artifact(
            storage,
            uri,
            tmp_path / "download.pdparams",
            checksum="0" * 64,
            size_bytes=len(b"tampered"),
            require_integrity=True,
        )


def test_paddlex_results_reject_non_finite_or_out_of_range_values(tmp_path):
    evaluation = tmp_path / "evaluate_result.json"
    evaluation.write_text('{"bbox_mAP_50": NaN}', encoding="utf-8")
    with pytest.raises(PaddleXResultError, match="outside"):
        read_evaluation_result(evaluation)

    output = tmp_path / "output"
    output.mkdir()
    Image.new("RGB", (2, 2), "white").save(output / "annotated.png")
    inference = output / "inference_result.json"
    inference.write_text(
        json.dumps(
            {
                "boxes": [
                    {"label": "bad", "score": 1.1, "coordinate": [0, 0, 1, 1]}
                ],
                "annotated_image": "annotated.png",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(PaddleXResultError, match="numeric"):
        read_inference_result(inference, output)


def test_paddlex_results_reject_oversized_json_and_invalid_image(tmp_path):
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"{" + b" " * (1024 * 1024) + b"}")
    with pytest.raises(PaddleXResultError, match="too large"):
        read_evaluation_result(oversized)

    output = tmp_path / "output"
    output.mkdir()
    (output / "annotated.png").write_bytes(b"not-an-image")
    result = output / "inference_result.json"
    result.write_text(
        '{"boxes":[],"annotated_image":"annotated.png"}', encoding="utf-8"
    )
    with pytest.raises(PaddleXResultError, match="image is invalid"):
        read_inference_result(result, output)
