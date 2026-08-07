from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from visiox_db.models import Annotation, Dataset, DatasetSample, DatasetVersion
from visiox_storage.client import ObjectStorageClient


SPLITS = ("train", "val", "test")
_SPLIT_ORDER = {name: index for index, name in enumerate(SPLITS)}
_HASH_CHUNK_SIZE = 1024 * 1024
_SUPPORTED_RUNTIME_MODELS = frozenset({"PP-YOLOE_plus-S", "RT-DETR-L"})
_IMPORTED_ANNOTATION_SOURCES = frozenset({"label_studio", "coco", "yolo"})
_SAFE_SAMPLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")
_SAFE_IMAGE_SUFFIX = re.compile(r"^\.[A-Za-z0-9]{1,10}$")


class PaddleXDatasetError(ValueError):
    pass


@dataclass(frozen=True)
class PaddleXDetectionExportReport:
    dataset_id: str
    dataset_version_id: str
    output_dir: Path
    sample_count: int
    annotation_count: int
    manifest_checksum: str


@dataclass(frozen=True)
class _PreparedSample:
    sample: DatasetSample
    split: str
    image_path: str
    boxes: tuple[dict[str, object], ...]


def export_paddlex_detection_dataset(
    session: Session,
    storage: ObjectStorageClient,
    dataset_id: str,
    dataset_version_id: str,
    output_dir: Path,
    *,
    runtime_model_id: str,
) -> PaddleXDetectionExportReport:
    if runtime_model_id not in _SUPPORTED_RUNTIME_MODELS:
        raise PaddleXDatasetError(
            f"unsupported PaddleX runtime model: {runtime_model_id}"
        )
    dataset, version, categories, samples = _preflight(
        session,
        storage,
        dataset_id,
        dataset_version_id,
    )
    output_dir = output_dir.resolve()
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(
        prefix=f".{output_dir.name}-",
        dir=output_dir.parent,
    ) as temporary:
        staging_dir = Path(temporary)
        manifest = _write_export(
            storage,
            dataset,
            version,
            categories,
            samples,
            staging_dir,
        )
        _publish_staged_directory(staging_dir, output_dir)

    return PaddleXDetectionExportReport(
        dataset_id=dataset.id,
        dataset_version_id=version.id,
        output_dir=output_dir,
        sample_count=len(samples),
        annotation_count=sum(len(item.boxes) for item in samples),
        manifest_checksum=str(manifest["checksum_sha256"]),
    )


def _preflight(
    session: Session,
    storage: ObjectStorageClient,
    dataset_id: str,
    dataset_version_id: str,
) -> tuple[
    Dataset,
    DatasetVersion,
    tuple[dict[str, object], ...],
    tuple[_PreparedSample, ...],
]:
    dataset = session.get(Dataset, dataset_id)
    if dataset is None:
        raise PaddleXDatasetError(f"dataset not found: {dataset_id}")
    if dataset.task != "detect":
        raise PaddleXDatasetError("PaddleX detection adapter requires a detect dataset")
    version = session.get(DatasetVersion, dataset_version_id)
    if version is None or version.dataset_id != dataset.id:
        raise PaddleXDatasetError(
            "dataset version does not belong to the source dataset"
        )
    categories, category_ids = _categories(dataset.class_schema or {})
    samples = list(
        session.scalars(
            select(DatasetSample)
            .where(DatasetSample.dataset_id == dataset.id)
            .order_by(DatasetSample.id)
        )
    )
    annotations = list(
        session.scalars(
            select(Annotation)
            .join(DatasetSample, Annotation.dataset_sample_id == DatasetSample.id)
            .where(
                DatasetSample.dataset_id == dataset.id,
                Annotation.source.in_(_IMPORTED_ANNOTATION_SOURCES),
            )
            .order_by(Annotation.created_at, Annotation.id)
        )
    )
    annotations_by_sample: dict[str, list[Annotation]] = {}
    for annotation in annotations:
        if annotation.validation_status == "pending":
            continue
        if annotation.validation_status != "valid":
            raise PaddleXDatasetError(
                "sample has an invalid imported annotation: "
                f"{annotation.dataset_sample_id}"
            )
        annotations_by_sample.setdefault(annotation.dataset_sample_id, []).append(
            annotation
        )

    prepared: list[_PreparedSample] = []
    split_counts = {split: 0 for split in SPLITS}
    for sample in samples:
        if _SAFE_SAMPLE_ID.fullmatch(sample.id) is None:
            raise PaddleXDatasetError(
                f"sample id cannot be used as a safe filename: {sample.id}"
            )
        if sample.split not in _SPLIT_ORDER:
            raise PaddleXDatasetError(
                f"sample split must be train, val, or test: {sample.id}"
            )
        if not sample.checksum:
            raise PaddleXDatasetError(f"sample checksum is unavailable: {sample.id}")
        if not sample.width or not sample.height:
            raise PaddleXDatasetError(f"sample dimensions are unavailable: {sample.id}")
        sample_annotations = annotations_by_sample.get(sample.id, [])
        if sample.annotation_status != "labeled" or not sample_annotations:
            raise PaddleXDatasetError(f"sample has no imported annotation: {sample.id}")
        results = _annotation_results(sample, sample_annotations)
        boxes = tuple(
            _coco_box(dataset, sample, result, category_ids) for result in results
        )
        bucket, object_name = _storage_location(sample.file_uri)
        try:
            image_size = storage.object_size(bucket, object_name)
        except Exception as exc:
            raise PaddleXDatasetError(
                f"sample image is unavailable: {sample.id}"
            ) from exc
        if image_size <= 0:
            raise PaddleXDatasetError(f"sample image is unavailable: {sample.id}")
        split = str(sample.split)
        split_counts[split] += 1
        suffix = Path(object_name).suffix.lower()
        if _SAFE_IMAGE_SUFFIX.fullmatch(suffix) is None:
            suffix = ".jpg"
        prepared.append(
            _PreparedSample(
                sample=sample,
                split=split,
                image_path=f"images/{split}/{sample.id}{suffix}",
                boxes=boxes,
            )
        )

    if split_counts["train"] == 0:
        raise PaddleXDatasetError("train split must contain at least one sample")
    if split_counts["val"] == 0:
        raise PaddleXDatasetError("val split must contain at least one sample")
    current_revision = compute_paddlex_detection_source_revision(
        dataset,
        samples,
        annotations,
    )
    if version.source_revision != current_revision:
        raise PaddleXDatasetError(
            "dataset content does not match the dataset version source revision"
        )
    prepared.sort(key=lambda item: (_SPLIT_ORDER[item.split], item.sample.id))
    return dataset, version, categories, tuple(prepared)


