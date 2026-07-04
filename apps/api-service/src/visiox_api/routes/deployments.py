from __future__ import annotations

import inspect
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from visiox_common.tasks import TaskCommand, TaskStatus, TaskType
from visiox_db.models import Deployment, Device, EdgeAppVersion, Task
from visiox_db.session import get_session
from visiox_messaging.streams import RedisStreamProducer


router = APIRouter(prefix="/deployments", tags=["deployments"])
ACTIVE_DEPLOYMENT_STATUSES = {"pending", "deploying", "stopping", "rolling_back"}


class DeploymentCreateRequest(BaseModel):
    device_id: str
    edge_app_version_id: str


class DeploymentRollbackRequest(BaseModel):
    target_edge_app_version_id: str


class DeploymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    device_id: str
    edge_app_version_id: str
    task_id: str | None
    status: str
    active: bool
    deployed_at: datetime | None
    stopped_at: datetime | None
    logs_uri: str | None
    created_at: datetime
    updated_at: datetime


class DeploymentListResponse(BaseModel):
    items: list[DeploymentResponse]
    total: int
    limit: int
    offset: int


def get_deployment_session() -> Generator[Session]:
    yield from get_session()


def get_deployment_stream_producer(request: Request) -> RedisStreamProducer:
    return RedisStreamProducer(request.app.state.redis)


async def _enqueue(producer: Any, command: TaskCommand) -> str:
    result = producer.enqueue(command)
    if inspect.isawaitable(result):
        return await result
    return str(result)


@router.post("", response_model=DeploymentResponse)
async def create_deployment(
    request: DeploymentCreateRequest,
    response: Response,
    session: Session = Depends(get_deployment_session),
    producer: Any = Depends(get_deployment_stream_producer),
) -> Deployment:
    device = _device_or_404(session, request.device_id)
    version = _ready_version_or_409(session, request.edge_app_version_id)
    _reject_active_device_operation(session, device.id)
    deployment = Deployment(
        device_id=device.id,
        edge_app_version_id=version.id,
        status="pending",
        active=False,
    )
    task = Task(
        task_type=TaskType.DEPLOY_APP.value,
        status=TaskStatus.QUEUED.value,
        progress=0,
        resource_type="deployment",
        payload={},
    )
    device.status = "deploying"
    session.add_all([deployment, task, device])
    session.flush()
    task.resource_id = deployment.id
    deployment.task_id = task.id
    task.payload = {
        "action": "deploy",
        "deployment_id": deployment.id,
        "device_id": device.id,
        "edge_app_version_id": version.id,
    }
    session.commit()
    session.refresh(deployment)
    session.refresh(task)
    response.status_code = status.HTTP_201_CREATED

    await _enqueue_deployment_task(session, producer, deployment, task, TaskType.DEPLOY_APP)
    return deployment


@router.get("", response_model=DeploymentListResponse)
def list_deployments(
    device_id: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_deployment_session),
) -> DeploymentListResponse:
    filters = []
    if device_id is not None:
        filters.append(Deployment.device_id == device_id)
    if status_filter is not None:
        filters.append(Deployment.status == status_filter)
    total_query = select(func.count()).select_from(Deployment)
    list_query = select(Deployment).order_by(Deployment.created_at, Deployment.id)
    if filters:
        total_query = total_query.where(*filters)
        list_query = list_query.where(*filters)
    total = session.scalar(total_query) or 0
    deployments = session.scalars(list_query.limit(limit).offset(offset)).all()
    return DeploymentListResponse(items=list(deployments), total=total, limit=limit, offset=offset)


@router.get("/{deployment_id}", response_model=DeploymentResponse)
def get_deployment(deployment_id: str, session: Session = Depends(get_deployment_session)) -> Deployment:
    return _deployment_or_404(session, deployment_id)


