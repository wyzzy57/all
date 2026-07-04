from typing import Any

from visiox_db.models import Dataset, DatasetSample
from visiox_yolo26.converters.internal_schema import ClassMap, result_error


def class_for_sample(dataset: Dataset, sample: DatasetSample, results: list[dict[str, Any]], class_map: ClassMap) -> str:
    classifications = [result for result in results if result.get("shape") == "classification"]
    if len(classifications) != 1:
        source = results[0] if results else {}
        raise result_error("classify sample requires exactly one classification", dataset, sample, source)
    class_id = class_map.resolve(classifications[0], dataset.id, sample.id)
    return class_map.class_name(class_id)
