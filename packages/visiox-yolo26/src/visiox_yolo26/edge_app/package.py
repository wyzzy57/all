from __future__ import annotations

import hashlib
import io
import json
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SUPPORTED_MODEL_FORMATS = frozenset({"onnx", "torchscript"})


@dataclass(frozen=True)
class EdgeAppPackageSpec:
    app_name: str
    version: str
    task: str
    model_format: str
    model_path: Path
    runtime: dict[str, Any]
    cameras: list[dict[str, Any]]
    rules: dict[str, Any]
    image_ref: str
    output_path: Path


@dataclass(frozen=True)
class EdgeAppPackageResult:
    package_path: Path
    checksum: str
    manifest: dict[str, Any]


def build_edge_app_package(spec: EdgeAppPackageSpec) -> EdgeAppPackageResult:
    model_format = spec.model_format.lower().strip()
    if model_format not in SUPPORTED_MODEL_FORMATS:
        raise ValueError(f"unsupported model format: {spec.model_format}")
    if not spec.model_path.is_file():
        raise FileNotFoundError(spec.model_path)

    package_path = spec.output_path
    package_path.parent.mkdir(parents=True, exist_ok=True)

    manifest = _build_manifest(spec, model_format)
    with tarfile.open(package_path, mode="w:gz") as tar:
        _add_text(tar, "app.yaml", _as_yaml_like(manifest["app"]))
        _add_file(tar, f"model/exported.{model_format}", spec.model_path)
        _add_text(tar, "config/runtime.yaml", _as_yaml_like(spec.runtime))
        _add_text(tar, "config/cameras.yaml", _as_yaml_like(spec.cameras))
        _add_text(tar, "config/rules.yaml", _as_yaml_like(spec.rules))
        _add_text(tar, "services/yolo26-inference/image.txt", f"{spec.image_ref}\n")

    checksum = _sha256_file(package_path)
    manifest["package"] = {
        "path": package_path.name,
        "checksum": checksum,
    }
    return EdgeAppPackageResult(package_path=package_path, checksum=checksum, manifest=manifest)


def _build_manifest(spec: EdgeAppPackageSpec, model_format: str) -> dict[str, Any]:
    return {
        "app": {
            "name": spec.app_name,
            "version": spec.version,
            "task": spec.task,
        },
        "model": {
            "format": model_format,
            "path": f"model/exported.{model_format}",
        },
        "runtime": spec.runtime,
        "cameras": spec.cameras,
        "rules": spec.rules,
        "services": {
            "yolo26-inference": {
                "image": spec.image_ref,
            },
        },
    }


def _add_file(tar: tarfile.TarFile, arcname: str, source_path: Path) -> None:
    data = source_path.read_bytes()
    info = tarfile.TarInfo(name=arcname)
    info.size = len(data)
    info.mode = 0o644
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    tar.addfile(info, io.BytesIO(data))


def _add_text(tar: tarfile.TarFile, arcname: str, content: str) -> None:
    data = content.encode("utf-8")
    info = tarfile.TarInfo(name=arcname)
    info.size = len(data)
    info.mode = 0o644
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    tar.addfile(info, io.BytesIO(data))


def _as_yaml_like(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
