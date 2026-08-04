#!/usr/bin/env bash
set -euo pipefail

exec python3 - "$@" <<'PY'
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}\Z")
IMAGE_DIGEST = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,430}@sha256:[a-f0-9]{64}\Z")
SHA256 = re.compile(r"[a-f0-9]{64}\Z")
GPU_UUID = re.compile(r"GPU-[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z")
CONTAINER_ID = re.compile(r"[a-f0-9]{12,64}\Z")
FIXED_ENTRYPOINT = "/usr/local/bin/visiox-train"
MAX_ARTIFACT_COUNT = 256
MAX_ARTIFACT_SIZE_BYTES = 8 * 1024 * 1024 * 1024
MAX_ARTIFACT_TOTAL_BYTES = 32 * 1024 * 1024 * 1024


class RequestValidationError(ValueError):
    def __init__(self, code):
        super().__init__("invalid distributed rank request")
        self.code = code


def invalid(code):
    raise RequestValidationError(code)


def canonical_checksum(value):
    encoded = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def safe_relative_path(value):
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        return False
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and not (len(value) >= 2 and value[0].isalpha() and value[1] == ":")
        and all(part not in {"", ".", ".."} for part in value.split("/"))
        and path.as_posix() == value
    )


def managed_paths(paths):
    if not isinstance(paths, dict) or set(paths) != {"input", "output", "launch_spec"}:
        invalid("paths-shape")
    if any(not isinstance(value, str) or any(char in value for char in "\x00\r\n") for value in paths.values()):
        invalid("path")
    input_path = Path(paths["input"])
    output_path = Path(paths["output"])
    spec_path = Path(paths["launch_spec"])
    if not input_path.is_absolute() or not output_path.is_absolute() or not spec_path.is_absolute():
        invalid("path")
    if input_path.parent != output_path.parent or spec_path != input_path / "launch-spec.json":
        invalid("path-boundary")
    return paths


def valid_upload_url(value):
    parsed = urlsplit(value) if isinstance(value, str) else None
    return bool(parsed and parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password and not parsed.fragment)


def validate(request):
    if isinstance(request, dict) and request.get("action") == "inspect":
        if set(request) != {"action", "container_id"} or not isinstance(request["container_id"], str) or not CONTAINER_ID.fullmatch(request["container_id"]):
            invalid("inspect-request")
        return request
    if isinstance(request, dict) and request.get("action") == "logs":
        if set(request) != {"action", "container_id"} or not isinstance(request["container_id"], str) or not CONTAINER_ID.fullmatch(request["container_id"]):
            invalid("logs-request")
        return request
    if isinstance(request, dict) and request.get("action") == "manifest":
        if set(request) != {"action", "output_path"} or not isinstance(request["output_path"], str) or not Path(request["output_path"]).is_absolute():
            invalid("manifest-request")
        return request
    if isinstance(request, dict) and request.get("action") == "collect":
        if set(request) != {"action", "output_path", "artifact_manifest", "uploads"} or not isinstance(request["output_path"], str) or not Path(request["output_path"]).is_absolute():
            invalid("collect-request")
        uploads = request["uploads"]
        if not isinstance(uploads, dict) or len(uploads) > MAX_ARTIFACT_COUNT:
            invalid("collect-uploads")
        if any(not safe_relative_path(path) or not valid_upload_url(url) for path, url in uploads.items()):
            invalid("collect-upload")
        return request
    expected = {
        "schema_version", "action", "run_id", "attempt", "runtime_image_digest",
        "framework", "adapter_key", "gpu_uuids", "node_rank",
        "launch_spec_checksum", "paths",
    }
    if not isinstance(request, dict) or set(request) != expected or request.get("schema_version") != "1.0" or request.get("action") != "launch":
        invalid("launch-shape")
    for key in ("run_id", "framework", "adapter_key"):
        if not isinstance(request[key], str) or not IDENTIFIER.fullmatch(request[key]):
            invalid("identifier")
    if not isinstance(request["attempt"], int) or isinstance(request["attempt"], bool) or not 1 <= request["attempt"] <= 100000:
        invalid("attempt")
    if not isinstance(request["node_rank"], int) or isinstance(request["node_rank"], bool) or not 0 <= request["node_rank"] <= 1023:
        invalid("node-rank")
    if not isinstance(request["runtime_image_digest"], str) or not IMAGE_DIGEST.fullmatch(request["runtime_image_digest"]):
        invalid("image-digest")
    if not isinstance(request["launch_spec_checksum"], str) or not SHA256.fullmatch(request["launch_spec_checksum"]):
        invalid("launch-spec-checksum")
    gpus = request["gpu_uuids"]
    if not isinstance(gpus, list) or not gpus or len(gpus) > 64 or len(set(gpus)) != len(gpus):
        invalid("gpu-layout")
    if any(not isinstance(item, str) or not GPU_UUID.fullmatch(item) for item in gpus):
        invalid("gpu-layout")
    managed_paths(request["paths"])
    return request


