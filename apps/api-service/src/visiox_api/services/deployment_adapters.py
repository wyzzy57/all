from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import PurePosixPath
import re
from typing import Any

from visiox_edge_executor_worker.deployment import validate_image_digest
from visiox_edge_executor_worker.inventory import InventorySnapshot


_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_VERSION = re.compile(r"(\d+)(?:\.(\d+))?")
_PADDLEX_HPI_ADAPTERS = {"paddlex.object_detection.v1"}


@dataclass(frozen=True)
class DeploymentAdapterResolution:
    framework: str
    adapter_key: str
    adapter_version: str
    model_format: str
    runtime_image_digest: str
    resolved_backend: str
    device: str
    precision: str
    input_shape: tuple[int, int, int, int]
    gpu_uuids: tuple[str, ...]
    optimization: str
    runtime_config_checksum: str

    def as_config(self) -> dict[str, Any]:
        return {
            "framework": self.framework,
            "adapter_key": self.adapter_key,
            "adapter_version": self.adapter_version,
            "model_format": self.model_format,
            "runtime_image_digest": self.runtime_image_digest,
            "resolved_backend": self.resolved_backend,
            "device": self.device,
            "precision": self.precision,
            "input_shape": list(self.input_shape),
            "gpu_uuids": list(self.gpu_uuids),
            "optimization": self.optimization,
            "runtime_config_checksum": self.runtime_config_checksum,
        }


def resolve_deployment_adapter(
    pipeline: Any,
    model: Any,
    inventory: InventorySnapshot,
    *,
    runtime_image_digest: str,
    precision: str,
    input_shape: tuple[int, int, int, int],
    gpu_uuids: tuple[str, ...],
) -> DeploymentAdapterResolution:
    digest = validate_image_digest(runtime_image_digest)
    if not inventory.supported:
        raise ValueError("node inventory is not compatible with production deployment")
    _validate_common_identity(pipeline, model)
    available = tuple(gpu.uuid for gpu in inventory.gpus if gpu.uuid)
    explicit_gpu = bool(gpu_uuids)
    if explicit_gpu and not inventory.docker.nvidia_runtime_available:
        raise ValueError("requested GPU requires the NVIDIA container runtime")
    selected = gpu_uuids or (
        available[:1] if inventory.docker.nvidia_runtime_available else ()
    )
    if any(item not in available for item in selected):
        raise ValueError("requested GPU UUID is not available in node inventory")

    if pipeline.framework == "ultralytics":
        resolved_precision = (
            "fp16" if precision == "auto" and selected else
            "fp32" if precision == "auto" else precision
        )
        values = {
            "framework": "ultralytics",
            "adapter_key": pipeline.adapter_key,
            "adapter_version": pipeline.adapter_version,
            "model_format": model.model_format,
            "runtime_image_digest": digest,
            "resolved_backend": "tensorrt" if selected else "pytorch",
            "device": "gpu:0" if selected else "cpu",
            "precision": resolved_precision,
            "input_shape": input_shape,
            "gpu_uuids": selected,
            "optimization": "auto",
        }
        return DeploymentAdapterResolution(
            **values,
            runtime_config_checksum=runtime_config_checksum(values),
        )
    if pipeline.framework != "paddlex":
        raise ValueError("framework does not support production image deployment")
    _validate_paddlex_bundle(pipeline, model)
    compatibility = _paddlex_runtime_compatibility(pipeline, model, digest)
    compatible_backends = compatibility["backends"]
    compatible_precisions = compatibility["precisions"]
    hpi_requirements = compatibility.get("hpi_requirements")
    if hpi_requirements is None:
        hpi_requirements = {}
    if not isinstance(hpi_requirements, dict):
        raise ValueError("PaddleX HPI compatibility requirements are invalid")
    cuda_min = _compatibility_minimum(hpi_requirements, "cuda_min", (11, 8))
    tensorrt_min = _compatibility_minimum(
        hpi_requirements,
        "tensorrt_min",
        (8, 6),
    )
    compute_capability_min = _compatibility_minimum(
        hpi_requirements,
        "compute_capability_min",
        (7, 0),
    )
    if precision == "int8" and (
        "int8" not in compatible_precisions
        or compatibility.get("int8_calibrated") is not True
    ):
        raise ValueError("PaddleX INT8 requires an explicit calibrated compatibility declaration")
    hpi_precision = _paddlex_hpi_precision(precision, compatible_precisions)
    supports_hpi = bool(
        selected
        and inventory.docker.nvidia_runtime_available
        and pipeline.adapter_key in _PADDLEX_HPI_ADAPTERS
        and "paddlex_hpi_tensorrt" in compatible_backends
        and hpi_precision is not None
        and _version_at_least(inventory.cuda_version, cuda_min)
        and _version_at_least(inventory.tensorrt_version, tensorrt_min)
        and _version_at_least(
            inventory.compute_capability,
            compute_capability_min,
        )
    )
    if explicit_gpu and not supports_hpi:
        raise ValueError("requested GPU is not compatible with PaddleX HPI")
    if not supports_hpi:
        if "paddle_inference" not in compatible_backends or "fp32" not in compatible_precisions:
            raise ValueError("PaddleX CPU fallback is not declared compatible")
        selected = ()
    backend = "paddlex_hpi_tensorrt" if supports_hpi else "paddle_inference"
    resolved_precision = hpi_precision if supports_hpi else "fp32"
    values = {
        "framework": "paddlex",
        "adapter_key": pipeline.adapter_key,
        "adapter_version": pipeline.adapter_version,
        "model_format": "paddle_inference_bundle",
        "runtime_image_digest": digest,
        "resolved_backend": backend,
        "device": "gpu:0" if supports_hpi else "cpu",
        "precision": resolved_precision,
        "input_shape": input_shape,
        "gpu_uuids": selected,
        "optimization": "auto",
    }
    return DeploymentAdapterResolution(
        **values,
        runtime_config_checksum=runtime_config_checksum(values),
    )


