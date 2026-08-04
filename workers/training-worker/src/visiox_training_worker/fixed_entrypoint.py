from __future__ import annotations

import json
import os
from pathlib import Path

from visiox_training.contracts import LaunchSpec
from visiox_training.runtime import (
    load_fixed_launch_spec,
    require_string_env,
    run_worker_command,
    write_artifact_manifest,
)


ADAPTER_KEY = "ultralytics.object_detection.v1"
ADAPTER_VERSION = "1.0.0"
LAUNCH_SPEC_PATH = Path("/workspace/input/launch-spec.json")


def build_worker_command(payload: dict[str, object]) -> tuple[str, ...]:
    spec = LaunchSpec.model_validate(payload)
    if spec.adapter_key != ADAPTER_KEY or spec.adapter_version != ADAPTER_VERSION:
        raise ValueError("launch spec adapter does not match Ultralytics")
    if tuple(spec.argv) != ("/usr/local/bin/visiox-train",):
        raise ValueError("Ultralytics runtime only accepts the fixed entrypoint")
    arguments = json.loads(require_string_env(spec, "VISIOX_TRAINING_ARGUMENTS_JSON"))
    if not isinstance(arguments, list) or any(
        not isinstance(item, str)
        or not item
        or any(character in item for character in "\x00\r\n")
        for item in arguments
    ):
        raise ValueError("Ultralytics training arguments are invalid")
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

    def refresh_manifest() -> None:
        write_artifact_manifest(
            output_dir,
            task_id=require_string_env(spec, "VISIOX_TRAINING_JOB_ID"),
            adapter_key=ADAPTER_KEY,
            adapter_version=ADAPTER_VERSION,
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    refresh_manifest()
    return_code = run_worker_command(
        command,
        environment=environment,
        on_poll=refresh_manifest,
    )
    refresh_manifest()
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
