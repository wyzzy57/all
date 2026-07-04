from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from visiox_edge_agent.docker_runtime import DockerRuntime


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class EdgeAppDeployment:
    app_id: str
    version: str
    package_uri: str
    manifest: dict[str, Any]
    status: str = "deployed"
    deployed_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "app_id": self.app_id,
            "version": self.version,
            "package_uri": self.package_uri,
            "manifest": self.manifest,
            "status": self.status,
            "deployed_at": self.deployed_at,
            "updated_at": self.updated_at,
        }


class EdgeAppRegistry:
    def __init__(self, runtime: DockerRuntime | None = None) -> None:
        self._runtime = runtime or DockerRuntime()
        self._apps: dict[str, list[EdgeAppDeployment]] = {}

    def deploy(self, *, app_id: str, version: str, package_uri: str, manifest: dict[str, Any]) -> EdgeAppDeployment:
        deployment = EdgeAppDeployment(
            app_id=app_id,
            version=version,
            package_uri=package_uri,
            manifest=dict(manifest),
            status="deployed",
        )
        self._apps.setdefault(app_id, []).append(deployment)
        self._runtime.deploy(app_id=app_id, version=version, package_uri=package_uri, manifest=manifest)
        return deployment

    def start(self, app_id: str) -> EdgeAppDeployment:
        deployment = self._require_current(app_id)
        self._runtime.start(app_id)
        deployment.status = "running"
        deployment.updated_at = _now_iso()
        return deployment

    def stop(self, app_id: str) -> EdgeAppDeployment:
        deployment = self._require_current(app_id)
        self._runtime.stop(app_id)
        deployment.status = "stopped"
        deployment.updated_at = _now_iso()
        return deployment

    def rollback(self, app_id: str) -> EdgeAppDeployment:
        deployments = self._apps.get(app_id, [])
        if len(deployments) < 2:
            raise ValueError(f"app has no previous deployment: {app_id}")

        current = deployments.pop()
        current.status = "rolled_back"
        current.updated_at = _now_iso()

        previous = deployments[-1]
        previous.status = "deployed"
        previous.updated_at = _now_iso()
        self._runtime.rollback(
            app_id,
            version=previous.version,
            package_uri=previous.package_uri,
            manifest=previous.manifest,
        )
        return previous

    def rollback_to(self, *, app_id: str, version: str, package_uri: str, manifest: dict[str, Any]) -> EdgeAppDeployment:
        current = self.get(app_id)
        if current is not None:
            current.status = "rolled_back"
            current.updated_at = _now_iso()

        deployment = EdgeAppDeployment(
            app_id=app_id,
            version=version,
            package_uri=package_uri,
            manifest=dict(manifest),
            status="deployed",
        )
        self._apps.setdefault(app_id, []).append(deployment)
        self._runtime.rollback(app_id, version=version, package_uri=package_uri, manifest=manifest)
        return deployment

    def list(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for app_id in sorted(self._apps):
            deployments = self._apps[app_id]
            if not deployments:
                continue
            current = deployments[-1]
            items.append(
                {
                    **current.to_dict(),
                    "versions": [deployment.version for deployment in deployments],
                }
            )
        return items

    def get(self, app_id: str) -> EdgeAppDeployment | None:
        deployments = self._apps.get(app_id, [])
        if not deployments:
            return None
        return deployments[-1]

    def _require_current(self, app_id: str) -> EdgeAppDeployment:
        deployment = self.get(app_id)
        if deployment is None:
            raise KeyError(app_id)
        return deployment
