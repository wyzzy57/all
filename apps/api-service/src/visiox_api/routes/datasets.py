from datetime import UTC, datetime
import json
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile
from typing import Any
from zipfile import ZipFile

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from visiox_common.tasks import TaskStatus, TaskType
from visiox_db.models import (
    Annotation,
    Dataset,
    DatasetSample,
    DatasetVersion,
    LabelProject,
    Task,
    TrainingPipeline,
)
from visiox_api.dependencies.auth import get_current_user
from visiox_api.dependencies.authorization import require_resource_permission
from visiox_api.dependencies.database import get_db_session
from visiox_api.services.audit import record_audit
from visiox_api.services.authorization import authorized_resource_predicate
from visiox_api.services.dataset_versions import publish_labeled_llm_dataset
from visiox_db.models.identity import (
    AUDIT_RESULT_FAILED,
    AUDIT_RESULT_SUCCESS,
    PERMISSION_DELETE,
    PERMISSION_EDIT,
    PERMISSION_VIEW,
    User,
)
from visiox_storage.client import ObjectStorageClient
from visiox_yolo26.datasets.analysis import analyze_dataset
from visiox_yolo26.datasets.processing import process_dataset
from visiox_yolo26.datasets.validation import SUPPORTED_TASKS, validate_dataset_format


router = APIRouter(prefix="/datasets", tags=["datasets"])


class DatasetCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    task: str
    class_schema: dict[str, Any] = Field(default_factory=dict)
    source: str = "upload"
    preparation: bool = False


class DatasetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    task: str
    status: str
    format: str
    class_schema: dict[str, Any]
    schema_config: dict[str, Any]
    manifest_checksum: str | None
    sample_count: int
    annotation_count: int
    source: str | None
    storage_uri: str | None
    organization_id: str | None
    owner_user_id: str | None
    visibility: str
    asset_role: str
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


class DatasetProcessRequest(BaseModel):
    augment: dict[str, bool] = Field(default_factory=dict)
    clean: dict[str, bool] = Field(default_factory=dict)
    max_samples: int = Field(default=200, ge=1, le=1000)


class DatasetVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    dataset_id: str
    version: int
    status: str
    format: str
    object_uri: str
    manifest_uri: str
    manifest_checksum: str
    source_revision: str | None
    total_count: int
    valid_count: int
    invalid_count: int
    skipped_count: int
    size_bytes: int
    published_at: datetime


class DatasetConversionResponse(BaseModel):
    version: DatasetVersionResponse
    reused: bool
    total_count: int
    valid_count: int
    invalid_count: int
    skipped_count: int
    issues: list[dict[str, Any]]


get_dataset_session = get_db_session


def get_dataset_object_storage_client(request: Request) -> ObjectStorageClient:
    storage = getattr(request.app.state, "object_storage", None)
    if storage is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Object storage is not configured")
    return storage


def _utc_now() -> datetime:
    return datetime.now(UTC)


def dataset_or_404(session: Session, dataset_id: str) -> Dataset:
    dataset = session.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset not found")
    return dataset


def _validate_task(task: str) -> None:
    if task not in SUPPORTED_TASKS and task != "llm":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Unsupported task: {task}")


@router.post("", response_model=DatasetResponse, status_code=status.HTTP_201_CREATED)
def create_dataset(
    payload: DatasetCreateRequest,
    request: Request,
    session: Session = Depends(get_dataset_session),
    actor: User = Depends(get_current_user),
) -> Dataset:
    _validate_task(payload.task)
    dataset = Dataset(
        name=payload.name,
        task=payload.task,
        status="preparing" if payload.preparation else "created",
        class_schema=payload.class_schema,
        source=payload.source,
        organization_id=actor.organization_id,
        owner_user_id=actor.id,
        visibility="private",
        asset_role="working" if payload.preparation else "published",
    )
    session.add(dataset)
    session.flush()
    record_audit(
        session,
        actor,
        "dataset.create",
        "dataset",
        dataset.id,
        AUDIT_RESULT_SUCCESS,
        request.headers.get("x-request-id"),
        {"name": dataset.name, "task": dataset.task, "source": dataset.source},
    )
    session.commit()
    session.refresh(dataset)
    return dataset


