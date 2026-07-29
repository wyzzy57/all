from collections.abc import Generator
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from visiox_api.dependencies.auth import get_current_user
from visiox_api.dependencies.authorization import require_resource_permission
from visiox_api.dependencies.database import get_db_session
from visiox_api.services.authorization import authorized_resource_predicate
from visiox_db.models import TrainedModel
from visiox_db.models.identity import PERMISSION_EDIT, PERMISSION_VIEW, User


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
    organization_id: str | None
    owner_user_id: str | None
    visibility: str
    created_at: datetime
    updated_at: datetime


class TrainedModelListResponse(BaseModel):
    items: list[TrainedModelResponse]
    total: int
    limit: int
    offset: int


class TrainedModelUpdateRequest(BaseModel):
    deployment_name: str = Field(min_length=1, max_length=64, pattern=r"^[\w\u4e00-\u9fff-]+$")

    @field_validator("deployment_name")
    @classmethod
    def validate_deployment_name(cls, value: str) -> str:
        if value[0] in "-_" or value[-1] in "-_":
            raise ValueError("deployment_name cannot start or end with punctuation")
        return value


get_trained_model_session = get_db_session


@router.get("", response_model=TrainedModelListResponse)
def list_trained_models(
    task: str | None = None,
    pipeline_id: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_trained_model_session),
    actor: User = Depends(get_current_user),
) -> TrainedModelListResponse:
    filters = [
        authorized_resource_predicate(
            session, actor, TrainedModel, "trained_model", PERMISSION_VIEW
        )
    ]
    if task is not None:
        filters.append(TrainedModel.task == task)
    if pipeline_id is not None:
        filters.append(TrainedModel.pipeline_id == pipeline_id)
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


@router.patch("/{model_id}", response_model=TrainedModelResponse)
def update_trained_model(
    model_id: str,
    payload: TrainedModelUpdateRequest,
    session: Session = Depends(get_trained_model_session),
    actor: User = Depends(get_current_user),
) -> TrainedModel:
    model = session.get(TrainedModel, model_id)
    if model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trained model not found")
    require_resource_permission(session, actor, "trained_model", model_id, PERMISSION_EDIT)
    model.metrics = {**(model.metrics or {}), "deployment_name": payload.deployment_name}
    session.add(model)
    session.commit()
    session.refresh(model)
    return model
