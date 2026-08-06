from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Protocol

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from visiox_api.services.framework_adapters import FrameworkAdapterCatalog
from visiox_db.models import BaseModel, TrainedModel, TrainingPipeline
from visiox_storage.client import ObjectStorageClient
from visiox_training.adapters.base import FrameworkAdapter
from visiox_training.errors import FrameworkAdapterError


_ROLES = {
    "ultralytics": {
        "evaluation": ("best_weights", "last_weights", "checkpoint_weights"),
        "image_inference": ("best_weights", "last_weights", "checkpoint_weights"),
    },
    "paddlex": {
        "evaluation": ("best_dynamic_weights", "last_weights", "checkpoint_weights"),
        "image_inference": ("best_static_inference",),
    },
}
_FORMATS = {
    ("ultralytics", "evaluation"): {"pt"},
    ("ultralytics", "image_inference"): {"pt"},
    ("paddlex", "evaluation"): {"pdparams"},
    ("paddlex", "image_inference"): {"json", "directory", "bundle"},
}
_CHECKSUM_PATTERN = re.compile(r"[0-9a-fA-F]{64}")
_SAFE_IMAGE_SUFFIXES = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}


@dataclass(frozen=True)
class ResolvedPipelineModel:
    adapter: FrameworkAdapter
    model: TrainedModel | None
    artifact_uri: str
    artifact_role: str
    model_format: str


@dataclass(frozen=True)
class PaddleXInferenceRuntimeRequest:
    image_digest: str
    workspace: Path
    model_dir: Path
    image_path: Path
    output_dir: Path
    result_path: Path
    environment: str
    api_contract: str = "paddlex.create_model(model_dir=...)"

    @property
    def command(self) -> tuple[str, ...]:
        device = paddlex_device(self.environment)
        container_image = f"/workspace/input/image{safe_image_suffix(self.image_path.name)}"
        command = [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit=512",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=1g",
        ]
        if device.startswith("gpu:"):
            command.extend(("--gpus", f"device={device.removeprefix('gpu:')}"))
        command.extend(
            (
                "-v",
                f"{(self.workspace / 'paddlex_predict.py').resolve()}:/runner.py:ro",
                "-v",
                f"{self.model_dir.resolve()}:/workspace/model:ro",
                "-v",
                f"{self.image_path.resolve()}:{container_image}:ro",
                "-v",
                f"{self.output_dir.resolve()}:/workspace/output:rw",
                self.image_digest,
                "python",
                "/runner.py",
                device,
                container_image,
            )
        )
        return tuple(command)


class PaddleXInferenceRuntime(Protocol):
    def run(self, request: PaddleXInferenceRuntimeRequest) -> None: ...


class DockerPaddleXInferenceRuntime:
    def run(self, request: PaddleXInferenceRuntimeRequest) -> None:
        script = request.workspace / "paddlex_predict.py"
        script.write_text(_PADDLEX_PREDICT_SCRIPT, encoding="utf-8")
        try:
            completed = subprocess.run(
                request.command,
                check=False,
                capture_output=True,
                text=True,
                timeout=900,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError("PaddleX inference runtime could not be started") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout)[-2000:]
            raise RuntimeError(f"PaddleX inference failed: {detail}")
        if not request.result_path.is_file():
            raise RuntimeError("PaddleX inference did not produce inference_result.json")