def compute_paddlex_detection_source_revision(
    dataset: Dataset,
    samples: list[DatasetSample],
    annotations: list[Annotation],
) -> str:
    payload = {
        "dataset": {
            "id": dataset.id,
            "task": dataset.task,
            "class_schema": dataset.class_schema,
        },
        "samples": [
            {
                "id": sample.id,
                "file_uri": sample.file_uri,
                "width": sample.width,
                "height": sample.height,
                "checksum": sample.checksum,
                "split": sample.split,
                "annotation_status": sample.annotation_status,
            }
            for sample in sorted(samples, key=lambda item: item.id)
        ],
        "annotations": [
            {
                "id": annotation.id,
                "dataset_sample_id": annotation.dataset_sample_id,
                "source": annotation.source,
                "validation_status": annotation.validation_status,
                "internal_payload": annotation.internal_payload,
            }
            for annotation in sorted(annotations, key=lambda item: item.id)
        ],
    }
    return _canonical_checksum(payload)


def _categories(
    class_schema: dict[str, Any],
) -> tuple[tuple[dict[str, object], ...], dict[str, int]]:
    payload = class_schema.get("names")
    if isinstance(payload, dict):
        names = []
        for index in range(len(payload)):
            if str(index) in payload:
                name = payload[str(index)]
            elif index in payload:
                name = payload[index]
            else:
                raise PaddleXDatasetError("class category ids must be contiguous")
            names.append(str(name))
    elif isinstance(payload, list):
        names = [str(name) for name in payload]
    else:
        raise PaddleXDatasetError("class schema is missing names")
    if not names or any(not name for name in names) or len(set(names)) != len(names):
        raise PaddleXDatasetError("class names must be non-empty and unique")
    categories = tuple(
        {"id": index, "name": name, "supercategory": ""}
        for index, name in enumerate(names, start=1)
    )
    return categories, {name: index for index, name in enumerate(names, start=1)}


