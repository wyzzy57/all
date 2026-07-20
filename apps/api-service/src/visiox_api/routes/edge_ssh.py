import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated, Any

import json
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

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
REQUEST_TIMEOUT_SECONDS = 60.0
_DEADLINE_SCHEDULER_GUARD_SECONDS = 0.02
_INVALID_BOOTSTRAP_DETAIL = "Bootstrap request is invalid"
_INVALID_SCAN_DETAIL = "Host-key scan request is invalid"
_REQUEST_TIMEOUT_DETAIL = "Edge bootstrap request timed out"
_SERVICE_BUSY_DETAIL = "Edge SSH service is busy"
_MAX_BACKGROUND_CALLS = 4
_BACKGROUND_CAPACITY = threading.BoundedSemaphore(_MAX_BACKGROUND_CALLS)
_BACKGROUND_EXECUTOR = ThreadPoolExecutor(
    max_workers=_MAX_BACKGROUND_CALLS,
    thread_name_prefix="visiox-edge-ssh-api",
)


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


async def get_edge_ssh_request_deadline() -> float:
    return time.monotonic() + REQUEST_TIMEOUT_SECONDS


RequestDeadlineDependency = Annotated[
    float,
    Depends(get_edge_ssh_request_deadline),
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
async def scan_host_key(
    http_request: Request,
    deadline: RequestDeadlineDependency,
    service: BootstrapServiceDependency,
) -> dict[str, Any]:
    request = await _read_request_model(
        http_request,
        ScanHostKeyRequest,
        invalid_detail=_INVALID_SCAN_DETAIL,
        deadline=deadline,
    )
    if request.password is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Password is not accepted for host-key scans",
        )
    return await _invoke_with_deadline(
        lambda: _invoke(
            lambda: service.scan_host_key(
                host=request.host,
                port=request.port,
                deadline=deadline,
            )
        ),
        deadline,
    )


@router.post(
    "/bootstrap",
    response_model=EdgeNodeOperationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def bootstrap(
    http_request: Request,
    deadline: RequestDeadlineDependency,
    service: BootstrapServiceDependency,
) -> dict[str, Any]:
    request = await _read_request_model(
        http_request,
        BootstrapRequest,
        invalid_detail=_INVALID_BOOTSTRAP_DETAIL,
        deadline=deadline,
    )
    password = request.password.get_secret_value()
    try:
        return await _invoke_with_deadline(
            lambda: _invoke(
                lambda: service.bootstrap(
                    host=request.host,
                    port=request.port,
                    administrator=request.administrator,
                    password=password,
                    confirmed_fingerprint=request.confirmed_fingerprint,
                    node_name=request.node_name,
                    deadline=deadline,
                )
            ),
            deadline,
        )
    finally:
        password = ""


async def _read_request_model(
    http_request: Request,
    model_type: type[BootstrapRequest] | type[ScanHostKeyRequest],
    *,
    invalid_detail: str,
    deadline: float,
) -> BootstrapRequest | ScanHostKeyRequest:
    body = bytearray()
    try:
        async with asyncio.timeout(_wait_timeout(deadline)):
            async for chunk in http_request.stream():
                if len(body) + len(chunk) > MAX_BOOTSTRAP_HTTP_BODY_BYTES:
                    raise ValueError
                body.extend(chunk)
            value = json.loads(bytes(body))
            if not isinstance(value, dict):
                raise ValueError
            return model_type.model_validate(value)
    except TimeoutError:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=_REQUEST_TIMEOUT_DETAIL,
        ) from None
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=invalid_detail,
        ) from None


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError
    return remaining


async def _invoke_with_deadline(call: Any, deadline: float) -> dict[str, Any]:
    try:
        wait_timeout = _wait_timeout(deadline)
    except TimeoutError:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=_REQUEST_TIMEOUT_DETAIL,
        ) from None
    if not _BACKGROUND_CAPACITY.acquire(blocking=False):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_SERVICE_BUSY_DETAIL,
        )
    try:
        future = _BACKGROUND_EXECUTOR.submit(call)
    except Exception:
        _BACKGROUND_CAPACITY.release()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_SERVICE_BUSY_DETAIL,
        ) from None
    future.add_done_callback(lambda _: _BACKGROUND_CAPACITY.release())
    try:
        return await asyncio.wait_for(
            asyncio.wrap_future(future),
            timeout=wait_timeout,
        )
    except TimeoutError:
        future.cancel()
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=_REQUEST_TIMEOUT_DETAIL,
        ) from None


def _wait_timeout(deadline: float) -> float:
    return max(
        0.0,
        _remaining(deadline) - _DEADLINE_SCHEDULER_GUARD_SECONDS,
    )


@router.post("/{id}/test-connection", response_model=EdgeNodeOperationResponse)
async def test_connection(
    id: str,
    deadline: RequestDeadlineDependency,
    service: BootstrapServiceDependency,
) -> dict[str, Any]:
    return await _invoke_with_deadline(
        lambda: _invoke(lambda: service.test_connection(node_id=id, deadline=deadline)),
        deadline,
    )


@router.post("/{id}/rotate-key", response_model=EdgeNodeOperationResponse)
async def rotate_key(
    id: str,
    deadline: RequestDeadlineDependency,
    service: BootstrapServiceDependency,
) -> dict[str, Any]:
    return await _invoke_with_deadline(
        lambda: _invoke(lambda: service.rotate_key(node_id=id, deadline=deadline)),
        deadline,
    )
