from __future__ import annotations

import asyncio
import base64
from collections.abc import Generator
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import tarfile
from types import SimpleNamespace
from typing import Any

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi import UploadFile
from fastapi.testclient import TestClient
import httpx
from PIL import Image
import pytest
from starlette.datastructures import Headers
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from visiox_api.routes import services as services_route
from visiox_api.dependencies.auth import get_current_user
from visiox_api.routes.pipeline_inference import PipelinePredictResponse
from visiox_api.services.deployment_adapters import resolve_deployment_adapter
from visiox_api.routes.services import (
    get_service_session,
    get_service_stream_producer,
    router,
)
from visiox_common.tasks import TaskType
from visiox_db.models import (
    BaseModel,
    ComputeNode,
    DeploymentInstance,
    DeploymentService,
    RemoteExecution,
    ResourcePool,
    Task,
    TrainedModel,
    TrainingPipeline,
    Organization,
    User,
)
from visiox_edge_executor_worker.inventory import compatibility_policy, parse_inventory
from visiox_storage.client import InMemoryObjectStorageClient
from tests.integration.ownership_test_support import install_legacy_ownership


FIXTURES = Path(__file__).parents[1] / "fixtures" / "edge_inventory"
MODEL_CHECKSUM = "a" * 64
IMAGE_DIGEST = "registry.internal/visiox/yolo26-inference@sha256:" + "b" * 64
LEGACY_TEST_ACTOR = SimpleNamespace(
    id="legacy-admin", organization_id="legacy-org", role="admin"
)


class FakeEdgeProducer:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.failure = failure
        self.enqueued: list[dict[str, Any]] = []

    def enqueue(self, command):
        raise AssertionError("service operations must use the dedicated Edge producer")

    async def enqueue_edge_execution(self, **command: Any) -> str:
        if self.failure is not None:
            raise self.failure
        self.enqueued.append(command)
        return "1-0"


@pytest.fixture()
def service_runtime(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'visiox-services.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    install_legacy_ownership(factory)
    inventory = parse_inventory(
        json.loads((FIXTURES / "x86.json").read_text(encoding="utf-8"))
    )
    with factory() as session:
        session.add(Organization(id="legacy-org", name="Legacy", slug="legacy"))
        session.add(
            User(
                id="legacy-admin",
                organization_id="legacy-org",
                username="legacy-admin",
                display_name="Legacy Admin",
                email="legacy-admin@example.test",
                password_hash="test",
                role="admin",
                status="active",
                must_change_password=False,
            )
        )
        pool = ResourcePool(
            id="pool-x86",
            name="x86-production",
            organization_id="legacy-org",
            owner_user_id="legacy-admin",
            kind=inventory.platform_kind,
            selector=compatibility_policy(inventory),
            compatibility_policy=compatibility_policy(inventory),
            enabled=True,
        )
        pipeline = TrainingPipeline(
            id="pipeline-1",
            name="pepper-detect",
            organization_id="legacy-org",
            owner_user_id="legacy-admin",
            task="detect",
            scale="n",
            params_template={},
            default_environment={},
            status="success",
        )
        model = TrainedModel(
            id="model-1",
            pipeline_id=pipeline.id,
            organization_id="legacy-org",
            owner_user_id="legacy-admin",
            name="best.pt",
            version="best.pt",
            task="detect",
            artifact_uri="minio://models/trained/detect/best.pt",
            metrics={"checksum": MODEL_CHECKSUM},
            status="ready",
        )
        base_model = BaseModel(
            id="base-model-1",
            family="yolo26",
            task="detect",
            scale="n",
            filename="yolo26n.pt",
            source_path="yolo26n.pt",
            local_uri="minio://models/base/yolo26-detect-n/yolo26n.pt",
            checksum="c" * 64,
            size_bytes=5_544_453,
            status="ready",
        )
        node = ComputeNode(
            id="node-1",
            name="gpu-node-1",
            organization_id="legacy-org",
            owner_user_id="legacy-admin",
            resource_pool_id=pool.id,
            status="online",
            architecture=inventory.architecture,
            platform_kind=inventory.platform_kind,
            capabilities={"nvidia_gpu": True},
            resources={"gpu_count": 1},
            fingerprint={
                "compatibility_key": pool.compatibility_policy["compatibility_key"],
                "inventory_snapshot": inventory.model_dump(mode="json"),
            },
            agent_version="ssh-bootstrap",
        )
        session.add_all([pool, pipeline, model, base_model, node])
        session.commit()

    producer = FakeEdgeProducer()
    storage = InMemoryObjectStorageClient()
    app = FastAPI()
    app.state.object_storage = storage
    app.include_router(router)

    def override_session() -> Generator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_service_session] = override_session
    app.dependency_overrides[get_service_stream_producer] = lambda: producer
    app.dependency_overrides[get_current_user] = lambda: LEGACY_TEST_ACTOR
    return app, factory, producer


def _request(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "pepper-online",
        "pipeline_id": "pipeline-1",
        "trained_model_id": "model-1",
        "model_name": "yolo26n.pt",
        "model_weight": "best.pt",
        "environment": "gpu-node-1",
        "instance_name": "pepper-prod-01",
        "resource_summary": "RTX 4090",
        "node_id": "node-1",
        "image_digest": IMAGE_DIGEST,
        "model_checksum": MODEL_CHECKSUM,
        "port": 18080,
        "config": {"pipeline_name": "pepper-detect"},
    }
    payload.update(overrides)
    return payload


