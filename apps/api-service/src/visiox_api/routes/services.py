from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
import inspect
from pathlib import PurePosixPath
from typing import Any, Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from visiox_api.routes.pipeline_inference import (
    PipelinePredictor,
    PipelinePredictResponse,
    get_pipeline_inference_storage,
    get_pipeline_predictor,
    predict_pipeline_image,
)
from visiox_common.tasks import TaskStatus, TaskType
from visiox_db.base import new_id
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
from visiox_db.session import get_session
from visiox_edge_executor_worker.deployment import (
    DeploymentOptions,
    ModelArtifact,
    build_deployment_plan,
    validate_image_digest,
)
from visiox_edge_executor_worker.inventory import (
    InventorySnapshot,
    pool_accepts_inventory,
)
from visiox_messaging.streams import RedisStreamProducer
from visiox_storage.client import ObjectStorageClient


router = APIRouter(prefix="/services", tags=["services"])

_DEPLOY_OPERATION = "deploy"
_STOP_OPERATION = "stop_deployment"
_ROLLBACK_OPERATION = "rollback"
_ROLLBACK_FIELDS = {
    "container_id",
    "image_digest",
    "model_checksum",
    "engine",
    "engine_digest",
    "port",
}


class ServiceCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    pipeline_id: str
    trained_model_id: str
    model_name: str = Field(min_length=1, max_length=160)
    model_weight: str = Field(min_length=1, max_length=160)
    environment: str = Field(min_length=1, max_length=120)
    instance_name: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[\w\u4e00-\u9fff-]+$",
    )
    resource_summary: str = Field(default="", max_length=255)
    node_id: str
    image_digest: str
    model_checksum: str
    port: int = Field(default=8080, ge=1024, le=65535)
    format: Literal["auto", "pt", "onnx", "engine"] = "auto"
    precision: Literal["auto", "fp32", "fp16", "int8"] = "auto"
    input_shape: tuple[int, int, int, int] = (1, 3, 640, 640)
    gpu_uuids: tuple[str, ...] = ()
    calibration_dataset_uri: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class ServiceUpdateRequest(BaseModel):
    status: Literal["running", "stopped"]


class ServiceUpgradeRequest(BaseModel):
    trained_model_id: str
    image_digest: str
    model_checksum: str
    port: int = Field(default=8080, ge=1024, le=65535)
    format: Literal["auto", "pt", "onnx", "engine"] = "auto"
    precision: Literal["auto", "fp32", "fp16", "int8"] = "auto"
    input_shape: tuple[int, int, int, int] = (1, 3, 640, 640)
    gpu_uuids: tuple[str, ...] = ()
    calibration_dataset_uri: str | None = None


class ServiceResponse(BaseModel):
    id: str
    name: str
    pipeline_id: str
    trained_model_id: str | None
    model_name: str
    model_weight: str
    environment: str
    instance_count: int
    instance_name: str
    resource_summary: str
    status: str
    endpoint: str
    calls: int
    config: dict[str, Any]
    instance_id: str | None
    node_id: str | None
    container_id: str | None
    image_digest: str | None
    model_checksum: str | None
    engine: str | None
    engine_digest: str | None
    port: int | None
    health_status: str | None
    task_id: str | None
    remote_execution_id: str | None
    phase: str | None
    log_uri: str | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class ServiceListResponse(BaseModel):
    items: list[ServiceResponse]
    total: int
    limit: int
    offset: int


def get_service_session() -> Generator[Session]:
    yield from get_session()


def get_service_stream_producer(request: Request) -> RedisStreamProducer:
    return RedisStreamProducer(request.app.state.redis)


async def _enqueue_execution(
    producer: Any,
    *,
    task_id: str,
    task_type: TaskType,
    remote_execution_id: str,
) -> str:
    result = producer.enqueue_edge_execution(
        task_id=task_id,
        task_type=task_type,
        remote_execution_id=remote_execution_id,
    )
    if inspect.isawaitable(result):
        return await result
    return str(result)


