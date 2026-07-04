from __future__ import annotations

from collections.abc import Generator
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from visiox_db.models import TrainingPipeline
from visiox_db.session import get_session
from visiox_yolo26.training.params import (
    TrainingParamsError,
    validate_training_environment,
    validate_training_params,
)
from visiox_yolo26.training.prechecks import TrainingPrecheckError, validate_training_resources


router = APIRouter(prefix="/pipelines", tags=["pipelines"])


class PipelineCreateRequest(PydanticBaseModel):
    name: str
    task: str
    scale: str
    base_model_id: str
    dataset_id: str
    params_template: dict[str, Any] = Field(default_factory=dict)
    default_environment: dict[str, Any] = Field(default_factory=dict)


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


@router.post("", response_model=PipelineResponse)
def create_pipeline(
    request: PipelineCreateRequest,
    response: Response,
    session: Session = Depends(get_pipeline_session),
) -> TrainingPipeline:
    try:
        validate_training_resources(
            session,
            task=request.task,
            scale=request.scale,
            base_model_id=request.base_model_id,
            dataset_id=request.dataset_id,
        )
    except TrainingPrecheckError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
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
