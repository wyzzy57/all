from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urljoin

import httpx
from sqlalchemy.orm import Session

from visiox_common.tasks import TaskStatus
from visiox_db.models import BaseModel, ModelSource, Task
from visiox_storage.checksum import sha256_file, verify_sha256
from visiox_storage.client import ObjectStorageClient


class BaseModelDownloadError(RuntimeError):
    """Raised when a base model download task cannot complete."""


@dataclass(frozen=True)
class DownloadResult:
    base_model_id: str
    local_uri: str
    checksum: str
    size_bytes: int


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _mark_task_running(task: Task) -> None:
    task.status = TaskStatus.RUNNING.value
    task.stage = "download"
    task.started_at = task.started_at or _utc_now()


def _mark_failed(
    session: Session,
    task: Task | None,
    base_model: BaseModel | None,
    error: Exception,
) -> None:
    if base_model is not None:
        base_model.status = "failed"
        session.add(base_model)
    if task is not None:
        task.status = TaskStatus.FAILED.value
        task.error_code = "BASE_MODEL_DOWNLOAD_FAILED"
        task.error_message = str(error)
        task.retryable = True
        task.finished_at = _utc_now()
        session.add(task)
    session.commit()


def _source_file_path(source: ModelSource, base_model: BaseModel, tmp_dir: Path) -> Path:
    if source.type == "local_mount":
        if not source.mount_path:
            raise BaseModelDownloadError("local_mount source requires mount_path")
        path = Path(source.mount_path) / base_model.source_path
        if not path.is_file():
            raise BaseModelDownloadError(f"source file not found: {path}")
        return path

    if source.type == "http":
        if not source.base_url:
            raise BaseModelDownloadError("http source requires base_url")
        destination = tmp_dir / base_model.filename
        with httpx.stream("GET", urljoin(source.base_url.rstrip("/") + "/", base_model.source_path)) as response:
            response.raise_for_status()
            with destination.open("wb") as file:
                for chunk in response.iter_bytes():
                    file.write(chunk)
        return destination

    raise BaseModelDownloadError(f"unsupported direct source type: {source.type}")


def _copy_from_object_source(
    source: ModelSource,
    base_model: BaseModel,
    storage: ObjectStorageClient,
    destination: Path,
) -> Path:
    if source.type not in {"minio", "s3"}:
        raise BaseModelDownloadError(f"unsupported source type: {source.type}")
    if not source.bucket:
        raise BaseModelDownloadError(f"{source.type} source requires bucket")
    return storage.get_file(source.bucket, base_model.source_path, destination)


def download_base_model(
    session: Session,
    task_id: str,
    base_model_id: str,
    storage: ObjectStorageClient,
    bucket: str = "models",
) -> DownloadResult:
    task = session.get(Task, task_id)
    base_model = session.get(BaseModel, base_model_id)
    try:
        if task is None:
            raise BaseModelDownloadError(f"task not found: {task_id}")
        if base_model is None:
            raise BaseModelDownloadError(f"base model not found: {base_model_id}")
        if base_model.model_source_id is None:
            raise BaseModelDownloadError("base model has no model source")
        source = session.get(ModelSource, base_model.model_source_id)
        if source is None:
            raise BaseModelDownloadError(f"model source not found: {base_model.model_source_id}")
        if not source.enabled:
            raise BaseModelDownloadError(f"model source is disabled: {source.id}")

        _mark_task_running(task)
        session.add(task)
        session.commit()

        with TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            source_path = (
                _copy_from_object_source(source, base_model, storage, tmp_dir / base_model.filename)
                if source.type in {"minio", "s3"}
                else _source_file_path(source, base_model, tmp_dir)
            )
            checksum = sha256_file(source_path)
            verify_sha256(checksum, base_model.checksum)

            object_name = f"base/{base_model.id}/{base_model.filename}"
            local_uri = storage.put_file(bucket, object_name, source_path)
            size_bytes = source_path.stat().st_size

        base_model.local_uri = local_uri
        base_model.checksum = checksum
        base_model.size_bytes = size_bytes
        base_model.status = "ready"
        task.status = TaskStatus.SUCCESS.value
        task.progress = 100
        task.stage = "download"
        task.finished_at = _utc_now()
        session.add_all([base_model, task])
        session.commit()

        return DownloadResult(
            base_model_id=base_model.id,
            local_uri=local_uri,
            checksum=checksum,
            size_bytes=size_bytes,
        )
    except Exception as exc:
        _mark_failed(session, task, base_model, exc)
        raise
