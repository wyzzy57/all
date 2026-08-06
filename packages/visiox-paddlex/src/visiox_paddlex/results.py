from __future__ import annotations

import json
import math
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError


_COCO_METRICS = {
    "bbox_mAP": "detection.map_50_95",
    "bbox_mAP_50": "detection.map_50",
    "bbox_mAP_75": "detection.map_75",
    "bbox_mAP_s": "detection.map_small",
    "bbox_mAP_m": "detection.map_medium",
    "bbox_mAP_l": "detection.map_large",
    "bbox_AR_1": "detection.mar_1",
    "bbox_AR_10": "detection.mar_10",
    "bbox_AR_100": "detection.mar_100",
}
_MAX_RESULT_BYTES = 1024 * 1024
_MAX_ANNOTATED_IMAGE_BYTES = 25 * 1024 * 1024
_MAX_BOXES = 10_000


class PaddleXResultError(ValueError):
    pass


def read_evaluation_result(path: Path) -> dict[str, float]:
    payload = _read_object(path)
    source = payload.get("metrics", payload)
    if not isinstance(source, dict):
        raise PaddleXResultError("PaddleX evaluation metrics are invalid")
    metrics: dict[str, float] = {}
    for raw_name, canonical_name in _COCO_METRICS.items():
        value = source.get(raw_name)
        if isinstance(value, bool) or not isinstance(value, int | float):
            continue
        metric = float(value)
        if not math.isfinite(metric) or not 0.0 <= metric <= 1.0:
            raise PaddleXResultError("PaddleX evaluation metric is outside [0, 1]")
        metrics[canonical_name] = metric
    if not metrics:
        raise PaddleXResultError("PaddleX evaluation returned no COCO metrics")
    return metrics


def read_inference_result(
    path: Path, output_dir: Path
) -> tuple[list[dict[str, Any]], bytes]:
    payload = _read_object(path)
    raw_boxes = payload.get("boxes")
    image_name = payload.get("annotated_image")
    if not isinstance(raw_boxes, list) or not isinstance(image_name, str):
        raise PaddleXResultError("PaddleX inference result is invalid")
    if len(raw_boxes) > _MAX_BOXES:
        raise PaddleXResultError("PaddleX inference returned too many boxes")
    image_path = (output_dir / image_name).resolve()
    if output_dir.resolve() not in image_path.parents or not image_path.is_file():
        raise PaddleXResultError("PaddleX annotated image is unavailable")
    if image_path.stat().st_size > _MAX_ANNOTATED_IMAGE_BYTES:
        raise PaddleXResultError("PaddleX annotated image is too large")
    image_bytes = image_path.read_bytes()
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            image.verify()
        with Image.open(BytesIO(image_bytes)) as image:
            image.load()
            png_buffer = BytesIO()
            image.save(png_buffer, format="PNG")
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise PaddleXResultError("PaddleX annotated image is invalid") from exc
    predictions = [_normalize_box(item) for item in raw_boxes]
    return predictions, png_buffer.getvalue()


def _read_object(path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > _MAX_RESULT_BYTES:
            raise PaddleXResultError("PaddleX result file is too large")
    except OSError as exc:
        raise PaddleXResultError("PaddleX result file is unavailable or invalid") from exc
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PaddleXResultError("PaddleX result file is unavailable or invalid") from exc
    if not isinstance(payload, dict):
        raise PaddleXResultError("PaddleX result payload must be an object")
    return payload


def _normalize_box(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise PaddleXResultError("PaddleX prediction box is invalid")
    label = raw.get("label", raw.get("class_name"))
    score = raw.get("score")
    coordinates = raw.get("coordinate", raw.get("box"))
    if (
        not isinstance(label, str)
        or isinstance(score, bool)
        or not isinstance(score, int | float)
        or not isinstance(coordinates, list)
        or len(coordinates) != 4
        or any(isinstance(value, bool) or not isinstance(value, int | float) for value in coordinates)
    ):
        raise PaddleXResultError("PaddleX prediction box is invalid")
    numeric_score = float(score)
    numeric_coordinates = [float(value) for value in coordinates]
    if (
        not math.isfinite(numeric_score)
        or not 0.0 <= numeric_score <= 1.0
        or any(not math.isfinite(value) for value in numeric_coordinates)
    ):
        raise PaddleXResultError("PaddleX prediction box contains invalid numeric values")
    return {
        "label": label,
        "score": round(numeric_score, 6),
        "box": [round(value, 2) for value in numeric_coordinates],
    }
