from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import signal
import subprocess
from collections.abc import Mapping, Sequence
from typing import Any, Callable

from visiox_training.contracts import ArtifactEntry, ArtifactManifest, LaunchSpec


FIXED_TRAINING_ENTRYPOINT = "/usr/local/bin/visiox-train"
RUNTIME_INPUTS_ENV = "VISIOX_RUNTIME_INPUTS_JSON"
MANAGED_ARTIFACT_PATHS = {
    "dataset": "/workspace/dataset",
    "model": "/workspace/model/base.pt",
    "checkpoint": "/workspace/checkpoint/last.pt",
}
_HOST_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|//)")


def load_fixed_launch_spec(
    path: Path,
    *,
    adapter_key: str,
    adapter_version: str,
) -> LaunchSpec:
    spec = LaunchSpec.model_validate_json(path.read_text(encoding="utf-8"))
    if spec.adapter_key != adapter_key or spec.adapter_version != adapter_version:
        raise ValueError("launch spec adapter does not match the runtime image")
    if tuple(spec.argv) != (FIXED_TRAINING_ENTRYPOINT,):
        raise ValueError("runtime image only accepts the fixed training entrypoint")
    return spec


def build_runtime_inputs(
    *,
    parameters: Mapping[str, Any],
    model: Mapping[str, Any],
    dataset: Mapping[str, Any],
    artifact_roles: Sequence[str],
) -> dict[str, Any]:
    payload = {
        "parameters": dict(parameters),
        "model": dict(model),
        "dataset": dict(dataset),
        "artifacts": [
            {"role": role, "path": MANAGED_ARTIFACT_PATHS[role]}
            for role in artifact_roles
            if role in MANAGED_ARTIFACT_PATHS
        ],
    }
    if len(payload["artifacts"]) != len(artifact_roles):
        raise ValueError("runtime input contains an unsupported artifact role")
    _validate_runtime_inputs(payload)
    return payload


def encode_runtime_inputs(payload: Mapping[str, Any]) -> str:
    normalized = _validate_runtime_inputs(dict(payload))
    return json.dumps(
        normalized,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def load_runtime_inputs(spec: LaunchSpec) -> dict[str, Any]:
    raw = require_string_env(spec, RUNTIME_INPUTS_ENV)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("runtime inputs are not valid JSON") from exc
    return _validate_runtime_inputs(payload)


def _validate_runtime_inputs(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {
        "parameters",
        "model",
        "dataset",
        "artifacts",
    }:
        raise ValueError("runtime inputs must contain the managed fields")
    for field in ("parameters", "model", "dataset"):
        value = payload[field]
        if not isinstance(value, dict):
            raise ValueError(f"runtime input {field} must be an object")
        _validate_snapshot_value(value)
    artifacts = payload["artifacts"]
    if not isinstance(artifacts, list) or len(artifacts) > len(MANAGED_ARTIFACT_PATHS):
        raise ValueError("runtime input artifacts are invalid")
    seen: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) != {"role", "path"}:
            raise ValueError("runtime artifact description is invalid")
        role = artifact.get("role")
        if (
            not isinstance(role, str)
            or role in seen
            or artifact.get("path") != MANAGED_ARTIFACT_PATHS.get(role)
        ):
            raise ValueError("runtime artifact path is not managed")
        seen.add(role)
    json.dumps(payload, ensure_ascii=True, allow_nan=False)
    return payload


def _validate_snapshot_value(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if (
                not isinstance(key, str)
                or not key
                or any(char in key for char in "\x00\r\n")
            ):
                raise ValueError("runtime input key is invalid")
            _validate_snapshot_value(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _validate_snapshot_value(item)
        return
    if isinstance(value, str):
        if any(char in value for char in "\x00\r\n"):
            raise ValueError("runtime input contains control characters")
        if (
            value.startswith("/")
            or value.casefold().startswith("file:")
            or _HOST_PATH.match(value)
        ):
            raise ValueError("runtime input contains a host path")
        return
    if value is None or isinstance(value, bool | int | float):
        return
    raise ValueError("runtime input contains a non-JSON value")


def write_artifact_manifest(
    output_dir: Path,
    *,
    task_id: str,
    adapter_key: str,
    adapter_version: str,
) -> ArtifactManifest:
    output_dir = output_dir.resolve()
    entries: list[ArtifactEntry] = []
    for path in sorted(output_dir.rglob("*")):
        if not path.is_file() or path.name == "artifact-manifest.json":
            continue
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        entries.append(
            ArtifactEntry(
                path=path.relative_to(output_dir).as_posix(),
                size_bytes=path.stat().st_size,
                checksum_sha256=digest.hexdigest(),
                artifact_type=_artifact_type(path),
            )
        )
    unsigned = ArtifactManifest(
        task_id=task_id,
        adapter_key=adapter_key,
        adapter_version=adapter_version,
        artifacts=tuple(entries),
    )
    manifest = unsigned.model_copy(
        update={"checksum_sha256": unsigned.canonical_checksum_sha256()}
    )
    (output_dir / "artifact-manifest.json").write_text(
        json.dumps(
            manifest.model_dump(mode="json"),
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return manifest


def _artifact_type(path: Path) -> str:
    if path.suffix in {".pt", ".safetensors", ".pdparams"}:
        return "model_weight"
    if path.suffix in {".png", ".jpg", ".jpeg"}:
        return "visualization"
    if path.suffix in {".csv", ".json", ".jsonl"}:
        return "metrics"
    if "tfevents" in path.name:
        return "tensorboard_event"
    return "training_output"


def require_string_env(spec: LaunchSpec, key: str) -> str:
    value: Any = spec.env.get(key)
    if (
        not isinstance(value, str)
        or not value
        or any(char in value for char in "\x00\r\n")
    ):
        raise ValueError(f"launch spec environment value {key} is invalid")
    return value


def run_worker_command(
    command: tuple[str, ...],
    *,
    environment: dict[str, str],
    on_poll: Callable[[], None] | None = None,
    poll_interval_seconds: float = 5.0,
) -> int:
    process = subprocess.Popen(command, env=environment)

    def forward(signum: int, _frame: object) -> None:
        if process.poll() is None:
            process.send_signal(signum)

    previous = {
        signum: signal.signal(signum, forward)
        for signum in (signal.SIGTERM, signal.SIGINT)
    }
    try:
        while True:
            try:
                return int(process.wait(timeout=poll_interval_seconds))
            except subprocess.TimeoutExpired:
                if on_poll is not None:
                    on_poll()
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
