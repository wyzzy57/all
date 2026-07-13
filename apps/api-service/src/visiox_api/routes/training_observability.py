from __future__ import annotations

from collections.abc import Generator
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from visiox_api.services.training_observability import TrainingObservabilityService
from visiox_common.settings import get_settings
from visiox_db.models import Task, TrainingJob, TrainingPipeline
from visiox_db.session import get_session


router = APIRouter(prefix="/training-jobs/{training_job_id}/observability", tags=["training-observability"])


class SourceAvailabilityResponse(BaseModel):
    available: bool
    reason: str | None = None


AvailabilityResponse = dict[str, SourceAvailabilityResponse]


class ScalarPointResponse(BaseModel):
    step: float
    value: float
    timestamp: float


class ScalarsResponse(BaseModel):
    series: dict[str, list[ScalarPointResponse]]
    availability: AvailabilityResponse


class ResourcesResponse(BaseModel):
    series: dict[str, list[ScalarPointResponse]]
    availability: AvailabilityResponse


class GraphNodeResponse(BaseModel):
    id: str
    label: str
    op: str
    attributes: dict[str, Any]


class GraphEdgeResponse(BaseModel):
    source: str
    target: str


class GraphResponse(BaseModel):
    nodes: list[GraphNodeResponse]
    edges: list[GraphEdgeResponse]
    availability: AvailabilityResponse


class HistogramBucketResponse(BaseModel):
    lower: float
    upper: float
    count: float


class HistogramResponse(BaseModel):
    kind: Literal["weight", "gradient"]
    tag: str
    step: int
    buckets: list[HistogramBucketResponse]
    availability: AvailabilityResponse


class SummaryResponse(BaseModel):
    job_id: str
    pipeline_id: str
    pipeline_name: str
    status: str
    progress: dict[str, Any] = Field(default_factory=dict)
    timing: dict[str, Any] = Field(default_factory=dict)
    environment: dict[str, Any] = Field(default_factory=dict)
    latest_metrics: dict[str, float] = Field(default_factory=dict)
    available_scalar_keys: list[str] = Field(default_factory=list)
    available_histograms: dict[Literal["weight", "gradient"], list[str]] = Field(
        default_factory=lambda: {"weight": [], "gradient": []}
    )
    availability: AvailabilityResponse


def get_training_observability_session() -> Generator[Session]:
    yield from get_session()


def get_training_observability_service() -> TrainingObservabilityService:
    return TrainingObservabilityService(get_settings())


def _get_training_job(session: Session, training_job_id: str) -> TrainingJob:
    job = session.get(TrainingJob, training_job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Training job not found")
    return job


def _parse_scalar_keys(values: list[str]) -> list[str]:
    return [key for value in values for item in value.split(",") if (key := item.strip())]


def _job_environment(task: Task | None) -> dict[str, Any]:
    if task is None or not isinstance(task.payload, dict):
        return {}
    environment = task.payload.get("environment")
    return environment if isinstance(environment, dict) else {}


def _latest_metrics(job: TrainingJob) -> dict[str, float]:
    return {
        name: float(value)
        for name, value in job.metrics.items()
        if name != "observability" and isinstance(value, int | float)
    }


def _default_timing(job: TrainingJob) -> dict[str, Any]:
    timing: dict[str, Any] = {}
    if job.started_at is not None:
        timing["started_at"] = _serialize_datetime(job.started_at)
    if job.finished_at is not None:
        timing["finished_at"] = _serialize_datetime(job.finished_at)
    return timing


def _serialize_datetime(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


@router.get("/summary", response_model=SummaryResponse)
def get_training_observability_summary(
    training_job_id: str,
    session: Session = Depends(get_training_observability_session),
    service: TrainingObservabilityService = Depends(get_training_observability_service),
) -> SummaryResponse:
    job = _get_training_job(session, training_job_id)
    pipeline = session.get(TrainingPipeline, job.pipeline_id)
    if pipeline is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline not found")
    task = session.get(Task, job.task_id) if job.task_id else None
    summary = service.get_summary(job, pipeline, task)
    pipeline_data = summary.get("pipeline") if isinstance(summary.get("pipeline"), dict) else {}
    return SummaryResponse(
        job_id=str(summary.get("job_id", job.id)),
        pipeline_id=str(pipeline_data.get("id", job.pipeline_id)),
        pipeline_name=str(pipeline_data.get("name", getattr(pipeline, "name", job.pipeline_id))),
        status=str(summary.get("status", job.status)),
        progress=summary.get("progress") if isinstance(summary.get("progress"), dict) else {},
        timing=summary.get("timing") if isinstance(summary.get("timing"), dict) else _default_timing(job),
        environment=summary.get("environment") if isinstance(summary.get("environment"), dict) else _job_environment(task),
        latest_metrics=summary.get("latest_metrics") if isinstance(summary.get("latest_metrics"), dict) else _latest_metrics(job),
        available_scalar_keys=summary.get("available_scalar_keys")
        if isinstance(summary.get("available_scalar_keys"), list)
        else [],
        available_histograms=summary.get("available_histograms")
        if isinstance(summary.get("available_histograms"), dict)
        else {"weight": [], "gradient": []},
        availability=summary["availability"],
    )


@router.get("/scalars", response_model=ScalarsResponse)
def get_training_observability_scalars(
    training_job_id: str,
    keys: list[str] = Query(min_length=1),
    start_step: int | None = Query(default=None, ge=0),
    end_step: int | None = Query(default=None, ge=0),
    max_points: int | None = Query(default=None, ge=10, le=10_000),
    session: Session = Depends(get_training_observability_session),
    service: TrainingObservabilityService = Depends(get_training_observability_service),
) -> ScalarsResponse:
    job = _get_training_job(session, training_job_id)
    scalar_keys = _parse_scalar_keys(keys)
    if not scalar_keys:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="At least one scalar key is required")
    return ScalarsResponse.model_validate(service.get_scalars(job, scalar_keys, start_step, end_step, max_points))


@router.get("/resources", response_model=ResourcesResponse)
def get_training_observability_resources(
    training_job_id: str,
    start_step: int | None = Query(default=None, ge=0),
    end_step: int | None = Query(default=None, ge=0),
    max_points: int | None = Query(default=None, ge=10, le=10_000),
    session: Session = Depends(get_training_observability_session),
    service: TrainingObservabilityService = Depends(get_training_observability_service),
) -> ResourcesResponse:
    job = _get_training_job(session, training_job_id)
    return ResourcesResponse.model_validate(service.get_resources(job, start_step, end_step, max_points))


@router.get("/graph", response_model=GraphResponse)
def get_training_observability_graph(
    training_job_id: str,
    session: Session = Depends(get_training_observability_session),
    service: TrainingObservabilityService = Depends(get_training_observability_service),
) -> GraphResponse:
    job = _get_training_job(session, training_job_id)
    return GraphResponse.model_validate(service.get_graph(job))


@router.get("/histograms", response_model=HistogramResponse)
def get_training_observability_histogram(
    training_job_id: str,
    kind: Literal["weight", "gradient"],
    tag: str = Query(min_length=1),
    step: int = Query(ge=0),
    session: Session = Depends(get_training_observability_session),
    service: TrainingObservabilityService = Depends(get_training_observability_service),
) -> HistogramResponse:
    job = _get_training_job(session, training_job_id)
    return HistogramResponse.model_validate(service.get_histogram(job, kind, tag, step))
