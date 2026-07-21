from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from numbers import Real
from pathlib import Path
from typing import Any

import yaml


_MAX_HISTOGRAM_VALUES = 100_000
_BATCH_INDEX_ATTRIBUTE = "_visiox_batch_index"
_GRADIENT_SAMPLE_EPOCH_ATTRIBUTE = "_visiox_gradient_sample_epoch"
_GRADIENT_SAMPLES_ATTRIBUTE = "_visiox_gradient_samples"
_RESOURCE_SAMPLES_ATTRIBUTE = "_visiox_resource_samples"
_ZERO_GRAD_WRAPPED_ATTRIBUTE = "_visiox_zero_grad_wrapped"


logger = logging.getLogger(__name__)


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


def write_final_progress_snapshot(trainer: Any) -> Path | None:
    try:
        return write_progress_snapshot(trainer)
    except Exception:
        logger.exception("Training observability final snapshot failed")
        return None


def _write_weight_histograms(trainer: Any, writer: Any, epoch: int) -> None:
    for name, parameter in trainer.model.named_parameters():
        if not parameter.requires_grad:
            continue
        writer.add_histogram(f"weights/{name}", _sample_tensor(parameter), epoch)


def log_weight_histograms(trainer: Any) -> None:
    try:
        writer = _tensorboard_writer()
        if writer is None:
            return
        epoch = _epoch_number(trainer)
        if not should_capture_epoch(epoch, int(trainer.epochs), _capture_interval()):
            return
        _write_weight_histograms(trainer, writer, epoch)
    except Exception:
        logger.exception("Training observability weight histogram capture failed")


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
        except (ImportError, RuntimeError, OSError):
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


def reset_training_batch_index(trainer: Any) -> None:
    install_pre_zero_gradient_capture(trainer)
    setattr(trainer, _BATCH_INDEX_ATTRIBUTE, -1)
    setattr(trainer, _GRADIENT_SAMPLE_EPOCH_ATTRIBUTE, None)


def install_pre_zero_gradient_capture(trainer: Any) -> None:
    optimizer = getattr(trainer, "optimizer", None)
    if optimizer is None:
        return
    if getattr(optimizer, _ZERO_GRAD_WRAPPED_ATTRIBUTE, False):
        return

    original_zero_grad = optimizer.zero_grad

    def zero_grad_with_capture(*args: Any, **kwargs: Any) -> Any:
        try:
            capture_gradient_sample(trainer)
        except Exception:
            logger.exception("Training observability pre-zero gradient capture failed")
        return original_zero_grad(*args, **kwargs)

    optimizer.zero_grad = zero_grad_with_capture
    setattr(optimizer, _ZERO_GRAD_WRAPPED_ATTRIBUTE, True)


def track_training_batch_start(trainer: Any) -> None:
    install_pre_zero_gradient_capture(trainer)
    batch_index = getattr(trainer, _BATCH_INDEX_ATTRIBUTE, -1)
    setattr(trainer, _BATCH_INDEX_ATTRIBUTE, int(batch_index) + 1)


def _is_last_training_batch(trainer: Any) -> bool:
    batch_index = getattr(trainer, _BATCH_INDEX_ATTRIBUTE, None)
    if not isinstance(batch_index, int):
        return False
    try:
        batch_count = len(trainer.train_loader)
    except (AttributeError, TypeError):
        return False
    return isinstance(batch_count, int) and batch_count > 0 and batch_index == batch_count - 1


def capture_gradient_sample(trainer: Any) -> None:
    try:
        _capture_gradient_sample(trainer)
    except Exception:
        logger.exception("Training observability gradient capture failed")


