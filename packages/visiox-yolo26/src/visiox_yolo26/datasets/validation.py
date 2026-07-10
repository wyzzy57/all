from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from visiox_db.models import Annotation, Dataset, DatasetSample
from visiox_yolo26.datasets.analysis import analyze_dataset


SUPPORTED_TASKS = {"detect", "segment", "semantic", "pose", "obb", "classify"}
ANNOTATION_REQUIRED_TASKS = {"detect", "segment", "semantic", "pose", "obb"}
TASK_ALLOWED_SHAPES = {
    "detect": {"rectangle"},
    "segment": {"polygon"},
    "semantic": {"polygon", "brush"},
    "pose": {"rectangle", "keypoints"},
    "obb": {"polygon", "rectangle"},
    "classify": {"classification"},
}


def validate_dataset_format(session: Session, dataset_id: str) -> dict[str, Any]:
    dataset = session.get(Dataset, dataset_id)
    if dataset is None:
        raise ValueError(f"Dataset not found: {dataset_id}")

    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    if dataset.task not in SUPPORTED_TASKS:
        errors.append({"code": "UNSUPPORTED_TASK", "message": f"Unsupported task: {dataset.task}"})

    analysis = analyze_dataset(session, dataset_id)
    if analysis["sample_count"] == 0:
        errors.append({"code": "DATASET_EMPTY", "message": "Dataset has no samples"})

    if analysis["invalid_samples"]:
        errors.append({"code": "INVALID_SAMPLE", "message": "Dataset contains invalid samples"})

    annotation_count = session.scalar(
        select(func.count())
        .select_from(Annotation)
        .join(DatasetSample, Annotation.dataset_sample_id == DatasetSample.id)
        .where(DatasetSample.dataset_id == dataset_id)
    ) or 0
    if analysis["sample_count"] > 0 and annotation_count == 0:
        if dataset.task == "classify":
            warnings.append(
                {
                    "code": "CLASSIFY_LABELS_MISSING",
                    "message": "Classification dataset has no labels",
                }
            )
        elif dataset.task in ANNOTATION_REQUIRED_TASKS:
            errors.append({"code": "ANNOTATIONS_MISSING", "message": "Dataset has no annotations"})

    shape_errors = _annotation_shape_errors(session, dataset, dataset_id)
    if shape_errors:
        errors.extend(shape_errors)

    return {
        "valid": not errors,
        "task": dataset.task,
        "errors": errors,
        "warnings": warnings,
        "analysis": analysis,
    }


def _annotation_shape_errors(session: Session, dataset: Dataset, dataset_id: str) -> list[dict[str, str]]:
    allowed_shapes = TASK_ALLOWED_SHAPES.get(dataset.task)
    if allowed_shapes is None:
        return []

    annotations = list(
        session.scalars(
            select(Annotation)
            .join(DatasetSample, Annotation.dataset_sample_id == DatasetSample.id)
            .where(DatasetSample.dataset_id == dataset_id)
        ).all()
    )
    errors: list[dict[str, str]] = []
    for annotation in annotations:
        results = _annotation_results(annotation.internal_payload)
        if not results and dataset.task in ANNOTATION_REQUIRED_TASKS:
            errors.append(
                {
                    "code": "ANNOTATION_SHAPE_MISMATCH",
                    "message": f"{dataset.task} annotation has no task-compatible results",
                }
            )
            break
        shapes = {str(result.get("shape")) for result in results if result.get("shape") is not None}
        unsupported = shapes - allowed_shapes
        if unsupported:
            errors.append(
                {
                    "code": "ANNOTATION_SHAPE_MISMATCH",
                    "message": (
                        f"{dataset.task} annotation contains unsupported shape(s): "
                        f"{', '.join(sorted(unsupported))}"
                    ),
                }
            )
            break
        if dataset.task == "pose" and results:
            if "rectangle" not in shapes or "keypoints" not in shapes:
                errors.append(
                    {
                        "code": "ANNOTATION_SHAPE_MISMATCH",
                        "message": "pose annotation requires rectangle and keypoints results",
                    }
                )
                break
    return errors


def _annotation_results(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("annotations"), list):
        results: list[dict[str, Any]] = []
        for annotation in payload["annotations"]:
            if not isinstance(annotation, dict) or not isinstance(annotation.get("results"), list):
                continue
            results.extend(result for result in annotation["results"] if isinstance(result, dict))
        return results
    if "shape" in payload:
        return [payload]
    return []
