from collections.abc import Generator
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile
from typing import Any
from zipfile import BadZipFile, ZipFile

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from visiox_api.routes.datasets import dataset_or_404
from visiox_db.models import Dataset, DatasetSample
from visiox_db.session import get_session
from visiox_storage.checksum import sha256_bytes
from visiox_storage.client import ObjectStorageClient


router = APIRouter(prefix="/datasets/{dataset_id}/samples", tags=["dataset-samples"])
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
ZIP_EXTENSIONS = {".zip"}
ALLOWED_SPLITS = {"train", "val", "test", "unassigned"}


class DatasetSampleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    dataset_id: str
    file_uri: str
    width: int | None
    height: int | None
    checksum: str | None
    split: str | None
    annotation_status: str
    created_at: Any
    updated_at: Any


class SampleUploadResponse(BaseModel):
    samples: list[DatasetSampleResponse]
    created_count: int
    duplicate_count: int
    skipped_count: int


class DatasetSampleListResponse(BaseModel):
    items: list[DatasetSampleResponse]
    total: int
    limit: int
    offset: int


class SplitAssignment(BaseModel):
    sample_id: str
    split: str


class SplitAssignmentRequest(BaseModel):
    assignments: list[SplitAssignment] = Field(min_length=1)


def get_dataset_sample_session() -> Generator[Session]:
    yield from get_session()


def get_object_storage_client(request: Request) -> ObjectStorageClient:
    storage = getattr(request.app.state, "object_storage", None)
    if storage is not None:
        return storage
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Object storage client is not configured",
    )


@router.post(":upload", response_model=SampleUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_samples(
    dataset_id: str,
    response: Response,
    file: UploadFile = File(...),
    session: Session = Depends(get_dataset_sample_session),
    storage: ObjectStorageClient = Depends(get_object_storage_client),
) -> SampleUploadResponse:
    dataset = dataset_or_404(session, dataset_id)
    filename = file.filename or "upload"
    data = await file.read()
    extension = PurePosixPath(filename.replace("\\", "/")).suffix.lower()

    if extension in IMAGE_EXTENSIONS:
        result = _store_image(session, storage, dataset, _safe_basename(filename), data)
    elif extension in ZIP_EXTENSIONS:
        result = _store_zip(session, storage, dataset, data)
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported upload file type")

    dataset.sample_count = _dataset_sample_count(session, dataset.id)
    session.add(dataset)
    session.commit()
    for sample in result.samples:
        session.refresh(sample)
    session.refresh(dataset)
    if result.created_count == 0 and result.duplicate_count > 0:
        response.status_code = status.HTTP_200_OK
    return SampleUploadResponse(
        samples=result.samples,
        created_count=result.created_count,
        duplicate_count=result.duplicate_count,
        skipped_count=result.skipped_count,
    )


@router.get("", response_model=DatasetSampleListResponse)
def list_samples(
    dataset_id: str,
    split: str | None = None,
    annotation_status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_dataset_sample_session),
) -> DatasetSampleListResponse:
    dataset_or_404(session, dataset_id)
    filters = [DatasetSample.dataset_id == dataset_id]
    if split is not None:
        filters.append(DatasetSample.split == split)
    if annotation_status is not None:
        filters.append(DatasetSample.annotation_status == annotation_status)

    total = session.scalar(select(func.count()).select_from(DatasetSample).where(*filters)) or 0
    samples = session.scalars(
        select(DatasetSample).where(*filters).order_by(DatasetSample.created_at, DatasetSample.id).limit(limit).offset(offset)
    ).all()
    return DatasetSampleListResponse(items=list(samples), total=total, limit=limit, offset=offset)


