from __future__ import annotations

from typing import Any


class UltralyticsObservabilityAdapter:
    engine = "yolo26"

    def __init__(self, service: Any) -> None:
        self.service = service

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
