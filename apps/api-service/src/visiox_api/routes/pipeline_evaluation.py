from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal, Protocol

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from visiox_api.dependencies.auth import get_current_user
from visiox_api.dependencies.authorization import require_resource_permission
from visiox_api.dependencies.database import get_db_session
from visiox_api.routes.pipeline_inference import (
    _download_storage_uri,
    _normalize_ultralytics_device,
    _resolve_weight_uri,
)
from visiox_db.models import Dataset, PipelineEvaluation, TrainingPipeline
from visiox_db.models.identity import PERMISSION_EDIT, PERMISSION_USE, PERMISSION_VIEW, User
from visiox_storage.client import ObjectStorageClient
from visiox_yolo26.converters import export_yolo26_dataset


router = APIRouter(prefix="/pipelines", tags=["pipeline-evaluation"])


class PipelineEvaluationRequest(BaseModel):
    evaluation_set: Literal["val", "custom"] = "val"
    dataset_id: str | None = None
    model_weight: str = "best.pt"
    environment: str = "cpu"


class PipelineEvaluationResult(BaseModel):
    score: float | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)


class PipelineEvaluationResponse(BaseModel):
    id: str
    pipeline_id: str
    dataset_id: str
    evaluation_set: str
    model_weight: str
    environment: str
    status: str
    score: float | None
    metrics: dict[str, Any]
    created_at: str


class PipelineEvaluationListResponse(BaseModel):
    items: list[PipelineEvaluationResponse]
    total: int
    limit: int
    offset: int


class PipelineEvaluator(Protocol):
    def evaluate(
        self,
        *,
        model_path: Path,
        data_yaml_path: Path,
        environment: str,
        split: str,
    ) -> PipelineEvaluationResult: ...


class UltralyticsPipelineEvaluator:
    def evaluate(
        self,
        *,
        model_path: Path,
        data_yaml_path: Path,
        environment: str,
        split: str,
    ) -> PipelineEvaluationResult:
        from ultralytics import YOLO

        model = YOLO(str(model_path))
        metrics = model.val(
            data=str(data_yaml_path),
            split=split,
            device=_normalize_ultralytics_device(environment),
            verbose=False,
        )
        metric_values = _ultralytics_metric_dict(metrics)
        return PipelineEvaluationResult(score=_read_score(metric_values), metrics=metric_values)


get_pipeline_evaluation_session = get_db_session


def get_pipeline_evaluation_storage(request: Request) -> ObjectStorageClient:
    storage = getattr(request.app.state, "object_storage", None)
    if storage is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Object storage is not configured")
    return storage


def get_pipeline_evaluator() -> PipelineEvaluator:
    return UltralyticsPipelineEvaluator()


@router.post("/{pipeline_id}/evaluate", response_model=PipelineEvaluationResponse)
def evaluate_pipeline(
    pipeline_id: str,
    payload: PipelineEvaluationRequest,
    session: Session = Depends(get_pipeline_evaluation_session),
    storage: ObjectStorageClient = Depends(get_pipeline_evaluation_storage),
    evaluator: PipelineEvaluator = Depends(get_pipeline_evaluator),
    actor: User = Depends(get_current_user),
) -> PipelineEvaluationResponse:
    pipeline = session.get(TrainingPipeline, pipeline_id)
    if pipeline is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline not found")
    require_resource_permission(session, actor, "pipeline", pipeline_id, PERMISSION_EDIT)
    dataset_id = _evaluation_dataset_id(pipeline, payload)
    require_resource_permission(session, actor, "dataset", dataset_id, PERMISSION_USE)
    dataset = session.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Evaluation dataset not found")
    if dataset.task != pipeline.task:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Evaluation dataset task does not match pipeline")

    weight_uri = _resolve_weight_uri(session, pipeline, payload.model_weight)
    split = "test" if payload.evaluation_set == "custom" else "val"
    with TemporaryDirectory(prefix="visiox-eval-") as temp_dir:
        work_dir = Path(temp_dir)
        model_path = work_dir / "model.pt"
        dataset_dir = work_dir / "dataset"
        _download_storage_uri(storage, weight_uri, model_path)
        export_yolo26_dataset(session, storage, dataset_id, dataset_dir)
        _prepare_evaluation_dataset(dataset_dir)
        result = evaluator.evaluate(
            model_path=model_path,
            data_yaml_path=dataset_dir / "data.yaml",
            environment=payload.environment or "cpu",
            split=split,
        )

    evaluation = PipelineEvaluation(
        pipeline_id=pipeline.id,
        dataset_id=dataset_id,
        evaluation_set=payload.evaluation_set,
        model_weight=payload.model_weight,
        environment=payload.environment or "cpu",
        status="completed",
        score=result.score,
        metrics=result.metrics,
    )
    session.add(evaluation)
    session.commit()
    session.refresh(evaluation)

    return _evaluation_response(evaluation)


