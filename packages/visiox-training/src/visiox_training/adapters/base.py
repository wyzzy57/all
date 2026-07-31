from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from visiox_training.capabilities import FrameworkCapabilities
from visiox_training.contracts import (
    ArtifactManifest,
    DatasetManifest,
    LaunchSpec,
    TelemetryEnvelope,
)


class FrameworkAdapter(ABC):
    @property
    @abstractmethod
    def capabilities(self) -> FrameworkCapabilities:
        raise NotImplementedError

    @property
    def adapter_key(self) -> str:
        return self.capabilities.adapter_key

    @property
    def adapter_version(self) -> str:
        return self.capabilities.adapter_version

    @property
    def framework(self) -> str:
        return self.capabilities.framework

    @abstractmethod
    def validate_dataset(self, manifest: DatasetManifest) -> None:
        raise NotImplementedError

    @abstractmethod
    def build_launch_spec(
        self, manifest: DatasetManifest, parameters: dict[str, Any]
    ) -> LaunchSpec:
        raise NotImplementedError

    @abstractmethod
    def parse_telemetry(self, line: str) -> TelemetryEnvelope | None:
        raise NotImplementedError

    @abstractmethod
    def collect_artifacts(self, output_dir: Path) -> ArtifactManifest:
        raise NotImplementedError