def _annotation_results(
    sample: DatasetSample,
    annotations: list[Annotation],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    imported_payload = False
    for annotation in annotations:
        payload = annotation.internal_payload or {}
        annotation_payloads = payload.get("annotations")
        if not isinstance(annotation_payloads, list) or not annotation_payloads:
            continue
        imported_payload = True
        for annotation_payload in annotation_payloads:
            if not isinstance(annotation_payload, dict):
                raise PaddleXDatasetError(
                    f"sample has an invalid imported annotation: {sample.id}"
                )
            source_results = annotation_payload.get("results")
            if not isinstance(source_results, list):
                raise PaddleXDatasetError(
                    f"sample has an invalid imported annotation: {sample.id}"
                )
            for result in source_results:
                if not isinstance(result, dict):
                    raise PaddleXDatasetError(
                        f"sample has an invalid imported annotation: {sample.id}"
                    )
                results.append(result)
    if not imported_payload:
        raise PaddleXDatasetError(f"sample has no imported annotation: {sample.id}")
    return results


def _coco_box(
    dataset: Dataset,
    sample: DatasetSample,
    result: dict[str, Any],
    category_ids: dict[str, int],
) -> dict[str, object]:
    if result.get("shape") != "rectangle":
        raise PaddleXDatasetError(f"invalid box shape for sample: {sample.id}")
    class_name = result.get("class_name")
    if not isinstance(class_name, str) or class_name not in category_ids:
        raise PaddleXDatasetError(f"invalid box category for sample: {sample.id}")
    values: list[float] = []
    for field in ("x", "y", "width", "height"):
        value = result.get(field)
        if isinstance(value, bool):
            raise PaddleXDatasetError(f"invalid box for sample: {sample.id}")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise PaddleXDatasetError(f"invalid box for sample: {sample.id}") from exc
        if not 0 <= number <= 100:
            raise PaddleXDatasetError(f"invalid box for sample: {sample.id}")
        values.append(number)
    x, y, width, height = values
    if width <= 0 or height <= 0 or x + width > 100 or y + height > 100:
        raise PaddleXDatasetError(f"invalid box for sample: {sample.id}")
    pixel_width = width * int(sample.width) / 100
    pixel_height = height * int(sample.height) / 100
    return {
        "category_id": category_ids[class_name],
        "bbox": [
            x * int(sample.width) / 100,
            y * int(sample.height) / 100,
            pixel_width,
            pixel_height,
        ],
        "area": pixel_width * pixel_height,
        "iscrowd": 0,
    }


def _write_export(
    storage: ObjectStorageClient,
    dataset: Dataset,
    version: DatasetVersion,
    categories: tuple[dict[str, object], ...],
    samples: tuple[_PreparedSample, ...],
    output_dir: Path,
) -> dict[str, object]:
    sample_manifest: list[dict[str, object]] = []
    split_manifest: dict[str, dict[str, object]] = {}
    for split in SPLITS:
        split_samples = [item for item in samples if item.split == split]
        images: list[dict[str, object]] = []
        annotations: list[dict[str, object]] = []
        annotation_id = 1
        for image_id, item in enumerate(split_samples, start=1):
            destination = output_dir / item.image_path
            bucket, object_name = _storage_location(item.sample.file_uri)
            storage.get_file(bucket, object_name, destination)
            if _stream_sha256(destination) != item.sample.checksum:
                raise PaddleXDatasetError(
                    f"sample image checksum does not match dataset version: {item.sample.id}"
                )
            images.append(
                {
                    "id": image_id,
                    "file_name": item.image_path,
                    "width": item.sample.width,
                    "height": item.sample.height,
                }
            )
            for box in item.boxes:
                annotations.append(
                    {
                        "id": annotation_id,
                        "image_id": image_id,
                        **box,
                    }
                )
                annotation_id += 1
            sample_manifest.append(
                {
                    "sample_id": item.sample.id,
                    "split": split,
                    "source_checksum_sha256": item.sample.checksum,
                    "image_path": item.image_path,
                }
            )
        annotation_path = f"annotations/instance_{split}.json"
        _write_json(
            output_dir / annotation_path,
            {
                "images": images,
                "annotations": annotations,
                "categories": list(categories),
            },
        )
        split_manifest[split] = {
            "sample_count": len(images),
            "annotation_count": len(annotations),
            "annotation_path": annotation_path,
            "image_dir": f"images/{split}",
        }

    files = []
    for path in sorted(output_dir.rglob("*")):
        if path.is_file():
            files.append(
                {
                    "path": path.relative_to(output_dir).as_posix(),
                    "size_bytes": path.stat().st_size,
                    "checksum_sha256": _stream_sha256(path),
                }
            )
    unsigned: dict[str, object] = {
        "schema_version": "1.0",
        "format": "paddlex_coco_detection",
        "source": {
            "dataset_id": dataset.id,
            "dataset_version_id": version.id,
            "source_revision": version.source_revision,
        },
        "categories": list(categories),
        "splits": split_manifest,
        "samples": sample_manifest,
        "files": files,
    }
    manifest = {
        **unsigned,
        "checksum_sha256": _canonical_checksum(unsigned),
    }
    _write_json(output_dir / "dataset-manifest.json", manifest)
    return manifest


def _storage_location(uri: str) -> tuple[str, str]:
    scheme, separator, remainder = uri.partition("://")
    if not separator or scheme not in {"memory", "minio"}:
        raise PaddleXDatasetError(f"unsupported sample storage URI: {uri}")
    bucket, separator, object_name = remainder.partition("/")
    if not separator or not bucket or not object_name:
        raise PaddleXDatasetError(f"unsupported sample storage URI: {uri}")
    return bucket, object_name


def _publish_staged_directory(staging_dir: Path, output_dir: Path) -> None:
    backup_dir = output_dir.with_name(f".{output_dir.name}.backup-{uuid4().hex}")
    had_previous_output = output_dir.exists()
    if had_previous_output:
        output_dir.replace(backup_dir)
    try:
        staging_dir.replace(output_dir)
    except BaseException:
        if had_previous_output:
            if output_dir.exists():
                _remove_path(output_dir)
            backup_dir.replace(output_dir)
        raise
    if had_previous_output:
        _remove_path(backup_dir)


def _remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _canonical_checksum(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _stream_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(_HASH_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()
