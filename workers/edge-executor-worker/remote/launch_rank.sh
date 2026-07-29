#!/usr/bin/env bash
set -euo pipefail

exec python3 - "$@" <<'PY'
import json
import hashlib
import os
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
COLLECTABLE_ARTIFACTS = {
    "best.pt",
    "last.pt",
    "results.csv",
    "results.png",
    "confusion_matrix.png",
    "confusion_matrix_normalized.png",
    "BoxPR_curve.png",
    "BoxP_curve.png",
    "BoxR_curve.png",
    "BoxF1_curve.png",
    "labels.jpg",
    "train_batch0.jpg",
    "train_batch1.jpg",
    "train_batch2.jpg",
    "val_batch0_labels.jpg",
    "val_batch0_pred.jpg",
    "val_batch1_labels.jpg",
    "val_batch1_pred.jpg",
    "val_batch2_labels.jpg",
    "val_batch2_pred.jpg",
    "visiox-progress.json",
    "events.out.tfevents.remote",
    "adapter_model.safetensors",
    "adapter_config.json",
    "trainer_state.json",
    "trainer_log.jsonl",
    "train_results.json",
    "all_results.json",
    "training_args.yaml",
    "artifact-manifest.json",
    "visiox-metrics.jsonl",
    "resource_metrics.jsonl",
}
ENGINES = {"yolo26", "llamafactory"}


class RequestValidationError(ValueError):
    def __init__(self, code):
        super().__init__("invalid distributed rank request")
        self.code = code


def invalid(code):
    raise RequestValidationError(code)


def validate(request):
    if isinstance(request, dict) and request.get("action") == "inspect":
        if set(request) != {"action", "container_id"} or not isinstance(request["container_id"], str) or not CONTAINER_ID.fullmatch(request["container_id"]):
            invalid("inspect-request")
        return request
    if isinstance(request, dict) and request.get("action") == "logs":
        if set(request) != {"action", "container_id"} or not isinstance(request["container_id"], str) or not CONTAINER_ID.fullmatch(request["container_id"]):
            invalid("logs-request")
        return request
    if isinstance(request, dict) and request.get("action") == "collect":
        if set(request) != {"action", "output_path", "uploads"} or not isinstance(request["output_path"], str) or not Path(request["output_path"]).is_absolute():
            invalid("collect-request")
        uploads = request["uploads"]
        if not isinstance(uploads, dict) or not uploads or set(uploads) - COLLECTABLE_ARTIFACTS:
            invalid("collect-uploads")
        for url in uploads.values():
            parsed = urlsplit(url) if isinstance(url, str) else None
            if parsed is None or parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
                invalid("collect-url")
        return request
    expected = {"run_id", "attempt", "engine", "image_digest", "node_id", "gpu_uuids", "node_rank", "nnodes", "nproc_per_node", "master_addr", "master_port", "training_arguments", "paths"}
    expected_shape = expected | {"action"}
    if isinstance(request, dict) and request.get("engine") == "llamafactory":
        expected_shape.add("model_source")
    if not isinstance(request, dict) or request.get("action") != "launch" or set(request) != expected_shape:
        invalid("launch-shape")
    for key in ("run_id", "node_id"):
        if not isinstance(request[key], str) or not IDENTIFIER.fullmatch(request[key]):
            invalid("identifier")
    if not isinstance(request["image_digest"], str) or not IMAGE_DIGEST.fullmatch(request["image_digest"]):
        invalid("image-digest")
    if request["engine"] not in ENGINES:
        invalid("engine")
    if request["engine"] == "llamafactory" and request["model_source"] not in {"huggingface", "modelscope"}:
        invalid("model-source")
    integer_limits = {"attempt": (1, 100000), "node_rank": (0, 1023), "nnodes": (1, 1024), "nproc_per_node": (1, 64), "master_port": (1024, 65535)}
    for key, (minimum, maximum) in integer_limits.items():
        value = request[key]
        if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
            invalid("integer-field")
    if request["node_rank"] >= request["nnodes"]:
        invalid("rank-layout")
    gpus = request["gpu_uuids"]
    if not isinstance(gpus, list) or len(gpus) != request["nproc_per_node"] or len(set(gpus)) != len(gpus) or any(not isinstance(item, str) or not GPU_UUID.fullmatch(item) for item in gpus):
        invalid("gpu-layout")
    if not isinstance(request["master_addr"], str) or not LAN_ADDRESS.fullmatch(request["master_addr"]) or ".." in request["master_addr"]:
        invalid("master-address")
    arguments = request["training_arguments"]
    if not isinstance(arguments, list) or (request["engine"] == "yolo26" and not arguments) or any(not isinstance(item, str) or not item or any(char in item for char in "\x00\r\n") for item in arguments):
        invalid("training-arguments")
    paths = request["paths"]
    valid_path_shapes = (
        ({"model", "dataset", "output"}, {"model", "dataset", "checkpoint", "output"})
        if request["engine"] == "yolo26"
        else ({"dataset", "output"}, {"dataset", "checkpoint", "output"})
    )
    if not isinstance(paths, dict) or set(paths) not in valid_path_shapes:
        invalid("paths-shape")
    for value in paths.values():
        if not isinstance(value, str) or not Path(value).is_absolute() or any(char in value for char in "\x00\r\n"):
            invalid("path")
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
        invalid("inspect-state")
    exit_code = state.get("ExitCode")
    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
        invalid("inspect-exit-code")
    return {"running": state["Running"], "exit_code": exit_code}


