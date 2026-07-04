from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class RuntimeContainer:
    app_id: str
    version: str
    package_uri: str
    manifest: dict[str, Any]
    status: str = "stopped"
    previous_version: str | None = None
    updated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "app_id": self.app_id,
            "version": self.version,
            "package_uri": self.package_uri,
            "manifest": self.manifest,
            "status": self.status,
            "previous_version": self.previous_version,
            "updated_at": self.updated_at,
        }


class DockerRuntime:
    """In-memory Docker runtime placeholder for the first Edge Agent version."""

    def __init__(self) -> None:
        self._containers: dict[str, RuntimeContainer] = {}

    def deploy(self, *, app_id: str, version: str, package_uri: str, manifest: dict[str, Any]) -> RuntimeContainer:
        previous = self._containers.get(app_id)
        container = RuntimeContainer(
            app_id=app_id,
            version=version,
            package_uri=package_uri,
            manifest=dict(manifest),
            status="deployed",
            previous_version=previous.version if previous else None,
        )
        self._containers[app_id] = container
        return container

    def start(self, app_id: str) -> RuntimeContainer:
        container = self._require_container(app_id)
        container.status = "running"
        container.updated_at = _now_iso()
        return container

    def stop(self, app_id: str) -> RuntimeContainer:
        container = self._require_container(app_id)
        container.status = "stopped"
        container.updated_at = _now_iso()
        return container

    def rollback(self, app_id: str, *, version: str, package_uri: str, manifest: dict[str, Any]) -> RuntimeContainer:
        container = RuntimeContainer(
            app_id=app_id,
            version=version,
            package_uri=package_uri,
            manifest=dict(manifest),
            status="deployed",
            previous_version=self._containers.get(app_id).version if app_id in self._containers else None,
        )
        self._containers[app_id] = container
        return container

    def get(self, app_id: str) -> RuntimeContainer | None:
        return self._containers.get(app_id)

    def list(self) -> list[RuntimeContainer]:
        return sorted(self._containers.values(), key=lambda item: item.app_id)

    def _require_container(self, app_id: str) -> RuntimeContainer:
        container = self._containers.get(app_id)
        if container is None:
            raise KeyError(app_id)
        return container