def test_create_service_transactionally_persists_real_resources_then_enqueues(
    service_runtime,
) -> None:
    app, factory, producer = service_runtime

    with TestClient(app) as client:
        created = client.post("/services", json=_request())

    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "queued"
    assert body["endpoint"] == "pending"
    assert body["phase"] == "queued"
    assert body["node_id"] == "node-1"
    assert body["health_status"] == "pending"
    assert body["health_checked_at"] is None
    assert len(producer.enqueued) == 1

    assert producer.enqueued[0] == {
        "task_id": body["task_id"],
        "task_type": TaskType.EDGE_DEPLOY,
        "remote_execution_id": body["remote_execution_id"],
    }

    with factory() as session:
        service = session.get(DeploymentService, body["id"])
        instance = session.get(DeploymentInstance, body["instance_id"])
        execution = session.get(RemoteExecution, body["remote_execution_id"])
        task = session.get(Task, body["task_id"])
        assert service is not None
        assert instance is not None
        assert execution is not None
        assert task is not None
        assert instance.deployment_service_id == service.id
        assert instance.model_checksum == MODEL_CHECKSUM
        assert instance.image_digest == IMAGE_DIGEST
        assert instance.engine == "engine"
        assert execution.deployment_service_id == service.id
        assert execution.resource_id == instance.id
        assert execution.operation == "deploy"
        assert task.resource_id == service.id
        assert task.payload == {}
        serialized_config = json.dumps(service.config)
        assert "X-Amz" not in serialized_config
        assert "secret" not in serialized_config.casefold()


def test_running_service_inference_uses_deployed_endpoint(
    service_runtime,
    monkeypatch,
) -> None:
    app, factory, _producer = service_runtime
    with TestClient(app) as client:
        created = client.post("/services", json=_request())
    service_id = created.json()["id"]
    with factory() as session:
        service = session.get(DeploymentService, service_id)
        assert service is not None
        service.status = "running"
        service.endpoint = "http://edge.example:18080"
        session.add(service)
        session.commit()

    captured: dict[str, str] = {}

    async def fake_remote_prediction(service, file):
        captured["endpoint"] = service.endpoint
        captured["filename"] = file.filename
        return PipelinePredictResponse(
            pipeline_id=service.pipeline_id,
            model_weight=service.model_weight,
            environment=service.resource_summary,
            predictions=[{"label": "pepper", "confidence": 0.9}],
            result_image="data:image/png;base64,cG5n",
            latency_ms=12.5,
        )

    monkeypatch.setattr(
        services_route, "_request_deployed_prediction", fake_remote_prediction
    )
    with TestClient(app) as client:
        response = client.post(
            f"/services/{service_id}/predict/image",
            files={"file": ("pepper.png", b"image", "image/png")},
        )

    assert response.status_code == 200
    assert response.json()["latency_ms"] == 12.5
    assert captured == {
        "endpoint": "http://edge.example:18080",
        "filename": "pepper.png",
    }
    with factory() as session:
        service = session.get(DeploymentService, service_id)
        assert service is not None
        assert service.calls == 1


