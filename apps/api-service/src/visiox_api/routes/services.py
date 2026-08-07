from __future__ import annotations

import base64
from datetime import UTC, datetime
from io import BytesIO
import inspect
from pathlib import Path, PurePosixPath
import tarfile
from tempfile import TemporaryDirectory
from typing import Any, Literal

import httpx
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

from visiox_api.dependencies.auth import get_current_user
from visiox_api.dependencies.authorization import require_resource_permission
from visiox_api.dependencies.database import get_db_session
from visiox_api.services.authorization import authorized_resource_predicate
from visiox_api.services.deployment_adapters import (
    resolve_deployment_adapter,
    runtime_config_checksum,
)
from visiox_api.routes.pipeline_inference import (
    PipelinePredictResponse,
    get_pipeline_inference_storage,
)
from visiox_common.tasks import TaskStatus, TaskType
from visiox_common.settings import get_settings
from visiox_db.base import new_id
from visiox_db.models import (
    BaseModel as BaseModelRecord,
    ComputeNode,
    DeploymentInstance,
    DeploymentService,
    LogStream,
    RemoteExecution,
    ResourcePool,
    Task,
    TrainedModel,
    TrainingPipeline,
)
from visiox_db.models.identity import (
    PERMISSION_DELETE,
    PERMISSION_EDIT,
    PERMISSION_INVOKE,
    PERMISSION_USE,
    PERMISSION_VIEW,
    User,
)
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
from visiox_storage.checksum import sha256_file


router = APIRouter(prefix="/services", tags=["services"])

_PADDLEX_BUNDLE_MAX_BYTES = 8 * 1024 * 1024 * 1024
_PADDLEX_BUNDLE_MAX_MEMBER_BYTES = 4 * 1024 * 1024 * 1024
_PADDLEX_BUNDLE_MAX_MEMBERS = 64

_DEPLOY_OPERATION = "deploy"
_STOP_OPERATION = "stop_deployment"
_START_OPERATION = "start_deployment"
_RESTART_OPERATION = "restart_deployment"
_ROLLBACK_OPERATION = "rollback"
_ROLLBACK_FIELDS = {
    "container_id",
    "image_digest",
    "model_checksum",
    "engine",
    "engine_digest",
    "port",
}
_ROLLBACK_IDENTITY_FIELDS = {
    "framework",
    "adapter_key",
    "adapter_version",
    "model_format",
    "resolved_backend",
    "runtime_image_digest",
    "runtime_config_checksum",
}
_ROLLBACK_FIELD_SETS = {
    frozenset(_ROLLBACK_FIELDS),
    frozenset(_ROLLBACK_FIELDS | _ROLLBACK_IDENTITY_FIELDS),
}


class ServiceCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    pipeline_id: str
    trained_model_id: str | None = None
    base_model_id: str | None = None
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
    image_digest: str | None = None
    model_checksum: str | None = None
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
    desired_state: str
    active_revision: int | None
    endpoint: str
    calls: int
    config: dict[str, Any]
    organization_id: str | None
    owner_user_id: str | None
    visibility: str
    instance_id: str | None
    deployment_revision: int | None
    node_id: str | None
    container_id: str | None
    image_digest: str | None
    model_checksum: str | None
    engine: str | None
    engine_digest: str | None
    port: int | None
    health_status: str | None
    health_checked_at: datetime | None
    task_id: str | None
    remote_execution_id: str | None
    phase: str | None
    log_uri: str | None
    log_stream_id: str | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class ServiceListResponse(BaseModel):
    items: list[ServiceResponse]
    total: int
    limit: int
    offset: int


