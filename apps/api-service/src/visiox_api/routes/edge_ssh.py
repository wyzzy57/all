from typing import Annotated, Any

import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError
from starlette.concurrency import run_in_threadpool

from visiox_api.services.edge_bootstrap import (
    BootstrapChannelError,
    BootstrapRequestRejected,
    EdgeBootstrapService,
)
from visiox_api.services.management_proxy import require_management_proxy
from visiox_common.settings import Settings, get_settings


router = APIRouter(
    prefix="/edge-nodes",
    tags=["edge-nodes"],
    dependencies=[Depends(require_management_proxy)],
)

MAX_BOOTSTRAP_HTTP_BODY_BYTES = 64 * 1024
_INVALID_BOOTSTRAP_DETAIL = "Bootstrap request is invalid"


class ScanHostKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=22, ge=1, le=65535)
    password: SecretStr | None = Field(default=None, exclude=True)


class BootstrapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=22, ge=1, le=65535)
    administrator: str = Field(min_length=1, max_length=64)
    password: SecretStr
    confirmed_fingerprint: str = Field(min_length=1, max_length=128)
    node_name: str = Field(min_length=1, max_length=160)


class ScanHostKeyResponse(BaseModel):
    status: str
    host_key_type: str
    fingerprint: str


class EdgeNodeOperationResponse(BaseModel):
    status: str
    node_id: str


def get_edge_bootstrap_service(
    settings: Settings = Depends(get_settings),
) -> EdgeBootstrapService:
    return EdgeBootstrapService(settings.edge_bootstrap_socket)


BootstrapServiceDependency = Annotated[
    EdgeBootstrapService,
    Depends(get_edge_bootstrap_service),
]


def _invoke(call: Any) -> dict[str, Any]:
    try:
        return call()
    except BootstrapRequestRejected as error:
        if error.code in {
            "HOST_KEY_MISMATCH",
            "NODE_ALREADY_BOOTSTRAPPED",
            "NODE_NAME_CONFLICT",
            "SSH_HOST_IN_USE",
        }:
            status_code = status.HTTP_409_CONFLICT
        elif error.code == "REQUEST_TIMEOUT":
            status_code = status.HTTP_504_GATEWAY_TIMEOUT
        else:
            status_code = status.HTTP_502_BAD_GATEWAY
        detail: dict[str, Any] = {
            "error_code": error.code,
            "error_message": error.message,
        }
        if error.cleanup_required:
            detail["cleanup_required"] = True
        raise HTTPException(
            status_code=status_code,
            detail=detail,
        ) from None
    except BootstrapChannelError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Edge bootstrap service is unavailable",
        ) from None


@router.post("/scan-host-key", response_model=ScanHostKeyResponse)
def scan_host_key(
    request: ScanHostKeyRequest,
    service: BootstrapServiceDependency,
) -> dict[str, Any]:
    if request.password is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Password is not accepted for host-key scans",
        )
    return _invoke(lambda: service.scan_host_key(host=request.host, port=request.port))


@router.post(
    "/bootstrap",
    response_model=EdgeNodeOperationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def bootstrap(
    http_request: Request,
    service: BootstrapServiceDependency,
) -> dict[str, Any]:
    request = await _read_bootstrap_request(http_request)
    password = request.password.get_secret_value()
    try:
        return await run_in_threadpool(
            lambda: _invoke(
                lambda: service.bootstrap(
                    host=request.host,
                    port=request.port,
                    administrator=request.administrator,
                    password=password,
                    confirmed_fingerprint=request.confirmed_fingerprint,
                    node_name=request.node_name,
                )
            )
        )
    finally:
        password = ""


async def _read_bootstrap_request(http_request: Request) -> BootstrapRequest:
    body = bytearray()
    try:
        async for chunk in http_request.stream():
            if len(body) + len(chunk) > MAX_BOOTSTRAP_HTTP_BODY_BYTES:
                raise ValueError
            body.extend(chunk)
        value = json.loads(bytes(body))
        if not isinstance(value, dict):
            raise ValueError
        return BootstrapRequest.model_validate(value)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=_INVALID_BOOTSTRAP_DETAIL,
        ) from None


@router.post("/{id}/test-connection", response_model=EdgeNodeOperationResponse)
def test_connection(
    id: str,
    service: BootstrapServiceDependency,
) -> dict[str, Any]:
    return _invoke(lambda: service.test_connection(node_id=id))


@router.post("/{id}/rotate-key", response_model=EdgeNodeOperationResponse)
def rotate_key(
    id: str,
    service: BootstrapServiceDependency,
) -> dict[str, Any]:
    return _invoke(lambda: service.rotate_key(node_id=id))
