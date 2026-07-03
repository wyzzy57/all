from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from visiox_common.tasks import TaskStatus, TaskType
from visiox_db.models import Dataset, Task
from visiox_db.session import get_session
from visiox_yolo26.datasets.analysis import analyze_dataset
from visiox_yolo26.datasets.validation import SUPPORTED_TASKS, validate_dataset_format


router = APIRouter(prefix="/datasets", tags=["datasets"])


class DatasetCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    task: str
    class_schema: dict[str, Any] = Field(default_factory=dict)
    source: str = "upload"


class DatasetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    task: str
    status: str
    class_schema: dict[str, Any]
    sample_count: int
    annotation_count: int
    source: str | None
    storage_uri: str | None
    created_at: datetime
    updated_at: datetime


class DatasetListResponse(BaseModel):
    items: list[DatasetResponse]
    total: int
    limit: int
    offset: int


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


def get_dataset_session() -> Generator[Session]:
    yield from get_session()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def dataset_or_404(session: Session, dataset_id: str) -> Dataset:
    dataset = session.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset not found")
    return dataset


def _validate_task(task: str) -> None:
    if task not in SUPPORTED_TASKS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Unsupported task: {task}")


@router.post("", response_model=DatasetResponse, status_code=status.HTTP_201_CREATED)
def create_dataset(
    request: DatasetCreateRequest,
    session: Session = Depends(get_dataset_session),
) -> Dataset:
    _validate_task(request.task)
    dataset = Dataset(
        name=request.name,
        task=request.task,
        status="created",
        class_schema=request.class_schema,
        source=request.source,
    )
    session.add(dataset)
    session.commit()
    session.refresh(dataset)
    return dataset


@router.get("", response_model=DatasetListResponse)
def list_datasets(
    task: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_dataset_session),
) -> DatasetListResponse:
    filters = []
    if task is not None:
        filters.append(Dataset.task == task)
    if status_filter is not None:
        filters.append(Dataset.status == status_filter)

    total_query = select(func.count()).select_from(Dataset)
    list_query = select(Dataset).order_by(Dataset.created_at, Dataset.id)
    if filters:
        total_query = total_query.where(*filters)
        list_query = list_query.where(*filters)

    total = session.scalar(total_query) or 0
    datasets = session.scalars(list_query.limit(limit).offset(offset)).all()
    return DatasetListResponse(items=list(datasets), total=total, limit=limit, offset=offset)


@router.get("/{dataset_id}", response_model=DatasetResponse)
def get_dataset(dataset_id: str, session: Session = Depends(get_dataset_session)) -> Dataset:
    return dataset_or_404(session, dataset_id)


@router.post("/{dataset_id}/analyze", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
def create_dataset_analysis_task(
    dataset_id: str,
    session: Session = Depends(get_dataset_session),
) -> Task:
    dataset_or_404(session, dataset_id)
    result = analyze_dataset(session, dataset_id)
    now = _utc_now()
    task = Task(
        task_type="ANALYZE_DATASET",
        status=TaskStatus.SUCCESS.value,
        progress=100,
        resource_type="dataset",
        resource_id=dataset_id,
        stage="completed",
        payload={"dataset_id": dataset_id, "result": result},
        started_at=now,
        finished_at=now,
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


@router.post("/{dataset_id}/validate", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
def create_dataset_validation_task(
    dataset_id: str,
    session: Session = Depends(get_dataset_session),
) -> Task:
    dataset_or_404(session, dataset_id)
    result = validate_dataset_format(session, dataset_id)
    first_error = result["errors"][0]["code"] if result["errors"] else None
    now = _utc_now()
    task = Task(
        task_type=TaskType.VALIDATE_DATASET_FORMAT.value,
        status=TaskStatus.SUCCESS.value if result["valid"] else TaskStatus.FAILED.value,
        progress=100,
        resource_type="dataset",
        resource_id=dataset_id,
        stage="completed",
        payload={"dataset_id": dataset_id, "result": result},
        error_code=first_error,
        error_message=result["errors"][0]["message"] if result["errors"] else None,
        started_at=now,
        finished_at=now,
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    return task