def collect_logs(container_id):
    result = subprocess.run(
        ["docker", "logs", "--timestamps", container_id],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return {"stdout": result.stdout, "stderr": result.stderr}


def collect_artifacts(output_path, uploads):
    root = Path(output_path).resolve()
    results = {}
    for name, url in uploads.items():
        pattern = "**/events.out.tfevents.*" if name == "events.out.tfevents.remote" else f"**/{name}"
        matches = sorted(root.glob(pattern))
        if not matches:
            if name in {"best.pt", "last.pt"}:
                raise ValueError("required training artifact is missing")
            continue
        path = (
            max(matches, key=lambda candidate: candidate.stat().st_size)
            if name == "events.out.tfevents.remote"
            else matches[-1]
        ).resolve()
        if root not in path.parents:
            invalid("collect-path")
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
        if request["action"] == "collect":
            stage = "artifact-collect"
            artifacts = collect_artifacts(request["output_path"], request["uploads"])
            print(json.dumps({"artifacts": artifacts}, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
            return 0
        name = f"visiox-train-{request['run_id']}-{request['attempt']}-rank-{request['node_rank']}"
        stage = "previous-container-cleanup"
        subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True, text=True, timeout=30)
        command = [
            "docker", "run", "-d", "--name", name,
            "--user", f"{os.getuid()}:{os.getgid()}",
            "--network", "host", "--shm-size", "4g",
            "--gpus", "device=" + ",".join(request["gpu_uuids"]),
            "--label", "com.visiox.managed=true",
            "--label", f"com.visiox.training-run-id={request['run_id']}",
            "--label", f"com.visiox.training-attempt={request['attempt']}",
            "--label", f"com.visiox.training-node-rank={request['node_rank']}",
            "--mount", f"type=bind,src={request['paths']['dataset']},dst=/workspace/dataset",
            "--mount", f"type=bind,src={request['paths']['output']},dst=/workspace/output",
            "-e", "HOME=/tmp",
            "-e", "USER=visiox-edge",
            "-e", "LOGNAME=visiox-edge",
            "-e", "YOLO_CONFIG_DIR=/tmp",
            "-e", "MPLCONFIGDIR=/tmp",
            "-e", "TORCHINDUCTOR_CACHE_DIR=/tmp/torchinductor",
            "-e", f"VISIOX_DISTRIBUTED_RUN_ID={request['run_id']}",
            "-e", f"VISIOX_DISTRIBUTED_ATTEMPT={request['attempt']}",
        ]
        if request["engine"] == "yolo26":
            command.extend(["--mount", f"type=bind,src={request['paths']['model']},dst=/workspace/model/base.pt,readonly"])
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
        else:
            cache_root = Path.home() / ".cache" / "visiox" / "models"
            cache_root.mkdir(mode=0o750, parents=True, exist_ok=True)
            command.extend([
                "--mount", f"type=bind,src={cache_root},dst=/workspace/model-cache",
                "-e", "HF_HOME=/workspace/model-cache/huggingface",
                "-e", "MODELSCOPE_CACHE=/workspace/model-cache/modelscope",
            ])
            if request["model_source"] == "modelscope":
                command.extend(["-e", "USE_MODELSCOPE_HUB=1"])
            command.extend([
                request["image_digest"],
                "python", "-m", "visiox_llm_training_worker.entrypoint",
                "/workspace/dataset/train.yaml",
            ])
        stage = "container-launch"
        result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=60)
        container_id = result.stdout.strip()
        if not CONTAINER_ID.fullmatch(container_id):
            invalid("container-id")
        print(json.dumps({"container_id": container_id, "container_name": name, "node_rank": request["node_rank"]}, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
        return 0
    except Exception as error:
        detail = f":{error.code}" if isinstance(error, RequestValidationError) else ""
        print(
            f"distributed rank operation failed at stage={stage}{detail} ({type(error).__name__})",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
PY
