from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
import shlex
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from visiox_db.models import (
    ComputeNode,
    DeploymentInstance,
    DeploymentService,
    EdgeSshCredential,
    RemoteExecution,
    Task,
    TrainedModel,
)
from visiox_storage.client import ObjectStorageClient

from .crypto import EncryptedSecret
from .inventory import InventorySnapshot
from .redaction import redact
from .scripts import load_packaged_script
from .startup import EdgeExecutorSecurityContext
from .state import ExecutionResult


ModelFormat = Literal["pt", "onnx", "engine"]
RequestedFormat = Literal["auto", "pt", "onnx", "engine"]
Precision = Literal["auto", "fp32", "fp16", "int8"]

_IMAGE_DIGEST = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,430}@sha256:[a-f0-9]{64}\Z"
)
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_GPU_UUID = re.compile(r"GPU-[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z")
_CONTAINER_ID = re.compile(r"[a-f0-9]{12,64}\Z")
_LABEL_VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}\Z")
_TRANSFER_TIMEOUT_SECONDS = 60.0
_DEPLOYMENT_TIMEOUT_SECONDS = 30 * 60.0
_PRESIGNED_URL_TTL = timedelta(minutes=15)
_SHM_SIZE = "1g"

logger = logging.getLogger(__name__)


class DeploymentLabels:
    MANAGED = "com.visiox.managed"
    INSTANCE_ID = "com.visiox.deployment-instance-id"
    SERVICE_ID = "com.visiox.deployment-service-id"
    NODE_ID = "com.visiox.node-id"
    RESTART_POLICY = "com.visiox.restart-policy"
    HEALTH_PATH = "com.visiox.health-path"
    WARMUP_PATH = "com.visiox.warmup-path"