get_service_session = get_db_session


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
    storage: ObjectStorageClient = Depends(get_pipeline_inference_storage),
    actor: User = Depends(get_current_user),
) -> ServiceResponse:
    pipeline = session.get(TrainingPipeline, request.pipeline_id)
    if pipeline is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline not found"
        )
    require_resource_permission(session, actor, "pipeline", pipeline.id, PERMISSION_USE)
    require_resource_permission(session, actor, "node", request.node_id, PERMISSION_USE)
    if request.trained_model_id:
        require_resource_permission(
            session, actor, "trained_model", request.trained_model_id, PERMISSION_USE
        )
    trained_model, base_model, artifact = _resolve_deployment_artifact(
        session,
        pipeline,
        request,
        storage,
    )
    if pipeline.task != "detect" or artifact.task != "detect":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Production deployment supports YOLO26 detect models only",
        )

    node, inventory = _deployment_node(session, request.node_id)
    try:
        deployment_config, instance_engine = _build_deployment_config(
            pipeline, trained_model, artifact, inventory, request
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
    if base_model is not None:
        normalized_config["base_model_id"] = base_model.id
    normalized_config["deployment"] = deployment_config
    image_digest = deployment_config["image_digest"]
    service = DeploymentService(
        id=service_id,
        name=request.name.strip(),
        pipeline_id=pipeline.id,
        trained_model_id=trained_model.id if trained_model is not None else None,
        model_name=base_model.filename
        if base_model is not None
        else request.model_name,
        model_weight=base_model.filename
        if base_model is not None
        else request.model_weight,
        environment=request.environment,
        instance_count=1,
        instance_name=request.instance_name.strip(),
        resource_summary=request.resource_summary,
        status="queued",
        desired_state="running",
        active_revision=None,
        endpoint="pending",
        config=normalized_config,
        organization_id=actor.organization_id,
        owner_user_id=actor.id,
        visibility="private",
    )
    instance = DeploymentInstance(
        id=instance_id,
        deployment_service_id=service_id,
        deployment_revision=1,
        node_id=node.id,
        instance_name=service.instance_name,
        image_digest=image_digest,
        model_checksum=artifact.checksum,
        engine=instance_engine,
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
    try:
        session.add(service)
        session.flush()
        session.add_all([instance, task])
        session.flush()
        session.add(execution)
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
    actor: User = Depends(get_current_user),
) -> ServiceListResponse:
    filters = [
        authorized_resource_predicate(
            session, actor, DeploymentService, "service", PERMISSION_VIEW
        )
    ]
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
    actor: User = Depends(get_current_user),
) -> ServiceResponse:
    require_resource_permission(session, actor, "service", service_id, PERMISSION_VIEW)
    return _service_response(session, service_id)


@router.post(
    "/{service_id}/upgrade",
    response_model=ServiceResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upgrade_service(
    service_id: str,
    request: ServiceUpgradeRequest,
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_service_session),
    producer: Any = Depends(get_service_stream_producer),
    storage: ObjectStorageClient = Depends(get_pipeline_inference_storage),
) -> ServiceResponse:
    require_resource_permission(session, actor, "service", service_id, PERMISSION_EDIT)
    service = session.get(DeploymentService, service_id)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Service not found"
        )
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
    require_resource_permission(
        session, actor, "trained_model", trained_model.id, PERMISSION_USE
    )

    _node, inventory = _deployment_node(session, instance.node_id)
    try:
        artifact = _trained_deployment_artifact(storage, pipeline, trained_model)
        deployment_config, _instance_engine = _build_deployment_config(
            pipeline, trained_model, artifact, inventory, request
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error

    task_id = new_id()
    execution_id = new_id()
    config = dict(service.config)
    previous_tuple = _instance_runtime_tuple(service, instance)
    config["upgrade_snapshot"] = {
        "service_config": dict(service.config),
        "trained_model_id": service.trained_model_id,
        "service_status": service.status,
        "desired_state": service.desired_state,
        "active_revision": service.active_revision,
        "instance_status": instance.status,
        "instance_health_status": instance.health_status,
        "instance_deployment_revision": instance.deployment_revision,
        "rollback_metadata": dict(instance.rollback_metadata),
    }
    config["current_remote_execution_id"] = execution_id
    config["deployment"] = deployment_config
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
    service.desired_state = "running"
    instance.status = "upgrade_queued"
    instance.rollback_metadata = previous_tuple
    instance.deployment_revision = (
        service.active_revision or instance.deployment_revision
    ) + 1
    session.add_all([service, instance, task])
    session.flush()
    session.add(execution)
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
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_service_session),
    producer: Any = Depends(get_service_stream_producer),
) -> ServiceResponse:
    require_resource_permission(session, actor, "service", service_id, PERMISSION_EDIT)
    return await _queue_service_operation(
        session,
        producer,
        service_id=service_id,
        operation=_STOP_OPERATION,
        task_type=TaskType.EDGE_STOP_DEPLOYMENT,
        service_status="stopping",
        desired_state="stopped",
        require_rollback=False,
    )


