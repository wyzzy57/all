from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys

import yaml

from visiox_training.contracts import LaunchSpec
from visiox_training.runtime import (
    ArtifactManifestRefresher,
    load_fixed_launch_spec,
    load_runtime_inputs,
    require_string_env,
    run_worker_command,
)


ADAPTER_KEY = "llamafactory.llm_sft.v1"
ADAPTER_VERSION = "1.0.0"
LAUNCH_SPEC_PATH = Path("/workspace/input/launch-spec.json")
RUNTIME_CONFIG_PATH = "/workspace/output/.visiox-runtime/train.yaml"


def build_runtime_config(payload: dict[str, object]) -> dict[str, object]:
    spec = LaunchSpec.model_validate(payload)
    inputs = load_runtime_inputs(spec)
    artifacts = {item["role"]: item["path"] for item in inputs["artifacts"]}
    if artifacts.get("dataset") != "/workspace/dataset":
        raise ValueError("LLaMA-Factory dataset artifact is unavailable")
    dataset = inputs["dataset"]
    if dataset.get("format") not in {"alpaca", "sharegpt", "openai_messages"}:
        raise ValueError("LLaMA-Factory dataset format is unsupported")
    model = inputs["model"]
    model_id = model.get("runtime_id") or model.get("id")
    revision = model.get("revision")
    source = model.get("source")
    if not isinstance(model_id, str) or not model_id:
        raise ValueError("LLaMA-Factory model id is unavailable")
    if not isinstance(revision, str) or not revision:
        raise ValueError("LLaMA-Factory model revision is unavailable")
    if source not in {"huggingface", "modelscope", "model_registry"}:
        raise ValueError("LLaMA-Factory model source is unsupported")
    internal_fields = {
        "model_source",
        "model_id",
        "model_revision",
        "resolved_revision",
        "auto_optimize",
    }
    config = {
        key: value
        for key, value in inputs["parameters"].items()
        if key not in internal_fields and value is not None
    }
    config.update(
        {
            "model_name_or_path": model_id,
            "model_revision": revision,
            "dataset": "visiox_train",
            "dataset_dir": artifacts["dataset"],
            "output_dir": "/workspace/output/runtime",
            "logging_dir": "/workspace/output/runtime/runs",
            "report_to": "tensorboard",
            "overwrite_output_dir": True,
            "do_train": True,
            "plot_loss": True,
        }
    )
    return config


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
    inputs = load_runtime_inputs(spec)
    build_runtime_config(payload)
    environment = {**(base_environment or os.environ), **dict(spec.env)}
    if inputs["model"].get("source") == "modelscope":
        environment["USE_MODELSCOPE_HUB"] = "1"
    return (
        sys.executable,
        "-m",
        "visiox_llm_training_worker.entrypoint",
        RUNTIME_CONFIG_PATH,
    ), environment


def main() -> int:
    spec = load_fixed_launch_spec(
        LAUNCH_SPEC_PATH,
        adapter_key=ADAPTER_KEY,
        adapter_version=ADAPTER_VERSION,
    )
    config = build_runtime_config(spec.model_dump(mode="json"))
    runtime_config_path = Path(RUNTIME_CONFIG_PATH)
    runtime_config_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_config_path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    identity_source = Path("/workspace/dataset/visiox-run.json")
    if not identity_source.is_file():
        raise ValueError("LLaMA-Factory run identity artifact is unavailable")
    shutil.copyfile(identity_source, runtime_config_path.parent / "visiox-run.json")
    command, environment = build_worker_command(spec.model_dump(mode="json"))
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
