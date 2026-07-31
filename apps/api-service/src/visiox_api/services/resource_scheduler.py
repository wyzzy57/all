from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from visiox_db.models import (
    ComputeNode,
    ResourceAllocationPolicy,
    ResourceGrant,
    ResourcePool,
    User,
    UserGroupMembership,
)
from visiox_db.models.identity import (
    PERMISSION_USE,
    PRINCIPAL_GROUP,
    PRINCIPAL_ORGANIZATION,
    PRINCIPAL_USER,
    ROLE_ADMIN,
)
from visiox_edge_executor_worker.distributed import DistributedPlan


@dataclass(frozen=True, slots=True)
class WorkloadRequirements:
    gpu_count: int = 0
    min_gpu_memory_mib: int = 0
    architecture: str | None = None
    platform_kind: str | None = None
    workload_kind: str = "training"
    requested_instances: int = 1


class ResourceSchedulingError(Exception):
    def __init__(self, code: str, constraints: dict[str, object] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.constraints = constraints or {}


def freeze_distributed_allocation(
    plan: DistributedPlan,
) -> tuple[dict[str, object], dict[str, object]]:
    ranks = [
        {
            "node_id": rank.node_id,
            "node_rank": rank.node_rank,
            "lan_address": rank.lan_address,
            "gpu_uuids": list(rank.gpu_uuids),
        }
        for rank in plan.nodes
    ]
    return (
        {
            "kind": "distributed",
            "resource_pool_id": plan.resource_pool_id,
            "gpu_count": plan.world_size,
        },
        {
            "kind": "distributed",
            "resource_pool_id": plan.resource_pool_id,
            "node_ids": [rank.node_id for rank in plan.nodes],
            "ranks": ranks,
            "world_size": plan.world_size,
            "master_addr": plan.master_addr,
            "master_port": plan.master_port,
            "platform_kind": plan.platform_kind,
            "compatibility_key": plan.compatibility_key,
        },
    )


def list_schedulable_nodes(
    session: Session,
    actor: User,
    workload: WorkloadRequirements,
) -> list[ComputeNode]:
    nodes = list(
        session.scalars(
            select(ComputeNode)
            .where(
                ComputeNode.enabled.is_(True),
                ComputeNode.status == "online",
                ComputeNode.resource_pool_id.is_not(None),
            )
            .order_by(ComputeNode.id)
        )
    )
    result: list[ComputeNode] = []
    for node in nodes:
        pool = session.get(ResourcePool, node.resource_pool_id)
        if pool is None or not pool.enabled:
            continue
        if actor.role != ROLE_ADMIN and not _can_use_pool(session, actor, pool.id):
            continue
        if not _node_satisfies(node, workload):
            continue
        result.append(node)
    return result


def assert_allocation_available(
    session: Session,
    actor: User,
    resource_pool_id: str,
    workload: WorkloadRequirements,
) -> None:
    pool = session.get(ResourcePool, resource_pool_id)
    if pool is None or not pool.enabled:
        raise ResourceSchedulingError(
            "NO_COMPATIBLE_NODE", {"resource_pool_id": resource_pool_id}
        )

    if actor.role != ROLE_ADMIN:
        if not _can_use_pool(session, actor, pool.id):
            raise ResourceSchedulingError("NO_COMPATIBLE_NODE")
        policies = _active_policies(session, actor, pool.id)
        if not policies:
            raise ResourceSchedulingError(
                "RESOURCE_QUOTA_EXCEEDED", {"reason": "allocation_missing"}
            )
        gpu_limit = _effective_limit(policies, "max_gpu_count")
        service_limit = _effective_limit(policies, "max_service_instances")
        if gpu_limit is not None and workload.gpu_count > gpu_limit:
            raise ResourceSchedulingError(
                "RESOURCE_QUOTA_EXCEEDED",
                {
                    "resource": "gpu_count",
                    "limit": gpu_limit,
                    "requested": workload.gpu_count,
                },
            )
        if (
            workload.workload_kind == "service"
            and service_limit is not None
            and workload.requested_instances > service_limit
        ):
            raise ResourceSchedulingError(
                "RESOURCE_QUOTA_EXCEEDED",
                {
                    "resource": "service_instances",
                    "limit": service_limit,
                    "requested": workload.requested_instances,
                },
            )

    candidates = [
        node
        for node in list_schedulable_nodes(session, actor, workload)
        if node.resource_pool_id == resource_pool_id
    ]
    if not candidates:
        raise ResourceSchedulingError(
            "NO_COMPATIBLE_NODE",
            {
                "architecture": workload.architecture,
                "platform_kind": workload.platform_kind,
                "gpu_count": workload.gpu_count,
                "min_gpu_memory_mib": workload.min_gpu_memory_mib,
            },
        )


def _principal_filters(session: Session, actor: User):
    group_ids = list(
        session.scalars(
            select(UserGroupMembership.group_id).where(
                UserGroupMembership.user_id == actor.id
            )
        )
    )
    filters = [
        and_(
            ResourceGrant.principal_type == PRINCIPAL_USER,
            ResourceGrant.principal_id == actor.id,
        ),
        and_(
            ResourceGrant.principal_type == PRINCIPAL_ORGANIZATION,
            ResourceGrant.principal_id == actor.organization_id,
        ),
    ]
    if group_ids:
        filters.append(
            and_(
                ResourceGrant.principal_type == PRINCIPAL_GROUP,
                ResourceGrant.principal_id.in_(group_ids),
            )
        )
    return filters, group_ids


def _can_use_pool(session: Session, actor: User, pool_id: str) -> bool:
    filters, _ = _principal_filters(session, actor)
    now = datetime.now(UTC)
    grants = session.scalars(
        select(ResourceGrant).where(
            ResourceGrant.organization_id == actor.organization_id,
            ResourceGrant.resource_type == "resource_pool",
            ResourceGrant.resource_id == pool_id,
            or_(*filters),
        )
    )
    return any(
        PERMISSION_USE in grant.permissions
        and (grant.expires_at is None or _as_utc(grant.expires_at) > now)
        for grant in grants
    )


def _active_policies(
    session: Session,
    actor: User,
    pool_id: str,
) -> list[ResourceAllocationPolicy]:
    _, group_ids = _principal_filters(session, actor)
    principal_filters = [
        and_(
            ResourceAllocationPolicy.principal_type == PRINCIPAL_USER,
            ResourceAllocationPolicy.principal_id == actor.id,
        ),
        and_(
            ResourceAllocationPolicy.principal_type == PRINCIPAL_ORGANIZATION,
            ResourceAllocationPolicy.principal_id == actor.organization_id,
        ),
    ]
    if group_ids:
        principal_filters.append(
            and_(
                ResourceAllocationPolicy.principal_type == PRINCIPAL_GROUP,
                ResourceAllocationPolicy.principal_id.in_(group_ids),
            )
        )
    now = datetime.now(UTC)
    return [
        policy
        for policy in session.scalars(
            select(ResourceAllocationPolicy).where(
                ResourceAllocationPolicy.organization_id == actor.organization_id,
                ResourceAllocationPolicy.resource_pool_id == pool_id,
                or_(*principal_filters),
            )
        )
        if policy.expires_at is None or _as_utc(policy.expires_at) > now
    ]


def _effective_limit(
    policies: list[ResourceAllocationPolicy],
    field: str,
) -> int | None:
    limits = [
        getattr(policy, field)
        for policy in policies
        if getattr(policy, field) is not None
    ]
    return max(limits) if limits else None


def _node_satisfies(node: ComputeNode, workload: WorkloadRequirements) -> bool:
    if workload.architecture is not None and node.architecture != workload.architecture:
        return False
    if (
        workload.platform_kind is not None
        and node.platform_kind != workload.platform_kind
    ):
        return False
    gpu_count = int(node.resources.get("gpu_count") or 0)
    gpu_memory = int(node.resources.get("gpu_memory_total_mib") or 0)
    return gpu_count >= workload.gpu_count and gpu_memory >= workload.min_gpu_memory_mib


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
