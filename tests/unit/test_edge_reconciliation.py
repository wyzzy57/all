from __future__ import annotations

import json
import threading
from types import SimpleNamespace

from sqlalchemy import create_engine

from visiox_common.settings import Settings
from visiox_db.base import Base
from visiox_db.models import (
    ComputeNode,
    DeploymentInstance,
    DeploymentService,
    EdgeSshCredential,
    RemoteExecution,
    TrainedModel,
    TrainingPipeline,
)
from visiox_db.session import create_session_factory
from visiox_edge_executor_worker.crypto import CredentialCipher
from visiox_edge_executor_worker.reconciliation import (
    DockerLabels,
    RemoteRuntimeReconciler,
)
from visiox_edge_executor_worker.runner import build_application
from visiox_edge_executor_worker.scripts import load_packaged_script
from visiox_edge_executor_worker.ssh import CommandResult, RemotePrivateDirectory
from visiox_edge_executor_worker.startup import EdgeExecutorSecurityContext
from visiox_edge_executor_worker.state import ExecutionResult, RemoteExecutionRepository


class RecordingSshSession:
    def __init__(self, response: dict[str, object] | list[dict[str, object]]) -> None:
        self.responses = list(response) if isinstance(response, list) else [response]
        self.uploads: list[tuple[str, bytes]] = []
        self.validated: list[str] = []
        self.commands: list[str] = []
        self.cleaned: list[tuple[str, ...]] = []
        self.closed = False

    def create_private_directory(
        self, *, timeout_seconds: float
    ) -> RemotePrivateDirectory:
        assert timeout_seconds > 0
        return RemotePrivateDirectory(
            "/home/visiox-edge/.visiox-0123456789abcdef0123456789abcdef", 1000
        )

    def upload_bytes_exclusive(
        self,
        data: bytes,
        remote_path: str,
        *,
        expected_owner_uid: int | None,
        timeout_seconds: float,
    ) -> None:
        assert expected_owner_uid == 1000
        assert timeout_seconds > 0
        self.uploads.append((remote_path, data))

    def validate_remote_file(
        self,
        remote_path: str,
        *,
        expected_owner_uid: int | None,
        timeout_seconds: float,
    ) -> None:
        assert expected_owner_uid == 1000
        self.validated.append(remote_path)

    def run(self, command: str, *, timeout_seconds: float) -> CommandResult:
        self.commands.append(command)
        response = (
            self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        )
        return CommandResult(
            exit_status=0,
            stdout=json.dumps(response).encode("ascii"),
            stderr=b"",
        )

    def cleanup_private_directory(
        self,
        directory: RemotePrivateDirectory,
        remote_paths: tuple[str, ...],
        *,
        timeout_seconds: float,
    ) -> None:
        self.cleaned.append(remote_paths)

    def close(self) -> None:
        self.closed = True


class RecordingSshClient:
    def __init__(self, ssh_session: RecordingSshSession) -> None:
        self.ssh_session = ssh_session
        self.connections: list[dict[str, object]] = []

    def connect(self, **request):
        self.connections.append(request)
        return self.ssh_session


def _runtime(
    response: dict[str, object],
) -> tuple[RemoteRuntimeReconciler, RemoteExecutionRepository, RecordingSshClient]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    cipher = CredentialCipher(b"a" * 32, key_version=1)
    encrypted = cipher.encrypt(b"private-key")
    with session_factory() as session:
        session.add_all(
            [
                RemoteExecution(
                    id="exec-1",
                    node_id="node-1",
                    operation="deploy",
                    status="running",
                    resource_type="deployment_service",
                    resource_id="service-1",
                    idempotency_key="deploy-service-1-attempt-1",
                ),
                EdgeSshCredential(
                    node_id="node-1",
                    ssh_host="10.0.0.10",
                    ssh_port=22,
                    ssh_user="visiox-edge",
                    host_key_type="ssh-ed25519",
                    host_key_fingerprint="SHA256:pinned",
                    public_key="ssh-ed25519 public",
                    encrypted_private_key=encrypted.ciphertext,
                    encryption_nonce=encrypted.nonce,
                    key_version=encrypted.key_version,
                ),
            ]
        )
        session.commit()
    ssh_session = RecordingSshSession(response)
    ssh_client = RecordingSshClient(ssh_session)
    repository = RemoteExecutionRepository(session_factory)
    reconciler = RemoteRuntimeReconciler(
        session_factory,
        repository,
        EdgeExecutorSecurityContext(credential_cipher=cipher, ssh_client=ssh_client),
    )
    return reconciler, repository, ssh_client