@router.post("/splits", response_model=DatasetSampleListResponse)
def assign_splits(
    dataset_id: str,
    request: SplitAssignmentRequest,
    session: Session = Depends(get_dataset_sample_session),
) -> DatasetSampleListResponse:
    dataset_or_404(session, dataset_id)
    sample_ids = [assignment.sample_id for assignment in request.assignments]
    samples_by_id = {
        sample.id: sample
        for sample in session.scalars(select(DatasetSample).where(DatasetSample.id.in_(sample_ids))).all()
    }
    if len(samples_by_id) != len(set(sample_ids)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown sample id")

    for assignment in request.assignments:
        if assignment.split not in ALLOWED_SPLITS:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unsupported split")
        sample = samples_by_id[assignment.sample_id]
        if sample.dataset_id != dataset_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Sample does not belong to dataset")
        sample.split = assignment.split
        session.add(sample)
    session.commit()
    for sample in samples_by_id.values():
        session.refresh(sample)

    samples = list(samples_by_id.values())
    return DatasetSampleListResponse(items=samples, total=len(samples), limit=len(samples), offset=0)


class _UploadResult:
    def __init__(self) -> None:
        self.samples: list[DatasetSample] = []
        self.created_count = 0
        self.duplicate_count = 0
        self.skipped_count = 0


def _store_zip(
    session: Session,
    storage: ObjectStorageClient,
    dataset: Dataset,
    data: bytes,
) -> _UploadResult:
    result = _UploadResult()
    try:
        with ZipFile(_bytes_file(data)) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                _validate_zip_entry(info.filename)
                filename = _safe_basename(info.filename)
                if PurePosixPath(filename).suffix.lower() not in IMAGE_EXTENSIONS:
                    result.skipped_count += 1
                    continue
                image_data = archive.read(info)
                image_result = _store_image(session, storage, dataset, filename, image_data)
                result.samples.extend(image_result.samples)
                result.created_count += image_result.created_count
                result.duplicate_count += image_result.duplicate_count
                result.skipped_count += image_result.skipped_count
    except BadZipFile as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid zip file") from exc
    return result


def _store_image(
    session: Session,
    storage: ObjectStorageClient,
    dataset: Dataset,
    filename: str,
    data: bytes,
) -> _UploadResult:
    result = _UploadResult()
    checksum = sha256_bytes(data)
    duplicate = session.scalar(
        select(DatasetSample).where(DatasetSample.dataset_id == dataset.id, DatasetSample.checksum == checksum)
    )
    if duplicate is not None:
        result.samples.append(duplicate)
        result.duplicate_count = 1
        return result

    width, height = _image_dimensions(data)
    object_name = f"{dataset.id}/samples/{checksum}-{filename}"
    with NamedTemporaryFile(delete=False) as temp_file:
        temp_file.write(data)
        temp_path = Path(temp_file.name)
    try:
        file_uri = storage.put_file("datasets", object_name, temp_path)
    finally:
        temp_path.unlink(missing_ok=True)

    sample = DatasetSample(
        dataset_id=dataset.id,
        file_uri=file_uri,
        width=width,
        height=height,
        checksum=checksum,
        split="unassigned",
        annotation_status="unlabeled",
    )
    session.add(sample)
    session.flush()
    result.samples.append(sample)
    result.created_count = 1
    return result


def _image_dimensions(data: bytes) -> tuple[int, int]:
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(_bytes_file(data)) as image:
            return image.size
    except UnidentifiedImageError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid image file") from exc


def _bytes_file(data: bytes):
    from io import BytesIO

    return BytesIO(data)


def _validate_zip_entry(filename: str) -> None:
    if "\\" in filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unsafe zip entry: {filename}")
    if len(filename) >= 2 and filename[1] == ":":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unsafe zip entry: {filename}")
    normalized = filename.replace("\\", "/")
    path = PurePosixPath(normalized)
    if normalized.startswith("//") or path.is_absolute() or any(part == ".." for part in path.parts):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unsafe zip entry: {filename}")


def _safe_basename(filename: str) -> str:
    basename = PurePosixPath(filename.replace("\\", "/")).name
    cleaned = "".join(character if character.isalnum() or character in {".", "-", "_"} else "_" for character in basename)
    return cleaned or "upload"


def _dataset_sample_count(session: Session, dataset_id: str) -> int:
    return session.scalar(
        select(func.count()).select_from(DatasetSample).where(DatasetSample.dataset_id == dataset_id)
    ) or 0
