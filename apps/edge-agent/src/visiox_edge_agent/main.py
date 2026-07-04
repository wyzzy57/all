from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field

from visiox_edge_agent.apps import EdgeAppDeployment, EdgeAppRegistry


class DeployRequest(BaseModel):
    app_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    package_uri: str = Field(min_length=1)
    manifest: dict[str, Any] = Field(default_factory=dict)


class RollbackRequest(BaseModel):
    version: str = Field(min_length=1)
    package_uri: str = Field(min_length=1)
    manifest: dict[str, Any] = Field(default_factory=dict)


class CameraTestRequest(BaseModel):
    camera_id: str | None = None
    rtsp_url: str | None = None
    timeout_seconds: int = Field(default=5, ge=1, le=60)


def create_app(registry: EdgeAppRegistry | None = None) -> FastAPI:
    app = FastAPI(title="Visiox Edge Agent", version="0.1.0")
    app.state.registry = registry or EdgeAppRegistry()

    @app.get("/health")
    def health() -> dict[str, object]:
        return {
            "service": "edge-agent",
            "status": "ok",
        }

    @app.get("/device/info")
    def device_info() -> dict[str, object]:
        return {
            "device_id": "edge-agent-dev",
            "status": "online",
            "runtime": "memory",
            "capabilities": {
                "app_deploy": True,
                "camera_test": True,
                "docker_runtime": False,
                "yolo_inference": False,
            },
        }

    @app.post("/apps/deploy")
    def deploy_app(request: DeployRequest) -> dict[str, object]:
        deployment = _registry(app).deploy(
            app_id=request.app_id,
            version=request.version,
            package_uri=request.package_uri,
            manifest=request.manifest,
        )
        return _success_response("deployed", deployment)

    @app.post("/apps/{app_id}/start")
    def start_app(app_id: str) -> dict[str, object]:
        try:
            deployment = _registry(app).start(app_id)
        except KeyError as exc:
            raise _not_found(app_id) from exc
        return _success_response("running", deployment)

    @app.post("/apps/{app_id}/stop")
    def stop_app(app_id: str) -> dict[str, object]:
        try:
            deployment = _registry(app).stop(app_id)
        except KeyError as exc:
            raise _not_found(app_id) from exc
        return _success_response("stopped", deployment)

    @app.post("/apps/{app_id}/rollback")
    def rollback_app(app_id: str, request: RollbackRequest | None = None) -> dict[str, object]:
        try:
            if request is None:
                deployment = _registry(app).rollback(app_id)
            else:
                deployment = _registry(app).rollback_to(
                    app_id=app_id,
                    version=request.version,
                    package_uri=request.package_uri,
                    manifest=request.manifest,
                )
        except KeyError as exc:
            raise _not_found(app_id) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return _success_response("rolled_back", deployment)

    @app.get("/apps")
    def list_apps() -> dict[str, object]:
        apps = _registry(app).list()
        return {
            "success": True,
            "status": "ok",
            "count": len(apps),
            "apps": apps,
        }

    @app.post("/camera/test")
    def camera_test(request: CameraTestRequest) -> dict[str, object]:
        target = request.camera_id or request.rtsp_url
        if not target:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="camera_id or rtsp_url is required",
            )
        return {
            "success": True,
            "status": "reachable",
            "camera": {
                "camera_id": request.camera_id,
                "rtsp_url": request.rtsp_url,
                "timeout_seconds": request.timeout_seconds,
            },
            "checks": {
                "network": "simulated",
                "rtsp": "not_tested",
            },
        }

    return app


def _registry(app: FastAPI) -> EdgeAppRegistry:
    return app.state.registry


def _success_response(status_text: str, deployment: EdgeAppDeployment) -> dict[str, object]:
    return {
        "success": True,
        "status": status_text,
        "app": deployment.to_dict(),
    }


def _not_found(app_id: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"app not found: {app_id}")


app = create_app()
