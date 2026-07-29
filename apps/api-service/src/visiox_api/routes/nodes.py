from datetime import UTC, datetime
import hmac
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from visiox_api.dependencies.auth import get_current_user, require_admin
from visiox_api.dependencies.authorization import require_resource_permission
from visiox_api.dependencies.database import get_db_session
from visiox_api.services.authorization import authorized_resource_predicate
from visiox_api.services.audit import record_audit
from visiox_api.services.management_proxy import require_management_proxy
from visiox_api.services.node_inventory import mark_inventory_failure
from visiox_api.services.node_registry import refresh_stale_nodes
from visiox_api.services.edge_bootstrap import EdgeBootstrapService
from visiox_common.settings import Settings, get_settings
from visiox_db.models import (
    AgentEnrollmentToken,
    ComputeNode,
    DeploymentInstance,
    EdgeSshCredential,
    NodeCommand,
    NodeEvent,
    RemoteExecution,
    ResourcePool,
    User,
)
from visiox_db.models.identity import PERMISSION_USE, PERMISSION_VIEW
from visiox_edge_executor_worker.inventory import (
    InventorySnapshot,
    compatibility_key,
    compatibility_policy,
    pool_accepts_inventory,
)

from .edge_ssh import (
    REQUEST_TIMEOUT_SECONDS,
    _invoke,
    _invoke_with_deadline,
    get_edge_bootstrap_service,
)


router = APIRouter(tags=["nodes"], dependencies=[Depends(require_management_proxy)])


class NodeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    organization_id: str | None
    owner_user_id: str | None
    visibility: str
    resource_pool_id: str | None
    status: str
    architecture: str
    platform_kind: str
    capabilities: dict[str, Any]
    resources: dict[str, Any]
    fingerprint: dict[str, Any]
    agent_version: str
    enabled: bool
    labels: dict[str, Any]
    connection_method: str
    inventory_refreshed_at: datetime | None
    resource_revision: int
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
    organization_id: str | None
    owner_user_id: str | None
    visibility: str
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


class ManualNodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=22, ge=1, le=65535)
    administrator: str = Field(min_length=1, max_length=64)
    password: SecretStr
    confirmed_fingerprint: str = Field(min_length=1, max_length=128)
    labels: dict[str, str] = Field(default_factory=dict)
    resource_pool_id: str | None = None


get_node_session = get_db_session


def get_edge_inventory_service(
    service: EdgeBootstrapService = Depends(get_edge_bootstrap_service),
) -> EdgeBootstrapService:
    return service


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
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_node_session),
    settings: Settings = Depends(get_settings),
) -> NodeListResponse:
    _refresh_stale(session, settings)
    nodes = list(
        session.scalars(
            select(ComputeNode)
            .where(
                authorized_resource_predicate(
                    session, actor, ComputeNode, "node", PERMISSION_VIEW
                )
            )
            .order_by(ComputeNode.created_at, ComputeNode.id)
        )
    )
    return NodeListResponse(items=[NodeResponse.model_validate(node) for node in nodes], total=len(nodes))


