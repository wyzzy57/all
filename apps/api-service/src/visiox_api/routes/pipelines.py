from __future__ import annotations

from collections.abc import Generator
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict
from pydantic import Field
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from visiox_api.dependencies.auth import get_current_user
from visiox_api.dependencies.authorization import require_resource_permission
from visiox_api.dependencies.database import get_db_session
from visiox_api.services.authorization import authorized_resource_predicate
from visiox_db.models import (
    BaseModel,
    DeploymentService,
    DistributedTrainingRun,
    PipelineEvaluation,
    RemoteExecution,
    Task,
    TrainedModel,
    TrainingJob,
    TrainingPipeline,
)
from visiox_db.models.identity import (
    PERMISSION_DELETE,
    PERMISSION_EDIT,
    PERMISSION_USE,
    PERMISSION_VIEW,
    User,
)
from visiox_yolo26.training.params import (
    TrainingParamsError,
    validate_training_environment,
    validate_training_params,
)
from visiox_yolo26.training.prechecks import TrainingPrecheckError, validate_training_resources
from visiox_api.services.llm_training import (
    LlmTrainingConfigError,
    validate_llamafactory_config,
    validate_llm_dataset,
    validate_llm_environment,
)


router = APIRouter(prefix="/pipelines", tags=["pipelines"])


class PipelineCreateRequest(PydanticBaseModel):
    name: str
    engine: str = Field(default="yolo26", pattern=r"^(yolo26|llamafactory)$")
    task: str
    scale: str = "n"
    base_model_id: str | None = None
    dataset_id: str | None = None
    params_template: dict[str, Any] = Field(default_factory=dict)
    default_environment: dict[str, Any] = Field(default_factory=dict)


class PipelineUpdateRequest(PydanticBaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    engine: str | None = Field(default=None, pattern=r"^(yolo26|llamafactory)$")
    task: str | None = None
    scale: str | None = None
    base_model_id: str | None = None
    dataset_id: str | None = None
    params_template: dict[str, Any] | None = None
    default_environment: dict[str, Any] | None = None
    is_public: bool | None = None
    public_scope: dict[str, Any] | None = None
    is_favorite: bool | None = None


class PipelineResponse(PydanticBaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    engine: str
    task: str
    scale: str
    base_model_id: str | None
    dataset_id: str | None
    params_template: dict[str, Any]
    default_environment: dict[str, Any]
    status: str
    is_public: bool
    public_scope: dict[str, Any]
    is_favorite: bool
    organization_id: str | None
    owner_user_id: str | None
    visibility: str
    created_at: datetime
    updated_at: datetime


class PipelineListResponse(PydanticBaseModel):
    items: list[PipelineResponse]
    total: int
    limit: int
    offset: int


get_pipeline_session = get_db_session


def _unprocessable(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=message)


@router.post("", response_model=PipelineResponse)
def create_pipeline(
    request: PipelineCreateRequest,
    response: Response,
    session: Session = Depends(get_pipeline_session),
    actor: User = Depends(get_current_user),
) -> TrainingPipeline:
    if request.dataset_id:
        require_resource_permission(session, actor, "dataset", request.dataset_id, PERMISSION_USE)
    if request.engine == "llamafactory":
        if request.task != "llm":
            raise _unprocessable("LLaMA-Factory pipelines must use task llm")
        if request.base_model_id is not None:
            raise _unprocessable("LLaMA-Factory pipelines use an external model reference")
        try:
            params_template = validate_llamafactory_config(request.params_template)
            default_environment = validate_llm_environment(request.default_environment)
            if request.dataset_id:
                validate_llm_dataset(session, request.dataset_id)
        except LlmTrainingConfigError as exc:
            raise _unprocessable(str(exc)) from exc
        pipeline = TrainingPipeline(
            name=request.name,
            engine=request.engine,
            task=request.task,
            scale=request.scale,
            dataset_id=request.dataset_id,
            params_template=params_template,
            default_environment=default_environment,
            status="ready" if request.dataset_id and params_template.get("model_id") else "draft",
            organization_id=actor.organization_id,
            owner_user_id=actor.id,
            visibility="private",
        )
        session.add(pipeline)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Pipeline name already exists") from exc
        session.refresh(pipeline)
        response.status_code = status.HTTP_201_CREATED
        return pipeline

    if request.dataset_id is not None and request.base_model_id is None:
        raise _unprocessable("base_model_id is required when dataset_id is provided")
    if request.base_model_id and not request.dataset_id:
        base_model = session.get(BaseModel, request.base_model_id)
        if base_model is None:
            raise _unprocessable("Base model not found")
        if base_model.status != "ready" or not base_model.local_uri:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Base model is not ready")
        if base_model.task != request.task or base_model.scale != request.scale:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Base model task or scale does not match pipeline")
    if request.base_model_id and request.dataset_id:
        try:
            validate_training_resources(
                session,
                task=request.task,
                scale=request.scale,
                base_model_id=request.base_model_id,
                dataset_id=request.dataset_id,
            )
        except TrainingPrecheckError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    try:
        params_template = validate_training_params(request.params_template)
        default_environment = validate_training_environment(request.default_environment)
    except TrainingParamsError as exc:
        raise _unprocessable(str(exc)) from exc

    pipeline = TrainingPipeline(
        name=request.name,
        engine=request.engine,
        task=request.task,
        scale=request.scale,
        base_model_id=request.base_model_id,
        dataset_id=request.dataset_id,
        params_template=params_template,
        default_environment=default_environment,
        status="ready" if request.base_model_id and request.dataset_id else "draft",
        organization_id=actor.organization_id,
        owner_user_id=actor.id,
        visibility="private",
    )
    session.add(pipeline)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="产线名称已存在，请使用其他名称") from exc
    session.refresh(pipeline)
    response.status_code = status.HTTP_201_CREATED
    return pipeline


@router.get("", response_model=PipelineListResponse)
def list_pipelines(
    task: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_pipeline_session),
    actor: User = Depends(get_current_user),
) -> PipelineListResponse:
    filters = [
        authorized_resource_predicate(
            session, actor, TrainingPipeline, "pipeline", PERMISSION_VIEW
        )
    ]
    if task is not None:
        filters.append(TrainingPipeline.task == task)
    if status_filter is not None:
        filters.append(TrainingPipeline.status == status_filter)
    total_query = select(func.count()).select_from(TrainingPipeline)
    list_query = select(TrainingPipeline).order_by(TrainingPipeline.created_at, TrainingPipeline.id)
    if filters:
        total_query = total_query.where(*filters)
        list_query = list_query.where(*filters)
    total = session.scalar(total_query) or 0
    items = session.scalars(list_query.limit(limit).offset(offset)).all()
    return PipelineListResponse(items=list(items), total=total, limit=limit, offset=offset)


