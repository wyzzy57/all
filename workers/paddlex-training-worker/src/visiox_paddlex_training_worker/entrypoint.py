from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
from threading import Event
import time
import traceback
from typing import Any, Callable

from visiox_training.runtime import load_fixed_launch_spec, write_artifact_manifest

from visiox_paddlex_training_worker.artifacts import write_train_result
from visiox_paddlex_training_worker.config import (
    ADAPTER_KEY,
    ADAPTER_VERSION,
    BEST_INFERENCE_DIR,
    build_training_command,
    build_training_config,
)
from visiox_paddlex_training_worker.resources import ResourceSampler
from visiox_paddlex_training_worker.telemetry import TelemetryRecorder


UTC = timezone.utc
LAUNCH_SPEC_PATH = Path("/workspace/input/launch-spec.json")
OUTPUT_DIR = Path("/workspace/output")
CHECKPOINT_DIR = Path("/workspace/checkpoint")
_TERM_GRACE_SECONDS = 10.0
_KILL_GRACE_SECONDS = 5.0


def run_training(
    payload: dict[str, object],
    *,
    output_dir: Path = OUTPUT_DIR,
    popen_factory: Callable[..., Any] = subprocess.Popen,
    stop_event: Event | None = None,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    mlflow_module: Any | None = None,
    writer_factory: Callable[[str], Any] | None = None,
) -> int:
    config = build_training_config(payload)
    command = build_training_command(config)
    environment = dict(payload.get("env", {}))
    task_id = str(environment["VISIOX_TRAINING_JOB_ID"])
    output_dir.mkdir(parents=True, exist_ok=True)
    if config.resume:
        _prepare_resume_checkpoint(output_dir)
    stop_event = stop_event or Event()
    telemetry = TelemetryRecorder(
        output_dir,
        run_name=f"visiox-{task_id}",
        tags={"visiox.training_job_id": task_id, "visiox.framework": "paddlex"},
        mlflow_tracking_uri=environment.get("MLFLOW_TRACKING_URI"),
        mlflow_module=mlflow_module,
        writer_factory=writer_factory,
    )
    resources = ResourceSampler(output_dir)
    resources.start()
    stdout_path = output_dir / "stdout.log"
    stderr_path = output_dir / "stderr.log"
    offset = 0
    exit_code = 1
    state = "failed"
    try:
        with stdout_path.open("a", encoding="utf-8", buffering=1) as stdout, (
            stderr_path.open("a", encoding="utf-8", buffering=1)
        ) as stderr:
            managed_environment = {
                **os.environ,
                **environment,
                "VISIOX_PADDLEX_IMAGE_SIZE": str(config.image_size),
                "VISIOX_PADDLEX_WORKERS": str(config.workers),
                "VISIOX_PADDLEX_EVAL_DIR": str(output_dir / "evaluation"),
            }

            def update_running_state() -> None:
                nonlocal offset
                stdout.flush()
                offset = _tail_log(stdout_path, offset, telemetry)
                resources.step = telemetry.step
                _write_progress(
                    output_dir,
                    config,
                    telemetry,
                    resources.latest,
                    state="running",
                )

            exit_code = _run_command(
                command,
                environment=managed_environment,
                stdout=stdout,
                stderr=stderr,
                popen_factory=popen_factory,
                stop_event=stop_event,
                on_tick=update_running_state,
                sleep=sleep,
                monotonic=monotonic,
            )
            if exit_code == 0 and not stop_event.is_set():
                if not (output_dir / "best_model" / "best_model.pdparams").is_file():
                    stderr.write(
                        "PaddleX training did not create best_model/best_model.pdparams\n"
                    )
                    stderr.flush()
                    exit_code = 1
                elif not _static_bundle_is_complete(output_dir):
                    stderr.write(
                        "PaddleX training did not create a complete best-model static inference bundle\n"
                    )
                    stderr.flush()
                    exit_code = 1
        state = (
            "canceled"
            if stop_event.is_set()
            else "completed"
            if exit_code == 0
            else "failed"
        )
        return exit_code
    finally:
        final_status = (
            "FINISHED"
            if state == "completed"
            else "KILLED"
            if state == "canceled"
            else "FAILED"
        )
        cleanup_steps = (
            ("tail stdout", lambda: _tail_log(stdout_path, offset, telemetry)),
            ("stop resource sampler", resources.stop),
            ("close telemetry", lambda: telemetry.close(status=final_status)),
            (
                "write final progress",
                lambda: _write_progress(
                    output_dir,
                    config,
                    telemetry,
                    resources.latest,
                    state=state,
                ),
            ),
            (
                "write training result",
                lambda: write_train_result(
                    output_dir,
                    status=state,
                    exit_code=exit_code,
                ),
            ),
        )
        for stage, action in cleanup_steps:
            try:
                action()
            except Exception as exc:
                _append_cleanup_error(stderr_path, stage, exc)
        write_artifact_manifest(
            output_dir,
            task_id=task_id,
            adapter_key=ADAPTER_KEY,
            adapter_version=ADAPTER_VERSION,
        )


