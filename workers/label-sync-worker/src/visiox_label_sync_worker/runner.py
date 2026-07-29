import argparse
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from visiox_common.settings import get_settings
from visiox_common.tasks import TaskStatus, TaskType
from visiox_db.models import LabelProject, Task
from visiox_db.session import create_session_factory
from visiox_storage.client import MinioObjectStorageClient, ObjectStorageClient
from visiox_yolo26.labelstudio.client import LabelStudioClient

from visiox_label_sync_worker.main import import_label_project_annotations, sync_label_project_samples


LABEL_SYNC_TASK_TYPES = {
    TaskType.SYNC_LABEL_STUDIO_DATA.value,
    TaskType.IMPORT_LABEL_STUDIO_ANNOTATION.value,
}


def queue_stale_label_projects(session: Session, *, stale_after_seconds: int) -> int:
    cutoff = datetime.now(UTC) - timedelta(seconds=stale_after_seconds)
    projects = session.scalars(
        select(LabelProject).where(
            (LabelProject.last_sync_at.is_(None)) | (LabelProject.last_sync_at < cutoff),
            LabelProject.sync_status != "pending",
        )
    ).all()
    created = 0
    for project in projects:
        active = session.scalar(
            select(Task.id).where(
                Task.task_type == TaskType.IMPORT_LABEL_STUDIO_ANNOTATION.value,
                Task.resource_type == "label_project",
                Task.resource_id == project.id,
                Task.status.in_([TaskStatus.QUEUED.value, TaskStatus.RUNNING.value]),
            )
        )
        if active is not None:
            continue
        session.add(
            Task(
                task_type=TaskType.IMPORT_LABEL_STUDIO_ANNOTATION.value,
                status=TaskStatus.QUEUED.value,
                progress=0,
                resource_type="label_project",
                resource_id=project.id,
                payload={"dataset_id": project.dataset_id, "label_project_id": project.id, "compensation": True},
            )
        )
        created += 1
    if created:
        session.commit()
    return created


def run_pending_label_sync_tasks(
    session: Session,
    storage: ObjectStorageClient,
    client: LabelStudioClient,
    limit: int = 10,
) -> list[Task]:
    tasks = session.scalars(
        select(Task)
        .where(Task.task_type.in_(LABEL_SYNC_TASK_TYPES), Task.status == TaskStatus.QUEUED.value)
        .order_by(Task.created_at, Task.id)
        .limit(limit)
    ).all()
    completed: list[Task] = []
    for task in tasks:
        label_project_id = task.payload.get("label_project_id") or task.resource_id
        if not label_project_id:
            task.status = TaskStatus.FAILED.value
            task.error_code = "MISSING_LABEL_PROJECT_ID"
            task.error_message = "Task payload must include label_project_id"
            session.add(task)
            session.commit()
            session.refresh(task)
            completed.append(task)
            continue
        if task.task_type == TaskType.SYNC_LABEL_STUDIO_DATA.value:
            completed.append(sync_label_project_samples(session, client, task.id, label_project_id, storage))
        elif task.task_type == TaskType.IMPORT_LABEL_STUDIO_ANNOTATION.value:
            completed.append(import_label_project_annotations(session, storage, client, task.id, label_project_id))
    return completed


def poll_label_sync_tasks(
    session_factory: Callable[[], Session],
    storage: ObjectStorageClient,
    client: LabelStudioClient,
    interval_seconds: float = 2.0,
) -> None:
    while True:
        with session_factory() as session:
            queue_stale_label_projects(session, stale_after_seconds=get_settings().label_studio_reconcile_seconds)
            processed = run_pending_label_sync_tasks(session, storage, client)
        if not processed:
            time.sleep(interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Label Studio sync tasks.")
    parser.add_argument("--once", action="store_true", help="Process queued tasks once and exit.")
    parser.add_argument("--interval-seconds", type=float, default=2.0)
    args = parser.parse_args()

    settings = get_settings()
    session_factory = create_session_factory()
    storage = MinioObjectStorageClient(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )
    with LabelStudioClient(settings.label_studio_url, settings.label_studio_token, timeout=60.0) as client:
        if args.once:
            with session_factory() as session:
                run_pending_label_sync_tasks(session, storage, client)
            return
        poll_label_sync_tasks(
            session_factory=session_factory,
            storage=storage,
            client=client,
            interval_seconds=args.interval_seconds,
        )


if __name__ == "__main__":
    main()
