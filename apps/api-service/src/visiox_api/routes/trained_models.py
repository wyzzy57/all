from collections.abc import Generator
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from visiox_db.models import TrainedModel
from visiox_db.session import get_session


router = APIRouter(prefix="/trained-models", tags=["trained-models"])


class TrainedModelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    pipeline_id: str | None
    training_job_id: str | None
    name: str
    version: str
    task: str
    artifact_uri: str
    metrics: dict[str, Any]
    status: str
    created_at: datetime
    updated_at: datetime


class TrainedModelListResponse(BaseModel):
    items: list[TrainedModelResponse]
    total: int
    limit: int
    offset: int


def get_trained_model_session() -> Generator[Session]:
    yield from get_session()


@router.get("", response_model=TrainedModelListResponse)
def list_trained_models(
    task: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_trained_model_session),
) -> TrainedModelListResponse:
    filters = []
    if task is not None:
        filters.append(TrainedModel.task == task)
    if status_filter is not None:
        filters.append(TrainedModel.status == status_filter)

    total_query = select(func.count()).select_from(TrainedModel)
    list_query = select(TrainedModel).order_by(TrainedModel.created_at.desc(), TrainedModel.id.desc())
    if filters:
        total_query = total_query.where(*filters)
        list_query = list_query.where(*filters)

    total = session.scalar(total_query) or 0
    items = session.scalars(list_query.limit(limit).offset(offset)).all()
    return TrainedModelListResponse(items=list(items), total=total, limit=limit, offset=offset)
