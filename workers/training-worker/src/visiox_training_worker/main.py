from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from visiox_common.tasks import TaskStatus, TaskType
from visiox_db.models import BaseModel, Dataset, Task, TrainedModel, TrainingJob, TrainingPipeline
from visiox_storage.client import ObjectStorageClient
from visiox_yolo26.converters import export_yolo26_dataset
from visiox_yolo26.converters.internal_schema import parse_storage_uri
from visiox_yolo26.training.commands import build_train_command
from visiox_yolo26.training.prechecks import validate_training_resources


TERMINAL_TASK_STATUSES = {TaskStatus.SUCCESS.value, TaskStatus.FAILED.value, TaskStatus.CANCELED.value}


class TrainingWorkerError(RuntimeError):
    pass


class InvalidTaskPayloadError(TrainingWorkerError):
    pass


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    metrics: dict[str, object] | None = None
    artifact_path: Path | None = None


class TrainingCommandRunner(Protocol):
    def run(self, argv: list[str], work_dir: Path) -> CommandResult: ...


@dataclass(frozen=True)
class TrainingResult:
    training_job_id: str
    trained_model_id: str | None
    status: str


def run_training_job(
    session: Session,
    storage: ObjectStorageClient,
    runner: TrainingCommandRunner,
    task_id: str,
    training_job_id: str,
    work_dir: Path,
) -> TrainingResult:
    task = _require_task(session, task_id, training_job_id)
    job = _require_job(session, training_job_id, task_id)
    if job.status == "success" and job.trained_model_id is not None:
        return TrainingResult(training_job_id=job.id, trained_model_id=job.trained_model_id, status=job.status)
    if task.status in TERMINAL_TASK_STATUSES and task.status != TaskStatus.SUCCESS.value:
        raise TrainingWorkerError(f"task is terminal: {task.status}")

    work_dir.mkdir(parents=True, exist_ok=True)
    now = _utc_now()
    claimed = session.execute(
        update(Task)
        .where(Task.id == task.id, Task.status == TaskStatus.QUEUED.value)
        .values(status=TaskStatus.RUNNING.value, stage="prepare", started_at=task.started_at or now)
    ).rowcount
    if claimed != 1:
        session.rollback()
        task = _require_task(session, task_id, training_job_id)
        job = _require_job(session, training_job_id, task_id)
        if job.status == "success" and job.trained_model_id is not None:
            return TrainingResult(training_job_id=job.id, trained_model_id=job.trained_model_id, status=job.status)
        raise TrainingWorkerError(f"task is already claimed: {task.status}")
    job.status = "running"
    job.started_at = job.started_at or now
    session.add(job)
    session.commit()
    task = _require_task(session, task_id, training_job_id)
    job = _require_job(session, training_job_id, task_id)
    stored_objects: list[tuple[str, str]] = []

    try:
        pipeline = session.get(TrainingPipeline, job.pipeline_id)
        if pipeline is None:
            raise TrainingWorkerError("training pipeline not found")
        base_model = session.get(BaseModel, pipeline.base_model_id)
        dataset = session.get(Dataset, pipeline.dataset_id)
        if base_model is None or dataset is None:
            raise TrainingWorkerError("pipeline resources are missing")
        validate_training_resources(
            session,
            task=pipeline.task,
            scale=pipeline.scale,
            base_model_id=base_model.id,
            dataset_id=dataset.id,
        )
        _validate_task_payload(task, job, pipeline, base_model, dataset)

        base_model_path = _download_base_model(storage, base_model, work_dir / "base-model" / "base.pt")
        dataset_dir = work_dir / "dataset"
        task.stage = "export_dataset"
        session.add(task)
        session.commit()
        export_yolo26_dataset(session, storage, str(pipeline.dataset_id), dataset_dir)

        task.stage = "train"
        session.add(task)
        session.commit()
        params = dict(job.params or {})
        payload = task.payload or {}
        environment = payload.get("environment")
        if isinstance(environment, dict):
            params.update(environment)
        command = build_train_command(
            base_model_path=base_model_path,
            data_yaml_path=dataset_dir / "data.yaml",
            params=params,
            project_dir=work_dir / "runs",
            run_name=f"job-{job.id}",
        )
        result = runner.run(command.argv, work_dir)
        if result.exit_code != 0:
            raise TrainingWorkerError(f"training command failed with exit code {result.exit_code}: {result.stderr}")
        if result.artifact_path is None:
            raise TrainingWorkerError("training artifact path is missing")

        task.stage = "persist_artifacts"
        session.add(task)
        session.commit()
        metrics = result.metrics or {}
        trained_model = TrainedModel(
            pipeline_id=pipeline.id,
            training_job_id=job.id,
            name=f"{pipeline.name}-{job.id[:8]}",
            version=job.id[:12],
            task=pipeline.task,
            artifact_uri="pending",
            metrics=metrics,
            status="ready",
        )
        session.add(trained_model)
        session.flush()
        artifact_object = f"trained/{trained_model.id}/best.pt"
        artifact_uri = storage.put_file("models", artifact_object, result.artifact_path)
        stored_objects.append(("models", artifact_object))
        log_path = work_dir / "training.log"
        log_path.write_text(_format_log(result), encoding="utf-8")
        log_object = f"jobs/{job.id}/training.log"
        log_uri = storage.put_file("training", log_object, log_path, content_type="text/plain")
        stored_objects.append(("training", log_object))

        trained_model.artifact_uri = artifact_uri
        job.trained_model_id = trained_model.id
        job.status = "success"
        job.metrics = metrics
        job.log_uri = log_uri
        job.finished_at = _utc_now()
        task.status = TaskStatus.SUCCESS.value
        task.progress = 100
        task.stage = "completed"
        task.finished_at = job.finished_at
        task.error_code = None
        task.error_message = None
        task.retryable = False
        session.add_all([trained_model, job, task])
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            existing_model = _trained_model_for_job(session, job.id)
            if existing_model is not None:
                return TrainingResult(training_job_id=job.id, trained_model_id=existing_model.id, status="success")
            raise
        return TrainingResult(training_job_id=job.id, trained_model_id=trained_model.id, status=job.status)
    except Exception as exc:
        session.rollback()
        _cleanup_stored_objects(storage, stored_objects)
        task = session.get(Task, task_id)
        job = session.get(TrainingJob, training_job_id)
        if task is not None:
            task.status = TaskStatus.FAILED.value
            task.error_code = "INVALID_TASK_PAYLOAD" if isinstance(exc, InvalidTaskPayloadError) else "TRAINING_FAILED"
            task.error_message = str(exc)
            task.retryable = True
            task.finished_at = _utc_now()
            task.stage = task.stage or "train"
            session.add(task)
        if job is not None:
            job.status = "failed"
            job.finished_at = _utc_now()
            session.add(job)
        session.commit()
        raise


