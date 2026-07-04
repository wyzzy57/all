from __future__ import annotations

import inspect
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from visiox_common.tasks import TaskCommand, TaskStatus, TaskType
from visiox_db.models import EdgeApp, EdgeAppVersion, Task, TrainedModel
from visiox_db.session import get_session
from visiox_messaging.streams import RedisStreamProducer


router = APIRouter(prefix="/edge-apps", tags=["edge-apps"])
DEFAULT_INFERENCE_IMAGE = "registry.local/visiox/yolo26-inference:0.1.0"
SUPPORTED_EXPORT_FORMATS = {"onnx", "torchscript"}


class EdgeAppCreateRequest(BaseModel):
    name: str
    description: str | None = None


class EdgeAppResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None
    status: str
    created_at: datetime
    updated_at: datetime


class EdgeAppListResponse(BaseModel):
    items: list[EdgeAppResponse]
    total: int
    limit: int
    offset: int


class EdgeRuntimeRequest(BaseModel):
    image: str = DEFAULT_INFERENCE_IMAGE
    device: str = "cpu"
    confidence: float = Field(default=0.25, ge=0, le=1)
    iou: float = Field(default=0.7, ge=0, le=1)
    imgsz: int | None = Field(default=None, gt=0)
    half: bool = False


class EdgeAppVersionCreateRequest(BaseModel):
    trained_model_id: str
    version: str | None = None
    export_format: str = "onnx"
    runtime: EdgeRuntimeRequest = Field(default_factory=EdgeRuntimeRequest)
    cameras: list[dict[str, Any]] = Field(default_factory=list)
    rules: dict[str, Any] = Field(default_factory=dict)


class EdgeAppVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    edge_app_id: str
    trained_model_id: str | None
    version: str
    package_uri: str
    manifest: dict[str, Any]
    checksum: str | None
    status: str
    created_at: datetime
    updated_at: datetime
    task_id: str | None = None


class EdgeAppVersionListResponse(BaseModel):
    items: list[EdgeAppVersionResponse]
    total: int
    limit: int
    offset: int


def get_edge_app_session() -> Generator[Session]:
    yield from get_session()


def get_edge_app_stream_producer(request: Request) -> RedisStreamProducer:
    return RedisStreamProducer(request.app.state.redis)


async def _enqueue(producer: Any, command: TaskCommand) -> str:
    result = producer.enqueue(command)
    if inspect.isawaitable(result):
        return await result
    return str(result)


@router.post("", response_model=EdgeAppResponse)
def create_edge_app(
    request: EdgeAppCreateRequest,
    response: Response,
    session: Session = Depends(get_edge_app_session),
) -> EdgeApp:
    app = EdgeApp(name=request.name, description=request.description, status="draft")
    session.add(app)
    session.commit()
    session.refresh(app)
    response.status_code = status.HTTP_201_CREATED
    return app


@router.get("", response_model=EdgeAppListResponse)
def list_edge_apps(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_edge_app_session),
) -> EdgeAppListResponse:
    filters = []
    if status_filter is not None:
        filters.append(EdgeApp.status == status_filter)
    total_query = select(func.count()).select_from(EdgeApp)
    list_query = select(EdgeApp).order_by(EdgeApp.created_at, EdgeApp.id)
    if filters:
        total_query = total_query.where(*filters)
        list_query = list_query.where(*filters)
    total = session.scalar(total_query) or 0
    apps = session.scalars(list_query.limit(limit).offset(offset)).all()
    return EdgeAppListResponse(items=list(apps), total=total, limit=limit, offset=offset)


@router.get("/{edge_app_id}", response_model=EdgeAppResponse)
def get_edge_app(edge_app_id: str, session: Session = Depends(get_edge_app_session)) -> EdgeApp:
    app = session.get(EdgeApp, edge_app_id)
    if app is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Edge app not found")
    return app


