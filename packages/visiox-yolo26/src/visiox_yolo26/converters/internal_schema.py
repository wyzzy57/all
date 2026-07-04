from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from visiox_db.models import Dataset, DatasetSample


class ConversionError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        dataset_id: str | None = None,
        sample_id: str | None = None,
        source_result_id: str | None = None,
    ) -> None:
        parts = [message]
        if dataset_id is not None:
            parts.append(f"dataset={dataset_id}")
        if sample_id is not None:
            parts.append(f"sample={sample_id}")
        if source_result_id is not None:
            parts.append(f"source_result_id={source_result_id}")
        super().__init__("; ".join(parts))
        self.dataset_id = dataset_id
        self.sample_id = sample_id
        self.source_result_id = source_result_id


@dataclass(frozen=True)
class ConversionWarning:
    message: str
    dataset_id: str
    sample_id: str
    source_result_id: str | None = None


@dataclass(frozen=True)
class ClassMap:
    names: tuple[str, ...]
    name_to_id: dict[str, int]

    def resolve(self, result: dict[str, Any], dataset_id: str, sample_id: str) -> int:
        source_result_id = _source_result_id(result)
        if result.get("class_id") is not None:
            class_id = int(result["class_id"])
            if 0 <= class_id < len(self.names):
                return class_id
            raise ConversionError(
                f"unknown class id: {class_id}",
                dataset_id=dataset_id,
                sample_id=sample_id,
                source_result_id=source_result_id,
            )
        class_name = result.get("class_name")
        if isinstance(class_name, str) and class_name in self.name_to_id:
            return self.name_to_id[class_name]
        raise ConversionError(
            f"unknown class: {class_name}",
            dataset_id=dataset_id,
            sample_id=sample_id,
            source_result_id=source_result_id,
        )

    def class_name(self, class_id: int) -> str:
        return self.names[class_id]


def parse_class_map(class_schema: dict[str, Any]) -> ClassMap:
    names_payload = class_schema.get("names")
    if isinstance(names_payload, dict):
        names = tuple(str(names_payload[str(index)] if str(index) in names_payload else names_payload[index]) for index in range(len(names_payload)))
    elif isinstance(names_payload, list):
        names = tuple(str(name) for name in names_payload)
    else:
        classes_payload = class_schema.get("classes")
        if isinstance(classes_payload, list):
            names = tuple(str(item["name"] if isinstance(item, dict) and "name" in item else item) for item in classes_payload)
        else:
            names = ()
    if not names:
        raise ConversionError("class schema is missing names")
    return ClassMap(names=names, name_to_id={name: index for index, name in enumerate(names)})


def flatten_results(dataset: Dataset, sample: DatasetSample, annotations: list[Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for annotation in annotations:
        payload = annotation.internal_payload or {}
        for annotation_payload in payload.get("annotations", []):
            for result in annotation_payload.get("results", []):
                if not isinstance(result, dict):
                    continue
                if result.get("shape") == "brush" and result.get("rle") is not None:
                    raise result_error("brush rle is not supported", dataset, sample, result)
                results.append(result)
    if not results:
        raise ConversionError("missing annotation", dataset_id=dataset.id, sample_id=sample.id)
    return results


def require_dimensions(
    dataset: Dataset,
    sample: DatasetSample,
    result: dict[str, Any] | None = None,
) -> tuple[int, int]:
    if sample.width is None:
        if result is not None:
            raise result_error("missing width", dataset, sample, result)
        raise ConversionError("missing width", dataset_id=dataset.id, sample_id=sample.id)
    if sample.height is None:
        if result is not None:
            raise result_error("missing height", dataset, sample, result)
        raise ConversionError("missing height", dataset_id=dataset.id, sample_id=sample.id)
    return sample.width, sample.height


def require_percent(value: Any, name: str, dataset: Dataset, sample: DatasetSample, result: dict[str, Any]) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise result_error(f"invalid coordinate {name}: {value}", dataset, sample, result) from exc
    if number < 0 or number > 100:
        raise result_error(f"invalid coordinate {name}: {value}", dataset, sample, result)
    return number


def rectangle(result: dict[str, Any], dataset: Dataset, sample: DatasetSample) -> tuple[float, float, float, float]:
    x = require_percent(result.get("x"), "x", dataset, sample, result)
    y = require_percent(result.get("y"), "y", dataset, sample, result)
    width = require_percent(result.get("width"), "width", dataset, sample, result)
    height = require_percent(result.get("height"), "height", dataset, sample, result)
    if x + width > 100 or y + height > 100 or width <= 0 or height <= 0:
        raise result_error("invalid coordinate rectangle bounds", dataset, sample, result)
    return x, y, width, height


def polygon_points(result: dict[str, Any], dataset: Dataset, sample: DatasetSample) -> list[tuple[float, float]]:
    points_payload = result.get("points")
    if not isinstance(points_payload, list) or len(points_payload) < 3:
        raise result_error("invalid polygon points", dataset, sample, result)
    points = []
    for index, point in enumerate(points_payload):
        if not isinstance(point, list | tuple) or len(point) != 2:
            raise result_error(f"invalid polygon point {index}", dataset, sample, result)
        points.append(
            (
                require_percent(point[0], f"points[{index}].x", dataset, sample, result),
                require_percent(point[1], f"points[{index}].y", dataset, sample, result),
            )
        )
    return points


def fmt(value: float) -> str:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text if text and text != "-0" else "0"


def normalized_percent(value: float) -> str:
    return fmt(value / 100)


def yolo_rectangle_values(rect: tuple[float, float, float, float]) -> list[str]:
    x, y, width, height = rect
    return [
        normalized_percent(x + width / 2),
        normalized_percent(y + height / 2),
        normalized_percent(width),
        normalized_percent(height),
    ]


def source_image_name(sample: DatasetSample) -> str:
    return Path(parse_storage_uri(sample.file_uri)[1]).name


def sample_stem(sample: DatasetSample) -> str:
    return Path(source_image_name(sample)).stem


def parse_storage_uri(uri: str) -> tuple[str, str]:
    marker = "://"
    if marker not in uri:
        raise ConversionError(f"unsupported storage uri: {uri}")
    remainder = uri.split(marker, 1)[1]
    bucket, separator, object_name = remainder.partition("/")
    if not separator or not bucket or not object_name:
        raise ConversionError(f"unsupported storage uri: {uri}")
    return bucket, object_name


def result_error(message: str, dataset: Dataset, sample: DatasetSample, result: dict[str, Any]) -> ConversionError:
    return ConversionError(
        message,
        dataset_id=dataset.id,
        sample_id=sample.id,
        source_result_id=_source_result_id(result),
    )


def _source_result_id(result: dict[str, Any]) -> str | None:
    value = result.get("source_result_id")
    return str(value) if value is not None else None