def resolve_pipeline_model(
    session: Session,
    catalog: FrameworkAdapterCatalog,
    pipeline: TrainingPipeline,
    selection: str,
    *,
    operation: str,
) -> ResolvedPipelineModel:
    adapter = _resolve_adapter(catalog, pipeline)
    allowed_roles = _ROLES.get(pipeline.framework, {}).get(operation)
    if not allowed_roles:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Framework {pipeline.framework!r} does not support {operation}",
        )
    selected = (selection or "latest").strip()
    normalized = selected.casefold()
    if normalized in {"base", "official"}:
        if pipeline.framework != "ultralytics" or pipeline.base_model_id is None:
            raise HTTPException(status_code=409, detail="Official model is not compatible with pipeline")
        base = session.get(BaseModel, pipeline.base_model_id)
        if base is None or not base.local_uri:
            raise HTTPException(status_code=404, detail="Base model artifact not found")
        return ResolvedPipelineModel(adapter, None, base.local_uri, "official", "pt")

    statement = select(TrainedModel).where(
        TrainedModel.pipeline_id == pipeline.id,
        TrainedModel.status == "ready",
    )
    explicit = session.get(TrainedModel, selected)
    if explicit is not None:
        if explicit.pipeline_id != pipeline.id:
            raise HTTPException(status_code=404, detail="Selected model weight not found")
        if explicit.status != "ready":
            raise HTTPException(status_code=409, detail="Selected model weight is not ready")
        model = explicit
    else:
        requested_role = _selection_role(normalized, allowed_roles)
        if normalized not in {"latest", "trained", "best", "best.pt", "last", "last.pt"}:
            raise HTTPException(status_code=404, detail="Selected model weight not found")
        if requested_role is not None:
            statement = statement.where(TrainedModel.artifact_role == requested_role)
        else:
            statement = statement.where(TrainedModel.artifact_role.in_(allowed_roles))
        model = session.scalars(
            statement.order_by(TrainedModel.created_at.desc(), TrainedModel.id.desc())
        ).first()
        if model is None:
            if pipeline.framework == "ultralytics" and normalized in {"latest", "trained"}:
                return resolve_pipeline_model(
                    session, catalog, pipeline, "official", operation=operation
                )
            raise HTTPException(status_code=404, detail="Compatible trained model not found")
    _validate_model_identity(pipeline, adapter, model, operation, allowed_roles)
    return ResolvedPipelineModel(
        adapter=adapter,
        model=model,
        artifact_uri=model.artifact_uri,
        artifact_role=model.artifact_role,
        model_format=str(model.model_format),
    )


def download_artifact(
    storage: ObjectStorageClient,
    uri: str,
    destination: Path,
    *,
    checksum: str | None = None,
    size_bytes: int | None = None,
    require_integrity: bool = False,
) -> Path:
    if require_integrity and (not checksum or size_bytes is None):
        raise HTTPException(status_code=409, detail="Model artifact integrity metadata is unavailable")
    bucket, object_name = _storage_location(uri)
    path = storage.get_file(bucket, object_name, destination)
    _verify_download(path, checksum, size_bytes)
    return path