@router.post("/{edge_app_id}/versions", response_model=EdgeAppVersionResponse)
async def create_edge_app_version(
    edge_app_id: str,
    request: EdgeAppVersionCreateRequest,
    response: Response,
    session: Session = Depends(get_edge_app_session),
    producer: Any = Depends(get_edge_app_stream_producer),
) -> EdgeAppVersionResponse:
    app = session.get(EdgeApp, edge_app_id)
    if app is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Edge app not found")
    trained_model = session.get(TrainedModel, request.trained_model_id)
    if trained_model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trained model not found")
    if trained_model.status != "ready":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Trained model is not ready: {trained_model.status}")
    if not trained_model.artifact_uri:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Trained model artifact is missing")
    export_format = request.export_format.lower()
    if export_format not in SUPPORTED_EXPORT_FORMATS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"Unsupported export format: {request.export_format}")

    version_name = request.version or f"{trained_model.version}-{trained_model.id[:8]}"
    duplicate_version = session.scalar(
        select(EdgeAppVersion).where(
            EdgeAppVersion.edge_app_id == app.id,
            EdgeAppVersion.version == version_name,
        )
    )
    if duplicate_version is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Edge app version already exists")
    runtime = request.runtime.model_dump()
    manifest = {
        "edge_app_id": app.id,
        "trained_model_id": trained_model.id,
        "task": trained_model.task,
        "version": version_name,
        "export_format": export_format,
        "runtime": runtime,
        "cameras": request.cameras,
        "rules": request.rules,
    }
    version = EdgeAppVersion(
        edge_app_id=app.id,
        trained_model_id=trained_model.id,
        version=version_name,
        package_uri="pending",
        manifest=manifest,
        status="queued",
    )
    task = Task(
        task_type=TaskType.BUILD_EDGE_APP_PACKAGE.value,
        status=TaskStatus.QUEUED.value,
        progress=0,
        resource_type="edge_app_version",
        payload={},
    )
    if app.status != "ready":
        app.status = "packaging"
    session.add_all([app, version, task])
    session.flush()
    task.resource_id = version.id
    task.payload = {
        "edge_app_id": app.id,
        "edge_app_version_id": version.id,
        "trained_model_id": trained_model.id,
        "export_format": export_format,
        "runtime": runtime,
        "cameras": request.cameras,
        "rules": request.rules,
    }
    version.manifest = {**manifest, "task_id": task.id}
    session.commit()
    session.refresh(version)
    session.refresh(task)
    response.status_code = status.HTTP_201_CREATED

    command = TaskCommand(
        task_id=task.id,
        task_type=TaskType.BUILD_EDGE_APP_PACKAGE,
        resource_refs={"edge_app_version_id": version.id},
        payload=task.payload,
    )
    try:
        await _enqueue(producer, command)
    except Exception as exc:
        version.status = "failed"
        app.status = "ready" if _edge_app_has_ready_version(session, app.id) else "failed"
        task.status = TaskStatus.FAILED.value
        task.error_code = "ENQUEUE_FAILED"
        task.error_message = str(exc)
        task.retryable = True
        task.finished_at = datetime.now(UTC)
        session.add_all([app, version, task])
        session.commit()
        session.refresh(version)
        session.refresh(task)

    return _version_response(version, task)


@router.get("/{edge_app_id}/versions", response_model=EdgeAppVersionListResponse)
def list_edge_app_versions(
    edge_app_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_edge_app_session),
) -> EdgeAppVersionListResponse:
    app = session.get(EdgeApp, edge_app_id)
    if app is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Edge app not found")
    total = session.scalar(
        select(func.count()).select_from(EdgeAppVersion).where(EdgeAppVersion.edge_app_id == edge_app_id)
    ) or 0
    versions = session.scalars(
        select(EdgeAppVersion)
        .where(EdgeAppVersion.edge_app_id == edge_app_id)
        .order_by(EdgeAppVersion.created_at, EdgeAppVersion.id)
        .limit(limit)
        .offset(offset)
    ).all()
    items = [_version_response(version, _task_for_version(session, version.id)) for version in versions]
    return EdgeAppVersionListResponse(items=items, total=total, limit=limit, offset=offset)


def _task_for_version(session: Session, edge_app_version_id: str) -> Task | None:
    return session.scalar(
        select(Task)
        .where(
            Task.task_type == TaskType.BUILD_EDGE_APP_PACKAGE.value,
            Task.resource_type == "edge_app_version",
            Task.resource_id == edge_app_version_id,
        )
        .order_by(Task.created_at.desc(), Task.id.desc())
        .limit(1)
    )


def _edge_app_has_ready_version(session: Session, edge_app_id: str) -> bool:
    return (
        session.scalar(
            select(func.count())
            .select_from(EdgeAppVersion)
            .where(
                EdgeAppVersion.edge_app_id == edge_app_id,
                EdgeAppVersion.status == "ready",
            )
        )
        or 0
    ) > 0


def _version_response(version: EdgeAppVersion, task: Task | None = None) -> EdgeAppVersionResponse:
    return EdgeAppVersionResponse(
        id=version.id,
        edge_app_id=version.edge_app_id,
        trained_model_id=version.trained_model_id,
        version=version.version,
        package_uri=version.package_uri,
        manifest=version.manifest,
        checksum=version.checksum,
        status=version.status,
        created_at=version.created_at,
        updated_at=version.updated_at,
        task_id=task.id if task is not None else None,
    )
