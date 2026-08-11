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
import tarfile
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import zipfile


IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}\Z")
IMAGE_DIGEST = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,430}@sha256:[a-f0-9]{64}\Z")
SHA256 = re.compile(r"[a-f0-9]{64}\Z")
ARTIFACT_ROLES = {"dataset", "model", "checkpoint"}
MANAGED_ARTIFACT_PATHS = {
    "dataset": "/workspace/dataset",
    "model": "/workspace/model/base.pt",
    "checkpoint": "/workspace/checkpoint/last.pt",
}
FIXED_ENTRYPOINT = "/usr/local/bin/visiox-train"
REQUIRED_SIGNED_ENV = {
    "VISIOX_TRAINING_JOB_ID", "VISIOX_TRAINING_RUN_ID", "VISIOX_TRAINING_ATTEMPT",
    "VISIOX_FRAMEWORK", "VISIOX_NODE_ID", "VISIOX_NODE_RANK", "VISIOX_NNODES",
    "VISIOX_NPROC_PER_NODE", "VISIOX_GPU_UUIDS_JSON", "VISIOX_MASTER_ADDR",
    "VISIOX_MASTER_PORT", "VISIOX_OUTPUT_DIR", "VISIOX_DATASET_DIR",
    "VISIOX_RUNTIME_INPUTS_JSON",
}


def invalid():
    raise ValueError("invalid training staging request")


def canonical_checksum(value):
    encoded = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def safe_relative_path(value):
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and not (len(value) >= 2 and value[0].isalpha() and value[1] == ":") and all(part not in {"", ".", ".."} for part in value.split("/")) and path.as_posix() == value


def valid_url(value):
    parsed = urlsplit(value) if isinstance(value, str) and len(value) <= 4096 else None
    return bool(parsed and parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password and not parsed.fragment)


def validate_snapshot_value(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or not key or any(char in key for char in "\x00\r\n"):
                invalid()
            validate_snapshot_value(item)
        return
    if isinstance(value, list):
        for item in value:
            validate_snapshot_value(item)
        return
    if isinstance(value, str):
        if any(char in value for char in "\x00\r\n") or value.startswith("/") or value.casefold().startswith("file:") or re.match(r"^(?:[A-Za-z]:[\\/]|\\\\|//)", value):
            invalid()
        return
    if value is None or isinstance(value, (bool, int, float)):
        return
    invalid()


def validate_runtime_inputs(value):
    if not isinstance(value, dict) or set(value) != {"parameters", "model", "dataset", "artifacts"}:
        invalid()
    for field in ("parameters", "model", "dataset"):
        if not isinstance(value[field], dict):
            invalid()
        validate_snapshot_value(value[field])
    artifacts = value["artifacts"]
    if not isinstance(artifacts, list) or len(artifacts) > len(MANAGED_ARTIFACT_PATHS):
        invalid()
    roles = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) != {"role", "path"}:
            invalid()
        role = artifact["role"]
        if role in roles or artifact["path"] != MANAGED_ARTIFACT_PATHS.get(role):
            invalid()
        roles.add(role)
    return value


def validate_launch_spec(spec):
    expected = {"schema_version", "adapter_key", "adapter_version", "argv", "env", "working_directory"}
    if not isinstance(spec, dict) or set(spec) != expected or spec.get("schema_version") != "1.0":
        invalid()
    if not isinstance(spec["adapter_key"], str) or not IDENTIFIER.fullmatch(spec["adapter_key"]):
        invalid()
    if not isinstance(spec["adapter_version"], str) or not IDENTIFIER.fullmatch(spec["adapter_version"]):
        invalid()
    if spec["argv"] != [FIXED_ENTRYPOINT] or spec["working_directory"] != "workspace":
        invalid()
    environment = spec["env"]
    if not isinstance(environment, dict) or not REQUIRED_SIGNED_ENV.issubset(environment):
        invalid()
    for key, value in environment.items():
        if not isinstance(key, str) or not key or "=" in key or any(char in key for char in "\x00\r\n"):
            invalid()
        if not isinstance(value, str) or any(char in value for char in "\x00\r\n"):
            invalid()
    if environment["VISIOX_OUTPUT_DIR"] != "/workspace/output" or environment["VISIOX_DATASET_DIR"] != "/workspace/dataset":
        invalid()
    try:
        runtime_inputs = validate_runtime_inputs(json.loads(environment["VISIOX_RUNTIME_INPUTS_JSON"]))
        gpu_uuids = json.loads(environment["VISIOX_GPU_UUIDS_JSON"])
    except json.JSONDecodeError:
        invalid()
    if not isinstance(gpu_uuids, list) or not all(isinstance(item, str) and item for item in gpu_uuids):
        invalid()
    return spec


