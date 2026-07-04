from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from visiox_db.models import Camera, Device
from visiox_db.session import get_session


router = APIRouter(tags=["devices"])


class AgentClient(Protocol):
    def health(self, endpoint_url: str) -> dict[str, Any]: ...

    def camera_test(self, endpoint_url: str, rtsp_url: str) -> dict[str, Any]: ...


class HttpxAgentClient:
    def health(self, endpoint_url: str) -> dict[str, Any]:
        with httpx.Client(timeout=10) as client:
            response = client.get(f"{endpoint_url.rstrip('/')}/health")
            response.raise_for_status()
            return response.json()

    def camera_test(self, endpoint_url: str, rtsp_url: str) -> dict[str, Any]:
        with httpx.Client(timeout=10) as client:
            response = client.post(f"{endpoint_url.rstrip('/')}/camera/test", json={"rtsp_url": rtsp_url})
            response.raise_for_status()
            return response.json()


class DeviceCreateRequest(BaseModel):
    name: str
    endpoint_url: str
    token_ref: str | None = None


class DeviceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    endpoint_url: str
    status: str
    token_ref: str | None
    last_heartbeat_at: datetime | None
    resource_info: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class DeviceListResponse(BaseModel):
    items: list[DeviceResponse]
    total: int
    limit: int
    offset: int


class CameraCreateRequest(BaseModel):
    name: str
    rtsp_url: str


class CameraResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    device_id: str
    name: str
    rtsp_url: str
    status: str
    last_snapshot_uri: str | None
    created_at: datetime
    updated_at: datetime


class CameraListResponse(BaseModel):
    items: list[CameraResponse]
    total: int
    limit: int
    offset: int


class AgentHealthResponse(BaseModel):
    device_id: str
    status: str
    agent: dict[str, Any]


class CameraTestResponse(BaseModel):
    camera_id: str
    status: str
    agent: dict[str, Any]


def get_device_session() -> Generator[Session]:
    yield from get_session()


def get_agent_client() -> AgentClient:
    return HttpxAgentClient()


@router.post("/devices", response_model=DeviceResponse)
def create_device(
    request: DeviceCreateRequest,
    response: Response,
    session: Session = Depends(get_device_session),
) -> Device:
    device = Device(
        name=request.name,
        endpoint_url=request.endpoint_url.rstrip("/"),
        token_ref=request.token_ref,
        status="offline",
        resource_info={},
    )
    session.add(device)
    session.commit()
    session.refresh(device)
    response.status_code = status.HTTP_201_CREATED
    return device


@router.get("/devices", response_model=DeviceListResponse)
def list_devices(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_device_session),
) -> DeviceListResponse:
    filters = []
    if status_filter is not None:
        filters.append(Device.status == status_filter)
    total_query = select(func.count()).select_from(Device)
    list_query = select(Device).order_by(Device.created_at, Device.id)
    if filters:
        total_query = total_query.where(*filters)
        list_query = list_query.where(*filters)
    total = session.scalar(total_query) or 0
    devices = session.scalars(list_query.limit(limit).offset(offset)).all()
    return DeviceListResponse(items=list(devices), total=total, limit=limit, offset=offset)


@router.get("/devices/{device_id}", response_model=DeviceResponse)
def get_device(device_id: str, session: Session = Depends(get_device_session)) -> Device:
    return _device_or_404(session, device_id)


@router.post("/devices/{device_id}:check", response_model=AgentHealthResponse)
def check_device_agent(
    device_id: str,
    session: Session = Depends(get_device_session),
    agent_client: AgentClient = Depends(get_agent_client),
) -> AgentHealthResponse:
    device = _device_or_404(session, device_id)
    try:
        agent_payload = agent_client.health(device.endpoint_url)
    except Exception as exc:
        device.status = "offline"
        session.add(device)
        session.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    device.status = "online"
    device.last_heartbeat_at = datetime.now(UTC)
    device.resource_info = agent_payload.get("device", agent_payload) if isinstance(agent_payload, dict) else {}
    session.add(device)
    session.commit()
    session.refresh(device)
    return AgentHealthResponse(device_id=device.id, status=device.status, agent=agent_payload)


@router.post("/devices/{device_id}/cameras", response_model=CameraResponse)
def create_camera(
    device_id: str,
    request: CameraCreateRequest,
    response: Response,
    session: Session = Depends(get_device_session),
) -> Camera:
    _device_or_404(session, device_id)
    camera = Camera(device_id=device_id, name=request.name, rtsp_url=request.rtsp_url, status="inactive")
    session.add(camera)
    session.commit()
    session.refresh(camera)
    response.status_code = status.HTTP_201_CREATED
    return camera


@router.get("/devices/{device_id}/cameras", response_model=CameraListResponse)
def list_cameras(
    device_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_device_session),
) -> CameraListResponse:
    _device_or_404(session, device_id)
    total = session.scalar(select(func.count()).select_from(Camera).where(Camera.device_id == device_id)) or 0
    cameras = session.scalars(
        select(Camera).where(Camera.device_id == device_id).order_by(Camera.created_at, Camera.id).limit(limit).offset(offset)
    ).all()
    return CameraListResponse(items=list(cameras), total=total, limit=limit, offset=offset)


@router.post("/cameras/{camera_id}:test", response_model=CameraTestResponse)
def test_camera(
    camera_id: str,
    session: Session = Depends(get_device_session),
    agent_client: AgentClient = Depends(get_agent_client),
) -> CameraTestResponse:
    camera = session.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Camera not found")
    device = _device_or_404(session, camera.device_id)
    try:
        agent_payload = agent_client.camera_test(device.endpoint_url, camera.rtsp_url)
    except Exception as exc:
        camera.status = "error"
        session.add(camera)
        session.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    camera.status = "active" if agent_payload.get("ok", True) else "error"
    session.add(camera)
    session.commit()
    return CameraTestResponse(camera_id=camera.id, status=camera.status, agent=agent_payload)


def _device_or_404(session: Session, device_id: str) -> Device:
    device = session.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return device
