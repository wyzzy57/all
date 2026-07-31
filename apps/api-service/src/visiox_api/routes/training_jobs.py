from __future__ import annotations

import copy
import inspect
import shutil
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from visiox_common.tasks import TaskCommand, TaskStatus, TaskType
from visiox_api.dependencies.auth import get_current_user
from visiox_api.dependencies.authorization import require_resource_permission
from visiox_api.dependencies.database import get_db_session
from visiox_api.services.authorization import authorized_resource_predicate
from visiox_api.services.pipeline_locking import (
    get_pipeline_for_update,
    lock_pipeline_to_first_job,
)
from visiox_api.services.training_submission import (
    TrainingSubmissionError,
    TrainingSubmissionService,
)
from visiox_api.services.resource_scheduler import freeze_distributed_allocation
from visiox_common.settings import Settings, get_settings
from visiox_db.base import new_id
from visiox_db.models import (
    ComputeNode,
    DistributedTrainingRun,
    EdgeSshCredential,
    RemoteExecution,
    ResourcePool,
    Task,
    TrainedModel,
    TrainingJob,
    TrainingJobAttempt,
    TrainingPipeline,
)
from visiox_db.models.identity import (
    PERMISSION_DELETE,
    PERMISSION_EDIT,
    PERMISSION_USE,
    PERMISSION_VIEW,
    User,
)
from visiox_edge_executor_worker.deployment import validate_image_digest
from visiox_edge_executor_worker.distributed import (
    DistributedNode,
    DistributedPlan,
    IncompatibleResourcePoolError,
    build_distributed_plan,
)
from visiox_edge_executor_worker.inventory import InventorySnapshot, compatibility_key
from visiox_messaging.streams import RedisStreamProducer
from visiox_storage.client import ObjectStorageClient
from visiox_yolo26.training.params import (
    TrainingParamsError,
    merge_training_params,
    validate_training_environment,
)
from visiox_yolo26.training.prechecks import (
    TrainingPrecheckError,
    validate_training_resources,
)
from visiox_api.services.llm_training import (
    LlmTrainingConfigError,
    validate_llamafactory_config,
    validate_llm_dataset,
    validate_llm_environment,
    resolve_llm_dataset_version,
)


router = APIRouter(tags=["training-jobs"])


class DistributedTrainingRequest(PydanticBaseModel):
    resource_pool_id: str = Field(min_length=1, max_length=128)
    requested_gpus: int = Field(ge=1)
    node_ids: list[str] | None = None
    training_image_digest: str | None = Field(
        default=None, min_length=1, max_length=512
    )
    master_port: int = Field(default=29500, ge=1024, le=65535)


class DistributedTrainingResumeRequest(PydanticBaseModel):
    distributed: DistributedTrainingRequest | None = None
    checkpoint_uri: str | None = Field(default=None, min_length=1, max_length=2048)
    checkpoint_checksum: str | None = Field(
        default=None,
        pattern=r"^[0-9a-fA-F]{64,128}$",
    )
    checkpoint_framework: str | None = Field(default=None, min_length=1, max_length=80)
    checkpoint_model_family: str | None = Field(
        default=None, min_length=1, max_length=120
    )


class TrainingJobCreateRequest(PydanticBaseModel):
    params: dict[str, Any] = Field(default_factory=dict)
    environment: dict[str, Any] = Field(default_factory=dict)
    distributed: DistributedTrainingRequest | None = None
    dataset_version_id: str | None = Field(default=None, min_length=1, max_length=128)


class TrainingJobResponse(PydanticBaseModel):
    id: str
    pipeline_id: str
    task_id: str | None
    trained_model_id: str | None
    status: str
    params: dict[str, Any]
    environment: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any]
    log_uri: str | None
    log_stream_id: str | None = None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime
    distributed_run_id: str | None = None
    remote_execution_id: str | None = None


class TrainingJobListResponse(PydanticBaseModel):
    items: list[TrainingJobResponse]
    total: int
    limit: int
    offset: int


class TrainingArtifactResponse(PydanticBaseModel):
    name: str
    kind: str
    size_bytes: int
    download_url: str


class TrainingArtifactListResponse(PydanticBaseModel):
    items: list[TrainingArtifactResponse]


get_training_job_session = get_db_session


def get_training_stream_producer(request: Request) -> RedisStreamProducer:
    return RedisStreamProducer(request.app.state.redis)


def get_training_object_storage_client(request: Request) -> ObjectStorageClient:
    storage = getattr(request.app.state, "object_storage", None)
    if storage is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Object storage is not configured",
        )
    return storage


async def _enqueue(producer: Any, command: TaskCommand) -> str:
    result = producer.enqueue(command)
    if inspect.isawaitable(result):
        return await result
    return str(result)


