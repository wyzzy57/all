from collections.abc import Generator
from pathlib import Path, PurePosixPath
import json
from tempfile import NamedTemporaryFile
from typing import Any
from zipfile import BadZipFile, ZipFile

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from visiox_api.dependencies.auth import get_current_user
from visiox_api.dependencies.authorization import require_resource_permission
from visiox_api.dependencies.database import get_db_session
from visiox_api.routes.datasets import dataset_or_404
from visiox_common.settings import Settings, get_settings
from visiox_db.models import Annotation, Dataset, DatasetSample
from visiox_db.models.identity import PERMISSION_EDIT, PERMISSION_VIEW, User
from visiox_storage.checksum import sha256_bytes
from visiox_storage.client import ObjectStorageClient


router = APIRouter(prefix="/datasets/{dataset_id}/samples", tags=["dataset-samples"])
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
ZIP_EXTENSIONS = {".zip"}
LABEL_EXTENSIONS = {".txt"}
YAML_EXTENSIONS = {".yaml", ".yml"}
JSON_EXTENSIONS = {".json"}
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


class SplitRatioRequest(BaseModel):
    train_ratio: int = Field(ge=0, le=100)
    val_ratio: int = Field(ge=0, le=100)
    test_ratio: int = Field(ge=0, le=100)


get_dataset_sample_session = get_db_session


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
    settings: Settings = Depends(get_settings),
    actor: User = Depends(get_current_user),
) -> SampleUploadResponse:
    dataset = dataset_or_404(session, dataset_id)
    require_resource_permission(session, actor, "dataset", dataset_id, PERMISSION_EDIT)
    filename = file.filename or "upload"
    data = await file.read()
    _ensure_size_within_limit(len(data), settings.max_dataset_upload_bytes, "Upload file is too large")
    extension = PurePosixPath(filename.replace("\\", "/")).suffix.lower()

    result = _UploadResult()
    try:
        if extension in IMAGE_EXTENSIONS:
            result = _store_image(session, storage, dataset, _safe_basename(filename), data, settings)
        elif extension in ZIP_EXTENSIONS:
            result = _store_zip(session, storage, dataset, data, settings)
        else:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported upload file type")

        dataset.sample_count = _dataset_sample_count(session, dataset.id)
        session.add(dataset)
        session.commit()
        for sample in result.samples:
            session.refresh(sample)
        session.refresh(dataset)
    except Exception:
        session.rollback()
        _cleanup_stored_objects(storage, result.stored_objects)
        raise
    if result.created_count == 0 and result.duplicate_count > 0:
        response.status_code = status.HTTP_200_OK
    return SampleUploadResponse(
        samples=result.samples,
        created_count=result.created_count,
        duplicate_count=result.duplicate_count,
        skipped_count=result.skipped_count,
    )


@router.post(":upload-batch", response_model=SampleUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_sample_batch(
    dataset_id: str,
    response: Response,
    files: list[UploadFile] = File(...),
    relative_paths: list[str] | None = Form(default=None),
    session: Session = Depends(get_dataset_sample_session),
    storage: ObjectStorageClient = Depends(get_object_storage_client),
    settings: Settings = Depends(get_settings),
    actor: User = Depends(get_current_user),
) -> SampleUploadResponse:
    dataset = dataset_or_404(session, dataset_id)
    require_resource_permission(session, actor, "dataset", dataset_id, PERMISSION_EDIT)
    if len(files) > settings.max_dataset_zip_entries:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="Upload has too many files")

    entries: list[tuple[str, bytes]] = []
    total_size = 0
    for index, upload in enumerate(files):
        filename = _upload_relative_path(upload, relative_paths, index)
        _validate_dataset_entry(filename)
        data = await upload.read()
        total_size += len(data)
        _ensure_size_within_limit(total_size, settings.max_dataset_zip_uncompressed_bytes, "Upload is too large")
        entries.append((filename, data))

    result = _store_dataset_entries(session, storage, dataset, entries, settings)
    try:
        dataset.sample_count = _dataset_sample_count(session, dataset.id)
        session.add(dataset)
        session.commit()
        for sample in result.samples:
            session.refresh(sample)
        session.refresh(dataset)
    except Exception:
        session.rollback()
        _cleanup_stored_objects(storage, result.stored_objects)
        raise
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
    actor: User = Depends(get_current_user),
) -> DatasetSampleListResponse:
    dataset_or_404(session, dataset_id)
    require_resource_permission(session, actor, "dataset", dataset_id, PERMISSION_VIEW)
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