def test_deployed_prediction_adapter_returns_annotated_image(service_runtime) -> None:
    app, factory, _producer = service_runtime
    with TestClient(app) as client:
        created = client.post("/services", json=_request())
    with factory() as session:
        service = session.get(DeploymentService, created.json()["id"])
        assert service is not None
        service.status = "running"
        service.endpoint = "http://edge.example:18080"
        session.add(service)
        session.commit()
        session.expunge(service)

    image_buffer = BytesIO()
    Image.new("RGB", (64, 48), "white").save(image_buffer, format="PNG")
    image_bytes = image_buffer.getvalue()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "http://edge.example:18080/predict/image"
        return httpx.Response(
            200,
            json={
                "predictions": [
                    {
                        "class_id": 0,
                        "label": "pepper",
                        "confidence": 0.91,
                        "bbox": {"x1": 5, "y1": 6, "x2": 30, "y2": 32},
                    },
                    {
                        "class_id": 1,
                        "label": "leaf",
                        "confidence": 0.83,
                        "bbox": {"x1": 35, "y1": 6, "x2": 55, "y2": 32},
                    },
                ],
                "latency_ms": 8.75,
            },
        )

    async def run_prediction() -> PipelinePredictResponse:
        upload = UploadFile(
            filename="pepper.png",
            file=BytesIO(image_bytes),
            headers=Headers({"content-type": "image/png"}),
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await services_route._request_deployed_prediction(
                service,
                upload,
                client=client,
            )

    result = asyncio.run(run_prediction())
    assert result.predictions[0]["label"] == "pepper"
    assert result.latency_ms == 8.75
    assert result.result_image.startswith("data:image/png;base64,")
    assert result.environment == "RTX 4090"
    encoded_image = result.result_image.split(",", 1)[1]
    with Image.open(BytesIO(base64.b64decode(encoded_image))) as annotated:
        assert annotated.convert("RGB").getpixel((5, 32)) != annotated.convert(
            "RGB"
        ).getpixel((35, 32))


def test_create_service_resolves_system_image_and_trained_checksum(
    service_runtime,
    monkeypatch,
) -> None:
    app, factory, _producer = service_runtime
    monkeypatch.setattr(
        "visiox_api.routes.services.get_settings",
        lambda: type("Settings", (), {"deployment_image_digest": IMAGE_DIGEST})(),
    )
    payload = _request(name="automatic-metadata")
    payload.pop("image_digest")
    payload.pop("model_checksum")

    with TestClient(app) as client:
        response = client.post("/services", json=payload)

    assert response.status_code == 201
    with factory() as session:
        instance = session.get(DeploymentInstance, response.json()["instance_id"])
        assert instance is not None
        assert instance.image_digest == IMAGE_DIGEST
        assert instance.model_checksum == MODEL_CHECKSUM


def test_create_service_calculates_and_persists_missing_trained_checksum(
    service_runtime,
    tmp_path,
) -> None:
    app, factory, _producer = service_runtime
    model_bytes = b"legacy-trained-model"
    artifact_path = tmp_path / "best.pt"
    artifact_path.write_bytes(model_bytes)
    storage = app.state.object_storage
    storage.put_file("models", "trained/detect/best.pt", artifact_path)
    with factory() as session:
        model = session.get(TrainedModel, "model-1")
        assert model is not None
        model.metrics = {}
        session.add(model)
        session.commit()

    with TestClient(app) as client:
        response = client.post(
            "/services",
            json=_request(name="legacy-model", model_checksum=None),
        )

    expected_checksum = sha256(model_bytes).hexdigest()
    assert response.status_code == 201
    assert response.json()["model_checksum"] == expected_checksum
    with factory() as session:
        model = session.get(TrainedModel, "model-1")
        assert model is not None
        assert model.metrics["checksum"] == expected_checksum


def test_create_service_accepts_official_base_model(service_runtime) -> None:
    app, factory, _producer = service_runtime
    payload = _request(
        name="official-model",
        trained_model_id=None,
        base_model_id="base-model-1",
        model_name="ignored-client-name",
        model_weight="ignored-client-weight",
        model_checksum=None,
    )

    with TestClient(app) as client:
        response = client.post("/services", json=payload)

    assert response.status_code == 201
    body = response.json()
    assert body["trained_model_id"] is None
    assert body["model_name"] == "yolo26n.pt"
    assert body["model_weight"] == "yolo26n.pt"
    with factory() as session:
        service = session.get(DeploymentService, body["id"])
        instance = session.get(DeploymentInstance, body["instance_id"])
        assert service is not None
        assert service.config["base_model_id"] == "base-model-1"
        assert instance is not None
        assert instance.model_checksum == "c" * 64


def test_create_service_persists_redacted_deterministic_enqueue_failure(
    service_runtime,
) -> None:
    app, factory, _producer = service_runtime
    failing = FakeEdgeProducer(
        failure=RuntimeError("password=registry-secret X-Amz-Signature=signed-secret")
    )
    app.dependency_overrides[get_service_stream_producer] = lambda: failing

    with TestClient(app) as client:
        response = client.post("/services", json=_request(name="enqueue-failure"))

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "ENQUEUE_FAILED"
    assert body["error_message"] == "Edge deployment could not be queued"
    assert "secret" not in json.dumps(body).casefold()
    with factory() as session:
        execution = session.get(RemoteExecution, body["remote_execution_id"])
        task = session.get(Task, body["task_id"])
        instance = session.get(DeploymentInstance, body["instance_id"])
        assert execution is not None and execution.status == "failed"
        assert task is not None and task.status == "FAILED"
        assert instance is not None and instance.status == "failed"


def test_stop_service_creates_real_stop_execution_and_dedicated_command(
    service_runtime,
) -> None:
    app, _factory, producer = service_runtime
    with TestClient(app) as client:
        created = client.post("/services", json=_request())
        producer.enqueued.clear()
        stopped = client.post(f"/services/{created.json()['id']}/stop")

    assert stopped.status_code == 202
    body = stopped.json()
    assert body["status"] == "stopping"
    assert body["phase"] == "queued"
    assert body["config"]["current_remote_execution_id"] == body["remote_execution_id"]
    assert producer.enqueued == [
        {
            "task_id": body["task_id"],
            "task_type": TaskType.EDGE_STOP_DEPLOYMENT,
            "remote_execution_id": body["remote_execution_id"],
        }
    ]


def test_stopped_service_can_start_and_running_service_can_restart(
    service_runtime,
) -> None:
    app, factory, producer = service_runtime
    with TestClient(app) as client:
        created = client.post("/services", json=_request()).json()
        with factory() as session:
            service = session.get(DeploymentService, created["id"])
            instance = session.get(DeploymentInstance, created["instance_id"])
            assert service is not None and instance is not None
            service.status = "stopped"
            service.desired_state = "stopped"
            service.active_revision = 1
            instance.status = "stopped"
            instance.health_status = "stopped"
            instance.deployment_revision = 1
            instance.container_id = "a" * 64
            session.commit()

        producer.enqueued.clear()
        started = client.post(f"/services/{created['id']}/start")
        assert started.status_code == 202
        assert started.json()["status"] == "starting"
        assert started.json()["desired_state"] == "running"
        assert started.json()["active_revision"] == 1
        assert producer.enqueued[-1]["task_type"] == TaskType.EDGE_START_DEPLOYMENT

        with factory() as session:
            service = session.get(DeploymentService, created["id"])
            instance = session.get(DeploymentInstance, created["instance_id"])
            assert service is not None and instance is not None
            service.status = "running"
            instance.status = "running"
            instance.health_status = "healthy"
            session.commit()

        restarted = client.post(f"/services/{created['id']}/restart")

    assert restarted.status_code == 202
    assert restarted.json()["status"] == "restarting"
    assert producer.enqueued[-1]["task_type"] == TaskType.EDGE_RESTART_DEPLOYMENT


def test_start_service_requires_a_successful_deployment_revision(
    service_runtime,
) -> None:
    app, _factory, _producer = service_runtime
    with TestClient(app) as client:
        created = client.post("/services", json=_request()).json()
        response = client.post(f"/services/{created['id']}/start")

    assert response.status_code == 409
    assert response.json()["detail"] == "No successful deployment revision is available"


def test_stop_service_without_instance_converges_and_can_be_deleted(
    service_runtime,
) -> None:
    app, factory, producer = service_runtime
    with TestClient(app) as client:
        created = client.post("/services", json=_request()).json()
        with factory() as session:
            service = session.get(DeploymentService, created["id"])
            instance = session.get(DeploymentInstance, created["instance_id"])
            assert service is not None and instance is not None
            service.status = "running"
            service.endpoint = "http://edge.example:18080"
            session.delete(instance)
            session.commit()

        producer.enqueued.clear()
        stopped = client.post(f"/services/{created['id']}/stop")
        deleted = client.delete(f"/services/{created['id']}")
        missing = client.get(f"/services/{created['id']}")

    assert stopped.status_code == 202
    assert stopped.json()["status"] == "stopped"
    assert stopped.json()["endpoint"] == ""
    assert producer.enqueued == []
    assert deleted.status_code == 204
    assert missing.status_code == 404


def test_paddlex_deployment_adapter_selects_pinned_hpi_tensorrt_backend() -> None:
    pipeline, model = _paddlex_deployment_records()
    resolution = resolve_deployment_adapter(
        pipeline,
        model,
        parse_inventory(json.loads((FIXTURES / "x86.json").read_text(encoding="utf-8"))),
        runtime_image_digest="registry.example/visiox/paddlex-inference@sha256:" + "d" * 64,
        precision="auto",
        input_shape=(1, 3, 640, 640),
        gpu_uuids=(),
    )

    assert resolution.framework == "paddlex"
    assert resolution.model_format == "paddle_inference_bundle"
    assert resolution.resolved_backend == "paddlex_hpi_tensorrt"
    assert resolution.device == "gpu:0"
    assert resolution.precision == "fp16"


def test_paddlex_deployment_adapter_fails_closed_for_unpinned_runtime() -> None:
    pipeline, model = _paddlex_deployment_records()
    with pytest.raises(ValueError, match="immutable image digest"):
        resolve_deployment_adapter(
            pipeline,
            model,
            parse_inventory(json.loads((FIXTURES / "x86.json").read_text(encoding="utf-8"))),
            runtime_image_digest="registry.example/paddlex:latest",
            precision="auto",
            input_shape=(1, 3, 640, 640),
            gpu_uuids=(),
        )


def test_paddlex_deployment_adapter_rejects_digest_outside_model_matrix() -> None:
    pipeline, model = _paddlex_deployment_records()
    with pytest.raises(ValueError, match="compatibility matrix"):
        resolve_deployment_adapter(
            pipeline,
            model,
            parse_inventory(json.loads((FIXTURES / "x86.json").read_text(encoding="utf-8"))),
            runtime_image_digest=(
                "registry.example/visiox/paddlex-inference@sha256:" + "e" * 64
            ),
            precision="auto",
            input_shape=(1, 3, 640, 640),
            gpu_uuids=(),
        )


def test_paddlex_deployment_adapter_preserves_explicit_fp16() -> None:
    pipeline, model = _paddlex_deployment_records()
    resolution = resolve_deployment_adapter(
        pipeline,
        model,
        parse_inventory(json.loads((FIXTURES / "x86.json").read_text(encoding="utf-8"))),
        runtime_image_digest=(
            "registry.example/visiox/paddlex-inference@sha256:" + "d" * 64
        ),
        precision="fp16",
        input_shape=(1, 3, 640, 640),
        gpu_uuids=(),
    )

    assert resolution.precision == "fp16"


def test_paddlex_deployment_adapter_accepts_explicit_fp32_gpu_when_declared() -> None:
    pipeline, model = _paddlex_deployment_records()
    resolution = resolve_deployment_adapter(
        pipeline,
        model,
        parse_inventory(json.loads((FIXTURES / "x86.json").read_text(encoding="utf-8"))),
        runtime_image_digest=(
            "registry.example/visiox/paddlex-inference@sha256:" + "d" * 64
        ),
        precision="fp32",
        input_shape=(1, 3, 640, 640),
        gpu_uuids=("GPU-x86-fixture",),
    )

    assert resolution.resolved_backend == "paddlex_hpi_tensorrt"
    assert resolution.device == "gpu:0"
    assert resolution.precision == "fp32"


def test_paddlex_deployment_adapter_rejects_explicit_gpu_below_matrix_requirements() -> None:
    pipeline, model = _paddlex_deployment_records()
    compatibility = model.deployment_compatibility["runtime_compatibility"][0]
    compatibility["hpi_requirements"] = {
        "cuda_min": "12.9",
        "tensorrt_min": "10.9",
        "compute_capability_min": "9.9",
    }

    with pytest.raises(ValueError, match="requested GPU"):
        resolve_deployment_adapter(
            pipeline,
            model,
            parse_inventory(json.loads((FIXTURES / "x86.json").read_text(encoding="utf-8"))),
            runtime_image_digest=(
                "registry.example/visiox/paddlex-inference@sha256:" + "d" * 64
            ),
            precision="fp16",
            input_shape=(1, 3, 640, 640),
            gpu_uuids=("GPU-x86-fixture",),
        )


def test_paddlex_deployment_adapter_falls_back_for_auto_gpu_below_matrix_requirements() -> None:
    pipeline, model = _paddlex_deployment_records()
    compatibility = model.deployment_compatibility["runtime_compatibility"][0]
    compatibility["hpi_requirements"] = {
        "cuda_min": "12.9",
        "tensorrt_min": "10.9",
        "compute_capability_min": "9.9",
    }

    resolution = resolve_deployment_adapter(
        pipeline,
        model,
        parse_inventory(json.loads((FIXTURES / "x86.json").read_text(encoding="utf-8"))),
        runtime_image_digest=(
            "registry.example/visiox/paddlex-inference@sha256:" + "d" * 64
        ),
        precision="auto",
        input_shape=(1, 3, 640, 640),
        gpu_uuids=(),
    )

    assert resolution.resolved_backend == "paddle_inference"
    assert resolution.device == "gpu:0"
    assert resolution.precision == "fp32"
    assert resolution.gpu_uuids == ("GPU-x86-fixture",)


def test_paddlex_deployment_adapter_runs_paddle_inference_on_explicit_gpu() -> None:
    pipeline, model = _paddlex_deployment_records()
    compatibility = model.deployment_compatibility["runtime_compatibility"][0]
    compatibility["backends"] = ["paddle_inference"]
    compatibility["precisions"] = ["fp32"]

    resolution = resolve_deployment_adapter(
        pipeline,
        model,
        parse_inventory(json.loads((FIXTURES / "x86.json").read_text(encoding="utf-8"))),
        runtime_image_digest=(
            "registry.example/visiox/paddlex-inference@sha256:" + "d" * 64
        ),
        precision="fp32",
        input_shape=(1, 3, 640, 640),
        gpu_uuids=("GPU-x86-fixture",),
    )

    assert resolution.resolved_backend == "paddle_inference"
    assert resolution.device == "gpu:0"
    assert resolution.precision == "fp32"
    assert resolution.gpu_uuids == ("GPU-x86-fixture",)


def test_paddlex_deployment_adapter_rejects_int8_without_compatibility() -> None:
    pipeline, model = _paddlex_deployment_records()
    with pytest.raises(ValueError, match="INT8"):
        resolve_deployment_adapter(
            pipeline,
            model,
            parse_inventory(json.loads((FIXTURES / "x86.json").read_text(encoding="utf-8"))),
            runtime_image_digest=(
                "registry.example/visiox/paddlex-inference@sha256:" + "d" * 64
            ),
            precision="int8",
            input_shape=(1, 3, 640, 640),
            gpu_uuids=(),
        )


def test_paddlex_deployment_adapter_rejects_explicit_gpu_when_hpi_is_incompatible() -> None:
    pipeline, model = _paddlex_deployment_records()
    inventory = parse_inventory(
        json.loads((FIXTURES / "x86.json").read_text(encoding="utf-8"))
    ).model_copy(update={"tensorrt_version": "8.5.3"})
    with pytest.raises(ValueError, match="requested GPU"):
        resolve_deployment_adapter(
            pipeline,
            model,
            inventory,
            runtime_image_digest=(
                "registry.example/visiox/paddlex-inference@sha256:" + "d" * 64
            ),
            precision="fp16",
            input_shape=(1, 3, 640, 640),
            gpu_uuids=("GPU-x86-fixture",),
        )


def test_create_paddlex_service_persists_resolved_runtime_and_checksummed_bundle(
    service_runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, factory, producer = service_runtime
    storage = app.state.object_storage
    runtime_digest = (
        "registry.example/visiox/paddlex-inference@sha256:" + "d" * 64
    )
    json_bytes = b'{"Global":{"model_name":"PP-YOLOE_plus-S"}}'
    params_bytes = b"paddle-static-parameters"
    prefix = "jobs/job-paddlex/attempt-1/"
    storage.objects[("models", prefix + "best_model/inference/inference.json")] = json_bytes
    storage.objects[("models", prefix + "best_model/inference/inference.pdiparams")] = params_bytes
    with factory() as session:
        pipeline = TrainingPipeline(
            id="pipeline-paddlex",
            name="paddlex-detect",
            organization_id="legacy-org",
            owner_user_id="legacy-admin",
            task="detect",
            scale="s",
            task_kind="object_detection",
            framework="paddlex",
            adapter_key="paddlex.object_detection.v1",
            adapter_version="1.0.0",
            model_family="PP-YOLOE",
            recipe={"model": {"runtime_id": "PP-YOLOE_plus-S"}},
            status="success",
        )
        model = TrainedModel(
            id="model-paddlex",
            pipeline_id=pipeline.id,
            organization_id="legacy-org",
            owner_user_id="legacy-admin",
            name="inference.json",
            display_name="Best static inference",
            version="attempt-1",
            task="detect",
            framework="paddlex",
            adapter_key="paddlex.object_detection.v1",
            model_family="PP-YOLOE",
            model_format="json",
            artifact_role="best_static_inference",
            artifact_uri="minio://models/" + prefix + "best_model/inference/inference.json",
            checksum=sha256(json_bytes).hexdigest(),
            size_bytes=len(json_bytes),
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
                        "checksum_sha256": sha256(json_bytes).hexdigest(),
                        "size_bytes": len(json_bytes),
                        "artifact_type": "paddle_inference_bundle",
                    },
                    {
                        "path": "best_model/inference/inference.pdiparams",
                        "checksum_sha256": sha256(params_bytes).hexdigest(),
                        "size_bytes": len(params_bytes),
                        "artifact_type": "paddle_inference_bundle",
                    },
                ],
            },
            status="ready",
        )
        session.add_all([pipeline, model])
        session.commit()
    monkeypatch.setattr(
        services_route,
        "get_settings",
        lambda: SimpleNamespace(
            deployment_image_digest=IMAGE_DIGEST,
            paddlex_inference_image_digest=runtime_digest,
        ),
    )

    with TestClient(app) as client:
        response = client.post(
            "/services",
            json=_request(
                name="paddlex-online",
                pipeline_id="pipeline-paddlex",
                trained_model_id="model-paddlex",
                model_name="PP-YOLOE_plus-S",
                model_weight="best_static_inference",
                image_digest=None,
            ),
        )

    assert response.status_code == 201, response.text
    deployment = response.json()["config"]["deployment"]
    assert deployment["framework"] == "paddlex"
    assert deployment["adapter_key"] == "paddlex.object_detection.v1"
    assert deployment["adapter_version"] == "1.0.0"
    assert deployment["model_format"] == "paddle_inference_bundle"
    assert deployment["resolved_backend"] == "paddle_inference"
    assert deployment["runtime_image_digest"] == runtime_digest
    assert deployment["image_digest"] == runtime_digest
    assert deployment["artifact_uri"].startswith("minio://models/deployments/")
    assert len(deployment["model_checksum"]) == 64
    bucket, object_name = deployment["artifact_uri"].removeprefix("minio://").split("/", 1)
    archive = storage.objects[(bucket, object_name)]
    assert sha256(archive).hexdigest() == deployment["model_checksum"]
    with tarfile.open(fileobj=BytesIO(archive), mode="r:") as bundle:
        assert sorted(bundle.getnames()) == ["inference.json", "inference.pdiparams"]
    with factory() as session:
        model = session.get(TrainedModel, "model-paddlex")
        assert model is not None
        assert model.deployment_compatibility["runtime_compatibility"] == [
            {
                "runtime_image_digest": runtime_digest,
                "adapter_key": "paddlex.object_detection.v1",
                "adapter_version": "1.0.0",
                "model_format": "paddle_inference_bundle",
                "backends": ["paddle_inference"],
                "precisions": ["fp32"],
                "source": "runtime_image_default",
            }
        ]

    service_id = response.json()["id"]
    instance_id = response.json()["instance_id"]
    with factory() as session:
        service = session.get(DeploymentService, service_id)
        instance = session.get(DeploymentInstance, instance_id)
        task = session.get(Task, response.json()["task_id"])
        execution = session.get(RemoteExecution, response.json()["remote_execution_id"])
        assert service is not None and instance is not None
        assert task is not None and execution is not None
        service.status = "running"
        service.desired_state = "running"
        service.active_revision = 1
        instance.status = "running"
        instance.health_status = "healthy"
        instance.container_id = "a" * 64
        instance.image_digest = runtime_digest
        instance.model_checksum = deployment["model_checksum"]
        instance.engine = "paddle_inference_bundle"
        instance.engine_digest = deployment["model_checksum"]
        instance.port = deployment["port"]
        instance.deployment_revision = 1
        task.status = "SUCCESS"
        execution.status = "finished"
        session.commit()

    def complete_operation(result, *, status: str, health_status: str) -> None:
        with factory() as session:
            service = session.get(DeploymentService, service_id)
            instance = session.get(DeploymentInstance, instance_id)
            task = session.get(Task, result.json()["task_id"])
            execution = session.get(RemoteExecution, result.json()["remote_execution_id"])
            assert service is not None and instance is not None
            assert task is not None and execution is not None
            service.status = status
            service.desired_state = "stopped" if status == "stopped" else "running"
            instance.status = status
            instance.health_status = health_status
            task.status = "SUCCESS"
            execution.status = "finished"
            session.commit()

    with TestClient(app) as client:
        stopped = client.post(f"/services/{service_id}/stop")
        assert stopped.status_code == 202
        assert stopped.json()["config"]["deployment"]["model_format"] == (
            "paddle_inference_bundle"
        )
        complete_operation(stopped, status="stopped", health_status="stopped")

        started = client.post(f"/services/{service_id}/start")
        assert started.status_code == 202
        assert started.json()["config"]["deployment"]["runtime_config_checksum"]
        complete_operation(started, status="running", health_status="healthy")

        restarted = client.post(f"/services/{service_id}/restart")
        assert restarted.status_code == 202
        assert restarted.json()["config"]["deployment"]["framework"] == "paddlex"
        complete_operation(restarted, status="running", health_status="healthy")

        next_digest = (
            "registry.example/visiox/paddlex-inference@sha256:" + "e" * 64
        )
        with factory() as session:
            model = session.get(TrainedModel, "model-paddlex")
            assert model is not None
            compatibility = dict(model.deployment_compatibility)
            entries = list(compatibility["runtime_compatibility"])
            entries.append({**entries[0], "runtime_image_digest": next_digest})
            model.deployment_compatibility = {
                **compatibility,
                "runtime_compatibility": entries,
            }
            session.commit()
        upgraded = client.post(
            f"/services/{service_id}/upgrade",
            json={
                "trained_model_id": "model-paddlex",
                "image_digest": next_digest,
                "model_checksum": deployment["model_checksum"],
                "port": deployment["port"],
            },
        )
        assert upgraded.status_code == 202, upgraded.text
        assert upgraded.json()["config"]["deployment"]["model_format"] == (
            "paddle_inference_bundle"
        )
        with factory() as session:
            service = session.get(DeploymentService, service_id)
            instance = session.get(DeploymentInstance, instance_id)
            task = session.get(Task, upgraded.json()["task_id"])
            execution = session.get(
                RemoteExecution, upgraded.json()["remote_execution_id"]
            )
            assert service is not None and instance is not None
            assert task is not None and execution is not None
            assert instance.rollback_metadata["framework"] == "paddlex"
            assert instance.rollback_metadata["model_format"] == (
                "paddle_inference_bundle"
            )
            assert instance.rollback_metadata["runtime_image_digest"] == runtime_digest
            assert instance.rollback_metadata["runtime_config_checksum"] == deployment[
                "runtime_config_checksum"
            ]
            service.status = "running"
            service.active_revision = 2
            instance.status = "running"
            instance.health_status = "healthy"
            instance.container_id = "b" * 64
            instance.image_digest = next_digest
            instance.engine_digest = deployment["model_checksum"]
            instance.deployment_revision = 2
            task.status = "SUCCESS"
            execution.status = "finished"
            session.commit()
        rolled_back = client.post(f"/services/{service_id}/rollback")
        assert rolled_back.status_code == 202
        assert rolled_back.json()["status"] == "rollback_queued"
    assert len(producer.enqueued) == 6


