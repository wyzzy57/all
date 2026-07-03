from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from visiox_db.models import Annotation, Dataset, DatasetSample


def analyze_dataset(session: Session, dataset_id: str) -> dict[str, Any]:
    dataset = session.get(Dataset, dataset_id)
    if dataset is None:
        raise ValueError(f"Dataset not found: {dataset_id}")

    samples = list(session.scalars(select(DatasetSample).where(DatasetSample.dataset_id == dataset_id)).all())
    sample_ids = [sample.id for sample in samples]
    annotations = []
    if sample_ids:
        annotations = list(
            session.scalars(select(Annotation).where(Annotation.dataset_sample_id.in_(sample_ids))).all()
        )

    annotations_by_sample: dict[str, int] = defaultdict(int)
    class_distribution: Counter[str] = Counter()
    for annotation in annotations:
        annotations_by_sample[annotation.dataset_sample_id] += 1
        class_key = _annotation_class_key(annotation.internal_payload)
        if class_key is not None:
            class_distribution[class_key] += 1

    image_sizes: Counter[str] = Counter()
    invalid_samples: list[str] = []
    for sample in samples:
        if sample.width is not None and sample.height is not None:
            image_sizes[f"{sample.width}x{sample.height}"] += 1
        if not sample.file_uri or not sample.checksum or sample.width is None or sample.height is None:
            invalid_samples.append(sample.id)

    sample_count = len(samples)
    empty_count = sum(1 for sample in samples if annotations_by_sample[sample.id] == 0)
    empty_annotation_ratio = (empty_count / sample_count) if sample_count else 0.0

    return {
        "sample_count": sample_count,
        "class_distribution": dict(sorted(class_distribution.items())),
        "annotation_count": len(annotations),
        "image_size_distribution": {
            "total_with_dimensions": sum(image_sizes.values()),
            "by_size": dict(sorted(image_sizes.items())),
        },
        "empty_annotation_ratio": empty_annotation_ratio,
        "invalid_samples": invalid_samples,
    }


def _annotation_class_key(payload: dict[str, Any]) -> str | None:
    if "class_name" in payload and payload["class_name"] is not None:
        return str(payload["class_name"])
    if "class_id" in payload and payload["class_id"] is not None:
        return str(payload["class_id"])
    return None
