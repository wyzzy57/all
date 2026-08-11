#!/usr/bin/env bash
set -euo pipefail

exec python3 - "$@" <<'PY'
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tarfile
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


IMAGE_DIGEST = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,430}@sha256:[a-f0-9]{64}\Z")
SHA256 = re.compile(r"[a-f0-9]{64}\Z")
CONTAINER_ID = re.compile(r"[a-f0-9]{12,64}\Z")
GPU_UUID = re.compile(r"GPU-[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z")
PLATFORM_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}\Z")
LABEL_KEYS = {
    "com.visiox.managed",
    "com.visiox.deployment-instance-id",
    "com.visiox.deployment-service-id",
    "com.visiox.node-id",
    "com.visiox.restart-policy",
    "com.visiox.health-path",
    "com.visiox.warmup-path",
}
IDENTITY_LABEL_KEYS = {
    "com.visiox.framework",
    "com.visiox.adapter-key",
    "com.visiox.adapter-version",
    "com.visiox.model-format",
    "com.visiox.resolved-backend",
    "com.visiox.runtime-digest",
}
RUNTIME_CHECKSUM_LABEL_KEY = "com.visiox.runtime-config-checksum"
TUPLE_KEYS = {
    "container_id",
    "image_digest",
    "model_checksum",
    "engine",
    "engine_digest",
    "port",
}
IDENTITY_TUPLE_KEYS = {
    "framework",
    "adapter_key",
    "adapter_version",
    "model_format",
    "resolved_backend",
    "runtime_image_digest",
}
RUNTIME_CHECKSUM_TUPLE_KEY = "runtime_config_checksum"
MODEL_KEYS = {"download_url", "checksum", "source_format"}
EXTENDED_MODEL_KEYS = MODEL_KEYS | {"archive_format"}
RUNTIME_KEYS = {
    "format",
    "precision",
    "input_shape",
    "gpu_uuids",
    "engine_cache_key",
    "calibration_download_url",
    "port",
    "shm_size",
    "restart_policy",
    "model_mount_read_only",
    "privileged",
}
EXTENDED_RUNTIME_KEYS = RUNTIME_KEYS | {
    "framework",
    "adapter_key",
    "adapter_version",
    "model_format",
    "resolved_backend",
    "runtime_image_digest",
    "optimization",
    "device",
}
RUNTIME_CHECKSUM_KEY = "runtime_config_checksum"
MAX_BUNDLE_BYTES = 8 * 1024 * 1024 * 1024
MAX_BUNDLE_MEMBER_BYTES = 4 * 1024 * 1024 * 1024
MAX_BUNDLE_MEMBERS = 64
WARMUP_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _invalid():
    raise ValueError("invalid deployment request")


def _keys(value, expected):
    if not isinstance(value, dict) or set(value) != expected:
        _invalid()
    return value


def _valid_url(value):
    if not isinstance(value, str) or len(value) > 4096:
        return False
    parsed = urlsplit(value)
    return (
        parsed.scheme in {"http", "https"}
        and parsed.hostname is not None
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment
    )


def _validate_labels(value):
    if not isinstance(value, dict) or frozenset(value) not in {
        frozenset(LABEL_KEYS),
        frozenset(LABEL_KEYS | IDENTITY_LABEL_KEYS),
        frozenset(LABEL_KEYS | IDENTITY_LABEL_KEYS | {RUNTIME_CHECKSUM_LABEL_KEY}),
    }:
        _invalid()
    labels = value
    dynamic = (
        labels["com.visiox.deployment-instance-id"],
        labels["com.visiox.deployment-service-id"],
        labels["com.visiox.node-id"],
    )
    if any(not isinstance(item, str) or not PLATFORM_ID.fullmatch(item) for item in dynamic):
        _invalid()
    if (
        labels["com.visiox.managed"] != "true"
        or labels["com.visiox.restart-policy"] != "unless-stopped"
        or labels["com.visiox.health-path"] != "/health"
        or labels["com.visiox.warmup-path"] != "/predict/image"
    ):
        _invalid()
    if IDENTITY_LABEL_KEYS.issubset(labels):
        if (
            labels["com.visiox.framework"] not in {"ultralytics", "paddlex"}
            or not all(
                isinstance(labels[key], str) and labels[key]
                for key in IDENTITY_LABEL_KEYS - {"com.visiox.runtime-digest"}
            )
            or not isinstance(labels["com.visiox.runtime-digest"], str)
            or not IMAGE_DIGEST.fullmatch(labels["com.visiox.runtime-digest"])
        ):
            _invalid()
    if RUNTIME_CHECKSUM_LABEL_KEY in labels and not SHA256.fullmatch(
        labels[RUNTIME_CHECKSUM_LABEL_KEY]
    ):
        _invalid()
    return labels


