from typing import Any

from visiox_db.models import Dataset, DatasetSample
from visiox_yolo26.converters.internal_schema import (
    ClassMap,
    normalized_percent,
    polygon_points,
    rectangle,
    result_error,
)


def convert_sample(dataset: Dataset, sample: DatasetSample, results: list[dict[str, Any]], class_map: ClassMap) -> list[str]:
    lines = []
    for result in results:
        class_id = class_map.resolve(result, dataset.id, sample.id)
        if result.get("shape") == "polygon":
            points = polygon_points(result, dataset, sample)
            if len(points) != 4:
                raise result_error("obb polygon must have exactly four points", dataset, sample, result)
        elif result.get("shape") == "rectangle":
            x, y, width, height = rectangle(result, dataset, sample)
            points = [(x, y), (x + width, y), (x + width, y + height), (x, y + height)]
        else:
            raise result_error(f"unsupported obb shape: {result.get('shape')}", dataset, sample, result)
        values = [str(class_id)]
        for x, y in points:
            values.extend([normalized_percent(x), normalized_percent(y)])
        lines.append(" ".join(values))
    return lines
