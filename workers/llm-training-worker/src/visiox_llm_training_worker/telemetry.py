from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any, Callable

from visiox_llm_training_worker.config import RunIdentity


_ALIASES = {
    "lr": "learning_rate",
    "train_loss": "loss",
    "train_runtime": "runtime_seconds",
    "train_samples_per_second": "samples_per_second",
    "train_steps_per_second": "steps_per_second",
}


class TelemetryRecorder:
    def __init__(
        self,
        output_dir: Path,
        identity: RunIdentity,
        *,
        mlflow_module: Any | None = None,
        writer_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.output_dir = output_dir
        self.identity = identity
        self.metrics_path = output_dir / "visiox-metrics.jsonl"
        self._last_signature: tuple[int, tuple[tuple[str, float], ...]] | None = None
        self._mlflow = mlflow_module if mlflow_module is not None else _optional_mlflow()
        self._writer = _create_writer(output_dir, writer_factory)
        self.mlflow_available = False
        self.mlflow_reason: str | None = None
        self._start_mlflow_run()

    def record(self, payload: dict[str, Any]) -> bool:
        step = _step(payload)
        metrics = normalize_metrics(payload)
        if not metrics:
            return False
        signature = (step, tuple(sorted(metrics.items())))
        if signature == self._last_signature:
            return False
        self._last_signature = signature
        event = {
            "step": step,
            "epoch": float(payload.get("epoch", 0) or 0),
            "timestamp": datetime.now(UTC).timestamp(),
            **metrics,
        }
        with self.metrics_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=True, separators=(",", ":")) + "\n")
        if self._writer is not None:
            try:
                for name, value in metrics.items():
                    self._writer.add_scalar(name, value, step)
                self._writer.flush()
            except Exception:
                self._writer = None
        if self._mlflow is not None and self.mlflow_available:
            try:
                self._mlflow.log_metrics(metrics, step=step)
            except Exception as exc:
                self.mlflow_available = False
                self.mlflow_reason = str(exc)
        return True

    def close(self, *, status: str) -> None:
        if self._writer is not None:
            try:
                self._writer.flush()
                self._writer.close()
            except Exception:
                pass
        if self._mlflow is not None and self.mlflow_available:
            try:
                self._mlflow.end_run(status=status)
            except Exception:
                pass

    def _start_mlflow_run(self) -> None:
        if self._mlflow is None:
            self.mlflow_reason = "mlflow package is unavailable"
            return
        try:
            if self.identity.mlflow_tracking_uri:
                self._mlflow.set_tracking_uri(self.identity.mlflow_tracking_uri)
            self._mlflow.start_run(run_name=self.identity.run_name, tags=self.identity.tags())
            self.mlflow_available = True
        except Exception as exc:
            self.mlflow_reason = str(exc)


def normalize_metrics(payload: dict[str, Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    for raw_name, raw_value in payload.items():
        if isinstance(raw_value, bool) or not isinstance(raw_value, int | float):
            continue
        name = _ALIASES.get(raw_name, raw_name)
        if name in {"current_steps", "total_steps", "percentage"}:
            continue
        result[name] = float(raw_value)
    return result


def _step(payload: dict[str, Any]) -> int:
    value = payload.get("current_steps", payload.get("step", 0))
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0


def _optional_mlflow() -> Any | None:
    try:
        import mlflow
    except ImportError:
        return None
    return mlflow


def _create_writer(output_dir: Path, writer_factory: Callable[[str], Any] | None) -> Any | None:
    try:
        if writer_factory is None:
            from torch.utils.tensorboard import SummaryWriter

            writer_factory = SummaryWriter
        return writer_factory(str(output_dir))
    except Exception:
        return None
