from collections.abc import Generator
from datetime import UTC, datetime
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from visiox_api.services.management_proxy import require_management_proxy
from visiox_api.services.node_registry import refresh_stale_nodes
from visiox_api.services.edge_bootstrap import EdgeBootstrapService
from visiox_common.settings import Settings, get_settings
from visiox_db.models import ComputeNode, ResourcePool
from visiox_db.session import get_session
from visiox_edge_executor_worker.inventory import (
    InventorySnapshot,
    compatibility_key,
    compatibility_policy,
    pool_accepts_inventory,
)

from .edge_ssh import REQUEST_TIMEOUT_SECONDS, _invoke, _invoke_with_deadline


router = APIRouter(tags=["nodes"], dependencies=[Depends(require_management_proxy)])


class NodeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    resource_pool_id: str | None
    status: str
    architecture: str
    platform_kind: str
    capabilities: dict[str, Any]
    resources: dict[str, Any]
    fingerprint: dict[str, Any]
    agent_version: str
    certificate_expires_at: datetime | None
    last_seen_at: datetime | None
    created_at: datetime
    updated_at: datetime


class NodeListResponse(BaseModel):
    items: list[NodeResponse]
    total: int


class ResourcePoolResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    kind: str
    selector: dict[str, Any]
    compatibility_policy: dict[str, Any]
    enabled: bool


class ResourcePoolListResponse(BaseModel):
    items: list[ResourcePoolResponse]
    total: int


class EdgeNodeProbeResponse(BaseModel):
    status: str
    node_id: str
    supported: bool
    unsupported_reasons: list[str]
    compatibility_key: str
    resource_pool_id: str | None
    inventory: InventorySnapshot


class NodePoolAssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_pool_id: str


def get_node_session() -> Generator[Session]:
    yield from get_session()


def get_edge_inventory_service(
    settings: Settings = Depends(get_settings),
) -> EdgeBootstrapService:
    return EdgeBootstrapService(settings.edge_bootstrap_socket)


EdgeInventoryServiceDependency = Annotated[
    EdgeBootstrapService,
    Depends(get_edge_inventory_service),
]


async def get_edge_inventory_deadline() -> float:
    return time.monotonic() + REQUEST_TIMEOUT_SECONDS


EdgeInventoryDeadlineDependency = Annotated[
    float,
    Depends(get_edge_inventory_deadline),
]


def _refresh_stale(session: Session, settings: Settings) -> None:
    refresh_stale_nodes(session, datetime.now(UTC), settings.agent_offline_after_seconds)


@router.get("/nodes", response_model=NodeListResponse)
def list_nodes(
    session: Session = Depends(get_node_session),
    settings: Settings = Depends(get_settings),
) -> NodeListResponse:
    _refresh_stale(session, settings)
    nodes = list(session.scalars(select(ComputeNode).order_by(ComputeNode.created_at, ComputeNode.id)))
    return NodeListResponse(items=[NodeResponse.model_validate(node) for node in nodes], total=len(nodes))


@router.get("/nodes/{node_id}", response_model=NodeResponse)
def get_node(
    node_id: str,
    session: Session = Depends(get_node_session),
    settings: Settings = Depends(get_settings),
) -> NodeResponse:
    _refresh_stale(session, settings)
    node = session.get(ComputeNode, node_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")
    return NodeResponse.model_validate(node)


@router.get("/resource-pools", response_model=ResourcePoolListResponse)
def list_resource_pools(session: Session = Depends(get_node_session)) -> ResourcePoolListResponse:
    pools = list(session.scalars(select(ResourcePool).order_by(ResourcePool.created_at, ResourcePool.id)))
    return ResourcePoolListResponse(
        items=[ResourcePoolResponse.model_validate(pool) for pool in pools],
        total=len(pools),
    )


@router.post("/edge-nodes/{id}/probe", response_model=EdgeNodeProbeResponse)
async def probe_edge_node(
    id: str,
    deadline: EdgeInventoryDeadlineDependency,
    service: EdgeInventoryServiceDependency,
    session: Session = Depends(get_node_session),
) -> EdgeNodeProbeResponse:
    if session.get(ComputeNode, id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")
    response = await _invoke_with_deadline(
        lambda: _invoke(lambda: service.probe(node_id=id, deadline=deadline)),
        deadline,
    )
    try:
        snapshot = InventorySnapshot.model_validate(response["inventory"])
    except (KeyError, TypeError, ValidationError):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Edge inventory probe returned invalid data",
        ) from None

    try:
        pool = _persist_probe_inventory(session, id, snapshot, deadline=deadline)
    except Exception:
        session.rollback()
        raise
    return EdgeNodeProbeResponse(
        status="ok",
        node_id=id,
        supported=snapshot.supported,
        unsupported_reasons=list(snapshot.unsupported_reasons),
        compatibility_key=compatibility_key(snapshot),
        resource_pool_id=pool.id if pool is not None else None,
        inventory=snapshot,
    )


@router.put("/nodes/{node_id}/resource-pool", response_model=NodeResponse)
def assign_node_resource_pool(
    node_id: str,
    request: NodePoolAssignmentRequest,
    session: Session = Depends(get_node_session),
) -> NodeResponse:
    node = session.scalar(
        select(ComputeNode).where(ComputeNode.id == node_id).with_for_update()
    )
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")
    pool = session.get(ResourcePool, request.resource_pool_id)
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resource pool not found",
        )
    try:
        snapshot = _snapshot_from_node(node)
    except ValidationError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Node does not have a supported inventory",
        ) from None
    if (
        not snapshot.supported
        or not _resource_pool_accepts_inventory(pool, snapshot)
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Resource pool does not accept the node inventory",
        )
    node.resource_pool_id = pool.id
    session.commit()
    return NodeResponse.model_validate(node)


