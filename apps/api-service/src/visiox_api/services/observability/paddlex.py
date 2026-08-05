from __future__ import annotations

from typing import Any

from visiox_api.services.observability.base import (
    DETECTION_CANONICAL_NAMES,
    MetricIdentity,
)
from visiox_api.services.observability.ultralytics import (
    UltralyticsObservabilityAdapter,
)


_METRICS = {
    "loss": ("loss.total", "loss", "train"),
    "learning_rate": ("optimization.learning_rate", "rate", "train"),
    "bbox_map": (DETECTION_CANONICAL_NAMES["ap"], "ratio", "val"),
    "bbox_map_50": (DETECTION_CANONICAL_NAMES["ap50"], "ratio", "val"),
    "bbox_map_75": (DETECTION_CANONICAL_NAMES["ap75"], "ratio", "val"),
    "bbox_map_s": (DETECTION_CANONICAL_NAMES["aps"], "ratio", "val"),
    "bbox_map_m": (DETECTION_CANONICAL_NAMES["apm"], "ratio", "val"),
    "bbox_map_l": (DETECTION_CANONICAL_NAMES["apl"], "ratio", "val"),
    "ar": (DETECTION_CANONICAL_NAMES["ar"], "ratio", "val"),
}
def parse_log_line(line: str) -> dict[str, Any]:
    from visiox_paddlex_training_worker.telemetry import parse_log_line as parse

    parsed = parse(line)
    payload = {
        name: parsed[name]
        for name in ("step", "epoch")
        if name in parsed
    }
    metrics = {
        name: float(value)
        for name, value in parsed.items()
        if name not in {"step", "epoch_step", "epoch", "total_steps"}
    }
    if metrics:
        payload["metrics"] = metrics
    return payload


class PaddleXObservabilityAdapter(UltralyticsObservabilityAdapter):
    engine = "paddlex"

    def metric_identity(self, raw_name: str) -> MetricIdentity:
        normalized = raw_name.strip().lower().replace("/", ".")
        split = None
        metric = normalized
        if normalized.startswith("train."):
            split, metric = "train", normalized.removeprefix("train.")
        elif normalized.startswith(("eval.", "val.")):
            split = "val"
            metric = normalized.split(".", 1)[1]
        mapped = _METRICS.get(metric)
        if mapped is not None:
            canonical_name, unit, default_split = mapped
            return MetricIdentity(
                canonical_name, raw_name, unit, split or default_split
            )
        if metric.startswith("loss_"):
            family = metric.removeprefix("loss_")
            canonical_name = {
                "cls": "loss.classification",
                "bbox": "loss.bbox",
            }.get(family, f"loss.{family}")
            return MetricIdentity(canonical_name, raw_name, "loss", split or "train")
        return MetricIdentity(metric, raw_name, "scalar", split)

    def scalars(
        self,
        job: Any,
        keys: list[str],
        start_step: int | None,
        end_step: int | None,
        max_points: int | None,
    ) -> dict[str, Any]:
        return self.service.get_scalars(
            job,
            keys,
            start_step,
            end_step,
            max_points,
            engine=self.engine,
        )