@router.post(
    "/nodes/manual",
    response_model=NodeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_manual_node(
    request: ManualNodeRequest,
    deadline: EdgeInventoryDeadlineDependency,
    service: EdgeInventoryServiceDependency,
    actor: User = Depends(require_admin),
    session: Session = Depends(get_db_session),
) -> NodeResponse:
    host = request.host.strip().casefold().rstrip(".")
    scan = await _invoke_with_deadline(
        lambda: _invoke(
            lambda: service.scan_host_key(
                host=host,
                port=request.port,
                deadline=deadline,
            )
        ),
        deadline,
    )
    scanned_fingerprint = str(scan.get("fingerprint", ""))
    if not scanned_fingerprint or not hmac.compare_digest(
        scanned_fingerprint,
        request.confirmed_fingerprint,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "HOST_KEY_MISMATCH",
                "error_message": "Confirmed SSH host key does not match the server",
            },
        )

    record_audit(
        session,
        actor,
        "node.host_key.confirm",
        "compute_node",
        None,
        "success",
        None,
        {"host": host, "port": request.port, "fingerprint": scanned_fingerprint},
    )

    credential = session.scalar(
        select(EdgeSshCredential).where(
            EdgeSshCredential.ssh_host == host,
            EdgeSshCredential.ssh_port == request.port,
        )
    )
    if credential is not None and not hmac.compare_digest(
        credential.host_key_fingerprint,
        scanned_fingerprint,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "SSH_HOST_KEY_CHANGED",
                "error_message": "The SSH host key changed after this node was enrolled",
            },
        )
    if credential is None:
        bootstrap = await _invoke_with_deadline(
            lambda: _invoke(
                lambda: service.bootstrap(
                    host=host,
                    port=request.port,
                    administrator=request.administrator,
                    password=request.password.get_secret_value(),
                    confirmed_fingerprint=request.confirmed_fingerprint,
                    node_name=request.name.strip(),
                    deadline=deadline,
                )
            ),
            deadline,
        )
        node_id = str(bootstrap.get("node_id", ""))
        if not node_id:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Edge bootstrap returned invalid data",
            )
    else:
        node_id = credential.node_id

    probe = await _invoke_with_deadline(
        lambda: _invoke(lambda: service.probe(node_id=node_id, deadline=deadline)),
        deadline,
    )
    try:
        snapshot = InventorySnapshot.model_validate(probe["inventory"])
    except (KeyError, TypeError, ValidationError):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Edge inventory probe returned invalid data",
        ) from None

    try:
        pool = _persist_probe_inventory(
            session,
            node_id,
            snapshot,
            deadline=deadline,
            commit=False,
        )
        node = session.get(ComputeNode, node_id)
        if node is None:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Bootstrapped node was not persisted",
            )
        node.organization_id = actor.organization_id
        node.owner_user_id = actor.id
        node.visibility = "private"
        if pool is not None and pool.organization_id is None:
            pool.organization_id = actor.organization_id
            pool.owner_user_id = actor.id
            pool.visibility = "private"
        if request.resource_pool_id is not None:
            require_resource_permission(
                session, actor, "resource_pool", request.resource_pool_id, PERMISSION_USE
            )
            pool = session.get(ResourcePool, request.resource_pool_id)
            if pool is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Resource pool not found",
                )
            _require_resource_pool_accepts_inventory(pool, snapshot)
            node.resource_pool_id = pool.id
        node.name = request.name.strip()
        node.enabled = True
        node.labels = dict(request.labels)
        node.connection_method = "ssh"
        record_audit(
            session,
            actor,
            "node.create",
            "compute_node",
            node.id,
            "success",
            None,
            {"name": node.name, "host": host, "port": request.port},
        )
        record_audit(
            session,
            actor,
            "node.probe",
            "compute_node",
            node.id,
            "success",
            None,
            {"supported": snapshot.supported, "resource_pool_id": node.resource_pool_id},
        )
        session.commit()
        session.refresh(node)
    except Exception:
        session.rollback()
        raise
    return NodeResponse.model_validate(node)


@router.get("/nodes/{node_id}", response_model=NodeResponse)
def get_node(
    node_id: str,
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_node_session),
    settings: Settings = Depends(get_settings),
) -> NodeResponse:
    _refresh_stale(session, settings)
    require_resource_permission(session, actor, "node", node_id, PERMISSION_VIEW)
    node = session.get(ComputeNode, node_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")
    return NodeResponse.model_validate(node)


@router.get("/resource-pools", response_model=ResourcePoolListResponse)
def list_resource_pools(
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_node_session),
) -> ResourcePoolListResponse:
    pools = list(
        session.scalars(
            select(ResourcePool)
            .where(
                authorized_resource_predicate(
                    session, actor, ResourcePool, "resource_pool", PERMISSION_VIEW
                )
            )
            .order_by(ResourcePool.created_at, ResourcePool.id)
        )
    )
    return ResourcePoolListResponse(
        items=[ResourcePoolResponse.model_validate(pool) for pool in pools],
        total=len(pools),
    )