@router.post(
    "/{service_id}/start",
    response_model=ServiceResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_service(
    service_id: str,
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_service_session),
    producer: Any = Depends(get_service_stream_producer),
) -> ServiceResponse:
    require_resource_permission(session, actor, "service", service_id, PERMISSION_EDIT)
    service = session.get(DeploymentService, service_id)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Service not found"
        )
    if service.status == "running" and service.desired_state == "running":
        return _service_response(session, service_id)
    return await _queue_service_operation(
        session,
        producer,
        service_id=service_id,
        operation=_START_OPERATION,
        task_type=TaskType.EDGE_START_DEPLOYMENT,
        service_status="starting",
        desired_state="running",
        require_rollback=False,
        require_active_revision=True,
    )


@router.post(
    "/{service_id}/restart",
    response_model=ServiceResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def restart_service(
    service_id: str,
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_service_session),
    producer: Any = Depends(get_service_stream_producer),
) -> ServiceResponse:
    require_resource_permission(session, actor, "service", service_id, PERMISSION_EDIT)
    return await _queue_service_operation(
        session,
        producer,
        service_id=service_id,
        operation=_RESTART_OPERATION,
        task_type=TaskType.EDGE_RESTART_DEPLOYMENT,
        service_status="restarting",
        desired_state="running",
        require_rollback=False,
        require_active_revision=True,
    )


@router.post(
    "/{service_id}/rollback",
    response_model=ServiceResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def rollback_service(
    service_id: str,
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_service_session),
    producer: Any = Depends(get_service_stream_producer),
) -> ServiceResponse:
    require_resource_permission(session, actor, "service", service_id, PERMISSION_EDIT)
    return await _queue_service_operation(
        session,
        producer,
        service_id=service_id,
        operation=_ROLLBACK_OPERATION,
        task_type=TaskType.EDGE_ROLLBACK,
        service_status="rollback_queued",
        desired_state="running",
        require_rollback=True,
    )


@router.patch("/{service_id}", response_model=ServiceResponse)
async def update_service(
    service_id: str,
    request: ServiceUpdateRequest,
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_service_session),
    producer: Any = Depends(get_service_stream_producer),
) -> ServiceResponse:
    require_resource_permission(session, actor, "service", service_id, PERMISSION_EDIT)
    if request.status == "stopped":
        return await _queue_service_operation(
            session,
            producer,
            service_id=service_id,
            operation=_STOP_OPERATION,
            task_type=TaskType.EDGE_STOP_DEPLOYMENT,
            service_status="stopping",
            desired_state="stopped",
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
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_service_session),
) -> PipelinePredictResponse:
    require_resource_permission(
        session, actor, "service", service_id, PERMISSION_INVOKE
    )
    service = session.get(DeploymentService, service_id)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Service not found"
        )
    if service.status != "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Service is not running",
        )
    result = await _request_deployed_prediction(service, file)
    service.calls += 1
    session.add(service)
    session.commit()
    return result


async def _request_deployed_prediction(
    service: DeploymentService,
    file: UploadFile,
    *,
    client: httpx.AsyncClient | None = None,
) -> PipelinePredictResponse:
    endpoint = service.endpoint.strip().rstrip("/")
    try:
        endpoint_url = httpx.URL(endpoint)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Service endpoint is unavailable",
        ) from exc
    if endpoint_url.scheme not in {"http", "https"} or not endpoint_url.host:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Service endpoint is unavailable",
        )
    if file.content_type and not file.content_type.startswith("image/"):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only image files are supported",
        )
    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Image file is empty",
        )

    async def send(active_client: httpx.AsyncClient) -> httpx.Response:
        return await active_client.post(
            f"{endpoint}/predict/image",
            files={
                "file": (
                    Path(file.filename or "image.png").name,
                    image_bytes,
                    file.content_type or "application/octet-stream",
                )
            },
        )

    try:
        if client is None:
            async with httpx.AsyncClient(timeout=60.0) as active_client:
                response = await send(active_client)
        else:
            response = await send(client)
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Deployed inference service is unreachable: {exc}",
        ) from exc

    if response.is_error:
        detail = _upstream_error_detail(response)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Deployed inference failed: {detail}",
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Deployed inference returned an invalid response",
        ) from exc
    predictions = payload.get("predictions")
    if not isinstance(predictions, list):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Deployed inference response is missing predictions",
        )
    annotated_bytes = _render_deployed_predictions(image_bytes, predictions)
    return PipelinePredictResponse(
        pipeline_id=service.pipeline_id,
        model_weight=service.model_weight,
        environment=service.resource_summary or service.environment,
        predictions=predictions,
        result_image=f"data:image/png;base64,{base64.b64encode(annotated_bytes).decode('ascii')}",
        latency_ms=_optional_float(payload.get("latency_ms")),
    )