def _append_cleanup_error(path: Path, stage: str, exc: Exception) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(f"PaddleX worker cleanup failed during {stage}: {exc}\n")


def _run_command(
    command: tuple[str, ...],
    *,
    environment: dict[str, str],
    stdout: Any,
    stderr: Any,
    popen_factory: Callable[..., Any],
    stop_event: Event,
    on_tick: Callable[[], None],
    sleep: Callable[[float], None],
    monotonic: Callable[[], float],
) -> int:
    process = popen_factory(
        command,
        env=environment,
        stdout=stdout,
        stderr=stderr,
        text=True,
        start_new_session=True,
    )
    termination_deadline: float | None = None
    kill_deadline: float | None = None
    try:
        while True:
            return_code = process.poll()
            on_tick()
            if return_code is not None:
                return int(return_code)
            if stop_event.is_set():
                now = monotonic()
                if termination_deadline is None:
                    _signal_process_tree(process, force=False)
                    termination_deadline = now + _TERM_GRACE_SECONDS
                elif kill_deadline is None and now >= termination_deadline:
                    _signal_process_tree(process, force=True)
                    kill_deadline = monotonic() + _KILL_GRACE_SECONDS
                elif kill_deadline is not None and now >= kill_deadline:
                    return -9
            sleep(1)
    finally:
        if process.poll() is None:
            _stop_process_tree(process)


def _prepare_resume_checkpoint(
    output_dir: Path,
    *,
    checkpoint_dir: Path = CHECKPOINT_DIR,
) -> Path:
    source = checkpoint_dir / "last.pt"
    if not source.is_file():
        raise FileNotFoundError(f"resume checkpoint is missing: {source}")
    resume_dir = output_dir / ".resume"
    resume_dir.mkdir(parents=True, exist_ok=True)
    prefix = resume_dir / "last"
    shutil.copy2(source, prefix.with_suffix(".pdparams"))
    for suffix in (".pdopt", ".pdema"):
        companion = checkpoint_dir / f"last{suffix}"
        if companion.is_file():
            shutil.copy2(companion, prefix.with_suffix(suffix))
    return prefix


def _static_bundle_is_complete(output_dir: Path) -> bool:
    inference_dir = output_dir / Path(BEST_INFERENCE_DIR).relative_to(OUTPUT_DIR)
    graph_exists = (inference_dir / "inference.json").is_file() or (
        inference_dir / "inference.pdmodel"
    ).is_file()
    return graph_exists and (inference_dir / "inference.pdiparams").is_file()


def _tail_log(path: Path, offset: int, telemetry: TelemetryRecorder) -> int:
    if not path.is_file():
        return offset
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        stream.seek(offset)
        for line in stream:
            telemetry.record_line(line)
        return stream.tell()


