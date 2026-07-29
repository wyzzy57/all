import asyncio
import json

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from visiox_common.tasks import (
    EDGE_EXECUTOR_RESOURCE_REFS,
    EDGE_EXECUTOR_TASK_TYPES,
    TaskCommand,
    TaskType,
)
from visiox_db.base import Base
from visiox_db.models import (
    ComputeNode,
    DeploymentInstance,
    DeploymentService,
    DistributedTrainingRun,
    EdgeSshCredential,
    RemoteExecution,
    ResourcePool,
    TrainingJob,
    TrainingPipeline,
)
from visiox_messaging.streams import RedisStreamProducer, STREAM_BY_TASK_TYPE


def test_ssh_edge_models_persist_runtime_state_and_enforce_unique_keys():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        pool = ResourcePool(name="ssh-pool", kind="nvidia", selector={}, compatibility_policy={})
        session.add(pool)
        session.flush()
        node = ComputeNode(
            name="ssh-node-01",
            resource_pool_id=pool.id,
            status="online",
            architecture="x86_64",
            platform_kind="nvidia",
            capabilities={},
            resources={},
            fingerprint={},
            agent_version="0.1.0",
        )
        pipeline = TrainingPipeline(name="ssh-runtime", task="detect", scale="n", params_template={})
        session.add_all([node, pipeline])
        session.flush()
        service = DeploymentService(
            name="ssh-service",
            pipeline_id=pipeline.id,
            model_name="yolo26n",
            model_weight="yolo26n.pt",
            environment="edge",
            endpoint="http://pending",
        )
        training_job = TrainingJob(pipeline_id=pipeline.id, params={})
        session.add_all([service, training_job])
        session.flush()

        credential = EdgeSshCredential(
            node_id=node.id,
            ssh_host="10.0.0.10",
            host_key_type="ssh-ed25519",
            host_key_fingerprint="SHA256:fingerprint",
            public_key="ssh-ed25519 public-key",
            encrypted_private_key=b"ciphertext",
            encryption_nonce=b"nonce",
        )
        execution = RemoteExecution(
            node_id=node.id,
            deployment_service_id=service.id,
            operation="deploy",
            idempotency_key="deploy-service-1-attempt-1",
        )
        instance = DeploymentInstance(
            deployment_service_id=service.id,
            node_id=node.id,
            instance_name="primary",
            engine="tensorrt",
            status="queued",
        )
        distributed_run = DistributedTrainingRun(
            training_job_id=training_job.id,
            resource_pool_id=pool.id,
            node_ids=[node.id],
            ranks=[{"node_id": node.id, "node_rank": 0, "local_rank": 0}],
            master_addr="10.0.0.10",
            master_port=29500,
        )
        session.add_all([credential, execution, instance, distributed_run])
        session.commit()

        assert session.scalar(select(EdgeSshCredential).where(EdgeSshCredential.node_id == node.id)) is not None
        assert session.scalar(select(RemoteExecution).where(RemoteExecution.idempotency_key == execution.idempotency_key))
        assert session.scalar(select(DeploymentInstance).where(DeploymentInstance.deployment_service_id == service.id))
        assert session.scalar(select(DistributedTrainingRun).where(DistributedTrainingRun.training_job_id == training_job.id))

        session.add(RemoteExecution(node_id=node.id, operation="deploy", idempotency_key=execution.idempotency_key))
        with pytest.raises(IntegrityError):
            session.commit()


def test_edge_task_types_use_identifier_only_stream_payload():
    command = TaskCommand(
        task_id="task-1",
        task_type=TaskType.EDGE_DEPLOY,
        resource_refs={"remote_execution_id": "exec-1"},
    )
    fields = command.to_stream_fields()

    assert EDGE_EXECUTOR_TASK_TYPES == {
        TaskType.EDGE_PROBE,
        TaskType.EDGE_DEPLOY,
        TaskType.EDGE_STOP_DEPLOYMENT,
        TaskType.EDGE_START_DEPLOYMENT,
        TaskType.EDGE_RESTART_DEPLOYMENT,
        TaskType.EDGE_ROLLBACK,
        TaskType.EDGE_TRAIN,
        TaskType.EDGE_STOP_TRAINING,
        TaskType.EDGE_RESUME_TRAINING,
    }
    assert {STREAM_BY_TASK_TYPE[task_type] for task_type in EDGE_EXECUTOR_TASK_TYPES} == {
        "stream:edge_executor.commands"
    }
    assert json.loads(fields["resource_refs"]) == {"remote_execution_id": "exec-1"}
    assert json.loads(fields["payload"]) == {}


