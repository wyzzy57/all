from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile, status
from PIL import Image, UnidentifiedImageError

from visiox_paddlex_inference.config import (
    InferenceConfig,
    InferenceConfigError,
    load_config,
)
from visiox_paddlex_inference.predict import Predictor, load_predictor


MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
ALLOWED_IMAGE_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/bmp"}


def create_app(
    config: InferenceConfig | None = None, predictor: Predictor | None = None
) -> FastAPI:
    runtime_config = config or load_config()
    runtime_predictor = predictor or load_predictor(runtime_config)
    runtime_metadata = runtime_predictor.runtime_metadata
    loaded_at = datetime.now(timezone.utc)
    app = FastAPI(title="Visiox PaddleX Inference", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, object]:
        return {
            "service": "paddlex-inference",
            "status": "ok",
            "task": runtime_config.task,
            "model_loaded": runtime_predictor is not None,
        }

    @app.get("/metadata")
    def metadata() -> dict[str, object]:
        return {
            "task": runtime_config.task,
            "framework": "paddlex",
            "adapter_key": "paddlex.object_detection.v1",
            "model_format": runtime_config.model_format,
            "device": runtime_config.device,
            "precision": runtime_metadata["resolved_precision"],
            "resolved_precision": runtime_metadata["resolved_precision"],
            "input_size": list(runtime_config.input_size),
            "optimization": runtime_config.optimization,
            "resolved_backend": runtime_metadata["resolved_backend"],
            "loaded_at": loaded_at.isoformat(),
        }

    @app.post("/predict/image")
    async def predict_image(file: UploadFile = File(...)) -> dict[str, Any]:
        if file.content_type and file.content_type not in ALLOWED_IMAGE_CONTENT_TYPES:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="Unsupported image content type",
            )
        image_bytes = await file.read(MAX_IMAGE_BYTES + 1)
        if len(image_bytes) > MAX_IMAGE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="Image is too large",
            )
        try:
            with Image.open(BytesIO(image_bytes)) as image:
                width, height = image.size
        except (OSError, UnidentifiedImageError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Image content is invalid",
            ) from exc
        if width * height > MAX_IMAGE_PIXELS:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="Image dimensions are too large",
            )
        try:
            result = runtime_predictor.predict_image(
                image_bytes,
                {"filename": file.filename, "content_type": file.content_type},
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        return {
            "task": result.task,
            "predictions": result.predictions,
            "latency_ms": result.latency_ms,
            "image": result.image,
            "result_image": result.result_image,
        }

    return app


try:
    app = create_app()
except InferenceConfigError as exc:
    raise RuntimeError(f"Invalid PaddleX inference configuration: {exc}") from exc