@router.post("", response_model=ServiceResponse, status_code=status.HTTP_201_CREATED)
async def create_service(
    request: ServiceCreateRequest,
    session: Session = Depends(get_service_session),
    producer: Any = Depends(get_service_stream_producer),
) -> ServiceResponse:
    pipeline = session.get(TrainingPipeline, request.pipeline_id)
    if pipeline is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline not found")
    trained_model = session.get(TrainedModel, request.trained_model_id)
    if (
        trained_model is None
        or trained_model.pipeline_id != pipeline.id
        or trained_model.status != "ready"
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Trained model is not ready for this pipeline",
        )
    if pipeline.task != "detect" or trained_model.task != "detect":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Production deployment supports YOLO26 detect models only",
        )

    node, inventory = _deployment_node(session, request.node_id)
    try:
        image_digest = validate_image_digest(request.image_digest)
        artifact = ModelArtifact(
            task=trained_model.task,
            artifact_uri=trained_model.artifact_uri,
            checksum=request.model_checksum,
            format=_artifact_format(trained_model.artifact_uri),
        )
        options = DeploymentOptions(
            format=request.format,
            precision=request.precision,
            input_shape=request.input_shape,
            gpu_uuids=request.gpu_uuids,
            calibration_dataset_uri=request.calibration_dataset_uri,
        )
        plan = build_deployment_plan(artifact, inventory, options)
        _require_minio_uri(trained_model.artifact_uri, "Trained model artifact")
        if request.calibration_dataset_uri is not None:
            _require_minio_uri(
                request.calibration_dataset_uri,
                "Calibration dataset",
            )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error

    service_id = new_id()
    instance_id = new_id()
    task_id = new_id()
    execution_id = new_id()
    normalized_config = dict(request.config)
    normalized_config["current_remote_execution_id"] = execution_id
    normalized_config["deployment"] = {
        "image_digest": image_digest,
        "model_checksum": artifact.checksum,
        "model_format": artifact.format,
        "format": plan.export_format,
        "precision": plan.precision,
        "input_shape": list(plan.input_shape),
        "gpu_uuids": list(plan.gpu_uuids),
        "engine_cache_key": plan.engine_cache_key,
        "calibration_dataset_uri": request.calibration_dataset_uri,
        "port": request.port,
    }
    service = DeploymentService(
        id=service_id,
        name=request.name.strip(),
        pipeline_id=pipeline.id,
        trained_model_id=trained_model.id,
        model_name=request.model_name,
        model_weight=request.model_weight,
        environment=request.environment,
        instance_count=1,
        instance_name=request.instance_name.strip(),
        resource_summary=request.resource_summary,
        status="queued",
        endpoint="pending",
        config=normalized_config,
    )
    instance = DeploymentInstance(
        id=instance_id,
        deployment_service_id=service_id,
        node_id=node.id,
        instance_name=service.instance_name,
        image_digest=image_digest,
        model_checksum=artifact.checksum,
        engine=plan.export_format,
        engine_digest=None,
        port=request.port,
        status="queued",
        health_status="pending",
        rollback_metadata={},
    )
    task = Task(
        id=task_id,
        task_type=TaskType.EDGE_DEPLOY.value,
        status=TaskStatus.QUEUED.value,
        progress=0,
        resource_type="deployment_service",
        resource_id=service_id,
        payload={},
    )
    execution = RemoteExecution(
        id=execution_id,
        node_id=node.id,
        task_id=task_id,
        deployment_service_id=service_id,
        resource_type="deployment_instance",
        resource_id=instance_id,
        operation=_DEPLOY_OPERATION,
        phase="queued",
        status="queued",
        idempotency_key=f"deploy:{instance_id}:{execution_id}",
    )
    session.add_all([service, instance, task, execution])
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Service name or deployment instance already exists",
        ) from error

    try:
        await _enqueue_execution(
            producer,
            task_id=task_id,
            task_type=TaskType.EDGE_DEPLOY,
            remote_execution_id=execution_id,
        )
    except Exception:
        _mark_enqueue_failed(
            session,
            service_id=service_id,
            instance_id=instance_id,
            task_id=task_id,
            execution_id=execution_id,
        )
    return _service_response(session, service_id)


