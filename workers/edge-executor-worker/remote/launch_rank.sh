#!/usr/bin/env bash
set -euo pipefail

exec python3 - "$@" <<'PY'
import json
import hashlib
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}\Z")
IMAGE_DIGEST = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,430}@sha256:[a-f0-9]{64}\Z")
GPU_UUID = re.compile(r"GPU-[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z")
CONTAINER_ID = re.compile(r"[a-f0-9]{12,64}\Z")
LAN_ADDRESS = re.compile(r"[A-Za-z0-9][A-Za-z0-9.:-]{0,253}\Z")


def invalid():
    raise ValueError("invalid distributed rank request")


def validate(request):
    if isinstance(request, dict) and request.get("action") == "inspect":
        if set(request) != {"action", "container_id"} or not isinstance(request["container_id"], str) or not CONTAINER_ID.fullmatch(request["container_id"]):
            invalid()
        return request
    if isinstance(request, dict) and request.get("action") == "collect":
        if set(request) != {"action", "output_path", "uploads"} or not isinstance(request["output_path"], str) or not Path(request["output_path"]).is_absolute():
            invalid()
        uploads = request["uploads"]
        if not isinstance(uploads, dict) or not uploads or set(uploads) - {"best.pt", "last.pt", "results.csv", "results.png"}:
            invalid()
        for url in uploads.values():
            parsed = urlsplit(url) if isinstance(url, str) else None
            if parsed is None or parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
                invalid()
        return request
    expected = {"run_id", "attempt", "image_digest", "node_id", "gpu_uuids", "node_rank", "nnodes", "nproc_per_node", "master_addr", "master_port", "training_arguments", "paths"}
    if not isinstance(request, dict) or request.get("action") != "launch" or set(request) != expected | {"action"}:
        invalid()
    for key in ("run_id", "node_id"):
        if not isinstance(request[key], str) or not IDENTIFIER.fullmatch(request[key]):
            invalid()
    if not isinstance(request["image_digest"], str) or not IMAGE_DIGEST.fullmatch(request["image_digest"]):
        invalid()
    integer_limits = {"attempt": (1, 100000), "node_rank": (0, 1023), "nnodes": (1, 1024), "nproc_per_node": (1, 64), "master_port": (1024, 65535)}
    for key, (minimum, maximum) in integer_limits.items():
        value = request[key]
        if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
            invalid()
    if request["node_rank"] >= request["nnodes"]:
        invalid()
    gpus = request["gpu_uuids"]
    if not isinstance(gpus, list) or len(gpus) != request["nproc_per_node"] or len(set(gpus)) != len(gpus) or any(not isinstance(item, str) or not GPU_UUID.fullmatch(item) for item in gpus):
        invalid()
    if not isinstance(request["master_addr"], str) or not LAN_ADDRESS.fullmatch(request["master_addr"]) or ".." in request["master_addr"]:
        invalid()
    arguments = request["training_arguments"]
    if not isinstance(arguments, list) or not arguments or any(not isinstance(item, str) or not item or any(char in item for char in "\x00\r\n") for item in arguments):
        invalid()
    paths = request["paths"]
    if not isinstance(paths, dict) or set(paths) not in ({"model", "dataset", "output"}, {"model", "dataset", "checkpoint", "output"}):
        invalid()
    for value in paths.values():
        if not isinstance(value, str) or not Path(value).is_absolute() or any(char in value for char in "\x00\r\n"):
            invalid()
    return request


def inspect_container(container_id):
    result = subprocess.run(
        ["docker", "inspect", "--format", "{{json .State}}", container_id],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    state = json.loads(result.stdout)
    if not isinstance(state, dict) or not isinstance(state.get("Running"), bool):
        invalid()
    exit_code = state.get("ExitCode")
    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
        invalid()
    return {"running": state["Running"], "exit_code": exit_code}


def collect_artifacts(output_path, uploads):
    root = Path(output_path).resolve()
    results = {}
    for name, url in uploads.items():
        matches = sorted(root.glob(f"runs/**/{name}"))
        if not matches:
            if name in {"best.pt", "last.pt"}:
                raise ValueError("required training artifact is missing")
            continue
        path = matches[-1].resolve()
        if root not in path.parents:
            invalid()
        payload = path.read_bytes()
        request = Request(url, data=payload, method="PUT", headers={"Content-Type": "application/octet-stream"})
        with urlopen(request, timeout=600) as response:
            if not 200 <= int(response.status) < 300:
                raise ValueError("training artifact upload failed")
        results[name] = {"checksum": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload)}
    return results


def main():
    if len(sys.argv) != 2:
        return 2
    try:
        with open(sys.argv[1], "r", encoding="utf-8") as source:
            request = validate(json.load(source))
        if request["action"] == "inspect":
            print(json.dumps(inspect_container(request["container_id"]), ensure_ascii=True, separators=(",", ":"), sort_keys=True))
            return 0
        if request["action"] == "collect":
            artifacts = collect_artifacts(request["output_path"], request["uploads"])
            print(json.dumps({"artifacts": artifacts}, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
            return 0
        name = f"visiox-train-{request['run_id']}-{request['attempt']}-rank-{request['node_rank']}"
        subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True, text=True, timeout=30)
        command = [
            "docker", "run", "-d", "--name", name,
            "--network", "host", "--shm-size", "4g",
            "--gpus", "device=" + ",".join(request["gpu_uuids"]),
            "--label", "com.visiox.managed=true",
            "--label", f"com.visiox.training-run-id={request['run_id']}",
            "--label", f"com.visiox.training-attempt={request['attempt']}",
            "--label", f"com.visiox.training-node-rank={request['node_rank']}",
            "--mount", f"type=bind,src={request['paths']['model']},dst=/workspace/model/base.pt,readonly",
            "--mount", f"type=bind,src={request['paths']['dataset']},dst=/workspace/dataset,readonly",
            "--mount", f"type=bind,src={request['paths']['output']},dst=/workspace/output",
            "-e", "YOLO_CONFIG_DIR=/tmp/visiox-ultralytics",
            "-e", f"VISIOX_DISTRIBUTED_RUN_ID={request['run_id']}",
            "-e", f"VISIOX_DISTRIBUTED_ATTEMPT={request['attempt']}",
        ]
        if "checkpoint" in request["paths"]:
            command.extend(["--mount", f"type=bind,src={request['paths']['checkpoint']},dst=/workspace/checkpoint/last.pt,readonly"])
        command.extend([
            request["image_digest"],
            "torchrun",
            f"--nnodes={request['nnodes']}",
            f"--nproc-per-node={request['nproc_per_node']}",
            f"--node-rank={request['node_rank']}",
            f"--master-addr={request['master_addr']}",
            f"--master-port={request['master_port']}",
            "-m", "visiox_training_worker.train_entrypoint",
            *request["training_arguments"],
        ])
        result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=60)
        container_id = result.stdout.strip()
        if not CONTAINER_ID.fullmatch(container_id):
            invalid()
        print(json.dumps({"container_id": container_id, "container_name": name, "node_rank": request["node_rank"]}, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
        return 0
    except Exception:
        print("distributed rank launch failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
PY
