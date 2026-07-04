from __future__ import annotations

import time
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Protocol

from PIL import Image

from visiox_yolo26_inference.config import InferenceConfig, validate_model_task_match


@dataclass(slots=True)
class PredictionResult:
    task: str
    predictions: list[dict[str, Any]]
    latency_ms: float
    image: dict[str, Any]


class Predictor(Protocol):
    def predict_image(self, image_bytes: bytes, metadata: dict[str, Any] | None = None) -> PredictionResult:
        pass


class DeterministicPredictor:
    def __init__(self, config: InferenceConfig) -> None:
        self.config = config

    def predict_image(self, image_bytes: bytes, metadata: dict[str, Any] | None = None) -> PredictionResult:
        start = time.perf_counter()
        with Image.open(BytesIO(image_bytes)) as image:
            width, height = image.size
            mode = image.mode

        image_info = {
            "width": width,
            "height": height,
            "mode": mode,
            "metadata": dict(metadata or {}),
        }
        predictions = self._predictions_for_task(width=width, height=height)
        latency_ms = (time.perf_counter() - start) * 1000
        return PredictionResult(
            task=self.config.task,
            predictions=predictions,
            latency_ms=round(latency_ms, 3),
            image=image_info,
        )

    def _predictions_for_task(self, *, width: int, height: int) -> list[dict[str, Any]]:
        label = self._class_name(0)
        if self.config.task == "detect":
            return [
                {
                    "class_id": 0,
                    "label": label,
                    "confidence": self.config.confidence,
                    "bbox": _center_box(width, height),
                }
            ]
        if self.config.task == "segment":
            return [
                {
                    "class_id": 0,
                    "label": label,
                    "confidence": self.config.confidence,
                    "bbox": _center_box(width, height),
                    "polygon": _center_polygon(width, height),
                }
            ]
        if self.config.task == "semantic":
            return [
                {
                    "class_id": 0,
                    "label": label,
                    "confidence": self.config.confidence,
                    "mask": {
                        "width": width,
                        "height": height,
                        "rle": "deterministic-background-foreground",
                    },
                }
            ]
        if self.config.task == "pose":
            return [
                {
                    "class_id": 0,
                    "label": label,
                    "confidence": self.config.confidence,
                    "bbox": _center_box(width, height),
                    "keypoints": _pose_keypoints(width, height),
                }
            ]
        if self.config.task == "obb":
            return [
                {
                    "class_id": 0,
                    "label": label,
                    "confidence": self.config.confidence,
                    "obb": _oriented_box(width, height),
                    "angle": 0.0,
                }
            ]
        if self.config.task == "classify":
            confidence = max(self.config.confidence, 0.9)
            return [
                {
                    "class_id": 0,
                    "label": label,
                    "confidence": confidence,
                    "topk": [
                        {"class_id": 0, "label": label, "confidence": confidence},
                        {"class_id": 1, "label": self._class_name(1), "confidence": 0.1},
                    ],
                }
            ]
        raise ValueError(f"unsupported YOLO26 task: {self.config.task}")

    def _class_name(self, class_id: int) -> str:
        if class_id < len(self.config.class_names):
            return self.config.class_names[class_id]
        return f"class_{class_id}"


def load_predictor(config: InferenceConfig) -> Predictor:
    validate_model_task_match(config)
    return DeterministicPredictor(config)


def _center_box(width: int, height: int) -> dict[str, float]:
    x1 = width * 0.25
    y1 = height * 0.25
    x2 = width * 0.75
    y2 = height * 0.75
    return {"x1": round(x1, 3), "y1": round(y1, 3), "x2": round(x2, 3), "y2": round(y2, 3)}


def _center_polygon(width: int, height: int) -> list[dict[str, float]]:
    box = _center_box(width, height)
    return [
        {"x": box["x1"], "y": box["y1"]},
        {"x": box["x2"], "y": box["y1"]},
        {"x": box["x2"], "y": box["y2"]},
        {"x": box["x1"], "y": box["y2"]},
    ]


def _pose_keypoints(width: int, height: int) -> list[dict[str, float | str]]:
    return [
        {"name": "center", "x": round(width * 0.5, 3), "y": round(height * 0.5, 3), "confidence": 0.99},
        {"name": "top", "x": round(width * 0.5, 3), "y": round(height * 0.25, 3), "confidence": 0.95},
        {"name": "bottom", "x": round(width * 0.5, 3), "y": round(height * 0.75, 3), "confidence": 0.95},
    ]


def _oriented_box(width: int, height: int) -> list[dict[str, float]]:
    return _center_polygon(width, height)
