from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict
from pydantic import Field
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from visiox_api.dependencies.auth import get_current_user
from visiox_api.dependencies.authorization import require_resource_permission
from visiox_api.dependencies.database import get_db_session
from visiox_api.services.authorization import (
    authorized_resource_predicate,
    resolve_permissions,
)
from visiox_api.services.framework_adapters import (
    FrameworkAdapterCatalog,
    get_framework_adapter_catalog,
)
from visiox_api.services.pipeline_configuration import (
    PipelineConfigurationError,
    PipelineConfigurationService,
)
from visiox_api.services.pipeline_locking import get_pipeline_for_update
from visiox_db.models import (
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


router = APIRouter(prefix="/pipelines", tags=["pipelines"])


class PipelineCreateRequest(PydanticBaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    engine: str | None = None
    task: str | None = None
    scale: str | None = None
    task_kind: str | None = None
    framework: str | None = None
    adapter_key: str | None = None
    adapter_version: str | None = None
    model_family: str | None = None
    recipe: dict[str, Any] = Field(default_factory=dict)
    base_model_id: str | None = None
    dataset_id: str | None = None
    params_template: dict[str, Any] = Field(default_factory=dict)
    default_environment: dict[str, Any] = Field(default_factory=dict)


class PipelineUpdateRequest(PydanticBaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=160)
    engine: str | None = None
    task: str | None = None
    scale: str | None = None
    task_kind: str | None = None
    framework: str | None = None
    adapter_key: str | None = None
    adapter_version: str | None = None
    model_family: str | None = None
    recipe: dict[str, Any] | None = None
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
    task_kind: str
    framework: str
    adapter_key: str
    adapter_version: str
    model_family: str
    recipe: dict[str, Any]
    framework_locked_at: datetime | None
    first_submitted_job_id: str | None
    cloned_from_pipeline_id: str | None
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


class PipelineCloneRequest(PydanticBaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    engine: str | None = None
    task: str | None = None
    scale: str | None = None
    task_kind: str | None = None
    framework: str | None = None
    adapter_key: str | None = None
    adapter_version: str | None = None
    model_family: str | None = None
    recipe: dict[str, Any] | None = None
    base_model_id: str | None = None
    dataset_id: str | None = None
    params_template: dict[str, Any] | None = None
    default_environment: dict[str, Any] | None = None


get_pipeline_session = get_db_session


def _unprocessable(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=message
    )


@router.post("", response_model=PipelineResponse)
def create_pipeline(
    request: PipelineCreateRequest,
    response: Response,
    session: Session = Depends(get_pipeline_session),
    actor: User = Depends(get_current_user),
    catalog: FrameworkAdapterCatalog = Depends(get_framework_adapter_catalog),
) -> TrainingPipeline:
    if request.dataset_id:
        require_resource_permission(
            session, actor, "dataset", request.dataset_id, PERMISSION_USE
        )
    try:
        configuration = PipelineConfigurationService(catalog).resolve_create(
            session,
            request.model_dump(),
            request.model_fields_set,
        )
    except PipelineConfigurationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    pipeline = TrainingPipeline(
        name=request.name.strip(),
        organization_id=actor.organization_id,
        owner_user_id=actor.id,
        visibility="private",
    )
    PipelineConfigurationService(catalog).apply(pipeline, configuration)
    session.add(pipeline)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="产线名称已存在，请使用其他名称",
        ) from exc
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
    list_query = select(TrainingPipeline).order_by(
        TrainingPipeline.created_at, TrainingPipeline.id
    )
    if filters:
        total_query = total_query.where(*filters)
        list_query = list_query.where(*filters)
    total = session.scalar(total_query) or 0
    items = session.scalars(list_query.limit(limit).offset(offset)).all()
    return PipelineListResponse(
        items=list(items), total=total, limit=limit, offset=offset
    )


@router.get("/{pipeline_id}", response_model=PipelineResponse)
def get_pipeline(
    pipeline_id: str,
    session: Session = Depends(get_pipeline_session),
    actor: User = Depends(get_current_user),
) -> TrainingPipeline:
    pipeline = session.get(TrainingPipeline, pipeline_id)
    if pipeline is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline not found"
        )
    require_resource_permission(
        session, actor, "pipeline", pipeline_id, PERMISSION_VIEW
    )
    return pipeline


@router.post("/{pipeline_id}/clone", response_model=PipelineResponse)
def clone_pipeline(
    pipeline_id: str,
    request: PipelineCloneRequest,
    response: Response,
    session: Session = Depends(get_pipeline_session),
    actor: User = Depends(get_current_user),
    catalog: FrameworkAdapterCatalog = Depends(get_framework_adapter_catalog),
) -> TrainingPipeline:
    source = session.get(TrainingPipeline, pipeline_id)
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline not found"
        )
    require_resource_permission(
        session, actor, "pipeline", pipeline_id, PERMISSION_VIEW
    )
    require_resource_permission(session, actor, "pipeline", pipeline_id, PERMISSION_USE)

    configuration_fields = {
        "engine",
        "task",
        "scale",
        "task_kind",
        "framework",
        "adapter_key",
        "adapter_version",
        "model_family",
        "recipe",
        "base_model_id",
        "dataset_id",
        "params_template",
        "default_environment",
    }
    values = {key: getattr(source, key) for key in configuration_fields}
    override_values = request.model_dump()
    values.update(
        {
            key: override_values[key]
            for key in request.model_fields_set
            if key in configuration_fields
        }
    )
    if values.get("dataset_id"):
        require_resource_permission(
            session,
            actor,
            "dataset",
            str(values["dataset_id"]),
            PERMISSION_USE,
        )
    try:
        configuration = PipelineConfigurationService(catalog).resolve_create(
            session,
            values,
            configuration_fields,
        )
    except PipelineConfigurationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    permissions = resolve_permissions(
        session,
        actor,
        "pipeline",
        source.id,
        source.owner_user_id or "",
    )
    may_copy_public_configuration = PERMISSION_EDIT in permissions
    clone = TrainingPipeline(
        name=request.name.strip(),
        organization_id=actor.organization_id,
        owner_user_id=actor.id,
        visibility=source.visibility if may_copy_public_configuration else "private",
        is_public=source.is_public if may_copy_public_configuration else False,
        public_scope=dict(source.public_scope or {})
        if may_copy_public_configuration
        else {},
        is_favorite=False,
        status="draft",
        framework_locked_at=None,
        first_submitted_job_id=None,
        cloned_from_pipeline_id=source.id,
    )
    PipelineConfigurationService(catalog).apply(clone, configuration)
    clone.status = "draft"
    session.add(clone)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Pipeline name already exists",
        ) from exc
    session.refresh(clone)
    response.status_code = status.HTTP_201_CREATED
    return clone


