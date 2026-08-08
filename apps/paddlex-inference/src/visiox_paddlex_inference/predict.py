from __future__ import annotations

import base64
from dataclasses import dataclass
from io import BytesIO
import json
import math
import time
from typing import Any, Protocol

from PIL import Image
import yaml

from visiox_paddlex_inference.config import InferenceConfig

MAX_PREDICTIONS = 1000
MAX_ANNOTATED_IMAGE_BYTES = 10 * 1024 * 1024
MAX_LABEL_CHARS = 256


@dataclass(slots=True)
class PredictionResult:
    task: str
    predictions: list[dict[str, Any]]
    latency_ms: float
    image: dict[str, Any]
    result_image: str | None = None


class Predictor(Protocol):
    runtime_metadata: dict[str, str]

    def predict_image(
        self, image_bytes: bytes, metadata: dict[str, Any] | None = None
    ) -> PredictionResult: ...


class PaddleXPredictor:
    def __init__(self, config: InferenceConfig) -> None:
        import paddlex

        self.config = config
        self.model = paddlex.create_model(**_create_model_kwargs(config))
        self.runtime_metadata = {
            "resolved_backend": config.backend,
            "resolved_precision": (
                config.precision if config.backend == "paddlex_hpi_tensorrt" else "fp32"
            ),
        }

    def predict_image(
        self, image_bytes: bytes, metadata: dict[str, Any] | None = None
    ) -> PredictionResult:
        start = time.perf_counter()
        with Image.open(BytesIO(image_bytes)) as opened:
            image = opened.convert("RGB")
            width, height = image.size
        results = list(
            self.model.predict(input=image, threshold=self.config.confidence)
        )
        if not results:
            raise ValueError("PaddleX returned no prediction result")
        framework_result = results[0]
        raw = framework_result.json
        payload = raw if isinstance(raw, dict) else json.loads(raw)
        boxes = payload.get("res", payload).get("boxes", [])
        if not isinstance(boxes, list):
            raise ValueError("PaddleX prediction boxes are invalid")
        if len(boxes) > MAX_PREDICTIONS:
            raise ValueError("PaddleX returned too many predictions")
        predictions = [_normalize_box(box, self.config.class_names) for box in boxes]
        return PredictionResult(
            task="detect",
            predictions=predictions,
            latency_ms=round((time.perf_counter() - start) * 1000, 3),
            image={
                "width": width,
                "height": height,
                "mode": "RGB",
                "metadata": dict(metadata or {}),
            },
            result_image=_encode_result_image(getattr(framework_result, "img", None)),
        )


class DeterministicPredictor:
    def __init__(self, config: InferenceConfig) -> None:
        self.config = config
        self.runtime_metadata = {
            "resolved_backend": "deterministic",
            "resolved_precision": "fp32",
        }

    def predict_image(
        self, image_bytes: bytes, metadata: dict[str, Any] | None = None
    ) -> PredictionResult:
        start = time.perf_counter()
        with Image.open(BytesIO(image_bytes)) as opened:
            width, height = opened.size
        return PredictionResult(
            task="detect",
            predictions=[],
            latency_ms=round((time.perf_counter() - start) * 1000, 3),
            image={
                "width": width,
                "height": height,
                "mode": "RGB",
                "metadata": dict(metadata or {}),
            },
        )


def load_predictor(config: InferenceConfig) -> Predictor:
    return (
        PaddleXPredictor(config)
        if config.production
        else DeterministicPredictor(config)
    )


def _create_model_kwargs(config: InferenceConfig) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model_name": _bundle_model_name(config.model_dir),
        "model_dir": str(config.model_dir),
        "device": config.device,
        "img_size": config.input_size,
        "use_hpip": config.backend == "paddlex_hpi_tensorrt",
    }
    if config.backend == "paddlex_hpi_tensorrt":
        kwargs["hpi_params"] = {
            "selected_backends": {"gpu": "tensorrt"},
            "backend_config": {
                "tensorrt": {
                    "precision": config.precision.upper(),
                    "dynamic_shapes": {"x": _dynamic_shapes(config.input_size)},
                }
            },
        }
    return kwargs


def _bundle_model_name(model_dir: Any) -> str:
    path = model_dir / "inference.yml"
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError("PaddleX inference bundle metadata is unavailable") from exc
    global_config = payload.get("Global") if isinstance(payload, dict) else None
    model_name = (
        global_config.get("model_name") if isinstance(global_config, dict) else None
    )
    if not isinstance(model_name, str) or not model_name.strip():
        raise ValueError("PaddleX inference bundle model identity is unavailable")
    return model_name.strip()


def _dynamic_shapes(input_size: tuple[int, int]) -> list[list[int]]:
    height, width = input_size
    return [
        [1, 3, _half_aligned(height), _half_aligned(width)],
        [1, 3, height, width],
        [1, 3, min(4096, height * 2), min(4096, width * 2)],
    ]


def _half_aligned(value: int) -> int:
    return max(32, (value // 2 // 32) * 32)


def _normalize_box(box: dict[str, Any], class_names: list[str]) -> dict[str, Any]:
    coordinate = box.get("coordinate") or box.get("bbox")
    if not isinstance(coordinate, (list, tuple)) or len(coordinate) != 4:
        raise ValueError("PaddleX prediction box is invalid")
    class_id = int(box.get("cls_id", box.get("class_id", 0)))
    label = box.get("label") or (
        class_names[class_id]
        if 0 <= class_id < len(class_names)
        else f"class_{class_id}"
    )
    label = str(label)
    if len(label) > MAX_LABEL_CHARS:
        raise ValueError("PaddleX prediction label is too long")
    confidence = float(box.get("score", box.get("confidence", 0.0)))
    coordinates = [float(value) for value in coordinate]
    if not all(math.isfinite(value) for value in [confidence, *coordinates]):
        raise ValueError("PaddleX prediction values must be finite")
    if not 0 <= confidence <= 1:
        raise ValueError("PaddleX prediction confidence must be between 0 and 1")
    if coordinates[2] < coordinates[0] or coordinates[3] < coordinates[1]:
        raise ValueError("PaddleX prediction box is invalid")
    return {
        "class_id": class_id,
        "label": label,
        "confidence": confidence,
        "bbox": {
            "x1": coordinates[0],
            "y1": coordinates[1],
            "x2": coordinates[2],
            "y2": coordinates[3],
        },
    }


def _encode_result_image(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Image.Image):
        image = value
    else:
        try:
            image = Image.fromarray(value)
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("PaddleX annotated result is invalid") from exc
    stream = BytesIO()
    image.convert("RGB").save(stream, format="PNG")
    encoded = stream.getvalue()
    if len(encoded) > MAX_ANNOTATED_IMAGE_BYTES:
        raise ValueError("PaddleX annotated result is too large")
    return f"data:image/png;base64,{base64.b64encode(encoded).decode('ascii')}"
