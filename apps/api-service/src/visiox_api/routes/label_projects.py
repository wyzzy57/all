import inspect
from collections.abc import Generator
from datetime import UTC, datetime
import hashlib
import json
import secrets
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from visiox_api.dependencies.auth import get_current_user
from visiox_api.dependencies.authorization import require_resource_permission
from visiox_api.dependencies.database import get_db_session
from visiox_api.routes.datasets import dataset_or_404
from visiox_api.services.audit import record_audit
from visiox_common.settings import Settings, get_settings
from visiox_common.tasks import TaskCommand, TaskStatus, TaskType
from visiox_db.models import LabelProject, Task
from visiox_db.models.identity import (
    AUDIT_RESULT_FAILED,
    AUDIT_RESULT_SUCCESS,
    PERMISSION_EDIT,
    PERMISSION_VIEW,
    User,
)
from visiox_messaging.streams import RedisStreamProducer
from visiox_yolo26.labelstudio.client import LabelStudioClient, LabelStudioError
from visiox_yolo26.labelstudio.templates import build_label_config


router = APIRouter(tags=["label-projects"])
LABEL_STUDIO_PROVIDER = "label_studio"


class LabelProjectCreateRequest(BaseModel):
    external_project_id: str | None = None


class LabelProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    dataset_id: str
    provider: str
    external_project_id: str | None
    project_url: str | None = None
    sync_status: str
    last_sync_at: datetime | None
    created_at: datetime
    updated_at: datetime


class LabelProjectListResponse(BaseModel):
    items: list[LabelProjectResponse]
    total: int


class LabelProjectTaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    task_type: str
    status: str
    progress: int
    resource_type: str | None
    resource_id: str | None
    stage: str | None
    payload: dict[str, Any]
    error_code: str | None
    error_message: str | None
    retryable: bool
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class LabelProjectLaunchResponse(BaseModel):
    launch_url: str
    expires_in: int = 60


class LabelProjectLaunchExchangeRequest(BaseModel):
    token: str


class LabelProjectLaunchExchangeResponse(BaseModel):
    destination: str
    external_project_id: str


get_label_project_session = get_db_session


def get_label_sync_stream_producer(request: Request) -> RedisStreamProducer:
    return RedisStreamProducer(request.app.state.redis)


def get_label_launch_cache(request: Request) -> Any:
    return request.app.state.redis


def get_label_studio_client(settings: Settings = Depends(get_settings)) -> Generator[LabelStudioClient]:
    client = LabelStudioClient(base_url=settings.label_studio_url, token=settings.label_studio_token)
    try:
        yield client
    finally:
        client.close()


async def _enqueue(producer: Any, command: TaskCommand) -> str:
    result = producer.enqueue(command)
    if inspect.isawaitable(result):
        return await result
    return str(result)


@router.post(
    "/datasets/{dataset_id}/label-projects",
    response_model=LabelProjectResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_label_project(
    dataset_id: str,
    response: Response,
    http_request: Request,
    payload: LabelProjectCreateRequest | None = None,
    session: Session = Depends(get_label_project_session),
    settings: Settings = Depends(get_settings),
    label_studio: LabelStudioClient = Depends(get_label_studio_client),
    actor: User = Depends(get_current_user),
) -> LabelProjectResponse:
    dataset = dataset_or_404(session, dataset_id)
    require_resource_permission(session, actor, "dataset", dataset_id, PERMISSION_EDIT)
    existing = _find_dataset_label_project(session, dataset_id)
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        return _label_project_response(existing, settings)

    payload = payload or LabelProjectCreateRequest()
    external_project_id = payload.external_project_id
    try:
        if external_project_id:
            _validate_external_project_id(external_project_id)
            label_studio.get_project(external_project_id)
        else:
            label_config = build_label_config(dataset.task, dataset.class_schema)
            external_project = label_studio.create_project(dataset.name, label_config)
            external_project_id = str(external_project["id"])
    except LabelStudioError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Label Studio 服务不可用，请确认服务已启动并可访问：{exc}",
        ) from exc

    label_project = LabelProject(
        dataset_id=dataset.id,
        provider=LABEL_STUDIO_PROVIDER,
        external_project_id=external_project_id,
        sync_status="pending",
    )
    session.add(label_project)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = _find_dataset_label_project(session, dataset_id)
        if existing is not None:
            response.status_code = status.HTTP_200_OK
            return _label_project_response(existing, settings)
        raise
    session.refresh(label_project)
    record_audit(
        session,
        actor,
        "label_project.create",
        "dataset",
        dataset.id,
        AUDIT_RESULT_SUCCESS,
        http_request.headers.get("x-request-id"),
        {"label_project_id": label_project.id, "external_project_id": external_project_id},
    )
    session.commit()
    return _label_project_response(label_project, settings)


