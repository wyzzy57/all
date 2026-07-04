from __future__ import annotations

from collections.abc import Generator
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from visiox_db.models import Annotation, BaseModel, Dataset, DatasetSample, TrainingPipeline
from visiox_db.session import get_session
from visiox_yolo26.tasks import YOLO26_SCALES, YOLO26_TASKS
from visiox_yolo26.training.params import (
    TrainingParamsError,
    validate_training_environment,
    validate_training_params,
)


router = APIRouter(prefix="/pipelines", tags=["pipelines"])


class PipelineCreateRequest(PydanticBaseModel):
    name: str
    task: str
    scale: str
    base_model_id: str
    dataset_id: str
    params_template: dict[str, Any] = {}
    default_environment: dict[str, Any] = {}


class PipelineResponse(PydanticBaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    task: str
    scale: str
    base_model_id: str | None
    dataset_id: str | None
    params_template: dict[str, Any]
    default_environment: dict[str, Any]
    status: str
    created_at: datetime
    updated_at: datetime


class PipelineListResponse(PydanticBaseModel):
    items: list[PipelineResponse]
    total: int
    limit: int
    offset: int


def get_pipeline_session() -> Generator[Session]:
    yield from get_session()


def _unprocessable(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=message)


def _validate_pipeline_prechecks(
    session: Session,
    task: str,
    scale: str,
    base_model_id: str,
    dataset_id: str,
) -> tuple[BaseModel, Dataset]:
    if task not in YOLO26_TASKS:
        raise _unprocessable(f"unsupported YOLO26 task: {task}")
    if scale not in YOLO26_SCALES:
        raise _unprocessable(f"unsupported YOLO26 scale: {scale}")

    base_model = session.get(BaseModel, base_model_id)
    if base_model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Base model not found")
    if base_model.status != "ready":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Base model is not ready: {base_model.status}")
    if base_model.task != task or base_model.scale != scale:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Base model task or scale does not match pipeline")

    dataset = session.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset not found")
    if dataset.task != task:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Dataset task does not match pipeline")
    if dataset.sample_count <= 0:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Dataset has no samples")
    if dataset.annotation_count <= 0:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Dataset has no annotations")
    sample_exists = session.scalar(select(func.count()).select_from(DatasetSample).where(DatasetSample.dataset_id == dataset.id)) or 0
    annotation_exists = (
        session.scalar(
            select(func.count())
            .select_from(Annotation)
            .join(DatasetSample, Annotation.dataset_sample_id == DatasetSample.id)
            .where(DatasetSample.dataset_id == dataset.id)
        )
        or 0
    )
    if sample_exists <= 0 or annotation_exists <= 0:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Dataset samples or annotations are missing")
    return base_model, dataset


@router.post("", response_model=PipelineResponse)
def create_pipeline(
    request: PipelineCreateRequest,
    response: Response,
    session: Session = Depends(get_pipeline_session),
) -> TrainingPipeline:
    _validate_pipeline_prechecks(session, request.task, request.scale, request.base_model_id, request.dataset_id)
    try:
        params_template = validate_training_params(request.params_template)
        default_environment = validate_training_environment(request.default_environment)
    except TrainingParamsError as exc:
        raise _unprocessable(str(exc)) from exc

    pipeline = TrainingPipeline(
        name=request.name,
        task=request.task,
        scale=request.scale,
        base_model_id=request.base_model_id,
        dataset_id=request.dataset_id,
        params_template=params_template,
        default_environment=default_environment,
        status="ready",
    )
    session.add(pipeline)
    session.commit()
    session.refresh(pipeline)
    response.status_code = status.HTTP_201_CREATED
    return pipeline


@router.get("", response_model=PipelineListResponse)
def list_pipelines(
    task: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_pipeline_session),
) -> PipelineListResponse:
    filters = []
    if task is not None:
        filters.append(TrainingPipeline.task == task)
    if status_filter is not None:
        filters.append(TrainingPipeline.status == status_filter)
    total_query = select(func.count()).select_from(TrainingPipeline)
    list_query = select(TrainingPipeline).order_by(TrainingPipeline.created_at, TrainingPipeline.id)
    if filters:
        total_query = total_query.where(*filters)
        list_query = list_query.where(*filters)
    total = session.scalar(total_query) or 0
    items = session.scalars(list_query.limit(limit).offset(offset)).all()
    return PipelineListResponse(items=list(items), total=total, limit=limit, offset=offset)


@router.get("/{pipeline_id}", response_model=PipelineResponse)
def get_pipeline(pipeline_id: str, session: Session = Depends(get_pipeline_session)) -> TrainingPipeline:
    pipeline = session.get(TrainingPipeline, pipeline_id)
    if pipeline is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline not found")
    return pipeline
