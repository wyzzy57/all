from __future__ import annotations

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
from visiox_api.routes.pipeline_inference import _normalize_ultralytics_device
from visiox_api.services.framework_adapters import (
    FrameworkAdapterCatalog,
    get_framework_adapter_catalog,
)
from visiox_api.services.pipeline_evaluation import (
    DockerPaddleXEvaluationRuntime,
    PaddleXEvaluationRuntime,
    PaddleXEvaluationRuntimeRequest,
    paddlex_evaluation_result,
    paddlex_model_config,
)
from visiox_api.services.pipeline_inference import (
    download_artifact,
    paddlex_device,
    resolve_pipeline_model,
)
from visiox_db.models import Dataset, DatasetVersion, PipelineEvaluation, TrainingPipeline
from visiox_db.models.identity import PERMISSION_EDIT, PERMISSION_USE, PERMISSION_VIEW, User
from visiox_storage.client import ObjectStorageClient
from visiox_yolo26.converters import export_yolo26_dataset
from visiox_paddlex.datasets import export_paddlex_detection_dataset


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


def get_pipeline_evaluation_runtime() -> PaddleXEvaluationRuntime:
    return DockerPaddleXEvaluationRuntime()


@router.post("/{pipeline_id}/evaluate", response_model=PipelineEvaluationResponse)
def evaluate_pipeline(
    pipeline_id: str,
    payload: PipelineEvaluationRequest,
    session: Session = Depends(get_pipeline_evaluation_session),
    storage: ObjectStorageClient = Depends(get_pipeline_evaluation_storage),
    evaluator: PipelineEvaluator = Depends(get_pipeline_evaluator),
    runtime: PaddleXEvaluationRuntime = Depends(get_pipeline_evaluation_runtime),
    catalog: FrameworkAdapterCatalog = Depends(get_framework_adapter_catalog),
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

    resolved = resolve_pipeline_model(
        session, catalog, pipeline, payload.model_weight, operation="evaluation"
    )
    split = "test" if payload.evaluation_set == "custom" else "val"
    dataset_version = None
    runtime_model_id = None
    config_path = None
    device = None
    runtime_digest = None
    if pipeline.framework == "paddlex":
        dataset_version = session.scalar(
            select(DatasetVersion)
            .where(DatasetVersion.dataset_id == dataset.id)
            .order_by(DatasetVersion.version.desc(), DatasetVersion.id.desc())
        )
        if dataset_version is None or dataset_version.format != "coco":
            raise HTTPException(status_code=409, detail="Compatible dataset version is unavailable")
        runtime_model_id = _paddlex_runtime_model_id(pipeline)
        try:
            config_path = paddlex_model_config(runtime_model_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        device = paddlex_device(payload.environment)
        runtime_digest = _require_runtime_digest(
            resolved.adapter.capabilities.training_runtime_image_digest,
            "PaddleX evaluation runtime is unavailable",
        )
    with TemporaryDirectory(prefix="visiox-eval-") as temp_dir:
        work_dir = Path(temp_dir)
        model_name = (
            resolved.model.name
            if resolved.model is not None
            else f"model.{resolved.model_format}"
        )
        model_path = work_dir / "model" / Path(model_name).name
        dataset_dir = work_dir / "dataset"
        download_artifact(
            storage,
            resolved.artifact_uri,
            model_path,
            checksum=resolved.model.checksum if resolved.model else None,
            size_bytes=resolved.model.size_bytes if resolved.model else None,
            require_integrity=pipeline.framework == "paddlex",
        )
        if pipeline.framework == "ultralytics":
            export_yolo26_dataset(session, storage, dataset_id, dataset_dir)
            _prepare_evaluation_dataset(dataset_dir)
            result = evaluator.evaluate(
                model_path=model_path,
                data_yaml_path=dataset_dir / "data.yaml",
                environment=payload.environment or "cpu",
                split=split,
            )
        elif pipeline.framework == "paddlex":
            assert dataset_version is not None
            assert runtime_model_id is not None
            assert config_path is not None
            assert device is not None
            assert runtime_digest is not None
            export_paddlex_detection_dataset(
                session,
                storage,
                dataset.id,
                dataset_version.id,
                dataset_dir,
                runtime_model_id=runtime_model_id,
            )
            output_dir = work_dir / "output"
            output_dir.mkdir()
            request = PaddleXEvaluationRuntimeRequest(
                image_digest=runtime_digest,
                workspace=work_dir,
                config_path=config_path,
                overrides=(
                    "Global.mode=evaluate",
                    "Global.dataset_dir=/workspace/dataset",
                    "Global.output=/workspace/output",
                    f"Global.device={device}",
                    f"Evaluate.weight_path=/workspace/model/{model_path.name}",
                ),
                result_path=output_dir / "evaluate_result.json",
                device=device,
            )
            try:
                runtime.run(request)
                score, metrics = paddlex_evaluation_result(request.result_path)
            except (RuntimeError, ValueError) as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            result = PipelineEvaluationResult(score=score, metrics=metrics)
        else:  # registry resolution already rejects unknown framework identities
            raise HTTPException(status_code=409, detail="Framework evaluation is not implemented")

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


def _paddlex_runtime_model_id(pipeline: TrainingPipeline) -> str:
    model = pipeline.recipe.get("model") if isinstance(pipeline.recipe, dict) else None
    runtime_id = model.get("runtime_id") if isinstance(model, dict) else None
    if not isinstance(runtime_id, str) or not runtime_id:
        raise HTTPException(status_code=409, detail="PaddleX runtime model is unavailable")
    return runtime_id


def _require_runtime_digest(value: str | None, message: str) -> str:
    if not value:
        raise HTTPException(status_code=409, detail=message)
    return value