def validate(request):
    if isinstance(request, dict) and request.get("action") == "resume":
        expected = {"schema_version", "action", "run_id", "attempt", "training_job_id", "adapter_key", "adapter_version"}
        if set(request) != expected or request.get("schema_version") != "1.0":
            invalid()
        if not isinstance(request["run_id"], str) or not IDENTIFIER.fullmatch(request["run_id"]):
            invalid()
        if not isinstance(request["attempt"], int) or isinstance(request["attempt"], bool) or request["attempt"] < 1:
            invalid()
        for key in ("training_job_id", "adapter_key", "adapter_version"):
            if not isinstance(request[key], str) or not IDENTIFIER.fullmatch(request[key]):
                invalid()
        return request
    expected = {"schema_version", "run_id", "attempt", "runtime_image_digest", "launch_spec", "launch_spec_checksum", "artifacts"}
    if not isinstance(request, dict) or set(request) != expected or request.get("schema_version") != "1.0":
        invalid()
    if not isinstance(request["run_id"], str) or not IDENTIFIER.fullmatch(request["run_id"]):
        invalid()
    if not isinstance(request["attempt"], int) or isinstance(request["attempt"], bool) or request["attempt"] < 1:
        invalid()
    if not isinstance(request["runtime_image_digest"], str) or not IMAGE_DIGEST.fullmatch(request["runtime_image_digest"]):
        invalid()
    spec = validate_launch_spec(request["launch_spec"])
    env = spec["env"]
    if env["VISIOX_TRAINING_RUN_ID"] != request["run_id"] or env["VISIOX_TRAINING_ATTEMPT"] != str(request["attempt"]):
        invalid()
    if not isinstance(request["launch_spec_checksum"], str) or not SHA256.fullmatch(request["launch_spec_checksum"]):
        invalid()
    if canonical_checksum(spec) != request["launch_spec_checksum"]:
        invalid()
    artifacts = request["artifacts"]
    if not isinstance(artifacts, list) or len(artifacts) > 8:
        invalid()
    roles = set()
    targets = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) != {"role", "download_url", "checksum_sha256", "target_path", "unpack_to"}:
            invalid()
        role = artifact["role"]
        target = artifact["target_path"]
        unpack_to = artifact["unpack_to"]
        if role not in ARTIFACT_ROLES or role in roles or not valid_url(artifact["download_url"]):
            invalid()
        if not isinstance(artifact["checksum_sha256"], str) or not SHA256.fullmatch(artifact["checksum_sha256"]):
            invalid()
        if not safe_relative_path(target) or target in targets:
            invalid()
        if unpack_to is not None and (role != "dataset" or not safe_relative_path(unpack_to)):
            invalid()
        roles.add(role)
        targets.add(target)
    runtime_inputs = json.loads(env["VISIOX_RUNTIME_INPUTS_JSON"])
    if roles != {item["role"] for item in runtime_inputs["artifacts"]}:
        invalid()
    return request


def download(url, destination, checksum):
    destination.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".partial")
    digest = hashlib.sha256()
    request = Request(url, headers={"User-Agent": "visiox-edge-executor/1"})
    with urlopen(request, timeout=300) as response, temporary.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            digest.update(chunk)
            output.write(chunk)
    if digest.hexdigest() != checksum:
        temporary.unlink(missing_ok=True)
        raise ValueError("training artifact checksum mismatch")
    os.replace(temporary, destination)


