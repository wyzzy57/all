from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import update
from sqlalchemy.orm import Session

from visiox_common.tasks import TaskStatus, TaskType
from visiox_db.models import EdgeApp, EdgeAppVersion, Task, TrainedModel
from visiox_storage.client import ObjectStorageClient
from visiox_yolo26.converters.internal_schema import parse_storage_uri
from visiox_yolo26.edge_app import EdgeAppPackageSpec, build_edge_app_package
from visiox_yolo26.export import ExportParamsError, build_export_command


TERMINAL_TASK_STATUSES = {TaskStatus.SUCCESS.value, TaskStatus.FAILED.value, TaskStatus.CANCELED.value}
SUPPORTED_EXPORT_FORMATS = {"onnx", "torchscript"}


class EdgeAppPackagingError(RuntimeError):
    pass


class InvalidEdgeAppTaskPayloadError(EdgeAppPackagingError):
    pass


@dataclass(frozen=True)
class ExportCommandResult:
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    artifact_path: Path | None = None


class ExportCommandRunner(Protocol):
    def run(self, argv: list[str], work_dir: Path) -> ExportCommandResult: ...


@dataclass(frozen=True)
class EdgeAppPackagingResult:
    edge_app_version_id: str
    package_uri: str | None
    status: str


def run_edge_app_packaging(
    session: Session,
    storage: ObjectStorageClient,
    runner: ExportCommandRunner,
    task_id: str,
    edge_app_version_id: str,
    work_dir: Path,
    stale_after_seconds: int = 3600,
) -> EdgeAppPackagingResult:
    task = _require_task(session, task_id, edge_app_version_id)
    version = _require_version(session, edge_app_version_id)
    if version.status == "ready" and version.package_uri != "pending":
        return EdgeAppPackagingResult(edge_app_version_id=version.id, package_uri=version.package_uri, status=version.status)
    if task.status in TERMINAL_TASK_STATUSES and task.status != TaskStatus.SUCCESS.value:
        raise EdgeAppPackagingError(f"task is terminal: {task.status}")

    work_dir.mkdir(parents=True, exist_ok=True)
    now = _utc_now()
    claim_statuses = [TaskStatus.QUEUED.value]
    if _is_stale_running_task(task, now, stale_after_seconds):
        claim_statuses.append(TaskStatus.RUNNING.value)
    claimed = session.execute(
        update(Task)
        .where(Task.id == task.id, Task.status.in_(claim_statuses))
        .values(status=TaskStatus.RUNNING.value, stage="prepare", started_at=task.started_at or now)
    ).rowcount
    if claimed != 1:
        session.rollback()
        task = _require_task(session, task_id, edge_app_version_id)
        version = _require_version(session, edge_app_version_id)
        if version.status == "ready" and version.package_uri != "pending":
            return EdgeAppPackagingResult(edge_app_version_id=version.id, package_uri=version.package_uri, status=version.status)
        raise EdgeAppPackagingError(f"task is already claimed: {task.status}")
    version.status = "packaging"
    session.add(version)
    session.commit()

    stored_objects: list[tuple[str, str]] = []
    try:
        task = _require_task(session, task_id, edge_app_version_id)
        version = _require_version(session, edge_app_version_id)
        app = session.get(EdgeApp, version.edge_app_id)
        model = session.get(TrainedModel, version.trained_model_id)
        if app is None:
            raise EdgeAppPackagingError("edge app not found")
        if model is None:
            raise EdgeAppPackagingError("trained model not found")
        if model.status != "ready":
            raise EdgeAppPackagingError(f"trained model is not ready: {model.status}")
        _validate_task_payload(task, version, model)
        payload = task.payload or {}
        export_format = str(payload.get("export_format", "onnx")).lower()
        runtime = _dict_payload(payload, "runtime")
        cameras = _list_payload(payload, "cameras")
        rules = _dict_payload(payload, "rules")

        model_path = _download_model(storage, model, work_dir / "model" / "input.pt")
        task.stage = "export_model"
        session.add(task)
        session.commit()
        export_dir = work_dir / "export"
        try:
            command = build_export_command(
                model_path=model_path,
                format=export_format,
                output_dir=export_dir,
                imgsz=runtime.get("imgsz"),
                half=runtime.get("half", False),
                device=runtime.get("device"),
            )
        except ExportParamsError as exc:
            raise InvalidEdgeAppTaskPayloadError(str(exc)) from exc
        export_result = runner.run(command.argv, work_dir)
        if export_result.exit_code != 0:
            raise EdgeAppPackagingError(f"export command failed with exit code {export_result.exit_code}: {export_result.stderr}")
        if export_result.artifact_path is None:
            raise EdgeAppPackagingError("export artifact path is missing")

        task.stage = "package_edge_app"
        session.add(task)
        session.commit()
        package_result = build_edge_app_package(
            EdgeAppPackageSpec(
                app_name=app.name,
                version=version.version,
                task=model.task,
                model_format=export_format,
                model_path=export_result.artifact_path,
                runtime=runtime,
                cameras=cameras,
                rules=rules,
                image_ref=str(runtime.get("image") or "registry.local/visiox/yolo26-inference:0.1.0"),
                output_path=work_dir / "package" / "edge-app-package.tar.gz",
            )
        )

        task.stage = "persist_package"
        session.add(task)
        session.commit()
        object_name = f"edge-apps/{version.id}/package.tar.gz"
        package_uri = storage.put_file("packages", object_name, package_result.package_path)
        stored_objects.append(("packages", object_name))

        version.package_uri = package_uri
        version.checksum = package_result.checksum
        version.manifest = {
            **(version.manifest or {}),
            **package_result.manifest,
            "task_id": task.id,
            "trained_model_id": model.id,
            "export_format": export_format,
        }
        version.status = "ready"
        app.status = "ready"
        task.status = TaskStatus.SUCCESS.value
        task.progress = 100
        task.stage = "completed"
        task.error_code = None
        task.error_message = None
        task.retryable = False
        task.finished_at = _utc_now()
        session.add_all([app, version, task])
        session.commit()
        return EdgeAppPackagingResult(edge_app_version_id=version.id, package_uri=version.package_uri, status=version.status)
    except Exception as exc:
        session.rollback()
        _cleanup_stored_objects(storage, stored_objects)
        task = session.get(Task, task_id)
        version = session.get(EdgeAppVersion, edge_app_version_id)
        app = session.get(EdgeApp, version.edge_app_id) if version is not None else None
        if task is not None:
            task.status = TaskStatus.FAILED.value
            task.error_code = "INVALID_TASK_PAYLOAD" if isinstance(exc, InvalidEdgeAppTaskPayloadError) else "EDGE_APP_PACKAGE_FAILED"
            task.error_message = str(exc)
            task.retryable = True
            task.finished_at = _utc_now()
            task.stage = task.stage or "package_edge_app"
            session.add(task)
        if version is not None:
            version.status = "failed"
            session.add(version)
        if app is not None:
            app.status = "ready" if _edge_app_has_ready_version(session, app.id) else "failed"
            session.add(app)
        session.commit()
        raise