def _render_deployed_predictions(
    image_bytes: bytes,
    predictions: list[dict[str, Any]],
) -> bytes:
    from PIL import Image, ImageDraw

    with Image.open(BytesIO(image_bytes)) as source:
        image = source.convert("RGB")
    draw = ImageDraw.Draw(image)
    line_width = max(2, round(min(image.size) / 240))
    for prediction in predictions:
        bbox = prediction.get("bbox")
        if not isinstance(bbox, dict):
            continue
        try:
            coordinates = tuple(float(bbox[key]) for key in ("x1", "y1", "x2", "y2"))
        except (KeyError, TypeError, ValueError):
            continue
        color = _prediction_color(prediction)
        draw.rectangle(coordinates, outline=color, width=line_width)
        label = str(prediction.get("label", prediction.get("class_id", "object")))
        confidence = _optional_float(prediction.get("confidence"))
        caption = f"{label} {confidence:.2f}" if confidence is not None else label
        text_box = draw.textbbox((coordinates[0], coordinates[1]), caption)
        text_height = max(14, text_box[3] - text_box[1] + 6)
        caption_top = max(0.0, coordinates[1] - text_height)
        caption_right = min(
            float(image.width), coordinates[0] + text_box[2] - text_box[0] + 8
        )
        draw.rectangle(
            (coordinates[0], caption_top, caption_right, coordinates[1]),
            fill=color,
        )
        draw.text((coordinates[0] + 4, caption_top + 2), caption, fill="white")
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


_PREDICTION_COLORS = (
    "#e45756",
    "#54a24b",
    "#f58518",
    "#b279a2",
    "#72b7b2",
    "#ff9da6",
    "#eeca3b",
    "#4c78a8",
    "#9d755d",
    "#79706e",
)


def _prediction_color(prediction: dict[str, Any]) -> str:
    class_id = prediction.get("class_id")
    try:
        color_index = int(class_id)
    except (TypeError, ValueError):
        label = str(prediction.get("label", "object"))
        color_index = sum(label.encode("utf-8"))
    return _PREDICTION_COLORS[color_index % len(_PREDICTION_COLORS)]


def _upstream_error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:500] or f"HTTP {response.status_code}"
    detail = payload.get("detail") if isinstance(payload, dict) else None
    return str(detail or f"HTTP {response.status_code}")[:500]


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


@router.delete("/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_service(
    service_id: str,
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_service_session),
) -> Response:
    require_resource_permission(
        session, actor, "service", service_id, PERMISSION_DELETE
    )
    service = session.get(DeploymentService, service_id)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Service not found"
        )
    if service.status != "stopped":
        instance_id = session.scalar(
            select(DeploymentInstance.id).where(
                DeploymentInstance.deployment_service_id == service_id
            )
        )
        if instance_id is not None:
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Node not found"
        )
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


def _resolve_deployment_artifact(
    session: Session,
    pipeline: TrainingPipeline,
    request: ServiceCreateRequest,
    storage: ObjectStorageClient,
) -> tuple[TrainedModel | None, BaseModelRecord | None, ModelArtifact]:
    if bool(request.trained_model_id) == bool(request.base_model_id):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Select exactly one trained model or official base model",
        )

    if request.trained_model_id is not None:
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
        try:
            artifact = _trained_deployment_artifact(storage, pipeline, trained_model)
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Trained model metadata is invalid",
            ) from error
        return trained_model, None, artifact

    base_model = session.get(BaseModelRecord, request.base_model_id)
    if (
        base_model is None
        or base_model.family.lower() != "yolo26"
        or base_model.task != pipeline.task
        or base_model.status != "ready"
        or base_model.local_uri is None
        or base_model.checksum is None
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Official base model is not ready for this pipeline",
        )
    try:
        artifact = ModelArtifact(
            task=base_model.task,
            artifact_uri=base_model.local_uri,
            checksum=base_model.checksum,
            format=_artifact_format(base_model.local_uri),
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Official base model metadata is invalid",
        ) from error
    return None, base_model, artifact


