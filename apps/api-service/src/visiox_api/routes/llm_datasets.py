from __future__ import annotations

from datetime import UTC, datetime
import hashlib
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from visiox_api.dependencies.auth import get_current_user
from visiox_api.dependencies.authorization import require_resource_permission
from visiox_api.dependencies.database import get_db_session
from visiox_api.services.llm_datasets import (
    LlmDatasetError,
    LlmDatasetValidation,
    canonical_jsonl,
    preview_records,
    validate_llm_dataset_file,
)
from visiox_common.settings import Settings, get_settings
from visiox_db.models import Dataset, DatasetSample
from visiox_db.models.identity import PERMISSION_EDIT, PERMISSION_VIEW, User
from visiox_storage.client import ObjectStorageClient


router = APIRouter(prefix="/datasets/llm", tags=["llm-datasets"])


class LlmDatasetResponse(BaseModel):
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


class LlmDatasetReference(BaseModel):
    dataset_id: str = Field(min_length=1, max_length=128)


class LlmDatasetPreviewRequest(LlmDatasetReference):
    limit: int = Field(default=5, ge=1, le=20)


class LlmDatasetIssueResponse(BaseModel):
    index: int
    code: str
    message: str


class LlmDatasetValidationResponse(BaseModel):
    dataset: LlmDatasetResponse
    total_count: int
    valid_count: int
    invalid_count: int
    issues: list[LlmDatasetIssueResponse]


class LlmDatasetPreviewResponse(BaseModel):
    dataset_id: str
    format: str
    manifest_checksum: str
    samples: list[dict[str, Any]]
    token_analysis: dict[str, Any]


get_llm_dataset_session = get_db_session


def get_llm_dataset_storage(request: Request) -> ObjectStorageClient:
    storage = getattr(request.app.state, "object_storage", None)
    if storage is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Object storage is not configured")
    return storage


@router.post("/upload", response_model=LlmDatasetValidationResponse, status_code=status.HTTP_201_CREATED)
async def upload_llm_dataset(
    file: UploadFile = File(...),
    name: str = Form(..., min_length=1, max_length=160),
    dataset_format: str = Form(default="auto", alias="format"),
    session: Session = Depends(get_llm_dataset_session),
    storage: ObjectStorageClient = Depends(get_llm_dataset_storage),
    settings: Settings = Depends(get_settings),
    actor: User = Depends(get_current_user),
) -> LlmDatasetValidationResponse:
    clean_name = name.strip()
    if session.scalar(select(Dataset.id).where(Dataset.name == clean_name)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Dataset name already exists")
    suffix = Path(file.filename or "dataset.jsonl").suffix.lower()
    if suffix not in {".json", ".jsonl", ".csv"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="LLM dataset file must be JSON, JSONL, or CSV",
        )
    source_path = await _save_upload(file, suffix=suffix, max_bytes=settings.max_dataset_upload_bytes)
    normalized_path: Path | None = None
    try:
        validation = _validate_or_422(source_path, dataset_format)
        if not validation.records:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="LLM dataset has no valid SFT records",
            )
        with NamedTemporaryFile(delete=False, suffix=".jsonl") as normalized_file:
            normalized_path = Path(normalized_file.name)
            normalized_file.write(canonical_jsonl(validation.records))
        dataset = Dataset(
            name=clean_name,
            task="llm",
            status="validated",
            format=validation.format,
            class_schema={"format": validation.format},
            schema_config={
                **validation.schema_config,
                "validation": {
                    "total_count": validation.total_count,
                    "valid_count": len(validation.records),
                    "invalid_count": len(validation.issues),
                    "validated_at": datetime.now(UTC).isoformat(),
                },
            },
            manifest_checksum=validation.manifest_checksum,
            sample_count=len(validation.records),
            annotation_count=0,
            source="llm_upload",
            organization_id=actor.organization_id,
            owner_user_id=actor.id,
            visibility="private",
            asset_role="working",
        )
        session.add(dataset)
        session.flush()
        dataset.storage_uri = storage.put_file(
            "datasets",
            f"{dataset.id}/llm/train.jsonl",
            normalized_path,
            content_type="application/x-ndjson",
        )
        for record in validation.records:
            payload = canonical_jsonl([record])
            with NamedTemporaryFile(delete=False, suffix=".json") as sample_file:
                sample_path = Path(sample_file.name)
                sample_file.write(payload)
            try:
                object_uri = storage.put_file(
                    "datasets",
                    f"{dataset.id}/llm/samples/{record['source_row_id']}.json",
                    sample_path,
                    content_type="application/json",
                )
            finally:
                sample_path.unlink(missing_ok=True)
            session.add(
                DatasetSample(
                    dataset_id=dataset.id,
                    file_uri=object_uri,
                    checksum=hashlib.sha256(payload).hexdigest(),
                    split=None,
                    annotation_status="unlabeled",
                )
            )
        session.commit()
        session.refresh(dataset)
        return _validation_response(dataset, validation)
    finally:
        source_path.unlink(missing_ok=True)
        if normalized_path is not None:
            normalized_path.unlink(missing_ok=True)


