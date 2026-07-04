from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from visiox_db.models import Annotation, Dataset, DatasetSample
from visiox_storage.client import ObjectStorageClient
from visiox_yolo26.converters import classify, detect, obb, pose, segment, semantic
from visiox_yolo26.converters.internal_schema import (
    ConversionError,
    ConversionWarning,
    flatten_results,
    parse_class_map,
    parse_storage_uri,
    require_dimensions,
    sample_stem,
    source_image_name,
)
from visiox_yolo26.tasks import YOLO26_TASKS


@dataclass
class ConversionReport:
    dataset_id: str
    task: str
    output_dir: Path
    written_files: list[Path] = field(default_factory=list)
    warnings: list[ConversionWarning] = field(default_factory=list)
    sample_count: int = 0
    annotation_count: int = 0


def export_yolo26_dataset(
    session: Session,
    storage: ObjectStorageClient,
    dataset_id: str,
    output_dir: Path,
) -> ConversionReport:
    dataset = session.get(Dataset, dataset_id)
    if dataset is None:
        raise ConversionError(f"dataset not found: {dataset_id}", dataset_id=dataset_id)
    if dataset.task not in YOLO26_TASKS:
        raise ConversionError(f"unsupported YOLO26 task: {dataset.task}", dataset_id=dataset_id)

    output_dir.mkdir(parents=True, exist_ok=True)
    class_map = parse_class_map(dataset.class_schema or {})
    report = ConversionReport(dataset_id=dataset.id, task=dataset.task, output_dir=output_dir)

    samples = list(
        session.scalars(
            select(DatasetSample)
            .where(DatasetSample.dataset_id == dataset.id)
            .order_by(DatasetSample.split, DatasetSample.id)
        )
    )
    for sample in samples:
        split = sample.split or "train"
        annotations = list(
            session.scalars(
                select(Annotation)
                .where(Annotation.dataset_sample_id == sample.id)
                .order_by(Annotation.created_at, Annotation.id)
            )
        )
        results = flatten_results(dataset, sample, annotations)
        if dataset.task != "classify":
            require_dimensions(dataset, sample, results[0])

        if dataset.task == "classify":
            _export_classify_sample(storage, dataset, sample, results, class_map, split, output_dir, report)
        else:
            image_path = _copy_image(storage, sample, output_dir / "images" / split / source_image_name(sample))
            report.written_files.append(image_path)
            label_stem = sample_stem(sample)
            if dataset.task == "detect":
                lines = detect.convert_sample(dataset, sample, results, class_map)
                _write_lines(output_dir / "labels" / split / f"{label_stem}.txt", lines, report)
            elif dataset.task == "segment":
                lines = segment.convert_sample(dataset, sample, results, class_map)
                _write_lines(output_dir / "labels" / split / f"{label_stem}.txt", lines, report)
            elif dataset.task == "obb":
                lines = obb.convert_sample(dataset, sample, results, class_map)
                _write_lines(output_dir / "labels" / split / f"{label_stem}.txt", lines, report)
            elif dataset.task == "pose":
                lines = pose.convert_sample(dataset, sample, results, class_map)
                _write_lines(output_dir / "labels" / split / f"{label_stem}.txt", lines, report)
            elif dataset.task == "semantic":
                mask, warnings = semantic.convert_sample(dataset, sample, results, class_map)
                mask_path = output_dir / "masks" / split / f"{label_stem}.png"
                mask_path.parent.mkdir(parents=True, exist_ok=True)
                mask.save(mask_path)
                report.written_files.append(mask_path)
                report.warnings.extend(warnings)

        report.sample_count += 1
        report.annotation_count += len(results)

    _write_data_yaml(output_dir / "data.yaml", dataset.task, class_map.names, report)
    return report


def _export_classify_sample(
    storage: ObjectStorageClient,
    dataset: Dataset,
    sample: DatasetSample,
    results: list[dict[str, object]],
    class_map,
    split: str,
    output_dir: Path,
    report: ConversionReport,
) -> None:
    class_name = classify.class_for_sample(dataset, sample, results, class_map)
    image_path = _copy_image(storage, sample, output_dir / split / class_name / source_image_name(sample))
    report.written_files.append(image_path)


def _copy_image(storage: ObjectStorageClient, sample: DatasetSample, destination: Path) -> Path:
    bucket, object_name = parse_storage_uri(sample.file_uri)
    return storage.get_file(bucket, object_name, destination)


def _write_lines(path: Path, lines: list[str], report: ConversionReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    report.written_files.append(path)


def _write_data_yaml(path: Path, task: str, names: tuple[str, ...], report: ConversionReport) -> None:
    if task == "classify":
        train_path = "train"
        val_path = "val"
        test_path = "test"
    else:
        train_path = "images/train"
        val_path = "images/val"
        test_path = "images/test"
    lines = [
        "path: .",
        f"train: {train_path}",
        f"val: {val_path}",
        f"test: {test_path}",
        "names:",
    ]
    lines.extend(f"  {index}: {name}" for index, name in enumerate(names))
    lines.append(f"task: {task}")
    if task == "semantic":
        lines.append("mask: masks")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    report.written_files.append(path)
