from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
import re
import signal
import subprocess
import time
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
_MANIFEST_NAME = "artifact-manifest.json"
_MANIFEST_TEMP_NAME = ".artifact-manifest.json.tmp"
_HASH_CHUNK_SIZE = 1024 * 1024
logger = logging.getLogger(__name__)


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
    manifest = ArtifactManifestRefresher(
        output_dir,
        task_id=task_id,
        adapter_key=adapter_key,
        adapter_version=adapter_version,
    ).refresh(force=True, strict=True)
    assert manifest is not None
    return manifest


class ArtifactManifestRefresher:
    def __init__(
        self,
        output_dir: Path,
        *,
        task_id: str,
        adapter_key: str,
        adapter_version: str,
        minimum_interval_seconds: float = 60.0,
    ) -> None:
        if minimum_interval_seconds < 0:
            raise ValueError("manifest refresh interval cannot be negative")
        self.output_dir = output_dir.resolve()
        self.task_id = task_id
        self.adapter_key = adapter_key
        self.adapter_version = adapter_version
        self.minimum_interval_seconds = minimum_interval_seconds
        self._last_attempt = float("-inf")
        self._manifest: ArtifactManifest | None = None
        self._cache: dict[str, tuple[tuple[int, int, int, int], ArtifactEntry]] = {}

    def refresh(
        self,
        *,
        force: bool = False,
        strict: bool = False,
    ) -> ArtifactManifest | None:
        now = time.monotonic()
        if not force and now - self._last_attempt < self.minimum_interval_seconds:
            return self._manifest
        self._last_attempt = now
        try:
            manifest, cache = self._build_manifest(rehash_all=strict)
            self._write_manifest(manifest)
        except Exception:
            if strict:
                raise
            logger.exception("periodic artifact manifest refresh failed")
            return None
        self._manifest = manifest
        self._cache = cache
        return manifest

    def _build_manifest(
        self,
        *,
        rehash_all: bool,
    ) -> tuple[
        ArtifactManifest,
        dict[str, tuple[tuple[int, int, int, int], ArtifactEntry]],
    ]:
        entries: list[ArtifactEntry] = []
        cache: dict[str, tuple[tuple[int, int, int, int], ArtifactEntry]] = {}
        for path in sorted(self.output_dir.rglob("*")):
            if not path.is_file() or path.name in {
                _MANIFEST_NAME,
                _MANIFEST_TEMP_NAME,
            }:
                continue
            relative = path.relative_to(self.output_dir).as_posix()
            before = _file_fingerprint(path)
            cached = self._cache.get(relative)
            if not rehash_all and cached is not None and cached[0] == before:
                entry = cached[1]
            else:
                digest = _stream_file_digest(path)
                try:
                    after = _file_fingerprint(path)
                except FileNotFoundError as exc:
                    raise RuntimeError(
                        f"artifact changed while hashing: {relative}"
                    ) from exc
                if before != after:
                    raise RuntimeError(f"artifact changed while hashing: {relative}")
                entry = ArtifactEntry(
                    path=relative,
                    size_bytes=after[2],
                    checksum_sha256=digest,
                    artifact_type=_artifact_type(path),
                )
                before = after
            entries.append(entry)
            cache[relative] = (before, entry)
        unsigned = ArtifactManifest(
            task_id=self.task_id,
            adapter_key=self.adapter_key,
            adapter_version=self.adapter_version,
            artifacts=tuple(entries),
        )
        manifest = unsigned.model_copy(
            update={"checksum_sha256": unsigned.canonical_checksum_sha256()}
        )
        return manifest, cache

    def _write_manifest(self, manifest: ArtifactManifest) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.output_dir / _MANIFEST_TEMP_NAME
        temporary.write_text(
            json.dumps(
                manifest.model_dump(mode="json"),
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        temporary.replace(self.output_dir / _MANIFEST_NAME)


def _file_fingerprint(path: Path) -> tuple[int, int, int, int]:
    stat = path.stat()
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)


def _stream_file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(_HASH_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


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
                    try:
                        on_poll()
                    except Exception:
                        logger.exception("periodic artifact manifest refresh failed")
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
