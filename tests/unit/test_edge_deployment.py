from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from visiox_edge_executor_worker.deployment import (
    DeployInferenceHandler,
    DeploymentOptions,
    ModelArtifact,
    RollbackDeploymentHandler,
    StopDeploymentHandler,
    build_deployment_plan,
    build_deployment_handlers,
    engine_cache_key,
    validate_image_digest,
)
from visiox_edge_executor_worker.crypto import CredentialCipher
from visiox_edge_executor_worker.inventory import parse_inventory
from visiox_edge_executor_worker.scripts import load_packaged_script
from visiox_edge_executor_worker.ssh import CommandResult, RemotePrivateDirectory
from visiox_edge_executor_worker.startup import EdgeExecutorSecurityContext
from visiox_db.base import Base
from visiox_db.models import (
    ComputeNode,
    DeploymentInstance,
    DeploymentService,
    EdgeSshCredential,
    RemoteExecution,
    Task,
    TrainedModel,
    TrainingPipeline,
)


FIXTURES = Path(__file__).parents[1] / "fixtures" / "edge_inventory"
MODEL_CHECKSUM = "a" * 64
IMAGE_DIGEST = "registry.internal/visiox/yolo26-inference@sha256:" + "b" * 64
ENGINE_CACHE_KEY = engine_cache_key(
    model_checksum=MODEL_CHECKSUM,
    tensorrt_version="10.2.0.19-1+cuda12.5",
    compute_capability="8.9",
    precision="fp16",
    input_shape=(1, 3, 640, 640),
)


def _inventory(name: str = "x86.json"):
    return parse_inventory(json.loads((FIXTURES / name).read_text(encoding="utf-8")))


def _model(**overrides: object) -> ModelArtifact:
    values: dict[str, object] = {
        "task": "detect",
        "artifact_uri": "minio://models/trained/model-1/best.pt",
        "checksum": MODEL_CHECKSUM,
        "format": "pt",
    }
    values.update(overrides)
    return ModelArtifact.model_validate(values)


def test_auto_plan_prefers_tensorrt_fp16_and_explicit_inventory_gpu() -> None:
    plan = build_deployment_plan(_model(), _inventory(), DeploymentOptions())

    assert plan.export_format == "engine"
    assert plan.precision == "fp16"
    assert plan.input_shape == (1, 3, 640, 640)
    assert plan.gpu_uuids == ("GPU-x86-fixture",)
    assert plan.engine_cache_key == engine_cache_key(
        model_checksum=MODEL_CHECKSUM,
        tensorrt_version="10.2.0.19-1+cuda12.5",
        compute_capability="8.9",
        precision="fp16",
        input_shape=(1, 3, 640, 640),
    )


def test_engine_cache_key_uses_only_required_canonical_identity() -> None:
    identity = {
        "model_checksum": MODEL_CHECKSUM,
        "tensorrt_version": "10.2.0.19-1+cuda12.5",
        "compute_capability": "8.9",
        "precision": "fp16",
        "input_shape": [1, 3, 960, 960],
    }
    expected = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()

    assert engine_cache_key(
        model_checksum=MODEL_CHECKSUM,
        tensorrt_version="10.2.0.19-1+cuda12.5",
        compute_capability="8.9",
        precision="fp16",
        input_shape=(1, 3, 960, 960),
    ) == expected


def test_manual_onnx_fp32_shape_and_gpu_override() -> None:
    plan = build_deployment_plan(
        _model(),
        _inventory(),
        DeploymentOptions(
            format="onnx",
            precision="fp32",
            input_shape=(1, 3, 1280, 1280),
            gpu_uuids=("GPU-x86-fixture",),
        ),
    )

    assert plan.export_format == "onnx"
    assert plan.precision == "fp32"
    assert plan.input_shape == (1, 3, 1280, 1280)
    assert plan.engine_cache_key is None