def _require_task(session: Session, task_id: str, training_job_id: str) -> Task:
    task = session.get(Task, task_id)
    if task is None:
        raise TrainingWorkerError("task not found")
    if task.task_type != TaskType.TRAIN_MODEL.value:
        raise TrainingWorkerError(f"unexpected task type: {task.task_type}")
    if task.resource_type != "training_job" or task.resource_id != training_job_id:
        raise TrainingWorkerError("task does not match training job")
    payload = task.payload or {}
    if payload.get("training_job_id") not in {None, training_job_id}:
        raise TrainingWorkerError("task payload does not match training job")
    return task


def _require_job(session: Session, training_job_id: str, task_id: str) -> TrainingJob:
    job = session.get(TrainingJob, training_job_id)
    if job is None:
        raise TrainingWorkerError("training job not found")
    if job.task_id != task_id:
        raise TrainingWorkerError("training job does not match task")
    return job


def _download_base_model(storage: ObjectStorageClient, base_model: BaseModel, destination: Path) -> Path:
    bucket, object_name = parse_storage_uri(str(base_model.local_uri))
    return storage.get_file(bucket, object_name, destination)


def _trained_model_for_job(session: Session, training_job_id: str) -> TrainedModel | None:
    return session.query(TrainedModel).filter(TrainedModel.training_job_id == training_job_id).one_or_none()


def _validate_task_payload(
    task: Task,
    job: TrainingJob,
    pipeline: TrainingPipeline,
    base_model: BaseModel,
    dataset: Dataset,
) -> None:
    payload = task.payload or {}
    expected = {
        "pipeline_id": pipeline.id,
        "training_job_id": job.id,
        "dataset_id": dataset.id,
        "base_model_id": base_model.id,
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            raise InvalidTaskPayloadError(f"task payload {key} does not match training job")
    if payload.get("params") != (job.params or {}):
        raise InvalidTaskPayloadError("task payload params do not match training job")
    if "environment" in payload and not isinstance(payload["environment"], dict):
        raise InvalidTaskPayloadError("task payload environment must be an object")


def _cleanup_stored_objects(storage: ObjectStorageClient, stored_objects: list[tuple[str, str]]) -> None:
    for bucket, object_name in reversed(stored_objects):
        try:
            storage.delete_file(bucket, object_name)
        except Exception:
            pass


def _format_log(result: CommandResult) -> str:
    return f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}\n"


def _utc_now() -> datetime:
    return datetime.now(UTC)