@router.get("/datasets/{dataset_id}/label-projects", response_model=LabelProjectListResponse)
def list_label_projects(
    dataset_id: str,
    session: Session = Depends(get_label_project_session),
    settings: Settings = Depends(get_settings),
    actor: User = Depends(get_current_user),
) -> LabelProjectListResponse:
    dataset_or_404(session, dataset_id)
    require_resource_permission(session, actor, "dataset", dataset_id, PERMISSION_VIEW)
    projects = session.scalars(
        select(LabelProject)
        .where(LabelProject.dataset_id == dataset_id, LabelProject.provider == LABEL_STUDIO_PROVIDER)
        .order_by(LabelProject.created_at, LabelProject.id)
    ).all()
    return LabelProjectListResponse(
        items=[_label_project_response(project, settings) for project in projects],
        total=len(projects),
    )


@router.post(
    "/label-projects/{project_id}/sync-samples",
    response_model=LabelProjectTaskResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_sync_samples_task(
    project_id: str,
    request: Request,
    session: Session = Depends(get_label_project_session),
    producer: Any = Depends(get_label_sync_stream_producer),
    actor: User = Depends(get_current_user),
) -> Task:
    require_resource_permission(session, actor, "label_project", project_id, PERMISSION_EDIT)
    return await _create_label_project_task(
        session=session,
        producer=producer,
        project_id=project_id,
        task_type=TaskType.SYNC_LABEL_STUDIO_DATA,
        actor=actor,
        request_id=request.headers.get("x-request-id"),
    )


@router.post(
    "/label-projects/{project_id}/import-annotations",
    response_model=LabelProjectTaskResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_import_annotations_task(
    project_id: str,
    request: Request,
    session: Session = Depends(get_label_project_session),
    producer: Any = Depends(get_label_sync_stream_producer),
    actor: User = Depends(get_current_user),
) -> Task:
    require_resource_permission(session, actor, "label_project", project_id, PERMISSION_EDIT)
    return await _create_label_project_task(
        session=session,
        producer=producer,
        project_id=project_id,
        task_type=TaskType.IMPORT_LABEL_STUDIO_ANNOTATION,
        actor=actor,
        request_id=request.headers.get("x-request-id"),
    )


async def _create_label_project_task(
    session: Session,
    producer: Any,
    project_id: str,
    task_type: TaskType,
    actor: User,
    request_id: str | None,
) -> Task:
    project = _label_project_or_404(session, project_id)
    task = Task(
        task_type=task_type.value,
        status=TaskStatus.QUEUED.value,
        progress=0,
        resource_type="label_project",
        resource_id=project.id,
        payload={"dataset_id": project.dataset_id, "label_project_id": project.id},
    )
    session.add(task)
    session.commit()
    session.refresh(task)

    command = TaskCommand(
        task_id=task.id,
        task_type=task_type,
        resource_refs={"dataset_id": project.dataset_id, "label_project_id": project.id},
        payload=task.payload,
    )
    try:
        await _enqueue(producer, command)
    except Exception as exc:
        task.status = TaskStatus.FAILED.value
        task.error_code = "ENQUEUE_FAILED"
        task.error_message = str(exc)
        task.finished_at = datetime.now(UTC)
        session.add(task)
        session.commit()
        session.refresh(task)
    record_audit(
        session,
        actor,
        "label_project.sync" if task_type == TaskType.SYNC_LABEL_STUDIO_DATA else "label_project.import",
        "dataset",
        project.dataset_id,
        AUDIT_RESULT_FAILED if task.status == TaskStatus.FAILED.value else AUDIT_RESULT_SUCCESS,
        request_id,
        {"label_project_id": project.id, "task_id": task.id},
    )
    session.commit()
    return task


@router.post("/label-projects/{project_id}/launch", response_model=LabelProjectLaunchResponse)
async def launch_label_project(
    project_id: str,
    request: Request,
    session: Session = Depends(get_label_project_session),
    settings: Settings = Depends(get_settings),
    cache: Any = Depends(get_label_launch_cache),
    actor: User = Depends(get_current_user),
) -> LabelProjectLaunchResponse:
    project = _label_project_or_404(session, project_id)
    require_resource_permission(session, actor, "label_project", project_id, PERMISSION_VIEW)
    if not project.external_project_id or not settings.label_studio_public_url:
        raise HTTPException(status_code=409, detail="Managed Label Studio launch is unavailable")
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    destination = f"/projects/{project.external_project_id}/data"
    value = json.dumps(
        {
            "project_id": project.id,
            "external_project_id": project.external_project_id,
            "destination": destination,
            "user_id": actor.id,
        },
        separators=(",", ":"),
    )
    stored = await cache.set(f"label-launch:{token_hash}", value, ex=60, nx=True)
    if not stored:
        raise HTTPException(status_code=503, detail="Unable to create Label Studio launch session")
    record_audit(
        session,
        actor,
        "label_project.launch",
        "label_project",
        project.id,
        AUDIT_RESULT_SUCCESS,
        request.headers.get("x-request-id"),
        {"external_project_id": project.external_project_id},
    )
    session.commit()
    return LabelProjectLaunchResponse(
        launch_url=f"{settings.label_studio_public_url.rstrip('/')}/visiox-auth?launch_token={quote(token, safe='')}"
    )


@router.post(
    "/internal/label-studio/launch/exchange",
    response_model=LabelProjectLaunchExchangeResponse,
    include_in_schema=False,
)
async def exchange_label_project_launch(
    payload: LabelProjectLaunchExchangeRequest,
    cache: Any = Depends(get_label_launch_cache),
) -> LabelProjectLaunchExchangeResponse:
    token_hash = hashlib.sha256(payload.token.encode("utf-8")).hexdigest()
    raw = await cache.getdel(f"label-launch:{token_hash}")
    if not raw:
        raise HTTPException(status_code=401, detail="Label Studio launch token is invalid or expired")
    data = json.loads(raw)
    return LabelProjectLaunchExchangeResponse(
        destination=str(data["destination"]),
        external_project_id=str(data["external_project_id"]),
    )


def _find_dataset_label_project(session: Session, dataset_id: str) -> LabelProject | None:
    return session.scalars(
        select(LabelProject)
        .where(LabelProject.dataset_id == dataset_id, LabelProject.provider == LABEL_STUDIO_PROVIDER)
        .order_by(LabelProject.created_at, LabelProject.id)
        .limit(1)
    ).first()


def _label_project_response(project: LabelProject, settings: Settings) -> LabelProjectResponse:
    project_url = None
    if project.external_project_id:
        base_url = settings.label_studio_public_url or settings.label_studio_url
        destination = f"/projects/{project.external_project_id}/data"
        if settings.label_studio_public_url:
            project_url = f"{base_url.rstrip('/')}/visiox-auth?next={quote(destination, safe='/')}"
        else:
            project_url = f"{base_url.rstrip('/')}{destination}"
    response = LabelProjectResponse.model_validate(project)
    response.project_url = project_url
    return response


def _label_project_or_404(session: Session, project_id: str) -> LabelProject:
    project = session.get(LabelProject, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Label project not found")
    return project


def _validate_external_project_id(external_project_id: str) -> None:
    if not external_project_id.isdecimal() or int(external_project_id) <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="external_project_id must be a positive integer string",
        )