def test_reconciler_strictly_inspects_remote_docker_by_stable_labels() -> None:
    reconciler, repository, ssh_client = _runtime({"containers": []})

    assert reconciler.reconcile_startup() == 1

    execution = repository.load("exec-1")
    assert execution is not None
    assert execution.status == "queued"
    assert execution.phase == "reconciliation_pending"
    assert ssh_client.connections == [
        {
            "host": "10.0.0.10",
            "port": 22,
            "username": "visiox-edge",
            "private_key": b"private-key",
            "expected_fingerprint": "SHA256:pinned",
            "timeout_seconds": 30.0,
        }
    ]
    ssh_session = ssh_client.ssh_session
    assert [path.rsplit("/", 1)[-1] for path, _ in ssh_session.uploads] == [
        "inspect_runtime.sh",
        "request.json",
    ]
    request = json.loads(ssh_session.uploads[1][1])
    assert request == {
        "labels": {
            DockerLabels.MANAGED: "true",
            DockerLabels.REMOTE_EXECUTION_ID: "exec-1",
            DockerLabels.NODE_ID: "node-1",
            DockerLabels.RESOURCE_TYPE: "deployment_service",
            DockerLabels.RESOURCE_ID: "service-1",
        }
    }
    assert "exec-1" not in ssh_session.commands[0]
    assert ssh_session.cleaned
    assert ssh_session.closed is True


def test_reconciler_persists_deterministic_terminal_remote_failure() -> None:
    reconciler, repository, _ = _runtime(
        {
            "containers": [
                {
                    "id": "a" * 64,
                    "status": "exited",
                    "exit_code": 137,
                    "oom_killed": True,
                    "health": None,
                }
            ]
        }
    )

    assert reconciler.reconcile_startup() == 1

    execution = repository.load("exec-1")
    assert execution is not None
    assert execution.status == "failed"
    assert execution.error_code == "REMOTE_RUNTIME_FAILED"
    assert execution.error_message == "Remote runtime exited unsuccessfully"


def test_reconciler_contains_three_or_more_matching_remote_runtimes() -> None:
    reconciler, repository, _ = _runtime(
        {
            "containers": [
                {
                    "id": character * 64,
                    "status": "running",
                    "exit_code": 0,
                    "oom_killed": False,
                    "health": "healthy",
                }
                for character in ("a", "b", "c")
            ]
        }
    )

    assert reconciler.reconcile_startup() == 1

    execution = repository.load("exec-1")
    assert execution is not None
    assert execution.status == "failed"
    assert execution.phase == "reconciliation_failed"
    assert execution.error_code == "REMOTE_STATE_AMBIGUOUS"
    assert execution.error_message == "Multiple remote runtimes matched one execution"


def test_reconciler_persists_recoverable_state_for_invalid_remote_response() -> None:
    reconciler, repository, _ = _runtime({"unexpected": "password=secret"})

    assert reconciler.reconcile_startup() == 1

    execution = repository.load("exec-1")
    assert execution is not None
    assert execution.status == "queued"
    assert execution.phase == "reconciliation_retry"
    assert execution.error_code == "REMOTE_INSPECTION_RETRY"
    assert execution.error_message == "Remote runtime inspection must be retried"


def test_reconciler_redacts_corrupt_credential_failures(monkeypatch) -> None:
    reconciler, repository, _ = _runtime({"containers": []})

    def fail_to_decrypt(node_id: str):
        raise ValueError("client_secret=credential-secret")

    monkeypatch.setattr(reconciler, "_load_target", fail_to_decrypt)

    assert reconciler.reconcile_startup() == 1

    execution = repository.load("exec-1")
    assert execution is not None
    assert execution.status == "queued"
    assert execution.error_code == "REMOTE_INSPECTION_RETRY"
    assert execution.error_message == "Remote runtime inspection must be retried"
    assert "secret" not in execution.error_message.casefold()