@router.get("/{sample_id}/content")
def get_sample_content(
    dataset_id: str,
    sample_id: str,
    session: Session = Depends(get_dataset_sample_session),
    storage: ObjectStorageClient = Depends(get_object_storage_client),
    actor: User = Depends(get_current_user),
) -> FileResponse:
    dataset_or_404(session, dataset_id)
    require_resource_permission(session, actor, "dataset", dataset_id, PERMISSION_VIEW)
    sample = session.get(DatasetSample, sample_id)
    if sample is None or sample.dataset_id != dataset_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sample not found")
    bucket, object_name = _parse_storage_uri(sample.file_uri)
    suffix = PurePosixPath(object_name).suffix or ".img"
    with NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
        temp_path = Path(temp_file.name)
    storage.get_file(bucket, object_name, temp_path)
    return FileResponse(temp_path, media_type=_image_media_type(suffix), background=_DeleteFileTask(temp_path))


@router.post("/splits", response_model=DatasetSampleListResponse)
def assign_splits(
    dataset_id: str,
    request: SplitAssignmentRequest,
    session: Session = Depends(get_dataset_sample_session),
    actor: User = Depends(get_current_user),
) -> DatasetSampleListResponse:
    dataset_or_404(session, dataset_id)
    require_resource_permission(session, actor, "dataset", dataset_id, PERMISSION_EDIT)
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


@router.post("/splits:ratio", response_model=DatasetSampleListResponse)
def assign_splits_by_ratio(
    dataset_id: str,
    request: SplitRatioRequest,
    session: Session = Depends(get_dataset_sample_session),
    actor: User = Depends(get_current_user),
) -> DatasetSampleListResponse:
    dataset_or_404(session, dataset_id)
    require_resource_permission(session, actor, "dataset", dataset_id, PERMISSION_EDIT)
    ratio_total = request.train_ratio + request.val_ratio + request.test_ratio
    if ratio_total != 100:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Split ratios must add up to 100")

    samples = list(
        session.scalars(select(DatasetSample).where(DatasetSample.dataset_id == dataset_id).order_by(DatasetSample.id)).all()
    )
    total = len(samples)
    train_count = round(total * request.train_ratio / 100)
    val_count = round(total * request.val_ratio / 100)
    if train_count + val_count > total:
        val_count = max(0, total - train_count)
    test_count = total - train_count - val_count

    split_sequence = ["train"] * train_count + ["val"] * val_count + ["test"] * test_count
    for sample, split in zip(samples, split_sequence, strict=False):
        sample.split = split
        session.add(sample)
    session.commit()
    for sample in samples:
        session.refresh(sample)

    return DatasetSampleListResponse(items=samples[:200], total=total, limit=min(total, 200), offset=0)


class _UploadResult:
    def __init__(self) -> None:
        self.samples: list[DatasetSample] = []
        self.created_count = 0
        self.duplicate_count = 0
        self.skipped_count = 0
        self.stored_objects: list[tuple[str, str]] = []


def _store_zip(
    session: Session,
    storage: ObjectStorageClient,
    dataset: Dataset,
    data: bytes,
    settings: Settings,
) -> _UploadResult:
    result = _UploadResult()
    try:
        with ZipFile(_bytes_file(data)) as archive:
            entries = [info for info in archive.infolist() if not info.is_dir()]
            if len(entries) > settings.max_dataset_zip_entries:
                raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="Zip file has too many entries")
            total_uncompressed = sum(info.file_size for info in entries)
            _ensure_size_within_limit(
                total_uncompressed,
                settings.max_dataset_zip_uncompressed_bytes,
                "Zip file is too large after extraction",
            )
            result = _store_dataset_entries(
                session,
                storage,
                dataset,
                [(info.filename, archive.read(info)) for info in entries],
                settings,
            )
    except BadZipFile as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid zip file") from exc
    except Exception:
        _cleanup_stored_objects(storage, result.stored_objects)
        raise
    return result