def _validate_tuple(value):
    if not isinstance(value, dict) or frozenset(value) not in {
        frozenset(TUPLE_KEYS),
        frozenset(TUPLE_KEYS | IDENTITY_TUPLE_KEYS),
        frozenset(TUPLE_KEYS | IDENTITY_TUPLE_KEYS | {RUNTIME_CHECKSUM_TUPLE_KEY}),
    }:
        _invalid()
    target = value
    if (
        not isinstance(target["container_id"], str)
        or not CONTAINER_ID.fullmatch(target["container_id"])
        or not isinstance(target["image_digest"], str)
        or not IMAGE_DIGEST.fullmatch(target["image_digest"])
        or not isinstance(target["model_checksum"], str)
        or not SHA256.fullmatch(target["model_checksum"])
        or target["engine"] not in {"pt", "onnx", "engine", "paddle_inference_bundle"}
        or not isinstance(target["engine_digest"], str)
        or not SHA256.fullmatch(target["engine_digest"])
        or not isinstance(target["port"], int)
        or isinstance(target["port"], bool)
        or not 1024 <= target["port"] <= 65535
    ):
        _invalid()
    if IDENTITY_TUPLE_KEYS.issubset(target):
        if (
            target["framework"] not in {"ultralytics", "paddlex"}
            or target["runtime_image_digest"] != target["image_digest"]
            or target["model_format"] not in {"pt", "onnx", "engine", "paddle_inference_bundle"}
            or not all(
                isinstance(target[key], str) and target[key]
                for key in IDENTITY_TUPLE_KEYS
            )
        ):
            _invalid()
    if RUNTIME_CHECKSUM_TUPLE_KEY in target and not SHA256.fullmatch(
        target[RUNTIME_CHECKSUM_TUPLE_KEY]
    ):
        _invalid()
    return target