@router.get("", response_model=ServiceListResponse)
def list_services(
    status_filter: str | None = Query(default=None, alias="status"),
    pipeline_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_service_session),
) -> ServiceListResponse:
    filters = []
    if status_filter:
        filters.append(DeploymentService.status == status_filter)
    if pipeline_id:
        filters.append(DeploymentService.pipeline_id == pipeline_id)
    count_query = select(func.count()).select_from(DeploymentService)
    list_query = select(DeploymentService.id).order_by(
        DeploymentService.created_at.desc(),
        DeploymentService.id.desc(),
    )
    if filters:
        count_query = count_query.where(*filters)
        list_query = list_query.where(*filters)
    total = session.scalar(count_query) or 0
    ids = session.scalars(list_query.limit(limit).offset(offset)).all()
    return ServiceListResponse(
        items=[_service_response(session, service_id) for service_id in ids],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{service_id}", response_model=ServiceResponse)
def get_service(
    service_id: str,
    session: Session = Depends(get_service_session),
) -> ServiceResponse:
    return _service_response(session, service_id)


@router.post(
    "/{service_id}/upgrade",
    response_model=ServiceResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upgrade_service(
    service_id: str,
    request: ServiceUpgradeRequest,
    session: Session = Depends(get_service_session),
    producer: Any = Depends(get_service_stream_producer),
) -> ServiceResponse:
    service = session.get(DeploymentService, service_id)
    if service is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
    instance = session.scalar(
        select(DeploymentInstance).where(
            DeploymentInstance.deployment_service_id == service_id
        )
    )
    if instance is None or not _instance_has_healthy_tuple(instance):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Service does not have a healthy deployment to upgrade",
        )
    pipeline = session.get(TrainingPipeline, service.pipeline_id)
    trained_model = session.get(TrainedModel, request.trained_model_id)
    if (
        pipeline is None
        or trained_model is None
        or trained_model.pipeline_id != pipeline.id
        or trained_model.status != "ready"
        or pipeline.task != "detect"
        or trained_model.task != "detect"
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Trained model is not ready for this deployment pipeline",
        )

    _node, inventory = _deployment_node(session, instance.node_id)
    try:
        image_digest = validate_image_digest(request.image_digest)
        artifact = ModelArtifact(
            task=trained_model.task,
            artifact_uri=trained_model.artifact_uri,
            checksum=request.model_checksum,
            format=_artifact_format(trained_model.artifact_uri),
        )
        plan = build_deployment_plan(
            artifact,
            inventory,
            DeploymentOptions(
                format=request.format,
                precision=request.precision,
                input_shape=request.input_shape,
                gpu_uuids=request.gpu_uuids,
                calibration_dataset_uri=request.calibration_dataset_uri,
            ),
        )
        _require_minio_uri(trained_model.artifact_uri, "Trained model artifact")
        if request.calibration_dataset_uri is not None:
            _require_minio_uri(request.calibration_dataset_uri, "Calibration dataset")
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error

    task_id = new_id()
    execution_id = new_id()
    config = dict(service.config)
    config["current_remote_execution_id"] = execution_id
    config["deployment"] = {
        "image_digest": image_digest,
        "model_checksum": artifact.checksum,
        "model_format": artifact.format,
        "format": plan.export_format,
        "precision": plan.precision,
        "input_shape": list(plan.input_shape),
        "gpu_uuids": list(plan.gpu_uuids),
        "engine_cache_key": plan.engine_cache_key,
        "calibration_dataset_uri": request.calibration_dataset_uri,
        "port": request.port,
    }
    task = Task(
        id=task_id,
        task_type=TaskType.EDGE_DEPLOY.value,
        status=TaskStatus.QUEUED.value,
        progress=0,
        resource_type="deployment_service",
        resource_id=service.id,
        payload={},
    )
    execution = RemoteExecution(
        id=execution_id,
        node_id=instance.node_id,
        task_id=task_id,
        deployment_service_id=service.id,
        resource_type="deployment_instance",
        resource_id=instance.id,
        operation=_DEPLOY_OPERATION,
        phase="queued",
        status="queued",
        idempotency_key=f"deploy:{instance.id}:{execution_id}",
    )
    service.trained_model_id = trained_model.id
    service.config = config
    service.status = "upgrade_queued"
    instance.status = "upgrade_queued"
    session.add_all([service, instance, task, execution])
    session.commit()
    try:
        await _enqueue_execution(
            producer,
            task_id=task_id,
            task_type=TaskType.EDGE_DEPLOY,
            remote_execution_id=execution_id,
        )
    except Exception:
        _mark_enqueue_failed(
            session,
            service_id=service.id,
            instance_id=instance.id,
            task_id=task_id,
            execution_id=execution_id,
            restore_healthy=True,
        )
    return _service_response(session, service.id)


@router.post(
    "/{service_id}/stop",
    response_model=ServiceResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def stop_service(
    service_id: str,
    session: Session = Depends(get_service_session),
    producer: Any = Depends(get_service_stream_producer),
) -> ServiceResponse:
    return await _queue_service_operation(
        session,
        producer,
        service_id=service_id,
        operation=_STOP_OPERATION,
        task_type=TaskType.EDGE_STOP_DEPLOYMENT,
        service_status="stopping",
        require_rollback=False,
    )


@router.post(
    "/{service_id}/rollback",
    response_model=ServiceResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def rollback_service(
    service_id: str,
    session: Session = Depends(get_service_session),
    producer: Any = Depends(get_service_stream_producer),
) -> ServiceResponse:
    return await _queue_service_operation(
        session,
        producer,
        service_id=service_id,
        operation=_ROLLBACK_OPERATION,
        task_type=TaskType.EDGE_ROLLBACK,
        service_status="rollback_queued",
        require_rollback=True,
    )


@router.patch("/{service_id}", response_model=ServiceResponse)
async def update_service(
    service_id: str,
    request: ServiceUpdateRequest,
    session: Session = Depends(get_service_session),
    producer: Any = Depends(get_service_stream_producer),
) -> ServiceResponse:
    if request.status == "stopped":
        return await _queue_service_operation(
            session,
            producer,
            service_id=service_id,
            operation=_STOP_OPERATION,
            task_type=TaskType.EDGE_STOP_DEPLOYMENT,
            service_status="stopping",
            require_rollback=False,
        )
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail="Use the rollback operation to restore a prior deployment",
    )


@router.post("/{service_id}/predict/image", response_model=PipelinePredictResponse)
async def predict_service_image(
    service_id: str,
    file: UploadFile = File(...),
    session: Session = Depends(get_service_session),
    storage: ObjectStorageClient = Depends(get_pipeline_inference_storage),
    predictor: PipelinePredictor = Depends(get_pipeline_predictor),
) -> PipelinePredictResponse:
    service = session.get(DeploymentService, service_id)
    if service is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
    if service.status != "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Service is not running",
        )
    result = await predict_pipeline_image(
        pipeline_id=service.pipeline_id,
        file=file,
        model_weight=service.model_weight,
        environment=service.environment,
        session=session,
        storage=storage,
        predictor=predictor,
    )
    service.calls += 1
    session.add(service)
    session.commit()
    return result


@router.delete("/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_service(
    service_id: str,
    session: Session = Depends(get_service_session),
) -> Response:
    service = session.get(DeploymentService, service_id)
    if service is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
    if service.status != "stopped":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Service must be stopped before deletion",
        )
    execution_task_ids = list(
        session.scalars(
            select(RemoteExecution.task_id).where(
                RemoteExecution.deployment_service_id == service_id,
                RemoteExecution.task_id.is_not(None),
            )
        )
    )
    session.execute(
        delete(RemoteExecution).where(
            RemoteExecution.deployment_service_id == service_id
        )
    )
    session.execute(
        delete(DeploymentInstance).where(
            DeploymentInstance.deployment_service_id == service_id
        )
    )
    if execution_task_ids:
        session.execute(delete(Task).where(Task.id.in_(execution_task_ids)))
    session.delete(service)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _deployment_node(
    session: Session,
    node_id: str,
) -> tuple[ComputeNode, InventorySnapshot]:
    node = session.get(ComputeNode, node_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")
    if node.status != "online" or node.resource_pool_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Node is not available for deployment",
        )
    try:
        inventory = InventorySnapshot.model_validate(
            node.fingerprint.get("inventory_snapshot")
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Node does not have a supported inventory",
        ) from error
    pool = session.get(ResourcePool, node.resource_pool_id)
    if (
        pool is None
        or not pool.enabled
        or pool.kind != inventory.platform_kind
        or not pool_accepts_inventory(pool.compatibility_policy, inventory)
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Resource pool does not accept the node inventory",
        )
    return node, inventory