def test_int8_requires_calibration_dataset() -> None:
    with pytest.raises(ValueError, match="calibration dataset"):
        build_deployment_plan(
            _model(),
            _inventory(),
            DeploymentOptions(precision="int8"),
        )

    plan = build_deployment_plan(
        _model(),
        _inventory(),
        DeploymentOptions(
            precision="int8",
            calibration_dataset_uri="minio://datasets/calibration/images.zip",
        ),
    )
    assert plan.precision == "int8"


@pytest.mark.parametrize(
    ("model", "options", "message"),
    [
        (_model(task="segment"), DeploymentOptions(), "detect"),
        (_model(format="engine"), DeploymentOptions(format="onnx"), "cannot convert"),
        (_model(), DeploymentOptions(gpu_uuids=("GPU-missing",)), "GPU UUID"),
    ],
)
def test_plan_rejects_unsupported_model_or_gpu(model, options, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        build_deployment_plan(model, _inventory(), options)


def test_plan_rejects_incompatible_task4_inventory() -> None:
    raw = json.loads((FIXTURES / "x86.json").read_text(encoding="utf-8"))
    raw["docker"]["runtimes"] = ["runc"]

    with pytest.raises(ValueError, match="compatible"):
        build_deployment_plan(_model(), parse_inventory(raw), DeploymentOptions())


@pytest.mark.parametrize(
    "value",
    [
        "registry.internal/visiox/yolo26-inference:latest",
        "registry.internal/visiox/yolo26-inference@sha256:short",
        "https://user:secret@registry.internal/image@sha256:" + "b" * 64,
    ],
)
def test_image_reference_must_be_immutable_and_credential_free(value: str) -> None:
    with pytest.raises(ValueError, match="immutable image digest"):
        validate_image_digest(value)

    assert validate_image_digest(IMAGE_DIGEST) == IMAGE_DIGEST


def test_deployment_scripts_are_packaged_static_python_drivers() -> None:
    for name in (
        "deploy_inference.sh",
        "inspect_deployment.sh",
        "stop_deployment.sh",
    ):
        script = load_packaged_script(name).decode("utf-8")
        assert "python3" in script
        assert "request.json" not in script
        assert "eval " not in script
        assert "docker " not in script
        assert "shell=True" not in script


class RecordingStorage:
    def __init__(self) -> None:
        self.requests: list[tuple[str, object]] = []

    def presigned_get_url(self, uri: str, *, expires) -> str:
        self.requests.append((uri, expires))
        return "https://minio.internal/object?X-Amz-Signature=short-lived-secret"


class DeploymentSshSession:
    def __init__(self, results: list[CommandResult]) -> None:
        self.results = list(results)
        self.uploads: list[tuple[bytes, str]] = []
        self.commands: list[str] = []
        self.run_timeouts: list[float] = []
        self.cleaned_paths: tuple[str, ...] | None = None
        self.closed = False

    def create_private_directory(self, *, timeout_seconds: float) -> RemotePrivateDirectory:
        assert 0 < timeout_seconds <= 60
        return RemotePrivateDirectory("/home/visiox-edge/.visiox-random", 1000)

    def upload_bytes_exclusive(
        self,
        data: bytes,
        remote_path: str,
        *,
        expected_owner_uid: int | None,
        timeout_seconds: float,
    ) -> None:
        assert expected_owner_uid == 1000
        assert 0 < timeout_seconds <= 60
        self.uploads.append((data, remote_path))

    def validate_remote_file(
        self,
        remote_path: str,
        *,
        expected_owner_uid: int | None,
        timeout_seconds: float,
    ) -> None:
        assert expected_owner_uid == 1000
        assert remote_path == self.uploads[-1][1]
        assert 0 < timeout_seconds <= 60

    def run(self, command: str, *, timeout_seconds: float) -> CommandResult:
        assert 0 < timeout_seconds <= 1800
        self.commands.append(command)
        self.run_timeouts.append(timeout_seconds)
        return self.results.pop(0)

    def cleanup_private_directory(
        self,
        workspace: RemotePrivateDirectory,
        remote_paths: tuple[str, ...],
        *,
        timeout_seconds: float,
    ) -> None:
        assert workspace.path == "/home/visiox-edge/.visiox-random"
        assert 0 < timeout_seconds <= 60
        self.cleaned_paths = remote_paths

    def close(self) -> None:
        self.closed = True


class DeploymentSshClient:
    def __init__(self, session: DeploymentSshSession) -> None:
        self.session = session
        self.connect_request: dict[str, object] | None = None

    def connect(self, **request):
        self.connect_request = request
        return self.session


def _deployment_database(*, prior_healthy: bool = False):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    cipher = CredentialCipher(b"m" * 32, key_version=1)
    encrypted = cipher.encrypt(b"private-key-fixture")
    inventory = _inventory()
    deployment_config = {
        "deployment": {
            "image_digest": IMAGE_DIGEST,
            "model_checksum": MODEL_CHECKSUM,
            "model_format": "pt",
            "format": "engine",
            "precision": "fp16",
            "input_shape": [1, 3, 640, 640],
            "gpu_uuids": ["GPU-x86-fixture"],
            "engine_cache_key": ENGINE_CACHE_KEY,
            "calibration_dataset_uri": None,
            "port": 18080,
        }
    }
    with factory() as database:
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
            artifact_uri="minio://models/trained/model-1/best.pt",
            status="ready",
        )
        node = ComputeNode(
            id="node-1",
            name="node",
            status="online",
            architecture=inventory.architecture,
            platform_kind=inventory.platform_kind,
            capabilities={},
            resources={},
            fingerprint={"inventory_snapshot": inventory.model_dump(mode="json")},
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
            status="queued",
            endpoint="pending",
            config=deployment_config,
        )
        instance = DeploymentInstance(
            id="instance-1",
            deployment_service_id=service.id,
            node_id=node.id,
            instance_name=service.instance_name,
            container_id="a" * 64 if prior_healthy else None,
            image_digest=(
                "registry.internal/visiox/yolo26-inference@sha256:" + "c" * 64
                if prior_healthy
                else IMAGE_DIGEST
            ),
            model_checksum="d" * 64 if prior_healthy else MODEL_CHECKSUM,
            engine="engine",
            engine_digest="e" * 64 if prior_healthy else ENGINE_CACHE_KEY,
            port=18080,
            status="running" if prior_healthy else "queued",
            health_status="healthy" if prior_healthy else "pending",
            rollback_metadata={},
        )
        task = Task(
            id="task-1",
            task_type="EDGE_DEPLOY",
            status="RUNNING",
            resource_type="deployment_service",
            resource_id=service.id,
        )
        execution = RemoteExecution(
            id="exec-1",
            node_id=node.id,
            task_id=task.id,
            deployment_service_id=service.id,
            resource_type="deployment_instance",
            resource_id=instance.id,
            operation="deploy",
            phase="dispatching",
            status="running",
            idempotency_key="deploy-instance-1-attempt-1",
        )
        credential = EdgeSshCredential(
            node_id=node.id,
            ssh_host="edge.internal",
            ssh_port=22,
            ssh_user="visiox-edge",
            host_key_type="ssh-ed25519",
            host_key_fingerprint="SHA256:pinned",
            public_key="ssh-ed25519 public",
            encrypted_private_key=encrypted.ciphertext,
            encryption_nonce=encrypted.nonce,
            key_version=encrypted.key_version,
        )
        database.add_all(
            [pipeline, model, node, service, instance, task, execution, credential]
        )
        database.commit()
    return factory, cipher


