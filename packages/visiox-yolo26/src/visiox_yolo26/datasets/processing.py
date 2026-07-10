from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import os
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import Any
from uuid import uuid4

os.environ.setdefault("NO_ALBUMENTATIONS_UPDATE", "1")

import albumentations as A
import cv2
import numpy as np
from PIL import Image, UnidentifiedImageError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from visiox_db.models import Annotation, Dataset, DatasetSample
from visiox_storage.checksum import sha256_bytes
from visiox_storage.client import ObjectStorageClient


AugmentRules = dict[str, bool]
CleanRules = dict[str, bool]


@dataclass
class ProcessDatasetResult:
    augmented_count: int
    skipped_augmentation_count: int
    cleaning_issues: list[dict[str, Any]]
    cleanvision_issue_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "augmented_count": self.augmented_count,
            "skipped_augmentation_count": self.skipped_augmentation_count,
            "cleaning_issue_count": len(self.cleaning_issues),
            "cleanvision_issue_count": self.cleanvision_issue_count,
            "cleaning_issues": self.cleaning_issues[:200],
        }


def process_dataset(
    session: Session,
    storage: ObjectStorageClient,
    dataset_id: str,
    augment_rules: AugmentRules | None = None,
    clean_rules: CleanRules | None = None,
    max_samples: int = 200,
) -> dict[str, Any]:
    dataset = session.get(Dataset, dataset_id)
    if dataset is None:
        raise ValueError(f"Dataset not found: {dataset_id}")

    samples = list(
        session.scalars(
            select(DatasetSample)
            .where(DatasetSample.dataset_id == dataset_id)
            .order_by(DatasetSample.created_at, DatasetSample.id)
            .limit(max_samples)
        ).all()
    )
    annotations_by_sample = _annotations_by_sample(session, [sample.id for sample in samples])
    augment_rules = augment_rules or {}
    clean_rules = clean_rules or {}

    augmented_count = 0
    skipped_count = 0
    cleanvision_issues: list[dict[str, Any]] = []
    supplemental_issues: list[dict[str, Any]] = []

    with TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        local_paths: dict[str, Path] = {}
        for sample in samples:
            parsed = _parse_storage_uri(sample.file_uri)
            if parsed is None:
                supplemental_issues.append(_issue(sample, "missing_file", "Sample file is unavailable"))
                continue
            bucket, object_name = parsed
            suffix = Path(object_name).suffix or ".jpg"
            local_path = tmp_path / f"{sample.id}{suffix}"
            storage.get_file(bucket, object_name, local_path)
            local_paths[sample.id] = local_path

        if _any_enabled(clean_rules):
            cleanvision_issues = _run_cleanvision(local_paths)
            supplemental_issues.extend(_run_supplemental_cleaning(samples, local_paths, clean_rules))

        if _any_enabled(augment_rules):
            transforms = _selected_transforms(augment_rules)
            for transform_name, transform in transforms:
                for sample in samples:
                    local_path = local_paths.get(sample.id)
                    if local_path is None:
                        skipped_count += 1
                        continue
                    annotations = annotations_by_sample.get(sample.id, [])
                    created = _augment_sample(
                        session=session,
                        storage=storage,
                        dataset=dataset,
                        sample=sample,
                        annotations=annotations,
                        local_path=local_path,
                        transform_name=transform_name,
                        transform=transform,
                    )
                    if created:
                        augmented_count += 1
                    else:
                        skipped_count += 1

    dataset.sample_count = session.scalar(
        select(func.count()).select_from(DatasetSample).where(DatasetSample.dataset_id == dataset.id)
    ) or 0
    dataset.annotation_count = session.scalar(
        select(func.count())
        .select_from(Annotation)
        .join(DatasetSample, Annotation.dataset_sample_id == DatasetSample.id)
        .where(DatasetSample.dataset_id == dataset.id)
    ) or 0
    session.add(dataset)
    session.commit()

    result = ProcessDatasetResult(
        augmented_count=augmented_count,
        skipped_augmentation_count=skipped_count,
        cleaning_issues=[*cleanvision_issues, *supplemental_issues],
        cleanvision_issue_count=len(cleanvision_issues),
    )
    return result.as_dict()


def _selected_transforms(rules: AugmentRules) -> list[tuple[str, A.BasicTransform]]:
    transforms: list[tuple[str, A.BasicTransform]] = []
    if rules.get("horizontalFlip"):
        transforms.append(("horizontal_flip", A.HorizontalFlip(p=1)))
    if rules.get("verticalFlip"):
        transforms.append(("vertical_flip", A.VerticalFlip(p=1)))
    if rules.get("rotate"):
        transforms.append(("rotate", A.Rotate(limit=15, border_mode=cv2.BORDER_CONSTANT, p=1)))
    if rules.get("translate"):
        transforms.append(("translate", A.ShiftScaleRotate(shift_limit=0.08, scale_limit=0, rotate_limit=0, border_mode=cv2.BORDER_CONSTANT, p=1)))
    if rules.get("scale"):
        transforms.append(("scale", A.RandomScale(scale_limit=0.15, p=1)))
    if rules.get("noise"):
        transforms.append(("noise", A.GaussNoise(var_limit=(10.0, 40.0), p=1)))
    if rules.get("blur"):
        transforms.append(("blur", A.Blur(blur_limit=3, p=1)))
    return transforms


