from collections.abc import Generator
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from visiox_db.models import BaseModel
from visiox_db.session import get_session


router = APIRouter(prefix="/base-models", tags=["base-models"])


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


def get_base_model_session() -> Generator[Session]:
    yield from get_session()


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
