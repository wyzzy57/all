import json
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from visiox_common.tasks import TaskStatus, TaskType
from visiox_db.models import Annotation, Dataset, DatasetSample, LabelProject, Task
from visiox_storage.client import ObjectStorageClient
from visiox_yolo26.labelstudio.importer import normalize_label_studio_task


def sync_label_project_samples(session: Session, client: Any, task_id: str, label_project_id: str) -> Task:
    task = _start_task(session, task_id, "syncing_samples")
    project = _label_project_or_raise(session, label_project_id)
    try:
        _validate_task(task, project, TaskType.SYNC_LABEL_STUDIO_DATA)
        samples = session.scalars(
            select(DatasetSample)
            .where(DatasetSample.dataset_id == project.dataset_id)
            .order_by(DatasetSample.created_at, DatasetSample.id)
        ).all()
        label_studio_tasks = [
            {
                "data": {
                    "image": sample.file_uri,
                    "visiox_dataset_id": project.dataset_id,
                    "visiox_sample_id": sample.id,
                }
            }
            for sample in samples
        ]
        client.import_tasks(project.external_project_id, label_studio_tasks)
        project.sync_status = "synced"
        project.last_sync_at = _utc_now()
        task.status = TaskStatus.SUCCESS.value
        task.progress = 100
        task.stage = "completed"
        task.finished_at = _utc_now()
        session.add_all([project, task])
        session.commit()
        session.refresh(task)
        return task
    except Exception as exc:
        return _fail_task(session, task, project, "LABEL_STUDIO_SYNC_FAILED", exc)


def import_label_project_annotations(
    session: Session,
    storage: ObjectStorageClient,
    client: Any,
    task_id: str,
    label_project_id: str,
) -> Task:
    task = _start_task(session, task_id, "importing_annotations")
    project = _label_project_or_raise(session, label_project_id)
    stored_objects: list[tuple[str, str]] = []
    try:
        _validate_task(task, project, TaskType.IMPORT_LABEL_STUDIO_ANNOTATION)
        export_payload = client.export_annotations(project.external_project_id)
        for task_payload in export_payload:
            normalized = normalize_label_studio_task(task_payload)
            sample = session.get(DatasetSample, normalized.sample_id)
            if sample is None or sample.dataset_id != project.dataset_id:
                raise ValueError(f"Unknown dataset sample in Label Studio export: {normalized.sample_id}")
            raw_payload_uri, stored_object = _store_raw_payload(storage, project, sample, task_payload)
            stored_objects.append(stored_object)
            annotation = session.scalar(
                select(Annotation).where(
                    Annotation.dataset_sample_id == sample.id,
                    Annotation.source == "label_studio",
                )
            )
            if annotation is None:
                annotation = Annotation(dataset_sample_id=sample.id, source="label_studio")
            annotation.raw_payload_uri = raw_payload_uri
            annotation.internal_payload = normalized.payload
            annotation.validation_status = "pending"
            sample.annotation_status = "labeled"
            session.add_all([annotation, sample])

        dataset = session.get(Dataset, project.dataset_id)
        if dataset is not None:
            session.flush()
            dataset.annotation_count = session.scalar(
                select(func.count()).select_from(Annotation).join(DatasetSample).where(DatasetSample.dataset_id == dataset.id)
            ) or 0
            session.add(dataset)
        project.sync_status = "imported"
        project.last_sync_at = _utc_now()
        task.status = TaskStatus.SUCCESS.value
        task.progress = 100
        task.stage = "completed"
        task.finished_at = _utc_now()
        session.add_all([project, task])
        session.commit()
        session.refresh(task)
        return task
    except Exception as exc:
        _cleanup_stored_objects(storage, stored_objects)
        return _fail_task(session, task, project, "LABEL_STUDIO_IMPORT_FAILED", exc)


def _start_task(session: Session, task_id: str, stage: str) -> Task:
    task = _task_or_raise(session, task_id)
    task.status = TaskStatus.RUNNING.value
    task.progress = 10
    task.stage = stage
    task.started_at = task.started_at or _utc_now()
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


def _fail_task(session: Session, task: Task, project: LabelProject, error_code: str, exc: Exception) -> Task:
    session.rollback()
    task = _task_or_raise(session, task.id)
    project = _label_project_or_raise(session, project.id)
    task.status = TaskStatus.FAILED.value
    task.error_code = error_code
    task.error_message = str(exc)
    task.finished_at = _utc_now()
    project.sync_status = "failed"
    session.add_all([task, project])
    session.commit()
    session.refresh(task)
    return task


def _store_raw_payload(
    storage: ObjectStorageClient,
    project: LabelProject,
    sample: DatasetSample,
    payload: dict[str, Any],
) -> tuple[str, tuple[str, str]]:
    bucket = "label-studio"
    object_name = f"{project.dataset_id}/label-studio/{project.id}/{sample.id}.json"
    with NamedTemporaryFile("w", encoding="utf-8", delete=False) as temp_file:
        json.dump(payload, temp_file, ensure_ascii=True, separators=(",", ":"))
        temp_path = Path(temp_file.name)
    try:
        return storage.put_file(bucket, object_name, temp_path, content_type="application/json"), (bucket, object_name)
    finally:
        temp_path.unlink(missing_ok=True)


def _task_or_raise(session: Session, task_id: str) -> Task:
    task = session.get(Task, task_id)
    if task is None:
        raise ValueError(f"Task not found: {task_id}")
    return task


def _label_project_or_raise(session: Session, label_project_id: str) -> LabelProject:
    project = session.get(LabelProject, label_project_id)
    if project is None:
        raise ValueError(f"Label project not found: {label_project_id}")
    return project


def _validate_task(task: Task, project: LabelProject, expected_type: TaskType) -> None:
    if task.task_type != expected_type.value:
        raise ValueError(f"Task {task.id} is not {expected_type.value}")
    if task.resource_id is not None and task.resource_id != project.id:
        raise ValueError(f"Task {task.id} does not target label project {project.id}")
    payload_project_id = task.payload.get("label_project_id")
    if payload_project_id is not None and payload_project_id != project.id:
        raise ValueError(f"Task {task.id} payload does not target label project {project.id}")


def _cleanup_stored_objects(storage: ObjectStorageClient, stored_objects: list[tuple[str, str]]) -> None:
    for bucket, object_name in reversed(stored_objects):
        try:
            storage.delete_file(bucket, object_name)
        except Exception:
            pass


def _utc_now() -> datetime:
    return datetime.now(UTC)