def _capture_gradient_sample(trainer: Any) -> None:
    epoch_index = getattr(trainer, "epoch", None)
    total_epochs = getattr(trainer, "epochs", None)
    if not isinstance(epoch_index, int) or not isinstance(total_epochs, int):
        return
    if not _is_last_training_batch(trainer):
        return
    epoch = epoch_index + 1
    if not should_capture_epoch(epoch, total_epochs, _capture_interval()):
        return
    if getattr(trainer, _GRADIENT_SAMPLE_EPOCH_ATTRIBUTE, None) == epoch:
        return

    samples: dict[str, Any] = {}
    for name, parameter in trainer.model.named_parameters():
        gradient = getattr(parameter, "grad", None)
        if parameter.requires_grad and gradient is not None:
            samples[name] = _sample_tensor(gradient, copy=True)
    if samples:
        setattr(trainer, _GRADIENT_SAMPLES_ATTRIBUTE, samples)
        setattr(trainer, _GRADIENT_SAMPLE_EPOCH_ATTRIBUTE, epoch)


def log_epoch_observability(trainer: Any) -> None:
    try:
        epoch = _epoch_number(trainer)
    except Exception:
        logger.exception("Training observability epoch lookup failed")
        epoch = None

    resource_metrics: dict[str, float] = {}
    if epoch is not None:
        try:
            resource_metrics = _collect_resource_metrics(trainer)
        except Exception:
            logger.exception("Training observability resource collection failed")
        try:
            resource_sample = {"step": epoch, "timestamp": time.time(), **resource_metrics}
            resource_samples = getattr(trainer, _RESOURCE_SAMPLES_ATTRIBUTE, None)
            if resource_samples is None:
                resource_samples = []
                setattr(trainer, _RESOURCE_SAMPLES_ATTRIBUTE, resource_samples)
            resource_samples.append(resource_sample)
        except Exception:
            logger.exception("Training observability resource sample retention failed")

    try:
        writer = _tensorboard_writer()
    except Exception:
        logger.exception("Training observability TensorBoard writer lookup failed")
        writer = None
    gradient_samples = getattr(trainer, _GRADIENT_SAMPLES_ATTRIBUTE, {})
    try:
        if writer is not None and epoch is not None:
            for tag, value in resource_metrics.items():
                try:
                    writer.add_scalar(tag, value, epoch)
                except Exception:
                    logger.exception("Training observability scalar write failed for %s", tag)
            try:
                if should_capture_epoch(epoch, int(trainer.epochs), _capture_interval()):
                    _write_weight_histograms(trainer, writer, epoch)
            except Exception:
                logger.exception("Training observability weight histogram write failed")
            if isinstance(gradient_samples, dict):
                for name, values in gradient_samples.items():
                    try:
                        writer.add_histogram(f"gradients/{name}", _sample_tensor(values), epoch)
                    except Exception:
                        logger.exception("Training observability gradient histogram write failed for %s", name)
    finally:
        setattr(trainer, _GRADIENT_SAMPLES_ATTRIBUTE, {})
        setattr(trainer, _GRADIENT_SAMPLE_EPOCH_ATTRIBUTE, None)
        try:
            write_progress_snapshot(trainer)
        except Exception:
            logger.exception("Training observability progress snapshot failed")


def main() -> None:
    from ultralytics import YOLO, settings

    rank = int(os.getenv("RANK", "-1"))
    settings.update(
        {
            "mlflow": rank in {-1, 0},
            "tensorboard": rank in {-1, 0},
        }
    )
    overrides = parse_overrides(sys.argv[1:])
    model_path = str(overrides.pop("model"))
    task = overrides.pop("task", None)
    overrides.pop("mode", None)
    model = YOLO(model_path, task=task)
    model.add_callback("on_pretrain_routine_end", install_pre_zero_gradient_capture)
    model.add_callback("on_train_epoch_start", reset_training_batch_index)
    model.add_callback("on_train_batch_start", track_training_batch_start)
    model.add_callback("on_before_zero_grad", capture_gradient_sample)
    model.add_callback("on_train_batch_end", capture_gradient_sample)
    model.add_callback("on_train_epoch_end", log_epoch_observability)
    model.add_callback("on_train_end", write_final_progress_snapshot)
    model.train(**overrides)


if __name__ == "__main__":
    main()