@router.post("/{dataset_id}/convert-labeled", response_model=DatasetConversionResponse)
def convert_labeled_dataset(
    dataset_id: str,
    request: Request,
    session: Session = Depends(get_dataset_session),
    storage: ObjectStorageClient = Depends(get_dataset_object_storage_client),
    actor: User = Depends(get_current_user),
) -> DatasetConversionResponse:
    dataset = dataset_or_404(session, dataset_id)
    require_resource_permission(session, actor, "dataset", dataset_id, PERMISSION_EDIT)
    try:
        result = publish_labeled_llm_dataset(session, storage, dataset)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    record_audit(
        session,
        actor,
        "dataset.publish_version",
        "dataset",
        dataset.id,
        AUDIT_RESULT_SUCCESS,
        request.headers.get("x-request-id"),
        {"dataset_version_id": result.version.id, "version": result.version.version, "reused": result.reused},
    )
    session.commit()
    return DatasetConversionResponse(
        version=DatasetVersionResponse.model_validate(result.version),
        reused=result.reused,
        total_count=result.total_count,
        valid_count=result.valid_count,
        invalid_count=result.invalid_count,
        skipped_count=result.skipped_count,
        issues=result.issues,
    )


@router.get("/{dataset_id}/versions", response_model=list[DatasetVersionResponse])
def list_dataset_versions(
    dataset_id: str,
    session: Session = Depends(get_dataset_session),
    actor: User = Depends(get_current_user),
) -> list[DatasetVersion]:
    dataset_or_404(session, dataset_id)
    require_resource_permission(session, actor, "dataset", dataset_id, PERMISSION_VIEW)
    return list(
        session.scalars(
            select(DatasetVersion)
            .where(DatasetVersion.dataset_id == dataset_id)
            .order_by(DatasetVersion.version.desc())
        ).all()
    )


@router.post("/{dataset_id}/promote", response_model=DatasetResponse, status_code=status.HTTP_201_CREATED)
def promote_annotated_dataset(
    dataset_id: str,
    response: Response,
    request: Request,
    session: Session = Depends(get_dataset_session),
    actor: User = Depends(get_current_user),
) -> Dataset:
    source_dataset = dataset_or_404(session, dataset_id)
    require_resource_permission(session, actor, "dataset", dataset_id, PERMISSION_EDIT)
    source_marker = f"preparation://{source_dataset.id}"
    existing = session.scalar(select(Dataset).where(Dataset.storage_uri == source_marker))
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        return existing

    source_samples = session.scalars(
        select(DatasetSample)
        .where(DatasetSample.dataset_id == source_dataset.id)
        .order_by(DatasetSample.created_at, DatasetSample.id)
    ).all()
    annotations_by_sample: dict[str, list[Annotation]] = {}
    if source_samples:
        source_annotations = session.scalars(
            select(Annotation).where(Annotation.dataset_sample_id.in_([sample.id for sample in source_samples]))
        ).all()
        for annotation in source_annotations:
            annotations_by_sample.setdefault(annotation.dataset_sample_id, []).append(annotation)

    annotated_samples = [sample for sample in source_samples if annotations_by_sample.get(sample.id)]
    if not annotated_samples:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Label Studio has no annotations to convert",
        )

    dataset = Dataset(
        name=_next_promoted_dataset_name(session, source_dataset.name),
        task=source_dataset.task,
        status="created",
        class_schema=source_dataset.class_schema,
        sample_count=len(annotated_samples),
        annotation_count=sum(len(annotations_by_sample[sample.id]) for sample in annotated_samples),
        source="label_studio",
        storage_uri=source_marker,
        organization_id=actor.organization_id,
        owner_user_id=actor.id,
        visibility="private",
        asset_role="published",
    )
    session.add(dataset)
    session.flush()
    for source_sample in annotated_samples:
        cloned_sample = DatasetSample(
            dataset_id=dataset.id,
            file_uri=source_sample.file_uri,
            width=source_sample.width,
            height=source_sample.height,
            checksum=source_sample.checksum,
            split=source_sample.split,
            annotation_status="labeled",
        )
        session.add(cloned_sample)
        session.flush()
        for source_annotation in annotations_by_sample[source_sample.id]:
            session.add(
                Annotation(
                    dataset_sample_id=cloned_sample.id,
                    source=source_annotation.source,
                    raw_payload_uri=source_annotation.raw_payload_uri,
                    internal_payload=source_annotation.internal_payload,
                    validation_status="pending",
                )
            )
    record_audit(
        session,
        actor,
        "dataset.promote",
        "dataset",
        dataset.id,
        AUDIT_RESULT_SUCCESS,
        request.headers.get("x-request-id"),
        {"source_dataset_id": source_dataset.id, "annotation_count": dataset.annotation_count},
    )
    session.commit()
    session.refresh(dataset)
    return dataset