def _persist_probe_inventory(
    session: Session,
    node_id: str,
    snapshot: InventorySnapshot,
    *,
    deadline: float,
) -> ResourcePool | None:
    node = session.scalar(
        select(ComputeNode)
        .where(ComputeNode.id == node_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")

    key = compatibility_key(snapshot)
    preserved_fingerprint = dict(node.fingerprint)
    node.architecture = snapshot.architecture
    node.platform_kind = snapshot.platform_kind
    node.capabilities = {
        "nvidia_gpu": bool(snapshot.gpus) or snapshot.platform_kind == "jetson",
        "docker": snapshot.docker.model_dump(mode="json"),
        "gpu_models": [gpu.name for gpu in snapshot.gpus],
    }
    node.resources = {
        "cpu_logical_cores": snapshot.cpu_logical_cores,
        "memory_total_kib": snapshot.memory_total_kib,
        "gpu_count": len(snapshot.gpus) or int(snapshot.platform_kind == "jetson"),
        "gpu_memory_total_mib": sum(
            gpu.memory_total_mib or 0 for gpu in snapshot.gpus
        ),
    }
    node.fingerprint = {
        **preserved_fingerprint,
        "compatibility_key": key,
        "cuda_version": snapshot.cuda_version,
        "cuda_runtime_version": snapshot.cuda_runtime_version,
        "driver_cuda_compatibility_version": (
            snapshot.driver_cuda_compatibility_version
        ),
        "tensorrt_version": snapshot.tensorrt_version,
        "compute_capability": snapshot.compute_capability,
        "driver_version": snapshot.driver_version,
        "jetpack_version": snapshot.jetpack_version,
        "l4t_version": snapshot.l4t_version,
        "unsupported_reasons": list(snapshot.unsupported_reasons),
        "inventory_snapshot": snapshot.model_dump(mode="json"),
    }

    pool: ResourcePool | None = None
    if snapshot.supported:
        current_pool = (
            session.get(ResourcePool, node.resource_pool_id)
            if node.resource_pool_id is not None
            else None
        )
        if (
            current_pool is not None
            and _resource_pool_accepts_inventory(current_pool, snapshot)
        ):
            pool = current_pool
        else:
            pool = _get_or_create_inventory_pool(session, snapshot)
        node.resource_pool_id = pool.id
        if node.status not in {"disabled", "draining"}:
            node.status = "online"
    else:
        node.resource_pool_id = None
        if node.status not in {"disabled", "draining"}:
            node.status = "incompatible"

    if time.monotonic() >= deadline:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Edge inventory probe timed out",
        )
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise
    return pool


def _get_or_create_inventory_pool(
    session: Session,
    snapshot: InventorySnapshot,
) -> ResourcePool:
    key = compatibility_key(snapshot)
    pool = session.scalar(select(ResourcePool).where(ResourcePool.name == key))
    if pool is not None:
        _require_resource_pool_accepts_inventory(pool, snapshot)
        return pool
    policy = compatibility_policy(snapshot)
    pool = ResourcePool(
        name=key,
        kind=snapshot.platform_kind,
        selector=dict(policy),
        compatibility_policy=policy,
        enabled=True,
    )
    try:
        with session.begin_nested():
            session.add(pool)
            session.flush()
    except IntegrityError:
        winning_pool = session.scalar(select(ResourcePool).where(ResourcePool.name == key))
        if winning_pool is None:
            raise
        _require_resource_pool_accepts_inventory(winning_pool, snapshot)
        return winning_pool
    return pool


def _resource_pool_accepts_inventory(
    pool: ResourcePool,
    snapshot: InventorySnapshot,
) -> bool:
    return (
        pool.enabled
        and pool.kind == snapshot.platform_kind
        and pool_accepts_inventory(pool.compatibility_policy, snapshot)
    )


def _require_resource_pool_accepts_inventory(
    pool: ResourcePool,
    snapshot: InventorySnapshot,
) -> None:
    if not _resource_pool_accepts_inventory(pool, snapshot):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Resource pool does not accept the node inventory",
        )


def _snapshot_from_node(node: ComputeNode) -> InventorySnapshot:
    snapshot = node.fingerprint.get("inventory_snapshot")
    return InventorySnapshot.model_validate(snapshot)


@router.post("/nodes/{node_id}/drain", response_model=NodeResponse)
def drain_node(node_id: str, session: Session = Depends(get_node_session)) -> NodeResponse:
    node = session.get(ComputeNode, node_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")
    node.status = "draining"
    session.commit()
    return NodeResponse.model_validate(node)
