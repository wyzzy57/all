from __future__ import annotations

from dataclasses import dataclass

from packaging.version import InvalidVersion, Version

from visiox_training.adapters.base import FrameworkAdapter
from visiox_training.errors import (
    AmbiguousAdapterError,
    DuplicateAdapterError,
    InvalidAdapterVersionError,
    UnknownAdapterError,
    UnknownAdapterVersionError,
    UnsupportedTaskFrameworkError,
)


@dataclass(frozen=True)
class LegacyEngineMapping:
    task_type: str
    framework: str
    adapter_key: str


_LEGACY_ENGINE_MAPPINGS = {
    "yolo26": LegacyEngineMapping(
        task_type="object_detection",
        framework="ultralytics",
        adapter_key="ultralytics.object_detection.v1",
    ),
    "llamafactory": LegacyEngineMapping(
        task_type="llm_sft",
        framework="llamafactory",
        adapter_key="llamafactory.llm_sft.v1",
    ),
}


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[tuple[str, Version], FrameworkAdapter] = {}

    @staticmethod
    def _version(value: str) -> Version:
        try:
            return Version(value)
        except InvalidVersion as exc:
            raise InvalidAdapterVersionError(
                f"Invalid PEP 440 adapter version {value!r}"
            ) from exc

    def register(self, adapter: FrameworkAdapter) -> None:
        version = self._version(adapter.adapter_version)
        identity = (adapter.adapter_key, version)
        if identity in self._adapters:
            raise DuplicateAdapterError(
                f"Adapter {adapter.adapter_key!r} version {adapter.adapter_version!r} "
                "is already registered"
            )
        self._adapters[identity] = adapter

    def get(self, adapter_key: str, adapter_version: str) -> FrameworkAdapter:
        version = self._version(adapter_version)
        adapter = self._adapters.get((adapter_key, version))
        if adapter is not None:
            return adapter
        if any(key == adapter_key for key, _ in self._adapters):
            raise UnknownAdapterVersionError(
                f"Unknown version {adapter_version!r} for adapter {adapter_key!r}"
            )
        raise UnknownAdapterError(f"Unknown adapter {adapter_key!r}")

    def list(self) -> tuple[FrameworkAdapter, ...]:
        return tuple(
            adapter
            for _, adapter in sorted(self._adapters.items(), key=lambda item: item[0])
        )

    def resolve(
        self,
        *,
        task_type: str,
        framework: str,
        adapter_key: str | None = None,
        adapter_version: str | None = None,
    ) -> FrameworkAdapter:
        matches = [
            adapter
            for adapter in self._adapters.values()
            if adapter.framework == framework
            and adapter.capabilities.supports_task(task_type)
            and (adapter_key is None or adapter.adapter_key == adapter_key)
        ]
        if not matches:
            raise UnsupportedTaskFrameworkError(
                f"Framework {framework!r} does not support task {task_type!r}"
            )
        if adapter_version is not None:
            requested_version = self._version(adapter_version)
            version_matches = [
                adapter
                for adapter in matches
                if self._version(adapter.adapter_version) == requested_version
            ]
            if not version_matches:
                raise UnknownAdapterVersionError(
                    f"Unknown adapter version {adapter_version!r} for framework "
                    f"{framework!r} and task {task_type!r}"
                )
            return self._require_unambiguous(version_matches)
        highest_version = max(
            self._version(adapter.adapter_version) for adapter in matches
        )
        highest_matches = [
            adapter
            for adapter in matches
            if self._version(adapter.adapter_version) == highest_version
        ]
        return self._require_unambiguous(highest_matches)

    @staticmethod
    def _require_unambiguous(matches: list[FrameworkAdapter]) -> FrameworkAdapter:
        if len(matches) > 1:
            identities = sorted(
                f"{adapter.adapter_key}@{adapter.adapter_version}"
                for adapter in matches
            )
            raise AmbiguousAdapterError(
                "Adapter resolution is ambiguous: " + ", ".join(identities)
            )
        return matches[0]

    @staticmethod
    def legacy_engine_mapping(engine: str) -> LegacyEngineMapping:
        try:
            return _LEGACY_ENGINE_MAPPINGS[engine]
        except KeyError as exc:
            raise UnknownAdapterError(f"Unknown legacy engine {engine!r}") from exc


def create_registry() -> AdapterRegistry:
    return AdapterRegistry()