def _require_task(session: Session, task_id: str, edge_app_version_id: str) -> Task:
    task = session.get(Task, task_id)
    if task is None:
        raise EdgeAppPackagingError("task not found")
    if task.task_type != TaskType.BUILD_EDGE_APP_PACKAGE.value:
        raise EdgeAppPackagingError(f"unexpected task type: {task.task_type}")
    if task.resource_type != "edge_app_version" or task.resource_id != edge_app_version_id:
        raise EdgeAppPackagingError("task does not match edge app version")
    payload = task.payload or {}
    if payload.get("edge_app_version_id") not in {None, edge_app_version_id}:
        raise EdgeAppPackagingError("task payload does not match edge app version")
    return task


def _require_version(session: Session, edge_app_version_id: str) -> EdgeAppVersion:
    version = session.get(EdgeAppVersion, edge_app_version_id)
    if version is None:
        raise EdgeAppPackagingError("edge app version not found")
    return version


def _download_model(storage: ObjectStorageClient, model: TrainedModel, destination: Path) -> Path:
    bucket, object_name = parse_storage_uri(str(model.artifact_uri))
    return storage.get_file(bucket, object_name, destination)


def _validate_task_payload(task: Task, version: EdgeAppVersion, model: TrainedModel) -> None:
    payload = task.payload or {}
    expected = {
        "edge_app_id": version.edge_app_id,
        "edge_app_version_id": version.id,
        "trained_model_id": model.id,
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            raise InvalidEdgeAppTaskPayloadError(f"task payload {key} does not match edge app version")
    if not isinstance(payload.get("export_format"), str):
        raise InvalidEdgeAppTaskPayloadError("task payload export_format must be a string")
    if str(payload["export_format"]).lower() not in SUPPORTED_EXPORT_FORMATS:
        raise InvalidEdgeAppTaskPayloadError(f"unsupported export format: {payload['export_format']}")
    runtime = _dict_payload(payload, "runtime")
    cameras = _list_payload(payload, "cameras")
    rules = _dict_payload(payload, "rules")
    _validate_runtime(runtime)
    manifest = version.manifest or {}
    expected_payload = {
        "export_format": manifest.get("export_format"),
        "runtime": manifest.get("runtime"),
        "cameras": manifest.get("cameras"),
        "rules": manifest.get("rules"),
    }
    actual_payload = {
        "export_format": payload.get("export_format"),
        "runtime": runtime,
        "cameras": cameras,
        "rules": rules,
    }
    for key, value in expected_payload.items():
        if value is not None and actual_payload[key] != value:
            raise InvalidEdgeAppTaskPayloadError(f"task payload {key} does not match edge app version")


def _dict_payload(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise InvalidEdgeAppTaskPayloadError(f"task payload {key} must be an object")
    return value


def _validate_runtime(runtime: dict[str, Any]) -> None:
    imgsz = runtime.get("imgsz")
    if imgsz is not None and (isinstance(imgsz, bool) or not isinstance(imgsz, int) or imgsz <= 0):
        raise InvalidEdgeAppTaskPayloadError("task payload runtime.imgsz must be a positive integer")
    half = runtime.get("half", False)
    if not isinstance(half, bool):
        raise InvalidEdgeAppTaskPayloadError("task payload runtime.half must be a boolean")
    device = runtime.get("device")
    if device is not None and (not isinstance(device, str) or not device.strip()):
        raise InvalidEdgeAppTaskPayloadError("task payload runtime.device must be a non-empty string")
    image = runtime.get("image")
    if image is not None and (not isinstance(image, str) or not image.strip()):
        raise InvalidEdgeAppTaskPayloadError("task payload runtime.image must be a non-empty string")


def _list_payload(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = payload.get(key)
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise InvalidEdgeAppTaskPayloadError(f"task payload {key} must be a list of objects")
    return value


def _cleanup_stored_objects(storage: ObjectStorageClient, stored_objects: list[tuple[str, str]]) -> None:
    for bucket, object_name in reversed(stored_objects):
        try:
            storage.delete_file(bucket, object_name)
        except Exception:
            pass


def _edge_app_has_ready_version(session: Session, edge_app_id: str) -> bool:
    return bool(
        session.query(EdgeAppVersion.id)
        .filter(
            EdgeAppVersion.edge_app_id == edge_app_id,
            EdgeAppVersion.status == "ready",
        )
        .first()
    )


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