def _command_result(payload: dict[str, object], *, status: int = 0) -> CommandResult:
    return CommandResult(
        exit_status=status,
        stdout=json.dumps(payload, separators=(",", ":")).encode("ascii"),
        stderr=b"Authorization: Bearer remote-secret" if status else b"",
    )


def _load_execution(factory, operation: str = "deploy") -> RemoteExecution:
    with factory() as database:
        execution = database.get(RemoteExecution, "exec-1")
        assert execution is not None
        execution.operation = operation
        database.commit()
        return execution


def test_deploy_handler_presigns_at_execution_and_persists_healthy_upgrade() -> None:
    factory, cipher = _deployment_database(prior_healthy=True)
    ssh_session = DeploymentSshSession(
        [
            _command_result(
                {
                    "container_id": "b" * 64,
                    "image_digest": IMAGE_DIGEST,
                    "model_checksum": MODEL_CHECKSUM,
                    "engine": "engine",
                    "engine_digest": "f" * 64,
                    "port": 18080,
                    "health_status": "healthy",
                }
            )
        ]
    )
    ssh_client = DeploymentSshClient(ssh_session)
    storage = RecordingStorage()
    handler = DeployInferenceHandler(
        factory,
        EdgeExecutorSecurityContext(cipher, ssh_client),  # type: ignore[arg-type]
        storage,  # type: ignore[arg-type]
    )

    result = handler.execute(_load_execution(factory))

    assert result.status == "succeeded"
    assert result.phase == "running"
    assert storage.requests[0][0] == "minio://models/trained/model-1/best.pt"
    assert 0 < storage.requests[0][1].total_seconds() <= 900  # type: ignore[union-attr]
    request_bytes = next(
        data for data, path in ssh_session.uploads if path.endswith("/request.json")
    )
    request = json.loads(request_bytes)
    assert request["model"]["download_url"].startswith("https://minio.internal/")
    assert request["model"]["checksum"] == MODEL_CHECKSUM
    assert request["runtime"]["gpu_uuids"] == ["GPU-x86-fixture"]
    assert request["runtime"]["shm_size"] == "1g"
    assert request["previous_container_id"] == "a" * 64
    assert request["labels"]["com.visiox.deployment-instance-id"] == "instance-1"
    assert request["labels"]["com.visiox.restart-policy"] == "unless-stopped"
    assert request["labels"]["com.visiox.health-path"] == "/health"
    assert request["labels"]["com.visiox.warmup-path"] == "/predict/image"
    assert all("short-lived-secret" not in command for command in ssh_session.commands)
    assert ssh_session.run_timeouts == [1800.0]
    assert ssh_client.connect_request == {
        "host": "edge.internal",
        "port": 22,
        "username": "visiox-edge",
        "private_key": b"private-key-fixture",
        "expected_fingerprint": "SHA256:pinned",
        "timeout_seconds": ssh_client.connect_request["timeout_seconds"],
    }
    assert ssh_session.cleaned_paths is not None
    assert ssh_session.closed is True

    with factory() as database:
        service = database.get(DeploymentService, "service-1")
        instance = database.get(DeploymentInstance, "instance-1")
        task = database.get(Task, "task-1")
        execution = database.get(RemoteExecution, "exec-1")
        assert service is not None and service.status == "running"
        assert service.endpoint == "http://edge.internal:18080"
        assert instance is not None and instance.container_id == "b" * 64
        assert instance.engine_digest == "f" * 64
        assert instance.health_status == "healthy"
        assert instance.rollback_metadata == {
            "container_id": "a" * 64,
            "image_digest": "registry.internal/visiox/yolo26-inference@sha256:"
            + "c" * 64,
            "model_checksum": "d" * 64,
            "engine": "engine",
            "engine_digest": "e" * 64,
            "port": 18080,
        }
        assert task is not None and task.status == "SUCCESS"
        assert execution is not None and execution.phase == "running"