def _next_promoted_dataset_name(session: Session, source_name: str) -> str:
    base = f"{source_name}-数据集"
    candidate = base
    suffix = 2
    while session.scalar(select(Dataset.id).where(Dataset.name == candidate)) is not None:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


@router.get("", response_model=DatasetListResponse)
def list_datasets(
    task: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_dataset_session),
    actor: User = Depends(get_current_user),
) -> DatasetListResponse:
    filters = [authorized_resource_predicate(session, actor, Dataset, "dataset", PERMISSION_VIEW)]
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


@router.get("/{dataset_id}/export")
def export_dataset(
    dataset_id: str,
    session: Session = Depends(get_dataset_session),
    storage: ObjectStorageClient = Depends(get_dataset_object_storage_client),
    actor: User = Depends(get_current_user),
) -> FileResponse:
    dataset = dataset_or_404(session, dataset_id)
    require_resource_permission(session, actor, "dataset", dataset_id, PERMISSION_VIEW)
    samples = session.scalars(
        select(DatasetSample).where(DatasetSample.dataset_id == dataset.id).order_by(DatasetSample.created_at, DatasetSample.id)
    ).all()
    sample_ids = [sample.id for sample in samples]
    annotations_by_sample: dict[str, list[Annotation]] = {sample.id: [] for sample in samples}
    if sample_ids:
        annotations = session.scalars(select(Annotation).where(Annotation.dataset_sample_id.in_(sample_ids))).all()
        for annotation in annotations:
            annotations_by_sample.setdefault(annotation.dataset_sample_id, []).append(annotation)

    with NamedTemporaryFile(delete=False, suffix=".zip") as temp_file:
        export_path = Path(temp_file.name)

    try:
        with ZipFile(export_path, "w") as archive:
            archive.writestr(
                "metadata.json",
                json.dumps(
                    {
                        "id": dataset.id,
                        "name": dataset.name,
                        "task": dataset.task,
                        "status": dataset.status,
                        "class_schema": dataset.class_schema,
                        "sample_count": dataset.sample_count,
                        "annotation_count": dataset.annotation_count,
                        "source": dataset.source,
                        "created_at": dataset.created_at.isoformat(),
                        "updated_at": dataset.updated_at.isoformat(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            archive.writestr(
                "annotations.json",
                json.dumps(
                    {
                        "samples": [
                            {
                                "id": sample.id,
                                "file_uri": sample.file_uri,
                                "width": sample.width,
                                "height": sample.height,
                                "split": sample.split,
                                "annotation_status": sample.annotation_status,
                                "annotations": [
                                    {
                                        "id": annotation.id,
                                        "source": annotation.source,
                                        "validation_status": annotation.validation_status,
                                        "payload": annotation.internal_payload,
                                    }
                                    for annotation in annotations_by_sample.get(sample.id, [])
                                ],
                            }
                            for sample in samples
                        ]
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            for index, sample in enumerate(samples, start=1):
                parsed = _parse_storage_uri(sample.file_uri)
                if parsed is None:
                    continue
                bucket, object_name = parsed
                suffix = PurePosixPath(object_name).suffix or ".bin"
                with NamedTemporaryFile(delete=False, suffix=suffix) as sample_file:
                    sample_path = Path(sample_file.name)
                try:
                    storage.get_file(bucket, object_name, sample_path)
                    archive.write(sample_path, f"images/{index:06d}_{_safe_export_name(object_name, suffix)}")
                finally:
                    sample_path.unlink(missing_ok=True)
    except Exception:
        export_path.unlink(missing_ok=True)
        raise

    filename = f"{_safe_export_name(dataset.name or dataset.id, '.zip')}"
    return FileResponse(
        export_path,
        media_type="application/zip",
        filename=filename,
        background=BackgroundTask(export_path.unlink, missing_ok=True),
    )


@router.get("/{dataset_id}", response_model=DatasetResponse)
def get_dataset(
    dataset_id: str,
    session: Session = Depends(get_dataset_session),
    actor: User = Depends(get_current_user),
) -> Dataset:
    dataset = dataset_or_404(session, dataset_id)
    require_resource_permission(session, actor, "dataset", dataset_id, PERMISSION_VIEW)
    return dataset


@router.delete("/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dataset(
    dataset_id: str,
    request: Request,
    session: Session = Depends(get_dataset_session),
    storage: ObjectStorageClient = Depends(get_dataset_object_storage_client),
    actor: User = Depends(get_current_user),
) -> Response:
    dataset = dataset_or_404(session, dataset_id)
    require_resource_permission(session, actor, "dataset", dataset_id, PERMISSION_DELETE)
    samples = session.scalars(select(DatasetSample).where(DatasetSample.dataset_id == dataset.id)).all()
    sample_ids = [sample.id for sample in samples]
    label_projects = session.scalars(select(LabelProject).where(LabelProject.dataset_id == dataset.id)).all()
    label_project_ids = [project.id for project in label_projects]
    object_uris = [sample.file_uri for sample in samples]
    if sample_ids:
        annotations = session.scalars(select(Annotation).where(Annotation.dataset_sample_id.in_(sample_ids))).all()
        object_uris.extend(annotation.raw_payload_uri for annotation in annotations if annotation.raw_payload_uri)
    shared_uris: set[str] = set()
    if object_uris:
        shared_uris.update(
            session.scalars(
                select(DatasetSample.file_uri).where(
                    DatasetSample.dataset_id != dataset.id,
                    DatasetSample.file_uri.in_(object_uris),
                )
            ).all()
        )
        shared_uris.update(
            uri
            for uri in session.scalars(
                select(Annotation.raw_payload_uri)
                .join(DatasetSample, Annotation.dataset_sample_id == DatasetSample.id)
                .where(
                    DatasetSample.dataset_id != dataset.id,
                    Annotation.raw_payload_uri.in_(object_uris),
                )
            ).all()
            if uri
        )
        object_uris = [uri for uri in object_uris if uri not in shared_uris]

    if label_project_ids:
        session.execute(delete(Task).where(Task.resource_type == "label_project", Task.resource_id.in_(label_project_ids)))
    session.execute(delete(Task).where(Task.resource_type == "dataset", Task.resource_id == dataset.id))
    session.execute(update(TrainingPipeline).where(TrainingPipeline.dataset_id == dataset.id).values(dataset_id=None))
    if sample_ids:
        session.execute(delete(Annotation).where(Annotation.dataset_sample_id.in_(sample_ids)))
        session.execute(delete(DatasetSample).where(DatasetSample.id.in_(sample_ids)))
    session.execute(delete(LabelProject).where(LabelProject.dataset_id == dataset.id))
    session.delete(dataset)
    record_audit(
        session,
        actor,
        "dataset.delete",
        "dataset",
        dataset.id,
        AUDIT_RESULT_SUCCESS,
        request.headers.get("x-request-id"),
        {"name": dataset.name},
    )
    session.commit()
    _delete_storage_objects(storage, object_uris)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{dataset_id}/analyze", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
def create_dataset_analysis_task(
    dataset_id: str,
    request: Request,
    session: Session = Depends(get_dataset_session),
    actor: User = Depends(get_current_user),
) -> Task:
    dataset_or_404(session, dataset_id)
    require_resource_permission(session, actor, "dataset", dataset_id, PERMISSION_EDIT)
    result = analyze_dataset(session, dataset_id)
    now = _utc_now()
    task = Task(
        task_type=TaskType.ANALYZE_DATASET.value,
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
    record_audit(
        session,
        actor,
        "dataset.analyze",
        "dataset",
        dataset_id,
        AUDIT_RESULT_SUCCESS,
        request.headers.get("x-request-id"),
        {"task_id": task.id},
    )
    session.commit()
    session.refresh(task)
    return task


@router.post("/{dataset_id}/process", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
def create_dataset_processing_task(
    dataset_id: str,
    payload: DatasetProcessRequest,
    request: Request,
    session: Session = Depends(get_dataset_session),
    storage: ObjectStorageClient = Depends(get_dataset_object_storage_client),
    actor: User = Depends(get_current_user),
) -> Task:
    dataset_or_404(session, dataset_id)
    require_resource_permission(session, actor, "dataset", dataset_id, PERMISSION_EDIT)
    now = _utc_now()
    try:
        result = process_dataset(
            session=session,
            storage=storage,
            dataset_id=dataset_id,
            augment_rules=payload.augment,
            clean_rules=payload.clean,
            max_samples=payload.max_samples,
        )
        task = Task(
            task_type=TaskType.PROCESS_DATASET.value,
            status=TaskStatus.SUCCESS.value,
            progress=100,
            resource_type="dataset",
            resource_id=dataset_id,
            stage="completed",
            payload={"dataset_id": dataset_id, "result": result},
            started_at=now,
            finished_at=_utc_now(),
        )
        audit_result = AUDIT_RESULT_SUCCESS
    except Exception as exc:
        session.rollback()
        task = Task(
            task_type=TaskType.PROCESS_DATASET.value,
            status=TaskStatus.FAILED.value,
            progress=100,
            resource_type="dataset",
            resource_id=dataset_id,
            stage="failed",
            payload={"dataset_id": dataset_id},
            error_code="PROCESS_DATASET_FAILED",
            error_message=str(exc),
            retryable=True,
            started_at=now,
            finished_at=_utc_now(),
        )
        audit_result = AUDIT_RESULT_FAILED
    session.add(task)
    record_audit(
        session,
        actor,
        "dataset.process",
        "dataset",
        dataset_id,
        audit_result,
        request.headers.get("x-request-id"),
        {"task_id": task.id, "augment": payload.augment, "clean": payload.clean},
    )
    session.commit()
    session.refresh(task)
    return task


def _delete_storage_objects(storage: ObjectStorageClient, uris: list[str]) -> None:
    for uri in uris:
        parsed = _parse_storage_uri(uri)
        if parsed is None:
            continue
        bucket, object_name = parsed
        try:
            storage.delete_file(bucket, object_name)
        except Exception:
            pass


def _parse_storage_uri(uri: str) -> tuple[str, str] | None:
    marker = "://"
    if marker not in uri:
        return None
    remainder = uri.split(marker, 1)[1]
    bucket, separator, object_name = remainder.partition("/")
    if not separator or not bucket or not object_name:
        return None
    return bucket, object_name


def _safe_export_name(value: str, fallback_suffix: str) -> str:
    name = PurePosixPath(value.replace("\\", "/")).name or "dataset"
    cleaned = "".join(character if character.isalnum() or character in {".", "-", "_"} else "_" for character in name)
    if "." not in cleaned and fallback_suffix:
        cleaned = f"{cleaned}{fallback_suffix}"
    return cleaned


@router.post("/{dataset_id}/validate", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
def create_dataset_validation_task(
    dataset_id: str,
    request: Request,
    session: Session = Depends(get_dataset_session),
    actor: User = Depends(get_current_user),
) -> Task:
    dataset = dataset_or_404(session, dataset_id)
    require_resource_permission(session, actor, "dataset", dataset_id, PERMISSION_EDIT)
    result = validate_dataset_format(session, dataset_id)
    first_error = result["errors"][0]["code"] if result["errors"] else None
    now = _utc_now()
    if result["valid"]:
        dataset.status = "validated"
        dataset.asset_role = "published"
        session.add(dataset)
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
    record_audit(
        session,
        actor,
        "dataset.validate",
        "dataset",
        dataset_id,
        AUDIT_RESULT_SUCCESS if result["valid"] else AUDIT_RESULT_FAILED,
        request.headers.get("x-request-id"),
        {"task_id": task.id, "valid": result["valid"]},
    )
    session.commit()
    session.refresh(task)
    return task