def load_launch_spec(request):
    path = Path(request["paths"]["launch_spec"])
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != request["launch_spec_checksum"]:
        invalid("launch-spec-file-checksum")
    spec = json.loads(payload)
    if not isinstance(spec, dict) or spec.get("schema_version") != "1.0" or spec.get("entrypoint") != FIXED_ENTRYPOINT:
        invalid("launch-spec")
    distributed = spec.get("distributed")
    if not isinstance(distributed, dict):
        invalid("launch-spec-distributed")
    expected = {
        "training_job_id": None,
        "run_id": request["run_id"],
        "attempt": request["attempt"],
        "framework": request["framework"],
        "adapter_key": request["adapter_key"],
    }
    for key, value in expected.items():
        if value is not None and spec.get(key) != value:
            invalid("launch-spec-identity")
    if not isinstance(spec.get("training_job_id"), str) or not IDENTIFIER.fullmatch(spec["training_job_id"]):
        invalid("launch-spec-job")
    if distributed.get("node_rank") != request["node_rank"] or distributed.get("gpu_uuids") != request["gpu_uuids"]:
        invalid("launch-spec-rank")
    if spec.get("paths", {}).get("launch_spec") != "/workspace/input/launch-spec.json" or spec.get("paths", {}).get("output") != "/workspace/output":
        invalid("launch-spec-paths")
    return spec


def inspect_container(container_id):
    result = subprocess.run(["docker", "inspect", "--format", "{{json .State}}", container_id], check=True, capture_output=True, text=True, timeout=30)
    state = json.loads(result.stdout)
    if not isinstance(state, dict) or not isinstance(state.get("Running"), bool):
        invalid("inspect-state")
    exit_code = state.get("ExitCode")
    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
        invalid("inspect-exit-code")
    return {"running": state["Running"], "exit_code": exit_code}


def collect_logs(container_id):
    result = subprocess.run(["docker", "logs", "--timestamps", container_id], check=False, capture_output=True, text=True, timeout=60)
    return {"stdout": result.stdout, "stderr": result.stderr}


def build_artifact_manifest(output_path):
    root = Path(output_path).resolve()
    files = sorted(path for path in root.rglob("*") if path.is_file() and path.name != "artifact-manifest.json")
    if len(files) > MAX_ARTIFACT_COUNT:
        raise ValueError("artifact manifest contains too many files")
    artifacts = []
    total = 0
    for path in files:
        resolved = path.resolve()
        if root not in resolved.parents:
            raise ValueError("artifact path escaped output root")
        size = resolved.stat().st_size
        if size > MAX_ARTIFACT_SIZE_BYTES:
            raise ValueError("artifact exceeds size limit")
        total += size
        if total > MAX_ARTIFACT_TOTAL_BYTES:
            raise ValueError("artifact manifest exceeds total size limit")
        digest = hashlib.sha256()
        with resolved.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        artifacts.append({"path": resolved.relative_to(root).as_posix(), "size_bytes": size, "checksum_sha256": digest.hexdigest()})
    return {"schema_version": "1.0", "artifacts": artifacts}


def validate_artifact_manifest(manifest, output_path):
    root = Path(output_path).resolve()
    if not isinstance(manifest, dict) or set(manifest) != {"schema_version", "artifacts"} or manifest.get("schema_version") != "1.0":
        raise ValueError("invalid artifact manifest")
    artifacts = manifest["artifacts"]
    if not isinstance(artifacts, list) or len(artifacts) > MAX_ARTIFACT_COUNT:
        raise ValueError("invalid artifact manifest")
    seen = set()
    total = 0
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) != {"path", "size_bytes", "checksum_sha256"}:
            raise ValueError("invalid artifact manifest")
        relative = artifact["path"]
        if not safe_relative_path(relative) or relative in seen:
            raise ValueError("invalid artifact path")
        path = (root / relative).resolve()
        if root not in path.parents or not path.is_file():
            raise ValueError("artifact path escaped output root")
        size = artifact["size_bytes"]
        if not isinstance(size, int) or isinstance(size, bool) or size < 0 or size > MAX_ARTIFACT_SIZE_BYTES or path.stat().st_size != size:
            raise ValueError("artifact size mismatch")
        total += size
        if total > MAX_ARTIFACT_TOTAL_BYTES:
            raise ValueError("artifact manifest exceeds total size limit")
        checksum = artifact["checksum_sha256"]
        if not isinstance(checksum, str) or not SHA256.fullmatch(checksum):
            raise ValueError("invalid artifact checksum")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != checksum:
            raise ValueError("artifact checksum mismatch")
        seen.add(relative)
    return manifest


