from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from collections.abc import Callable
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


class TrainingCanceledError(TrainingWorkerError):
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
    weight_paths: dict[str, Path] | None = None
    visualization_paths: dict[str, Path] | None = None


class TrainingCommandRunner(Protocol):
    def run(
        self,
        argv: list[str],
        work_dir: Path,
        should_cancel: Callable[[], bool] | None = None,
    ) -> CommandResult: ...


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
    pipeline = session.get(TrainingPipeline, job.pipeline_id)
    job.status = "running"
    job.started_at = job.started_at or now
    if pipeline is not None:
        pipeline.status = "running"
        session.add(pipeline)
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

        base_model_path = _stage_base_model(storage, base_model, work_dir / "base-model" / "base.pt")
        dataset_dir = work_dir / "dataset"
        task.stage = "export_dataset"
        session.add(task)
        session.commit()
        export_yolo26_dataset(session, storage, str(pipeline.dataset_id), dataset_dir)
        _prepare_training_dataset_dir(dataset_dir)

        task.stage = "train"
        session.add(task)
        session.commit()
        params = dict(job.params or {})
        payload = task.payload or {}
        environment = payload.get("environment")
        if isinstance(environment, dict):
            params.update(environment)
        params = _normalize_runtime_params(params)
        command = build_train_command(
            base_model_path=base_model_path,
            data_yaml_path=dataset_dir / "data.yaml",
            params=params,
            project_dir=work_dir / "runs",
            run_name=f"job-{job.id}",
        )
        run_name = f"job-{job.id}"
        job.metrics = {
            **(job.metrics or {}),
            "observability": {
                "mlflow_run_name": run_name,
                "tensorboard_run_name": run_name,
            },
        }
        session.add(job)
        session.commit()
        instrumented_command = [sys.executable, "-m", "visiox_training_worker.train_entrypoint", *command.argv[2:]]
        result = runner.run(instrumented_command, work_dir, should_cancel=lambda: _task_is_canceled(session, task_id))
        if _task_is_canceled(session, task_id):
            raise TrainingCanceledError("training canceled")
        if result.exit_code != 0:
            raise TrainingWorkerError(f"training command failed with exit code {result.exit_code}: {result.stderr}")
        if result.artifact_path is None:
            raise TrainingWorkerError("training artifact path is missing")

        task.stage = "persist_artifacts"
        session.add(task)
        session.commit()
        metrics = {**(job.metrics or {}), **(result.metrics or {})}
        weight_paths = _training_weight_paths(result)
        weight_uris: dict[str, str] = {}
        trained_model: TrainedModel | None = None
        for weight_name, weight_path in weight_paths.items():
            model = TrainedModel(
                pipeline_id=pipeline.id,
                training_job_id=job.id if trained_model is None else None,
                name=weight_name,
                version=weight_name,
                task=pipeline.task,
                artifact_uri="pending",
                metrics=metrics,
                status="ready",
            )
            session.add(model)
            session.flush()
            artifact_object = f"trained/{model.id}/{weight_name}"
            artifact_uri = storage.put_file("models", artifact_object, weight_path)
            stored_objects.append(("models", artifact_object))
            model.artifact_uri = artifact_uri
            weight_uris[weight_name] = artifact_uri
            session.add(model)
            if trained_model is None:
                trained_model = model
        log_path = work_dir / "training.log"
        log_path.write_text(_format_log(result), encoding="utf-8")
        log_object = f"jobs/{job.id}/training.log"
        log_uri = storage.put_file("training", log_object, log_path, content_type="text/plain")
        stored_objects.append(("training", log_object))
        visualization_uris = _store_visualizations(storage, job.id, result.visualization_paths or {}, stored_objects)
        if weight_uris:
            metrics = {**metrics, "weights": weight_uris}
        if visualization_uris:
            metrics = {**metrics, "visualizations": visualization_uris}

        if trained_model is None:
            raise TrainingWorkerError("training artifact path is missing")
        job.trained_model_id = trained_model.id
        job.status = "success"
        job.metrics = metrics
        job.log_uri = log_uri
        job.finished_at = _utc_now()
        pipeline.status = "success"
        task.status = TaskStatus.SUCCESS.value
        task.progress = 100
        task.stage = "completed"
        task.finished_at = job.finished_at
        task.error_code = None
        task.error_message = None
        task.retryable = False
        session.add_all([job, pipeline, task])
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
        canceled = isinstance(exc, TrainingCanceledError) or (task is not None and task.status == TaskStatus.CANCELED.value)
        if task is not None:
            task.status = TaskStatus.CANCELED.value if canceled else TaskStatus.FAILED.value
            task.error_code = "INVALID_TASK_PAYLOAD" if isinstance(exc, InvalidTaskPayloadError) else "TRAINING_FAILED"
            if canceled:
                task.error_code = "TRAINING_CANCELED"
            task.error_message = str(exc)
            task.retryable = not canceled
            task.finished_at = _utc_now()
            task.stage = task.stage or "train"
            session.add(task)
        if job is not None:
            job.status = "canceled" if canceled else "failed"
            job.finished_at = _utc_now()
            session.add(job)
            pipeline = session.get(TrainingPipeline, job.pipeline_id)
            if pipeline is not None:
                pipeline.status = "canceled" if canceled else "failed"
                session.add(pipeline)
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


