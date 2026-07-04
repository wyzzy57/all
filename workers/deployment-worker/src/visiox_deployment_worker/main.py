from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from visiox_common.tasks import TaskStatus, TaskType
from visiox_db.models import Deployment, Device, EdgeAppVersion, Task
from visiox_storage.client import ObjectStorageClient
from visiox_yolo26.converters.internal_schema import parse_storage_uri


TERMINAL_TASK_STATUSES = {TaskStatus.SUCCESS.value, TaskStatus.FAILED.value, TaskStatus.CANCELED.value}
DEPLOYMENT_TASK_TYPES = {TaskType.DEPLOY_APP.value, TaskType.STOP_APP.value, TaskType.ROLLBACK_APP.value}


class DeploymentWorkerError(RuntimeError):
    pass


class InvalidDeploymentPayloadError(DeploymentWorkerError):
    pass


class AgentClient(Protocol):
    def deploy_app(self, endpoint_url: str, payload: dict[str, Any]) -> dict[str, Any]: ...

    def start_app(self, endpoint_url: str, app_id: str) -> dict[str, Any]: ...

    def stop_app(self, endpoint_url: str, app_id: str) -> dict[str, Any]: ...

    def rollback_app(self, endpoint_url: str, app_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...


class HttpxAgentClient:
    def deploy_app(self, endpoint_url: str, payload: dict[str, Any]) -> dict[str, Any]:
        with httpx.Client(timeout=30) as client:
            response = client.post(f"{endpoint_url.rstrip('/')}/apps/deploy", json=payload)
            response.raise_for_status()
            return response.json()

    def start_app(self, endpoint_url: str, app_id: str) -> dict[str, Any]:
        with httpx.Client(timeout=30) as client:
            response = client.post(f"{endpoint_url.rstrip('/')}/apps/{app_id}/start")
            response.raise_for_status()
            return response.json()

    def stop_app(self, endpoint_url: str, app_id: str) -> dict[str, Any]:
        with httpx.Client(timeout=30) as client:
            response = client.post(f"{endpoint_url.rstrip('/')}/apps/{app_id}/stop")
            response.raise_for_status()
            return response.json()

    def rollback_app(self, endpoint_url: str, app_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        with httpx.Client(timeout=30) as client:
            response = client.post(f"{endpoint_url.rstrip('/')}/apps/{app_id}/rollback", json=payload)
            response.raise_for_status()
            return response.json()


@dataclass(frozen=True)
class DeploymentTaskResult:
    deployment_id: str
    status: str


def run_deployment_task(
    session: Session,
    storage: ObjectStorageClient,
    agent_client: AgentClient,
    task_id: str,
    deployment_id: str,
    work_dir: Path,
    stale_after_seconds: int = 3600,
) -> DeploymentTaskResult:
    task = _require_task(session, task_id, deployment_id)
    deployment = _require_deployment(session, deployment_id)
    if task.status == TaskStatus.SUCCESS.value:
        return DeploymentTaskResult(deployment_id=deployment.id, status=deployment.status)
    if task.status in TERMINAL_TASK_STATUSES:
        raise DeploymentWorkerError(f"task is terminal: {task.status}")

    now = _utc_now()
    claim_statuses = [TaskStatus.QUEUED.value]
    if _is_stale_running_task(task, now, stale_after_seconds):
        claim_statuses.append(TaskStatus.RUNNING.value)
    claimed = session.execute(
        update(Task)
        .where(Task.id == task.id, Task.status.in_(claim_statuses))
        .values(status=TaskStatus.RUNNING.value, stage="deploy", started_at=task.started_at or now)
    ).rowcount
    if claimed != 1:
        session.rollback()
        task = _require_task(session, task_id, deployment_id)
        deployment = _require_deployment(session, deployment_id)
        if task.status == TaskStatus.SUCCESS.value:
            return DeploymentTaskResult(deployment_id=deployment.id, status=deployment.status)
        raise DeploymentWorkerError(f"task is already claimed: {task.status}")
    session.commit()

    work_dir.mkdir(parents=True, exist_ok=True)
    stored_objects: list[tuple[str, str]] = []
    try:
        task = _require_task(session, task_id, deployment_id)
        deployment = _require_deployment(session, deployment_id)
        device = session.get(Device, deployment.device_id)
        version = session.get(EdgeAppVersion, deployment.edge_app_version_id)
        if device is None:
            raise DeploymentWorkerError("device not found")
        if version is None:
            raise DeploymentWorkerError("edge app version not found")
        payload = task.payload or {}
        action = _validate_payload(payload, task, deployment)
        log_lines = [f"action={action}", f"deployment_id={deployment.id}", f"device_id={device.id}"]
        if action == "deploy":
            _run_deploy(session, storage, agent_client, task, deployment, device, version, work_dir, log_lines)
        elif action == "stop":
            _run_stop(agent_client, deployment, device, version, log_lines)
        elif action == "rollback":
            target_version_id = str(payload["target_edge_app_version_id"])
            target_version = session.get(EdgeAppVersion, target_version_id)
            if target_version is None or target_version.status != "ready":
                raise InvalidDeploymentPayloadError("target edge app version is not ready")
            if target_version.edge_app_id != version.edge_app_id:
                raise InvalidDeploymentPayloadError("target edge app version does not match deployment edge app")
            _run_rollback(agent_client, deployment, device, target_version, log_lines)
        else:
            raise InvalidDeploymentPayloadError(f"unsupported deployment action: {action}")

        log_uri = _persist_log(storage, deployment.id, "\n".join(log_lines) + "\n", work_dir, stored_objects)
        task.status = TaskStatus.SUCCESS.value
        task.progress = 100
        task.stage = action
        task.error_code = None
        task.error_message = None
        task.retryable = False
        task.finished_at = _utc_now()
        deployment.logs_uri = log_uri
        device.status = "online"
        session.add_all([task, deployment, device])
        session.commit()
        return DeploymentTaskResult(deployment_id=deployment.id, status=deployment.status)
    except Exception as exc:
        session.rollback()
        _cleanup_stored_objects(storage, stored_objects)
        task = session.get(Task, task_id)
        deployment = session.get(Deployment, deployment_id)
        device = session.get(Device, deployment.device_id) if deployment is not None else None
        if task is not None:
            task.status = TaskStatus.FAILED.value
            task.error_code = "INVALID_DEPLOYMENT_PAYLOAD" if isinstance(exc, InvalidDeploymentPayloadError) else "DEPLOYMENT_FAILED"
            task.error_message = str(exc)
            task.retryable = True
            task.finished_at = _utc_now()
            session.add(task)
        if deployment is not None:
            deployment.status = "failed"
            deployment.active = False
            session.add(deployment)
        if device is not None:
            device.status = "error"
            session.add(device)
        session.commit()
        raise


def _run_deploy(
    session: Session,
    storage: ObjectStorageClient,
    agent_client: AgentClient,
    task: Task,
    deployment: Deployment,
    device: Device,
    version: EdgeAppVersion,
    work_dir: Path,
    log_lines: list[str],
) -> None:
    if version.status != "ready" or version.package_uri == "pending":
        raise DeploymentWorkerError(f"edge app version is not ready: {version.status}")
    task.stage = "download_package"
    deployment.status = "deploying"
    device.status = "deploying"
    session.add_all([task, deployment, device])
    session.commit()
    _download_package(storage, version, work_dir / "package.tar.gz")
    app_id = _edge_app_runtime_id(version)
    deploy_payload = {
        "app_id": app_id,
        "version": version.version,
        "package_uri": version.package_uri,
        "manifest": version.manifest,
    }
    log_lines.append(f"package_uri={version.package_uri}")
    task.stage = "agent_deploy"
    session.add(task)
    session.commit()
    deploy_result = agent_client.deploy_app(device.endpoint_url, deploy_payload)
    _validate_agent_response(deploy_result, expected_status="deployed", expected_app_id=app_id, expected_version=version.version)
    log_lines.append(f"agent_deploy={deploy_result}")
    start_result = agent_client.start_app(device.endpoint_url, app_id)
    _validate_agent_response(start_result, expected_status="running", expected_app_id=app_id)
    log_lines.append(f"agent_start={start_result}")
    _deactivate_other_deployments(session, deployment)
    deployment.status = "running"
    deployment.active = True
    deployment.deployed_at = _utc_now()


def _run_stop(
    agent_client: AgentClient,
    deployment: Deployment,
    device: Device,
    version: EdgeAppVersion,
    log_lines: list[str],
) -> None:
    app_id = _edge_app_runtime_id(version)
    stop_result = agent_client.stop_app(device.endpoint_url, app_id)
    _validate_agent_response(stop_result, expected_status="stopped", expected_app_id=app_id)
    log_lines.append(f"agent_stop={stop_result}")
    deployment.status = "stopped"
    deployment.active = False
    deployment.stopped_at = _utc_now()


def _run_rollback(
    agent_client: AgentClient,
    deployment: Deployment,
    device: Device,
    target_version: EdgeAppVersion,
    log_lines: list[str],
) -> None:
    app_id = _edge_app_runtime_id(target_version)
    rollback_payload = {
        "version": target_version.version,
        "package_uri": target_version.package_uri,
        "manifest": target_version.manifest,
    }
    rollback_result = agent_client.rollback_app(device.endpoint_url, app_id, rollback_payload)
    _validate_agent_response(rollback_result, expected_status="rolled_back", expected_app_id=app_id, expected_version=target_version.version)
    log_lines.append(f"agent_rollback={rollback_result}")
    deployment.edge_app_version_id = target_version.id
    deployment.status = "rolled_back"
    deployment.active = True
    deployment.deployed_at = _utc_now()


def _require_task(session: Session, task_id: str, deployment_id: str) -> Task:
    task = session.get(Task, task_id)
    if task is None:
        raise DeploymentWorkerError("task not found")
    if task.task_type not in DEPLOYMENT_TASK_TYPES:
        raise DeploymentWorkerError(f"unexpected task type: {task.task_type}")
    if task.resource_type != "deployment" or task.resource_id != deployment_id:
        raise DeploymentWorkerError("task does not match deployment")
    return task


def _require_deployment(session: Session, deployment_id: str) -> Deployment:
    deployment = session.get(Deployment, deployment_id)
    if deployment is None:
        raise DeploymentWorkerError("deployment not found")
    return deployment


def _validate_payload(payload: dict[str, Any], task: Task, deployment: Deployment) -> str:
    action = payload.get("action")
    expected_action = {
        TaskType.DEPLOY_APP.value: "deploy",
        TaskType.STOP_APP.value: "stop",
        TaskType.ROLLBACK_APP.value: "rollback",
    }[task.task_type]
    if action != expected_action:
        raise InvalidDeploymentPayloadError("task payload action does not match task type")
    if payload.get("deployment_id") != deployment.id:
        raise InvalidDeploymentPayloadError("task payload deployment_id does not match deployment")
    if payload.get("device_id") != deployment.device_id:
        raise InvalidDeploymentPayloadError("task payload device_id does not match deployment")
    if action == "deploy" and payload.get("edge_app_version_id") != deployment.edge_app_version_id:
        raise InvalidDeploymentPayloadError("task payload edge_app_version_id does not match deployment")
    if action == "rollback" and payload.get("edge_app_version_id") not in {None, deployment.edge_app_version_id}:
        raise InvalidDeploymentPayloadError("task payload edge_app_version_id does not match deployment")
    if action == "rollback" and not isinstance(payload.get("target_edge_app_version_id"), str):
        raise InvalidDeploymentPayloadError("task payload target_edge_app_version_id is required")
    return str(action)


def _download_package(storage: ObjectStorageClient, version: EdgeAppVersion, destination: Path) -> Path:
    bucket, object_name = parse_storage_uri(str(version.package_uri))
    return storage.get_file(bucket, object_name, destination)


def _persist_log(
    storage: ObjectStorageClient,
    deployment_id: str,
    content: str,
    work_dir: Path,
    stored_objects: list[tuple[str, str]],
) -> str:
    path = work_dir / "deployment.log"
    path.write_text(content, encoding="utf-8")
    object_name = f"deployments/{deployment_id}/deployment.log"
    stored_objects.append(("deployment-logs", object_name))
    return storage.put_file("deployment-logs", object_name, path, content_type="text/plain")


def _cleanup_stored_objects(storage: ObjectStorageClient, stored_objects: list[tuple[str, str]]) -> None:
    for bucket, object_name in reversed(stored_objects):
        try:
            storage.delete_file(bucket, object_name)
        except Exception:
            pass


def _deactivate_other_deployments(session: Session, deployment: Deployment) -> None:
    others = session.scalars(
        select(Deployment).where(
            Deployment.device_id == deployment.device_id,
            Deployment.id != deployment.id,
            Deployment.active.is_(True),
        )
    ).all()
    for other in others:
        other.active = False
        session.add(other)


def _edge_app_runtime_id(version: EdgeAppVersion) -> str:
    return str((version.manifest or {}).get("edge_app_id") or version.edge_app_id)


def _validate_agent_response(
    payload: dict[str, Any],
    *,
    expected_status: str,
    expected_app_id: str,
    expected_version: str | None = None,
) -> None:
    if payload.get("success") is not True:
        raise DeploymentWorkerError(f"agent returned unsuccessful response: {payload}")
    if payload.get("status") != expected_status:
        raise DeploymentWorkerError(f"agent returned unexpected status: {payload.get('status')}")
    app_payload = payload.get("app")
    if isinstance(app_payload, dict):
        if app_payload.get("app_id") != expected_app_id:
            raise DeploymentWorkerError("agent returned unexpected app_id")
        if expected_version is not None and app_payload.get("version") != expected_version:
            raise DeploymentWorkerError("agent returned unexpected version")


def _is_stale_running_task(task: Task, now: datetime, stale_after_seconds: int) -> bool:
    if task.status != TaskStatus.RUNNING.value or task.started_at is None:
        return False
    if stale_after_seconds <= 0:
        return True
    started_at = task.started_at
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    return started_at <= now - timedelta(seconds=stale_after_seconds)


def _utc_now() -> datetime:
    return datetime.now(UTC)
