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
ALLOWED_ARTIFACT_TARGETS = {
    "/workspace/dataset",
    "/workspace/model/base.pt",
    "/workspace/checkpoint/last.pt",
}


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
    return not path.is_absolute() and not (len(value) >= 2 and value[0].isalpha() and value[1] == ":") and all(part not in {"", ".", ".."} for part in value.split("/")) and path.as_posix() == value


def valid_upload_url(value):
    parsed = urlsplit(value) if isinstance(value, str) else None
    return bool(parsed and parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password and not parsed.fragment)


def managed_paths(paths):
    if not isinstance(paths, dict) or set(paths) != {"output", "launch_spec"}:
        invalid("paths-shape")
    if any(not isinstance(value, str) or not Path(value).is_absolute() or any(char in value for char in "\x00\r\n") for value in paths.values()):
        invalid("path")
    output_path = Path(paths["output"])
    spec_path = Path(paths["launch_spec"])
    if output_path.parent != spec_path.parent.parent or spec_path.name != "launch-spec.json":
        invalid("path-boundary")
    return paths


def validate(request):
    if isinstance(request, dict) and request.get("action") in {"inspect", "logs"}:
        if set(request) != {"action", "container_id"} or not isinstance(request["container_id"], str) or not CONTAINER_ID.fullmatch(request["container_id"]):
            invalid(f"{request.get('action')}-request")
        return request
    if isinstance(request, dict) and request.get("action") == "manifest":
        expected = {"action", "output_path", "task_id", "adapter_key", "adapter_version"}
        if set(request) != expected or not isinstance(request["output_path"], str) or not Path(request["output_path"]).is_absolute():
            invalid("manifest-request")
        for key in ("task_id", "adapter_key", "adapter_version"):
            if not isinstance(request[key], str) or not IDENTIFIER.fullmatch(request[key]):
                invalid("manifest-identity")
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
        "framework", "adapter_key", "adapter_version", "gpu_uuids", "node_rank",
        "launch_spec_checksum", "paths", "mounts",
    }
    if not isinstance(request, dict) or set(request) != expected or request.get("schema_version") != "1.0" or request.get("action") != "launch":
        invalid("launch-shape")
    for key in ("run_id", "framework", "adapter_key", "adapter_version"):
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
    if not isinstance(gpus, list) or not gpus or len(gpus) > 64 or len(set(gpus)) != len(gpus) or any(not isinstance(item, str) or not GPU_UUID.fullmatch(item) for item in gpus):
        invalid("gpu-layout")
    managed_paths(request["paths"])
    mounts = request["mounts"]
    root = Path(request["paths"]["launch_spec"]).parent.parent.resolve()
    if not isinstance(mounts, list) or len(mounts) > 3:
        invalid("mounts")
    targets = set()
    for mount in mounts:
        if not isinstance(mount, dict) or set(mount) != {"source", "target", "read_only"} or mount["read_only"] is not True:
            invalid("mount")
        if mount["target"] not in ALLOWED_ARTIFACT_TARGETS or mount["target"] in targets:
            invalid("mount-target")
        source = Path(mount["source"]).resolve() if isinstance(mount["source"], str) else None
        if source is None or root not in source.parents or not source.exists():
            invalid("mount-source")
        targets.add(mount["target"])
    return request


