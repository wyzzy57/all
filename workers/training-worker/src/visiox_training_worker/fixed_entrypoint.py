from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

from visiox_training.contracts import LaunchSpec
from visiox_training.runtime import (
    ArtifactManifestRefresher,
    load_fixed_launch_spec,
    load_runtime_inputs,
    require_string_env,
    run_worker_command,
)
from visiox_yolo26.training.commands import build_train_command


ADAPTER_KEY = "ultralytics.object_detection.v1"
ADAPTER_VERSION = "1.0.0"
LAUNCH_SPEC_PATH = Path("/workspace/input/launch-spec.json")


def build_worker_command(payload: dict[str, object]) -> tuple[str, ...]:
    spec = LaunchSpec.model_validate(payload)
    if spec.adapter_key != ADAPTER_KEY or spec.adapter_version != ADAPTER_VERSION:
        raise ValueError("launch spec adapter does not match Ultralytics")
    if tuple(spec.argv) != ("/usr/local/bin/visiox-train",):
        raise ValueError("Ultralytics runtime only accepts the fixed entrypoint")
    inputs = load_runtime_inputs(spec)
    artifacts = {item["role"]: item["path"] for item in inputs["artifacts"]}
    if artifacts.get("model") != "/workspace/model/base.pt":
        raise ValueError("Ultralytics model artifact is unavailable")
    if artifacts.get("dataset") != "/workspace/dataset":
        raise ValueError("Ultralytics dataset artifact is unavailable")
    dataset = inputs["dataset"]
    if dataset.get("format") not in {"yolo", "coco"}:
        raise ValueError("Ultralytics dataset format is unsupported")
    parameters = dict(inputs["parameters"])
    parameters.pop("device", None)
    if any(
        key in parameters for key in {"model", "data", "project", "name", "exist_ok"}
    ):
        raise ValueError("Ultralytics managed arguments cannot be overridden")
    translated = build_train_command(
        base_model_path=PurePosixPath(artifacts["model"]),  # type: ignore[arg-type]
        data_yaml_path=PurePosixPath(f"{artifacts['dataset']}/data.yaml"),  # type: ignore[arg-type]
        params=parameters,
        project_dir=PurePosixPath("/workspace/output/runs"),  # type: ignore[arg-type]
        run_name=f"job-{require_string_env(spec, 'VISIOX_TRAINING_JOB_ID')}",
    )
    arguments = translated.argv[2:]
    if any(any(character in item for character in "\x00\r\n") for item in arguments):
        raise ValueError("Ultralytics translated arguments are invalid")
    return (
        "torchrun",
        f"--nnodes={int(require_string_env(spec, 'VISIOX_NNODES'))}",
        f"--nproc-per-node={int(require_string_env(spec, 'VISIOX_NPROC_PER_NODE'))}",
        f"--node-rank={int(require_string_env(spec, 'VISIOX_NODE_RANK'))}",
        f"--master-addr={require_string_env(spec, 'VISIOX_MASTER_ADDR')}",
        f"--master-port={int(require_string_env(spec, 'VISIOX_MASTER_PORT'))}",
        "-m",
        "visiox_training_worker.train_entrypoint",
        *arguments,
    )


def main() -> int:
    spec = load_fixed_launch_spec(
        LAUNCH_SPEC_PATH,
        adapter_key=ADAPTER_KEY,
        adapter_version=ADAPTER_VERSION,
    )
    command = build_worker_command(spec.model_dump(mode="json"))
    environment = {**os.environ, **dict(spec.env)}
    output_dir = Path(require_string_env(spec, "VISIOX_OUTPUT_DIR"))

    manifest_refresher = ArtifactManifestRefresher(
        output_dir,
        task_id=require_string_env(spec, "VISIOX_TRAINING_JOB_ID"),
        adapter_key=ADAPTER_KEY,
        adapter_version=ADAPTER_VERSION,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_refresher.refresh(force=True, strict=False)
    return_code = run_worker_command(
        command,
        environment=environment,
        on_poll=manifest_refresher.refresh,
    )
    manifest_refresher.refresh(force=True, strict=True)
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