@router.get("/{pipeline_id}", response_model=PipelineResponse)
def get_pipeline(
    pipeline_id: str,
    session: Session = Depends(get_pipeline_session),
    actor: User = Depends(get_current_user),
) -> TrainingPipeline:
    pipeline = session.get(TrainingPipeline, pipeline_id)
    if pipeline is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline not found")
    require_resource_permission(session, actor, "pipeline", pipeline_id, PERMISSION_VIEW)
    return pipeline


@router.patch("/{pipeline_id}", response_model=PipelineResponse)
def update_pipeline(
    pipeline_id: str,
    request: PipelineUpdateRequest,
    session: Session = Depends(get_pipeline_session),
    actor: User = Depends(get_current_user),
) -> TrainingPipeline:
    if request.dataset_id:
        require_resource_permission(session, actor, "dataset", request.dataset_id, PERMISSION_USE)
    pipeline = session.get(TrainingPipeline, pipeline_id)
    if pipeline is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline not found")
    require_resource_permission(session, actor, "pipeline", pipeline_id, PERMISSION_EDIT)
    engine = request.engine or pipeline.engine
    touches_training_config = any(
        value is not None
        for value in (
            request.task,
            request.engine,
            request.scale,
            request.base_model_id,
            request.dataset_id,
            request.params_template,
            request.default_environment,
        )
    )
    if touches_training_config and pipeline.status == "running":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Running pipeline cannot be edited")

    if request.name is not None:
        pipeline.name = request.name.strip()
    if request.engine is not None and request.engine != pipeline.engine and pipeline.status != "draft":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Training engine can only be changed on a draft pipeline")
    if engine == "llamafactory":
        task = request.task or pipeline.task
        if task != "llm":
            raise _unprocessable("LLaMA-Factory pipelines must use task llm")
        if request.base_model_id is not None:
            raise _unprocessable("LLaMA-Factory pipelines use an external model reference")
        params = request.params_template if request.params_template is not None else pipeline.params_template
        environment = request.default_environment if request.default_environment is not None else pipeline.default_environment
        dataset_id = request.dataset_id if "dataset_id" in request.model_fields_set else pipeline.dataset_id
        try:
            params = validate_llamafactory_config(params)
            environment = validate_llm_environment(environment)
            if dataset_id:
                validate_llm_dataset(session, dataset_id)
        except LlmTrainingConfigError as exc:
            raise _unprocessable(str(exc)) from exc
        pipeline.engine = engine
        pipeline.task = task
        pipeline.scale = request.scale or pipeline.scale
        pipeline.base_model_id = None
        pipeline.dataset_id = dataset_id
        pipeline.params_template = params
        pipeline.default_environment = environment
        pipeline.status = "ready" if dataset_id and params.get("model_id") else "draft"
        if request.is_public is not None:
            pipeline.is_public = request.is_public
        if request.public_scope is not None:
            pipeline.public_scope = request.public_scope
        if request.is_favorite is not None:
            pipeline.is_favorite = request.is_favorite
        session.add(pipeline)
        session.commit()
        session.refresh(pipeline)
        return pipeline

    if request.base_model_id is not None or request.dataset_id is not None or request.task is not None or request.scale is not None:
        base_model_id = request.base_model_id or pipeline.base_model_id
        dataset_id = request.dataset_id or pipeline.dataset_id
        if base_model_id is None or dataset_id is None:
            raise _unprocessable("base_model_id and dataset_id are required")
        base_model = session.get(BaseModel, base_model_id)
        if base_model is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Base model not found")
        task = request.task or base_model.task or pipeline.task
        scale = request.scale or base_model.scale or pipeline.scale
        try:
            validate_training_resources(
                session,
                task=task,
                scale=scale,
                base_model_id=base_model_id,
                dataset_id=dataset_id,
            )
        except TrainingPrecheckError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        pipeline.task = task
        pipeline.scale = scale
        pipeline.base_model_id = base_model_id
        pipeline.dataset_id = dataset_id
    if request.params_template is not None:
        try:
            pipeline.params_template = validate_training_params(request.params_template)
        except TrainingParamsError as exc:
            raise _unprocessable(str(exc)) from exc
    if request.default_environment is not None:
        try:
            pipeline.default_environment = validate_training_environment(request.default_environment)
        except TrainingParamsError as exc:
            raise _unprocessable(str(exc)) from exc
    if request.is_public is not None:
        pipeline.is_public = request.is_public
    if request.public_scope is not None:
        pipeline.public_scope = request.public_scope
    if request.is_favorite is not None:
        pipeline.is_favorite = request.is_favorite
    if touches_training_config:
        pipeline.status = "ready"

    session.add(pipeline)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="产线名称已存在，请使用其他名称") from exc
    session.refresh(pipeline)
    return pipeline