def test_deploy_handler_persists_fixed_failure_without_remote_secrets() -> None:
    factory, cipher = _deployment_database()
    ssh_session = DeploymentSshSession([_command_result({}, status=23)])
    handler = DeployInferenceHandler(
        factory,
        EdgeExecutorSecurityContext(
            cipher,
            DeploymentSshClient(ssh_session),  # type: ignore[arg-type]
        ),
        RecordingStorage(),  # type: ignore[arg-type]
    )

    result = handler.execute(_load_execution(factory))

    assert result.status == "failed"
    assert result.error_code == "EDGE_DEPLOY_FAILED"
    assert result.error_message == "Edge deployment failed"
    assert "secret" not in json.dumps(result.__dict__).casefold()
    with factory() as database:
        service = database.get(DeploymentService, "service-1")
        instance = database.get(DeploymentInstance, "instance-1")
        task = database.get(Task, "task-1")
        assert service is not None and service.status == "failed"
        assert instance is not None and instance.status == "failed"
        assert task is not None and task.status == "FAILED"
        assert "secret" not in (task.error_message or "").casefold()


def test_stop_handler_uses_stable_instance_label_and_persists_stopped() -> None:
    factory, cipher = _deployment_database(prior_healthy=True)
    ssh_session = DeploymentSshSession(
        [_command_result({"stopped_container_ids": ["a" * 64]})]
    )
    handler = StopDeploymentHandler(
        factory,
        EdgeExecutorSecurityContext(
            cipher,
            DeploymentSshClient(ssh_session),  # type: ignore[arg-type]
        ),
    )

    result = handler.execute(_load_execution(factory, "stop_deployment"))

    assert result.status == "succeeded"
    request_bytes = next(
        data for data, path in ssh_session.uploads if path.endswith("/request.json")
    )
    assert json.loads(request_bytes) == {
        "labels": {"com.visiox.deployment-instance-id": "instance-1"}
    }
    with factory() as database:
        service = database.get(DeploymentService, "service-1")
        instance = database.get(DeploymentInstance, "instance-1")
        assert service is not None and service.status == "stopped"
        assert service.endpoint == "pending"
        assert instance is not None and instance.status == "stopped"
        assert instance.health_status == "stopped"


