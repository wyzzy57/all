from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from visiox_common.tasks import TaskType
from visiox_storage.client import ObjectStorageClient
from visiox_training_worker.export_flow import EdgeAppPackagingResult, ExportCommandRunner, run_edge_app_packaging
from visiox_training_worker.main import TrainingCommandRunner, TrainingResult, run_training_job


class TrainingWorkerDispatchError(RuntimeError):
    pass


@dataclass(frozen=True)
class TrainingWorkerRunners:
    training: TrainingCommandRunner
    export: ExportCommandRunner


def dispatch_training_worker_task(
    session: Session,
    storage: ObjectStorageClient,
    runners: TrainingWorkerRunners,
    *,
    task_id: str,
    task_type: str,
    resource_refs: dict[str, str],
    work_dir: Path,
) -> TrainingResult | EdgeAppPackagingResult:
    if task_type == TaskType.TRAIN_MODEL.value:
        training_job_id = resource_refs.get("training_job_id")
        if training_job_id is None:
            raise TrainingWorkerDispatchError("training_job_id resource ref is required")
        return run_training_job(session, storage, runners.training, task_id, training_job_id, work_dir / task_id)

    if task_type == TaskType.BUILD_EDGE_APP_PACKAGE.value:
        edge_app_version_id = resource_refs.get("edge_app_version_id")
        if edge_app_version_id is None:
            raise TrainingWorkerDispatchError("edge_app_version_id resource ref is required")
        return run_edge_app_packaging(session, storage, runners.export, task_id, edge_app_version_id, work_dir / task_id)

    raise TrainingWorkerDispatchError(f"unsupported training worker task type: {task_type}")