def _validate_request(value):
    if not isinstance(value, dict) or value.get("action") not in {"deploy", "rollback"}:
        _invalid()
    if value["action"] == "rollback":
        request = _keys(value, {"action", "current_container_id", "labels", "target"})
        if (
            not isinstance(request["current_container_id"], str)
            or not CONTAINER_ID.fullmatch(request["current_container_id"])
        ):
            _invalid()
        _validate_labels(request["labels"])
        _validate_tuple(request["target"])
        return request

    request = _keys(
        value,
        {
            "action",
            "image_digest",
            "labels",
            "model",
            "runtime",
            "previous_container_id",
        },
    )
    if not isinstance(request["image_digest"], str) or not IMAGE_DIGEST.fullmatch(request["image_digest"]):
        _invalid()
    _validate_labels(request["labels"])
    model = request["model"]
    if not isinstance(model, dict) or frozenset(model) not in {
        frozenset(MODEL_KEYS),
        frozenset(EXTENDED_MODEL_KEYS),
    }:
        _invalid()
    if (
        not _valid_url(model["download_url"])
        or not isinstance(model["checksum"], str)
        or not SHA256.fullmatch(model["checksum"])
        or model["source_format"] not in {"pt", "onnx", "engine", "paddle_inference_bundle"}
    ):
        _invalid()
    if "archive_format" in model and model["archive_format"] not in {None, "tar"}:
        _invalid()
    runtime = request["runtime"]
    if not isinstance(runtime, dict) or frozenset(runtime) not in {
        frozenset(RUNTIME_KEYS),
        frozenset(EXTENDED_RUNTIME_KEYS),
        frozenset(EXTENDED_RUNTIME_KEYS | {RUNTIME_CHECKSUM_KEY}),
    }:
        _invalid()
    shape = runtime["input_shape"]
    gpus = runtime["gpu_uuids"]
    if (
        runtime["format"] not in {"pt", "onnx", "engine", "paddle_inference_bundle"}
        or runtime["precision"] not in {"fp32", "fp16", "int8"}
        or not isinstance(shape, list)
        or len(shape) != 4
        or shape[0:2] != [1, 3]
        or any(not isinstance(item, int) or isinstance(item, bool) for item in shape)
        or not 32 <= shape[2] <= 4096
        or not 32 <= shape[3] <= 4096
        or shape[2] % 32
        or shape[3] % 32
        or not isinstance(gpus, list)
        or len(set(gpus)) != len(gpus)
        or any(not isinstance(item, str) or not GPU_UUID.fullmatch(item) for item in gpus)
        or not isinstance(runtime["port"], int)
        or isinstance(runtime["port"], bool)
        or not 1024 <= runtime["port"] <= 65535
        or runtime["shm_size"] != "1g"
        or runtime["restart_policy"] != "unless-stopped"
        or runtime["model_mount_read_only"] is not True
        or runtime["privileged"] is not False
    ):
        _invalid()
    if EXTENDED_RUNTIME_KEYS.issubset(runtime):
        if (
            runtime["framework"] not in {"ultralytics", "paddlex"}
            or runtime["runtime_image_digest"] != request["image_digest"]
            or runtime["model_format"] != model["source_format"]
            or runtime["optimization"] not in {"auto", "paddle_inference", "paddlex_hpi_tensorrt"}
            or not isinstance(runtime["device"], str)
            or not runtime["device"]
        ):
            _invalid()
        if runtime["framework"] != "paddlex" and not gpus:
            _invalid()
        if runtime["framework"] == "paddlex" and (
            model.get("archive_format") != "tar"
            or runtime["format"] != "paddle_inference_bundle"
            or runtime["resolved_backend"] not in {"paddle_inference", "paddlex_hpi_tensorrt"}
        ):
            _invalid()
    if RUNTIME_CHECKSUM_KEY in runtime and not SHA256.fullmatch(
        runtime[RUNTIME_CHECKSUM_KEY]
    ):
        _invalid()
    cache_key = runtime["engine_cache_key"]
    if runtime["format"] == "engine":
        if not isinstance(cache_key, str) or not SHA256.fullmatch(cache_key):
            _invalid()
    elif cache_key is not None:
        _invalid()
    calibration_url = runtime["calibration_download_url"]
    if runtime["precision"] == "int8":
        if runtime["format"] != "engine" or not _valid_url(calibration_url):
            _invalid()
    elif calibration_url is not None:
        _invalid()
    previous = request["previous_container_id"]
    if previous is not None and (
        not isinstance(previous, str) or not CONTAINER_ID.fullmatch(previous)
    ):
        _invalid()
    return request


def _container_args(
    request,
    *,
    artifact_path,
    config_path,
    engine_digest,
    name,
    host_port,
    runtime_user,
):
    runtime = request["runtime"]
    bind_host = "127.0.0.1" if host_port is None else "0.0.0.0"
    labels = dict(request["labels"])
    labels.update(
        {
            "com.visiox.image-digest": request["image_digest"],
            "com.visiox.model-checksum": request["model"]["checksum"],
            "com.visiox.engine": runtime["format"],
            "com.visiox.engine-digest": engine_digest,
            "com.visiox.port": str(runtime["port"]),
        }
    )
    args = [
        "docker",
        "run",
        "-d",
        "--name",
        name,
        "--user",
        runtime_user,
        "--shm-size",
        runtime["shm_size"],
        "--restart",
        runtime["restart_policy"],
    ]
    if runtime["gpu_uuids"]:
        args.extend(["--gpus", "device=" + ",".join(runtime["gpu_uuids"])])
    for key in sorted(labels):
        args.extend(["--label", f"{key}={labels[key]}"])
    model_destination = (
        "/models/model"
        if runtime.get("framework") == "paddlex"
        else f"/models/model.{runtime['format']}"
    )
    args.extend(
        [
            "--mount",
            f"type=bind,src={artifact_path},dst={model_destination},readonly",
            "--mount",
            f"type=bind,src={config_path},dst=/app/config.json,readonly",
            "-e",
            "VISIOX_INFERENCE_CONFIG=/app/config.json",
        ]
    )
    if runtime.get("framework", "ultralytics") == "ultralytics":
        args.extend(["-e", "YOLO_CONFIG_DIR=/tmp/visiox-ultralytics"])
    elif runtime.get("framework") == "paddlex":
        args.extend(["-e", "HOME=/tmp/visiox-paddlex"])
    args.extend(
        [
            "-p",
            f"{bind_host}:{'' if host_port is None else host_port}:8080",
            request["image_digest"],
        ]
    )
    return args