async def _enqueue_edge_execution(
    producer: Any,
    *,
    task_id: str,
    task_type: TaskType,
    remote_execution_id: str,
) -> str:
    result = producer.enqueue_edge_execution(
        task_id=task_id,
        task_type=task_type,
        remote_execution_id=remote_execution_id,
    )
    if inspect.isawaitable(result):
        return await result
    return str(result)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _job_response(
    job: TrainingJob,
    environment: dict[str, Any] | None = None,
    task: Task | None = None,
    *,
    distributed_run_id: str | None = None,
    remote_execution_id: str | None = None,
) -> TrainingJobResponse:
    if environment is None and task is not None:
        payload = task.payload or {}
        env_payload = payload.get("environment")
        environment = env_payload if isinstance(env_payload, dict) else {}
    observability = job.metrics.get("observability", {})
    log_stream_id = (
        observability.get("log_stream_id")
        if isinstance(observability, dict)
        and isinstance(observability.get("log_stream_id"), str)
        else None
    )
    return TrainingJobResponse(
        id=job.id,
        pipeline_id=job.pipeline_id,
        task_id=job.task_id,
        trained_model_id=job.trained_model_id,
        status=job.status,
        params=job.params,
        environment=environment or {},
        metrics=job.metrics,
        log_uri=job.log_uri,
        log_stream_id=log_stream_id,
        started_at=job.started_at,
        finished_at=job.finished_at,
        created_at=job.created_at,
        updated_at=job.updated_at,
        distributed_run_id=distributed_run_id,
        remote_execution_id=remote_execution_id,
    )


@router.post("/pipelines/{pipeline_id}/jobs", response_model=TrainingJobResponse)
async def create_training_job(
    pipeline_id: str,
    request: TrainingJobCreateRequest,
    response: Response,
    session: Session = Depends(get_training_job_session),
    producer: Any = Depends(get_training_stream_producer),
    settings: Settings = Depends(get_settings),
    actor: User = Depends(get_current_user),
) -> TrainingJobResponse:
    pipeline = get_pipeline_for_update(session, pipeline_id)
    if pipeline is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline not found"
        )
    require_resource_permission(
        session, actor, "pipeline", pipeline_id, PERMISSION_EDIT
    )
    if pipeline.dataset_id:
        require_resource_permission(
            session, actor, "dataset", pipeline.dataset_id, PERMISSION_USE
        )
    if pipeline.status != "ready":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Pipeline is not ready: {pipeline.status}",
        )
    if pipeline.engine == "llamafactory":
        return await _create_llm_training_job(
            session=session,
            producer=producer,
            pipeline=pipeline,
            request=request,
            response=response,
            settings=settings,
            actor=actor,
        )
    if pipeline.framework == "paddlex":
        if request.distributed is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="PaddleX training requires an edge GPU allocation",
            )
        if not settings.paddlex_training_image_digest.strip():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="PaddleX training image digest is not configured",
            )
        try:
            submission_service = TrainingSubmissionService(session, settings)
            params = submission_service.normalize_parameters(pipeline, request.params)
        except TrainingSubmissionError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        environment = {
            **copy.deepcopy(pipeline.default_environment or {}),
            **copy.deepcopy(request.environment),
        }
        return await _create_distributed_training_job(
            session=session,
            producer=producer,
            pipeline=pipeline,
            context_payload={
                "engine": "paddlex",
                "dataset_id": pipeline.dataset_id,
                "resolved_config": params,
            },
            params=params,
            environment=environment,
            distributed=request.distributed,
            response=response,
            image_digest_override=settings.paddlex_training_image_digest,
            actor=actor,
            settings=settings,
            dataset_version_id=request.dataset_version_id,
        )
    try:
        resources = validate_training_resources(
            session,
            task=pipeline.task,
            scale=pipeline.scale,
            base_model_id=str(pipeline.base_model_id),
            dataset_id=str(pipeline.dataset_id),
        )
    except TrainingPrecheckError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    try:
        params = merge_training_params(pipeline.params_template, request.params)
        environment = {
            **validate_training_environment(pipeline.default_environment),
            **validate_training_environment(request.environment),
        }
    except TrainingParamsError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    if request.distributed is not None:
        return await _create_distributed_training_job(
            session=session,
            producer=producer,
            pipeline=pipeline,
            context_payload={
                "engine": "yolo26",
                "dataset_id": resources.dataset.id,
                "base_model_id": resources.base_model.id,
            },
            params=params,
            environment=environment,
            distributed=request.distributed,
            response=response,
            actor=actor,
            settings=settings,
            dataset_version_id=request.dataset_version_id,
        )

    job_id = new_id()
    try:
        submission = TrainingSubmissionService(session, settings).resolve(
            pipeline=pipeline,
            job_id=job_id,
            attempt_number=1,
            parameters=params,
            environment=environment,
            resource_request={"kind": "local", "gpu_count": 0},
            allocation={
                "kind": "local",
                "device": str(environment.get("device", "cpu")),
            },
            dataset_version_id=request.dataset_version_id,
        )
    except TrainingSubmissionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    job = TrainingJob(
        id=job_id,
        pipeline_id=pipeline.id,
        status="queued",
        params=params,
        resolved_snapshot=submission.snapshot.model_dump(mode="json"),
        launch_spec_checksum=submission.launch_spec_checksum,
        organization_id=actor.organization_id,
        owner_user_id=actor.id,
        visibility="private",
    )
    task = Task(
        task_type=TaskType.TRAIN_MODEL.value,
        status=TaskStatus.QUEUED.value,
        progress=0,
        resource_type="training_job",
        payload={},
    )
    attempt = TrainingJobAttempt(
        training_job_id=job_id,
        attempt_number=1,
        status="queued",
        launch_spec=submission.launch_spec.model_dump(mode="json"),
        launch_spec_checksum=submission.launch_spec_checksum,
    )
    try:
        session.add_all([job, task, attempt])
        session.flush()
        task.resource_id = job.id
        job.task_id = task.id
        task.payload = {
            "pipeline_id": pipeline.id,
            "training_job_id": job.id,
            "dataset_id": resources.dataset.id,
            "base_model_id": resources.base_model.id,
            "params": params,
            "environment": environment,
        }
        pipeline.status = "running"
        lock_pipeline_to_first_job(pipeline, job.id)
        session.add(pipeline)
        session.commit()
    except Exception:
        session.rollback()
        raise
    session.refresh(job)
    session.refresh(task)
    response.status_code = status.HTTP_201_CREATED

    command = TaskCommand(
        task_id=task.id,
        task_type=TaskType.TRAIN_MODEL,
        resource_refs={"training_job_id": job.id},
        payload=task.payload,
    )
    try:
        await _enqueue(producer, command)
    except Exception as exc:
        now = _utc_now()
        job.status = "failed"
        job.finished_at = now
        pipeline.status = "failed"
        task.status = TaskStatus.FAILED.value
        task.error_code = "ENQUEUE_FAILED"
        task.error_message = str(exc)
        task.finished_at = now
        task.retryable = True
        session.add_all([job, pipeline, task])
        session.commit()
        session.refresh(job)
        session.refresh(task)

    return _job_response(job, environment, task)


