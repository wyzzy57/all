import inspect
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from visiox_api.routes.datasets import dataset_or_404
from visiox_common.settings import Settings, get_settings
from visiox_common.tasks import TaskCommand, TaskStatus, TaskType
from visiox_db.models import LabelProject, Task
from visiox_db.session import get_session
from visiox_messaging.streams import RedisStreamProducer
from visiox_yolo26.labelstudio.client import LabelStudioClient
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


def get_label_project_session() -> Generator[Session]:
    yield from get_session()


def get_label_sync_stream_producer(request: Request) -> RedisStreamProducer:
    return RedisStreamProducer(request.app.state.redis)


def get_label_studio_client(settings: Settings = Depends(get_settings)) -> LabelStudioClient:
    return LabelStudioClient(base_url=settings.label_studio_url, token=settings.label_studio_token)


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
    request: LabelProjectCreateRequest | None = None,
    session: Session = Depends(get_label_project_session),
    label_studio: LabelStudioClient = Depends(get_label_studio_client),
) -> LabelProject:
    dataset = dataset_or_404(session, dataset_id)
    existing = _find_dataset_label_project(session, dataset_id)
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        return existing

    request = request or LabelProjectCreateRequest()
    external_project_id = request.external_project_id
    if external_project_id:
        label_studio.get_project(external_project_id)
    else:
        label_config = build_label_config(dataset.task, dataset.class_schema)
        external_project = label_studio.create_project(dataset.name, label_config)
        external_project_id = str(external_project["id"])

    label_project = LabelProject(
        dataset_id=dataset.id,
        provider=LABEL_STUDIO_PROVIDER,
        external_project_id=external_project_id,
        sync_status="pending",
    )
    session.add(label_project)
    session.commit()
    session.refresh(label_project)
    return label_project


@router.get("/datasets/{dataset_id}/label-projects", response_model=LabelProjectListResponse)
def list_label_projects(
    dataset_id: str,
    session: Session = Depends(get_label_project_session),
) -> LabelProjectListResponse:
    dataset_or_404(session, dataset_id)
    projects = session.scalars(
        select(LabelProject)
        .where(LabelProject.dataset_id == dataset_id, LabelProject.provider == LABEL_STUDIO_PROVIDER)
        .order_by(LabelProject.created_at, LabelProject.id)
    ).all()
    return LabelProjectListResponse(items=list(projects), total=len(projects))


@router.post(
    "/label-projects/{project_id}/sync-samples",
    response_model=LabelProjectTaskResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_sync_samples_task(
    project_id: str,
    session: Session = Depends(get_label_project_session),
    producer: Any = Depends(get_label_sync_stream_producer),
) -> Task:
    return await _create_label_project_task(
        session=session,
        producer=producer,
        project_id=project_id,
        task_type=TaskType.SYNC_LABEL_STUDIO_DATA,
    )


@router.post(
    "/label-projects/{project_id}/import-annotations",
    response_model=LabelProjectTaskResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_import_annotations_task(
    project_id: str,
    session: Session = Depends(get_label_project_session),
    producer: Any = Depends(get_label_sync_stream_producer),
) -> Task:
    return await _create_label_project_task(
        session=session,
        producer=producer,
        project_id=project_id,
        task_type=TaskType.IMPORT_LABEL_STUDIO_ANNOTATION,
    )


async def _create_label_project_task(
    session: Session,
    producer: Any,
    project_id: str,
    task_type: TaskType,
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
    return task


def _find_dataset_label_project(session: Session, dataset_id: str) -> LabelProject | None:
    return session.scalars(
        select(LabelProject)
        .where(LabelProject.dataset_id == dataset_id, LabelProject.provider == LABEL_STUDIO_PROVIDER)
        .order_by(LabelProject.created_at, LabelProject.id)
        .limit(1)
    ).first()


def _label_project_or_404(session: Session, project_id: str) -> LabelProject:
    project = session.get(LabelProject, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Label project not found")
    return project