def _stage_base_model(storage: ObjectStorageClient, base_model: BaseModel, destination: Path) -> Path:
    bucket, object_name = parse_storage_uri(str(base_model.local_uri))
    staged_path = storage.get_file(bucket, object_name, destination)
    if _needs_real_base_model(staged_path):
        downloaded_path = _download_base_model_asset(str(base_model.filename), destination.parent)
        shutil.copyfile(downloaded_path, staged_path)
        storage.put_file(bucket, object_name, staged_path, content_type="application/octet-stream")
    return staged_path


def _needs_real_base_model(path: Path) -> bool:
    header = path.read_bytes()[:256].lower()
    return header.startswith(b"visiox prepared") or header.startswith(b"version https://git-lfs")


def _download_base_model_asset(filename: str, target_dir: Path) -> Path:
    from ultralytics.utils.downloads import attempt_download_asset

    target_dir.mkdir(parents=True, exist_ok=True)
    safe_filename = Path(filename).name
    previous_cwd = Path.cwd()
    try:
        os.chdir(target_dir)
        return Path(attempt_download_asset(safe_filename)).resolve()
    finally:
        os.chdir(previous_cwd)


def _normalize_runtime_params(params: dict[str, object]) -> dict[str, object]:
    normalized = dict(params)
    device = normalized.get("device")
    if _is_cuda_device(device) and not _cuda_is_available():
        normalized["device"] = "cpu"
    return normalized


def _is_cuda_device(device: object) -> bool:
    if isinstance(device, int):
        return device >= 0
    if isinstance(device, str):
        text = device.strip().lower()
        return text.isdigit() or text.startswith("cuda")
    return False


def _cuda_is_available() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _task_is_canceled(session: Session, task_id: str) -> bool:
    task = session.get(Task, task_id, populate_existing=True)
    return task is not None and task.status == TaskStatus.CANCELED.value


def _prepare_training_dataset_dir(dataset_dir: Path) -> None:
    for relative_dir in (
        "images/train",
        "images/val",
        "images/test",
        "labels/train",
        "labels/val",
        "labels/test",
    ):
        (dataset_dir / relative_dir).mkdir(parents=True, exist_ok=True)
    _rewrite_data_yaml_path(dataset_dir / "data.yaml", dataset_dir)


def _rewrite_data_yaml_path(data_yaml_path: Path, dataset_dir: Path) -> None:
    lines = data_yaml_path.read_text(encoding="utf-8").splitlines()
    absolute_path_line = f"path: {dataset_dir.resolve().as_posix()}"
    for index, line in enumerate(lines):
        if line.strip().startswith("path:"):
            lines[index] = absolute_path_line
            break
    else:
        lines.insert(0, absolute_path_line)
    data_yaml_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _trained_model_for_job(session: Session, training_job_id: str) -> TrainedModel | None:
    return session.query(TrainedModel).filter(TrainedModel.training_job_id == training_job_id).one_or_none()


def _training_weight_paths(result: CommandResult) -> dict[str, Path]:
    if result.weight_paths:
        weights = {
            _safe_weight_name(name): path
            for name, path in result.weight_paths.items()
            if path.exists() and path.is_file()
        }
        if weights:
            return dict(sorted(weights.items(), key=lambda item: 0 if item[0] == "best.pt" else 1))
    if result.artifact_path is not None and result.artifact_path.exists():
        return {_safe_weight_name(result.artifact_path.name): result.artifact_path}
    return {}


def _safe_weight_name(name: str) -> str:
    filename = Path(name).name or "best.pt"
    if not filename.endswith(".pt"):
        filename = f"{filename}.pt"
    return filename


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


def _store_visualizations(
    storage: ObjectStorageClient,
    training_job_id: str,
    visualization_paths: dict[str, Path],
    stored_objects: list[tuple[str, str]],
) -> dict[str, str]:
    uris: dict[str, str] = {}
    for name, path in visualization_paths.items():
        if not path.exists() or not path.is_file():
            continue
        safe_name = _safe_artifact_name(name)
        object_name = f"jobs/{training_job_id}/visualizations/{safe_name}"
        content_type = "image/png" if path.suffix.lower() == ".png" else None
        uris[safe_name] = storage.put_file("training", object_name, path, content_type=content_type)
        stored_objects.append(("training", object_name))
    return uris


def _safe_artifact_name(name: str) -> str:
    cleaned = "".join(character if character.isalnum() or character in {".", "-", "_"} else "_" for character in name)
    return cleaned or "artifact"


def _format_log(result: CommandResult) -> str:
    return f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}\n"


def _utc_now() -> datetime:
    return datetime.now(UTC)