def _promote(operations, previous_container_id):
    candidate = operations.start_candidate()
    try:
        operations.warmup(candidate)
    except Exception:
        operations.stop(candidate)
        operations.remove(candidate)
        raise
    if previous_container_id is not None:
        operations.stop(previous_container_id)
    operations.stop(candidate)
    operations.remove(candidate)
    active = None
    try:
        active = operations.start_active()
        operations.warmup(active)
    except Exception:
        if active is not None:
            operations.stop(active)
        if previous_container_id is not None:
            operations.start_existing(previous_container_id)
            operations.warmup(previous_container_id)
        raise
    return operations.result(active)


def _rollback(operations, current_container_id, target_container_id):
    operations.stop(current_container_id)
    try:
        operations.start_existing(target_container_id)
        operations.warmup(target_container_id)
    except Exception:
        operations.stop(target_container_id)
        operations.start_existing(current_container_id)
        operations.warmup(current_container_id)
        raise
    return operations.result(target_container_id)


def _run(args, *, timeout=7200):
    return subprocess.run(
        args,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url, destination, expected_checksum=None, max_bytes=None):
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if destination.exists() and (
        expected_checksum is None or _sha256(destination) == expected_checksum
    ):
        return
    temporary = destination.with_name(destination.name + "." + secrets.token_hex(8) + ".tmp")
    try:
        with urlopen(Request(url, method="GET"), timeout=60) as response, temporary.open("xb") as output:
            length = response.headers.get("Content-Length")
            if max_bytes is not None and length is not None and int(length) > max_bytes:
                raise ValueError("downloaded object exceeds size limit")
            downloaded = 0
            while chunk := response.read(1024 * 1024):
                downloaded += len(chunk)
                if max_bytes is not None and downloaded > max_bytes:
                    raise ValueError("downloaded object exceeds size limit")
                output.write(chunk)
        os.chmod(temporary, 0o400)
        if expected_checksum is not None and _sha256(temporary) != expected_checksum:
            raise ValueError("downloaded object checksum did not match")
        temporary.replace(destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def _extract_bundle(archive_path, destination):
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = destination.with_name(destination.name + "." + secrets.token_hex(8) + ".tmp")
    temporary.mkdir(mode=0o700)
    try:
        with tarfile.open(archive_path, mode="r:") as archive:
            members = archive.getmembers()
            if not members or len(members) > MAX_BUNDLE_MEMBERS:
                raise ValueError("bundle archive is invalid")
            names = set()
            for member in members:
                path = Path(member.name)
                if (
                    member.name in names
                    or path.is_absolute()
                    or ".." in path.parts
                    or len(path.parts) != 1
                    or not member.isfile()
                    or member.size < 0
                    or member.size > MAX_BUNDLE_MEMBER_BYTES
                ):
                    raise ValueError("bundle archive is invalid")
                names.add(member.name)
            if sum(member.size for member in members) > MAX_BUNDLE_BYTES:
                raise ValueError("bundle archive is invalid")
            if not any(name.endswith(".json") for name in names) or not any(
                name.endswith(".pdiparams") for name in names
            ):
                raise ValueError("bundle archive is invalid")
            for member in members:
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError("bundle archive is invalid")
                target = temporary / member.name
                written = 0
                with target.open("xb") as output:
                    while chunk := source.read(1024 * 1024):
                        written += len(chunk)
                        if written > member.size or written > MAX_BUNDLE_MEMBER_BYTES:
                            raise ValueError("bundle archive is invalid")
                        output.write(chunk)
                if written != member.size:
                    raise ValueError("bundle archive is invalid")
                os.chmod(target, 0o400)
        if destination.exists():
            for existing in destination.iterdir():
                existing.unlink()
            destination.rmdir()
        temporary.replace(destination)
        os.chmod(destination, 0o500)
    except Exception:
        if temporary.exists():
            for child in temporary.iterdir():
                if child.is_file():
                    child.unlink()
            temporary.rmdir()
        raise


def _write_config(path, request, artifact_path):
    runtime = request["runtime"]
    if runtime.get("framework") == "paddlex":
        config = {
            "production": True,
            "task": "detect",
            "model_dir": "/models/model",
            "model_format": "paddle_inference_bundle",
            "device": runtime["device"],
            "backend": runtime["resolved_backend"],
            "precision": runtime["precision"],
            "input_size": runtime["input_shape"][2:],
            "optimization": runtime["optimization"],
        }
    else:
        config = {
            "production": True,
            "task": "detect",
            "model_path": f"/models/{artifact_path.name}",
            "model_format": runtime["format"],
            "device": "cuda:0",
            "input": {"type": "http", "shape": runtime["input_shape"]},
        }
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    with temporary.open("x", encoding="ascii") as output:
        json.dump(config, output, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    os.chmod(temporary, 0o400)
    temporary.replace(path)


class Operations:
    def __init__(self, request):
        self.request = request
        self.runtime = request.get("runtime")
        self.runtime_user = f"{os.getuid()}:{os.getgid()}"
        self.artifact_path = None
        self.config_path = None
        self.engine_digest = None

    def pull(self):
        _run(["docker", "pull", self.request["image_digest"]], timeout=900)

    def verify_previous(self):
        previous = self.request["previous_container_id"]
        listed = _run(
            [
                "docker",
                "ps",
                "--filter",
                "label=com.visiox.deployment-instance-id="
                + self.request["labels"]["com.visiox.deployment-instance-id"],
                "--format",
                "{{.ID}}",
            ],
            timeout=30,
        ).stdout.splitlines()
        listed = [item.strip() for item in listed if item.strip()]
        if previous is None and listed:
            raise ValueError("untracked active deployment exists")
        if previous is not None and previous not in listed:
            raise ValueError("previous deployment is not running")

    def prepare(self):
        model = self.request["model"]
        runtime = self.runtime
        model_root = Path.home() / ".local" / "share" / "visiox" / "models" / model["checksum"]
        source_name = (
            "paddle-inference-bundle.tar"
            if model["source_format"] == "paddle_inference_bundle"
            else "model." + model["source_format"]
        )
        source_path = model_root / source_name
        _download(
            model["download_url"],
            source_path,
            model["checksum"],
            MAX_BUNDLE_BYTES if model["source_format"] == "paddle_inference_bundle" else None,
        )
        artifact_path = source_path
        if model["source_format"] == "paddle_inference_bundle":
            artifact_path = model_root / "paddle-inference-bundle"
            marker = artifact_path / ".visiox-checksum"
            if not artifact_path.is_dir() or not marker.is_file() or marker.read_text(
                encoding="ascii"
            ) != model["checksum"]:
                _extract_bundle(source_path, artifact_path)
                os.chmod(artifact_path, 0o700)
                marker.write_text(model["checksum"], encoding="ascii")
                os.chmod(marker, 0o400)
                os.chmod(artifact_path, 0o500)
        elif runtime["format"] != model["source_format"]:
            if runtime["format"] == "engine":
                output_root = Path.home() / ".cache" / "visiox" / "engines" / runtime["engine_cache_key"]
            else:
                shape_key = "x".join(str(item) for item in runtime["input_shape"])
                output_root = model_root / ("onnx-" + shape_key)
            output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            artifact_path = output_root / ("model." + runtime["format"])
            if not artifact_path.exists():
                calibration_path = None
                if runtime["calibration_download_url"] is not None:
                    calibration_path = output_root / "calibration.dataset"
                    _download(runtime["calibration_download_url"], calibration_path)
                args = [
                    "docker",
                    "run",
                    "--rm",
                    "--user",
                    self.runtime_user,
                    "--gpus",
                    "device=" + ",".join(runtime["gpu_uuids"]),
                    "--shm-size",
                    runtime["shm_size"],
                    "-e",
                    "YOLO_CONFIG_DIR=/tmp/visiox-ultralytics",
                    "--mount",
                    f"type=bind,src={source_path},dst=/input/model.{model['source_format']},readonly",
                    "--mount",
                    f"type=bind,src={output_root},dst=/output",
                ]
                if calibration_path is not None:
                    args.extend(
                        [
                            "--mount",
                            f"type=bind,src={calibration_path},dst=/input/calibration.dataset,readonly",
                        ]
                    )
                args.extend(
                    [
                        self.request["image_digest"],
                        "python",
                        "-m",
                        "visiox_yolo26_inference.optimize",
                        "--source",
                        f"/input/model.{model['source_format']}",
                        "--source-format",
                        model["source_format"],
                        "--target-format",
                        runtime["format"],
                        "--precision",
                        runtime["precision"],
                        "--input-shape",
                        ",".join(str(item) for item in runtime["input_shape"]),
                        "--output",
                        f"/output/model.{runtime['format']}",
                    ]
                )
                if calibration_path is not None:
                    args.extend(["--calibration", "/input/calibration.dataset"])
                _run(args)
            if not artifact_path.is_file():
                raise ValueError("optimized model artifact is unavailable")
            os.chmod(artifact_path, 0o400)
        self.artifact_path = artifact_path
        self.engine_digest = (
            model["checksum"]
            if artifact_path.is_dir()
            else _sha256(artifact_path)
        )
        config_root = (
            Path.home()
            / ".local"
            / "share"
            / "visiox"
            / "deployments"
            / self.request["labels"]["com.visiox.deployment-instance-id"]
        )
        self.config_path = config_root / (self.engine_digest + ".json")
        _write_config(self.config_path, self.request, artifact_path)

    def _start(self, *, candidate):
        name = (
            "visiox-"
            + ("candidate-" if candidate else "active-")
            + self.request["labels"]["com.visiox.deployment-instance-id"][-24:]
            + "-"
            + secrets.token_hex(4)
        )
        result = _run(
            _container_args(
                self.request,
                artifact_path=self.artifact_path,
                config_path=self.config_path,
                engine_digest=self.engine_digest,
                name=name,
                host_port=None if candidate else self.runtime["port"],
                runtime_user=self.runtime_user,
            ),
            timeout=120,
        )
        container_id = result.stdout.strip()
        if not CONTAINER_ID.fullmatch(container_id):
            raise ValueError("container identifier is invalid")
        return container_id

    def start_candidate(self):
        return self._start(candidate=True)

    def start_active(self):
        return self._start(candidate=False)

    def stop(self, container):
        _run(["docker", "stop", "--time", "10", container], timeout=30)

    def remove(self, container):
        _run(["docker", "rm", "--force", container], timeout=30)

    def start_existing(self, container):
        _run(["docker", "start", container], timeout=30)

    def _host_port(self, container):
        output = _run(["docker", "port", container, "8080/tcp"], timeout=30).stdout.strip()
        matches = [
            re.fullmatch(
                r"(?:127\.0\.0\.1|0\.0\.0\.0|\[::1\]|\[::\]):([0-9]{4,5})",
                line.strip(),
            )
            for line in output.splitlines()
            if line.strip()
        ]
        if not matches or any(match is None for match in matches):
            raise ValueError("container endpoint is invalid")
        ports = {int(match.group(1)) for match in matches if match is not None}
        if len(ports) != 1:
            raise ValueError("container endpoint is invalid")
        return ports.pop()

    def warmup(self, container):
        port = self._host_port(container)
        boundary = "visiox" + secrets.token_hex(8)
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="warmup.png"\r\n'
            "Content-Type: image/png\r\n\r\n"
        ).encode("ascii") + WARMUP_PNG + f"\r\n--{boundary}--\r\n".encode("ascii")
        deadline = time.monotonic() + 90
        while True:
            try:
                with urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as response:
                    health = json.load(response)
                if response.status != 200 or health.get("status") != "ok" or health.get("task") != "detect":
                    raise ValueError("health response is invalid")
                prediction_request = Request(
                    f"http://127.0.0.1:{port}/predict/image",
                    data=body,
                    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                    method="POST",
                )
                with urlopen(prediction_request, timeout=30) as response:
                    prediction = json.load(response)
                if (
                    response.status != 200
                    or prediction.get("task") != "detect"
                    or not isinstance(prediction.get("predictions"), list)
                ):
                    raise ValueError("image warmup response is invalid")
                return
            except Exception:
                if time.monotonic() >= deadline:
                    raise ValueError("deployment warmup failed") from None
                time.sleep(2)

    def verify_target(self):
        target = self.request["target"]
        output = _run(
            [
                "docker",
                "inspect",
                "--format",
                "{{json .Config.Labels}}",
                target["container_id"],
            ],
            timeout=30,
        ).stdout
        labels = json.loads(output)
        expected = {
            "com.visiox.deployment-instance-id": self.request["labels"]["com.visiox.deployment-instance-id"],
            "com.visiox.image-digest": target["image_digest"],
            "com.visiox.model-checksum": target["model_checksum"],
            "com.visiox.engine": target["engine"],
            "com.visiox.engine-digest": target["engine_digest"],
            "com.visiox.port": str(target["port"]),
        }
        if IDENTITY_TUPLE_KEYS.issubset(target):
            expected.update(
                {
                    "com.visiox.framework": target["framework"],
                    "com.visiox.adapter-key": target["adapter_key"],
                    "com.visiox.adapter-version": target["adapter_version"],
                    "com.visiox.model-format": target["model_format"],
                    "com.visiox.resolved-backend": target["resolved_backend"],
                    "com.visiox.runtime-digest": target["runtime_image_digest"],
                }
            )
        if RUNTIME_CHECKSUM_TUPLE_KEY in target:
            expected[RUNTIME_CHECKSUM_LABEL_KEY] = target[RUNTIME_CHECKSUM_TUPLE_KEY]
        if not isinstance(labels, dict) or any(labels.get(key) != value for key, value in expected.items()):
            raise ValueError("rollback container tuple does not match")

    def result(self, container):
        if self.request["action"] == "rollback":
            result = dict(self.request["target"])
            result["container_id"] = container
        else:
            result = {
                "container_id": container,
                "image_digest": self.request["image_digest"],
                "model_checksum": self.request["model"]["checksum"],
                "engine": self.runtime["format"],
                "engine_digest": self.engine_digest,
                "port": self.runtime["port"],
            }
            if EXTENDED_RUNTIME_KEYS.issubset(self.runtime):
                result.update(
                    {
                        "framework": self.runtime["framework"],
                        "adapter_key": self.runtime["adapter_key"],
                        "adapter_version": self.runtime["adapter_version"],
                        "model_format": self.runtime["model_format"],
                        "resolved_backend": self.runtime["resolved_backend"],
                        "runtime_image_digest": self.runtime["runtime_image_digest"],
                    }
                )
            if RUNTIME_CHECKSUM_KEY in self.runtime:
                result[RUNTIME_CHECKSUM_TUPLE_KEY] = self.runtime[RUNTIME_CHECKSUM_KEY]
        result["health_status"] = "healthy"
        return result


def main():
    if len(sys.argv) != 2:
        return 2
    stage = "request-validation"
    try:
        with open(sys.argv[1], "r", encoding="utf-8") as request_file:
            request = _validate_request(json.load(request_file))
        operations = Operations(request)
        if request["action"] == "deploy":
            stage = "previous-deployment-check"
            operations.verify_previous()
            stage = "runtime-image-pull"
            operations.pull()
            stage = "model-prepare"
            operations.prepare()
            stage = "container-activation"
            result = _promote(operations, request["previous_container_id"])
        else:
            stage = "rollback-target-check"
            operations.verify_target()
            stage = "rollback-activation"
            result = _rollback(
                operations,
                request["current_container_id"],
                request["target"]["container_id"],
            )
        print(json.dumps(result, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
        return 0
    except Exception as error:
        print(
            f"deployment operation failed at stage={stage} ({type(error).__name__})",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
PY