def runtime_config_checksum(values: dict[str, Any]) -> str:
    canonical = {
        key: values[key]
        for key in (
            "framework",
            "adapter_key",
            "adapter_version",
            "model_format",
            "resolved_backend",
            "device",
            "precision",
            "input_shape",
            "gpu_uuids",
            "optimization",
            "runtime_image_digest",
        )
    }
    payload = json.dumps(
        canonical,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _version_at_least(value: str | None, minimum: tuple[int, int]) -> bool:
    if not isinstance(value, str):
        return False
    match = _VERSION.search(value)
    if match is None:
        return False
    parsed = (int(match.group(1)), int(match.group(2) or 0))
    return parsed >= minimum


def _compatibility_minimum(
    requirements: dict[str, Any],
    key: str,
    default: tuple[int, int],
) -> tuple[int, int]:
    value = requirements.get(key)
    if value is None:
        return default
    if not isinstance(value, str):
        raise ValueError("PaddleX HPI compatibility requirements are invalid")
    match = _VERSION.fullmatch(value.strip())
    if match is None:
        raise ValueError("PaddleX HPI compatibility requirements are invalid")
    return (int(match.group(1)), int(match.group(2) or 0))


def _paddlex_hpi_precision(
    requested: str,
    compatible: list[Any],
) -> str | None:
    declared = {item for item in compatible if isinstance(item, str)}
    if requested == "auto":
        if "fp16" in declared:
            return "fp16"
        if "fp32" in declared:
            return "fp32"
        return None
    return requested if requested in declared else None


def _validate_common_identity(pipeline: Any, model: Any) -> None:
    if model.status != "ready":
        raise ValueError("trained model is not ready")
    if pipeline.task != "detect" or model.task != "detect":
        raise ValueError("production deployment supports detection models only")
    if (
        model.framework != pipeline.framework
        or model.adapter_key != pipeline.adapter_key
        or model.model_family != pipeline.model_family
    ):
        raise ValueError("trained model identity does not match pipeline")
    manifest = model.artifact_manifest
    if (
        not isinstance(manifest, dict)
        or manifest.get("adapter_key") != pipeline.adapter_key
        or manifest.get("adapter_version") != pipeline.adapter_version
    ):
        raise ValueError("trained model manifest identity does not match pipeline")
    if not isinstance(model.checksum, str) or not _SHA256.fullmatch(model.checksum):
        raise ValueError("trained model checksum is unavailable")


def _validate_paddlex_bundle(pipeline: Any, model: Any) -> None:
    if model.artifact_role != "best_static_inference":
        raise ValueError("PaddleX deployment requires static inference artifacts")
    manifest = model.artifact_manifest
    identity = manifest.get("model_identity")
    runtime_id = pipeline.recipe.get("model", {}).get("runtime_id")
    if not isinstance(identity, dict) or identity.get("runtime_model_id") != runtime_id:
        raise ValueError("PaddleX model runtime identity does not match pipeline")
    entries = manifest.get("role_artifacts")
    if not isinstance(entries, list) or not entries:
        raise ValueError("PaddleX inference bundle manifest is unavailable")
    suffixes: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("PaddleX inference bundle manifest is invalid")
        path = entry.get("path")
        checksum = entry.get("checksum_sha256")
        size = entry.get("size_bytes")
        if (
            not isinstance(path, str)
            or PurePosixPath(path).is_absolute()
            or ".." in PurePosixPath(path).parts
            or not isinstance(checksum, str)
            or not _SHA256.fullmatch(checksum)
            or not isinstance(size, int)
            or size < 0
        ):
            raise ValueError("PaddleX inference bundle manifest is invalid")
        suffixes.add(PurePosixPath(path).suffix.lower())
    if ".json" not in suffixes or ".pdiparams" not in suffixes:
        raise ValueError("PaddleX inference bundle is incomplete")


def _paddlex_runtime_compatibility(
    pipeline: Any,
    model: Any,
    runtime_image_digest: str,
) -> dict[str, Any]:
    compatibility = model.deployment_compatibility
    entries = (
        compatibility.get("runtime_compatibility")
        if isinstance(compatibility, dict)
        else None
    )
    if not isinstance(entries, list):
        raise ValueError("PaddleX runtime is not present in the compatibility matrix")
    for entry in entries:
        if (
            isinstance(entry, dict)
            and entry.get("runtime_image_digest") == runtime_image_digest
            and entry.get("adapter_key") == pipeline.adapter_key
            and entry.get("adapter_version") == pipeline.adapter_version
            and entry.get("model_format") == "paddle_inference_bundle"
            and isinstance(entry.get("backends"), list)
            and isinstance(entry.get("precisions"), list)
        ):
            return entry
    raise ValueError("PaddleX runtime is not present in the compatibility matrix")
