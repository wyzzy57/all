from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict


_JETSON_COMPUTE_CAPABILITIES = (
    ("orin", "8.7"),
    ("xavier", "7.2"),
    ("tx2", "6.2"),
    ("nano", "5.3"),
    ("tx1", "5.3"),
)


class DockerInventory(BaseModel):
    model_config = ConfigDict(frozen=True)

    available: bool
    version: str | None
    runtimes: tuple[str, ...]
    default_runtime: str | None
    nvidia_runtime_available: bool


class GpuInventory(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    uuid: str | None
    memory_total_mib: int | None
    memory_used_mib: int | None = None
    utilization_percent: float | None = None
    temperature_celsius: float | None = None
    power_draw_watts: float | None = None
    compute_capability: str | None


class InventorySnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    supported: bool
    unsupported_reasons: tuple[str, ...]
    platform_kind: str
    architecture: str
    os_id: str | None
    os_version: str | None
    os_pretty_name: str | None
    kernel_release: str | None
    cpu_logical_cores: int | None
    cpu_utilization_percent: float | None = None
    memory_total_kib: int | None
    memory_available_kib: int | None = None
    disk_total_bytes: int | None = None
    disk_available_bytes: int | None = None
    docker: DockerInventory
    gpus: tuple[GpuInventory, ...]
    driver_version: str | None
    driver_cuda_compatibility_version: str | None = None
    cuda_runtime_version: str | None = None
    cuda_version: str | None
    cuda_major: int | None
    cudnn_version: str | None
    tensorrt_version: str | None
    tensorrt_major: int | None
    compute_capability: str | None
    jetpack_version: str | None
    l4t_version: str | None
    jetson_model: str | None


def parse_inventory(value: Mapping[str, Any] | bytes | str) -> InventorySnapshot:
    raw = _decode_object(value)
    os_release = _mapping(raw.get("os_release"))
    uname = _mapping(raw.get("uname"))
    cpu = _mapping(raw.get("cpu"))
    memory = _mapping(raw.get("memory"))
    disk = _mapping(raw.get("disk"))
    docker_raw = _mapping(raw.get("docker"))
    nvidia = _mapping(raw.get("nvidia"))
    cuda = _mapping(raw.get("cuda"))
    jetson = _mapping(raw.get("jetson"))
    packages = _mapping(jetson.get("packages"))

    architecture = _normalize_architecture(_text(uname.get("architecture")) or "unknown")
    os_id = _text(os_release.get("id"))
    jetson_model = _text(jetson.get("model"))
    jetson_release = _text(jetson.get("nv_tegra_release"))
    jetson_compatible = _text(jetson.get("compatible"))
    jetpack_version = _package_version(packages, ("nvidia-jetpack",))
    l4t_version = _package_version(packages, ("nvidia-l4t-core",))
    kernel_release = _text(uname.get("kernel_release"))
    is_jetson = _has_jetson_evidence(
        model=jetson_model,
        compatible=jetson_compatible,
        nv_tegra_release=jetson_release,
        kernel_release=kernel_release,
        jetpack_version=jetpack_version,
        l4t_version=l4t_version,
    )

    runtimes = tuple(
        sorted(
            {
                runtime.strip()
                for runtime in docker_raw.get("runtimes", ())
                if isinstance(runtime, str) and runtime.strip()
            }
        )
    )
    docker_available = docker_raw.get("available") is True
    docker = DockerInventory(
        available=docker_available,
        version=_text(docker_raw.get("version")),
        runtimes=runtimes,
        default_runtime=_text(docker_raw.get("default_runtime")),
        nvidia_runtime_available=any(runtime == "nvidia" for runtime in runtimes),
    )

    gpus = tuple(_parse_gpus(nvidia.get("gpus")))
    if is_jetson:
        platform_kind = "jetson"
    elif architecture == "x86_64" and gpus:
        platform_kind = "x86_nvidia"
    else:
        platform_kind = "unsupported"

    driver_cuda_compatibility_version = _text(
        nvidia.get("driver_cuda_compatibility_version")
    ) or _text(nvidia.get("cuda_version"))
    cuda_runtime_version = (
        _package_version(packages, ("cuda-cudart",))
        or _text(cuda.get("runtime_version"))
        or _text(cuda.get("version_file"))
        or _text(cuda.get("nvcc_version"))
        or _package_version(packages, ("cuda-toolkit",))
    )
    tensorrt_version = _package_version(packages, ("libnvinfer", "tensorrt"))
    cudnn_version = _package_version(packages, ("libcudnn", "cudnn"))
    compute_capabilities = {
        gpu.compute_capability for gpu in gpus if gpu.compute_capability is not None
    }
    if not compute_capabilities and is_jetson:
        inferred = _jetson_compute_capability(jetson_model)
        if inferred is not None:
            compute_capabilities.add(inferred)
    compute_capability = (
        next(iter(compute_capabilities)) if len(compute_capabilities) == 1 else None
    )

    reasons: list[str] = []
    if (os_id or "").lower() != "ubuntu":
        reasons.append("Ubuntu is required")
    if platform_kind == "unsupported":
        reasons.append("Only Ubuntu Jetson and x86 NVIDIA hosts are supported")
    elif platform_kind == "jetson" and architecture != "aarch64":
        reasons.append("Jetson architecture must be aarch64")
    elif platform_kind == "x86_nvidia" and architecture != "x86_64":
        reasons.append("x86 NVIDIA architecture must be x86_64")
    if not docker.available:
        reasons.append("Docker Engine is unavailable")
    if not docker.nvidia_runtime_available:
        reasons.append("NVIDIA Container Runtime is unavailable")
    if not gpus and not is_jetson:
        reasons.append("NVIDIA GPU inventory is unavailable")
    if platform_kind == "jetson" and _version_major(cuda_runtime_version) is None:
        reasons.append("CUDA runtime/toolkit is unavailable")
    if platform_kind == "jetson" and _version_major(tensorrt_version) is None:
        reasons.append("TensorRT version is unavailable")
    if len(compute_capabilities) > 1:
        reasons.append("GPU compute capabilities are heterogeneous")
    elif compute_capability is None:
        reasons.append("GPU compute capability is unavailable")

    return InventorySnapshot(
        supported=not reasons,
        unsupported_reasons=tuple(reasons),
        platform_kind=platform_kind,
        architecture=architecture,
        os_id=os_id,
        os_version=_text(os_release.get("version_id")),
        os_pretty_name=_text(os_release.get("pretty_name")),
        kernel_release=kernel_release,
        cpu_logical_cores=_integer(cpu.get("logical_cores")),
        cpu_utilization_percent=_number(cpu.get("utilization_percent")),
        memory_total_kib=_integer(memory.get("total_kib")),
        memory_available_kib=_integer(memory.get("available_kib")),
        disk_total_bytes=_integer(disk.get("total_bytes")),
        disk_available_bytes=_integer(disk.get("available_bytes")),
        docker=docker,
        gpus=gpus,
        driver_version=_text(nvidia.get("driver_version")),
        driver_cuda_compatibility_version=driver_cuda_compatibility_version,
        cuda_runtime_version=cuda_runtime_version,
        cuda_version=cuda_runtime_version,
        cuda_major=_version_major(cuda_runtime_version),
        cudnn_version=cudnn_version,
        tensorrt_version=tensorrt_version,
        tensorrt_major=_version_major(tensorrt_version),
        compute_capability=compute_capability,
        jetpack_version=jetpack_version,
        l4t_version=l4t_version,
        jetson_model=jetson_model,
    )


def compatibility_key(snapshot: InventorySnapshot) -> str:
    return ":".join(
        (
            snapshot.platform_kind,
            snapshot.architecture,
            _key_part(_compatibility_cuda_major(snapshot)),
            _key_part(_compatibility_tensorrt_major(snapshot)),
            _key_part(snapshot.compute_capability),
        )
    )


def compatibility_policy(snapshot: InventorySnapshot) -> dict[str, str | int]:
    return {
        "compatibility_key": compatibility_key(snapshot),
        "platform_kind": snapshot.platform_kind,
        "architecture": snapshot.architecture,
        "cuda_major": _compatibility_cuda_major(snapshot) or 0,
        "tensorrt_major": _compatibility_tensorrt_major(snapshot) or 0,
        "compute_capability": snapshot.compute_capability or "unknown",
    }


def _compatibility_cuda_major(snapshot: InventorySnapshot) -> int | None:
    if snapshot.platform_kind == "x86_nvidia":
        return _version_major(snapshot.driver_cuda_compatibility_version)
    return snapshot.cuda_major


def _compatibility_tensorrt_major(snapshot: InventorySnapshot) -> int | None:
    if snapshot.platform_kind == "x86_nvidia":
        return None
    return snapshot.tensorrt_major


def pool_accepts_inventory(
    policy: Mapping[str, Any],
    snapshot: InventorySnapshot,
) -> bool:
    return dict(policy) == compatibility_policy(snapshot)


def _decode_object(value: Mapping[str, Any] | bytes | str) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        decoded = json.loads(value)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        raise ValueError("Inventory probe output is invalid") from None
    if not isinstance(decoded, dict):
        raise ValueError("Inventory probe output is invalid")
    return decoded


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _integer(value: Any) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return None
    return value


def _number(value: Any) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        return None
    return float(value)


def _normalize_architecture(value: str) -> str:
    return {
        "amd64": "x86_64",
        "arm64": "aarch64",
    }.get(value.lower(), value.lower())


def _parse_gpus(value: Any) -> list[GpuInventory]:
    if not isinstance(value, list):
        return []
    parsed: list[GpuInventory] = []
    for item in value:
        gpu = _mapping(item)
        name = _text(gpu.get("name"))
        if name is None:
            continue
        parsed.append(
            GpuInventory(
                name=name,
                uuid=_text(gpu.get("uuid")),
                memory_total_mib=_integer(gpu.get("memory_total_mib")),
                memory_used_mib=_integer(gpu.get("memory_used_mib")),
                utilization_percent=_number(gpu.get("utilization_percent")),
                temperature_celsius=_number(gpu.get("temperature_celsius")),
                power_draw_watts=_number(gpu.get("power_draw_watts")),
                compute_capability=_normalize_compute_capability(
                    _text(gpu.get("compute_capability"))
                ),
            )
        )
    return parsed


def _normalize_compute_capability(value: str | None) -> str | None:
    if value is None:
        return None
    match = re.fullmatch(r"(\d+)\.(\d+)", value)
    if match is None:
        return None
    return f"{int(match.group(1))}.{int(match.group(2))}"


def _version_major(value: str | None) -> int | None:
    if value is None:
        return None
    match = re.search(r"(?:^|[^0-9])(\d+)(?:\.|$)", value)
    return int(match.group(1)) if match is not None else None


def _package_version(packages: Mapping[str, Any], prefixes: tuple[str, ...]) -> str | None:
    for name in sorted(packages):
        if any(name.startswith(prefix) for prefix in prefixes):
            version = _text(packages[name])
            if version is not None:
                return version
    return None


def _jetson_compute_capability(model: str | None) -> str | None:
    normalized = (model or "").lower()
    for marker, capability in _JETSON_COMPUTE_CAPABILITIES:
        if marker in normalized:
            return capability
    return None


def _has_jetson_evidence(
    *,
    model: str | None,
    compatible: str | None,
    nv_tegra_release: str | None,
    kernel_release: str | None,
    jetpack_version: str | None,
    l4t_version: str | None,
) -> bool:
    return any(
        (
            nv_tegra_release,
            jetpack_version,
            l4t_version,
            "jetson" in (model or "").lower(),
            "tegra" in (compatible or "").lower(),
            "tegra" in (kernel_release or "").lower(),
        )
    )


def _key_part(value: object | None) -> str:
    return "unknown" if value is None else str(value)
