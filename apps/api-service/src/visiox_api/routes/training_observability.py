from __future__ import annotations

from collections.abc import Generator
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from visiox_api.services.training_observability import TrainingObservabilityService
from visiox_api.dependencies.auth import get_current_user
from visiox_api.dependencies.authorization import require_resource_permission
from visiox_db.models import Task, TrainingJob, TrainingPipeline, User
from visiox_db.models.identity import PERMISSION_VIEW
from visiox_storage.client import ObjectStorageClient
from visiox_db.session import get_session


router = APIRouter(prefix="/training-jobs/{training_job_id}/observability", tags=["training-observability"])


class SourceAvailabilityResponse(BaseModel):
    available: bool
    reason: str | None = None


AvailabilityResponse = dict[str, SourceAvailabilityResponse]


class ScalarPointResponse(BaseModel):
    canonical_name: str = ""
    raw_name: str = ""
    unit: str = "scalar"
    split: str | None = None
    step: float
    epoch: float | None = None
    value: float
    timestamp: float
    source: str = "unknown"


class ScalarsResponse(BaseModel):
    series: dict[str, list[ScalarPointResponse]]
    availability: AvailabilityResponse


class ResourcesResponse(BaseModel):
    series: dict[str, list[ScalarPointResponse]]
    availability: AvailabilityResponse


class SecondaryActionResponse(BaseModel):
    source: str
    url: str


class SummaryResponse(BaseModel):
    job_id: str
    engine: str
    pipeline_id: str
    pipeline_name: str
    status: str
    progress: dict[str, Any] = Field(default_factory=dict)
    timing: dict[str, Any] = Field(default_factory=dict)
    environment: dict[str, Any] = Field(default_factory=dict)
    latest_metrics: dict[str, float] = Field(default_factory=dict)
    available_scalar_keys: list[str] = Field(default_factory=list)
    secondary_actions: list[SecondaryActionResponse] = Field(default_factory=list)
    availability: AvailabilityResponse


class AnalysisFindingResponse(BaseModel):
    code: str
    severity: str
    title: str
    message: str
    metric_names: list[str]
    step_range: list[float] | None
    observed_values: dict[str, float | None]


class AnalysisResponse(BaseModel):
    findings: list[AnalysisFindingResponse]
    availability: AvailabilityResponse


class ObservabilityArtifactResponse(BaseModel):
    path: str
    size_bytes: int
    sha256: str
    download_url: str


class ObservabilityArtifactsResponse(BaseModel):
    items: list[ObservabilityArtifactResponse]
    availability: AvailabilityResponse


def get_training_observability_session() -> Generator[Session]:
    yield from get_session()


def get_training_observability_service(request: Request) -> TrainingObservabilityService:
    return request.app.state.training_observability_service


def get_training_observability_storage(request: Request) -> ObjectStorageClient:
    storage = getattr(request.app.state, "object_storage", None)
    if storage is None:
        raise RuntimeError("Object storage is not configured")
    return storage


def _parse_minio_uri(uri: object) -> tuple[str, str] | None:
    if not isinstance(uri, str) or not uri.startswith("minio://"):
        return None
    location = uri.removeprefix("minio://")
    if "/" not in location:
        return None
    bucket, object_name = location.split("/", 1)
    if not bucket or not object_name:
        return None
    return bucket, object_name


def _get_training_job(session: Session, actor: User, training_job_id: str) -> TrainingJob:
    job = session.get(TrainingJob, training_job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Training job not found")
    require_resource_permission(session, actor, "training_job", job.id, PERMISSION_VIEW)
    return job


def _observability_adapter(
    service: TrainingObservabilityService, pipeline: TrainingPipeline
) -> Any:
    try:
        return service.for_pipeline(pipeline)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc


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
    actor: User = Depends(get_current_user),
    service: TrainingObservabilityService = Depends(get_training_observability_service),
) -> SummaryResponse:
    job = _get_training_job(session, actor, training_job_id)
    pipeline = session.get(TrainingPipeline, job.pipeline_id)
    if pipeline is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline not found")
    task = session.get(Task, job.task_id) if job.task_id else None
    summary = _observability_adapter(service, pipeline).summary(job, pipeline, task)
    pipeline_data = summary.get("pipeline") if isinstance(summary.get("pipeline"), dict) else {}
    summary_timing = summary.get("timing") if isinstance(summary.get("timing"), dict) else {}
    timing = {**_default_timing(job), **summary_timing}
    summary_environment = summary.get("environment") if isinstance(summary.get("environment"), dict) else {}
    summary_metrics = summary.get("latest_metrics") if isinstance(summary.get("latest_metrics"), dict) else {}
    return SummaryResponse(
        job_id=str(summary.get("job_id", job.id)),
        engine=str(summary.get("engine", pipeline.engine)),
        pipeline_id=str(pipeline_data.get("id", job.pipeline_id)),
        pipeline_name=str(pipeline_data.get("name", getattr(pipeline, "name", job.pipeline_id))),
        status=str(summary.get("status", job.status)),
        progress=summary.get("progress") if isinstance(summary.get("progress"), dict) else {},
        timing=timing,
        environment=summary_environment or _job_environment(task),
        latest_metrics=summary_metrics or _latest_metrics(job),
        available_scalar_keys=summary.get("available_scalar_keys")
        if isinstance(summary.get("available_scalar_keys"), list)
        else [],
        secondary_actions=summary.get("secondary_actions")
        if isinstance(summary.get("secondary_actions"), list)
        else [],
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
    actor: User = Depends(get_current_user),
    service: TrainingObservabilityService = Depends(get_training_observability_service),
) -> ScalarsResponse:
    job = _get_training_job(session, actor, training_job_id)
    pipeline = session.get(TrainingPipeline, job.pipeline_id)
    if pipeline is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline not found")
    scalar_keys = _parse_scalar_keys(keys)
    if not scalar_keys:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="At least one scalar key is required")
    return ScalarsResponse.model_validate(
        _observability_adapter(service, pipeline).scalars(job, scalar_keys, start_step, end_step, max_points)
    )