def _trained_deployment_artifact(
    storage: ObjectStorageClient,
    pipeline: TrainingPipeline,
    trained_model: TrainedModel,
) -> ModelArtifact:
    if pipeline.framework == "paddlex":
        bundle_uri, bundle_checksum = _prepare_paddlex_deployment_bundle(
            storage, trained_model
        )
        return ModelArtifact(
            task=trained_model.task,
            artifact_uri=bundle_uri,
            checksum=bundle_checksum,
            format="paddle_inference_bundle",
        )
    checksum = _trained_model_checksum(trained_model)
    if checksum is None:
        checksum = _calculate_trained_model_checksum(storage, trained_model)
        trained_model.metrics = {**trained_model.metrics, "checksum": checksum}
    return ModelArtifact(
        task=trained_model.task,
        artifact_uri=trained_model.artifact_uri,
        checksum=checksum,
        format=_artifact_format(trained_model.artifact_uri),
    )


def _build_deployment_config(
    pipeline: TrainingPipeline,
    trained_model: TrainedModel | None,
    artifact: ModelArtifact,
    inventory: InventorySnapshot,
    request: ServiceCreateRequest | ServiceUpgradeRequest,
) -> tuple[dict[str, Any], str]:
    settings = get_settings()
    default_digest = (
        settings.paddlex_inference_image_digest
        if pipeline.framework == "paddlex"
        else settings.deployment_image_digest
    )
    image_digest = validate_image_digest(request.image_digest or default_digest)
    _require_minio_uri(artifact.artifact_uri, "Model artifact")
    if request.calibration_dataset_uri is not None:
        _require_minio_uri(request.calibration_dataset_uri, "Calibration dataset")
    if pipeline.framework == "paddlex":
        if trained_model is None:
            raise ValueError("PaddleX deployment requires a trained static model")
        resolution = resolve_deployment_adapter(
            pipeline,
            trained_model,
            inventory,
            runtime_image_digest=image_digest,
            precision=request.precision,
            input_shape=request.input_shape,
            gpu_uuids=request.gpu_uuids,
        )
        return (
            {
                **resolution.as_config(),
                "image_digest": image_digest,
                "artifact_uri": artifact.artifact_uri,
                "model_checksum": artifact.checksum,
                "format": resolution.model_format,
                "engine_cache_key": None,
                "calibration_dataset_uri": None,
                "port": request.port,
            },
            resolution.model_format,
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
            runtime_image_digest=image_digest,
        ),
    )
    if request.model_checksum is not None and request.model_checksum != artifact.checksum:
        raise ValueError("Requested model checksum does not match the selected artifact")
    identity = {
        "framework": "ultralytics",
        "adapter_key": pipeline.adapter_key,
        "adapter_version": pipeline.adapter_version,
        "model_format": artifact.format,
        "resolved_backend": "tensorrt" if plan.export_format == "engine" else "pytorch",
        "runtime_image_digest": image_digest,
        "optimization": "auto",
        "device": "gpu:0" if plan.gpu_uuids else "cpu",
        "precision": plan.precision,
        "input_shape": plan.input_shape,
        "gpu_uuids": plan.gpu_uuids,
    }
    return (
        {
            **identity,
            "runtime_config_checksum": runtime_config_checksum(identity),
            "image_digest": image_digest,
            "artifact_uri": artifact.artifact_uri,
            "model_checksum": artifact.checksum,
            "format": plan.export_format,
            "input_shape": list(plan.input_shape),
            "gpu_uuids": list(plan.gpu_uuids),
            "engine_cache_key": plan.engine_cache_key,
            "calibration_dataset_uri": request.calibration_dataset_uri,
            "port": request.port,
        },
        plan.export_format,
    )


def _trained_model_checksum(model: TrainedModel) -> str | None:
    for key in ("checksum", "sha256", "artifact_checksum"):
        value = model.metrics.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _calculate_trained_model_checksum(
    storage: ObjectStorageClient,
    model: TrainedModel,
) -> str:
    try:
        _require_minio_uri(model.artifact_uri, "Trained model artifact")
        bucket, object_name = model.artifact_uri.removeprefix("minio://").split("/", 1)
        with TemporaryDirectory(prefix="visiox-model-checksum-") as temp_dir:
            artifact_path = Path(temp_dir) / (
                PurePosixPath(object_name).name or "model.pt"
            )
            storage.get_file(bucket, object_name, artifact_path)
            return sha256_file(artifact_path)
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Trained model artifact is unavailable for checksum calculation",
        ) from error


