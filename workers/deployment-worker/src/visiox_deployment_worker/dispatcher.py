from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from visiox_common.tasks import TaskType
from visiox_deployment_worker.main import AgentClient, DeploymentTaskResult, run_deployment_task
from visiox_storage.client import ObjectStorageClient


class DeploymentWorkerDispatchError(RuntimeError):
    pass


@dataclass(frozen=True)
class DeploymentWorkerClients:
    agent: AgentClient


def dispatch_deployment_worker_task(
    session: Session,
    storage: ObjectStorageClient,
    clients: DeploymentWorkerClients,
    *,
    task_id: str,
    task_type: str,
    resource_refs: dict[str, str],
    work_dir: Path,
) -> DeploymentTaskResult:
    if task_type not in {TaskType.DEPLOY_APP.value, TaskType.STOP_APP.value, TaskType.ROLLBACK_APP.value}:
        raise DeploymentWorkerDispatchError(f"unsupported deployment worker task type: {task_type}")
    deployment_id = resource_refs.get("deployment_id")
    if deployment_id is None:
        raise DeploymentWorkerDispatchError("deployment_id resource ref is required")
    return run_deployment_task(session, storage, clients.agent, task_id, deployment_id, work_dir / task_id)