@router.get("/resources", response_model=ResourcesResponse)
def get_training_observability_resources(
    training_job_id: str,
    start_step: int | None = Query(default=None, ge=0),
    end_step: int | None = Query(default=None, ge=0),
    max_points: int | None = Query(default=None, ge=10, le=10_000),
    session: Session = Depends(get_training_observability_session),
    actor: User = Depends(get_current_user),
    service: TrainingObservabilityService = Depends(get_training_observability_service),
) -> ResourcesResponse:
    job = _get_training_job(session, actor, training_job_id)
    pipeline = session.get(TrainingPipeline, job.pipeline_id)
    if pipeline is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline not found")
    return ResourcesResponse.model_validate(
        _observability_adapter(service, pipeline).resources(job, start_step, end_step, max_points)
    )


@router.get("/analysis", response_model=AnalysisResponse)
def get_training_observability_analysis(
    training_job_id: str,
    session: Session = Depends(get_training_observability_session),
    actor: User = Depends(get_current_user),
    service: TrainingObservabilityService = Depends(get_training_observability_service),
) -> AnalysisResponse:
    job = _get_training_job(session, actor, training_job_id)
    pipeline = session.get(TrainingPipeline, job.pipeline_id)
    if pipeline is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline not found")
    return AnalysisResponse.model_validate(_observability_adapter(service, pipeline).analysis(job))


@router.get("/artifacts", response_model=ObservabilityArtifactsResponse)
def get_training_observability_artifacts(
    training_job_id: str,
    session: Session = Depends(get_training_observability_session),
    actor: User = Depends(get_current_user),
    service: TrainingObservabilityService = Depends(get_training_observability_service),
) -> ObservabilityArtifactsResponse:
    job = _get_training_job(session, actor, training_job_id)
    pipeline = session.get(TrainingPipeline, job.pipeline_id)
    if pipeline is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline not found")
    return ObservabilityArtifactsResponse.model_validate(_observability_adapter(service, pipeline).artifacts(job))


@router.get("/artifacts/{artifact_path:path}")
def download_training_observability_artifact(
    training_job_id: str,
    artifact_path: str,
    session: Session = Depends(get_training_observability_session),
    actor: User = Depends(get_current_user),
    service: TrainingObservabilityService = Depends(get_training_observability_service),
    storage: ObjectStorageClient = Depends(get_training_observability_storage),
) -> FileResponse:
    job = _get_training_job(session, actor, training_job_id)
    pipeline = session.get(TrainingPipeline, job.pipeline_id)
    if pipeline is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline not found")
    listed = _observability_adapter(service, pipeline).artifacts(job)
    if artifact_path not in {item.get("path") for item in listed.get("items", [])}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Training artifact not found")
    name = Path(artifact_path).name
    metrics = job.metrics if isinstance(job.metrics, dict) else {}
    artifact_uris = metrics.get("artifacts") if isinstance(metrics.get("artifacts"), dict) else {}
    uri = metrics.get("adapter") if name == "adapter_model.safetensors" else artifact_uris.get(name)
    location = _parse_minio_uri(uri)
    if location is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Training artifact not found")
    bucket, object_name = location
    with NamedTemporaryFile(delete=False, suffix=f"-{name}") as temporary:
        destination = Path(temporary.name)
    storage.get_file(bucket, object_name, destination)
    return FileResponse(
        destination,
        filename=name,
        media_type="application/octet-stream",
        background=BackgroundTask(destination.unlink, missing_ok=True),
    )