def _prepare_paddlex_deployment_bundle(
    storage: ObjectStorageClient,
    model: TrainedModel,
) -> tuple[str, str]:
    runtime_digest = validate_image_digest(get_settings().paddlex_inference_image_digest)
    _ensure_paddlex_runtime_compatibility(model, runtime_digest)
    cached = model.deployment_compatibility.get("paddlex_bundle")
    if isinstance(cached, dict):
        uri = cached.get("uri")
        checksum = cached.get("checksum_sha256")
        size_bytes = cached.get("size_bytes")
        if (
            isinstance(uri, str)
            and isinstance(checksum, str)
            and isinstance(size_bytes, int)
            and cached.get("artifact_type") == "paddle_inference_bundle"
            and cached.get("artifact_role") == "paddle_inference_bundle"
        ):
            _require_minio_uri(uri, "PaddleX deployment bundle")
            bucket, object_name = uri.removeprefix("minio://").split("/", 1)
            manifest_entries = model.artifact_manifest.get("deployment_artifacts")
            manifest_match = isinstance(manifest_entries, list) and any(
                isinstance(item, dict)
                and item.get("uri") == uri
                and item.get("checksum_sha256") == checksum
                and item.get("size_bytes") == size_bytes
                and item.get("artifact_type") == "paddle_inference_bundle"
                and item.get("artifact_role") == "paddle_inference_bundle"
                for item in manifest_entries
            )
            if (
                len(checksum) == 64
                and all(ch in "0123456789abcdef" for ch in checksum)
                and size_bytes > 0
                and manifest_match
                and storage.object_size(bucket, object_name) == size_bytes
            ):
                return uri, checksum

    manifest = model.artifact_manifest
    entries = manifest.get("role_artifacts") if isinstance(manifest, dict) else None
    if not isinstance(entries, list) or not entries:
        raise ValueError("PaddleX inference bundle manifest is unavailable")
    try:
        source_bucket, primary_object = model.artifact_uri.removeprefix(
            "minio://"
        ).split("/", 1)
    except ValueError as error:
        raise ValueError("PaddleX static artifacts must use durable MinIO URIs") from error
    if model.artifact_uri == f"{source_bucket}/{primary_object}":
        raise ValueError("PaddleX static artifacts must use durable MinIO URIs")
    validated: list[tuple[PurePosixPath, str, int]] = []
    for raw in entries:
        if not isinstance(raw, dict):
            raise ValueError("PaddleX inference bundle manifest is invalid")
        path = raw.get("path")
        checksum = raw.get("checksum_sha256")
        size = raw.get("size_bytes")
        candidate = PurePosixPath(path) if isinstance(path, str) else PurePosixPath(".")
        if (
            not isinstance(path, str)
            or candidate.is_absolute()
            or ".." in candidate.parts
            or not isinstance(checksum, str)
            or len(checksum) != 64
            or not all(ch in "0123456789abcdef" for ch in checksum)
            or not isinstance(size, int)
            or size < 0
        ):
            raise ValueError("PaddleX inference bundle manifest is invalid")
        validated.append((candidate, checksum, size))
    if (
        len(validated) > _PADDLEX_BUNDLE_MAX_MEMBERS
        or any(size > _PADDLEX_BUNDLE_MAX_MEMBER_BYTES for _path, _checksum, size in validated)
        or sum(size for _path, _checksum, size in validated) > _PADDLEX_BUNDLE_MAX_BYTES
    ):
        raise ValueError("PaddleX inference bundle exceeds platform size limits")
    parents = {item[0].parent for item in validated}
    names = {item[0].name for item in validated}
    if len(parents) != 1 or len(names) != len(validated):
        raise ValueError("PaddleX inference bundle must contain one flat model directory")
    primary_entry = next(
        (path for path, _checksum, _size in validated if primary_object.endswith(str(path))),
        None,
    )
    if primary_entry is None:
        raise ValueError("PaddleX primary artifact does not match its manifest")
    object_prefix = primary_object[: -len(str(primary_entry))]

    with TemporaryDirectory(prefix="visiox-paddlex-deploy-") as temp_dir:
        root = Path(temp_dir)
        archive_path = root / "paddle-inference-bundle.tar"
        local_files: list[tuple[Path, str]] = []
        for path, checksum, size in sorted(validated, key=lambda item: str(item[0])):
            local = root / "files" / path.name
            storage.get_file(source_bucket, object_prefix + str(path), local)
            actual_size = local.stat().st_size
            if (
                actual_size > _PADDLEX_BUNDLE_MAX_MEMBER_BYTES
                or actual_size != size
                or sha256_file(local) != checksum
            ):
                raise ValueError("PaddleX static artifact integrity check failed")
            local_files.append((local, path.name))
        with tarfile.open(archive_path, mode="w") as archive:
            for local, name in local_files:
                info = tarfile.TarInfo(name)
                info.size = local.stat().st_size
                info.mode = 0o400
                info.mtime = 0
                with local.open("rb") as source:
                    archive.addfile(info, source)
        bundle_checksum = sha256_file(archive_path)
        bundle_size = archive_path.stat().st_size
        object_name = f"deployments/{model.id}/paddle-inference-{bundle_checksum}.tar"
        storage.put_file(
            "models", object_name, archive_path, content_type="application/x-tar"
        )
    bundle_uri = f"minio://models/{object_name}"
    model.deployment_compatibility = {
        **model.deployment_compatibility,
        "paddlex_bundle": {
            "uri": bundle_uri,
            "checksum_sha256": bundle_checksum,
            "size_bytes": bundle_size,
            "format": "tar",
            "artifact_type": "paddle_inference_bundle",
            "artifact_role": "paddle_inference_bundle",
        },
    }
    bundle_entry = {
        "uri": bundle_uri,
        "checksum_sha256": bundle_checksum,
        "size_bytes": bundle_size,
        "format": "tar",
        "artifact_type": "paddle_inference_bundle",
        "artifact_role": "paddle_inference_bundle",
    }
    model.artifact_manifest = {
        **model.artifact_manifest,
        "deployment_artifacts": [bundle_entry],
    }
    return bundle_uri, bundle_checksum


