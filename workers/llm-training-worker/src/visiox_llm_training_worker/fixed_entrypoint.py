from __future__ import annotations

import os
from pathlib import Path
import sys

from visiox_training.contracts import LaunchSpec
from visiox_training.runtime import (
    load_fixed_launch_spec,
    require_string_env,
    run_worker_command,
    write_artifact_manifest,
)


ADAPTER_KEY = "llamafactory.llm_sft.v1"
ADAPTER_VERSION = "1.0.0"
LAUNCH_SPEC_PATH = Path("/workspace/input/launch-spec.json")


def build_worker_command(
    payload: dict[str, object],
    *,
    base_environment: dict[str, str] | None = None,
) -> tuple[tuple[str, ...], dict[str, str]]:
    spec = LaunchSpec.model_validate(payload)
    if spec.adapter_key != ADAPTER_KEY or spec.adapter_version != ADAPTER_VERSION:
        raise ValueError("launch spec adapter does not match LLaMA-Factory")
    if tuple(spec.argv) != ("/usr/local/bin/visiox-train",):
        raise ValueError("LLaMA-Factory runtime only accepts the fixed entrypoint")
    config_path = require_string_env(spec, "VISIOX_LLM_CONFIG_PATH")
    environment = {**(base_environment or os.environ), **dict(spec.env)}
    return (
        sys.executable,
        "-m",
        "visiox_llm_training_worker.entrypoint",
        config_path,
    ), environment


def main() -> int:
    spec = load_fixed_launch_spec(
        LAUNCH_SPEC_PATH,
        adapter_key=ADAPTER_KEY,
        adapter_version=ADAPTER_VERSION,
    )
    command, environment = build_worker_command(spec.model_dump(mode="json"))
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
