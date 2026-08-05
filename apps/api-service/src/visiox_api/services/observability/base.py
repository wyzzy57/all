from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


DETECTION_CANONICAL_NAMES = {
    "ap": "detection.ap",
    "ap50_95": "detection.ap",
    "ap50": "detection.ap50",
    "ap75": "detection.ap75",
    "aps": "detection.aps",
    "apm": "detection.apm",
    "apl": "detection.apl",
    "ar": "detection.ar",
}


@dataclass(frozen=True)
class MetricIdentity:
    canonical_name: str
    raw_name: str
    unit: str = "scalar"
    split: str | None = None


class ObservabilityAdapter(Protocol):
    engine: str

    def metric_identity(self, raw_name: str) -> MetricIdentity: ...

    def summary(self, job: Any, pipeline: Any, task: Any) -> dict[str, Any]: ...

    def scalars(
        self,
        job: Any,
        keys: list[str],
        start_step: int | None,
        end_step: int | None,
        max_points: int | None,
    ) -> dict[str, Any]: ...

    def resources(
        self,
        job: Any,
        start_step: int | None,
        end_step: int | None,
        max_points: int | None,
    ) -> dict[str, Any]: ...

    def analysis(self, job: Any) -> dict[str, Any]: ...

    def artifacts(self, job: Any) -> dict[str, Any]: ...
