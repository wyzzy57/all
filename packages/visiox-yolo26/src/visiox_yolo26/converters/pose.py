from typing import Any

from visiox_db.models import Dataset, DatasetSample
from visiox_yolo26.converters.internal_schema import (
    ClassMap,
    normalized_percent,
    rectangle,
    require_percent,
    result_error,
    yolo_rectangle_values,
)

COCO17_KEYPOINTS = (
    "nose",
    "left_eye",
    "right_eye",
    "left_ear",
    "right_ear",
    "left_shoulder",
    "right_shoulder",
    "left_elbow",
    "right_elbow",
    "left_wrist",
    "right_wrist",
    "left_hip",
    "right_hip",
    "left_knee",
    "right_knee",
    "left_ankle",
    "right_ankle",
)


def convert_sample(dataset: Dataset, sample: DatasetSample, results: list[dict[str, Any]], class_map: ClassMap) -> list[str]:
    bbox_result = next((result for result in results if result.get("shape") == "rectangle"), None)
    if bbox_result is None:
        raise result_error("pose sample requires rectangle bbox", dataset, sample, results[0])
    class_id = class_map.resolve(bbox_result, dataset.id, sample.id)
    values = [str(class_id), *yolo_rectangle_values(rectangle(bbox_result, dataset, sample))]

    keyed_results: dict[str, dict[str, Any]] = {}
    sequential_results = []
    for result in results:
        if result.get("shape") == "keypoints":
            keypoint_name = result.get("keypoint_name")
            if isinstance(keypoint_name, str):
                keyed_results[keypoint_name] = result
            else:
                sequential_results.append(result)
        elif result is not bbox_result:
            raise result_error(f"unsupported pose shape: {result.get('shape')}", dataset, sample, result)

    for index, keypoint_name in enumerate(COCO17_KEYPOINTS):
        result = keyed_results.get(keypoint_name)
        if result is None and index < len(sequential_results):
            result = sequential_results[index]
        if result is None:
            values.extend(["0", "0", "0"])
            continue
        x = require_percent(result.get("x"), "x", dataset, sample, result)
        y = require_percent(result.get("y"), "y", dataset, sample, result)
        values.extend([normalized_percent(x), normalized_percent(y), "2"])
    return [" ".join(values)]
