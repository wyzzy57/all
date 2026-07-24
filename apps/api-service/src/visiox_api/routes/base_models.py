from collections.abc import Generator
from datetime import datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from visiox_db.models import BaseModel
from visiox_db.session import get_session
from visiox_common.settings import Settings, get_settings
from visiox_storage.client import ObjectStorageClient
from visiox_yolo26.tasks import YOLO26_SCALES, YOLO26_TASKS


router = APIRouter(prefix="/base-models", tags=["base-models"])
_MODEL_UPLOAD_CHUNK_BYTES = 1024 * 1024


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


def get_base_model_storage(request: Request) -> ObjectStorageClient:
    storage = getattr(request.app.state, "object_storage", None)
    if storage is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Object storage client is not configured",
        )
    return storage


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


@router.post(":upload", response_model=BaseModelResponse, status_code=status.HTTP_201_CREATED)
async def upload_base_model(
    file: UploadFile = File(...),
    task: str = Form(...),
    scale: str = Form(...),
    session: Session = Depends(get_base_model_session),
    storage: ObjectStorageClient = Depends(get_base_model_storage),
    settings: Settings = Depends(get_settings),
) -> BaseModel:
    if task not in YOLO26_TASKS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Unsupported YOLO task")
    if scale not in YOLO26_SCALES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Unsupported YOLO model scale")

    original_filename = _safe_model_filename(file.filename or "model.pt")
    if PurePosixPath(original_filename).suffix.lower() != ".pt":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only trainable Ultralytics/PyTorch .pt weights are supported",
        )

    model_id = str(uuid4())
    object_name = f"custom/{model_id}/{original_filename}"
    stored_filename = f"{model_id[:8]}-{original_filename}"
    digest = sha256()
    size_bytes = 0
    header = b""
    with NamedTemporaryFile(delete=False, suffix=".pt") as temporary_file:
        temporary_path = Path(temporary_file.name)
        while chunk := await file.read(_MODEL_UPLOAD_CHUNK_BYTES):
            size_bytes += len(chunk)
            if size_bytes > settings.max_model_upload_bytes:
                temporary_file.close()
                temporary_path.unlink(missing_ok=True)
                raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="Model file is too large")
            if len(header) < 256:
                header += chunk[: 256 - len(header)]
            digest.update(chunk)
            temporary_file.write(chunk)

    try:
        _validate_pt_payload(header, size_bytes)
        local_uri = storage.put_file(
            "models",
            object_name,
            temporary_path,
            content_type="application/octet-stream",
        )
        model = BaseModel(
            id=model_id,
            family=f"custom-{model_id}",
            task=task,
            scale=scale,
            filename=stored_filename,
            source_path=f"upload://{original_filename}",
            local_uri=local_uri,
            checksum=digest.hexdigest(),
            size_bytes=size_bytes,
            status="ready",
        )
        session.add(model)
        try:
            session.commit()
        except Exception:
            session.rollback()
            storage.delete_file("models", object_name)
            raise
        session.refresh(model)
        return model
    finally:
        temporary_path.unlink(missing_ok=True)


def _safe_model_filename(filename: str) -> str:
    basename = PurePosixPath(filename.replace("\\", "/")).name.strip()
    safe = "".join(character if character.isalnum() or character in {".", "-", "_"} else "_" for character in basename)
    return safe[:180] or "model.pt"


def _validate_pt_payload(header: bytes, size_bytes: int) -> None:
    if size_bytes < 1024:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Model file is empty or incomplete")
    lowered = header.lower()
    if lowered.startswith(b"version https://git-lfs"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Git LFS pointer is not a model weight file")
    if not (header.startswith(b"PK\x03\x04") or header.startswith(b"\x80")):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid PyTorch .pt weight file")


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