def _augment_sample(
    session: Session,
    storage: ObjectStorageClient,
    dataset: Dataset,
    sample: DatasetSample,
    annotations: list[Annotation],
    local_path: Path,
    transform_name: str,
    transform: A.BasicTransform,
) -> bool:
    image = cv2.imread(str(local_path), cv2.IMREAD_COLOR)
    if image is None:
        return False
    height, width = image.shape[:2]
    bboxes, labels, annotation_refs = _rectangle_bboxes(annotations, width, height)
    if annotations and not bboxes:
        return False

    compose = A.Compose(
        [transform],
        bbox_params=A.BboxParams(format="pascal_voc", label_fields=["labels"], min_visibility=0.2),
    )
    try:
        augmented = compose(image=image, bboxes=bboxes, labels=labels)
    except Exception:
        return False

    augmented_image = augmented["image"]
    success, encoded = cv2.imencode(".jpg", augmented_image)
    if not success:
        return False
    data = encoded.tobytes()
    checksum = sha256_bytes(data)
    duplicate = session.scalar(
        select(DatasetSample).where(DatasetSample.dataset_id == dataset.id, DatasetSample.checksum == checksum)
    )
    if duplicate is not None:
        return False

    object_name = f"{dataset.id}/samples/{checksum}-{transform_name}-{_safe_name(local_path.stem)}.jpg"
    with NamedTemporaryFile(delete=False, suffix=".jpg") as temp_file:
        temp_file.write(data)
        temp_path = Path(temp_file.name)
    try:
        file_uri = storage.put_file("datasets", object_name, temp_path, content_type="image/jpeg")
    finally:
        temp_path.unlink(missing_ok=True)

    new_sample = DatasetSample(
        dataset_id=dataset.id,
        file_uri=file_uri,
        width=int(augmented_image.shape[1]),
        height=int(augmented_image.shape[0]),
        checksum=checksum,
        split=sample.split,
        annotation_status=sample.annotation_status,
    )
    session.add(new_sample)
    session.flush()

    if bboxes:
        transformed_bboxes = list(augmented["bboxes"])
        new_annotations = _transformed_annotations(annotations, annotation_refs, transformed_bboxes, new_sample)
        for annotation in new_annotations:
            session.add(annotation)
    return True


def _rectangle_bboxes(annotations: list[Annotation], image_width: int, image_height: int) -> tuple[list[list[float]], list[int], list[dict[str, Any]]]:
    bboxes: list[list[float]] = []
    labels: list[int] = []
    refs: list[dict[str, Any]] = []
    for annotation_index, annotation in enumerate(annotations):
        for result_index, result in _iter_results(annotation.internal_payload):
            if result.get("shape") != "rectangle":
                continue
            x1 = float(result.get("x", 0)) / 100 * image_width
            y1 = float(result.get("y", 0)) / 100 * image_height
            x2 = x1 + float(result.get("width", 0)) / 100 * image_width
            y2 = y1 + float(result.get("height", 0)) / 100 * image_height
            if x2 <= x1 or y2 <= y1:
                continue
            bboxes.append([x1, y1, x2, y2])
            labels.append(int(result.get("class_id") or 0))
            refs.append({"annotation_index": annotation_index, "result_index": result_index, "result": result})
    return bboxes, labels, refs


def _transformed_annotations(
    annotations: list[Annotation],
    refs: list[dict[str, Any]],
    transformed_bboxes: list[tuple[float, float, float, float]],
    sample: DatasetSample,
) -> list[Annotation]:
    if not refs or not transformed_bboxes:
        return []
    grouped: dict[int, list[dict[str, Any]]] = {}
    for ref, bbox in zip(refs, transformed_bboxes, strict=False):
        x1, y1, x2, y2 = bbox
        source = dict(ref["result"])
        source["x"] = x1 / max(sample.width or 1, 1) * 100
        source["y"] = y1 / max(sample.height or 1, 1) * 100
        source["width"] = (x2 - x1) / max(sample.width or 1, 1) * 100
        source["height"] = (y2 - y1) / max(sample.height or 1, 1) * 100
        source["source_result_id"] = f"{sample.id}-{uuid4().hex[:8]}"
        grouped.setdefault(int(ref["annotation_index"]), []).append(source)

    created = []
    for annotation_index, results in grouped.items():
        source_annotation = annotations[annotation_index]
        created.append(
            Annotation(
                dataset_sample_id=sample.id,
                source=f"{source_annotation.source}:augmented",
                internal_payload={"annotations": [{"source_annotation_id": f"aug-{sample.id}", "results": results}]},
                validation_status=source_annotation.validation_status,
            )
        )
    return created


