from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from visiox_db.models import Annotation, Dataset, DatasetSample
from visiox_yolo26.datasets.analysis import analyze_dataset


SUPPORTED_TASKS = {"detect", "segment", "semantic", "pose", "obb", "classify"}
ANNOTATION_REQUIRED_TASKS = {"detect", "segment", "semantic", "pose", "obb"}


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

    return {
        "valid": not errors,
        "task": dataset.task,
        "errors": errors,
        "warnings": warnings,
        "analysis": analysis,
    }