@router.patch("/{pipeline_id}", response_model=PipelineResponse)
def update_pipeline(
    pipeline_id: str,
    request: PipelineUpdateRequest,
    session: Session = Depends(get_pipeline_session),
    actor: User = Depends(get_current_user),
    catalog: FrameworkAdapterCatalog = Depends(get_framework_adapter_catalog),
) -> TrainingPipeline:
    if request.dataset_id:
        require_resource_permission(
            session, actor, "dataset", request.dataset_id, PERMISSION_USE
        )
    pipeline = get_pipeline_for_update(session, pipeline_id)
    if pipeline is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline not found"
        )
    require_resource_permission(
        session, actor, "pipeline", pipeline_id, PERMISSION_EDIT
    )
    configuration_fields = {
        "engine",
        "task",
        "scale",
        "task_kind",
        "framework",
        "adapter_key",
        "adapter_version",
        "model_family",
        "recipe",
        "base_model_id",
        "dataset_id",
        "params_template",
        "default_environment",
    }
    touches_training_config = bool(request.model_fields_set & configuration_fields)
    if touches_training_config:
        service = PipelineConfigurationService(catalog)
        current_configuration = service.from_pipeline(pipeline)
        try:
            configuration = service.resolve_update(
                session,
                pipeline,
                request.model_dump(),
                request.model_fields_set,
            )
        except PipelineConfigurationError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        effective_change = service.has_effective_change(
            current_configuration, configuration
        )
        if effective_change and pipeline.status == "running":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Running pipeline cannot be edited",
            )
        if effective_change:
            service.apply(pipeline, configuration)
    if request.name is not None:
        pipeline.name = request.name.strip()
    if request.is_public is not None:
        pipeline.is_public = request.is_public
    if request.public_scope is not None:
        pipeline.public_scope = request.public_scope
    if request.is_favorite is not None:
        pipeline.is_favorite = request.is_favorite

    session.add(pipeline)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="产线名称已存在，请使用其他名称",
        ) from exc
    session.refresh(pipeline)
    return pipeline


@router.delete("/{pipeline_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_pipeline(
    pipeline_id: str,
    session: Session = Depends(get_pipeline_session),
    actor: User = Depends(get_current_user),
) -> None:
    pipeline = get_pipeline_for_update(session, pipeline_id)
    if pipeline is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline not found"
        )
    require_resource_permission(
        session, actor, "pipeline", pipeline_id, PERMISSION_DELETE
    )

    if session.scalar(
        select(DeploymentService.id)
        .where(DeploymentService.pipeline_id == pipeline_id)
        .limit(1)
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Delete pipeline services before deleting the pipeline",
        )

    pipeline.first_submitted_job_id = None
    session.add(pipeline)
    session.flush()
    session.execute(
        update(TrainingPipeline)
        .where(TrainingPipeline.cloned_from_pipeline_id == pipeline_id)
        .values(cloned_from_pipeline_id=None)
    )
    session.flush()

    job_ids = list(
        session.scalars(
            select(TrainingJob.id).where(TrainingJob.pipeline_id == pipeline_id)
        )
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
        session.execute(
            delete(TrainedModel).where(TrainedModel.pipeline_id == pipeline_id)
        )
    session.execute(
        delete(PipelineEvaluation).where(PipelineEvaluation.pipeline_id == pipeline_id)
    )
    session.delete(pipeline)
    session.commit()
