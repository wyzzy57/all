from __future__ import annotations

from typing import Any, Protocol


class ObservabilityAdapter(Protocol):
    engine: str

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