@router.post("/validate", response_model=LlmDatasetValidationResponse)
def validate_llm_dataset(
    request: LlmDatasetReference,
    session: Session = Depends(get_llm_dataset_session),
    storage: ObjectStorageClient = Depends(get_llm_dataset_storage),
    actor: User = Depends(get_current_user),
) -> LlmDatasetValidationResponse:
    dataset = _dataset_or_404(session, request.dataset_id)
    require_resource_permission(session, actor, "dataset", dataset.id, PERMISSION_EDIT)
    path = _download_dataset(dataset, storage)
    try:
        validation = _validate_or_422(path, "auto")
        dataset.format = validation.format
        dataset.class_schema = {**(dataset.class_schema or {}), "format": validation.format}
        dataset.schema_config = {
            **validation.schema_config,
            "validation": {
                "total_count": validation.total_count,
                "valid_count": len(validation.records),
                "invalid_count": len(validation.issues),
                "validated_at": datetime.now(UTC).isoformat(),
            },
        }
        dataset.manifest_checksum = validation.manifest_checksum
        dataset.sample_count = len(validation.records)
        dataset.status = "validated" if validation.records else "invalid"
        session.add(dataset)
        session.commit()
        session.refresh(dataset)
        return _validation_response(dataset, validation)
    finally:
        path.unlink(missing_ok=True)


@router.post("/preview", response_model=LlmDatasetPreviewResponse)
def preview_llm_dataset(
    request: LlmDatasetPreviewRequest,
    session: Session = Depends(get_llm_dataset_session),
    storage: ObjectStorageClient = Depends(get_llm_dataset_storage),
    actor: User = Depends(get_current_user),
) -> LlmDatasetPreviewResponse:
    dataset = _dataset_or_404(session, request.dataset_id)
    require_resource_permission(session, actor, "dataset", dataset.id, PERMISSION_VIEW)
    path = _download_dataset(dataset, storage)
    try:
        validation = _validate_or_422(path, "auto")
        samples = preview_records(validation.records, validation.format, limit=request.limit)
        token_counts = [int(sample["token_estimate"]) for sample in samples]
        return LlmDatasetPreviewResponse(
            dataset_id=dataset.id,
            format=validation.format,
            manifest_checksum=validation.manifest_checksum,
            samples=samples,
            token_analysis={
                "method": "format_aware_estimate",
                "exact": False,
                "sample_count": len(samples),
                "minimum": min(token_counts, default=0),
                "maximum": max(token_counts, default=0),
                "average": round(sum(token_counts) / len(token_counts), 2) if token_counts else 0,
            },
        )
    finally:
        path.unlink(missing_ok=True)


async def _save_upload(upload: UploadFile, *, suffix: str, max_bytes: int) -> Path:
    with NamedTemporaryFile(delete=False, suffix=suffix) as temporary:
        path = Path(temporary.name)
        size = 0
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > max_bytes:
                path.unlink(missing_ok=True)
                raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="LLM dataset upload is too large")
            temporary.write(chunk)
    return path


def _dataset_or_404(session: Session, dataset_id: str) -> Dataset:
    dataset = session.get(Dataset, dataset_id)
    if dataset is None or dataset.task != "llm":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="LLM dataset not found")
    if not dataset.storage_uri:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="LLM dataset artifact is unavailable")
    return dataset


def _download_dataset(dataset: Dataset, storage: ObjectStorageClient) -> Path:
    parsed = _parse_storage_uri(str(dataset.storage_uri))
    if parsed is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="LLM dataset storage URI is invalid")
    with NamedTemporaryFile(delete=False, suffix=".jsonl") as temporary:
        path = Path(temporary.name)
    try:
        storage.get_file(parsed[0], parsed[1], path)
    except Exception as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="LLM dataset artifact is unavailable") from exc
    return path


def _parse_storage_uri(uri: str) -> tuple[str, str] | None:
    for scheme in ("minio://", "memory://"):
        if uri.startswith(scheme):
            remainder = uri.removeprefix(scheme)
            if "/" not in remainder:
                return None
            bucket, object_name = remainder.split("/", 1)
            return (bucket, object_name) if bucket and object_name else None
    return None


def _validate_or_422(path: Path, requested_format: str) -> LlmDatasetValidation:
    try:
        return validate_llm_dataset_file(path, requested_format=requested_format)
    except LlmDatasetError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc


def _validation_response(dataset: Dataset, validation: LlmDatasetValidation) -> LlmDatasetValidationResponse:
    return LlmDatasetValidationResponse(
        dataset=LlmDatasetResponse.model_validate(dataset),
        total_count=validation.total_count,
        valid_count=len(validation.records),
        invalid_count=len(validation.issues),
        issues=[LlmDatasetIssueResponse(index=item.index, code=item.code, message=item.message) for item in validation.issues[:100]],
    )
