from __future__ import annotations

from visiox_api.services.observability.ultralytics import UltralyticsObservabilityAdapter
from visiox_api.services.observability.base import MetricIdentity


class LlamaFactoryObservabilityAdapter(UltralyticsObservabilityAdapter):
    engine = "llamafactory"

    def metric_identity(self, raw_name: str) -> MetricIdentity:
        normalized = raw_name.strip().lower().replace("/", ".")
        validation_prefixes = ("eval.", "eval_", "val.", "val_")
        split = "val" if normalized.startswith(validation_prefixes) else "train"
        metric = normalized
        for prefix in (*validation_prefixes, "train.", "train_"):
            if metric.startswith(prefix):
                metric = metric.removeprefix(prefix)
                break
        if metric == "loss":
            return MetricIdentity("language.loss", raw_name, "loss", split)
        if metric in {"learning_rate", "lr"}:
            return MetricIdentity(
                "optimization.learning_rate", raw_name, "rate", split
            )
        if metric == "grad_norm":
            return MetricIdentity("optimization.gradient_norm", raw_name, "scalar", split)
        return MetricIdentity(metric, raw_name, "scalar", split)
