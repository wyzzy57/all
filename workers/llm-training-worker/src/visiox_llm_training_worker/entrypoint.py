from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any

import psutil
import yaml

from visiox_training.runtime import write_artifact_manifest

from visiox_llm_training_worker.config import apply_managed_config, load_run_identity
from visiox_llm_training_worker.resources import ResourceSampler
from visiox_llm_training_worker.telemetry import TelemetryRecorder


_MANAGED_ROOT = Path("/workspace")
_stop_requested = False


def main() -> int:
    global _stop_requested
    _stop_requested = False
    if len(sys.argv) != 2:
        print(
            "usage: python -m visiox_llm_training_worker.entrypoint CONFIG",
            file=sys.stderr,
        )
        return 2
    config_path = Path(sys.argv[1]).resolve()
    telemetry: TelemetryRecorder | None = None
    resources: ResourceSampler | None = None
    try:
        identity = load_run_identity(config_path)
        config = apply_managed_config(_load_config(config_path), identity)
        output_dir = _managed_output_dir(config)
        output_dir.mkdir(parents=True, exist_ok=True)
        managed_config_path = output_dir / "training_args.yaml"
        managed_config_path.write_text(
            yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        _install_signal_handlers()
        telemetry = TelemetryRecorder(output_dir, identity)
        resources = ResourceSampler(output_dir)
        resources.start()
        process = subprocess.Popen(
            ["llamafactory-cli", "train", str(managed_config_path)]
        )
        while process.poll() is None:
            latest = _latest_trainer_log(output_dir)
            telemetry.record(latest)
            resources.step = int(latest.get("current_steps", 0) or 0)
            _write_progress(
                output_dir,
                config,
                state="running",
                latest=latest,
                resource_sample=resources.latest,
                telemetry=telemetry,
            )
            if _stop_requested:
                process.terminate()
            time.sleep(5)
        exit_code = int(process.returncode or 0)
        state = (
            "canceled"
            if _stop_requested
            else "completed"
            if exit_code == 0
            else "failed"
        )
        latest = _latest_trainer_log(output_dir)
        telemetry.record(latest)
        resources.stop()
        telemetry.close(
            status="FINISHED"
            if exit_code == 0
            else "KILLED"
            if _stop_requested
            else "FAILED"
        )
        _write_progress(
            output_dir,
            config,
            state=state,
            complete=exit_code == 0,
            latest=latest,
            resource_sample=resources.latest,
            telemetry=telemetry,
        )
        if exit_code == 0:
            identity = load_run_identity(config_path)
            _write_artifact_manifest(
                output_dir,
                task_id=identity.training_job_id,
                adapter_key="llamafactory.llm_sft.v1",
                adapter_version="1.0.0",
            )
        return exit_code
    except Exception as exc:
        if resources is not None:
            resources.stop()
        if telemetry is not None:
            telemetry.close(status="FAILED")
        print(
            f"LLM training entrypoint failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1


def _load_config(path: Path) -> dict[str, Any]:
    if _MANAGED_ROOT not in path.parents or not path.is_file():
        raise ValueError("training config must be a managed workspace file")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("training config must be a YAML object")
    required = {
        "model_name_or_path",
        "dataset",
        "dataset_dir",
        "output_dir",
        "stage",
        "finetuning_type",
    }
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"training config is missing: {', '.join(missing)}")
    if payload.get("stage") != "sft" or payload.get("finetuning_type") != "lora":
        raise ValueError("the first VisiOX LLM runtime supports SFT LoRA/QLoRA only")
    return payload


def _managed_output_dir(config: dict[str, Any]) -> Path:
    output_dir = Path(str(config["output_dir"])).resolve()
    output_root = (_MANAGED_ROOT / "output").resolve()
    if output_root not in output_dir.parents:
        raise ValueError("output_dir must be inside /workspace/output")
    return output_dir


def _install_signal_handlers() -> None:
    def request_stop(_signum: int, _frame: Any) -> None:
        global _stop_requested
        _stop_requested = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)


def _latest_trainer_log(output_dir: Path) -> dict[str, Any]:
    candidates = sorted(output_dir.glob("**/trainer_log.jsonl"))
    if not candidates:
        return {}
    latest: dict[str, Any] = {}
    for line in candidates[-1].read_text(encoding="utf-8").splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            latest.update(payload)
    return latest


def _resource_snapshot() -> dict[str, float]:
    process = psutil.Process()
    metrics = {
        "system.cpu_percent": float(psutil.cpu_percent(interval=None)),
        "system.memory_percent": float(psutil.virtual_memory().percent),
        "system.memory_used_gb": float(process.memory_info().rss) / 1024**3,
    }
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        utilization, used, total = [
            float(item.strip()) for item in result.stdout.splitlines()[0].split(",")
        ]
        metrics.update(
            {
                "system.gpu_utilization_percent": utilization,
                "system.gpu_memory_used_gb": used / 1024,
                "system.gpu_memory_total_gb": total / 1024,
            }
        )
    except (OSError, ValueError, subprocess.SubprocessError, IndexError):
        pass
    return metrics


def _write_progress(
    output_dir: Path,
    config: dict[str, Any],
    *,
    state: str,
    complete: bool = False,
    latest: dict[str, Any] | None = None,
    resource_sample: dict[str, Any] | None = None,
    telemetry: TelemetryRecorder | None = None,
) -> None:
    latest = latest if latest is not None else _latest_trainer_log(output_dir)
    percent = float(latest.get("percentage", 100 if complete else 0) or 0)
    current_steps = int(latest.get("current_steps", 0) or 0)
    total_steps = int(latest.get("total_steps", 0) or 0)
    if "learning_rate" not in latest and isinstance(latest.get("lr"), int | float):
        latest["learning_rate"] = latest["lr"]
    scalar_keys = ("loss", "eval_loss", "learning_rate", "epoch", "grad_norm")
    metrics = {
        key: float(latest[key])
        for key in scalar_keys
        if isinstance(latest.get(key), int | float)
    }
    snapshot = {
        "engine": "llamafactory",
        "state": state,
        "progress": {
            "current_step": current_steps,
            "total_steps": total_steps,
            "percent": max(0.0, min(100.0, percent)),
            "epoch": metrics.get("epoch", 0.0),
            "current_epoch": metrics.get("epoch", 0.0),
            "total_epochs": float(config.get("num_train_epochs", 0) or 0),
        },
        "timing": {"updated_at": datetime.now(UTC).isoformat()},
        "latest_metrics": metrics,
        "resources": [resource_sample or _resource_snapshot()],
        "availability": {
            "mlflow": {
                "available": telemetry.mlflow_available
                if telemetry is not None
                else False,
                "reason": telemetry.mlflow_reason
                if telemetry is not None
                else "not initialized",
            },
            "tensorboard": {"available": True, "reason": None},
        },
    }
    path = output_dir / "visiox-progress.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(snapshot, ensure_ascii=True), encoding="utf-8")
    temporary.replace(path)


def _write_artifact_manifest(
    output_dir: Path,
    *,
    task_id: str,
    adapter_key: str,
    adapter_version: str,
) -> None:
    write_artifact_manifest(
        output_dir,
        task_id=task_id,
        adapter_key=adapter_key,
        adapter_version=adapter_version,
    )


if __name__ == "__main__":
    raise SystemExit(main())