def _write_progress(
    output_dir: Path,
    config: Any,
    telemetry: TelemetryRecorder,
    resource_sample: dict[str, Any],
    *,
    state: str,
) -> None:
    total_steps = telemetry.total_steps * config.epochs
    percent = (
        100.0
        if state == "completed"
        else telemetry.step * 100.0 / total_steps
        if total_steps > 0
        else telemetry.epoch * 100.0 / config.epochs
        if config.epochs > 0
        else 0.0
    )
    payload = {
        "engine": "paddlex",
        "state": state,
        "progress": {
            "current_step": telemetry.step,
            "total_steps": total_steps,
            "current_epoch": telemetry.epoch,
            "total_epochs": config.epochs,
            "percent": max(0.0, min(100.0, percent)),
        },
        "timing": {"updated_at": datetime.now(UTC).isoformat()},
        "latest_metrics": telemetry.latest_metrics,
        "resources": [resource_sample],
        "availability": {
            "mlflow": {
                "available": telemetry.mlflow_available,
                "reason": telemetry.mlflow_reason,
            },
            "tensorboard": {
                "available": telemetry.tensorboard_available,
                "reason": telemetry.tensorboard_reason,
            },
        },
    }
    path = output_dir / "visiox-progress.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(path)


def _install_signal_handlers(stop_event: Event) -> None:
    def request_stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)


def _signal_process_tree(process: Any, *, force: bool) -> None:
    method_name = "kill" if force else "terminate"
    pid = getattr(process, "pid", None)
    if isinstance(pid, int) and pid > 0 and os.name != "nt":
        try:
            os.killpg(pid, signal.SIGKILL if force else signal.SIGTERM)
            return
        except ProcessLookupError:
            return
    method = getattr(process, method_name, None)
    if callable(method):
        method()


def _stop_process_tree(process: Any) -> None:
    _signal_process_tree(process, force=False)
    wait = getattr(process, "wait", None)
    if not callable(wait):
        return
    try:
        wait(timeout=10)
    except subprocess.TimeoutExpired:
        _signal_process_tree(process, force=True)
        try:
            wait(timeout=_KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            return


def main() -> int:
    stop_event = Event()
    _install_signal_handlers(stop_event)
    spec = None
    try:
        spec = load_fixed_launch_spec(
            LAUNCH_SPEC_PATH,
            adapter_key=ADAPTER_KEY,
            adapter_version=ADAPTER_VERSION,
        )
        return run_training(spec.model_dump(mode="json"), stop_event=stop_event)
    except Exception:
        task_id = (
            str(spec.env.get("VISIOX_TRAINING_JOB_ID", "unknown"))
            if spec is not None
            else "unknown"
        )
        _write_bootstrap_failure(task_id)
        raise


def _write_bootstrap_failure(task_id: str) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "stdout.log").touch(exist_ok=True)
    (OUTPUT_DIR / "visiox-metrics.jsonl").touch(exist_ok=True)
    (OUTPUT_DIR / "resource_metrics.jsonl").touch(exist_ok=True)
    with (OUTPUT_DIR / "stderr.log").open("a", encoding="utf-8") as stream:
        traceback.print_exc(file=stream)
    progress_path = OUTPUT_DIR / "visiox-progress.json"
    temporary = progress_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(
            {
                "engine": "paddlex",
                "state": "failed",
                "progress": {"percent": 0.0},
                "timing": {"updated_at": datetime.now(UTC).isoformat()},
            },
            ensure_ascii=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    temporary.replace(progress_path)
    write_train_result(OUTPUT_DIR, status="failed", exit_code=1)
    write_artifact_manifest(
        OUTPUT_DIR,
        task_id=task_id,
        adapter_key=ADAPTER_KEY,
        adapter_version=ADAPTER_VERSION,
    )


if __name__ == "__main__":
    raise SystemExit(main())
