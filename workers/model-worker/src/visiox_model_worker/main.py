from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
from sqlalchemy.orm import Session

from visiox_common.tasks import TaskStatus, TaskType
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


def _mark_task_success(task: Task) -> None:
    task.status = TaskStatus.SUCCESS.value
    task.progress = 100
    task.stage = "download"
    task.finished_at = _utc_now()


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


def _safe_relative_parts(source_path: str) -> list[str]:
    parsed = urlsplit(source_path)
    normalized = source_path.replace("\\", "/")
    parts = [part for part in normalized.split("/") if part not in {"", "."}]
    if (
        parsed.scheme
        or parsed.netloc
        or normalized.startswith("/")
        or normalized.startswith("//")
        or any(part == ".." for part in parts)
        or Path(source_path).is_absolute()
    ):
        raise BaseModelDownloadError(f"unsafe source path: {source_path}")
    return parts


def _local_mount_path(mount_path: str, source_path: str) -> Path:
    parts = _safe_relative_parts(source_path)
    root = Path(mount_path).resolve()
    path = root.joinpath(*parts).resolve()
    if path != root and root not in path.parents:
        raise BaseModelDownloadError(f"unsafe source path: {source_path}")
    return path


def _http_source_url(base_url: str, source_path: str) -> str:
    parts = _safe_relative_parts(source_path)
    parsed = urlsplit(base_url)
    if not parsed.scheme or not parsed.netloc:
        raise BaseModelDownloadError("http source requires absolute base_url")
    base_path = parsed.path.rstrip("/")
    encoded_path = "/".join(quote(part) for part in parts)
    path = f"{base_path}/{encoded_path}" if base_path else f"/{encoded_path}"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _source_file_path(source: ModelSource, base_model: BaseModel, tmp_dir: Path) -> Path:
    if source.type == "local_mount":
        if not source.mount_path:
            raise BaseModelDownloadError("local_mount source requires mount_path")
        path = _local_mount_path(source.mount_path, base_model.source_path)
        if not path.is_file():
            raise BaseModelDownloadError(f"source file not found: {path}")
        return path

    if source.type == "http":
        if not source.base_url:
            raise BaseModelDownloadError("http source requires base_url")
        destination = tmp_dir / base_model.filename
        with httpx.stream("GET", _http_source_url(source.base_url, base_model.source_path)) as response:
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


def _validate_task(task: Task, base_model_id: str) -> None:
    if task.task_type != TaskType.DOWNLOAD_BASE_MODEL.value:
        raise BaseModelDownloadError(f"task {task.id} is not DOWNLOAD_BASE_MODEL")
    if task.resource_id is not None:
        if task.resource_id != base_model_id:
            raise BaseModelDownloadError(
                f"task resource_id {task.resource_id} does not match base model {base_model_id}"
            )
        return
    if task.payload.get("base_model_id") != base_model_id:
        raise BaseModelDownloadError(
            f"task payload base_model_id {task.payload.get('base_model_id')} does not match {base_model_id}"
        )


def _is_terminal(task: Task) -> bool:
    return task.status in {
        TaskStatus.SUCCESS.value,
        TaskStatus.FAILED.value,
        TaskStatus.CANCELED.value,
    }


def _ready_result(base_model: BaseModel) -> DownloadResult:
    return DownloadResult(
        base_model_id=base_model.id,
        local_uri=base_model.local_uri or "",
        checksum=base_model.checksum or "",
        size_bytes=base_model.size_bytes or 0,
    )


def download_base_model(
    session: Session,
    task_id: str,
    base_model_id: str,
    storage: ObjectStorageClient,
    bucket: str = "models",
) -> DownloadResult:
    task = session.get(Task, task_id)
    base_model = session.get(BaseModel, base_model_id)
    if task is None:
        raise BaseModelDownloadError(f"task not found: {task_id}")
    _validate_task(task, base_model_id)
    if _is_terminal(task):
        if (
            task.status == TaskStatus.SUCCESS.value
            and base_model is not None
            and base_model.status == "ready"
        ):
            return _ready_result(base_model)
        raise BaseModelDownloadError(f"task {task.id} is already terminal: {task.status}")
    try:
        if base_model is None:
            raise BaseModelDownloadError(f"base model not found: {base_model_id}")
        if base_model.status == "ready":
            _mark_task_success(task)
            session.add(task)
            session.commit()
            return _ready_result(base_model)
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
        _mark_task_success(task)
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