def _artifact_format(uri: str) -> Literal["pt", "onnx", "engine"]:
    suffix = PurePosixPath(uri.split("?", 1)[0]).suffix.lower().removeprefix(".")
    if suffix not in {"pt", "onnx", "engine"}:
        raise ValueError("trained model format is not supported for deployment")
    return suffix  # type: ignore[return-value]


def _require_minio_uri(uri: str, name: str) -> None:
    remainder = uri.removeprefix("minio://")
    if (
        remainder == uri
        or "/" not in remainder
        or "?" in remainder
        or "#" in remainder
        or not all(remainder.split("/", 1))
    ):
        raise ValueError(f"{name} must use a durable MinIO URI")


async def _queue_service_operation(
    session: Session,
    producer: Any,
    *,
    service_id: str,
    operation: str,
    task_type: TaskType,
    service_status: str,
    require_rollback: bool,
) -> ServiceResponse:
    service = session.get(DeploymentService, service_id)
    if service is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
    instance = session.scalar(
        select(DeploymentInstance).where(
            DeploymentInstance.deployment_service_id == service_id
        )
    )
    if instance is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Deployment instance is unavailable",
        )
    if require_rollback and (
        set(instance.rollback_metadata) != _ROLLBACK_FIELDS
        or any(instance.rollback_metadata.get(field) is None for field in _ROLLBACK_FIELDS)
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No prior healthy deployment is available for rollback",
        )

    restore_healthy = _instance_has_healthy_tuple(instance)
    task_id = new_id()
    execution_id = new_id()
    task = Task(
        id=task_id,
        task_type=task_type.value,
        status=TaskStatus.QUEUED.value,
        progress=0,
        resource_type="deployment_service",
        resource_id=service.id,
        payload={},
    )
    execution = RemoteExecution(
        id=execution_id,
        node_id=instance.node_id,
        task_id=task_id,
        deployment_service_id=service.id,
        resource_type="deployment_instance",
        resource_id=instance.id,
        operation=operation,
        phase="queued",
        status="queued",
        idempotency_key=f"{operation}:{instance.id}:{execution_id}",
    )
    service.status = service_status
    service.config = {
        **service.config,
        "current_remote_execution_id": execution_id,
    }
    instance.status = service_status
    session.add_all([service, instance, task, execution])
    session.commit()
    try:
        await _enqueue_execution(
            producer,
            task_id=task_id,
            task_type=task_type,
            remote_execution_id=execution_id,
        )
    except Exception:
        _mark_enqueue_failed(
            session,
            service_id=service.id,
            instance_id=instance.id,
            task_id=task_id,
            execution_id=execution_id,
            restore_healthy=restore_healthy,
        )
    return _service_response(session, service.id)