def test_rollback_handler_uses_exact_prior_tuple_and_swaps_rollback_metadata() -> None:
    factory, cipher = _deployment_database(prior_healthy=True)
    previous = {
        "container_id": "c" * 64,
        "image_digest": "registry.internal/visiox/yolo26-inference@sha256:" + "d" * 64,
        "model_checksum": "e" * 64,
        "engine": "engine",
        "engine_digest": "f" * 64,
        "port": 18080,
    }
    with factory() as database:
        instance = database.get(DeploymentInstance, "instance-1")
        service = database.get(DeploymentService, "service-1")
        assert instance is not None
        assert service is not None
        instance.rollback_metadata = previous
        instance.status = "rollback_queued"
        service.status = "rollback_queued"
        database.commit()
    ssh_session = DeploymentSshSession(
        [
            _command_result(
                {
                    **previous,
                    "health_status": "healthy",
                }
            )
        ]
    )
    handler = RollbackDeploymentHandler(
        factory,
        EdgeExecutorSecurityContext(
            cipher,
            DeploymentSshClient(ssh_session),  # type: ignore[arg-type]
        ),
    )

    result = handler.execute(_load_execution(factory, "rollback"))

    assert result.status == "succeeded"
    request_bytes = next(
        data for data, path in ssh_session.uploads if path.endswith("/request.json")
    )
    request = json.loads(request_bytes)
    assert request["action"] == "rollback"
    assert request["target"] == previous
    assert request["current_container_id"] == "a" * 64
    with factory() as database:
        instance = database.get(DeploymentInstance, "instance-1")
        assert instance is not None
        assert instance.container_id == "c" * 64
        assert instance.image_digest == previous["image_digest"]
        assert instance.model_checksum == previous["model_checksum"]
        assert instance.rollback_metadata["container_id"] == "a" * 64


def test_production_handler_factory_registers_all_deployment_operations() -> None:
    handlers = build_deployment_handlers(
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
    )

    assert set(handlers) == {"deploy", "stop_deployment", "rollback"}
    assert isinstance(handlers["deploy"], DeployInferenceHandler)
    assert isinstance(handlers["stop_deployment"], StopDeploymentHandler)
    assert isinstance(handlers["rollback"], RollbackDeploymentHandler)


def _script_namespace(name: str) -> dict[str, object]:
    script = load_packaged_script(name).decode("utf-8")
    embedded = script.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    namespace: dict[str, object] = {"__name__": "visiox_script_test"}
    exec(compile(embedded, name, "exec"), namespace)
    return namespace