def load_launch_spec(request):
    path = Path(request["paths"]["launch_spec"])
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != request["launch_spec_checksum"]:
        invalid("launch-spec-file-checksum")
    spec = json.loads(payload)
    expected = {"schema_version", "adapter_key", "adapter_version", "argv", "env", "working_directory"}
    if not isinstance(spec, dict) or set(spec) != expected or spec.get("schema_version") != "1.0" or spec.get("argv") != [FIXED_ENTRYPOINT] or spec.get("working_directory") != "workspace":
        invalid("launch-spec")
    if spec.get("adapter_key") != request["adapter_key"] or spec.get("adapter_version") != request["adapter_version"]:
        invalid("launch-spec-adapter")
    env = spec.get("env")
    if not isinstance(env, dict) or any(not isinstance(key, str) or not key or "=" in key or not isinstance(value, str) or any(char in key + value for char in "\x00\r\n") for key, value in env.items()):
        invalid("launch-spec-env")
    expected_env = {
        "VISIOX_TRAINING_RUN_ID": request["run_id"],
        "VISIOX_TRAINING_ATTEMPT": str(request["attempt"]),
        "VISIOX_FRAMEWORK": request["framework"],
        "VISIOX_NODE_RANK": str(request["node_rank"]),
        "VISIOX_GPU_UUIDS_JSON": json.dumps(request["gpu_uuids"], separators=(",", ":")),
        "VISIOX_OUTPUT_DIR": "/workspace/output",
    }
    if any(env.get(key) != value for key, value in expected_env.items()):
        invalid("launch-spec-identity")
    if not isinstance(env.get("VISIOX_TRAINING_JOB_ID"), str) or not IDENTIFIER.fullmatch(env["VISIOX_TRAINING_JOB_ID"]):
        invalid("launch-spec-job")
    return spec


def inspect_container(container_id):
    result = subprocess.run(["docker", "inspect", "--format", "{{json .State}}", container_id], check=True, capture_output=True, text=True, timeout=30)
    state = json.loads(result.stdout)
    if not isinstance(state, dict) or not isinstance(state.get("Running"), bool) or isinstance(state.get("ExitCode"), bool) or not isinstance(state.get("ExitCode"), int):
        invalid("inspect-state")
    return {"running": state["Running"], "exit_code": state["ExitCode"]}


def collect_logs(container_id):
    result = subprocess.run(["docker", "logs", "--timestamps", container_id], check=False, capture_output=True, text=True, timeout=60)
    return {"stdout": result.stdout, "stderr": result.stderr}


def validate_artifact_manifest(manifest, output_path, expected_identity=None):
    root = Path(output_path).resolve()
    expected = {"schema_version", "task_id", "adapter_key", "adapter_version", "artifacts", "checksum_sha256"}
    if not isinstance(manifest, dict) or set(manifest) != expected or manifest.get("schema_version") != "1.0":
        raise ValueError("invalid artifact manifest")
    if expected_identity and any(manifest.get(key) != value for key, value in expected_identity.items()):
        raise ValueError("artifact manifest identity mismatch")
    checksum = manifest.get("checksum_sha256")
    if not isinstance(checksum, str) or not SHA256.fullmatch(checksum):
        raise ValueError("invalid artifact manifest checksum")
    unsigned = dict(manifest)
    unsigned.pop("checksum_sha256")
    if canonical_checksum(unsigned) != checksum:
        raise ValueError("artifact manifest checksum mismatch")
    artifacts = manifest["artifacts"]
    if not isinstance(artifacts, list) or len(artifacts) > MAX_ARTIFACT_COUNT:
        raise ValueError("invalid artifact manifest")
    seen = set()
    total = 0
    for artifact in artifacts:
        fields = {"schema_version", "path", "size_bytes", "checksum_sha256", "artifact_type"}
        if not isinstance(artifact, dict) or set(artifact) != fields or artifact.get("schema_version") != "1.0" or not isinstance(artifact.get("artifact_type"), str) or not artifact["artifact_type"]:
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
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if artifact.get("checksum_sha256") != digest:
            raise ValueError("artifact checksum mismatch")
        seen.add(relative)
    return manifest


def load_artifact_manifest(output_path, expected_identity):
    path = Path(output_path).resolve() / "artifact-manifest.json"
    return validate_artifact_manifest(json.loads(path.read_text(encoding="utf-8")), output_path, expected_identity)


