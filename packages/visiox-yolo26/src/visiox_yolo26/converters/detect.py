from typing import Any

from visiox_db.models import Dataset, DatasetSample
from visiox_yolo26.converters.internal_schema import (
    ClassMap,
    rectangle,
    result_error,
    yolo_rectangle_values,
)


def convert_sample(dataset: Dataset, sample: DatasetSample, results: list[dict[str, Any]], class_map: ClassMap) -> list[str]:
    lines = []
    for result in results:
        if result.get("shape") != "rectangle":
            raise result_error(f"unsupported detect shape: {result.get('shape')}", dataset, sample, result)
        class_id = class_map.resolve(result, dataset.id, sample.id)
        lines.append(" ".join([str(class_id), *yolo_rectangle_values(rectangle(result, dataset, sample))]))
    return lines