async def _create_llm_training_job(
    *,
    session: Session,
    producer: Any,
    pipeline: TrainingPipeline,
    request: TrainingJobCreateRequest,
    response: Response,
    settings: Settings,
    actor: User,
) -> TrainingJobResponse:
    if request.distributed is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="LLM training requires an edge GPU node",
        )
    if (
        request.distributed.requested_gpus != 1
        or not request.distributed.node_ids
        or len(request.distributed.node_ids) != 1
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="the first LLM release supports one node and one GPU",
        )
    try:
        dataset = validate_llm_dataset(session, str(pipeline.dataset_id))
        dataset_version = resolve_llm_dataset_version(
            session, dataset.id, request.dataset_version_id
        )
        params = validate_llamafactory_config(
            {**(pipeline.params_template or {}), **request.params}
        )
        environment = {
            **validate_llm_environment(pipeline.default_environment),
            **validate_llm_environment(request.environment),
        }
    except LlmTrainingConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    model_id = params.get("model_id")
    resolved_revision = params.get("resolved_revision")
    if not isinstance(model_id, str) or not model_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="LLM model reference is unavailable",
        )
    if not isinstance(resolved_revision, str) or not resolved_revision:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="LLM model revision has not been resolved",
        )
    if not dataset_version.manifest_checksum:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="LLM dataset manifest is unavailable",
        )
    image_digest = settings.llm_training_image_digest.strip()
    if not image_digest:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LLM training image is not configured",
        )
    return await _create_distributed_training_job(
        session=session,
        producer=producer,
        pipeline=pipeline,
        context_payload={
            "engine": "llamafactory",
            "dataset_id": dataset.id,
            "dataset_version_id": dataset_version.id,
            "dataset_version": dataset_version.version,
            "dataset_uri": dataset_version.object_uri,
            "dataset_format": dataset_version.format,
            "dataset_manifest_checksum": dataset_version.manifest_checksum,
            "model_reference": {
                "source": params.get("model_source"),
                "model_id": model_id,
                "revision": resolved_revision,
            },
            "resolved_config": params,
        },
        params=params,
        environment=environment,
        distributed=request.distributed,
        response=response,
        image_digest_override=image_digest,
        actor=actor,
        settings=settings,
        dataset_version_id=dataset_version.id,
    )