@router.post("/{deployment_id}:stop", response_model=DeploymentResponse)
async def stop_deployment(
    deployment_id: str,
    session: Session = Depends(get_deployment_session),
    producer: Any = Depends(get_deployment_stream_producer),
) -> Deployment:
    deployment = _deployment_or_404(session, deployment_id)
    if deployment.status != "running":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Deployment cannot be stopped: {deployment.status}")
    task = _operation_task(
        TaskType.STOP_APP,
        deployment,
        {
            "action": "stop",
            "deployment_id": deployment.id,
            "device_id": deployment.device_id,
        },
    )
    deployment.status = "stopping"
    session.add_all([deployment, task])
    session.flush()
    deployment.task_id = task.id
    session.commit()
    session.refresh(deployment)
    session.refresh(task)
    await _enqueue_deployment_task(session, producer, deployment, task, TaskType.STOP_APP)
    return deployment


@router.post("/{deployment_id}:rollback", response_model=DeploymentResponse)
async def rollback_deployment(
    deployment_id: str,
    request: DeploymentRollbackRequest,
    session: Session = Depends(get_deployment_session),
    producer: Any = Depends(get_deployment_stream_producer),
) -> Deployment:
    deployment = _deployment_or_404(session, deployment_id)
    if deployment.status != "running":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Deployment cannot be rolled back: {deployment.status}")
    target_version = _ready_version_or_409(session, request.target_edge_app_version_id)
    current_version = session.get(EdgeAppVersion, deployment.edge_app_version_id)
    if current_version is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Current edge app version is missing")
    if current_version.edge_app_id != target_version.edge_app_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Rollback target must belong to the same edge app")
    task = _operation_task(
        TaskType.ROLLBACK_APP,
        deployment,
        {
            "action": "rollback",
            "deployment_id": deployment.id,
            "device_id": deployment.device_id,
            "edge_app_version_id": deployment.edge_app_version_id,
            "target_edge_app_version_id": target_version.id,
        },
    )
    deployment.status = "rolling_back"
    session.add_all([deployment, task])
    session.flush()
    deployment.task_id = task.id
    session.commit()
    session.refresh(deployment)
    session.refresh(task)
    await _enqueue_deployment_task(session, producer, deployment, task, TaskType.ROLLBACK_APP)
    return deployment


def _operation_task(task_type: TaskType, deployment: Deployment, payload: dict[str, str]) -> Task:
    return Task(
        task_type=task_type.value,
        status=TaskStatus.QUEUED.value,
        progress=0,
        resource_type="deployment",
        resource_id=deployment.id,
        payload=payload,
    )


async def _enqueue_deployment_task(
    session: Session,
    producer: Any,
    deployment: Deployment,
    task: Task,
    task_type: TaskType,
) -> None:
    command = TaskCommand(
        task_id=task.id,
        task_type=task_type,
        resource_refs={"deployment_id": deployment.id},
        payload=task.payload,
    )
    try:
        await _enqueue(producer, command)
    except Exception as exc:
        now = datetime.now(UTC)
        deployment.status = "failed"
        deployment.active = False
        task.status = TaskStatus.FAILED.value
        task.error_code = "ENQUEUE_FAILED"
        task.error_message = str(exc)
        task.retryable = True
        task.finished_at = now
        device = session.get(Device, deployment.device_id)
        if device is not None:
            device.status = "error"
            session.add(device)
        session.add_all([deployment, task])
        session.commit()
        session.refresh(deployment)


def _device_or_404(session: Session, device_id: str) -> Device:
    device = session.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return device


def _deployment_or_404(session: Session, deployment_id: str) -> Deployment:
    deployment = session.get(Deployment, deployment_id)
    if deployment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")
    return deployment


def _ready_version_or_409(session: Session, edge_app_version_id: str) -> EdgeAppVersion:
    version = session.get(EdgeAppVersion, edge_app_version_id)
    if version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Edge app version not found")
    if version.status != "ready" or version.package_uri == "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Edge app version is not ready: {version.status}")
    return version


def _reject_active_device_operation(session: Session, device_id: str) -> None:
    active = session.scalar(
        select(Deployment)
        .where(
            Deployment.device_id == device_id,
            Deployment.status.in_(ACTIVE_DEPLOYMENT_STATUSES),
        )
        .limit(1)
    )
    if active is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Device already has an active deployment operation")