def test_reconciler_observes_shutdown_between_remote_executions(monkeypatch) -> None:
    reconciler, repository, _ = _runtime({"containers": []})
    executions = [SimpleNamespace(id=f"exec-{index}") for index in range(3)]
    stopped = threading.Event()
    visited: list[str] = []

    monkeypatch.setattr(repository, "list_reconcilable", lambda: executions)

    def reconcile_one(execution):
        visited.append(execution.id)
        stopped.set()
        return ExecutionResult.failed(
            error_code="STOP_TEST",
            error_message="fixed",
            phase="test",
        )

    monkeypatch.setattr(reconciler, "_reconcile", reconcile_one)
    monkeypatch.setattr(repository, "finalize", lambda *args: None)

    assert reconciler.reconcile_startup(stop_requested=stopped.is_set) == 1
    assert visited == ["exec-0"]


def test_build_application_installs_concrete_reconciler(tmp_path, monkeypatch) -> None:
    secret_path = tmp_path / "master-key"
    secret_path.write_bytes(b"a" * 32)
    settings = Settings(_env_file=None, edge_credential_master_key_file=secret_path)
    fake_redis = SimpleNamespace()

    monkeypatch.setattr(
        "visiox_edge_executor_worker.runner.create_session_factory",
        lambda: lambda: None,
    )

    application = build_application(
        settings,
        redis_factory=lambda url: fake_redis,
        server_factory=lambda *args, **kwargs: SimpleNamespace(),
    )

    assert isinstance(application.reconciler, RemoteRuntimeReconciler)


def test_static_reconciliation_script_is_packaged_and_uses_structured_docker_calls() -> (
    None
):
    script = load_packaged_script("inspect_runtime.sh").decode("utf-8")

    assert "subprocess.run" in script
    assert "shell=True" not in script
    assert "docker inspect" not in script
    assert "request_path" in script
    assert "len(container_ids) >" not in script


def _deployment_runtime(response: dict[str, object] | list[dict[str, object]]):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    cipher = CredentialCipher(b"a" * 32, key_version=1)
    encrypted = cipher.encrypt(b"private-key")
    image_digest = "registry.internal/visiox/yolo26-inference@sha256:" + "b" * 64
    with session_factory() as session:
        pipeline = TrainingPipeline(
            id="pipeline-1",
            name="pipeline",
            task="detect",
            scale="n",
            status="success",
        )
        model = TrainedModel(
            id="model-1",
            pipeline_id=pipeline.id,
            name="best.pt",
            version="best.pt",
            task="detect",
            artifact_uri="minio://models/model-1/best.pt",
            status="ready",
        )
        node = ComputeNode(
            id="node-1",
            name="node",
            status="online",
            architecture="x86_64",
            platform_kind="x86_nvidia",
            capabilities={},
            resources={},
            fingerprint={},
            agent_version="ssh-bootstrap",
        )
        service = DeploymentService(
            id="service-1",
            name="service",
            pipeline_id=pipeline.id,
            trained_model_id=model.id,
            model_name="yolo26n.pt",
            model_weight="best.pt",
            environment="node",
            instance_count=1,
            instance_name="pepper-prod-01",
            status="running",
            desired_state="running",
            active_revision=1,
            endpoint="pending",
            config={},
        )
        instance = DeploymentInstance(
            id="instance-1",
            deployment_service_id=service.id,
            deployment_revision=1,
            node_id=node.id,
            instance_name=service.instance_name,
            container_id="a" * 64,
            image_digest=image_digest,
            model_checksum="c" * 64,
            engine="engine",
            engine_digest="d" * 64,
            port=18080,
            status="running",
            health_status="healthy",
            rollback_metadata={},
        )
        execution = RemoteExecution(
            id="exec-deploy",
            node_id=node.id,
            deployment_service_id=service.id,
            resource_type="deployment_instance",
            resource_id=instance.id,
            operation="deploy",
            phase="warming_up",
            status="succeeded",
            idempotency_key="deploy-instance-1-attempt-1",
        )
        credential = EdgeSshCredential(
            node_id=node.id,
            ssh_host="10.0.0.10",
            ssh_port=22,
            ssh_user="visiox-edge",
            host_key_type="ssh-ed25519",
            host_key_fingerprint="SHA256:pinned",
            public_key="ssh-ed25519 public",
            encrypted_private_key=encrypted.ciphertext,
            encryption_nonce=encrypted.nonce,
            key_version=encrypted.key_version,
        )
        session.add_all(
            [pipeline, model, node, service, instance, execution, credential]
        )
        session.commit()
    ssh_session = RecordingSshSession(response)
    ssh_client = RecordingSshClient(ssh_session)
    repository = RemoteExecutionRepository(session_factory)
    reconciler = RemoteRuntimeReconciler(
        session_factory,
        repository,
        EdgeExecutorSecurityContext(credential_cipher=cipher, ssh_client=ssh_client),
    )
    return reconciler, session_factory, ssh_client, image_digest