def collect_artifacts(output_path, uploads, artifact_manifest):
    manifest = validate_artifact_manifest(artifact_manifest, output_path)
    entries = {item["path"]: item for item in manifest["artifacts"]}
    results = {}
    for relative, url in uploads.items():
        if relative not in entries:
            raise ValueError("requested artifact is not in trusted manifest")
        payload = (Path(output_path).resolve() / relative).read_bytes()
        request = Request(url, data=payload, method="PUT", headers={"Content-Type": "application/octet-stream"})
        with urlopen(request, timeout=600) as response:
            if not 200 <= int(response.status) < 300:
                raise ValueError("training artifact upload failed")
        results[relative] = {"checksum": entries[relative]["checksum_sha256"], "size_bytes": entries[relative]["size_bytes"]}
    return results


def main():
    if len(sys.argv) != 2:
        return 2
    stage = "request-validation"
    try:
        with open(sys.argv[1], "r", encoding="utf-8") as source:
            request = validate(json.load(source))
        if request["action"] == "inspect":
            print(json.dumps(inspect_container(request["container_id"]), ensure_ascii=True, separators=(",", ":"), sort_keys=True))
            return 0
        if request["action"] == "logs":
            print(json.dumps(collect_logs(request["container_id"]), ensure_ascii=True, separators=(",", ":"), sort_keys=True))
            return 0
        if request["action"] == "manifest":
            identity = {key: request[key] for key in ("task_id", "adapter_key", "adapter_version")}
            print(json.dumps(load_artifact_manifest(request["output_path"], identity), ensure_ascii=True, separators=(",", ":"), sort_keys=True))
            return 0
        if request["action"] == "collect":
            artifacts = collect_artifacts(request["output_path"], request["uploads"], request["artifact_manifest"])
            print(json.dumps({"artifacts": artifacts}, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
            return 0
        spec = load_launch_spec(request)
        name = f"visiox-train-{request['run_id']}-{request['attempt']}-rank-{request['node_rank']}"
        stage = "previous-container-cleanup"
        subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True, text=True, timeout=30)
        cache_root = Path.home() / ".cache" / "visiox" / "models"
        cache_root.mkdir(mode=0o750, parents=True, exist_ok=True)
        command = [
            "docker", "run", "-d", "--name", name,
            "--user", f"{os.getuid()}:{os.getgid()}", "--network", "host", "--shm-size", "4g",
            "--gpus", "device=" + ",".join(request["gpu_uuids"]),
            "--label", "com.visiox.managed=true",
            "--label", f"com.visiox.training-job-id={spec['env']['VISIOX_TRAINING_JOB_ID']}",
            "--label", f"com.visiox.training-run-id={request['run_id']}",
            "--label", f"com.visiox.training-attempt={request['attempt']}",
            "--label", f"com.visiox.training-node-rank={request['node_rank']}",
            "--label", f"com.visiox.training-framework={request['framework']}",
            "--label", f"com.visiox.training-adapter-key={request['adapter_key']}",
            "--label", f"com.visiox.launch-spec-checksum={request['launch_spec_checksum']}",
            "--mount", f"type=bind,src={request['paths']['launch_spec']},dst=/workspace/input/launch-spec.json,readonly",
            "--mount", f"type=bind,src={request['paths']['output']},dst=/workspace/output",
            "--mount", f"type=bind,src={cache_root},dst=/workspace/model-cache",
        ]
        for mount in request["mounts"]:
            command.extend(["--mount", f"type=bind,src={mount['source']},dst={mount['target']},readonly"])
        base_environment = {"HOME": "/tmp", "USER": "visiox-edge", "LOGNAME": "visiox-edge", "YOLO_CONFIG_DIR": "/tmp", "MPLCONFIGDIR": "/tmp", "TORCHINDUCTOR_CACHE_DIR": "/tmp/torchinductor"}
        for key, value in {**base_environment, **spec["env"]}.items():
            command.extend(["-e", f"{key}={value}"])
        command.extend([request["runtime_image_digest"], FIXED_ENTRYPOINT])
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