def test_paddlex_bundle_rejects_oversized_declared_member_before_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pipeline, model = _paddlex_deployment_records()
    model.id = "oversized-model"
    model.artifact_uri = (
        "minio://models/jobs/oversized/best_model/inference/inference.json"
    )
    model.artifact_manifest["role_artifacts"][0]["size_bytes"] = (
        4 * 1024 * 1024 * 1024 + 1
    )
    monkeypatch.setattr(
        services_route,
        "get_settings",
        lambda: SimpleNamespace(
            paddlex_inference_image_digest=(
                "registry.example/visiox/paddlex-inference@sha256:" + "d" * 64
            )
        ),
    )

    with pytest.raises(ValueError, match="size limits"):
        services_route._prepare_paddlex_deployment_bundle(  # noqa: SLF001
            InMemoryObjectStorageClient(),
            model,
        )


def _paddlex_deployment_records() -> tuple[SimpleNamespace, SimpleNamespace]:
    runtime_digest = (
        "registry.example/visiox/paddlex-inference@sha256:" + "d" * 64
    )
    pipeline = SimpleNamespace(
        task="detect", task_kind="object_detection", framework="paddlex",
        adapter_key="paddlex.object_detection.v1", adapter_version="1.0.0",
        model_family="PP-YOLOE", recipe={"model": {"runtime_id": "PP-YOLOE_plus-S"}},
    )
    model = SimpleNamespace(
        status="ready", task="detect", framework="paddlex",
        adapter_key="paddlex.object_detection.v1", model_family="PP-YOLOE",
        model_format="json", artifact_role="best_static_inference",
        checksum="a" * 64, size_bytes=10,
        artifact_manifest={
            "adapter_key": "paddlex.object_detection.v1", "adapter_version": "1.0.0",
            "model_identity": {"model_family": "PP-YOLOE", "runtime_model_id": "PP-YOLOE_plus-S"},
            "role_artifacts": [
                {"path": "best_model/inference/inference.json", "checksum_sha256": "b" * 64, "size_bytes": 4, "artifact_type": "paddle_inference_bundle"},
                {"path": "best_model/inference/inference.pdiparams", "checksum_sha256": "c" * 64, "size_bytes": 6, "artifact_type": "paddle_inference_bundle"},
            ],
        }, deployment_compatibility={
            "runtime_compatibility": [
                {
                    "runtime_image_digest": runtime_digest,
                    "adapter_key": "paddlex.object_detection.v1",
                    "adapter_version": "1.0.0",
                    "model_format": "paddle_inference_bundle",
                    "backends": ["paddle_inference", "paddlex_hpi_tensorrt"],
                    "precisions": ["fp32", "fp16"],
                    "hpi_requirements": {
                        "cuda_min": "11.8",
                        "tensorrt_min": "8.6",
                        "compute_capability_min": "7.0",
                    },
                }
            ]
        },
    )
    return pipeline, model


