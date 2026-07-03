import inspect
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from visiox_common.tasks import TaskCommand, TaskStatus, TaskType
from visiox_db.models import BaseModel, Task
from visiox_db.session import get_session
from visiox_messaging.streams import RedisStreamProducer


router = APIRouter(prefix="/base-models", tags=["base-models"])
ACTIVE_DOWNLOAD_STATUSES = {
    TaskStatus.PENDING.value,
    TaskStatus.QUEUED.value,
    TaskStatus.RUNNING.value,
}


class BaseModelResponse(PydanticBaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    family: str
    task: str
    scale: str
    filename: str
    source_path: str
    local_uri: str | None
    checksum: str | None
    size_bytes: int | None
    status: str
    model_source_id: str | None
    created_at: datetime
    updated_at: datetime


class BaseModelListResponse(PydanticBaseModel):
    items: list[BaseModelResponse]
    total: int
    limit: int
    offset: int


class BaseModelDownloadResponse(PydanticBaseModel):
    base_model_id: str
    status: str
    task_id: str | None


def get_base_model_session() -> Generator[Session]:
    yield from get_session()


def get_stream_producer(request: Request) -> RedisStreamProducer:
    return RedisStreamProducer(request.app.state.redis)


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def _enqueue(producer: Any, command: TaskCommand) -> str:
    result = producer.enqueue(command)
    if inspect.isawaitable(result):
        return await result
    return str(result)


def _base_model_or_404(session: Session, base_model_id: str) -> BaseModel:
    base_model = session.get(BaseModel, base_model_id)
    if base_model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Base model not found")
    return base_model


def ensure_base_model_ready(session: Session, base_model_id: str) -> BaseModel:
    base_model = _base_model_or_404(session, base_model_id)
    if base_model.status != "ready":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Base model is not ready: {base_model.status}",
        )
    return base_model


def _find_active_download_task(session: Session, base_model_id: str) -> Task | None:
    return session.scalars(
        select(Task)
        .where(
            Task.task_type == TaskType.DOWNLOAD_BASE_MODEL.value,
            Task.resource_type == "base_model",
            Task.resource_id == base_model_id,
            Task.status.in_(ACTIVE_DOWNLOAD_STATUSES),
        )
        .order_by(Task.created_at, Task.id)
        .limit(1)
    ).first()


@router.get("", response_model=BaseModelListResponse)
def list_base_models(
    task: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_base_model_session),
) -> BaseModelListResponse:
    filters = []
    if task is not None:
        filters.append(BaseModel.task == task)
    if status_filter is not None:
        filters.append(BaseModel.status == status_filter)

    total_query = select(func.count()).select_from(BaseModel)
    list_query = select(BaseModel).order_by(BaseModel.task, BaseModel.scale, BaseModel.id)
    if filters:
        total_query = total_query.where(*filters)
        list_query = list_query.where(*filters)

    total = session.scalar(total_query) or 0
    items = session.scalars(list_query.limit(limit).offset(offset)).all()
    return BaseModelListResponse(items=list(items), total=total, limit=limit, offset=offset)


@router.get("/{base_model_id}", response_model=BaseModelResponse)
def get_base_model(base_model_id: str, session: Session = Depends(get_base_model_session)) -> BaseModel:
    return _base_model_or_404(session, base_model_id)


@router.post("/{base_model_id}/download", response_model=BaseModelDownloadResponse)
async def create_base_model_download_task(
    base_model_id: str,
    response: Response,
    session: Session = Depends(get_base_model_session),
    producer: Any = Depends(get_stream_producer),
) -> BaseModelDownloadResponse:
    base_model = _base_model_or_404(session, base_model_id)
    if base_model.status == "ready":
        return BaseModelDownloadResponse(base_model_id=base_model.id, status=base_model.status, task_id=None)
    if base_model.status == "downloading":
        active_task = _find_active_download_task(session, base_model.id)
        return BaseModelDownloadResponse(
            base_model_id=base_model.id,
            status=base_model.status,
            task_id=active_task.id if active_task is not None else None,
        )

    task = Task(
        task_type=TaskType.DOWNLOAD_BASE_MODEL.value,
        status=TaskStatus.QUEUED.value,
        progress=0,
        resource_type="base_model",
        resource_id=base_model.id,
        payload={"base_model_id": base_model.id},
    )
    session.add(task)
    session.commit()
    session.refresh(task)

    command = TaskCommand(
        task_id=task.id,
        task_type=TaskType.DOWNLOAD_BASE_MODEL,
        resource_refs={"base_model_id": base_model.id},
        payload={"base_model_id": base_model.id},
    )
    try:
        await _enqueue(producer, command)
    except Exception as exc:
        task.status = TaskStatus.FAILED.value
        task.error_code = "ENQUEUE_FAILED"
        task.error_message = str(exc)
        task.finished_at = _utc_now()
        session.add(task)
        session.commit()
        session.refresh(task)
        return BaseModelDownloadResponse(base_model_id=base_model.id, status=base_model.status, task_id=task.id)

    base_model.status = "downloading"
    session.add(base_model)
    session.commit()
    session.refresh(base_model)

    response.status_code = status.HTTP_201_CREATED
    return BaseModelDownloadResponse(base_model_id=base_model.id, status=base_model.status, task_id=task.id)
