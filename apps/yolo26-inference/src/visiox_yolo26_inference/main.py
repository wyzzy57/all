from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from visiox_yolo26_inference.config import InferenceConfig, InferenceConfigError, load_config
from visiox_yolo26_inference.predict import Predictor, load_predictor


MAX_IMAGE_BYTES = 10 * 1024 * 1024
ALLOWED_IMAGE_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/bmp"}


@dataclass
class RuntimeMetrics:
    started_at: datetime
    prediction_requests: int = 0
    prediction_errors: int = 0
    reload_errors: int = 0
    reload_count: int = 0
    last_latency_ms: float | None = None


class RuntimeReloadRequest(BaseModel):
    task: str | None = None
    model_path: str | None = None
    model_format: str | None = None
    device: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    iou: float | None = Field(default=None, ge=0, le=1)
    class_names: list[str] | None = None
    input: dict[str, Any] | None = None


class VideoFrameRequest(BaseModel):
    image_base64: str
    camera_id: str | None = None
    timestamp_ms: int | None = Field(default=None, ge=0)


def create_app(config: InferenceConfig | None = None, predictor: Predictor | None = None) -> FastAPI:
    app = FastAPI(title="Visiox YOLO26 Inference", version="0.1.0")
    _load_runtime(app, config or load_config(), predictor=predictor, increment_reload=False)
    app.state.metrics = RuntimeMetrics(started_at=datetime.now(UTC))

    @app.get("/health")
    def health() -> dict[str, object]:
        runtime_config: InferenceConfig = app.state.config
        return {
            "service": "yolo26-inference",
            "status": "ok",
            "task": runtime_config.task,
            "model_loaded": app.state.predictor is not None,
        }

    @app.get("/model/info")
    def model_info() -> dict[str, object]:
        runtime_config: InferenceConfig = app.state.config
        return {
            "task": runtime_config.task,
            "model_path": str(runtime_config.model_path),
            "model_format": runtime_config.model_format,
            "device": runtime_config.device,
            "confidence": runtime_config.confidence,
            "iou": runtime_config.iou,
            "class_names": runtime_config.class_names,
            "input": runtime_config.input,
            "loaded_at": app.state.loaded_at.isoformat(),
        }

    @app.post("/predict/image")
    async def predict_image(file: UploadFile = File(...)) -> dict[str, object]:
        if file.content_type and file.content_type not in ALLOWED_IMAGE_CONTENT_TYPES:
            _record_prediction_request(app, failed=True)
            raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Unsupported image content type")
        image_bytes = await file.read()
        if len(image_bytes) > MAX_IMAGE_BYTES:
            _record_prediction_request(app, failed=True)
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Image is too large")
        return _predict(app, image_bytes, {"filename": file.filename, "content_type": file.content_type})

    if not app.state.config.production:

        @app.post("/predict/video-frame")
        def predict_video_frame(request: VideoFrameRequest) -> dict[str, object]:
            import base64
            import binascii

            try:
                if len(request.image_base64) > MAX_IMAGE_BYTES * 2:
                    raise ValueError("base64 image is too large")
                image_bytes = base64.b64decode(request.image_base64, validate=True)
            except (binascii.Error, ValueError) as exc:
                _record_prediction_request(app, failed=True)
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="Invalid base64 image",
                ) from exc
            if len(image_bytes) > MAX_IMAGE_BYTES:
                _record_prediction_request(app, failed=True)
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="Image is too large",
                )
            metadata = {
                "camera_id": request.camera_id,
                "timestamp_ms": request.timestamp_ms,
            }
            return _predict(app, image_bytes, metadata)

        @app.post("/runtime/reload")
        def runtime_reload(request: RuntimeReloadRequest) -> dict[str, object]:
            current: InferenceConfig = app.state.config
            payload = current.model_dump()
            for key, value in request.model_dump(exclude_none=True).items():
                payload[key] = value
            try:
                new_config = InferenceConfig(**payload)
                _load_runtime(app, new_config)
            except (InferenceConfigError, ValueError) as exc:
                app.state.metrics.reload_errors += 1
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=str(exc),
                ) from exc
            return {
                "status": "reloaded",
                "task": new_config.task,
                "model_path": str(new_config.model_path),
            }

    @app.get("/metrics")
    def metrics() -> dict[str, object]:
        runtime_metrics: RuntimeMetrics = app.state.metrics
        return {
            "started_at": runtime_metrics.started_at.isoformat(),
            "prediction_requests": runtime_metrics.prediction_requests,
            "prediction_errors": runtime_metrics.prediction_errors,
            "reload_count": runtime_metrics.reload_count,
            "reload_errors": runtime_metrics.reload_errors,
            "last_latency_ms": runtime_metrics.last_latency_ms,
        }

    return app


def _load_runtime(
    app: FastAPI,
    config: InferenceConfig,
    *,
    predictor: Predictor | None = None,
    increment_reload: bool = True,
) -> None:
    try:
        runtime_predictor = predictor or load_predictor(config)
    except InferenceConfigError:
        raise
    loaded_at = datetime.now(UTC)
    app.state.config = config
    app.state.predictor = runtime_predictor
    app.state.loaded_at = loaded_at
    if increment_reload and hasattr(app.state, "metrics"):
        app.state.metrics.reload_count += 1


def _predict(app: FastAPI, image_bytes: bytes, metadata: dict[str, Any]) -> dict[str, object]:
    metrics: RuntimeMetrics = app.state.metrics
    _record_prediction_request(app)
    try:
        result = app.state.predictor.predict_image(image_bytes, metadata)
    except Exception as exc:
        metrics.prediction_errors += 1
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    metrics.last_latency_ms = result.latency_ms
    return {
        "task": result.task,
        "predictions": result.predictions,
        "latency_ms": result.latency_ms,
        "image": result.image,
    }


def _record_prediction_request(app: FastAPI, *, failed: bool = False) -> None:
    if hasattr(app.state, "metrics"):
        app.state.metrics.prediction_requests += 1
        if failed:
            app.state.metrics.prediction_errors += 1


try:
    app = create_app()
except InferenceConfigError as exc:
    raise RuntimeError(f"Invalid YOLO26 inference configuration: {exc}") from exc
