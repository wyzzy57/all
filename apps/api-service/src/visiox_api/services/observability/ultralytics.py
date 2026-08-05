from __future__ import annotations

from typing import Any

from visiox_api.services.observability.base import (
    DETECTION_CANONICAL_NAMES,
    MetricIdentity,
)


_METRICS = {
    "box_loss": ("loss.box", "loss"),
    "cls_loss": ("loss.classification", "loss"),
    "dfl_loss": ("loss.dfl", "loss"),
    "learning_rate": ("optimization.learning_rate", "rate"),
    "metrics.map50": (DETECTION_CANONICAL_NAMES["ap50"], "ratio"),
    "metrics.map50_95": (DETECTION_CANONICAL_NAMES["ap50_95"], "ratio"),
    "metrics.map75": (DETECTION_CANONICAL_NAMES["ap75"], "ratio"),
    "metrics.maps": (DETECTION_CANONICAL_NAMES["aps"], "ratio"),
    "metrics.mapm": (DETECTION_CANONICAL_NAMES["apm"], "ratio"),
    "metrics.mapl": (DETECTION_CANONICAL_NAMES["apl"], "ratio"),
    "metrics.ar": (DETECTION_CANONICAL_NAMES["ar"], "ratio"),
    "metrics.precision": ("detection.precision", "ratio"),
    "metrics.recall": ("detection.recall", "ratio"),
}


def _normalized(name: str) -> str:
    import re

    value = name.strip().lower().replace("/", ".").replace("-", "_")
    value = re.sub(r"\([^)]*\)", "", value)
    value = re.sub(r"[^a-z0-9_.]+", "_", value)
    return value.strip("._")


class UltralyticsObservabilityAdapter:
    engine = "yolo26"

    def __init__(self, service: Any) -> None:
        self.service = service

    def metric_identity(self, raw_name: str) -> MetricIdentity:
        normalized = _normalized(raw_name)
        split = None
        metric = normalized
        if normalized.startswith("train."):
            split, metric = "train", normalized.removeprefix("train.")
        elif normalized.startswith("val."):
            split, metric = "val", normalized.removeprefix("val.")
        elif normalized.startswith("metrics."):
            split = "val"
        canonical_name, unit = _METRICS.get(
            normalized, _METRICS.get(metric, (metric, "scalar"))
        )
        return MetricIdentity(canonical_name, raw_name, unit, split)

    def summary(self, job: Any, pipeline: Any, task: Any) -> dict[str, Any]:
        return self.service.get_summary(job, pipeline, task)

    def scalars(self, job: Any, keys: list[str], start_step: int | None, end_step: int | None, max_points: int | None) -> dict[str, Any]:
        return self.service.get_scalars(job, keys, start_step, end_step, max_points, engine=self.engine)

    def resources(self, job: Any, start_step: int | None, end_step: int | None, max_points: int | None) -> dict[str, Any]:
        return self.service.get_resources(job, start_step, end_step, max_points, engine=self.engine)

    def analysis(self, job: Any) -> dict[str, Any]:
        return self.service.get_analysis(job, engine=self.engine)

    def artifacts(self, job: Any) -> dict[str, Any]:
        return self.service.get_artifacts(job)