@router.post("/edge-nodes/{id}/probe", response_model=EdgeNodeProbeResponse)
async def probe_edge_node(
    id: str,
    deadline: EdgeInventoryDeadlineDependency,
    service: EdgeInventoryServiceDependency,
    actor: User = Depends(require_admin),
    session: Session = Depends(get_db_session),
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


@router.post("/nodes/{node_id}/refresh", response_model=EdgeNodeProbeResponse)
async def refresh_node_inventory(
    node_id: str,
    deadline: EdgeInventoryDeadlineDependency,
    service: EdgeInventoryServiceDependency,
    actor: User = Depends(require_admin),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> EdgeNodeProbeResponse:
    node = session.get(ComputeNode, node_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")
    try:
        response = await _invoke_with_deadline(
            lambda: _invoke(lambda: service.probe(node_id=node_id, deadline=deadline)),
            deadline,
        )
        snapshot = InventorySnapshot.model_validate(response["inventory"])
        pool = _persist_probe_inventory(session, node_id, snapshot, deadline=deadline)
    except HTTPException as error:
        mark_inventory_failure(
            node,
            "Node inventory refresh failed",
            offline_after_seconds=settings.agent_offline_after_seconds,
        )
        record_audit(
            session,
            actor,
            "node.probe",
            "compute_node",
            node_id,
            "failure",
            None,
            {"status_code": error.status_code},
        )
        session.commit()
        raise
    except (KeyError, TypeError, ValidationError):
        mark_inventory_failure(
            node,
            "Node inventory response was invalid",
            offline_after_seconds=settings.agent_offline_after_seconds,
        )
        record_audit(
            session,
            actor,
            "node.probe",
            "compute_node",
            node_id,
            "failure",
            None,
            {"reason": "invalid_inventory"},
        )
        session.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Edge inventory probe returned invalid data",
        ) from None

    record_audit(
        session,
        actor,
        "node.probe",
        "compute_node",
        node_id,
        "success",
        None,
        {"supported": snapshot.supported, "resource_pool_id": pool.id if pool else None},
    )
    session.commit()
    return EdgeNodeProbeResponse(
        status="ok",
        node_id=node_id,
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
    actor: User = Depends(require_admin),
    session: Session = Depends(get_db_session),
) -> NodeResponse:
    require_resource_permission(session, actor, "node", node_id, PERMISSION_USE)
    require_resource_permission(
        session, actor, "resource_pool", request.resource_pool_id, PERMISSION_USE
    )
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


@router.post("/nodes/{node_id}/enable", response_model=NodeResponse)
def enable_node(
    node_id: str,
    actor: User = Depends(require_admin),
    session: Session = Depends(get_db_session),
) -> NodeResponse:
    node = _admin_node(session, node_id)
    node.enabled = True
    if node.status == "disabled":
        node.status = "offline"
    record_audit(session, actor, "node.enable", "compute_node", node.id, "success", None, {})
    session.commit()
    return NodeResponse.model_validate(node)


@router.post("/nodes/{node_id}/disable", response_model=NodeResponse)
def disable_node(
    node_id: str,
    actor: User = Depends(require_admin),
    session: Session = Depends(get_db_session),
) -> NodeResponse:
    node = _admin_node(session, node_id)
    node.enabled = False
    node.status = "disabled"
    record_audit(session, actor, "node.disable", "compute_node", node.id, "success", None, {})
    session.commit()
    return NodeResponse.model_validate(node)


@router.delete("/nodes/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_node(
    node_id: str,
    actor: User = Depends(require_admin),
    session: Session = Depends(get_db_session),
) -> None:
    node = _admin_node(session, node_id)
    has_history = session.scalar(
        select(RemoteExecution.id).where(RemoteExecution.node_id == node_id).limit(1)
    ) or session.scalar(
        select(DeploymentInstance.id).where(DeploymentInstance.node_id == node_id).limit(1)
    )
    if has_history:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Node with workload history cannot be deleted; disable it instead",
        )
    session.execute(delete(NodeEvent).where(NodeEvent.node_id == node_id))
    session.execute(delete(NodeCommand).where(NodeCommand.node_id == node_id))
    session.execute(delete(EdgeSshCredential).where(EdgeSshCredential.node_id == node_id))
    session.execute(
        update(AgentEnrollmentToken)
        .where(AgentEnrollmentToken.node_id == node_id)
        .values(node_id=None)
    )
    record_audit(session, actor, "node.delete", "compute_node", node.id, "success", None, {"name": node.name})
    session.delete(node)
    session.commit()


def _admin_node(session: Session, node_id: str) -> ComputeNode:
    node = session.get(ComputeNode, node_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")
    return node


def _persist_probe_inventory(
    session: Session,
    node_id: str,
    snapshot: InventorySnapshot,
    *,
    deadline: float,
    commit: bool = True,
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
        "cpu_utilization_percent": snapshot.cpu_utilization_percent,
        "memory_total_kib": snapshot.memory_total_kib,
        "memory_available_kib": snapshot.memory_available_kib,
        "disk_total_bytes": snapshot.disk_total_bytes,
        "disk_available_bytes": snapshot.disk_available_bytes,
        "gpu_count": len(snapshot.gpus) or int(snapshot.platform_kind == "jetson"),
        "gpu_memory_total_mib": sum(
            gpu.memory_total_mib or 0 for gpu in snapshot.gpus
        ),
        "gpu_memory_used_mib": sum(gpu.memory_used_mib or 0 for gpu in snapshot.gpus),
        "gpu_utilization_percent": max(
            (gpu.utilization_percent or 0 for gpu in snapshot.gpus),
            default=0,
        ),
        "gpu_temperature_celsius": max(
            (gpu.temperature_celsius or 0 for gpu in snapshot.gpus),
            default=0,
        ),
        "gpu_power_draw_watts": sum(gpu.power_draw_watts or 0 for gpu in snapshot.gpus),
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
    node.fingerprint.pop("inventory_error", None)
    node.inventory_refreshed_at = datetime.now(UTC)
    node.last_seen_at = node.inventory_refreshed_at
    node.resource_revision += 1

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
            if node.organization_id is None or node.owner_user_id is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Node ownership is unavailable",
                )
            pool = _get_or_create_inventory_pool(
                session,
                snapshot,
                organization_id=node.organization_id,
                owner_user_id=node.owner_user_id,
            )
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
    if commit:
        try:
            session.commit()
        except Exception:
            session.rollback()
            raise
    else:
        session.flush()
    return pool


def _get_or_create_inventory_pool(
    session: Session,
    snapshot: InventorySnapshot,
    *,
    organization_id: str,
    owner_user_id: str,
) -> ResourcePool:
    key = compatibility_key(snapshot)
    pool = session.scalar(select(ResourcePool).where(ResourcePool.name == key))
    if pool is not None:
        _require_resource_pool_accepts_inventory(pool, snapshot)
        return pool
    policy = compatibility_policy(snapshot)
    pool = ResourcePool(
        name=key,
        organization_id=organization_id,
        owner_user_id=owner_user_id,
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
def drain_node(
    node_id: str,
    actor: User = Depends(require_admin),
    session: Session = Depends(get_db_session),
) -> NodeResponse:
    node = session.get(ComputeNode, node_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")
    node.status = "draining"
    session.commit()
    return NodeResponse.model_validate(node)