def _run_cleanvision(local_paths: dict[str, Path]) -> list[dict[str, Any]]:
    if not local_paths:
        return []
    from cleanvision import Imagelab

    filepaths = [str(path) for path in local_paths.values()]
    sample_by_path = {str(path): sample_id for sample_id, path in local_paths.items()}
    lab = Imagelab(filepaths=filepaths, verbose=False)
    lab.find_issues()
    issues = getattr(lab, "issues", None)
    if issues is None:
        return []
    records = issues.reset_index().to_dict(orient="records")
    result: list[dict[str, Any]] = []
    for record in records:
        filepath = str(record.get("filepath") or record.get("index") or "")
        sample_id = sample_by_path.get(filepath) or sample_by_path.get(str(Path(filepath)))
        for key, value in record.items():
            if key.endswith("_issue") and bool(value):
                result.append({"sample_id": sample_id, "rule": key.removesuffix("_issue"), "source": "cleanvision"})
    return result


def _run_supplemental_cleaning(samples: Iterable[DatasetSample], local_paths: dict[str, Path], rules: CleanRules) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    checksums: dict[str, str] = {}
    for sample in samples:
        path = local_paths.get(sample.id)
        if path is None:
            continue
        try:
            with Image.open(path) as image:
                image.load()
                array = np.asarray(image.convert("RGB"))
        except (OSError, UnidentifiedImageError, ValueError) as exc:
            issues.append(_issue(sample, "invalid_image", str(exc)))
            continue

        gray = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)
        mean = float(np.mean(gray))
        std = float(np.std(gray))
        if rules.get("tooDark") and mean < 35:
            issues.append(_issue(sample, "too_dark", f"mean={mean:.2f}"))
        if rules.get("tooBright") and mean > 220:
            issues.append(_issue(sample, "too_bright", f"mean={mean:.2f}"))
        if rules.get("lowInformation") and std < 8:
            issues.append(_issue(sample, "low_information", f"std={std:.2f}"))
        if rules.get("blur") and cv2.Laplacian(gray, cv2.CV_64F).var() < 80:
            issues.append(_issue(sample, "blurry", "laplacian variance below threshold"))
        if rules.get("aspectRatio") and sample.width and sample.height:
            ratio = max(sample.width / sample.height, sample.height / sample.width)
            if ratio > 4:
                issues.append(_issue(sample, "aspect_ratio", f"ratio={ratio:.2f}"))
        if rules.get("size") and sample.width and sample.height and (sample.width < 32 or sample.height < 32):
            issues.append(_issue(sample, "small_size", f"{sample.width}x{sample.height}"))
        if rules.get("gray") and _is_gray(array):
            issues.append(_issue(sample, "gray", "RGB channels are nearly identical"))
        if rules.get("exactDuplicate") and sample.checksum:
            if sample.checksum in checksums:
                issues.append(_issue(sample, "exact_duplicate", f"duplicates {checksums[sample.checksum]}"))
            checksums[sample.checksum] = sample.id
    return issues


def _annotations_by_sample(session: Session, sample_ids: list[str]) -> dict[str, list[Annotation]]:
    if not sample_ids:
        return {}
    annotations = session.scalars(select(Annotation).where(Annotation.dataset_sample_id.in_(sample_ids))).all()
    result: dict[str, list[Annotation]] = {}
    for annotation in annotations:
        result.setdefault(annotation.dataset_sample_id, []).append(annotation)
    return result


def _iter_results(payload: dict[str, Any]):
    annotations = payload.get("annotations")
    if not isinstance(annotations, list):
        return
    result_index = 0
    for annotation in annotations:
        if not isinstance(annotation, dict) or not isinstance(annotation.get("results"), list):
            continue
        for result in annotation["results"]:
            if isinstance(result, dict):
                yield result_index, result
                result_index += 1


def _parse_storage_uri(uri: str) -> tuple[str, str] | None:
    marker = "://"
    if marker not in uri:
        return None
    remainder = uri.split(marker, 1)[1]
    bucket, separator, object_name = remainder.partition("/")
    if not separator or not bucket or not object_name:
        return None
    return bucket, object_name


def _safe_name(value: str) -> str:
    cleaned = "".join(character if character.isalnum() or character in {".", "-", "_"} else "_" for character in value)
    return cleaned or "sample"


def _issue(sample: DatasetSample, rule: str, detail: str) -> dict[str, Any]:
    return {"sample_id": sample.id, "rule": rule, "detail": detail, "source": "opencv"}


def _any_enabled(rules: dict[str, bool]) -> bool:
    return any(bool(value) for key, value in rules.items() if key != "all")


def _is_gray(array: np.ndarray) -> bool:
    return bool(
        np.mean(np.abs(array[:, :, 0].astype(np.int16) - array[:, :, 1].astype(np.int16))) < 2
        and np.mean(np.abs(array[:, :, 1].astype(np.int16) - array[:, :, 2].astype(np.int16))) < 2
    )
