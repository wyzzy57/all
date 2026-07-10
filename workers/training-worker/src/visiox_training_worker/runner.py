from __future__ import annotations

import argparse
import csv
import logging
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from visiox_common.settings import get_settings
from visiox_common.tasks import TaskStatus, TaskType
from visiox_db.models import Task
from visiox_db.session import create_session_factory
from visiox_storage.client import MinioObjectStorageClient, ObjectStorageClient
from visiox_training_worker.main import CommandResult, run_training_job


LOGGER = logging.getLogger(__name__)

ULTRALYTICS_VISUALIZATION_FILES = (
    "results.png",
    "confusion_matrix.png",
    "confusion_matrix_normalized.png",
    "PR_curve.png",
    "P_curve.png",
    "R_curve.png",
    "F1_curve.png",
    "labels.jpg",
    "train_batch0.jpg",
    "val_batch0_pred.jpg",
)


class SubprocessTrainingRunner:
    def run(
        self,
        argv: list[str],
        work_dir: Path,
        should_cancel: Callable[[], bool] | None = None,
    ) -> CommandResult:
        process = subprocess.Popen(
            argv,
            cwd=work_dir,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        while True:
            try:
                stdout, stderr = process.communicate(timeout=1)
                break
            except subprocess.TimeoutExpired:
                if should_cancel is None or not should_cancel():
                    continue
                process.terminate()
                try:
                    stdout, stderr = process.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    stdout, stderr = process.communicate()
                break
        run_dir = _run_dir_from_argv(argv, work_dir)
        artifact = _best_weight_path(run_dir)
        return CommandResult(
            exit_code=process.returncode or 0,
            stdout=stdout,
            stderr=stderr,
            metrics=_read_ultralytics_metrics(run_dir),
            artifact_path=artifact,
            weight_paths=_weight_paths(run_dir),
            visualization_paths=_visualization_paths(run_dir),
        )


def run_pending_training_tasks(
    session: Session,
    storage: ObjectStorageClient,
    runner: SubprocessTrainingRunner,
    work_dir: Path,
    limit: int = 1,
) -> list[Task]:
    tasks = session.scalars(
        select(Task)
        .where(Task.task_type == TaskType.TRAIN_MODEL.value, Task.status == TaskStatus.QUEUED.value)
        .order_by(Task.created_at, Task.id)
        .limit(limit)
    ).all()
    processed: list[Task] = []
    for task in tasks:
        training_job_id = task.payload.get("training_job_id") or task.resource_id
        if not training_job_id:
            task.status = TaskStatus.FAILED.value
            task.error_code = "MISSING_TRAINING_JOB_ID"
            task.error_message = "Task payload must include training_job_id"
            session.add(task)
            session.commit()
            session.refresh(task)
            processed.append(task)
            continue
        try:
            run_training_job(session, storage, runner, task.id, str(training_job_id), work_dir)
        except Exception:
            LOGGER.exception("training task failed: %s", task.id)
        finally:
            refreshed = session.get(Task, task.id)
            if refreshed is not None:
                processed.append(refreshed)
    return processed


def poll_training_tasks(
    session_factory: Callable[[], Session],
    storage: ObjectStorageClient,
    runner: SubprocessTrainingRunner,
    work_dir: Path,
    interval_seconds: float = 2.0,
) -> None:
    while True:
        with session_factory() as session:
            processed = run_pending_training_tasks(session, storage, runner, work_dir)
        if not processed:
            time.sleep(interval_seconds)


def _run_dir_from_argv(argv: list[str], work_dir: Path) -> Path:
    project = _arg_value(argv, "project")
    name = _arg_value(argv, "name")
    if project and name:
        return Path(project) / name
    return work_dir / "runs" / "train"


def _arg_value(argv: list[str], key: str) -> str | None:
    prefix = f"{key}="
    for item in argv:
        if item.startswith(prefix):
            return item[len(prefix) :]
    return None


def _best_weight_path(run_dir: Path) -> Path | None:
    best = run_dir / "weights" / "best.pt"
    if best.exists():
        return best
    last = run_dir / "weights" / "last.pt"
    if last.exists():
        return last
    return None


def _weight_paths(run_dir: Path) -> dict[str, Path]:
    weights_dir = run_dir / "weights"
    return {
        name: weights_dir / name
        for name in ("best.pt", "last.pt")
        if (weights_dir / name).exists()
    }


def _read_ultralytics_metrics(run_dir: Path) -> dict[str, object]:
    results_csv = run_dir / "results.csv"
    if not results_csv.exists():
        return {}
    with results_csv.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        return {}
    latest = rows[-1]
    metrics: dict[str, object] = {}
    for raw_key, raw_value in latest.items():
        key = raw_key.strip()
        value = raw_value.strip() if isinstance(raw_value, str) else raw_value
        if value in {None, ""}:
            continue
        metrics[key] = _number_or_text(value)
    return metrics


def _number_or_text(value: object) -> object:
    if not isinstance(value, str):
        return value
    try:
        number = float(value)
    except ValueError:
        return value
    return int(number) if number.is_integer() else number


def _visualization_paths(run_dir: Path) -> dict[str, Path]:
    return {name: run_dir / name for name in ULTRALYTICS_VISUALIZATION_FILES if (run_dir / name).exists()}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Ultralytics training tasks.")
    parser.add_argument("--once", action="store_true", help="Process queued tasks once and exit.")
    parser.add_argument("--interval-seconds", type=float, default=2.0)
    parser.add_argument("--work-dir", type=Path, default=Path("/tmp/visiox-training"))
    args = parser.parse_args()

    settings = get_settings()
    session_factory = create_session_factory()
    storage = MinioObjectStorageClient(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )
    runner = SubprocessTrainingRunner()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    if args.once:
        with session_factory() as session:
            run_pending_training_tasks(session, storage, runner, args.work_dir)
        return
    poll_training_tasks(
        session_factory=session_factory,
        storage=storage,
        runner=runner,
        work_dir=args.work_dir,
        interval_seconds=args.interval_seconds,
    )


if __name__ == "__main__":
    main()