async def _create_distributed_training_job(
    *,
    session: Session,
    producer: Any,
    pipeline: TrainingPipeline,
    context_payload: dict[str, Any],
    params: dict[str, Any],
    environment: dict[str, Any],
    distributed: DistributedTrainingRequest,
    response: Response,
    image_digest_override: str | None = None,
    actor: User,
    settings: Settings,
    dataset_version_id: str | None = None,
) -> TrainingJobResponse:
    plan, image_digest = _distributed_plan(
        session,
        distributed,
        image_digest_override=image_digest_override,
    )
    _authorize_distributed_plan(session, actor, plan)
    job_id = new_id()
    task_id = new_id()
    run_id = new_id()
    execution_id = new_id()
    ranks = _rank_payloads(plan)
    resource_request, allocation = freeze_distributed_allocation(plan)
    try:
        submission = TrainingSubmissionService(session, settings).resolve(
            pipeline=pipeline,
            job_id=job_id,
            attempt_number=1,
            parameters=params,
            environment=environment,
            resource_request=resource_request,
            allocation=allocation,
            runtime_image_digest=image_digest,
            dataset_version_id=dataset_version_id,
        )
    except TrainingSubmissionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    job = TrainingJob(
        id=job_id,
        pipeline_id=pipeline.id,
        status="queued",
        params=params,
        resolved_snapshot=submission.snapshot.model_dump(mode="json"),
        launch_spec_checksum=submission.launch_spec_checksum,
        metrics={"dataset_snapshot": context_payload}
        if context_payload.get("dataset_version_id")
        else {},
        organization_id=actor.organization_id,
        owner_user_id=actor.id,
        visibility="private",
    )
    task = Task(
        id=task_id,
        task_type=TaskType.EDGE_TRAIN.value,
        status=TaskStatus.QUEUED.value,
        progress=0,
        resource_type="training_job",
        resource_id=job_id,
        payload={
            "pipeline_id": pipeline.id,
            "training_job_id": job_id,
            "distributed_training_run_id": run_id,
            **context_payload,
            "params": params,
            "environment": environment,
        },
    )
    run = DistributedTrainingRun(
        id=run_id,
        training_job_id=job_id,
        resource_pool_id=plan.resource_pool_id,
        node_ids=[rank.node_id for rank in plan.nodes],
        ranks=ranks,
        master_addr=plan.master_addr,
        master_port=plan.master_port,
        world_size=plan.world_size,
        rendezvous_backend=plan.rendezvous_backend,
        training_image_digest=image_digest,
        attempt=1,
        status="queued",
    )
    execution = RemoteExecution(
        id=execution_id,
        node_id=plan.nodes[0].node_id,
        task_id=task_id,
        training_job_id=job_id,
        resource_type="distributed_training_run",
        resource_id=run_id,
        operation="train",
        phase="queued",
        status="queued",
        idempotency_key=f"train:{run_id}:1",
    )
    job.task_id = task_id
    attempt_record = TrainingJobAttempt(
        training_job_id=job_id,
        attempt_number=1,
        status="queued",
        launch_spec=submission.launch_spec.model_dump(mode="json"),
        launch_spec_checksum=submission.launch_spec_checksum,
    )
    try:
        pipeline.status = "running"
        session.add(task)
        session.flush()
        session.add_all([job, pipeline, attempt_record])
        session.flush()
        lock_pipeline_to_first_job(pipeline, job_id)
        session.add(pipeline)
        session.flush()
        session.add(run)
        session.flush()
        session.add(execution)
        session.commit()
    except Exception:
        session.rollback()
        raise
    response.status_code = status.HTTP_201_CREATED
    try:
        await _enqueue_edge_execution(
            producer,
            task_id=task_id,
            task_type=TaskType.EDGE_TRAIN,
            remote_execution_id=execution_id,
        )
    except Exception as exc:
        _mark_distributed_enqueue_failed(
            session,
            job=job,
            pipeline=pipeline,
            task=task,
            run=run,
            execution=execution,
            error=exc,
        )
    session.refresh(job)
    session.refresh(task)
    return _job_response(
        job,
        environment,
        task,
        distributed_run_id=run.id,
        remote_execution_id=execution.id,
    )