@router.get("/{pipeline_id}/evaluations", response_model=PipelineEvaluationListResponse)
def list_pipeline_evaluations(
    pipeline_id: str,
    limit: int = 50,
    offset: int = 0,
    session: Session = Depends(get_pipeline_evaluation_session),
    actor: User = Depends(get_current_user),
) -> PipelineEvaluationListResponse:
    if session.get(TrainingPipeline, pipeline_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline not found")
    require_resource_permission(session, actor, "pipeline", pipeline_id, PERMISSION_VIEW)
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    total = session.scalar(select(func.count()).select_from(PipelineEvaluation).where(PipelineEvaluation.pipeline_id == pipeline_id)) or 0
    evaluations = session.scalars(
        select(PipelineEvaluation)
        .where(PipelineEvaluation.pipeline_id == pipeline_id)
        .order_by(PipelineEvaluation.created_at.desc(), PipelineEvaluation.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return PipelineEvaluationListResponse(
        items=[_evaluation_response(evaluation) for evaluation in evaluations],
        total=total,
        limit=limit,
        offset=offset,
    )


def _evaluation_response(evaluation: PipelineEvaluation) -> PipelineEvaluationResponse:
    return PipelineEvaluationResponse(
        id=evaluation.id,
        pipeline_id=evaluation.pipeline_id,
        dataset_id=evaluation.dataset_id,
        evaluation_set=evaluation.evaluation_set,
        model_weight=evaluation.model_weight,
        environment=evaluation.environment,
        status=evaluation.status,
        score=evaluation.score,
        metrics=evaluation.metrics,
        created_at=evaluation.created_at.isoformat(),
    )


def _evaluation_dataset_id(pipeline: TrainingPipeline, payload: PipelineEvaluationRequest) -> str:
    if payload.evaluation_set == "custom":
        if not payload.dataset_id:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Custom evaluation requires dataset_id")
        return payload.dataset_id
    if pipeline.dataset_id is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Pipeline has no dataset")
    return pipeline.dataset_id


def _prepare_evaluation_dataset(dataset_dir: Path) -> None:
    for relative_dir in ("images/train", "images/val", "images/test", "labels/train", "labels/val", "labels/test"):
        (dataset_dir / relative_dir).mkdir(parents=True, exist_ok=True)
    data_yaml_path = dataset_dir / "data.yaml"
    lines = data_yaml_path.read_text(encoding="utf-8").splitlines()
    absolute_path_line = f"path: {dataset_dir.resolve().as_posix()}"
    for index, line in enumerate(lines):
        if line.strip().startswith("path:"):
            lines[index] = absolute_path_line
            break
    else:
        lines.insert(0, absolute_path_line)
    data_yaml_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _ultralytics_metric_dict(metrics: Any) -> dict[str, Any]:
    results = getattr(metrics, "results_dict", None)
    if isinstance(results, dict):
        return {str(key): _json_value(value) for key, value in results.items()}
    return {}


def _read_score(metrics: dict[str, Any]) -> float | None:
    for key in ("metrics/mAP50(B)", "metrics/mAP50-95(B)", "mAP50", "fitness"):
        value = metrics.get(key)
        if isinstance(value, int | float):
            return round(float(value), 4)
    return None


def _json_value(value: Any) -> Any:
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, int | float | str | bool) or value is None:
        return value
    return str(value)