def _mark_enqueue_failed(
    session: Session,
    *,
    service_id: str,
    instance_id: str,
    task_id: str,
    execution_id: str,
    restore_healthy: bool = False,
) -> None:
    now = datetime.now(UTC)
    service = session.get(DeploymentService, service_id)
    instance = session.get(DeploymentInstance, instance_id)
    task = session.get(Task, task_id)
    execution = session.get(RemoteExecution, execution_id)
    assert service is not None
    assert instance is not None
    assert task is not None
    assert execution is not None
    service.status = "running" if restore_healthy else "failed"
    instance.status = "running" if restore_healthy else "failed"
    instance.health_status = "healthy" if restore_healthy else "unhealthy"
    task.status = TaskStatus.FAILED.value
    task.error_code = "ENQUEUE_FAILED"
    task.error_message = "Edge deployment could not be queued"
    task.finished_at = now
    execution.status = "failed"
    execution.phase = "enqueue_failed"
    execution.error_code = "ENQUEUE_FAILED"
    execution.error_message = "Edge deployment could not be queued"
    execution.finished_at = now
    session.add_all([service, instance, task, execution])
    session.commit()


def _instance_has_healthy_tuple(instance: DeploymentInstance) -> bool:
    return (
        instance.health_status == "healthy"
        and instance.container_id is not None
        and instance.image_digest is not None
        and instance.model_checksum is not None
        and instance.engine is not None
        and instance.engine_digest is not None
        and instance.port is not None
    )