class ModelArtifact(BaseModel):
    model_config = ConfigDict(frozen=True)

    task: str
    artifact_uri: str = Field(min_length=1, max_length=2048)
    checksum: str
    format: ModelFormat

    @field_validator("task", "format")
    @classmethod
    def _normalize_text(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("checksum")
    @classmethod
    def _validate_checksum(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _SHA256.fullmatch(normalized):
            raise ValueError("model checksum must be a SHA-256 digest")
        return normalized


class DeploymentOptions(BaseModel):
    model_config = ConfigDict(frozen=True)

    format: RequestedFormat = "auto"
    precision: Precision = "auto"
    input_shape: tuple[int, int, int, int] = (1, 3, 640, 640)
    gpu_uuids: tuple[str, ...] = ()
    calibration_dataset_uri: str | None = None
    runtime_image_digest: str | None = None

    @field_validator("input_shape")
    @classmethod
    def _validate_input_shape(
        cls,
        value: tuple[int, int, int, int],
    ) -> tuple[int, int, int, int]:
        batch, channels, height, width = value
        if batch != 1 or channels != 3:
            raise ValueError("input shape must use batch 1 and three image channels")
        if not 32 <= height <= 4096 or not 32 <= width <= 4096:
            raise ValueError("input image dimensions must be between 32 and 4096")
        if height % 32 or width % 32:
            raise ValueError("input image dimensions must be divisible by 32")
        return value

    @field_validator("gpu_uuids")
    @classmethod
    def _validate_gpu_uuids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value) or any(
            not _GPU_UUID.fullmatch(item) for item in value
        ):
            raise ValueError("GPU UUID selection is invalid")
        return value


class DeploymentPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_format: ModelFormat
    export_format: ModelFormat
    precision: Literal["fp32", "fp16", "int8"]
    input_shape: tuple[int, int, int, int]
    gpu_uuids: tuple[str, ...]
    engine_cache_key: str | None


def validate_image_digest(value: str) -> str:
    normalized = value.strip()
    if (
        "://" in normalized
        or "?" in normalized
        or "#" in normalized
        or normalized.count("@") != 1
        or not _IMAGE_DIGEST.fullmatch(normalized)
    ):
        raise ValueError("inference image must be an immutable image digest")
    return normalized


def engine_cache_key(
    *,
    model_checksum: str,
    tensorrt_version: str,
    compute_capability: str,
    precision: str,
    input_shape: tuple[int, int, int, int],
) -> str:
    identity = {
        "model_checksum": model_checksum,
        "tensorrt_version": tensorrt_version,
        "compute_capability": compute_capability,
        "precision": precision,
        "input_shape": list(input_shape),
    }
    payload = json.dumps(
        identity,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def build_deployment_plan(
    model: ModelArtifact,
    inventory: InventorySnapshot,
    options: DeploymentOptions,
) -> DeploymentPlan:
    if model.task != "detect":
        raise ValueError("production deployment supports YOLO26 detect models only")
    if not inventory.supported:
        raise ValueError("node inventory is not compatible with production deployment")

    available_gpu_uuids = tuple(
        gpu.uuid for gpu in inventory.gpus if gpu.uuid is not None
    )
    selected_gpu_uuids = options.gpu_uuids or available_gpu_uuids[:1]
    if not selected_gpu_uuids or any(
        uuid not in available_gpu_uuids for uuid in selected_gpu_uuids
    ):
        raise ValueError("requested GPU UUID is not available in node inventory")

    export_format: ModelFormat = (
        "engine" if options.format == "auto" else options.format
    )
    conversion_order = {"pt": 0, "onnx": 1, "engine": 2}
    if conversion_order[export_format] < conversion_order[model.format]:
        raise ValueError(
            f"cannot convert {model.format} model to {export_format} format"
        )

    precision = options.precision
    if precision == "auto":
        precision = "fp16" if export_format == "engine" else "fp32"
    if precision == "int8" and not options.calibration_dataset_uri:
        raise ValueError("INT8 precision requires a calibration dataset")
    if precision == "int8" and export_format != "engine":
        raise ValueError("INT8 precision requires TensorRT engine format")

    cache_key = None
    if export_format == "engine":
        runtime_identity = inventory.tensorrt_version or options.runtime_image_digest
        if not runtime_identity or not inventory.compute_capability:
            raise ValueError("TensorRT inventory is incomplete")
        cache_key = engine_cache_key(
            model_checksum=model.checksum,
            tensorrt_version=runtime_identity,
            compute_capability=inventory.compute_capability,
            precision=precision,
            input_shape=options.input_shape,
        )

    return DeploymentPlan(
        source_format=model.format,
        export_format=export_format,
        precision=precision,
        input_shape=options.input_shape,
        gpu_uuids=selected_gpu_uuids,
        engine_cache_key=cache_key,
    )


class _PersistedDeployment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    image_digest: str
    model_checksum: str
    model_format: ModelFormat
    format: ModelFormat
    precision: Literal["fp32", "fp16", "int8"]
    input_shape: tuple[int, int, int, int]
    gpu_uuids: tuple[str, ...]
    engine_cache_key: str | None
    calibration_dataset_uri: str | None
    port: int = Field(ge=1024, le=65535)


class _DeploymentResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    container_id: str
    image_digest: str
    model_checksum: str
    engine: ModelFormat
    engine_digest: str
    port: int = Field(ge=1024, le=65535)
    health_status: Literal["healthy"]

    @field_validator("container_id")
    @classmethod
    def _validate_container_id(cls, value: str) -> str:
        if not _CONTAINER_ID.fullmatch(value):
            raise ValueError("container identifier is invalid")
        return value

    @field_validator("engine_digest")
    @classmethod
    def _validate_engine_digest(cls, value: str) -> str:
        if not _SHA256.fullmatch(value):
            raise ValueError("engine digest is invalid")
        return value


class _StopResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stopped_container_ids: tuple[str, ...]

    @field_validator("stopped_container_ids")
    @classmethod
    def _validate_container_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not _CONTAINER_ID.fullmatch(item) for item in value):
            raise ValueError("container identifier is invalid")
        return value


@dataclass(frozen=True)
class _DeploymentContext:
    execution_id: str
    task_id: str
    service_id: str
    instance_id: str
    node_id: str
    service: DeploymentService
    instance: DeploymentInstance
    node: ComputeNode
    model: TrainedModel


@dataclass(frozen=True)
class _SshTarget:
    host: str
    port: int
    username: str
    expected_fingerprint: str
    private_key: bytes


class _DeploymentHandlerBase:
    script_name: str
    failure_code: str
    failure_message: str

    def __init__(
        self,
        session_factory: Callable[[], Session],
        security: EdgeExecutorSecurityContext,
    ) -> None:
        self._session_factory = session_factory
        self._security = security
        self._script = load_packaged_script(self.script_name)

    def _load_context(self, execution: RemoteExecution) -> _DeploymentContext:
        with self._session_factory() as session:
            current = session.get(RemoteExecution, execution.id)
            if (
                current is None
                or current.status != "running"
                or current.task_id is None
                or current.deployment_service_id is None
                or current.resource_id is None
            ):
                raise ValueError("deployment execution is not runnable")
            service = session.get(DeploymentService, current.deployment_service_id)
            instance = session.get(DeploymentInstance, current.resource_id)
            node = session.get(ComputeNode, current.node_id)
            model = (
                session.get(TrainedModel, service.trained_model_id)
                if service is not None and service.trained_model_id is not None
                else None
            )
            task = session.get(Task, current.task_id)
            if (
                service is None
                or instance is None
                or node is None
                or model is None
                or task is None
                or instance.deployment_service_id != service.id
                or instance.node_id != node.id
            ):
                raise ValueError("deployment resources are incomplete")
            session.expunge_all()
            return _DeploymentContext(
                execution_id=current.id,
                task_id=task.id,
                service_id=service.id,
                instance_id=instance.id,
                node_id=node.id,
                service=service,
                instance=instance,
                node=node,
                model=model,
            )

    def _load_target(self, node_id: str) -> _SshTarget:
        with self._session_factory() as session:
            credential = session.scalar(
                select(EdgeSshCredential).where(EdgeSshCredential.node_id == node_id)
            )
            if credential is None:
                raise ValueError("edge SSH credential is unavailable")
            private_key = self._security.credential_cipher.decrypt(
                EncryptedSecret(
                    ciphertext=credential.encrypted_private_key,
                    nonce=credential.encryption_nonce,
                    key_version=credential.key_version,
                )
            )
            return _SshTarget(
                host=credential.ssh_host,
                port=credential.ssh_port,
                username=credential.ssh_user,
                expected_fingerprint=credential.host_key_fingerprint,
                private_key=private_key,
            )

    def _transition(self, context: _DeploymentContext, phase: str) -> None:
        progress = {
            "connecting": 5,
            "probing": 15,
            "preparing": 30,
            "optimizing": 50,
            "starting": 70,
            "warming_up": 85,
            "stopping": 60,
            "rollback_starting": 60,
            "rollback_warming_up": 85,
            "running": 100,
            "stopped": 100,
        }.get(phase, 0)
        with self._session_factory() as session:
            execution = session.get(RemoteExecution, context.execution_id)
            service = session.get(DeploymentService, context.service_id)
            instance = session.get(DeploymentInstance, context.instance_id)
            task = session.get(Task, context.task_id)
            if not all((execution, service, instance, task)):
                raise ValueError("deployment resources are incomplete")
            assert execution is not None
            assert service is not None
            assert instance is not None
            assert task is not None
            execution.phase = phase
            service.status = phase
            instance.status = phase
            task.status = "RUNNING"
            task.progress = progress
            task.stage = phase
            if task.started_at is None:
                task.started_at = datetime.now(UTC)
            session.add_all([execution, service, instance, task])
            session.commit()

    def _run_script(
        self,
        target: _SshTarget,
        request: Mapping[str, Any],
    ) -> object:
        ssh_session = self._security.ssh_client.connect(
            host=target.host,
            port=target.port,
            username=target.username,
            private_key=target.private_key,
            expected_fingerprint=target.expected_fingerprint,
            timeout_seconds=_TRANSFER_TIMEOUT_SECONDS,
        )
        try:
            workspace = ssh_session.create_private_directory(
                timeout_seconds=_TRANSFER_TIMEOUT_SECONDS
            )
            script_path = str(PurePosixPath(workspace.path) / self.script_name)
            request_path = str(PurePosixPath(workspace.path) / "request.json")
            remote_paths = (script_path, request_path)
            request_bytes = json.dumps(
                request,
                allow_nan=False,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("ascii")
            try:
                for data, path in (
                    (self._script, script_path),
                    (request_bytes, request_path),
                ):
                    ssh_session.upload_bytes_exclusive(
                        data,
                        path,
                        expected_owner_uid=workspace.owner_uid,
                        timeout_seconds=_TRANSFER_TIMEOUT_SECONDS,
                    )
                    ssh_session.validate_remote_file(
                        path,
                        expected_owner_uid=workspace.owner_uid,
                        timeout_seconds=_TRANSFER_TIMEOUT_SECONDS,
                    )
                command = (
                    f"/bin/bash {shlex.quote(script_path)} "
                    f"{shlex.quote(request_path)}"
                )
                result = ssh_session.run(
                    command,
                    timeout_seconds=_DEPLOYMENT_TIMEOUT_SECONDS,
                )
                if result.exit_status != 0:
                    logger.error(
                        "Remote deployment script failed with exit code %s: %s",
                        result.exit_status,
                        redact(result.stderr.decode("utf-8", errors="replace")),
                    )
                    raise RuntimeError("remote deployment script failed")
                return json.loads(result.stdout)
            finally:
                ssh_session.cleanup_private_directory(
                    workspace,
                    remote_paths,
                    timeout_seconds=_TRANSFER_TIMEOUT_SECONDS,
                )
        finally:
            ssh_session.close()

    def _fail(
        self,
        execution_id: str,
        *,
        restore_healthy: bool,
    ) -> ExecutionResult:
        now = datetime.now(UTC)
        with self._session_factory() as session:
            execution = session.get(RemoteExecution, execution_id)
            if execution is None:
                return ExecutionResult.failed(
                    error_code=self.failure_code,
                    error_message=self.failure_message,
                    phase="failed",
                )
            service = session.get(DeploymentService, execution.deployment_service_id)
            instance = session.get(DeploymentInstance, execution.resource_id)
            task = session.get(Task, execution.task_id)
            execution.phase = "failed"
            execution.error_code = self.failure_code
            execution.error_message = self.failure_message
            if service is not None:
                service.status = "running" if restore_healthy else "failed"
                session.add(service)
            if instance is not None:
                instance.status = "running" if restore_healthy else "failed"
                instance.health_status = "healthy" if restore_healthy else "unhealthy"
                instance.health_checked_at = now
                session.add(instance)
            if task is not None:
                task.status = "FAILED"
                task.stage = "failed"
                task.error_code = self.failure_code
                task.error_message = self.failure_message
                task.finished_at = now
                session.add(task)
            session.add(execution)
            session.commit()
        return ExecutionResult.failed(
            error_code=self.failure_code,
            error_message=self.failure_message,
            phase="failed",
        )


class DeployInferenceHandler(_DeploymentHandlerBase):
    script_name = "deploy_inference.sh"
    failure_code = "EDGE_DEPLOY_FAILED"
    failure_message = "Edge deployment failed"

    def __init__(
        self,
        session_factory: Callable[[], Session],
        security: EdgeExecutorSecurityContext,
        storage: ObjectStorageClient,
    ) -> None:
        super().__init__(session_factory, security)
        self._storage = storage

    def execute(self, execution: RemoteExecution) -> ExecutionResult:
        context: _DeploymentContext | None = None
        prior: dict[str, Any] | None = None
        try:
            context = self._load_context(execution)
            prior = _healthy_tuple(context.instance)
            self._transition(context, "connecting")
            target = self._load_target(context.node_id)
            self._transition(context, "probing")
            desired, plan = _validated_desired_state(context)
            self._transition(context, "preparing")
            model_url = self._storage.presigned_get_url(
                context.model.artifact_uri,
                expires=_PRESIGNED_URL_TTL,
            )
            calibration_url = None
            if desired.calibration_dataset_uri is not None:
                calibration_url = self._storage.presigned_get_url(
                    desired.calibration_dataset_uri,
                    expires=_PRESIGNED_URL_TTL,
                )
            request = _deploy_request(
                context,
                desired,
                plan,
                model_url=model_url,
                calibration_url=calibration_url,
                previous_container_id=(prior or {}).get("container_id"),
            )
            self._transition(context, "optimizing")
            response = self._run_script(target, request)
            self._transition(context, "starting")
            deployed = _DeploymentResult.model_validate(response)
            _require_deployment_matches(deployed, desired, plan)
            self._transition(context, "warming_up")
            _persist_running(
                self._session_factory,
                context,
                deployed,
                endpoint=_endpoint(target.host, deployed.port),
                rollback_metadata=prior or {},
            )
            return ExecutionResult.succeeded(phase="running")
        except Exception as error:
            logger.error(
                "Deployment execution %s failed: %s",
                execution.id,
                redact(error),
            )
            return self._fail(
                execution.id,
                restore_healthy=prior is not None,
            )


class StopDeploymentHandler(_DeploymentHandlerBase):
    script_name = "stop_deployment.sh"
    failure_code = "EDGE_STOP_FAILED"
    failure_message = "Edge deployment could not be stopped"

    def execute(self, execution: RemoteExecution) -> ExecutionResult:
        context: _DeploymentContext | None = None
        try:
            context = self._load_context(execution)
            self._transition(context, "connecting")
            target = self._load_target(context.node_id)
            self._transition(context, "stopping")
            response = self._run_script(
                target,
                {"labels": {DeploymentLabels.INSTANCE_ID: context.instance_id}},
            )
            _StopResult.model_validate(response)
            _persist_stopped(self._session_factory, context)
            return ExecutionResult.succeeded(phase="stopped")
        except Exception:
            return self._fail(
                execution.id,
                restore_healthy=(
                    context is not None
                    and _healthy_tuple(context.instance) is not None
                ),
            )


class RollbackDeploymentHandler(_DeploymentHandlerBase):
    script_name = "deploy_inference.sh"
    failure_code = "EDGE_ROLLBACK_FAILED"
    failure_message = "Edge deployment rollback failed"

    def execute(self, execution: RemoteExecution) -> ExecutionResult:
        context: _DeploymentContext | None = None
        current: dict[str, Any] | None = None
        try:
            context = self._load_context(execution)
            current = _healthy_tuple(context.instance)
            target_tuple = _validate_rollback_tuple(context.instance.rollback_metadata)
            self._transition(context, "connecting")
            target = self._load_target(context.node_id)
            self._transition(context, "rollback_starting")
            response = self._run_script(
                target,
                {
                    "action": "rollback",
                    "current_container_id": context.instance.container_id,
                    "labels": _deployment_labels(context),
                    "target": target_tuple,
                },
            )
            rolled_back = _DeploymentResult.model_validate(response)
            _require_rollback_matches(rolled_back, target_tuple)
            self._transition(context, "rollback_warming_up")
            _persist_running(
                self._session_factory,
                context,
                rolled_back,
                endpoint=_endpoint(target.host, rolled_back.port),
                rollback_metadata=current or {},
            )
            return ExecutionResult.succeeded(phase="running")
        except Exception:
            return self._fail(
                execution.id,
                restore_healthy=current is not None,
            )


def build_deployment_handlers(
    session_factory: Callable[[], Session],
    security: EdgeExecutorSecurityContext,
    storage: ObjectStorageClient,
) -> dict[str, _DeploymentHandlerBase]:
    return {
        "deploy": DeployInferenceHandler(session_factory, security, storage),
        "stop_deployment": StopDeploymentHandler(session_factory, security),
        "rollback": RollbackDeploymentHandler(session_factory, security),
    }


def _validated_desired_state(
    context: _DeploymentContext,
) -> tuple[_PersistedDeployment, DeploymentPlan]:
    deployment = context.service.config.get("deployment")
    try:
        desired = _PersistedDeployment.model_validate(deployment)
        validate_image_digest(desired.image_digest)
        artifact = ModelArtifact(
            task=context.model.task,
            artifact_uri=context.model.artifact_uri,
            checksum=desired.model_checksum,
            format=desired.model_format,
        )
        inventory = InventorySnapshot.model_validate(
            context.node.fingerprint.get("inventory_snapshot")
        )
        plan = build_deployment_plan(
            artifact,
            inventory,
            DeploymentOptions(
                format=desired.format,
                precision=desired.precision,
                input_shape=desired.input_shape,
                gpu_uuids=desired.gpu_uuids,
                calibration_dataset_uri=desired.calibration_dataset_uri,
                runtime_image_digest=desired.image_digest,
            ),
        )
    except (ValueError, ValidationError):
        raise ValueError("persisted deployment configuration is invalid") from None
    if desired.engine_cache_key != plan.engine_cache_key:
        raise ValueError("persisted engine cache identity is invalid")
    return desired, plan


def _deployment_labels(context: _DeploymentContext) -> dict[str, str]:
    dynamic_labels = {
        DeploymentLabels.INSTANCE_ID: context.instance_id,
        DeploymentLabels.SERVICE_ID: context.service_id,
        DeploymentLabels.NODE_ID: context.node_id,
    }
    if any(
        not _LABEL_VALUE.fullmatch(value) for value in dynamic_labels.values()
    ):
        raise ValueError("deployment label value is invalid")
    return {
        DeploymentLabels.MANAGED: "true",
        **dynamic_labels,
        DeploymentLabels.RESTART_POLICY: "unless-stopped",
        DeploymentLabels.HEALTH_PATH: "/health",
        DeploymentLabels.WARMUP_PATH: "/predict/image",
    }


def _deploy_request(
    context: _DeploymentContext,
    desired: _PersistedDeployment,
    plan: DeploymentPlan,
    *,
    model_url: str,
    calibration_url: str | None,
    previous_container_id: str | None,
) -> dict[str, Any]:
    return {
        "action": "deploy",
        "image_digest": desired.image_digest,
        "labels": _deployment_labels(context),
        "model": {
            "download_url": model_url,
            "checksum": desired.model_checksum,
            "source_format": desired.model_format,
        },
        "runtime": {
            "format": plan.export_format,
            "precision": plan.precision,
            "input_shape": list(plan.input_shape),
            "gpu_uuids": list(plan.gpu_uuids),
            "engine_cache_key": plan.engine_cache_key,
            "calibration_download_url": calibration_url,
            "port": desired.port,
            "shm_size": _SHM_SIZE,
            "restart_policy": "unless-stopped",
            "model_mount_read_only": True,
            "privileged": False,
        },
        "previous_container_id": previous_container_id,
    }


def _healthy_tuple(instance: DeploymentInstance) -> dict[str, Any] | None:
    if (
        instance.container_id is None
        or instance.image_digest is None
        or instance.model_checksum is None
        or instance.engine_digest is None
        or instance.port is None
        or instance.health_status != "healthy"
    ):
        return None
    return {
        "container_id": instance.container_id,
        "image_digest": instance.image_digest,
        "model_checksum": instance.model_checksum,
        "engine": instance.engine,
        "engine_digest": instance.engine_digest,
        "port": instance.port,
    }


def _validate_rollback_tuple(value: Mapping[str, Any]) -> dict[str, Any]:
    if set(value) != {
        "container_id",
        "image_digest",
        "model_checksum",
        "engine",
        "engine_digest",
        "port",
    }:
        raise ValueError("rollback tuple is invalid")
    try:
        result = _DeploymentResult(
            **value,
            health_status="healthy",
        )
        validate_image_digest(result.image_digest)
    except (ValueError, ValidationError):
        raise ValueError("rollback tuple is invalid") from None
    return dict(value)


def _require_deployment_matches(
    deployed: _DeploymentResult,
    desired: _PersistedDeployment,
    plan: DeploymentPlan,
) -> None:
    if (
        deployed.image_digest != desired.image_digest
        or deployed.model_checksum != desired.model_checksum
        or deployed.engine != plan.export_format
        or deployed.port != desired.port
    ):
        raise ValueError("remote deployment result did not match the desired tuple")


def _require_rollback_matches(
    deployed: _DeploymentResult,
    target: Mapping[str, Any],
) -> None:
    if any(
        getattr(deployed, field) != value
        for field, value in target.items()
    ):
        raise ValueError("remote rollback result did not match the prior tuple")


def _persist_running(
    session_factory: Callable[[], Session],
    context: _DeploymentContext,
    deployed: _DeploymentResult,
    *,
    endpoint: str,
    rollback_metadata: Mapping[str, Any],
) -> None:
    now = datetime.now(UTC)
    with session_factory() as session:
        execution = session.get(RemoteExecution, context.execution_id)
        service = session.get(DeploymentService, context.service_id)
        instance = session.get(DeploymentInstance, context.instance_id)
        task = session.get(Task, context.task_id)
        if not all((execution, service, instance, task)):
            raise ValueError("deployment resources are incomplete")
        assert execution is not None
        assert service is not None
        assert instance is not None
        assert task is not None
        execution.phase = "running"
        execution.error_code = None
        execution.error_message = None
        service.status = "running"
        service.endpoint = endpoint
        instance.container_id = deployed.container_id
        instance.image_digest = deployed.image_digest
        instance.model_checksum = deployed.model_checksum
        instance.engine = deployed.engine
        instance.engine_digest = deployed.engine_digest
        instance.port = deployed.port
        instance.endpoint = endpoint
        instance.status = "running"
        instance.health_status = "healthy"
        instance.health_checked_at = now
        instance.rollback_metadata = dict(rollback_metadata)
        task.status = "SUCCESS"
        task.progress = 100
        task.stage = "running"
        task.error_code = None
        task.error_message = None
        task.finished_at = now
        session.add_all([execution, service, instance, task])
        session.commit()


def _persist_stopped(
    session_factory: Callable[[], Session],
    context: _DeploymentContext,
) -> None:
    now = datetime.now(UTC)
    with session_factory() as session:
        execution = session.get(RemoteExecution, context.execution_id)
        service = session.get(DeploymentService, context.service_id)
        instance = session.get(DeploymentInstance, context.instance_id)
        task = session.get(Task, context.task_id)
        if not all((execution, service, instance, task)):
            raise ValueError("deployment resources are incomplete")
        assert execution is not None
        assert service is not None
        assert instance is not None
        assert task is not None
        execution.phase = "stopped"
        service.status = "stopped"
        service.endpoint = "pending"
        instance.status = "stopped"
        instance.health_status = "stopped"
        instance.health_checked_at = now
        instance.endpoint = None
        task.status = "SUCCESS"
        task.progress = 100
        task.stage = "stopped"
        task.finished_at = now
        session.add_all([execution, service, instance, task])
        session.commit()


def _endpoint(host: str, port: int) -> str:
    bracketed = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"http://{bracketed}:{port}"