def _healthy_deployment_response(image_digest: str) -> dict[str, object]:
    return {
        "containers": [
            {
                "id": "a" * 64,
                "status": "running",
                "health": "healthy",
                "labels": {
                    "com.visiox.deployment-instance-id": "instance-1",
                    "com.visiox.image-digest": image_digest,
                    "com.visiox.model-checksum": "c" * 64,
                    "com.visiox.engine": "engine",
                    "com.visiox.engine-digest": "d" * 64,
                    "com.visiox.port": "18080",
                },
                "endpoint_reachable": True,
            }
        ]
    }


def test_reconciler_recovers_running_deployment_from_exact_observed_tuple() -> None:
    image_digest = "registry.internal/visiox/yolo26-inference@sha256:" + "b" * 64
    reconciler, factory, ssh_client, _ = _deployment_runtime(
        _healthy_deployment_response(image_digest)
    )

    assert reconciler.reconcile_startup() == 1

    with factory() as session:
        service = session.get(DeploymentService, "service-1")
        instance = session.get(DeploymentInstance, "instance-1")
        execution = session.get(RemoteExecution, "exec-deploy")
        assert service is not None and service.status == "running"
        assert service.endpoint == "http://10.0.0.10:18080"
        assert instance is not None and instance.status == "running"
        assert instance.endpoint == service.endpoint
        assert instance.health_status == "healthy"
        assert instance.health_checked_at is not None
        assert execution is not None and execution.phase == "reconciled_running"
        assert execution.error_code is None
    uploaded_names = [
        path.rsplit("/", 1)[-1] for path, _ in ssh_client.ssh_session.uploads
    ]
    assert uploaded_names == ["inspect_deployment.sh", "request.json"]
    request = json.loads(ssh_client.ssh_session.uploads[1][1])
    assert request == {
        "labels": {"com.visiox.deployment-instance-id": "instance-1"},
        "expected_container_id": "a" * 64,
        "port": 18080,
    }


def test_reconciler_matches_extended_paddlex_deployment_identity() -> None:
    image_digest = (
        "registry.internal/visiox/paddlex-inference@sha256:" + "b" * 64
    )
    response = _healthy_deployment_response(image_digest)
    runtime_checksum = "9" * 64
    labels = response["containers"][0]["labels"]  # type: ignore[index]
    labels.update(  # type: ignore[union-attr]
        {
            "com.visiox.framework": "paddlex",
            "com.visiox.adapter-key": "paddlex.object_detection.v1",
            "com.visiox.adapter-version": "1.0.0",
            "com.visiox.model-format": "paddle_inference_bundle",
            "com.visiox.resolved-backend": "paddlex_hpi_tensorrt",
            "com.visiox.runtime-digest": image_digest,
            "com.visiox.runtime-config-checksum": runtime_checksum,
        }
    )
    labels["com.visiox.engine"] = "paddle_inference_bundle"  # type: ignore[index]
    reconciler, factory, _ssh_client, _ = _deployment_runtime(response)
    with factory() as session:
        service = session.get(DeploymentService, "service-1")
        instance = session.get(DeploymentInstance, "instance-1")
        assert service is not None and instance is not None
        service.config = {
            "deployment": {
                "framework": "paddlex",
                "adapter_key": "paddlex.object_detection.v1",
                "adapter_version": "1.0.0",
                "model_format": "paddle_inference_bundle",
                "resolved_backend": "paddlex_hpi_tensorrt",
                    "runtime_image_digest": image_digest,
                    "runtime_config_checksum": runtime_checksum,
            }
        }
        instance.image_digest = image_digest
        instance.engine = "paddle_inference_bundle"
        session.add_all([service, instance])
        session.commit()

    assert reconciler.reconcile_startup() == 1

    with factory() as session:
        service = session.get(DeploymentService, "service-1")
        instance = session.get(DeploymentInstance, "instance-1")
        assert service is not None and service.status == "running"
        assert instance is not None and instance.health_status == "healthy"