def _ensure_paddlex_runtime_compatibility(
    model: TrainedModel,
    runtime_image_digest: str,
) -> None:
    compatibility = dict(model.deployment_compatibility)
    entries = compatibility.get("runtime_compatibility")
    normalized = list(entries) if isinstance(entries, list) else []
    if not any(
        isinstance(entry, dict)
        and entry.get("runtime_image_digest") == runtime_image_digest
        and entry.get("adapter_key") == model.adapter_key
        and entry.get("model_format") == "paddle_inference_bundle"
        for entry in normalized
    ):
        adapter_version = model.artifact_manifest.get("adapter_version")
        normalized.append(
            {
                "runtime_image_digest": runtime_image_digest,
                "adapter_key": model.adapter_key,
                "adapter_version": adapter_version,
                "model_format": "paddle_inference_bundle",
                "backends": ["paddle_inference"],
                "precisions": ["fp32"],
                "source": "runtime_image_default",
            }
        )
    model.deployment_compatibility = {
        **compatibility,
        "runtime_compatibility": normalized,
    }


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
    desired_state: str,
    require_rollback: bool,
    require_active_revision: bool = False,
) -> ServiceResponse:
    service = session.get(DeploymentService, service_id)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Service not found"
        )
    instance = session.scalar(
        select(DeploymentInstance).where(
            DeploymentInstance.deployment_service_id == service_id
        )
    )
    if instance is None:
        if operation == _STOP_OPERATION:
            service.status = "stopped"
            service.desired_state = "stopped"
            service.endpoint = ""
            service.config = {
                key: value
                for key, value in service.config.items()
                if key != "current_remote_execution_id"
            }
            session.add(service)
            session.commit()
            return _service_response(session, service.id)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Deployment instance is unavailable",
        )
    if require_active_revision and (
        service.active_revision is None
        or instance.deployment_revision != service.active_revision
        or instance.container_id is None
        or instance.port is None
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No successful deployment revision is available",
        )
    if require_rollback and not _valid_rollback_metadata(instance.rollback_metadata):
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
    service.desired_state = desired_state
    service.config = {
        **service.config,
        "current_remote_execution_id": execution_id,
    }
    instance.status = service_status
    session.add_all([service, instance, task])
    session.flush()
    session.add(execution)
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
    if restore_healthy:
        _restore_upgrade_snapshot(service, instance)
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


