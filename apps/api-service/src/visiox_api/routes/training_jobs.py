from __future__ import annotations

import inspect
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from visiox_common.tasks import TaskCommand, TaskStatus, TaskType
from visiox_db.models import Task, TrainedModel, TrainingJob, TrainingPipeline
from visiox_db.session import get_session
from visiox_messaging.streams import RedisStreamProducer
from visiox_storage.client import ObjectStorageClient
from visiox_yolo26.training.params import (
    TrainingParamsError,
    merge_training_params,
    validate_training_environment,
)
from visiox_yolo26.training.prechecks import TrainingPrecheckError, validate_training_resources


router = APIRouter(tags=["training-jobs"])


class TrainingJobCreateRequest(PydanticBaseModel):
    params: dict[str, Any] = Field(default_factory=dict)
    environment: dict[str, Any] = Field(default_factory=dict)


class TrainingJobResponse(PydanticBaseModel):
    id: str
    pipeline_id: str
    task_id: str | None
    trained_model_id: str | None
    status: str
    params: dict[str, Any]
    environment: dict[str, Any] = Field(default_factory=dict)
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


class TrainingArtifactResponse(PydanticBaseModel):
    name: str
    kind: str
    size_bytes: int
    download_url: str


class TrainingArtifactListResponse(PydanticBaseModel):
    items: list[TrainingArtifactResponse]


def get_training_job_session() -> Generator[Session]:
    yield from get_session()


def get_training_stream_producer(request: Request) -> RedisStreamProducer:
    return RedisStreamProducer(request.app.state.redis)


def get_training_object_storage_client(request: Request) -> ObjectStorageClient:
    storage = getattr(request.app.state, "object_storage", None)
    if storage is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Object storage is not configured")
    return storage


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
    try:
        resources = validate_training_resources(
            session,
            task=pipeline.task,
            scale=pipeline.scale,
            base_model_id=str(pipeline.base_model_id),
            dataset_id=str(pipeline.dataset_id),
        )
    except TrainingPrecheckError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
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
        "dataset_id": resources.dataset.id,
        "base_model_id": resources.base_model.id,
        "params": params,
        "environment": environment,
    }
    pipeline.status = "running"
    session.add(pipeline)
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
        pipeline.status = "failed"
        task.status = TaskStatus.FAILED.value
        task.error_code = "ENQUEUE_FAILED"
        task.error_message = str(exc)
        task.finished_at = now
        task.retryable = True
        session.add_all([job, pipeline, task])
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