def _remote_deploy_request() -> dict[str, object]:
    return {
        "action": "deploy",
        "image_digest": IMAGE_DIGEST,
        "labels": {
            "com.visiox.managed": "true",
            "com.visiox.deployment-instance-id": "instance-1",
            "com.visiox.deployment-service-id": "service-1",
            "com.visiox.node-id": "node-1",
            "com.visiox.restart-policy": "unless-stopped",
            "com.visiox.health-path": "/health",
            "com.visiox.warmup-path": "/predict/image",
        },
        "model": {
            "download_url": "https://minio.internal/model?X-Amz-Signature=secret",
            "checksum": MODEL_CHECKSUM,
            "source_format": "pt",
        },
        "runtime": {
            "format": "engine",
            "precision": "fp16",
            "input_shape": [1, 3, 640, 640],
            "gpu_uuids": ["GPU-x86-fixture"],
            "engine_cache_key": ENGINE_CACHE_KEY,
            "calibration_download_url": None,
            "port": 18080,
            "shm_size": "1g",
            "restart_policy": "unless-stopped",
            "model_mount_read_only": True,
            "privileged": False,
        },
        "previous_container_id": "a" * 64,
    }


def test_deploy_script_builds_hardened_docker_arguments_from_validated_json() -> None:
    namespace = _script_namespace("deploy_inference.sh")
    request = namespace["_validate_request"](_remote_deploy_request())  # type: ignore[operator]
    args = namespace["_container_args"](  # type: ignore[operator]
        request,
        artifact_path=Path("/var/lib/visiox/model.engine"),
        config_path=Path("/var/lib/visiox/config.json"),
        name="visiox-candidate-fixed",
        host_port=None,
        runtime_user="1000:1000",
    )

    assert args[:3] == ["docker", "run", "-d"]
    assert "--privileged" not in args
    assert ["--gpus", "device=GPU-x86-fixture"] == args[
        args.index("--gpus") : args.index("--gpus") + 2
    ]
    assert ["--shm-size", "1g"] == args[
        args.index("--shm-size") : args.index("--shm-size") + 2
    ]
    assert ["--restart", "unless-stopped"] == args[
        args.index("--restart") : args.index("--restart") + 2
    ]
    assert ["--user", "1000:1000"] == args[
        args.index("--user") : args.index("--user") + 2
    ]
    config_index = args.index("YOLO_CONFIG_DIR=/tmp/visiox-ultralytics")
    assert args[config_index - 1] == "-e"
    mounts = [args[index + 1] for index, value in enumerate(args) if value == "--mount"]
    assert any(
        mount.endswith("dst=/models/model.engine,readonly") for mount in mounts
    )
    assert any(mount.endswith("dst=/app/config.json,readonly") for mount in mounts)
    assert ["-p", "127.0.0.1::8080"] == args[
        args.index("-p") : args.index("-p") + 2
    ]
    assert args[-1] == IMAGE_DIGEST


def test_deploy_script_exposes_only_active_container_to_the_lan() -> None:
    namespace = _script_namespace("deploy_inference.sh")
    request = namespace["_validate_request"](_remote_deploy_request())  # type: ignore[operator]

    active_args = namespace["_container_args"](  # type: ignore[operator]
        request,
        artifact_path=Path("/var/lib/visiox/model.engine"),
        config_path=Path("/var/lib/visiox/config.json"),
        name="visiox-active-fixed",
        host_port=18080,
        runtime_user="1000:1000",
    )

    assert ["-p", "0.0.0.0:18080:8080"] == active_args[
        active_args.index("-p") : active_args.index("-p") + 2
    ]


def test_deploy_script_writes_production_image_only_inference_config(tmp_path: Path) -> None:
    namespace = _script_namespace("deploy_inference.sh")
    request = namespace["_validate_request"](_remote_deploy_request())  # type: ignore[operator]
    config_path = tmp_path / "config.json"

    namespace["_write_config"](  # type: ignore[operator]
        config_path,
        request,
        Path("/var/lib/visiox/model.engine"),
    )

    config = json.loads(config_path.read_text(encoding="ascii"))
    assert config["production"] is True
    assert config["task"] == "detect"
    assert config["input"] == {"type": "http", "shape": [1, 3, 640, 640]}


