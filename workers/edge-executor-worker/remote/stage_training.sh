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
GPU_UUID = re.compile(r"GPU-[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z")
LAN_ADDRESS = re.compile(r"[A-Za-z0-9][A-Za-z0-9.:-]{0,253}\Z")
ARTIFACT_ROLES = {"dataset", "model", "checkpoint"}
FIXED_ENTRYPOINT = "/usr/local/bin/visiox-train"
CONTAINER_PATHS = {
    "launch_spec": "/workspace/input/launch-spec.json",
    "dataset": "/workspace/input/dataset",
    "model": "/workspace/input/artifacts/base.pt",
    "checkpoint": "/workspace/input/artifacts/last.pt",
    "output": "/workspace/output",
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
    return (
        not path.is_absolute()
        and not (len(value) >= 2 and value[0].isalpha() and value[1] == ":")
        and all(part not in {"", ".", ".."} for part in value.split("/"))
        and path.as_posix() == value
    )


def valid_url(value):
    if not isinstance(value, str) or len(value) > 4096:
        return False
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password and not parsed.fragment


def validate_launch_spec(spec):
    expected = {
        "schema_version", "training_job_id", "run_id", "attempt", "framework",
        "adapter_key", "adapter_version", "entrypoint", "parameters", "environment",
        "distributed", "paths",
    }
    if not isinstance(spec, dict) or set(spec) != expected or spec.get("schema_version") != "1.0":
        invalid()
    for key in ("training_job_id", "run_id", "framework", "adapter_key", "adapter_version"):
        if not isinstance(spec[key], str) or not IDENTIFIER.fullmatch(spec[key]):
            invalid()
    if spec["entrypoint"] != FIXED_ENTRYPOINT:
        invalid()
    if not isinstance(spec["attempt"], int) or isinstance(spec["attempt"], bool) or not 1 <= spec["attempt"] <= 100000:
        invalid()
    parameters = spec["parameters"]
    if not isinstance(parameters, dict) or not isinstance(parameters.get("arguments"), list):
        invalid()
    if any(not isinstance(item, str) or any(char in item for char in "\x00\r\n") for item in parameters["arguments"]):
        invalid()
    environment = spec["environment"]
    if not isinstance(environment, dict):
        invalid()
    for key, value in environment.items():
        if not isinstance(key, str) or not key or "=" in key or any(char in key for char in "\x00\r\n"):
            invalid()
        if not isinstance(value, str) or any(char in value for char in "\x00\r\n"):
            invalid()
    distributed = spec["distributed"]
    expected_distributed = {"node_id", "gpu_uuids", "node_rank", "nnodes", "nproc_per_node", "rendezvous"}
    if not isinstance(distributed, dict) or set(distributed) != expected_distributed:
        invalid()
    if not isinstance(distributed["node_id"], str) or not IDENTIFIER.fullmatch(distributed["node_id"]):
        invalid()
    integer_limits = {"node_rank": (0, 1023), "nnodes": (1, 1024), "nproc_per_node": (1, 64)}
    for key, (minimum, maximum) in integer_limits.items():
        value = distributed[key]
        if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
            invalid()
    if distributed["node_rank"] >= distributed["nnodes"]:
        invalid()
    gpus = distributed["gpu_uuids"]
    if not isinstance(gpus, list) or len(gpus) != distributed["nproc_per_node"] or len(set(gpus)) != len(gpus):
        invalid()
    if any(not isinstance(item, str) or not GPU_UUID.fullmatch(item) for item in gpus):
        invalid()
    rendezvous = distributed["rendezvous"]
    if not isinstance(rendezvous, dict) or set(rendezvous) != {"master_addr", "master_port"}:
        invalid()
    if not isinstance(rendezvous["master_addr"], str) or not LAN_ADDRESS.fullmatch(rendezvous["master_addr"]) or ".." in rendezvous["master_addr"]:
        invalid()
    port = rendezvous["master_port"]
    if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
        invalid()
    if spec["paths"] != CONTAINER_PATHS:
        invalid()
    return spec


def validate(request):
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
    if spec["run_id"] != request["run_id"] or spec["attempt"] != request["attempt"]:
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
        stage = "runtime-image-pull"
        subprocess.run(["docker", "pull", request["runtime_image_digest"]], check=True, capture_output=True, text=True, timeout=1800)
        stage = "workspace-prepare"
        root = Path.home() / ".local" / "share" / "visiox" / "training" / request["run_id"] / str(request["attempt"])
        input_dir = root / "input"
        output_dir = root / "output"
        input_dir.mkdir(mode=0o750, parents=True, exist_ok=True)
        output_dir.mkdir(mode=0o750, parents=True, exist_ok=True)
        for artifact in request["artifacts"]:
            stage = f"artifact-download:{artifact['role']}"
            destination = input_dir / artifact["target_path"]
            download(artifact["download_url"], destination, artifact["checksum_sha256"])
            if artifact["unpack_to"] is not None:
                stage = f"artifact-unpack:{artifact['role']}"
                unpack_dataset(destination, input_dir / artifact["unpack_to"])
        launch_spec_path = input_dir / "launch-spec.json"
        launch_spec_path.write_text(
            json.dumps(request["launch_spec"], ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True),
            encoding="utf-8",
        )
        launch_spec_path.chmod(0o440)
        print(json.dumps({
            "root": str(root),
            "paths": {
                "input": str(input_dir),
                "output": str(output_dir),
                "launch_spec": str(launch_spec_path),
            },
        }, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
        return 0
    except Exception as error:
        print(f"training artifact staging failed at stage={stage} ({type(error).__name__})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
PY