def test_each_edge_task_type_has_an_exact_required_resource_ref():
    expected_refs = {
        task_type: {"remote_execution_id": "exec-1"}
        for task_type in EDGE_EXECUTOR_TASK_TYPES
    }

    assert EDGE_EXECUTOR_RESOURCE_REFS == {
        task_type: frozenset(resource_refs) for task_type, resource_refs in expected_refs.items()
    }
    for task_type, resource_refs in expected_refs.items():
        command = TaskCommand(task_id="task-1", task_type=task_type, resource_refs=resource_refs)
        assert command.resource_refs == resource_refs


def test_dedicated_edge_enqueue_serializes_only_remote_execution_id() -> None:
    class FakeRedis:
        def __init__(self) -> None:
            self.calls = []

        async def xadd(self, stream_name, fields):
            self.calls.append((stream_name, fields))
            return b"1-0"

    redis_client = FakeRedis()
    producer = RedisStreamProducer(redis_client)

    message_id = asyncio.run(
        producer.enqueue_edge_execution(
            task_id="task-1",
            task_type=TaskType.EDGE_DEPLOY,
            remote_execution_id="exec-1",
        )
    )

    assert message_id == "1-0"
    stream_name, fields = redis_client.calls[0]
    assert stream_name == "stream:edge_executor.commands"
    assert json.loads(fields["resource_refs"]) == {"remote_execution_id": "exec-1"}
    assert json.loads(fields["payload"]) == {}


@pytest.mark.parametrize(
    ("resource_refs", "payload"),
    [
        ({"password_id": "super-secret"}, {}),
        ({"remote_execution_id": "https://storage.test/model?X-Amz-Signature=secret"}, {}),
        ({"remote_execution_id": "-----BEGIN PRIVATE KEY-----"}, {}),
        ({"remote_execution_id": "exec 1"}, {}),
        ({}, {}),
        ({"remote_execution_id": "exec-1", "node_id": "node-1"}, {}),
        ({"deployment_service_id": "service-1"}, {}),
        ({"remote_execution_id": "exec-1"}, {"password": "not-allowed"}),
    ],
)
def test_edge_task_commands_reject_sensitive_invalid_missing_or_extra_refs(resource_refs, payload):
    with pytest.raises(ValidationError, match="identifier-only"):
        TaskCommand(
            task_id="task-1",
            task_type=TaskType.EDGE_DEPLOY,
            resource_refs=resource_refs,
            payload=payload,
        )


@pytest.mark.parametrize(
    ("resource_refs", "payload", "sensitive_value"),
    [
        ({"remote_execution_id": "exec-1"}, {"password": "super-secret"}, "super-secret"),
        (
            {"remote_execution_id": "https://storage.test/model?X-Amz-Signature=signed-secret"},
            {},
            "signed-secret",
        ),
        ({"remote_execution_id": "-----BEGIN PRIVATE KEY-----"}, {}, "PRIVATE KEY"),
        ({"remote_execution_id": "exec-bearer-token"}, {}, "bearer-token"),
    ],
)
def test_edge_task_serialization_revalidates_mutated_current_state(resource_refs, payload, sensitive_value):
    command = TaskCommand(
        task_id="task-1",
        task_type=TaskType.EDGE_DEPLOY,
        resource_refs={"remote_execution_id": "exec-1"},
    )
    command.resource_refs = resource_refs
    command.payload = payload

    with pytest.raises(ValueError, match="identifier-only") as exc_info:
        command.to_stream_fields()

    assert sensitive_value not in str(exc_info.value)
