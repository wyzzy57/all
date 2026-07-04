from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class NormalizedLabelStudioTask:
    sample_id: str
    payload: dict[str, Any]


def normalize_label_studio_task(task_payload: dict[str, Any]) -> NormalizedLabelStudioTask:
    sample_id = _sample_id_from_task(task_payload)
    annotations = []
    for annotation in task_payload.get("annotations", []):
        results = [_normalize_result(result) for result in annotation.get("result", [])]
        annotations.append(
            {
                "source_annotation_id": annotation.get("id"),
                "results": [result for result in results if result is not None],
            }
        )
    return NormalizedLabelStudioTask(
        sample_id=sample_id,
        payload={
            "source": "label_studio",
            "source_task_id": task_payload.get("id"),
            "annotations": annotations,
        },
    )


def _sample_id_from_task(task_payload: dict[str, Any]) -> str:
    data = task_payload.get("data") or {}
    sample_id = data.get("visiox_sample_id")
    if not sample_id:
        raise ValueError("Label Studio task is missing data.visiox_sample_id")
    return str(sample_id)


def _normalize_result(result: dict[str, Any]) -> dict[str, Any] | None:
    value = result.get("value") or {}
    result_type = result.get("type")
    base: dict[str, Any] = {"source_result_id": result.get("id")}

    if result_type == "rectanglelabels":
        return base | {
            "class_name": _first(value.get("rectanglelabels")),
            "shape": "rectangle",
            "x": value.get("x"),
            "y": value.get("y"),
            "width": value.get("width"),
            "height": value.get("height"),
        }
    if result_type == "polygonlabels":
        return base | {
            "class_name": _first(value.get("polygonlabels")),
            "shape": "polygon",
            "points": value.get("points", []),
        }
    if result_type == "brushlabels":
        return base | {
            "class_name": _first(value.get("brushlabels")),
            "shape": "brush",
            "rle": value.get("rle"),
            "format": value.get("format"),
        }
    if result_type == "keypointlabels":
        return base | {
            "class_name": _first(value.get("keypointlabels")),
            "shape": "keypoints",
            "x": value.get("x"),
            "y": value.get("y"),
        }
    if result_type == "choices":
        return base | {
            "class_name": _first(value.get("choices")),
            "shape": "classification",
        }
    return base | {
        "shape": "unknown",
        "result_type": result_type,
        "value": value,
    }


def _first(value: Any) -> Any:
    if isinstance(value, list) and value:
        return value[0]
    return value