def safe_target(root, member):
    candidate = (root / member).resolve()
    if root.resolve() not in candidate.parents and candidate != root.resolve():
        raise ValueError("unsafe dataset archive member")
    return candidate


def unpack_dataset(archive, destination):
    destination.mkdir(mode=0o750, parents=True, exist_ok=True)
    if tarfile.is_tarfile(archive):
        with tarfile.open(archive, "r:*") as source:
            for member in source.getmembers():
                safe_target(destination, member.name)
                if member.issym() or member.islnk() or member.isdev():
                    raise ValueError("unsafe dataset archive member")
            source.extractall(destination, filter="data")
        return
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as source:
            for member in source.infolist():
                safe_target(destination, member.filename)
            source.extractall(destination)
        return
    raise ValueError("dataset artifact must be a tar or zip archive")


def main():
    if len(sys.argv) != 2:
        return 2
    stage = "request-validation"
    try:
        with open(sys.argv[1], "r", encoding="utf-8") as source:
            request = validate(json.load(source))
        root = Path.home() / ".local" / "share" / "visiox" / "training" / request["run_id"] / str(request["attempt"])
        input_dir = root / "input"
        output_dir = root / "output"
        launch_spec_path = input_dir / "launch-spec.json"
        if request.get("action") == "resume":
            stage = "workspace-resume"
            if not launch_spec_path.is_file() or not output_dir.is_dir():
                raise ValueError("training workspace is unavailable")
            encoded_spec = launch_spec_path.read_bytes()
            spec = validate_launch_spec(json.loads(encoded_spec))
            canonical_spec = json.dumps(spec, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
            env = spec["env"]
            if encoded_spec != canonical_spec:
                raise ValueError("training launch spec is not canonical")
            if (
                spec["adapter_key"] != request["adapter_key"]
                or spec["adapter_version"] != request["adapter_version"]
                or env["VISIOX_TRAINING_JOB_ID"] != request["training_job_id"]
                or env["VISIOX_TRAINING_RUN_ID"] != request["run_id"]
                or env["VISIOX_TRAINING_ATTEMPT"] != str(request["attempt"])
            ):
                raise ValueError("training workspace identity mismatch")
            print(json.dumps({"root": str(root), "paths": {"output": str(output_dir), "launch_spec": str(launch_spec_path), "artifacts": {}}}, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
            return 0
        stage = "runtime-image-pull"
        subprocess.run(["docker", "pull", request["runtime_image_digest"]], check=True, capture_output=True, text=True, timeout=1800)
        stage = "runtime-entrypoint-check"
        subprocess.run(
            ["docker", "run", "--rm", "--entrypoint", "/usr/bin/test", request["runtime_image_digest"], "-x", FIXED_ENTRYPOINT],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        stage = "workspace-prepare"
        input_dir.mkdir(mode=0o750, parents=True, exist_ok=True)
        output_dir.mkdir(mode=0o750, parents=True, exist_ok=True)
        artifact_paths = {}
        for artifact in request["artifacts"]:
            stage = f"artifact-download:{artifact['role']}"
            destination = input_dir / artifact["target_path"]
            download(artifact["download_url"], destination, artifact["checksum_sha256"])
            artifact_path = destination
            if artifact["unpack_to"] is not None:
                stage = f"artifact-unpack:{artifact['role']}"
                artifact_path = input_dir / artifact["unpack_to"]
                unpack_dataset(destination, artifact_path)
            artifact_paths[artifact["role"]] = str(artifact_path)
        launch_spec_path.write_text(json.dumps(request["launch_spec"], ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True), encoding="utf-8")
        launch_spec_path.chmod(0o440)
        print(json.dumps({"root": str(root), "paths": {"output": str(output_dir), "launch_spec": str(launch_spec_path), "artifacts": artifact_paths}}, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
        return 0
    except Exception as error:
        print(f"training artifact staging failed at stage={stage} ({type(error).__name__})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
PY
