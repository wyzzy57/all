from typing import Any

from visiox_db.models import Dataset, DatasetSample
from visiox_yolo26.converters.internal_schema import ClassMap, normalized_percent, polygon_points, result_error


def convert_sample(dataset: Dataset, sample: DatasetSample, results: list[dict[str, Any]], class_map: ClassMap) -> list[str]:
    lines = []
    for result in results:
        if result.get("shape") != "polygon":
            raise result_error(f"unsupported segment shape: {result.get('shape')}", dataset, sample, result)
        class_id = class_map.resolve(result, dataset.id, sample.id)
        values = [str(class_id)]
        for x, y in polygon_points(result, dataset, sample):
            values.extend([normalized_percent(x), normalized_percent(y)])
        lines.append(" ".join(values))
    return lines