@router.delete("/{pipeline_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_pipeline(
    pipeline_id: str,
    session: Session = Depends(get_pipeline_session),
    actor: User = Depends(get_current_user),
) -> None:
    pipeline = session.get(TrainingPipeline, pipeline_id)
    if pipeline is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline not found")
    require_resource_permission(session, actor, "pipeline", pipeline_id, PERMISSION_DELETE)

    if session.scalar(
        select(DeploymentService.id).where(DeploymentService.pipeline_id == pipeline_id).limit(1)
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Delete pipeline services before deleting the pipeline",
        )

    job_ids = list(
        session.scalars(select(TrainingJob.id).where(TrainingJob.pipeline_id == pipeline_id))
    )
    task_ids: set[str] = set()
    if job_ids:
        task_ids.update(
            value
            for value in session.scalars(
                select(TrainingJob.task_id).where(
                    TrainingJob.id.in_(job_ids),
                    TrainingJob.task_id.is_not(None),
                )
            )
            if value is not None
        )
        run_ids = list(
            session.scalars(
                select(DistributedTrainingRun.id).where(
                    DistributedTrainingRun.training_job_id.in_(job_ids)
                )
            )
        )
        execution_filter = RemoteExecution.training_job_id.in_(job_ids)
        if run_ids:
            execution_filter = or_(
                execution_filter,
                RemoteExecution.resource_id.in_(run_ids),
            )
        task_ids.update(
            value
            for value in session.scalars(
                select(RemoteExecution.task_id).where(
                    execution_filter,
                    RemoteExecution.task_id.is_not(None),
                )
            )
            if value is not None
        )
        task_ids.update(
            session.scalars(
                select(Task.id).where(
                    Task.resource_type == "training_job",
                    Task.resource_id.in_(job_ids),
                )
            )
        )
        session.execute(delete(RemoteExecution).where(execution_filter))
        session.execute(
            delete(DistributedTrainingRun).where(
                DistributedTrainingRun.training_job_id.in_(job_ids)
            )
        )
        session.execute(
            delete(TrainedModel).where(
                or_(
                    TrainedModel.pipeline_id == pipeline_id,
                    TrainedModel.training_job_id.in_(job_ids),
                )
            )
        )
        session.execute(delete(TrainingJob).where(TrainingJob.id.in_(job_ids)))
        if task_ids:
            session.execute(delete(Task).where(Task.id.in_(task_ids)))
    else:
        session.execute(delete(TrainedModel).where(TrainedModel.pipeline_id == pipeline_id))
    session.execute(
        delete(PipelineEvaluation).where(PipelineEvaluation.pipeline_id == pipeline_id)
    )
    session.delete(pipeline)
    session.commit()
