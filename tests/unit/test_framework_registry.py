from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from visiox_training.adapters.base import FrameworkAdapter
from visiox_training.capabilities import FrameworkCapabilities, TaskCapability
from visiox_training.contracts import (
    ArtifactManifest,
    DatasetManifest,
    LaunchSpec,
    TelemetryEnvelope,
)
from visiox_training.errors import (
    AmbiguousAdapterError,
    DuplicateAdapterError,
    InvalidAdapterVersionError,
    UnknownAdapterError,
    UnknownAdapterVersionError,
    UnsupportedTaskFrameworkError,
)
from visiox_training.registry import AdapterRegistry, LegacyEngineMapping


class FakeAdapter(FrameworkAdapter):
    def __init__(
        self,
        *,
        adapter_key: str = "fake.object_detection.v1",
        adapter_version: str = "1.0.0",
        framework: str = "fake",
        task_type: str = "object_detection",
    ) -> None:
        self._capabilities = FrameworkCapabilities(
            adapter_key=adapter_key,
            adapter_version=adapter_version,
            framework=framework,
            tasks=(TaskCapability(task_type=task_type),),
        )

    @property
    def capabilities(self) -> FrameworkCapabilities:
        return self._capabilities

    def validate_dataset(self, manifest: DatasetManifest) -> None:
        return None

    def build_launch_spec(
        self, manifest: DatasetManifest, parameters: dict[str, Any]
    ) -> LaunchSpec:
        return LaunchSpec(
            adapter_key=self.adapter_key,
            adapter_version=self.adapter_version,
            argv=("fake-train",),
        )

    def parse_telemetry(self, line: str) -> TelemetryEnvelope | None:
        return None

    def collect_artifacts(self, output_dir: Path) -> ArtifactManifest:
        return ArtifactManifest(
            task_id="task-1",
            adapter_key=self.adapter_key,
            adapter_version=self.adapter_version,
        )


def test_registry_registers_gets_and_lists_adapters() -> None:
    registry = AdapterRegistry()
    adapter = FakeAdapter()

    registry.register(adapter)

    assert registry.get(adapter.adapter_key, adapter.adapter_version) is adapter
    assert registry.list() == (adapter,)


def test_registry_rejects_duplicate_adapter_identity() -> None:
    registry = AdapterRegistry()
    registry.register(FakeAdapter())

    with pytest.raises(DuplicateAdapterError):
        registry.register(FakeAdapter())


def test_registry_reports_unknown_adapter_and_version_separately() -> None:
    registry = AdapterRegistry()
    adapter = FakeAdapter()
    registry.register(adapter)

    with pytest.raises(UnknownAdapterError):
        registry.get("missing.adapter.v1", "1.0.0")
    with pytest.raises(UnknownAdapterVersionError):
        registry.get(adapter.adapter_key, "2.0.0")


def test_registry_resolves_supported_task_framework_and_version() -> None:
    registry = AdapterRegistry()
    adapter_v1 = FakeAdapter(adapter_version="1.0.0")
    adapter_v2 = FakeAdapter(adapter_version="2.0.0")
    registry.register(adapter_v2)
    registry.register(adapter_v1)

    assert (
        registry.resolve(
            task_type="object_detection",
            framework="fake",
            adapter_version="1.0.0",
        )
        is adapter_v1
    )
    assert (
        registry.resolve(task_type="object_detection", framework="fake") is adapter_v2
    )


@pytest.mark.parametrize("versions", [("2.0.0", "10.0.0"), ("10.0.0", "2.0.0")])
def test_registry_resolves_versions_semantically_independent_of_registration_order(
    versions: tuple[str, str],
) -> None:
    registry = AdapterRegistry()
    adapters = {version: FakeAdapter(adapter_version=version) for version in versions}
    for version in versions:
        registry.register(adapters[version])

    assert (
        registry.resolve(task_type="object_detection", framework="fake")
        is adapters["10.0.0"]
    )


def test_registry_prefers_stable_release_over_prerelease() -> None:
    registry = AdapterRegistry()
    prerelease = FakeAdapter(adapter_version="3.0.0rc1")
    stable = FakeAdapter(adapter_version="3.0.0")
    registry.register(stable)
    registry.register(prerelease)

    assert registry.resolve(task_type="object_detection", framework="fake") is stable


@pytest.mark.parametrize("reverse", [False, True])
def test_registry_rejects_equal_highest_versions_as_ambiguous(
    reverse: bool,
) -> None:
    registry = AdapterRegistry()
    first = FakeAdapter(adapter_key="fake.alpha.v1", adapter_version="2.0.0")
    second = FakeAdapter(adapter_key="fake.beta.v1", adapter_version="2.0.0")
    adapters = (second, first) if reverse else (first, second)
    for adapter in adapters:
        registry.register(adapter)

    with pytest.raises(AmbiguousAdapterError):
        registry.resolve(task_type="object_detection", framework="fake")
    with pytest.raises(AmbiguousAdapterError):
        registry.resolve(
            task_type="object_detection",
            framework="fake",
            adapter_version="2.0.0",
        )
    assert (
        registry.resolve(
            task_type="object_detection",
            framework="fake",
            adapter_key="fake.beta.v1",
        )
        is second
    )


@pytest.mark.parametrize("adapter_version", ["not-a-version", "1.0+", "version one"])
def test_registry_rejects_invalid_pep440_adapter_versions(
    adapter_version: str,
) -> None:
    registry = AdapterRegistry()

    with pytest.raises(InvalidAdapterVersionError):
        registry.register(FakeAdapter(adapter_version=adapter_version))


def test_registry_rejects_unsupported_task_framework_combination() -> None:
    registry = AdapterRegistry()
    registry.register(FakeAdapter())

    with pytest.raises(UnsupportedTaskFrameworkError):
        registry.resolve(task_type="llm_sft", framework="fake")


def test_registry_instances_do_not_share_mutable_state() -> None:
    first = AdapterRegistry()
    second = AdapterRegistry()
    first.register(FakeAdapter())

    assert len(first.list()) == 1
    assert second.list() == ()


def test_legacy_engine_mapping_is_explicit_and_complete() -> None:
    assert AdapterRegistry.legacy_engine_mapping("yolo26") == LegacyEngineMapping(
        task_type="object_detection",
        framework="ultralytics",
        adapter_key="ultralytics.object_detection.v1",
    )
    assert AdapterRegistry.legacy_engine_mapping("llamafactory") == LegacyEngineMapping(
        task_type="llm_sft",
        framework="llamafactory",
        adapter_key="llamafactory.llm_sft.v1",
    )

    with pytest.raises(UnknownAdapterError):
        AdapterRegistry.legacy_engine_mapping("missing")


def test_framework_adapter_identity_is_derived_from_immutable_capabilities() -> None:
    adapter = FakeAdapter()

    assert adapter.adapter_key == "fake.object_detection.v1"
    assert adapter.adapter_version == "1.0.0"
    assert adapter.framework == "fake"