def test_reconciler_recovers_failed_deployment_when_remote_container_is_healthy() -> (
    None
):
    image_digest = "registry.internal/visiox/yolo26-inference@sha256:" + "b" * 64
    reconciler, factory, _ssh_client, _ = _deployment_runtime(
        _healthy_deployment_response(image_digest)
    )
    with factory() as session:
        service = session.get(DeploymentService, "service-1")
        instance = session.get(DeploymentInstance, "instance-1")
        execution = session.get(RemoteExecution, "exec-deploy")
        assert service is not None
        assert instance is not None
        assert execution is not None
        service.status = "failed"
        instance.status = "failed"
        instance.health_status = "unhealthy"
        execution.phase = "reconciliation_failed"
        execution.error_code = "REMOTE_DEPLOYMENT_UNHEALTHY"
        execution.error_message = "Remote deployment is not healthy"
        session.add_all([service, instance, execution])
        session.commit()

    assert reconciler.reconcile_deployments() == 1

    with factory() as session:
        service = session.get(DeploymentService, "service-1")
        instance = session.get(DeploymentInstance, "instance-1")
        execution = session.get(RemoteExecution, "exec-deploy")
        assert service is not None and service.status == "running"
        assert instance is not None and instance.status == "running"
        assert instance.health_status == "healthy"
        assert execution is not None and execution.phase == "reconciled_running"
        assert execution.error_code is None


def test_reconciler_preserves_user_stopped_service_without_remote_inspection() -> None:
    reconciler, factory, ssh_client, _ = _deployment_runtime({"containers": []})
    with factory() as session:
        service = session.get(DeploymentService, "service-1")
        instance = session.get(DeploymentInstance, "instance-1")
        assert service is not None and instance is not None
        service.status = "stopped"
        service.desired_state = "stopped"
        instance.status = "stopped"
        instance.health_status = "stopped"
        session.commit()

    assert reconciler.reconcile_deployments() == 0
    assert ssh_client.connections == []


def test_reconciler_starts_stopped_container_when_desired_state_is_running() -> None:
    image_digest = "registry.internal/visiox/yolo26-inference@sha256:" + "b" * 64
    stopped = _healthy_deployment_response(image_digest)
    stopped["containers"][0]["status"] = "exited"  # type: ignore[index]
    stopped["containers"][0]["health"] = None  # type: ignore[index]
    stopped["containers"][0]["endpoint_reachable"] = False  # type: ignore[index]
    started = {
        "already_running": False,
        "container_id": "a" * 64,
        "health_status": "healthy",
        "port": 18080,
    }
    reconciler, factory, ssh_client, _ = _deployment_runtime([stopped, started])
    with factory() as session:
        service = session.get(DeploymentService, "service-1")
        instance = session.get(DeploymentInstance, "instance-1")
        assert service is not None and instance is not None
        service.status = "stopped"
        service.desired_state = "running"
        instance.status = "stopped"
        instance.health_status = "stopped"
        session.commit()

    assert reconciler.reconcile_deployments() == 1

    with factory() as session:
        service = session.get(DeploymentService, "service-1")
        instance = session.get(DeploymentInstance, "instance-1")
        assert service is not None and service.status == "running"
        assert instance is not None and instance.status == "running"
        assert instance.health_status == "healthy"
    assert any(
        "start_deployment.sh" in command for command in ssh_client.ssh_session.commands
    )


