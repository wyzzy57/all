from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Callable


UTC = timezone.utc
_POSITION = re.compile(r"Epoch:\s*\[\s*(\d+)\s*]\s*\[\s*(\d+)\s*/\s*(\d+)\s*]")
_METRIC = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(learning_rate|loss(?:_[A-Za-z0-9_]+)?|bbox_mAP(?:_[A-Za-z0-9]+)?|AR)"
    r"\s*[:=]\s*"
    r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
)
_COCO_METRIC = re.compile(
    r"Average\s+(Precision|Recall)\s+\((AP|AR)\)\s*@\[\s*"
    r"IoU=([0-9.:]+)\s*\|\s*area=\s*(all|small|medium|large)\s*\|\s*"
    r"maxDets=\s*(\d+)\s*\]\s*=\s*"
    r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
)


def parse_log_line(line: str) -> dict[str, float | int]:
    payload: dict[str, float | int] = {}
    position = _POSITION.search(line)
    if position is not None:
        epoch, step, total_steps = position.groups()
        epoch_value = int(epoch)
        epoch_step = int(step)
        steps_per_epoch = int(total_steps)
        payload.update(
            {
                "step": epoch_value * steps_per_epoch + epoch_step,
                "epoch_step": epoch_step,
                "epoch": float(epoch_value),
                "total_steps": steps_per_epoch,
            }
        )
    for name, raw_value in _METRIC.findall(line):
        payload[name] = float(raw_value)
    coco_metric = _COCO_METRIC.search(line)
    if coco_metric is not None:
        _kind, name, iou, area, max_dets, raw_value = coco_metric.groups()
        payload[_canonical_coco_name(name, iou, area, int(max_dets))] = float(
            raw_value
        )
    return payload


def _canonical_coco_name(name: str, iou: str, area: str, max_dets: int) -> str:
    if name == "AP":
        if area == "small":
            return "bbox_mAP_s"
        if area == "medium":
            return "bbox_mAP_m"
        if area == "large":
            return "bbox_mAP_l"
        if iou == "0.50":
            return "bbox_mAP_50"
        if iou == "0.75":
            return "bbox_mAP_75"
        return "bbox_mAP"
    if area != "all":
        return {"small": "AR_s", "medium": "AR_m", "large": "AR_l"}[area]
    return "AR" if max_dets == 100 else f"AR_{max_dets}"


class TelemetryRecorder:
    def __init__(
        self,
        output_dir: Path,
        *,
        run_name: str,
        tags: dict[str, str],
        mlflow_tracking_uri: str | None = None,
        mlflow_module: Any | None = None,
        writer_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.metrics_path = output_dir / "visiox-metrics.jsonl"
        self.metrics_path.touch(exist_ok=True)
        self.latest_metrics: dict[str, float] = {}
        self.evaluation_metrics: dict[str, float] = {}
        self.output_dir = output_dir
        self.step = 0
        self.epoch = 0.0
        self.total_steps = 0
        self._last_signature: tuple[int, tuple[tuple[str, float], ...]] | None = None
        self._mlflow = mlflow_module if mlflow_module is not None else _optional_mlflow()
        self._writer = _create_writer(output_dir, writer_factory)
        self.tensorboard_available = self._writer is not None
        self.tensorboard_reason = (
            None if self.tensorboard_available else "TensorBoard writer is unavailable"
        )
        self.mlflow_available = False
        self.mlflow_reason: str | None = None
        self._mlflow_run_started = False
        if self._mlflow is None:
            self.mlflow_reason = "mlflow package is unavailable"
        else:
            try:
                if mlflow_tracking_uri:
                    self._mlflow.set_tracking_uri(mlflow_tracking_uri)
                self._mlflow.start_run(run_name=run_name, tags=tags)
                self.mlflow_available = True
                self._mlflow_run_started = True
            except Exception as exc:
                self.mlflow_reason = str(exc)

    def record_line(self, line: str) -> bool:
        payload = parse_log_line(line)
        metrics = {
            key: float(value)
            for key, value in payload.items()
            if key not in {"step", "epoch_step", "epoch", "total_steps"}
        }
        if not metrics:
            return False
        step = int(payload.get("step", self.step))
        signature = (step, tuple(sorted(metrics.items())))
        if signature == self._last_signature:
            return False
        self._last_signature = signature
        self.step = step
        self.epoch = float(payload.get("epoch", self.epoch))
        self.total_steps = int(payload.get("total_steps", self.total_steps))
        self.latest_metrics.update(metrics)
        self.evaluation_metrics.update(
            {
                key: value
                for key, value in metrics.items()
                if key.startswith("bbox_mAP") or key.startswith("AR")
            }
        )
        event = {
            "schema_version": "1.0",
            "source": "paddlex_log",
            "timestamp": datetime.now(UTC).timestamp(),
            "step": self.step,
            "epoch": self.epoch,
            "metrics": metrics,
        }
        with self.metrics_path.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(event, ensure_ascii=True, separators=(",", ":")) + "\n"
            )
        if self._writer is not None:
            try:
                for name, value in metrics.items():
                    self._writer.add_scalar(name, value, self.step)
                self._writer.flush()
            except Exception as exc:
                writer = self._writer
                self._writer = None
                self.tensorboard_available = False
                self.tensorboard_reason = str(exc)
                _close_writer(writer)
        if self._mlflow is not None and self._mlflow_run_started:
            try:
                self._mlflow.log_metrics(metrics, step=self.step)
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
            finally:
                self._writer = None
        if self._mlflow is not None and self._mlflow_run_started:
            try:
                self._mlflow.end_run(status=status)
            except Exception:
                pass
            finally:
                self._mlflow_run_started = False
        if self.evaluation_metrics:
            _write_evaluation_report(self.output_dir, self.evaluation_metrics)


def _optional_mlflow() -> Any | None:
    try:
        import mlflow
    except ImportError:
        return None
    return mlflow


def _create_writer(
    output_dir: Path,
    writer_factory: Callable[[str], Any] | None,
) -> Any | None:
    try:
        if writer_factory is None:
            from tensorboardX import SummaryWriter

            writer_factory = SummaryWriter
        return writer_factory(str(output_dir / "tensorboard"))
    except Exception:
        return None


def _close_writer(writer: Any) -> None:
    try:
        writer.close()
    except Exception:
        pass


def _write_evaluation_report(
    output_dir: Path,
    metrics: dict[str, float],
) -> None:
    report_dir = output_dir / "evaluation"
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / "evaluation.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "framework": "paddlex",
                "metrics": dict(sorted(metrics.items())),
            },
            ensure_ascii=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    temporary.replace(path)