@router.post(
    "/training-jobs/{training_job_id}/stop",
    response_model=TrainingJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def stop_distributed_training_job(
    training_job_id: str,
    session: Session = Depends(get_training_job_session),
    producer: Any = Depends(get_training_stream_producer),
    actor: User = Depends(get_current_user),
) -> TrainingJobResponse:
    job = session.get(TrainingJob, training_job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Training job not found"
        )
    require_resource_permission(
        session, actor, "training_job", training_job_id, PERMISSION_EDIT
    )
    run = _latest_distributed_run(session, job.id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Training job is not distributed",
        )
    if run.status not in {"queued", "running", "resuming"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Distributed training cannot be stopped from status: {run.status}",
        )
    task_id = new_id()
    execution_id = new_id()
    task = Task(
        id=task_id,
        task_type=TaskType.EDGE_STOP_TRAINING.value,
        status=TaskStatus.QUEUED.value,
        progress=0,
        resource_type="training_job",
        resource_id=job.id,
        payload={"distributed_training_run_id": run.id},
    )
    execution = RemoteExecution(
        id=execution_id,
        node_id=run.node_ids[0],
        task_id=task_id,
        training_job_id=job.id,
        resource_type="distributed_training_run",
        resource_id=run.id,
        operation="stop_training",
        phase="queued",
        status="queued",
        idempotency_key=f"stop_training:{run.id}:{execution_id}",
    )
    session.add(task)
    session.flush()
    job.task_id = task_id
    job.status = "stopping"
    run.status = "stopping"
    session.add_all([job, run])
    session.flush()
    session.add(execution)
    session.commit()
    try:
        await _enqueue_edge_execution(
            producer,
            task_id=task_id,
            task_type=TaskType.EDGE_STOP_TRAINING,
            remote_execution_id=execution_id,
        )
    except Exception as exc:
        pipeline = session.get(TrainingPipeline, job.pipeline_id)
        _mark_distributed_enqueue_failed(
            session,
            job=job,
            pipeline=pipeline,
            task=task,
            run=run,
            execution=execution,
            error=exc,
        )
    session.refresh(job)
    return _job_response(
        job,
        task=task,
        distributed_run_id=run.id,
        remote_execution_id=execution.id,
    )


@router.post(
    "/training-jobs/{training_job_id}/resume",
    response_model=TrainingJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def resume_distributed_training_job(
    training_job_id: str,
    request: DistributedTrainingResumeRequest,
    session: Session = Depends(get_training_job_session),
    producer: Any = Depends(get_training_stream_producer),
    actor: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> TrainingJobResponse:
    job = session.scalar(
        select(TrainingJob).where(TrainingJob.id == training_job_id).with_for_update()
    )
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Training job not found"
        )
    require_resource_permission(
        session, actor, "training_job", training_job_id, PERMISSION_EDIT
    )
    previous = _latest_distributed_run(session, job.id)
    if previous is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Training job is not distributed",
        )
    if previous.status not in {"failed", "stopped", "canceled"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Distributed training cannot be resumed from status: {previous.status}",
        )
    checkpoint_uri = previous.checkpoint_uri
    checkpoint_checksum = previous.checkpoint_checksum
    _validate_checkpoint(checkpoint_uri, checkpoint_checksum)
    if request.checkpoint_uri is not None and request.checkpoint_uri != checkpoint_uri:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Checkpoint URI does not match the executor-managed checkpoint",
        )
    if (
        request.checkpoint_checksum is not None
        and request.checkpoint_checksum.casefold() != checkpoint_checksum.casefold()
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Checkpoint checksum does not match the executor-managed checkpoint",
        )
    if previous.training_image_digest is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Distributed training image is unavailable for resume",
        )
    distributed = request.distributed or DistributedTrainingRequest(
        resource_pool_id=previous.resource_pool_id,
        requested_gpus=previous.world_size,
        node_ids=list(previous.node_ids),
        training_image_digest=previous.training_image_digest,
        master_port=previous.master_port,
    )
    if distributed.resource_pool_id != previous.resource_pool_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Resume must use the original compatible resource pool",
        )
    if distributed.training_image_digest != previous.training_image_digest:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Resume must use the original immutable training image",
        )
    plan, image_digest = _distributed_plan(session, distributed)
    _authorize_distributed_plan(session, actor, plan)
    context = _distributed_training_context(session, job)
    attempt = (
        int(
            session.scalar(
                select(func.max(TrainingJobAttempt.attempt_number)).where(
                    TrainingJobAttempt.training_job_id == job.id
                )
            )
            or 0
        )
        + 1
    )
    try:
        submission = TrainingSubmissionService(session, settings).resolve_retry(
            job=job,
            attempt_number=attempt,
            checkpoint_run=previous,
            checkpoint_framework=request.checkpoint_framework,
            checkpoint_model_family=request.checkpoint_model_family,
        )
    except TrainingSubmissionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    run_id = new_id()
    task_id = new_id()
    execution_id = new_id()
    run = DistributedTrainingRun(
        id=run_id,
        training_job_id=job.id,
        resource_pool_id=plan.resource_pool_id,
        node_ids=[rank.node_id for rank in plan.nodes],
        ranks=_rank_payloads(plan),
        master_addr=plan.master_addr,
        master_port=plan.master_port,
        world_size=plan.world_size,
        rendezvous_backend=plan.rendezvous_backend,
        training_image_digest=image_digest,
        checkpoint_uri=checkpoint_uri,
        checkpoint_checksum=checkpoint_checksum,
        attempt=attempt,
        status="queued",
    )
    task = Task(
        id=task_id,
        task_type=TaskType.EDGE_RESUME_TRAINING.value,
        status=TaskStatus.QUEUED.value,
        progress=0,
        resource_type="training_job",
        resource_id=job.id,
        payload={**context, "distributed_training_run_id": run_id},
    )
    execution = RemoteExecution(
        id=execution_id,
        node_id=plan.nodes[0].node_id,
        task_id=task_id,
        training_job_id=job.id,
        resource_type="distributed_training_run",
        resource_id=run_id,
        operation="resume_training",
        phase="queued",
        status="queued",
        idempotency_key=f"resume_training:{run_id}:{attempt}",
    )
    pipeline = session.get(TrainingPipeline, job.pipeline_id)
    attempt_record = TrainingJobAttempt(
        training_job_id=job.id,
        attempt_number=attempt,
        status="queued",
        launch_spec=submission.launch_spec.model_dump(mode="json"),
        launch_spec_checksum=submission.launch_spec_checksum,
    )
    try:
        session.add_all([task, attempt_record])
        session.flush()
        job.task_id = task_id
        job.status = "queued"
        job.finished_at = None
        if pipeline is not None:
            pipeline.status = "running"
        session.add_all([job, run])
        if pipeline is not None:
            session.add(pipeline)
        session.flush()
        session.add(execution)
        session.commit()
    except Exception:
        session.rollback()
        raise
    try:
        await _enqueue_edge_execution(
            producer,
            task_id=task_id,
            task_type=TaskType.EDGE_RESUME_TRAINING,
            remote_execution_id=execution_id,
        )
    except Exception as exc:
        _mark_distributed_enqueue_failed(
            session,
            job=job,
            pipeline=pipeline,
            task=task,
            run=run,
            execution=execution,
            error=exc,
        )
    session.refresh(job)
    return _job_response(
        job,
        context.get("environment", {}),
        task,
        distributed_run_id=run.id,
        remote_execution_id=execution.id,
    )


