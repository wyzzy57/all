from __future__ import annotations

from collections.abc import Generator
import json
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from visiox_api.routes.services import (
    get_service_session,
    get_service_stream_producer,
    router,
)
from visiox_common.tasks import TaskType
from visiox_db.models import (
    ComputeNode,
    DeploymentInstance,
    DeploymentService,
    RemoteExecution,
    ResourcePool,
    Task,
    TrainedModel,
    TrainingPipeline,
)
from visiox_edge_executor_worker.inventory import compatibility_policy, parse_inventory


FIXTURES = Path(__file__).parents[1] / "fixtures" / "edge_inventory"
MODEL_CHECKSUM = "a" * 64
IMAGE_DIGEST = "registry.internal/visiox/yolo26-inference@sha256:" + "b" * 64


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
    inventory = parse_inventory(
        json.loads((FIXTURES / "x86.json").read_text(encoding="utf-8"))
    )
    with factory() as session:
        pool = ResourcePool(
            id="pool-x86",
            name="x86-production",
            kind=inventory.platform_kind,
            selector=compatibility_policy(inventory),
            compatibility_policy=compatibility_policy(inventory),
            enabled=True,
        )
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
            artifact_uri="minio://models/trained/detect/best.pt",
            metrics={},
            status="ready",
        )
        node = ComputeNode(
            id="node-1",
            name="gpu-node-1",
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
        session.add_all([pool, pipeline, model, node])
        session.commit()

    producer = FakeEdgeProducer()
    app = FastAPI()
    app.include_router(router)

    def override_session() -> Generator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_service_session] = override_session
    app.dependency_overrides[get_service_stream_producer] = lambda: producer
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


def test_upgrade_queues_deploy_without_overwriting_healthy_observed_tuple(
    service_runtime,
) -> None:
    app, factory, producer = service_runtime
    next_image = "registry.internal/visiox/yolo26-inference@sha256:" + "c" * 64
    next_checksum = "d" * 64
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
        ({"node_id": "node-missing"}, "Node not found"),
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


def test_service_deployment_rejects_model_from_another_pipeline(service_runtime) -> None:
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
