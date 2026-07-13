from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from numbers import Real
from pathlib import Path
from typing import Any

import yaml


_MAX_HISTOGRAM_VALUES = 100_000
_GRADIENT_SAMPLES_ATTRIBUTE = "_visiox_gradient_samples"
_RESOURCE_SAMPLES_ATTRIBUTE = "_visiox_resource_samples"


def parse_overrides(arguments: list[str]) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    for argument in arguments:
        key, separator, raw_value = argument.partition("=")
        if not separator or not key:
            raise ValueError(f"Invalid training argument: {argument}")
        overrides[key] = yaml.safe_load(raw_value)
    return overrides


def should_capture_epoch(epoch: int, total_epochs: int, interval: int) -> bool:
    capture_interval = max(1, int(interval))
    return epoch == 1 or epoch % capture_interval == 0 or epoch == total_epochs


def _capture_interval() -> int:
    return max(1, int(os.getenv("VISIOX_TENSORBOARD_HISTOGRAM_INTERVAL", "5")))


def _tensorboard_writer() -> Any | None:
    try:
        from ultralytics.utils.callbacks import tensorboard as tensorboard_callback
    except ImportError:
        return None
    return tensorboard_callback.WRITER


def _sample_tensor(tensor: Any, *, copy: bool = False) -> Any:
    values = tensor.detach().float().flatten()
    if values.numel() > _MAX_HISTOGRAM_VALUES:
        stride = max(1, values.numel() // _MAX_HISTOGRAM_VALUES)
        values = values[::stride][:_MAX_HISTOGRAM_VALUES]
    values = values.cpu()
    return values.clone() if copy else values


def _epoch_number(trainer: Any) -> int:
    return int(trainer.epoch) + 1


def _scalar_metrics(trainer: Any) -> dict[str, float]:
    latest: dict[str, float] = {}
    for name, value in (getattr(trainer, "metrics", None) or {}).items():
        if not isinstance(value, Real):
            item = getattr(value, "item", None)
            if not callable(item):
                continue
            value = item()
        if isinstance(value, Real):
            latest[str(name)] = float(value)
    return latest


def write_progress_snapshot(trainer: Any) -> Path:
    path = Path(trainer.save_dir) / "visiox-progress.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    total_epochs = int(trainer.epochs)
    current_epoch = min(_epoch_number(trainer), total_epochs) if total_epochs > 0 else 0
    now = time.time()
    started_at = getattr(trainer, "train_time_start", getattr(trainer, "time_start", now))
    elapsed_seconds = max(0.0, now - float(started_at))
    eta_seconds = (
        elapsed_seconds * max(0, total_epochs - current_epoch) / current_epoch if current_epoch > 0 else None
    )
    payload = {
        "progress": {
            "current_epoch": current_epoch,
            "total_epochs": total_epochs,
            "percent": round(current_epoch * 100.0 / total_epochs, 2) if total_epochs > 0 else 0.0,
        },
        "timing": {
            "elapsed_seconds": elapsed_seconds,
            "eta_seconds": eta_seconds,
            "updated_at": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
        },
        "environment": {"device": str(getattr(trainer, "device", "unknown"))},
        "latest_metrics": _scalar_metrics(trainer),
        "resources": list(getattr(trainer, _RESOURCE_SAMPLES_ATTRIBUTE, [])),
    }
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
    temporary_path.replace(path)
    return path


def write_final_progress_snapshot(trainer: Any) -> Path:
    return write_progress_snapshot(trainer)


def _write_weight_histograms(trainer: Any, writer: Any, epoch: int) -> None:
    for name, parameter in trainer.model.named_parameters():
        if not parameter.requires_grad:
            continue
        writer.add_histogram(f"weights/{name}", _sample_tensor(parameter), epoch)


def log_weight_histograms(trainer: Any) -> None:
    writer = _tensorboard_writer()
    if writer is None:
        return
    epoch = _epoch_number(trainer)
    if not should_capture_epoch(epoch, int(trainer.epochs), _capture_interval()):
        return
    _write_weight_histograms(trainer, writer, epoch)


def _images_per_second(trainer: Any) -> float | None:
    epoch_time_start = getattr(trainer, "epoch_time_start", None)
    epoch_time = (
        time.time() - float(epoch_time_start)
        if isinstance(epoch_time_start, Real)
        else getattr(trainer, "epoch_time", None)
    )
    dataset = getattr(getattr(trainer, "train_loader", None), "dataset", None)
    if not isinstance(epoch_time, Real) or epoch_time <= 0 or dataset is None:
        return None
    try:
        return len(dataset) / float(epoch_time)
    except TypeError:
        return None


def _collect_resource_metrics(trainer: Any) -> dict[str, float]:
    import psutil

    metrics = {
        "system.cpu_percent": float(psutil.cpu_percent(interval=None)),
        "system.memory_percent": float(psutil.virtual_memory().percent),
        "system.memory_used_gb": float(psutil.Process().memory_info().rss) / 1024**3,
    }
    throughput = _images_per_second(trainer)
    if throughput is not None:
        metrics["train.images_per_second"] = throughput

    try:
        import torch
    except ImportError:
        return metrics
    cuda = getattr(torch, "cuda", None)
    if cuda is None or not cuda.is_available():
        return metrics

    utilization = getattr(cuda, "utilization", None)
    if callable(utilization):
        try:
            metrics["system.gpu_utilization_percent"] = float(utilization())
        except (RuntimeError, OSError):
            pass
    for key, method_name in (
        ("system.gpu_memory_used_gb", "memory_allocated"),
        ("system.gpu_memory_reserved_gb", "memory_reserved"),
    ):
        method = getattr(cuda, method_name, None)
        if callable(method):
            try:
                metrics[key] = float(method()) / 1024**3
            except (RuntimeError, OSError):
                pass
    return metrics


def _is_last_training_batch(trainer: Any) -> bool:
    batch_index = getattr(trainer, "batch_i", None)
    if not isinstance(batch_index, int):
        return False
    try:
        batch_count = len(trainer.train_loader)
    except (AttributeError, TypeError):
        batch_count = getattr(trainer, "nb", None)
    return isinstance(batch_count, int) and batch_count > 0 and batch_index == batch_count - 1


def capture_gradient_sample(trainer: Any) -> None:
    epoch = _epoch_number(trainer)
    if not should_capture_epoch(epoch, int(trainer.epochs), _capture_interval()):
        return
    if not _is_last_training_batch(trainer):
        return

    samples: dict[str, Any] = {}
    for name, parameter in trainer.model.named_parameters():
        gradient = getattr(parameter, "grad", None)
        if parameter.requires_grad and gradient is not None:
            samples[name] = _sample_tensor(gradient, copy=True)
    setattr(trainer, _GRADIENT_SAMPLES_ATTRIBUTE, samples)


def log_epoch_observability(trainer: Any) -> None:
    epoch = _epoch_number(trainer)
    resource_metrics = _collect_resource_metrics(trainer)
    resource_sample = {"step": epoch, "timestamp": time.time(), **resource_metrics}
    resource_samples = getattr(trainer, _RESOURCE_SAMPLES_ATTRIBUTE, None)
    if resource_samples is None:
        resource_samples = []
        setattr(trainer, _RESOURCE_SAMPLES_ATTRIBUTE, resource_samples)
    resource_samples.append(resource_sample)

    writer = _tensorboard_writer()
    gradient_samples = getattr(trainer, _GRADIENT_SAMPLES_ATTRIBUTE, {})
    try:
        if writer is not None:
            for tag, value in resource_metrics.items():
                writer.add_scalar(tag, value, epoch)
            if should_capture_epoch(epoch, int(trainer.epochs), _capture_interval()):
                _write_weight_histograms(trainer, writer, epoch)
            for name, values in gradient_samples.items():
                writer.add_histogram(f"gradients/{name}", _sample_tensor(values), epoch)
    finally:
        setattr(trainer, _GRADIENT_SAMPLES_ATTRIBUTE, {})
        write_progress_snapshot(trainer)


def main() -> None:
    from ultralytics import YOLO, settings

    settings.update({"mlflow": True, "tensorboard": True})
    overrides = parse_overrides(sys.argv[1:])
    model_path = str(overrides.pop("model"))
    task = overrides.pop("task", None)
    overrides.pop("mode", None)
    model = YOLO(model_path, task=task)
    model.add_callback("on_before_zero_grad", capture_gradient_sample)
    model.add_callback("on_train_epoch_end", log_epoch_observability)
    model.add_callback("on_train_end", write_final_progress_snapshot)
    model.train(**overrides)


if __name__ == "__main__":
    main()