def download_paddlex_static_bundle(
    storage: ObjectStorageClient, resolved: ResolvedPipelineModel, destination: Path
) -> Path:
    model = resolved.model
    if model is None:
        raise HTTPException(status_code=409, detail="Static inference requires a trained model")
    role_artifacts = model.artifact_manifest.get("role_artifacts")
    if not isinstance(role_artifacts, list):
        raise HTTPException(status_code=409, detail="Static inference bundle manifest is unavailable")
    primary_path = _primary_manifest_path(model)
    bucket, primary_object = _storage_location(model.artifact_uri)
    if not primary_object.endswith(primary_path):
        raise HTTPException(status_code=409, detail="Static inference bundle URI does not match manifest")
    prefix = primary_object[: -len(primary_path)]
    names: set[str] = set()
    downloads: list[tuple[str, Path, str, int]] = []
    primary_integrity: tuple[str, int] | None = None
    seen_paths: set[str] = set()
    for item in role_artifacts:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise HTTPException(status_code=409, detail="Static inference bundle manifest is invalid")
        path = PurePosixPath(str(item["path"]))
        if path.is_absolute() or ".." in path.parts:
            raise HTTPException(status_code=409, detail="Static inference bundle path is invalid")
        if "inference" not in path.parts:
            raise HTTPException(status_code=409, detail="Static inference bundle path is invalid")
        relative = PurePosixPath(*path.parts[path.parts.index("inference") + 1 :])
        if not relative.parts:
            raise HTTPException(status_code=409, detail="Static inference bundle path is invalid")
        checksum = item.get("checksum_sha256")
        size_bytes = item.get("size_bytes")
        if (
            path.as_posix() in seen_paths
            or not isinstance(checksum, str)
            or _CHECKSUM_PATTERN.fullmatch(checksum) is None
            or not isinstance(size_bytes, int)
            or size_bytes < 0
        ):
            raise HTTPException(status_code=409, detail="Static inference bundle integrity metadata is invalid")
        seen_paths.add(path.as_posix())
        names.add(relative.name)
        downloads.append(
            (prefix + path.as_posix(), destination / relative.as_posix(), checksum, size_bytes)
        )
        if path.as_posix() == primary_path:
            primary_integrity = (checksum, size_bytes)
    if not any(name.endswith(".json") for name in names) or not any(
        name.endswith(".pdiparams") for name in names
    ):
        raise HTTPException(status_code=409, detail="Static inference bundle is incomplete")
    if (
        primary_integrity is None
        or not isinstance(model.checksum, str)
        or _CHECKSUM_PATTERN.fullmatch(model.checksum) is None
        or model.size_bytes is None
        or primary_integrity[0].casefold() != model.checksum.casefold()
        or primary_integrity[1] != model.size_bytes
    ):
        raise HTTPException(
            status_code=409,
            detail="Static inference primary artifact integrity does not match model",
        )
    for object_name, target, checksum, size_bytes in downloads:
        downloaded = storage.get_file(bucket, object_name, target)
        _verify_download(downloaded, checksum, size_bytes)
    return destination


def _resolve_adapter(
    catalog: FrameworkAdapterCatalog, pipeline: TrainingPipeline
) -> FrameworkAdapter:
    try:
        adapter = catalog.registry.get(pipeline.adapter_key, pipeline.adapter_version)
    except FrameworkAdapterError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if adapter.framework != pipeline.framework or not adapter.capabilities.supports_task(
        pipeline.task_kind
    ):
        raise HTTPException(status_code=409, detail="Pipeline adapter identity is inconsistent")
    return adapter


def _validate_model_identity(
    pipeline: TrainingPipeline,
    adapter: FrameworkAdapter,
    model: TrainedModel,
    operation: str,
    allowed_roles: tuple[str, ...],
) -> None:
    if model.framework != pipeline.framework:
        raise HTTPException(status_code=409, detail="Selected model framework does not match pipeline")
    if model.adapter_key != pipeline.adapter_key:
        raise HTTPException(status_code=409, detail="Selected model adapter does not match pipeline")
    if model.task != pipeline.task:
        raise HTTPException(status_code=409, detail="Selected model task does not match pipeline")
    manifest = model.artifact_manifest or {}
    if (
        manifest.get("adapter_key") != adapter.adapter_key
        or manifest.get("adapter_version") != adapter.adapter_version
    ):
        raise HTTPException(status_code=409, detail="Selected model manifest identity does not match pipeline")
    if model.artifact_role not in allowed_roles:
        raise HTTPException(status_code=409, detail="Selected model artifact role is incompatible")
    if model.model_format not in _FORMATS[(pipeline.framework, operation)]:
        raise HTTPException(status_code=409, detail="Selected model format is incompatible")
    if pipeline.framework == "paddlex":
        recipe_model = pipeline.recipe.get("model") if isinstance(pipeline.recipe, dict) else None
        runtime_model_id = recipe_model.get("runtime_id") if isinstance(recipe_model, dict) else None
        supported_runtime_ids = {
            candidate.runtime_id
            for task in adapter.capabilities.tasks
            if task.task_type == pipeline.task_kind
            for candidate in task.models
        }
        if not isinstance(runtime_model_id, str) or runtime_model_id not in supported_runtime_ids:
            raise HTTPException(status_code=409, detail="PaddleX runtime model config is incompatible")
        identity = manifest.get("model_identity")
        if not isinstance(identity, dict):
            raise HTTPException(status_code=409, detail="Selected model manifest identity is unavailable")
        if model.model_family != pipeline.model_family or identity.get("model_family") != pipeline.model_family:
            raise HTTPException(status_code=409, detail="Selected model family does not match pipeline")
        if identity.get("runtime_model_id") != runtime_model_id:
            raise HTTPException(status_code=409, detail="Selected model runtime config does not match pipeline")
        if not model.checksum or model.size_bytes is None:
            raise HTTPException(status_code=409, detail="Model artifact integrity metadata is unavailable")