def _restore_upgrade_snapshot(
    service: DeploymentService,
    instance: DeploymentInstance,
) -> bool:
    snapshot = service.config.get("upgrade_snapshot")
    if not isinstance(snapshot, dict) or not isinstance(
        snapshot.get("service_config"), dict
    ):
        return False
    service.config = dict(snapshot["service_config"])
    service.trained_model_id = snapshot.get("trained_model_id")
    service.status = str(snapshot.get("service_status", "running"))
    service.desired_state = str(snapshot.get("desired_state", "running"))
    service.active_revision = snapshot.get("active_revision")
    instance.status = str(snapshot.get("instance_status", "running"))
    instance.health_status = str(
        snapshot.get("instance_health_status", "healthy")
    )
    instance.deployment_revision = int(
        snapshot.get("instance_deployment_revision", instance.deployment_revision)
    )
    rollback_metadata = snapshot.get("rollback_metadata")
    instance.rollback_metadata = (
        dict(rollback_metadata) if isinstance(rollback_metadata, dict) else {}
    )
    return True


def _instance_runtime_tuple(
    service: DeploymentService,
    instance: DeploymentInstance,
) -> dict[str, Any]:
    if not _instance_has_healthy_tuple(instance):
        raise ValueError("deployment instance does not have a healthy runtime tuple")
    result: dict[str, Any] = {
        "container_id": instance.container_id,
        "image_digest": instance.image_digest,
        "model_checksum": instance.model_checksum,
        "engine": instance.engine,
        "engine_digest": instance.engine_digest,
        "port": instance.port,
    }
    deployment = service.config.get("deployment")
    if isinstance(deployment, dict) and _ROLLBACK_IDENTITY_FIELDS.issubset(deployment):
        result.update(
            {field: str(deployment[field]) for field in _ROLLBACK_IDENTITY_FIELDS}
        )
    return result


def _valid_rollback_metadata(value: dict[str, Any]) -> bool:
    return frozenset(value) in _ROLLBACK_FIELD_SETS and all(
        value.get(field) is not None for field in value
    )


def _service_response(session: Session, service_id: str) -> ServiceResponse:
    service = session.get(DeploymentService, service_id)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Service not found"
        )
    instance = session.scalar(
        select(DeploymentInstance).where(
            DeploymentInstance.deployment_service_id == service_id
        )
    )
    execution = _current_service_execution(session, service)
    log_stream_id = session.scalar(
        select(LogStream.id)
        .where(
            LogStream.resource_type == "deployment_service",
            LogStream.resource_id == service.id,
        )
        .order_by(LogStream.created_at.desc(), LogStream.id.desc())
        .limit(1)
    )
    return ServiceResponse(
        id=service.id,
        name=service.name,
        organization_id=service.organization_id,
        owner_user_id=service.owner_user_id,
        visibility=service.visibility,
        pipeline_id=service.pipeline_id,
        trained_model_id=service.trained_model_id,
        model_name=service.model_name,
        model_weight=service.model_weight,
        environment=service.environment,
        instance_count=service.instance_count,
        instance_name=service.instance_name,
        resource_summary=service.resource_summary,
        status=service.status,
        desired_state=service.desired_state,
        active_revision=service.active_revision,
        endpoint=service.endpoint,
        calls=service.calls,
        config=service.config,
        instance_id=instance.id if instance is not None else None,
        deployment_revision=(
            instance.deployment_revision if instance is not None else None
        ),
        node_id=instance.node_id if instance is not None else None,
        container_id=instance.container_id if instance is not None else None,
        image_digest=instance.image_digest if instance is not None else None,
        model_checksum=instance.model_checksum if instance is not None else None,
        engine=instance.engine if instance is not None else None,
        engine_digest=instance.engine_digest if instance is not None else None,
        port=instance.port if instance is not None else None,
        health_status=instance.health_status if instance is not None else None,
        health_checked_at=instance.health_checked_at if instance is not None else None,
        task_id=execution.task_id if execution is not None else None,
        remote_execution_id=execution.id if execution is not None else None,
        phase=execution.phase if execution is not None else None,
        log_uri=execution.redacted_log_uri if execution is not None else None,
        log_stream_id=log_stream_id,
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
