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

    split_by_sample = {sample.id: sample.split or "unassigned" for sample in samples}
    annotations_by_sample: dict[str, int] = defaultdict(int)
    class_distribution: Counter[str] = Counter()
    class_distribution_by_split: dict[str, Counter[str]] = defaultdict(Counter)
    for annotation in annotations:
        annotations_by_sample[annotation.dataset_sample_id] += 1
        for class_key in _annotation_class_keys(annotation.internal_payload):
            class_distribution[class_key] += 1
            class_distribution_by_split[split_by_sample.get(annotation.dataset_sample_id, "unassigned")][class_key] += 1

    image_sizes: Counter[str] = Counter()
    split_distribution: Counter[str] = Counter()
    invalid_samples: list[str] = []
    for sample in samples:
        split_distribution[sample.split or "unassigned"] += 1
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
        "class_distribution_by_split": {
            split: dict(sorted(distribution.items()))
            for split, distribution in sorted(class_distribution_by_split.items())
        },
        "annotation_count": len(annotations),
        "image_size_distribution": {
            "total_with_dimensions": sum(image_sizes.values()),
            "by_size": dict(sorted(image_sizes.items())),
        },
        "split_distribution": dict(sorted(split_distribution.items())),
        "empty_annotation_ratio": empty_annotation_ratio,
        "invalid_samples": invalid_samples,
    }


def _annotation_class_keys(payload: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    if "class_name" in payload and payload["class_name"] is not None:
        keys.append(str(payload["class_name"]))
    if "class_id" in payload and payload["class_id"] is not None:
        keys.append(str(payload["class_id"]))
    if isinstance(payload.get("annotations"), list):
        for annotation in payload["annotations"]:
            if not isinstance(annotation, dict) or not isinstance(annotation.get("results"), list):
                continue
            for result in annotation["results"]:
                if not isinstance(result, dict):
                    continue
                if result.get("class_name") is not None:
                    keys.append(str(result["class_name"]))
                elif result.get("class_id") is not None:
                    keys.append(str(result["class_id"]))
    return keys