def test_deploy_script_rejects_policy_tampering_before_docker() -> None:
    namespace = _script_namespace("deploy_inference.sh")
    request = _remote_deploy_request()
    request["runtime"]["privileged"] = True  # type: ignore[index]

    with pytest.raises(ValueError, match="invalid deployment request"):
        namespace["_validate_request"](request)  # type: ignore[operator]


def test_upgrade_keeps_previous_container_until_candidate_real_warmup() -> None:
    namespace = _script_namespace("deploy_inference.sh")
    events: list[str] = []

    class Operations:
        def start_candidate(self):
            events.append("start:candidate")
            return "candidate"

        def warmup(self, container):
            events.append(f"warm:{container}:health+image")

        def stop(self, container):
            events.append(f"stop:{container}")

        def remove(self, container):
            events.append(f"remove:{container}")

        def start_active(self):
            events.append("start:active")
            return "active"

        def result(self, container):
            events.append(f"result:{container}")
            return {"container_id": container}

        def start_existing(self, container):
            events.append(f"restart:{container}")

    result = namespace["_promote"](Operations(), "previous")  # type: ignore[operator]

    assert result == {"container_id": "active"}
    assert events.index("warm:candidate:health+image") < events.index("stop:previous")
    assert events == [
        "start:candidate",
        "warm:candidate:health+image",
        "stop:previous",
        "stop:candidate",
        "remove:candidate",
        "start:active",
        "warm:active:health+image",
        "result:active",
    ]


def test_failed_final_activation_restores_previous_healthy_container() -> None:
    namespace = _script_namespace("deploy_inference.sh")
    events: list[str] = []

    class Operations:
        def start_candidate(self):
            events.append("start:candidate")
            return "candidate"

        def warmup(self, container):
            events.append(f"warm:{container}")
            if container == "active":
                raise RuntimeError("warmup failed")

        def stop(self, container):
            events.append(f"stop:{container}")

        def remove(self, container):
            events.append(f"remove:{container}")

        def start_active(self):
            events.append("start:active")
            return "active"

        def result(self, container):
            raise AssertionError("failed activation has no result")

        def start_existing(self, container):
            events.append(f"restart:{container}")

    with pytest.raises(RuntimeError, match="warmup failed"):
        namespace["_promote"](Operations(), "previous")  # type: ignore[operator]

    assert events[-3:] == ["stop:active", "restart:previous", "warm:previous"]


def test_rollback_script_restores_current_when_prior_tuple_fails_warmup() -> None:
    namespace = _script_namespace("deploy_inference.sh")
    events: list[str] = []

    class Operations:
        def stop(self, container):
            events.append(f"stop:{container}")

        def start_existing(self, container):
            events.append(f"start:{container}")

        def warmup(self, container):
            events.append(f"warm:{container}")
            if container == "prior":
                raise RuntimeError("prior unhealthy")

        def result(self, container):
            raise AssertionError("failed rollback has no result")

    with pytest.raises(RuntimeError, match="prior unhealthy"):
        namespace["_rollback"](Operations(), "current", "prior")  # type: ignore[operator]

    assert events == [
        "stop:current",
        "start:prior",
        "warm:prior",
        "stop:prior",
        "start:current",
        "warm:current",
    ]


def test_stop_script_filters_only_by_stable_instance_label() -> None:
    namespace = _script_namespace("stop_deployment.sh")
    request = namespace["_validate_request"](  # type: ignore[operator]
        {"labels": {"com.visiox.deployment-instance-id": "instance-1"}}
    )

    assert namespace["_list_command"](request) == [  # type: ignore[operator]
        "docker",
        "ps",
        "--filter",
        "label=com.visiox.deployment-instance-id=instance-1",
        "--format",
        "{{.ID}}",
    ]