def _selection_role(value: str, allowed_roles: tuple[str, ...]) -> str | None:
    if value in {"last", "last.pt"}:
        return "last_weights"
    if value in {"best", "best.pt"}:
        return allowed_roles[0]
    return None


def _primary_manifest_path(model: TrainedModel) -> str:
    role_artifacts = model.artifact_manifest.get("role_artifacts")
    if not isinstance(role_artifacts, list):
        raise HTTPException(status_code=409, detail="Artifact manifest is unavailable")
    for item in role_artifacts:
        if isinstance(item, dict) and isinstance(item.get("path"), str):
            if PurePosixPath(str(item["path"])).name == model.name:
                return str(item["path"])
    for item in role_artifacts:
        if isinstance(item, dict) and isinstance(item.get("path"), str):
            if PurePosixPath(str(item["path"])).suffix.casefold() == f".{model.model_format}":
                return str(item["path"])
    raise HTTPException(status_code=409, detail="Artifact manifest does not identify primary model")


def _storage_location(uri: str) -> tuple[str, str]:
    scheme, separator, remainder = uri.partition("://")
    if not separator or scheme not in {"memory", "minio"}:
        raise HTTPException(status_code=404, detail="Model artifact is not available")
    bucket, separator, object_name = remainder.partition("/")
    if not separator or not bucket or not object_name or "?" in object_name or "#" in object_name:
        raise HTTPException(status_code=404, detail="Model artifact is not available")
    return bucket, object_name


def paddlex_device(environment: str) -> str:
    value = (environment or "").strip().casefold()
    if not value or value == "cpu":
        return "cpu"
    if value.isdigit():
        return f"gpu:{value}"
    import re

    match = re.search(r"(?:gpu|cuda|device|node)[^\d]*(\d+)", value)
    if match:
        return f"gpu:{match.group(1)}"
    raise HTTPException(status_code=422, detail="PaddleX environment must select CPU or a GPU device")


def safe_image_suffix(filename: str | None) -> str:
    suffix = Path(filename or "").suffix.casefold()
    return suffix if suffix in _SAFE_IMAGE_SUFFIXES else ".png"


def _verify_download(path: Path, checksum: str | None, size_bytes: int | None) -> None:
    if size_bytes is not None and path.stat().st_size != size_bytes:
        raise HTTPException(status_code=409, detail="Model artifact size does not match manifest")
    if checksum is not None:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != checksum:
            raise HTTPException(status_code=409, detail="Model artifact checksum does not match manifest")


_PADDLEX_PREDICT_SCRIPT = r'''
import json
from pathlib import Path
import paddlex
import sys

device = sys.argv[1]
image_path = sys.argv[2]
model = paddlex.create_model(model_dir="/workspace/model", device=device)
results = list(model.predict(image_path, device=device))
if not results:
    raise RuntimeError("PaddleX returned no prediction result")
result = results[0]
output = Path("/workspace/output")
output.mkdir(parents=True, exist_ok=True)
result.save_to_img(str(output))
images = sorted(output.glob("*.png")) + sorted(output.glob("*.jpg"))
if not images:
    raise RuntimeError("PaddleX did not render an annotated image")
payload = result.json if isinstance(result.json, dict) else json.loads(result.json)
boxes = payload.get("res", payload).get("boxes", [])
(output / "inference_result.json").write_text(
    json.dumps({"boxes": boxes, "annotated_image": images[0].name}), encoding="utf-8"
)
'''