def test_reconciler_retries_deployment_while_container_health_is_starting() -> None:
    image_digest = "registry.internal/visiox/yolo26-inference@sha256:" + "b" * 64
    response = _healthy_deployment_response(image_digest)
    response["containers"][0]["health"] = "starting"  # type: ignore[index]
    response["containers"][0]["endpoint_reachable"] = False  # type: ignore[index]
    reconciler, factory, _ssh_client, _ = _deployment_runtime(response)

    assert reconciler.reconcile_deployments() == 1

    with factory() as session:
        service = session.get(DeploymentService, "service-1")
        instance = session.get(DeploymentInstance, "instance-1")
        execution = session.get(RemoteExecution, "exec-deploy")
        assert service is not None and service.status == "reconciliation_retry"
        assert instance is not None and instance.status == "reconciliation_retry"
        assert instance.health_status == "unknown"
        assert execution is not None and execution.phase == "reconciliation_retry"


def test_reconciler_persists_deterministic_failure_for_mismatched_deployment() -> None:
    image_digest = "registry.internal/visiox/yolo26-inference@sha256:" + "b" * 64
    response = _healthy_deployment_response(image_digest)
    response["containers"][0]["labels"]["com.visiox.model-checksum"] = "f" * 64  # type: ignore[index]
    reconciler, factory, _ssh_client, _ = _deployment_runtime(response)

    assert reconciler.reconcile_startup() == 1

    with factory() as session:
        service = session.get(DeploymentService, "service-1")
        instance = session.get(DeploymentInstance, "instance-1")
        execution = session.get(RemoteExecution, "exec-deploy")
        assert service is not None and service.status == "failed"
        assert service.endpoint == "pending"
        assert instance is not None and instance.status == "failed"
        assert instance.health_status == "unhealthy"
        assert execution is not None
        assert execution.phase == "reconciliation_failed"
        assert execution.error_code == "REMOTE_DEPLOYMENT_MISMATCH"
        assert execution.error_message == "Remote deployment state did not match"


def test_reconciler_does_not_inspect_deployment_while_optimization_is_active() -> None:
    reconciler, factory, ssh_client, _ = _deployment_runtime({"containers": []})
    with factory() as session:
        service = session.get(DeploymentService, "service-1")
        instance = session.get(DeploymentInstance, "instance-1")
        execution = session.get(RemoteExecution, "exec-deploy")
        assert service is not None
        assert instance is not None
        assert execution is not None
        service.status = "optimizing"
        instance.status = "optimizing"
        instance.health_status = "starting"
        instance.container_id = None
        instance.engine_digest = None
        execution.status = "running"
        execution.phase = "optimizing"
        session.add_all([service, instance, execution])
        session.commit()

    assert reconciler.reconcile_deployments() == 0

    with factory() as session:
        service = session.get(DeploymentService, "service-1")
        instance = session.get(DeploymentInstance, "instance-1")
        execution = session.get(RemoteExecution, "exec-deploy")
        assert service is not None and service.status == "optimizing"
        assert instance is not None and instance.status == "optimizing"
        assert instance.health_status == "starting"
        assert execution is not None and execution.phase == "optimizing"
        assert execution.error_code is None
    assert ssh_client.connections == []


def test_inspect_deployment_script_uses_stable_label_and_structured_commands() -> None:
    script = load_packaged_script("inspect_deployment.sh").decode("utf-8")
    embedded = script.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    namespace: dict[str, object] = {"__name__": "visiox_script_test"}
    exec(compile(embedded, "inspect_deployment.sh", "exec"), namespace)
    request = namespace["_validate_request"](  # type: ignore[operator]
        {
            "labels": {"com.visiox.deployment-instance-id": "instance-1"},
            "expected_container_id": "a" * 64,
            "port": 18080,
        }
    )

    assert namespace["_list_command"](request) == [  # type: ignore[operator]
        "docker",
        "ps",
        "-a",
        "--filter",
        "label=com.visiox.deployment-instance-id=instance-1",
        "--format",
        "{{.ID}}",
    ]
    assert "shell=True" not in script
    assert "docker inspect" not in script