@router.get("/training-jobs/{training_job_id}/log")
def get_training_job_log(
    training_job_id: str,
    session: Session = Depends(get_training_job_session),
    storage: ObjectStorageClient = Depends(get_training_object_storage_client),
) -> FileResponse:
    job = session.get(TrainingJob, training_job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Training job not found")
    if not job.log_uri:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Training log not found")
    bucket, object_name = _parse_storage_uri(job.log_uri)
    with NamedTemporaryFile(delete=False, suffix=f"-{training_job_id}.log") as temp_file:
        temp_path = Path(temp_file.name)
    storage.get_file(bucket, object_name, temp_path)
    return FileResponse(
        temp_path,
        media_type="text/plain; charset=utf-8",
        filename=f"{training_job_id}.log",
        background=BackgroundTask(temp_path.unlink, missing_ok=True),
    )


@router.get("/training-jobs/{training_job_id}/visualizations/{name}")
def get_training_job_visualization(
    training_job_id: str,
    name: str,
    session: Session = Depends(get_training_job_session),
    storage: ObjectStorageClient = Depends(get_training_object_storage_client),
) -> FileResponse:
    job = session.get(TrainingJob, training_job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Training job not found")
    visualizations = job.metrics.get("visualizations") if isinstance(job.metrics, dict) else None
    if not isinstance(visualizations, dict):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Training visualization not found")
    uri = visualizations.get(name)
    if not isinstance(uri, str):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Training visualization not found")
    bucket, object_name = _parse_storage_uri(uri)
    suffix = Path(name).suffix or ".png"
    with NamedTemporaryFile(delete=False, suffix=f"-{_safe_name(name) or 'visualization'}") as temp_file:
        temp_path = Path(temp_file.name)
    storage.get_file(bucket, object_name, temp_path)
    return FileResponse(temp_path, media_type=_media_type(suffix), background=BackgroundTask(temp_path.unlink, missing_ok=True))


@router.get("/training-jobs/{training_job_id}/artifacts", response_model=TrainingArtifactListResponse)
def list_training_job_artifacts(
    training_job_id: str,
    session: Session = Depends(get_training_job_session),
    storage: ObjectStorageClient = Depends(get_training_object_storage_client),
) -> TrainingArtifactListResponse:
    job = session.get(TrainingJob, training_job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Training job not found")
    artifact_uris = _training_artifact_uris(job, session)
    items: list[TrainingArtifactResponse] = []
    for kind in ("weight", "visualization"):
        for name, uri in artifact_uris[kind].items():
            bucket, object_name = _parse_storage_uri(uri)
            items.append(
                TrainingArtifactResponse(
                    name=name,
                    kind=kind,
                    size_bytes=storage.object_size(bucket, object_name),
                    download_url=f"/training-jobs/{job.id}/artifacts/{kind}/{name}",
                )
            )
    return TrainingArtifactListResponse(items=items)


@router.get("/training-jobs/{training_job_id}/artifacts/{kind}/{name}")
def download_training_job_artifact(
    training_job_id: str,
    kind: str,
    name: str,
    session: Session = Depends(get_training_job_session),
    storage: ObjectStorageClient = Depends(get_training_object_storage_client),
) -> FileResponse:
    job = session.get(TrainingJob, training_job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Training job not found")
    if kind not in {"weight", "visualization"}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Training artifact not found")
    uri = _training_artifact_uris(job, session)[kind].get(name)
    if not uri:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Training artifact not found")
    bucket, object_name = _parse_storage_uri(uri)
    safe_name = _safe_name(name)
    with NamedTemporaryFile(delete=False, suffix=f"-{safe_name or 'artifact'}") as temp_file:
        temp_path = Path(temp_file.name)
    storage.get_file(bucket, object_name, temp_path)
    return FileResponse(
        temp_path,
        media_type=_media_type(Path(name).suffix),
        filename=safe_name or "artifact",
        background=BackgroundTask(temp_path.unlink, missing_ok=True),
    )


def _training_artifact_uris(job: TrainingJob, session: Session) -> dict[str, dict[str, str]]:
    metrics = job.metrics if isinstance(job.metrics, dict) else {}
    weights = _string_map(metrics.get("weights"))
    visualizations = _string_map(metrics.get("visualizations"))
    if not weights:
        models = session.scalars(
            select(TrainedModel)
            .where(TrainedModel.pipeline_id == job.pipeline_id, TrainedModel.status == "ready")
            .order_by(TrainedModel.created_at.desc(), TrainedModel.id.desc())
        ).all()
        for model in models:
            _bucket, object_name = _parse_storage_uri(model.artifact_uri)
            name = _safe_name(Path(object_name).name)
            if name and name not in weights:
                weights[name] = model.artifact_uri
    return {
        "weight": dict(sorted(weights.items(), key=lambda item: _artifact_sort_key(item[0]))),
        "visualization": dict(sorted(visualizations.items())),
    }


def _string_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        _safe_name(str(name)): uri
        for name, uri in value.items()
        if _safe_name(str(name)) and isinstance(uri, str) and uri
    }


def _artifact_sort_key(name: str) -> tuple[int, str]:
    if name == "best.pt":
        return 0, name
    if name == "last.pt":
        return 1, name
    return 2, name


def _parse_storage_uri(uri: str) -> tuple[str, str]:
    marker = "://"
    if marker not in uri:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Training artifact is not available")
    remainder = uri.split(marker, 1)[1]
    bucket, separator, object_name = remainder.partition("/")
    if not separator or not bucket or not object_name:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Training artifact is not available")
    return bucket, object_name


def _safe_name(name: str) -> str:
    return "".join(character if character.isalnum() or character in {".", "-", "_"} else "_" for character in name)


def _media_type(suffix: str) -> str:
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
    }.get(suffix.lower(), "application/octet-stream")