def collect_artifacts(output_path, uploads, artifact_manifest=None):
    root = Path(output_path).resolve()
    manifest = validate_artifact_manifest(artifact_manifest or build_artifact_manifest(root), root)
    entries = {item["path"]: item for item in manifest["artifacts"]}
    results = {}
    for requested_path, url in uploads.items():
        relative = requested_path
        if relative not in entries:
            matches = [path for path in entries if PurePosixPath(path).name == requested_path]
            if len(matches) != 1:
                raise ValueError("requested artifact is not in trusted manifest")
            relative = matches[0]
        path = (root / relative).resolve()
        payload = path.read_bytes()
        request = Request(url, data=payload, method="PUT", headers={"Content-Type": "application/octet-stream"})
        with urlopen(request, timeout=600) as response:
            if not 200 <= int(response.status) < 300:
                raise ValueError("training artifact upload failed")
        results[requested_path] = {"checksum": entries[relative]["checksum_sha256"], "size_bytes": entries[relative]["size_bytes"]}
    return results


def main():
    if len(sys.argv) != 2:
        return 2
    stage = "request-validation"
    try:
        with open(sys.argv[1], "r", encoding="utf-8") as source:
            request = validate(json.load(source))
        if request["action"] == "inspect":
            stage = "container-inspect"
            print(json.dumps(inspect_container(request["container_id"]), ensure_ascii=True, separators=(",", ":"), sort_keys=True))
            return 0
        if request["action"] == "logs":
            stage = "container-logs"
            print(json.dumps(collect_logs(request["container_id"]), ensure_ascii=True, separators=(",", ":"), sort_keys=True))
            return 0
        if request["action"] == "manifest":
            stage = "artifact-manifest"
            print(json.dumps(build_artifact_manifest(request["output_path"]), ensure_ascii=True, separators=(",", ":"), sort_keys=True))
            return 0
        if request["action"] == "collect":
            stage = "artifact-collect"
            artifacts = collect_artifacts(request["output_path"], request["uploads"], request["artifact_manifest"])
            print(json.dumps({"artifacts": artifacts}, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
            return 0
        spec = load_launch_spec(request)
        name = f"visiox-train-{request['run_id']}-{request['attempt']}-rank-{request['node_rank']}"
        stage = "previous-container-cleanup"
        subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True, text=True, timeout=30)
        command = [
            "docker", "run", "-d", "--name", name,
            "--user", f"{os.getuid()}:{os.getgid()}",
            "--network", "host", "--shm-size", "4g",
            "--gpus", "device=" + ",".join(request["gpu_uuids"]),
            "--label", "com.visiox.managed=true",
            "--label", f"com.visiox.training-job-id={spec['training_job_id']}",
            "--label", f"com.visiox.training-run-id={request['run_id']}",
            "--label", f"com.visiox.training-attempt={request['attempt']}",
            "--label", f"com.visiox.training-node-rank={request['node_rank']}",
            "--label", f"com.visiox.training-framework={request['framework']}",
            "--label", f"com.visiox.training-adapter-key={request['adapter_key']}",
            "--label", f"com.visiox.launch-spec-checksum={request['launch_spec_checksum']}",
            "--mount", f"type=bind,src={request['paths']['input']},dst=/workspace/input,readonly",
            "--mount", f"type=bind,src={request['paths']['output']},dst=/workspace/output",
            "-e", "HOME=/tmp", "-e", "USER=visiox-edge", "-e", "LOGNAME=visiox-edge",
            "-e", "YOLO_CONFIG_DIR=/tmp", "-e", "MPLCONFIGDIR=/tmp",
            "-e", "TORCHINDUCTOR_CACHE_DIR=/tmp/torchinductor",
            request["runtime_image_digest"],
            "/usr/local/bin/visiox-train",
        ]
        stage = "container-launch"
        result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=60)
        container_id = result.stdout.strip()
        if not CONTAINER_ID.fullmatch(container_id):
            invalid("container-id")
        print(json.dumps({"container_id": container_id, "container_name": name, "node_rank": request["node_rank"]}, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
        return 0
    except Exception as error:
        detail = f":{error.code}" if isinstance(error, RequestValidationError) else ""
        print(f"distributed rank operation failed at stage={stage}{detail} ({type(error).__name__})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
PY