def test_running_service_without_instance_can_be_deleted_directly(
    service_runtime,
) -> None:
    app, factory, _producer = service_runtime
    with TestClient(app) as client:
        created = client.post("/services", json=_request()).json()
        with factory() as session:
            service = session.get(DeploymentService, created["id"])
            instance = session.get(DeploymentInstance, created["instance_id"])
            assert service is not None and instance is not None
            service.status = "running"
            session.delete(instance)
            session.commit()

        deleted = client.delete(f"/services/{created['id']}")

    assert deleted.status_code == 204


def test_upgrade_queues_deploy_without_overwriting_healthy_observed_tuple(
    service_runtime,
) -> None:
    app, factory, producer = service_runtime
    next_image = "registry.internal/visiox/yolo26-inference@sha256:" + "c" * 64
    next_checksum = MODEL_CHECKSUM
    with TestClient(app) as client:
        created = client.post("/services", json=_request()).json()
        with factory() as session:
            service = session.get(DeploymentService, created["id"])
            instance = session.get(DeploymentInstance, created["instance_id"])
            task = session.get(Task, created["task_id"])
            assert service is not None and instance is not None and task is not None
            service.status = "running"
            service.endpoint = "http://edge.example:18080"
            instance.status = "running"
            instance.health_status = "healthy"
            instance.container_id = "e" * 64
            instance.engine_digest = "f" * 64
            instance.endpoint = service.endpoint
            task.status = "SUCCESS"
            session.commit()
        producer.enqueued.clear()
        response = client.post(
            f"/services/{created['id']}/upgrade",
            json={
                "trained_model_id": "model-1",
                "image_digest": next_image,
                "model_checksum": next_checksum,
                "port": 18081,
                "gpu_uuids": ["GPU-x86-fixture"],
            },
        )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "upgrade_queued"
    assert body["phase"] == "queued"
    assert body["image_digest"] == IMAGE_DIGEST
    assert body["model_checksum"] == MODEL_CHECKSUM
    assert body["container_id"] == "e" * 64
    assert body["health_status"] == "healthy"
    assert body["config"]["deployment"]["image_digest"] == next_image
    assert body["config"]["deployment"]["model_checksum"] == next_checksum
    assert body["config"]["deployment"]["port"] == 18081
    assert producer.enqueued == [
        {
            "task_id": body["task_id"],
            "task_type": TaskType.EDGE_DEPLOY,
            "remote_execution_id": body["remote_execution_id"],
        }
    ]


