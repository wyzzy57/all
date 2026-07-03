import inspect
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from visiox_common.tasks import TaskCommand, TaskProgressEvent, TaskStatus, TaskType
from visiox_db.models import Task
from visiox_db.session import get_session
from visiox_messaging.streams import RedisStreamProducer


router = APIRouter(prefix="/tasks", tags=["tasks"])
TERMINAL_STATUSES = {TaskStatus.SUCCESS, TaskStatus.FAILED, TaskStatus.CANCELED}
CANCELABLE_STATUSES = {TaskStatus.PENDING, TaskStatus.QUEUED}


class CreateTaskRequest(BaseModel):
    task_type: TaskType
    resource_refs: dict[str, str] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)


class TaskResponse(BaseModel):
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


class TaskListResponse(BaseModel):
    items: list[TaskResponse]
    total: int
    limit: int
    offset: int


def get_task_session() -> Generator[Session]:
    yield from get_session()


def get_stream_producer(request: Request) -> RedisStreamProducer:
    return RedisStreamProducer(request.app.state.redis)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _task_or_404(session: Session, task_id: str) -> Task:
    task = session.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task


async def _enqueue(producer: Any, command: TaskCommand) -> str:
    result = producer.enqueue(command)
    if inspect.isawaitable(result):
        return await result
    return str(result)


@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    request: CreateTaskRequest,
    session: Session = Depends(get_task_session),
    producer: Any = Depends(get_stream_producer),
) -> Task:
    task = Task(
        task_type=request.task_type.value,
        status=TaskStatus.QUEUED.value,
        progress=0,
        payload=request.payload,
    )
    session.add(task)
    session.commit()
    session.refresh(task)

    command = TaskCommand(
        task_id=task.id,
        task_type=request.task_type,
        resource_refs=request.resource_refs,
        payload=request.payload,
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

    return task


@router.get("", response_model=TaskListResponse)
def list_tasks(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_task_session),
) -> TaskListResponse:
    total = session.scalar(select(func.count()).select_from(Task)) or 0
    tasks = session.scalars(
        select(Task).order_by(Task.created_at, Task.id).limit(limit).offset(offset)
    ).all()
    return TaskListResponse(items=list(tasks), total=total, limit=limit, offset=offset)


@router.get("/{task_id}", response_model=TaskResponse)
def get_task(task_id: str, session: Session = Depends(get_task_session)) -> Task:
    return _task_or_404(session, task_id)


@router.post("/{task_id}/cancel", response_model=TaskResponse)
def cancel_task(task_id: str, session: Session = Depends(get_task_session)) -> Task:
    task = _task_or_404(session, task_id)
    if TaskStatus(task.status) not in CANCELABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only PENDING or QUEUED tasks can be canceled",
        )

    task.status = TaskStatus.CANCELED.value
    task.finished_at = _utc_now()
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


def update_task_progress(
    session: Session,
    task_id: str,
    event: TaskProgressEvent,
    progress_broker: Any | None = None,
) -> Task:
    task = _task_or_404(session, task_id)
    if task.status == TaskStatus.CANCELED.value and event.status != TaskStatus.CANCELED:
        return task

    task.status = event.status.value
    task.progress = event.progress
    task.stage = event.stage
    task.error_code = event.error_code
    task.error_message = event.error_message
    if event.status == TaskStatus.RUNNING and task.started_at is None:
        task.started_at = _utc_now()
    if event.status in TERMINAL_STATUSES:
        task.finished_at = _utc_now()

    session.add(task)
    session.commit()
    session.refresh(task)

    if progress_broker is not None:
        result = progress_broker.publish(event)
        if inspect.isawaitable(result):
            raise RuntimeError("Async progress brokers must be published by the caller")

    return task
