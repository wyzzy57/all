from __future__ import annotations

import inspect
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, Request, status
from pydantic import BaseModel as PydanticBaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from visiox_common.tasks import TaskCommand, TaskStatus, TaskType
from visiox_db.models import BaseModel, Dataset, Task, TrainingJob, TrainingPipeline
from visiox_db.session import get_session
from visiox_messaging.streams import RedisStreamProducer
from visiox_yolo26.training.params import (
    TrainingParamsError,
    merge_training_params,
    validate_training_environment,
)


router = APIRouter(tags=["training-jobs"])


class TrainingJobCreateRequest(PydanticBaseModel):
    params: dict[str, Any] = {}
    environment: dict[str, Any] = {}


class TrainingJobResponse(PydanticBaseModel):
    id: str
    pipeline_id: str
    task_id: str | None
    trained_model_id: str | None
    status: str
    params: dict[str, Any]
    environment: dict[str, Any] = {}
    metrics: dict[str, Any]
    log_uri: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class TrainingJobListResponse(PydanticBaseModel):
    items: list[TrainingJobResponse]
    total: int
    limit: int
    offset: int


def get_training_job_session() -> Generator[Session]:
    yield from get_session()


def get_training_stream_producer(request: Request) -> RedisStreamProducer:
    return RedisStreamProducer(request.app.state.redis)


async def _enqueue(producer: Any, command: TaskCommand) -> str:
    result = producer.enqueue(command)
    if inspect.isawaitable(result):
        return await result
    return str(result)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _job_response(job: TrainingJob, environment: dict[str, Any] | None = None, task: Task | None = None) -> TrainingJobResponse:
    if environment is None and task is not None:
        payload = task.payload or {}
        env_payload = payload.get("environment")
        environment = env_payload if isinstance(env_payload, dict) else {}
    return TrainingJobResponse(
        id=job.id,
        pipeline_id=job.pipeline_id,
        task_id=job.task_id,
        trained_model_id=job.trained_model_id,
        status=job.status,
        params=job.params,
        environment=environment or {},
        metrics=job.metrics,
        log_uri=job.log_uri,
        started_at=job.started_at,
        finished_at=job.finished_at,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@router.post("/pipelines/{pipeline_id}/jobs", response_model=TrainingJobResponse)
async def create_training_job(
    pipeline_id: str,
    request: TrainingJobCreateRequest,
    response: Response,
    session: Session = Depends(get_training_job_session),
    producer: Any = Depends(get_training_stream_producer),
) -> TrainingJobResponse:
    pipeline = session.get(TrainingPipeline, pipeline_id)
    if pipeline is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline not found")
    if pipeline.status != "ready":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Pipeline is not ready: {pipeline.status}")
    base_model = session.get(BaseModel, pipeline.base_model_id)
    dataset = session.get(Dataset, pipeline.dataset_id)
    if base_model is None or dataset is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Pipeline references missing resources")
    try:
        params = merge_training_params(pipeline.params_template, request.params)
        environment = {**validate_training_environment(pipeline.default_environment), **validate_training_environment(request.environment)}
    except TrainingParamsError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    job = TrainingJob(pipeline_id=pipeline.id, status="queued", params=params)
    task = Task(
        task_type=TaskType.TRAIN_MODEL.value,
        status=TaskStatus.QUEUED.value,
        progress=0,
        resource_type="training_job",
        payload={},
    )
    session.add_all([job, task])
    session.flush()
    task.resource_id = job.id
    job.task_id = task.id
    task.payload = {
        "pipeline_id": pipeline.id,
        "training_job_id": job.id,
        "dataset_id": dataset.id,
        "base_model_id": base_model.id,
        "params": params,
        "environment": environment,
    }
    session.commit()
    session.refresh(job)
    session.refresh(task)
    response.status_code = status.HTTP_201_CREATED

    command = TaskCommand(
        task_id=task.id,
        task_type=TaskType.TRAIN_MODEL,
        resource_refs={"training_job_id": job.id},
        payload=task.payload,
    )
    try:
        await _enqueue(producer, command)
    except Exception as exc:
        now = _utc_now()
        job.status = "failed"
        job.finished_at = now
        task.status = TaskStatus.FAILED.value
        task.error_code = "ENQUEUE_FAILED"
        task.error_message = str(exc)
        task.finished_at = now
        task.retryable = True
        session.add_all([job, task])
        session.commit()
        session.refresh(job)
        session.refresh(task)

    return _job_response(job, environment, task)


@router.get("/training-jobs", response_model=TrainingJobListResponse)
def list_training_jobs(
    pipeline_id: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_training_job_session),
) -> TrainingJobListResponse:
    filters = []
    if pipeline_id is not None:
        filters.append(TrainingJob.pipeline_id == pipeline_id)
    if status_filter is not None:
        filters.append(TrainingJob.status == status_filter)
    total_query = select(func.count()).select_from(TrainingJob)
    list_query = select(TrainingJob).order_by(TrainingJob.created_at, TrainingJob.id)
    if filters:
        total_query = total_query.where(*filters)
        list_query = list_query.where(*filters)
    total = session.scalar(total_query) or 0
    jobs = session.scalars(list_query.limit(limit).offset(offset)).all()
    items = [_job_response(job, task=session.get(Task, job.task_id) if job.task_id else None) for job in jobs]
    return TrainingJobListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/training-jobs/{training_job_id}", response_model=TrainingJobResponse)
def get_training_job(training_job_id: str, session: Session = Depends(get_training_job_session)) -> TrainingJobResponse:
    job = session.get(TrainingJob, training_job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Training job not found")
    task = session.get(Task, job.task_id) if job.task_id else None
    return _job_response(job, task=task)