def test_upgrade_rejects_request_checksum_that_differs_from_artifact(
    service_runtime,
) -> None:
    app, factory, _producer = service_runtime
    with TestClient(app) as client:
        created = client.post("/services", json=_request()).json()
        with factory() as session:
            service = session.get(DeploymentService, created["id"])
            instance = session.get(DeploymentInstance, created["instance_id"])
            assert service is not None and instance is not None
            service.status = "running"
            instance.status = "running"
            instance.health_status = "healthy"
            instance.container_id = "e" * 64
            instance.engine_digest = "f" * 64
            session.commit()
        response = client.post(
            f"/services/{created['id']}/upgrade",
            json={
                "trained_model_id": "model-1",
                "image_digest": IMAGE_DIGEST,
                "model_checksum": "d" * 64,
                "gpu_uuids": ["GPU-x86-fixture"],
            },
        )

    assert response.status_code == 422
    assert "does not match" in response.json()["detail"]


def test_upgrade_enqueue_failure_restores_complete_previous_revision(
    service_runtime,
) -> None:
    app, factory, _producer = service_runtime
    failing = FakeEdgeProducer(failure=RuntimeError("queue unavailable"))
    app.dependency_overrides[get_service_stream_producer] = lambda: failing
    next_image = "registry.internal/visiox/yolo26-inference@sha256:" + "c" * 64
    with TestClient(app) as client:
        created = client.post("/services", json=_request()).json()
        with factory() as session:
            service = session.get(DeploymentService, created["id"])
            instance = session.get(DeploymentInstance, created["instance_id"])
            assert service is not None and instance is not None
            old_config = dict(service.config)
            service.status = "running"
            service.desired_state = "running"
            service.active_revision = 1
            instance.status = "running"
            instance.health_status = "healthy"
            instance.container_id = "e" * 64
            instance.engine_digest = "f" * 64
            instance.deployment_revision = 1
            session.commit()
        response = client.post(
            f"/services/{created['id']}/upgrade",
            json={
                "trained_model_id": "model-1",
                "image_digest": next_image,
                "model_checksum": MODEL_CHECKSUM,
                "gpu_uuids": ["GPU-x86-fixture"],
            },
        )

    assert response.status_code == 202
    with factory() as session:
        service = session.get(DeploymentService, created["id"])
        instance = session.get(DeploymentInstance, created["instance_id"])
        assert service is not None and instance is not None
        assert service.config == old_config
        assert service.trained_model_id == "model-1"
        assert service.status == "running"
        assert service.active_revision == 1
        assert instance.status == "running"
        assert instance.health_status == "healthy"
        assert instance.deployment_revision == 1