def _distributed_plan(
    session: Session,
    request: DistributedTrainingRequest,
    *,
    image_digest_override: str | None = None,
) -> tuple[DistributedPlan, str]:
    pool = session.get(ResourcePool, request.resource_pool_id)
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource pool not found"
        )
    if not pool.enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Resource pool is disabled"
        )
    try:
        image_digest = validate_image_digest(
            image_digest_override or request.training_image_digest or ""
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    requested_node_ids = request.node_ids
    if requested_node_ids is not None and (
        not requested_node_ids
        or len(requested_node_ids) != len(set(requested_node_ids))
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Distributed node_ids must be non-empty and unique",
        )
    query = select(ComputeNode).where(
        ComputeNode.resource_pool_id == pool.id,
        ComputeNode.status == "online",
    )
    if requested_node_ids is not None:
        query = query.where(ComputeNode.id.in_(requested_node_ids))
    nodes = session.scalars(query.order_by(ComputeNode.id)).all()
    if requested_node_ids is not None and {node.id for node in nodes} != set(
        requested_node_ids
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="One or more requested nodes are not available",
        )
    distributed_nodes: list[DistributedNode] = []
    try:
        for node in nodes:
            snapshot = InventorySnapshot.model_validate(
                node.fingerprint.get("inventory_snapshot")
            )
            key = compatibility_key(snapshot)
            if (
                snapshot.platform_kind != pool.kind
                or pool.compatibility_policy.get("compatibility_key") != key
            ):
                raise IncompatibleResourcePoolError(
                    "node inventory is incompatible with the resource pool"
                )
            credential = session.scalar(
                select(EdgeSshCredential).where(EdgeSshCredential.node_id == node.id)
            )
            if credential is None:
                raise IncompatibleResourcePoolError(
                    "distributed node does not have an SSH credential"
                )
            gpu_uuids = tuple(gpu.uuid for gpu in snapshot.gpus if gpu.uuid is not None)
            distributed_nodes.append(
                DistributedNode(
                    node_id=node.id,
                    resource_pool_id=pool.id,
                    platform_kind=snapshot.platform_kind,
                    architecture=snapshot.architecture,
                    compatibility_key=key,
                    lan_address=credential.ssh_host,
                    gpu_uuids=gpu_uuids,
                    status=node.status,
                    draining=node.status == "draining",
                )
            )
        plan = build_distributed_plan(
            distributed_nodes,
            requested_gpus=request.requested_gpus,
            master_port=request.master_port,
        )
    except (ValueError, IncompatibleResourcePoolError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    return plan, image_digest


def _authorize_distributed_plan(
    session: Session,
    actor: User,
    plan: DistributedPlan,
) -> None:
    require_resource_permission(
        session,
        actor,
        "resource_pool",
        plan.resource_pool_id,
        PERMISSION_USE,
    )
    for node in plan.nodes:
        require_resource_permission(
            session,
            actor,
            "node",
            node.node_id,
            PERMISSION_USE,
        )


def _rank_payloads(plan: DistributedPlan) -> list[dict[str, Any]]:
    return [
        {
            "node_id": rank.node_id,
            "node_rank": rank.node_rank,
            "lan_address": rank.lan_address,
            "gpu_uuids": list(rank.gpu_uuids),
        }
        for rank in plan.nodes
    ]


def _latest_distributed_run(
    session: Session,
    training_job_id: str,
) -> DistributedTrainingRun | None:
    return session.scalar(
        select(DistributedTrainingRun)
        .where(DistributedTrainingRun.training_job_id == training_job_id)
        .order_by(
            DistributedTrainingRun.attempt.desc(),
            DistributedTrainingRun.created_at.desc(),
            DistributedTrainingRun.id.desc(),
        )
    )


def _distributed_response_refs(
    session: Session,
    training_job_id: str,
) -> tuple[str | None, str | None]:
    run = _latest_distributed_run(session, training_job_id)
    if run is None:
        return None, None
    execution = session.scalar(
        select(RemoteExecution)
        .where(
            RemoteExecution.training_job_id == training_job_id,
            RemoteExecution.resource_type == "distributed_training_run",
            RemoteExecution.resource_id == run.id,
        )
        .order_by(RemoteExecution.created_at.desc(), RemoteExecution.id.desc())
    )
    return run.id, execution.id if execution is not None else None


def _distributed_training_context(session: Session, job: TrainingJob) -> dict[str, Any]:
    task = session.scalar(
        select(Task)
        .where(
            Task.resource_type == "training_job",
            Task.resource_id == job.id,
            Task.task_type.in_(
                [TaskType.EDGE_TRAIN.value, TaskType.EDGE_RESUME_TRAINING.value]
            ),
        )
        .order_by(Task.created_at.desc(), Task.id.desc())
    )
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Distributed training context is unavailable",
        )
    payload = dict(task.payload or {})
    payload.pop("distributed_training_run_id", None)
    return payload


def _validate_checkpoint(uri: str | None, checksum: str | None) -> None:
    if uri is None or checksum is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A durable checkpoint URI and checksum are required to resume",
        )
    remainder = uri.removeprefix("minio://")
    if remainder == uri or "/" not in remainder or not all(remainder.split("/", 1)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Resume checkpoint must use a durable MinIO URI",
        )


def _mark_distributed_enqueue_failed(
    session: Session,
    *,
    job: TrainingJob,
    pipeline: TrainingPipeline | None,
    task: Task,
    run: DistributedTrainingRun,
    execution: RemoteExecution,
    error: Exception,
) -> None:
    now = _utc_now()
    job.status = "failed"
    job.finished_at = now
    run.status = "failed"
    run.finished_at = now
    task.status = TaskStatus.FAILED.value
    task.error_code = "ENQUEUE_FAILED"
    task.error_message = str(error)
    task.finished_at = now
    task.retryable = True
    execution.status = "failed"
    execution.phase = "enqueue"
    execution.error_code = "ENQUEUE_FAILED"
    execution.error_message = str(error)
    execution.finished_at = now
    if pipeline is not None:
        pipeline.status = "failed"
        session.add(pipeline)
    session.add_all([job, run, task, execution])
    session.commit()


