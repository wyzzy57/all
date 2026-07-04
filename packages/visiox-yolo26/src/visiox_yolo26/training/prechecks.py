from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from visiox_db.models import Annotation, BaseModel, Dataset, DatasetSample
from visiox_yolo26.tasks import YOLO26_SCALES, YOLO26_TASKS


class TrainingPrecheckError(ValueError):
    pass


@dataclass(frozen=True)
class TrainingResources:
    base_model: BaseModel
    dataset: Dataset


def validate_training_resources(
    session: Session,
    *,
    task: str,
    scale: str,
    base_model_id: str,
    dataset_id: str,
) -> TrainingResources:
    if task not in YOLO26_TASKS:
        raise TrainingPrecheckError(f"unsupported YOLO26 task: {task}")
    if scale not in YOLO26_SCALES:
        raise TrainingPrecheckError(f"unsupported YOLO26 scale: {scale}")

    base_model = session.get(BaseModel, base_model_id)
    if base_model is None:
        raise TrainingPrecheckError("Base model not found")
    if base_model.status != "ready":
        raise TrainingPrecheckError(f"Base model is not ready: {base_model.status}")
    if not base_model.local_uri:
        raise TrainingPrecheckError("Base model artifact is missing")
    if base_model.task != task or base_model.scale != scale:
        raise TrainingPrecheckError("Base model task or scale does not match pipeline")

    dataset = session.get(Dataset, dataset_id)
    if dataset is None:
        raise TrainingPrecheckError("Dataset not found")
    if dataset.status != "validated":
        raise TrainingPrecheckError(f"Dataset is not validated: {dataset.status}")
    if dataset.task != task:
        raise TrainingPrecheckError("Dataset task does not match pipeline")
    if dataset.sample_count <= 0:
        raise TrainingPrecheckError("Dataset has no samples")
    if dataset.annotation_count <= 0:
        raise TrainingPrecheckError("Dataset has no annotations")

    sample_count = session.scalar(
        select(func.count()).select_from(DatasetSample).where(DatasetSample.dataset_id == dataset.id)
    ) or 0
    annotation_count = (
        session.scalar(
            select(func.count())
            .select_from(Annotation)
            .join(DatasetSample, Annotation.dataset_sample_id == DatasetSample.id)
            .where(
                DatasetSample.dataset_id == dataset.id,
                Annotation.validation_status == "valid",
            )
        )
        or 0
    )
    if sample_count <= 0:
        raise TrainingPrecheckError("Dataset samples are missing")
    if annotation_count <= 0:
        raise TrainingPrecheckError("Dataset valid annotations are missing")

    return TrainingResources(base_model=base_model, dataset=dataset)