def test_rollback_queues_exact_previous_tuple(service_runtime) -> None:
    app, factory, producer = service_runtime
    with TestClient(app) as client:
        created = client.post("/services", json=_request())
        service_id = created.json()["id"]
        with factory() as session:
            service = session.get(DeploymentService, service_id)
            instance = session.scalar(
                select(DeploymentInstance).where(
                    DeploymentInstance.deployment_service_id == service_id
                )
            )
            assert service is not None and instance is not None
            service.status = "running"
            instance.status = "running"
            instance.health_status = "healthy"
            instance.container_id = "a" * 64
            instance.rollback_metadata = {
                "container_id": "c" * 64,
                "image_digest": "registry.internal/visiox/yolo26-inference@sha256:"
                + "d" * 64,
                "model_checksum": "e" * 64,
                "engine": "engine",
                "engine_digest": "f" * 64,
                "port": 18080,
            }
            session.commit()
        producer.enqueued.clear()
        response = client.post(f"/services/{service_id}/rollback")

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "rollback_queued"
    assert body["health_status"] == "healthy"
    assert producer.enqueued[0]["task_type"] == TaskType.EDGE_ROLLBACK


def test_rollback_without_prior_healthy_tuple_is_rejected(service_runtime) -> None:
    app, _factory, _producer = service_runtime
    with TestClient(app) as client:
        created = client.post("/services", json=_request())
        response = client.post(f"/services/{created.json()['id']}/rollback")

    assert response.status_code == 409


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"image_digest": "registry/image:latest"}, "immutable"),
        ({"precision": "int8"}, "calibration"),
        ({"node_id": "node-missing"}, "Resource not found"),
    ],
)
def test_create_service_rejects_invalid_production_request(
    service_runtime,
    overrides,
    message,
) -> None:
    app, _factory, producer = service_runtime
    with TestClient(app) as client:
        response = client.post("/services", json=_request(**overrides))

    assert response.status_code in {404, 422}
    assert message.casefold() in response.text.casefold()
    assert producer.enqueued == []


def test_service_deployment_rejects_model_from_another_pipeline(
    service_runtime,
) -> None:
    app, factory, producer = service_runtime
    with factory() as session:
        session.add_all(
            [
                TrainingPipeline(
                    id="pipeline-2",
                    name="two",
                    task="detect",
                    scale="n",
                    status="success",
                ),
                TrainedModel(
                    id="model-2",
                    pipeline_id="pipeline-2",
                    name="best.pt",
                    version="best.pt",
                    task="detect",
                    artifact_uri="minio://models/trained/detect/best.pt",
                    status="ready",
                ),
            ]
        )
        session.commit()

    with TestClient(app) as client:
        response = client.post(
            "/services",
            json=_request(name="invalid", trained_model_id="model-2"),
        )

    assert response.status_code == 409
    assert producer.enqueued == []