@router.get("/training-jobs", response_model=TrainingJobListResponse)
def list_training_jobs(
    pipeline_id: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_training_job_session),
    actor: User = Depends(get_current_user),
) -> TrainingJobListResponse:
    filters = [
        authorized_resource_predicate(
            session, actor, TrainingJob, "training_job", PERMISSION_VIEW
        )
    ]
    if pipeline_id is not None:
        filters.append(TrainingJob.pipeline_id == pipeline_id)
    if status_filter is not None:
        filters.append(TrainingJob.status == status_filter)
    total_query = select(func.count()).select_from(TrainingJob)
    list_query = select(TrainingJob).order_by(TrainingJob.created_at, TrainingJob.id)
    if filters:
        total_query = total_query.where(*filters)
        list_query = list_query.where(*filters)
    total = session.scalar(total_query) or 0
    jobs = session.scalars(list_query.limit(limit).offset(offset)).all()
    items = []
    for job in jobs:
        run_id, execution_id = _distributed_response_refs(session, job.id)
        items.append(
            _job_response(
                job,
                task=session.get(Task, job.task_id) if job.task_id else None,
                distributed_run_id=run_id,
                remote_execution_id=execution_id,
            )
        )
    return TrainingJobListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/training-jobs/{training_job_id}", response_model=TrainingJobResponse)
def get_training_job(
    training_job_id: str,
    session: Session = Depends(get_training_job_session),
    actor: User = Depends(get_current_user),
) -> TrainingJobResponse:
    job = session.get(TrainingJob, training_job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Training job not found"
        )
    require_resource_permission(
        session, actor, "training_job", training_job_id, PERMISSION_VIEW
    )
    task = session.get(Task, job.task_id) if job.task_id else None
    run_id, execution_id = _distributed_response_refs(session, job.id)
    return _job_response(
        job,
        task=task,
        distributed_run_id=run_id,
        remote_execution_id=execution_id,
    )


@router.delete(
    "/training-jobs/{training_job_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_training_job(
    training_job_id: str,
    session: Session = Depends(get_training_job_session),
    storage: ObjectStorageClient = Depends(get_training_object_storage_client),
    actor: User = Depends(get_current_user),
) -> None:
    job = session.get(TrainingJob, training_job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Training job not found"
        )
    require_resource_permission(
        session, actor, "training_job", training_job_id, PERMISSION_DELETE
    )
    if job.status in {"pending", "queued", "running"}:
        has_execution_link = (
            job.task_id is not None
            or session.scalar(
                select(DistributedTrainingRun.id).where(
                    DistributedTrainingRun.training_job_id == job.id
                )
            )
            is not None
            or session.scalar(
                select(RemoteExecution.id).where(
                    RemoteExecution.training_job_id == job.id
                )
            )
            is not None
        )
        if job.status != "queued" or has_execution_link:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Active training jobs must be canceled first",
            )

    metrics = job.metrics if isinstance(job.metrics, dict) else {}
    removable_uris = list(_string_map(metrics.get("visualizations")).values())
    if job.log_uri:
        removable_uris.append(job.log_uri)
    for uri in removable_uris:
        try:
            bucket, object_name = _parse_storage_uri(uri)
            storage.delete_file(bucket, object_name)
        except Exception:
            # Database cleanup must still succeed when an optional artifact has already expired.
            pass

    run_path = get_settings().training_runs_root / "runs" / f"job-{job.id}"
    shutil.rmtree(run_path, ignore_errors=True)

    task = session.get(Task, job.task_id) if job.task_id else None
    for execution in session.scalars(
        select(RemoteExecution).where(RemoteExecution.training_job_id == job.id)
    ).all():
        session.delete(execution)
    for run in session.scalars(
        select(DistributedTrainingRun).where(
            DistributedTrainingRun.training_job_id == job.id
        )
    ).all():
        session.delete(run)
    for model in session.scalars(
        select(TrainedModel).where(TrainedModel.training_job_id == job.id)
    ).all():
        model.training_job_id = None
        session.add(model)
    for attempt in session.scalars(
        select(TrainingJobAttempt).where(TrainingJobAttempt.training_job_id == job.id)
    ).all():
        session.delete(attempt)
    session.flush()
    session.delete(job)
    session.flush()
    if task is not None:
        session.delete(task)
    session.commit()


@router.get("/training-jobs/{training_job_id}/log")
def get_training_job_log(
    training_job_id: str,
    session: Session = Depends(get_training_job_session),
    storage: ObjectStorageClient = Depends(get_training_object_storage_client),
    actor: User = Depends(get_current_user),
) -> FileResponse:
    job = session.get(TrainingJob, training_job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Training job not found"
        )
    require_resource_permission(
        session, actor, "training_job", training_job_id, PERMISSION_VIEW
    )
    if not job.log_uri:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Training log not found"
        )
    bucket, object_name = _parse_storage_uri(job.log_uri)
    with NamedTemporaryFile(
        delete=False, suffix=f"-{training_job_id}.log"
    ) as temp_file:
        temp_path = Path(temp_file.name)
    storage.get_file(bucket, object_name, temp_path)
    return FileResponse(
        temp_path,
        media_type="text/plain; charset=utf-8",
        filename=f"{training_job_id}.log",
        background=BackgroundTask(temp_path.unlink, missing_ok=True),
    )


