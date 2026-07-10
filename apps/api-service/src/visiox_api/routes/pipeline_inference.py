from __future__ import annotations

import base64
import re
from collections.abc import Generator
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Protocol

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from visiox_db.models import BaseModel as BaseModelRecord
from visiox_db.models import TrainedModel, TrainingPipeline
from visiox_db.session import get_session
from visiox_storage.client import ObjectStorageClient


router = APIRouter(prefix="/pipelines", tags=["pipeline-inference"])


class PipelineInferenceResult(BaseModel):
    predictions: list[dict[str, Any]] = Field(default_factory=list)
    annotated_image_bytes: bytes
    content_type: str = "image/png"


class PipelinePredictResponse(BaseModel):
    pipeline_id: str
    model_weight: str
    environment: str
    predictions: list[dict[str, Any]]
    result_image: str


class PipelinePredictor(Protocol):
    def predict(self, *, model_path: Path, image_path: Path, environment: str) -> PipelineInferenceResult: ...


class UltralyticsPipelinePredictor:
    def predict(self, *, model_path: Path, image_path: Path, environment: str) -> PipelineInferenceResult:
        from io import BytesIO

        from PIL import Image
        from ultralytics import YOLO

        model = YOLO(str(model_path))
        device = _normalize_ultralytics_device(environment)
        results = model.predict(source=str(image_path), device=device, verbose=False)
        if not results:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Ultralytics returned no prediction result")
        result = results[0]
        annotated = Image.fromarray(result.plot())
        image_buffer = BytesIO()
        annotated.save(image_buffer, format="PNG")
        return PipelineInferenceResult(
            predictions=_ultralytics_predictions(result),
            annotated_image_bytes=image_buffer.getvalue(),
            content_type="image/png",
        )


def get_pipeline_inference_session() -> Generator[Session]:
    yield from get_session()


def get_pipeline_inference_storage(request: Request) -> ObjectStorageClient:
    storage = getattr(request.app.state, "object_storage", None)
    if storage is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Object storage is not configured")
    return storage


def get_pipeline_predictor() -> PipelinePredictor:
    return UltralyticsPipelinePredictor()


@router.post("/{pipeline_id}/predict/image", response_model=PipelinePredictResponse)
async def predict_pipeline_image(
    pipeline_id: str,
    file: UploadFile = File(...),
    model_weight: str = Form("latest"),
    environment: str = Form("cpu"),
    session: Session = Depends(get_pipeline_inference_session),
    storage: ObjectStorageClient = Depends(get_pipeline_inference_storage),
    predictor: PipelinePredictor = Depends(get_pipeline_predictor),
) -> PipelinePredictResponse:
    pipeline = session.get(TrainingPipeline, pipeline_id)
    if pipeline is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline not found")
    if file.content_type and not file.content_type.startswith("image/"):
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Only image files are supported")

    weight_uri = _resolve_weight_uri(session, pipeline, model_weight)
    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Image file is empty")

    with TemporaryDirectory(prefix="visiox-infer-") as temp_dir:
        work_dir = Path(temp_dir)
        model_path = work_dir / "model.pt"
        image_path = work_dir / (Path(file.filename or "image").name or "image.png")
        _download_storage_uri(storage, weight_uri, model_path)
        image_path.write_bytes(image_bytes)
        result = predictor.predict(model_path=model_path, image_path=image_path, environment=environment or "cpu")

    encoded = base64.b64encode(result.annotated_image_bytes).decode("ascii")
    return PipelinePredictResponse(
        pipeline_id=pipeline.id,
        model_weight=model_weight,
        environment=environment or "cpu",
        predictions=result.predictions,
        result_image=f"data:{result.content_type};base64,{encoded}",
    )


def _resolve_weight_uri(session: Session, pipeline: TrainingPipeline, model_weight: str) -> str:
    selected = model_weight.strip() if model_weight else "latest"
    normalized = _normalize_weight_name(selected)
    if normalized in {"latest", "trained"}:
        trained = session.scalars(
            select(TrainedModel)
            .where(TrainedModel.pipeline_id == pipeline.id)
            .order_by(TrainedModel.created_at.desc(), TrainedModel.id.desc())
            .limit(1)
        ).first()
        if trained is not None:
            return trained.artifact_uri
        return _base_model_uri(session, pipeline)
    if normalized in {"best.pt", "last.pt"}:
        trained = session.scalars(
            select(TrainedModel)
            .where(
                TrainedModel.pipeline_id == pipeline.id,
                TrainedModel.status == "ready",
                TrainedModel.name == normalized,
            )
            .order_by(TrainedModel.created_at.desc(), TrainedModel.id.desc())
            .limit(1)
        ).first()
        if trained is not None:
            return trained.artifact_uri
        if normalized == "best.pt":
            trained = session.scalars(
                select(TrainedModel)
                .where(TrainedModel.pipeline_id == pipeline.id, TrainedModel.status == "ready")
                .order_by(TrainedModel.created_at.desc(), TrainedModel.id.desc())
                .limit(1)
            ).first()
            if trained is not None:
                return trained.artifact_uri
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{normalized} weight not found")
    if normalized in {"base", "official"}:
        return _base_model_uri(session, pipeline)

    trained = session.get(TrainedModel, selected)
    if trained is None or trained.pipeline_id != pipeline.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Selected model weight not found")
    return trained.artifact_uri


def _normalize_weight_name(model_weight: str) -> str:
    selected = model_weight.strip().lower()
    if selected in {"best", "last"}:
        return f"{selected}.pt"
    return selected


def _base_model_uri(session: Session, pipeline: TrainingPipeline) -> str:
    if pipeline.base_model_id is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Pipeline has no base model")
    base_model = session.get(BaseModelRecord, pipeline.base_model_id)
    if base_model is None or not base_model.local_uri:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Base model artifact not found")
    return base_model.local_uri


def _download_storage_uri(storage: ObjectStorageClient, uri: str, destination: Path) -> Path:
    bucket, object_name = _parse_storage_uri(uri)
    return storage.get_file(bucket, object_name, destination)


def _parse_storage_uri(uri: str) -> tuple[str, str]:
    marker = "://"
    if marker not in uri:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model artifact is not available")
    remainder = uri.split(marker, 1)[1]
    bucket, separator, object_name = remainder.partition("/")
    if not separator or not bucket or not object_name:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model artifact is not available")
    return bucket, object_name


def _normalize_ultralytics_device(environment: str) -> str:
    value = (environment or "").strip().lower()
    if not value or value == "cpu":
        return "cpu"
    if re.fullmatch(r"\d+(,\d+)*", value):
        return value
    match = re.search(r"(?:gpu|cuda|device|node)[^\d]*(\d+)", value)
    if match:
        return match.group(1)
    return value


def _ultralytics_predictions(result: Any) -> list[dict[str, Any]]:
    boxes = getattr(result, "boxes", None)
    if boxes is None:
        return []
    names = getattr(result, "names", {}) or {}
    predictions: list[dict[str, Any]] = []
    for box in boxes:
        cls_value = int(box.cls[0].item()) if getattr(box, "cls", None) is not None else -1
        label = str(names.get(cls_value, cls_value))
        score = float(box.conf[0].item()) if getattr(box, "conf", None) is not None else 0.0
        xyxy = box.xyxy[0].tolist() if getattr(box, "xyxy", None) is not None else []
        predictions.append(
            {
                "label": label,
                "score": round(score, 6),
                "box": [round(float(value), 2) for value in xyxy],
            }
        )
    return predictions