def _service_response(session: Session, service_id: str) -> ServiceResponse:
    service = session.get(DeploymentService, service_id)
    if service is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
    instance = session.scalar(
        select(DeploymentInstance).where(
            DeploymentInstance.deployment_service_id == service_id
        )
    )
    execution = _current_service_execution(session, service)
    return ServiceResponse(
        id=service.id,
        name=service.name,
        pipeline_id=service.pipeline_id,
        trained_model_id=service.trained_model_id,
        model_name=service.model_name,
        model_weight=service.model_weight,
        environment=service.environment,
        instance_count=service.instance_count,
        instance_name=service.instance_name,
        resource_summary=service.resource_summary,
        status=service.status,
        endpoint=service.endpoint,
        calls=service.calls,
        config=service.config,
        instance_id=instance.id if instance is not None else None,
        node_id=instance.node_id if instance is not None else None,
        container_id=instance.container_id if instance is not None else None,
        image_digest=instance.image_digest if instance is not None else None,
        model_checksum=instance.model_checksum if instance is not None else None,
        engine=instance.engine if instance is not None else None,
        engine_digest=instance.engine_digest if instance is not None else None,
        port=instance.port if instance is not None else None,
        health_status=instance.health_status if instance is not None else None,
        task_id=execution.task_id if execution is not None else None,
        remote_execution_id=execution.id if execution is not None else None,
        phase=execution.phase if execution is not None else None,
        log_uri=execution.redacted_log_uri if execution is not None else None,
        error_code=execution.error_code if execution is not None else None,
        error_message=execution.error_message if execution is not None else None,
        created_at=service.created_at,
        updated_at=service.updated_at,
    )


def _current_service_execution(
    session: Session,
    service: DeploymentService,
) -> RemoteExecution | None:
    execution_id = service.config.get("current_remote_execution_id")
    if isinstance(execution_id, str):
        execution = session.get(RemoteExecution, execution_id)
        if execution is not None and execution.deployment_service_id == service.id:
            return execution
    return session.scalar(
        select(RemoteExecution)
        .where(RemoteExecution.deployment_service_id == service.id)
        .order_by(RemoteExecution.created_at.desc(), RemoteExecution.id.desc())
        .limit(1)
    )