@router.get("/training-jobs/{training_job_id}/visualizations/{name}")
def get_training_job_visualization(
    training_job_id: str,
    name: str,
    session: Session = Depends(get_training_job_session),
    storage: ObjectStorageClient = Depends(get_training_object_storage_client),
    actor: User = Depends(get_current_user),
) -> FileResponse:
    job = session.get(TrainingJob, training_job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Training job not found"
        )
    require_resource_permission(
        session, actor, "training_job", training_job_id, PERMISSION_VIEW
    )
    visualizations = (
        job.metrics.get("visualizations") if isinstance(job.metrics, dict) else None
    )
    if not isinstance(visualizations, dict):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Training visualization not found",
        )
    uri = visualizations.get(name)
    if not isinstance(uri, str):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Training visualization not found",
        )
    bucket, object_name = _parse_storage_uri(uri)
    suffix = Path(name).suffix or ".png"
    with NamedTemporaryFile(
        delete=False, suffix=f"-{_safe_name(name) or 'visualization'}"
    ) as temp_file:
        temp_path = Path(temp_file.name)
    storage.get_file(bucket, object_name, temp_path)
    return FileResponse(
        temp_path,
        media_type=_media_type(suffix),
        background=BackgroundTask(temp_path.unlink, missing_ok=True),
    )


@router.get(
    "/training-jobs/{training_job_id}/artifacts",
    response_model=TrainingArtifactListResponse,
)
def list_training_job_artifacts(
    training_job_id: str,
    session: Session = Depends(get_training_job_session),
    storage: ObjectStorageClient = Depends(get_training_object_storage_client),
    actor: User = Depends(get_current_user),
) -> TrainingArtifactListResponse:
    job = session.get(TrainingJob, training_job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Training job not found"
        )
    require_resource_permission(
        session, actor, "training_job", training_job_id, PERMISSION_VIEW
    )
    artifact_uris = _training_artifact_uris(job, session)
    items: list[TrainingArtifactResponse] = []
    for kind in ("weight", "visualization"):
        for name, uri in artifact_uris[kind].items():
            bucket, object_name = _parse_storage_uri(uri)
            items.append(
                TrainingArtifactResponse(
                    name=name,
                    kind=kind,
                    size_bytes=storage.object_size(bucket, object_name),
                    download_url=f"/training-jobs/{job.id}/artifacts/{kind}/{name}",
                )
            )
    return TrainingArtifactListResponse(items=items)


@router.get("/training-jobs/{training_job_id}/artifacts/{kind}/{name}")
def download_training_job_artifact(
    training_job_id: str,
    kind: str,
    name: str,
    session: Session = Depends(get_training_job_session),
    storage: ObjectStorageClient = Depends(get_training_object_storage_client),
    actor: User = Depends(get_current_user),
) -> FileResponse:
    job = session.get(TrainingJob, training_job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Training job not found"
        )
    require_resource_permission(
        session, actor, "training_job", training_job_id, PERMISSION_VIEW
    )
    if kind not in {"weight", "visualization"}:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Training artifact not found"
        )
    uri = _training_artifact_uris(job, session)[kind].get(name)
    if not uri:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Training artifact not found"
        )
    bucket, object_name = _parse_storage_uri(uri)
    safe_name = _safe_name(name)
    with NamedTemporaryFile(
        delete=False, suffix=f"-{safe_name or 'artifact'}"
    ) as temp_file:
        temp_path = Path(temp_file.name)
    storage.get_file(bucket, object_name, temp_path)
    return FileResponse(
        temp_path,
        media_type=_media_type(Path(name).suffix),
        filename=safe_name or "artifact",
        background=BackgroundTask(temp_path.unlink, missing_ok=True),
    )


def _training_artifact_uris(
    job: TrainingJob, session: Session
) -> dict[str, dict[str, str]]:
    metrics = job.metrics if isinstance(job.metrics, dict) else {}
    weights = _string_map(metrics.get("weights"))
    visualizations = _string_map(metrics.get("visualizations"))
    if not weights:
        models = session.scalars(
            select(TrainedModel)
            .where(
                TrainedModel.pipeline_id == job.pipeline_id,
                TrainedModel.status == "ready",
            )
            .order_by(TrainedModel.created_at.desc(), TrainedModel.id.desc())
        ).all()
        for model in models:
            _bucket, object_name = _parse_storage_uri(model.artifact_uri)
            name = _safe_name(Path(object_name).name)
            if name and name not in weights:
                weights[name] = model.artifact_uri
    return {
        "weight": dict(
            sorted(weights.items(), key=lambda item: _artifact_sort_key(item[0]))
        ),
        "visualization": dict(sorted(visualizations.items())),
    }


def _string_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        _safe_name(str(name)): uri
        for name, uri in value.items()
        if _safe_name(str(name)) and isinstance(uri, str) and uri
    }


def _artifact_sort_key(name: str) -> tuple[int, str]:
    if name == "best.pt":
        return 0, name
    if name == "last.pt":
        return 1, name
    return 2, name


def _parse_storage_uri(uri: str) -> tuple[str, str]:
    marker = "://"
    if marker not in uri:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Training artifact is not available",
        )
    remainder = uri.split(marker, 1)[1]
    bucket, separator, object_name = remainder.partition("/")
    if not separator or not bucket or not object_name:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Training artifact is not available",
        )
    return bucket, object_name


def _safe_name(name: str) -> str:
    return "".join(
        character if character.isalnum() or character in {".", "-", "_"} else "_"
        for character in name
    )


def _media_type(suffix: str) -> str:
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
    }.get(suffix.lower(), "application/octet-stream")
