from typing import Any

from visiox_db.models import Dataset, DatasetSample
from visiox_yolo26.converters.internal_schema import ClassMap, result_error


def class_id_for_sample(dataset: Dataset, sample: DatasetSample, results: list[dict[str, Any]], class_map: ClassMap) -> int:
    classifications = [result for result in results if result.get("shape") == "classification"]
    if len(classifications) != 1:
        source = results[0] if results else {}
        raise result_error("classify sample requires exactly one classification", dataset, sample, source)
    return class_map.resolve(classifications[0], dataset.id, sample.id)