def _store_dataset_entries(
    session: Session,
    storage: ObjectStorageClient,
    dataset: Dataset,
    entries: list[tuple[str, bytes]],
    settings: Settings,
) -> _UploadResult:
    result = _UploadResult()
    try:
        labels = _label_entries(entries)
        coco = _coco_annotations(entries)
        _apply_class_schema_from_dataset_metadata(dataset, entries, coco)
        for filename, data in entries:
            _validate_dataset_entry(filename)
            extension = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
            if extension in IMAGE_EXTENSIONS:
                _ensure_size_within_limit(len(data), settings.max_dataset_image_bytes, "Image file is too large")
                image_result = _store_image(session, storage, dataset, _safe_basename(filename), data, settings)
                result.samples.extend(image_result.samples)
                result.created_count += image_result.created_count
                result.duplicate_count += image_result.duplicate_count
                result.skipped_count += image_result.skipped_count
                result.stored_objects.extend(image_result.stored_objects)
                sample = image_result.samples[0] if image_result.created_count == 1 else None
                if sample is not None:
                    label_text = _matching_label_text(filename, labels)
                    coco_payload = _matching_coco_payload(filename, coco)
                    if coco_payload:
                        sample.split = coco_payload["split"]
                        sample.annotation_status = "labeled"
                        session.add(
                            Annotation(
                                dataset_sample_id=sample.id,
                                source="coco",
                                internal_payload={"annotations": [{"source_annotation_id": f"coco-{sample.id}", "results": coco_payload["results"]}]},
                                validation_status="valid",
                            )
                        )
                        dataset.annotation_count = (dataset.annotation_count or 0) + 1
                    elif label_text:
                        sample.split = _split_from_path(filename)
                        sample.annotation_status = "labeled"
                        session.add(
                            Annotation(
                                dataset_sample_id=sample.id,
                                source="yolo",
                                internal_payload=_parse_yolo_detect_label(label_text, dataset, sample),
                                validation_status="valid",
                            )
                        )
                        dataset.annotation_count = (dataset.annotation_count or 0) + 1
            elif extension in LABEL_EXTENSIONS or extension in YAML_EXTENSIONS or extension in JSON_EXTENSIONS:
                result.skipped_count += 1
            else:
                result.skipped_count += 1
    except Exception:
        _cleanup_stored_objects(storage, result.stored_objects)
        raise
    return result


def _store_image(
    session: Session,
    storage: ObjectStorageClient,
    dataset: Dataset,
    filename: str,
    data: bytes,
    settings: Settings,
) -> _UploadResult:
    result = _UploadResult()
    _ensure_size_within_limit(len(data), settings.max_dataset_image_bytes, "Image file is too large")
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
        result.stored_objects.append(("datasets", object_name))
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
            image.verify()
        with Image.open(_bytes_file(data)) as image:
            image.load()
            return image.size
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid image file") from exc


def _bytes_file(data: bytes):
    from io import BytesIO

    return BytesIO(data)


class _DeleteFileTask:
    def __init__(self, path: Path) -> None:
        self.path = path

    async def __call__(self) -> None:
        self.path.unlink(missing_ok=True)


def _parse_storage_uri(uri: str) -> tuple[str, str]:
    marker = "://"
    if marker not in uri:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sample file is not available")
    remainder = uri.split(marker, 1)[1]
    bucket, separator, object_name = remainder.partition("/")
    if not separator or not bucket or not object_name:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sample file is not available")
    return bucket, object_name


def _image_media_type(suffix: str) -> str:
    return {
        ".bmp": "image/bmp",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(suffix.lower(), "application/octet-stream")


def _validate_zip_entry(filename: str) -> None:
    _validate_dataset_entry(filename)


def _validate_dataset_entry(filename: str) -> None:
    if "\\" in filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unsafe dataset entry: {filename}")
    if len(filename) >= 2 and filename[1] == ":":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unsafe dataset entry: {filename}")
    normalized = filename.replace("\\", "/")
    path = PurePosixPath(normalized)
    if normalized.startswith("//") or path.is_absolute() or any(part == ".." for part in path.parts):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unsafe dataset entry: {filename}")


def _safe_basename(filename: str) -> str:
    basename = PurePosixPath(filename.replace("\\", "/")).name
    cleaned = "".join(character if character.isalnum() or character in {".", "-", "_"} else "_" for character in basename)
    return cleaned or "upload"


def _upload_relative_path(upload: UploadFile, relative_paths: list[str] | None, index: int) -> str:
    if relative_paths is not None and index < len(relative_paths) and relative_paths[index]:
        return relative_paths[index]
    return upload.filename or "upload"


def _label_entries(entries: list[tuple[str, bytes]]) -> dict[str, str]:
    labels = {}
    for filename, data in entries:
        normalized = filename.replace("\\", "/")
        if PurePosixPath(normalized).suffix.lower() in LABEL_EXTENSIONS:
            labels[_label_key(normalized)] = data.decode("utf-8")
    return labels


def _label_key(filename: str) -> str:
    path = PurePosixPath(filename)
    parts = list(path.with_suffix("").parts)
    normalized_parts = ["labels" if part == "images" else part for part in parts]
    return "/".join(normalized_parts)


def _matching_label_text(image_filename: str, labels: dict[str, str]) -> str | None:
    normalized = image_filename.replace("\\", "/")
    path = PurePosixPath(normalized)
    image_key = _label_key(normalized)
    candidates = [
        image_key,
        "/".join(["labels" if part == "images" else part for part in path.with_suffix("").parts]),
        "/".join([*path.parent.parts, path.stem]),
        path.stem,
    ]
    for candidate in candidates:
        if candidate in labels:
            return labels[candidate]
    return None


def _coco_annotations(entries: list[tuple[str, bytes]]) -> dict[str, Any]:
    result = {"names": [], "by_image": {}}
    for filename, data in entries:
        normalized = filename.replace("\\", "/")
        if PurePosixPath(normalized).suffix.lower() not in JSON_EXTENSIONS:
            continue
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid COCO json: {filename}") from exc
        if not _looks_like_coco(payload):
            continue
        split = _split_from_path(normalized)
        if split == "unassigned":
            lowered = PurePosixPath(normalized).name.lower()
            if "train" in lowered:
                split = "train"
            elif "val" in lowered:
                split = "val"
            elif "test" in lowered:
                split = "test"
        categories = sorted(payload.get("categories", []), key=lambda item: item.get("id", 0))
        category_to_index = {category["id"]: index for index, category in enumerate(categories)}
        names = [str(category.get("name", category.get("id"))) for category in categories]
        if names:
            result["names"] = names
        images = {image["id"]: image for image in payload.get("images", [])}
        annotations_by_image: dict[Any, list[dict[str, Any]]] = {}
        for annotation in payload.get("annotations", []):
            annotations_by_image.setdefault(annotation.get("image_id"), []).append(annotation)
        for image_id, image in images.items():
            file_name = str(image.get("file_name", ""))
            width = float(image.get("width") or 0)
            height = float(image.get("height") or 0)
            if not file_name or width <= 0 or height <= 0:
                continue
            results = []
            for index, annotation in enumerate(annotations_by_image.get(image_id, []), start=1):
                bbox = annotation.get("bbox")
                if not isinstance(bbox, list) or len(bbox) != 4:
                    continue
                category_id = annotation.get("category_id")
                if category_id not in category_to_index:
                    continue
                x, y, box_width, box_height = [float(value) for value in bbox]
                class_id = category_to_index[category_id]
                results.append(
                    {
                        "source_result_id": str(annotation.get("id", f"{image_id}-{index}")),
                        "shape": "rectangle",
                        "class_id": class_id,
                        "class_name": names[class_id],
                        "x": x / width * 100,
                        "y": y / height * 100,
                        "width": box_width / width * 100,
                        "height": box_height / height * 100,
                    }
                )
            if results:
                for key in _image_match_keys(file_name):
                    result["by_image"][key] = {"split": split, "results": results}
    return result


def _looks_like_coco(payload: Any) -> bool:
    return (
        isinstance(payload, dict)
        and isinstance(payload.get("images"), list)
        and isinstance(payload.get("annotations"), list)
        and isinstance(payload.get("categories"), list)
    )


def _matching_coco_payload(image_filename: str, coco: dict[str, Any]) -> dict[str, Any] | None:
    by_image = coco.get("by_image", {})
    if not isinstance(by_image, dict):
        return None
    for key in _image_match_keys(image_filename):
        if key in by_image:
            return by_image[key]
    return None


def _image_match_keys(filename: str) -> list[str]:
    path = PurePosixPath(filename.replace("\\", "/"))
    return [str(path), path.name, path.stem]


def _split_from_path(filename: str) -> str:
    parts = set(PurePosixPath(filename.replace("\\", "/")).parts)
    for split in ("train", "val", "test"):
        if split in parts:
            return split
    return "unassigned"


def _parse_yolo_detect_label(label_text: str, dataset: Dataset, sample: DatasetSample) -> dict[str, Any]:
    names = _class_names(dataset.class_schema)
    results = []
    for line_number, line in enumerate(label_text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split()
        if len(parts) != 5:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid YOLO label line {line_number}")
        class_id = int(parts[0])
        values = [float(value) for value in parts[1:]]
        if class_id < 0 or class_id >= len(names) or any(value < 0 or value > 1 for value in values):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid YOLO label line {line_number}")
        x_center, y_center, width, height = values
        results.append(
            {
                "source_result_id": f"{sample.id}-{line_number}",
                "shape": "rectangle",
                "class_id": class_id,
                "class_name": names[class_id],
                "x": (x_center - width / 2) * 100,
                "y": (y_center - height / 2) * 100,
                "width": width * 100,
                "height": height * 100,
            }
        )
    return {"annotations": [{"source_annotation_id": f"yolo-{sample.id}", "results": results}]}


def _class_names(class_schema: dict[str, Any]) -> list[str]:
    names = class_schema.get("names")
    if isinstance(names, list) and names:
        return [str(name) for name in names]
    if isinstance(names, dict) and names:
        return [str(names[str(index)] if str(index) in names else names[index]) for index in range(len(names))]
    return ["class_0"]


def _apply_class_schema_from_dataset_metadata(
    dataset: Dataset,
    entries: list[tuple[str, bytes]],
    coco: dict[str, Any],
) -> None:
    coco_names = coco.get("names")
    if isinstance(coco_names, list) and coco_names:
        dataset.class_schema = {**(dataset.class_schema or {}), "names": coco_names}
        return
    for filename, data in entries:
        if PurePosixPath(filename.replace("\\", "/")).suffix.lower() not in YAML_EXTENSIONS:
            continue
        names = _parse_names_from_yaml_text(data.decode("utf-8", errors="ignore"))
        if names:
            dataset.class_schema = {**(dataset.class_schema or {}), "names": names}
            return


def _parse_names_from_yaml_text(text: str) -> list[str]:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("names:"):
            value = stripped.partition(":")[2].strip()
            if value.startswith("[") and value.endswith("]"):
                return [item.strip().strip("'\"") for item in value.strip("[]").split(",") if item.strip()]
            names = []
            for nested in lines[index + 1 :]:
                nested_stripped = nested.strip()
                if not nested.startswith(" ") or not nested_stripped:
                    break
                if ":" in nested_stripped:
                    names.append(nested_stripped.partition(":")[2].strip().strip("'\""))
                elif nested_stripped.startswith("-"):
                    names.append(nested_stripped[1:].strip().strip("'\""))
            return [name for name in names if name]
    return []


def _dataset_sample_count(session: Session, dataset_id: str) -> int:
    return session.scalar(
        select(func.count()).select_from(DatasetSample).where(DatasetSample.dataset_id == dataset_id)
    ) or 0


def _ensure_size_within_limit(size_bytes: int, max_bytes: int, detail: str) -> None:
    if size_bytes > max_bytes:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=detail)


def _cleanup_stored_objects(storage: ObjectStorageClient, stored_objects: list[tuple[str, str]]) -> None:
    for bucket, object_name in reversed(stored_objects):
        try:
            storage.delete_file(bucket, object_name)
        except Exception:
            pass
