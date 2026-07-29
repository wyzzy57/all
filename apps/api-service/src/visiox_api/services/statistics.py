from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any

from sqlalchemy import String, and_, func, select
from sqlalchemy.orm import Session

from visiox_api.services.authorization import authorized_resource_predicate
from visiox_db.models.datasets import Dataset
from visiox_db.models.edge_compute import ComputeNode, ResourcePool
from visiox_db.models.identity import (
    PERMISSION_USE,
    PRINCIPAL_GROUP,
    ROLE_ADMIN,
    ResourceAllocationPolicy,
    User,
    UserGroup,
    UserGroupMembership,
)
from visiox_db.models.model_space import (
    DeploymentInstance,
    DeploymentService,
    DistributedTrainingRun,
    TrainingJob,
    TrainingPipeline,
)


_RESOURCE_MODELS = {
    "pipelines": (TrainingPipeline, "pipeline"),
    "datasets": (Dataset, "dataset"),
    "training_jobs": (TrainingJob, "training_job"),
    "services": (DeploymentService, "service"),
    "nodes": (ComputeNode, "node"),
}

_ACTIVE_TRAINING_RUN_STATUSES = {"queued", "running", "resuming", "staging", "training"}
_ACTIVE_SERVICE_INSTANCE_STATUSES = {
    "queued",
    "starting",
    "running",
    "deploying",
    "upgrade_queued",
    "rollback_queued",
}
_GROUP_ALLOCATION_LIMITATION = (
    "Usage is aggregated by the union of allocated pools because the current schema "
    "does not record which group principal scheduled each workload."
)
_FAILURE_STATUSES = {
    "pipeline": {"failed", "error", "aborted", "cancelled"},
    "training_job": {"failed", "error", "aborted", "cancelled"},
    "service": {"failed", "error"},
    "node": {"failed", "error", "offline"},
}


def collect_scoped_statistics(
    session: Session,
    actor: User,
    *,
    months: int = 6,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return aggregate-only statistics for resources visible to ``actor``."""
    if months < 1:
        raise ValueError("months must be at least 1")

    generated_at = _as_utc(now or datetime.now(timezone.utc))
    labels, start, end = _month_window(generated_at, months)
    totals: dict[str, int] = {}
    status_buckets: dict[str, list[dict[str, int | str]]] = {}
    creation_trends: dict[str, dict[str, list[int] | list[str]]] = {}

    for key, (model, resource_type) in _RESOURCE_MODELS.items():
        predicate = authorized_resource_predicate(
            session,
            actor,
            model,
            resource_type,
        )
        totals[key] = int(session.scalar(select(func.count(model.id)).where(predicate)) or 0)
        status_buckets[key] = _status_buckets(session, model, predicate)
        creation_trends[key] = _creation_trend(
            session,
            model,
            predicate,
            labels,
            start,
            end,
        )

    totals.update(_identity_totals(session, actor))
    return {
        "generated_at": generated_at,
        "totals": totals,
        "status_buckets": status_buckets,
        "creation_trends": creation_trends,
    }


def collect_admin_overview_statistics(
    session: Session,
    actor: User,
    *,
    months: int = 6,
) -> dict[str, Any]:
    """Return organization-wide administrator statistics with safe failure links."""
    if actor.role != ROLE_ADMIN:
        raise PermissionError("Administrator role is required")
    overview = collect_scoped_statistics(session, actor, months=months)
    overview["recent_failures"] = _recent_failures(session, actor.organization_id)
    return overview


def collect_live_resource_statistics(
    session: Session,
    actor: User,
    *,
    now: datetime | None = None,
    stale_after: timedelta = timedelta(minutes=5),
) -> dict[str, Any]:
    """Return current, authorization-scoped node and service resource aggregates."""
    if stale_after.total_seconds() <= 0:
        raise ValueError("stale_after must be positive")

    generated_at = _as_utc(now or datetime.now(timezone.utc))
    nodes = list(
        session.scalars(
            select(ComputeNode)
            .where(
                authorized_resource_predicate(session, actor, ComputeNode, "node")
            )
            .order_by(ComputeNode.created_at, ComputeNode.id)
        )
    )
    node_ids = {node.id for node in nodes}
    fresh_nodes, freshness = _fresh_nodes(nodes, generated_at, stale_after)
    services = list(
        session.scalars(
            select(DeploymentService)
            .where(
                authorized_resource_predicate(
                    session, actor, DeploymentService, "service"
                )
            )
            .order_by(DeploymentService.created_at, DeploymentService.id)
        )
    )
    jobs = list(
        session.scalars(
            select(TrainingJob).where(
                authorized_resource_predicate(session, actor, TrainingJob, "training_job")
            )
        )
    )

    return {
        "generated_at": generated_at,
        "staleness_threshold_seconds": int(stale_after.total_seconds()),
        "nodes": {
            "status_buckets": _status_buckets_for_values(nodes),
            "freshness": freshness,
            "resource_usage": _node_resource_usage(fresh_nodes),
        },
        "gpus": {"series": _gpu_series(fresh_nodes)},
        "services": _service_health_statistics(session, services),
        "group_allocation_usage": _group_allocation_usage(
            session,
            actor,
            node_ids,
            services,
            jobs,
            generated_at,
        ),
    }


def _recent_failures(session: Session, organization_id: str | None) -> list[dict[str, Any]]:
    if organization_id is None:
        return []

    candidates: list[tuple[datetime, dict[str, Any]]] = []
    pipelines = session.scalars(
        select(TrainingPipeline)
        .where(
            TrainingPipeline.organization_id == organization_id,
            TrainingPipeline.status.in_(_FAILURE_STATUSES["pipeline"]),
        )
        .order_by(TrainingPipeline.updated_at.desc())
        .limit(10)
    )
    for pipeline in pipelines:
        candidates.append(
            _failure_record("pipeline", pipeline.id, pipeline.name, pipeline.status, pipeline.updated_at)
        )

    jobs = session.execute(
        select(TrainingJob, TrainingPipeline.name)
        .outerjoin(
            TrainingPipeline,
            and_(
                TrainingPipeline.id == TrainingJob.pipeline_id,
                TrainingPipeline.organization_id == TrainingJob.organization_id,
            ),
        )
        .where(
            TrainingJob.organization_id == organization_id,
            TrainingJob.status.in_(_FAILURE_STATUSES["training_job"]),
        )
        .order_by(TrainingJob.updated_at.desc())
        .limit(10)
    )
    for job, pipeline_name in jobs:
        candidates.append(
            _failure_record(
                "training_job",
                job.id,
                pipeline_name or f"Training job {job.id[:8]}",
                job.status,
                job.updated_at,
            )
        )

    services = session.scalars(
        select(DeploymentService)
        .where(
            DeploymentService.organization_id == organization_id,
            DeploymentService.status.in_(_FAILURE_STATUSES["service"]),
        )
        .order_by(DeploymentService.updated_at.desc())
        .limit(10)
    )
    for service in services:
        candidates.append(
            _failure_record("service", service.id, service.name, service.status, service.updated_at)
        )

    nodes = session.scalars(
        select(ComputeNode)
        .where(
            ComputeNode.organization_id == organization_id,
            ComputeNode.status.in_(_FAILURE_STATUSES["node"]),
        )
        .order_by(ComputeNode.updated_at.desc())
        .limit(10)
    )
    for node in nodes:
        candidates.append(
            _failure_record("node", node.id, node.name, node.status, node.updated_at)
        )

    candidates.sort(key=lambda item: item[0], reverse=True)
    return [record for _, record in candidates[:10]]


def _failure_record(
    resource_type: str,
    resource_id: str,
    name: str,
    status: str,
    updated_at: datetime,
) -> tuple[datetime, dict[str, Any]]:
    timestamp = _as_utc(updated_at)
    return timestamp, {
        "resource_type": resource_type,
        "resource_id": resource_id,
        "name": name,
        "status": status,
        "updated_at": timestamp,
    }


def _status_buckets(
    session: Session,
    model: type,
    predicate: Any,
) -> list[dict[str, int | str]]:
    rows = session.execute(
        select(model.status, func.count(model.id))
        .where(predicate)
        .group_by(model.status)
        .order_by(model.status)
    ).all()
    return [{"label": str(status), "value": int(count)} for status, count in rows]


def _status_buckets_for_values(values: list[Any]) -> list[dict[str, int | str]]:
    counts: dict[str, int] = {}
    for value in values:
        label = str(value.status)
        counts[label] = counts.get(label, 0) + 1
    return [{"label": label, "value": count} for label, count in sorted(counts.items())]


def _fresh_nodes(
    nodes: list[ComputeNode],
    now: datetime,
    stale_after: timedelta,
) -> tuple[list[ComputeNode], dict[str, int | datetime | None]]:
    fresh: list[ComputeNode] = []
    stale = 0
    unknown = 0
    refreshed_at: list[datetime] = []
    for node in nodes:
        if node.inventory_refreshed_at is None:
            unknown += 1
            continue
        timestamp = _as_utc(node.inventory_refreshed_at)
        if now - timestamp > stale_after:
            stale += 1
            continue
        fresh.append(node)
        refreshed_at.append(timestamp)
    return fresh, {
        "fresh": len(fresh),
        "stale": stale,
        "unknown": unknown,
        "oldest_fresh_at": min(refreshed_at) if refreshed_at else None,
        "newest_fresh_at": max(refreshed_at) if refreshed_at else None,
    }


def _node_resource_usage(nodes: list[ComputeNode]) -> dict[str, dict[str, float | int | None]]:
    values = {
        "cpu_utilization_percent": [
            _number(node.resources.get("cpu_utilization_percent")) for node in nodes
        ],
        "memory_utilization_percent": [_memory_utilization(node.resources) for node in nodes],
        "disk_utilization_percent": [_disk_utilization(node.resources) for node in nodes],
    }
    return {
        name: _aggregate_optional_numbers(samples)
        for name, samples in values.items()
    }


def _aggregate_optional_numbers(values: list[float | None]) -> dict[str, float | int | None]:
    available = [value for value in values if value is not None]
    return {
        "value": sum(available) / len(available) if available else None,
        "available": len(available),
        "unavailable": len(values) - len(available),
    }


def _memory_utilization(resources: dict[str, Any]) -> float | None:
    direct = _number(resources.get("memory_utilization_percent"))
    if direct is not None:
        return direct
    return _used_percentage(
        _number(resources.get("memory_total_kib")),
        _number(resources.get("memory_available_kib")),
    )


def _disk_utilization(resources: dict[str, Any]) -> float | None:
    direct = _number(resources.get("disk_utilization_percent"))
    if direct is not None:
        return direct
    return _used_percentage(
        _number(resources.get("disk_total_bytes")),
        _number(resources.get("disk_available_bytes")),
    )


def _used_percentage(total: float | None, available: float | None) -> float | None:
    if total is None or available is None or total <= 0:
        return None
    return (total - available) / total * 100


def _gpu_series(nodes: list[ComputeNode]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for node in nodes:
        refreshed_at = _as_utc(node.inventory_refreshed_at)  # fresh nodes always have it
        for position, gpu in enumerate(_inventory_gpus(node.fingerprint)):
            identifier = gpu.get("uuid")
            index = gpu.get("index", position)
            key = _anonymous_gpu_key(node.id, identifier, index)
            used = _number(gpu.get("memory_used_mib", gpu.get("memory_used_mb")))
            total = _number(gpu.get("memory_total_mib", gpu.get("memory_total_mb")))
            result.append(
                {
                    "key": key,
                    "refreshed_at": refreshed_at,
                    "utilization_percent": _metric_value(
                        _number(gpu.get("utilization_percent"))
                    ),
                    "memory_used_mib": _metric_value(used),
                    "memory_total_mib": _metric_value(total),
                    "memory_utilization_percent": _metric_value(
                        _used_percentage(total, total - used)
                        if total is not None and used is not None
                        else None
                    ),
                }
            )
    return result


def _inventory_gpus(fingerprint: dict[str, Any]) -> list[dict[str, Any]]:
    snapshot = fingerprint.get("inventory_snapshot")
    if not isinstance(snapshot, dict):
        return []
    gpus = snapshot.get("gpus")
    if not isinstance(gpus, list):
        nvidia = snapshot.get("nvidia")
        gpus = nvidia.get("gpus") if isinstance(nvidia, dict) else []
    return [gpu for gpu in gpus if isinstance(gpu, dict)] if isinstance(gpus, list) else []


def _anonymous_node_namespace(node_id: str) -> str:
    return sha256(node_id.encode("utf-8")).hexdigest()[:12]


def _anonymous_gpu_key(node_id: str, identifier: Any, index: Any) -> str:
    stable_identifier = identifier if isinstance(identifier, str) and identifier else f"index:{index}"
    digest = sha256(f"{node_id}:{stable_identifier}".encode("utf-8")).hexdigest()[:16]
    return f"gpu:{_anonymous_node_namespace(node_id)}:{digest}"


def _metric_value(value: float | None) -> dict[str, float | bool | None]:
    return {"value": value, "available": value is not None}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _service_health_statistics(
    session: Session,
    services: list[DeploymentService],
) -> dict[str, Any]:
    service_ids = [service.id for service in services]
    instances = (
        list(
            session.scalars(
                select(DeploymentInstance).where(
                    DeploymentInstance.deployment_service_id.in_(service_ids)
                )
            )
        )
        if service_ids
        else []
    )
    buckets: dict[str, int] = {}
    checked_at: list[datetime] = []
    for instance in instances:
        label = instance.health_status or "unknown"
        buckets[label] = buckets.get(label, 0) + 1
        if instance.health_checked_at is not None:
            checked_at.append(_as_utc(instance.health_checked_at))
    return {
        "calls": sum(int(service.calls) for service in services),
        "health_buckets": [
            {"label": label, "value": count} for label, count in sorted(buckets.items())
        ],
        "instances": len(instances),
        "latest_health_checked_at": max(checked_at) if checked_at else None,
    }


def _group_allocation_usage(
    session: Session,
    actor: User,
    node_ids: set[str],
    services: list[DeploymentService],
    jobs: list[TrainingJob],
    now: datetime,
) -> dict[str, int | str]:
    group_ids = _visible_group_ids(session, actor)
    if not group_ids:
        return _empty_group_allocation_usage()
    policies = list(
        session.scalars(
            select(ResourceAllocationPolicy).where(
                ResourceAllocationPolicy.organization_id == actor.organization_id,
                ResourceAllocationPolicy.principal_type == PRINCIPAL_GROUP,
                ResourceAllocationPolicy.principal_id.in_(group_ids),
                (ResourceAllocationPolicy.expires_at.is_(None))
                | (ResourceAllocationPolicy.expires_at > now),
            )
        )
    )
    policy_pool_ids = {policy.resource_pool_id for policy in policies}
    pool_ids = set(
        session.scalars(
            select(ResourcePool.id).where(
                ResourcePool.id.in_(policy_pool_ids),
                authorized_resource_predicate(
                    session,
                    actor,
                    ResourcePool,
                    "resource_pool",
                    PERMISSION_USE,
                ),
            )
        )
    )
    if not pool_ids:
        return _empty_group_allocation_usage()
    policies = [policy for policy in policies if policy.resource_pool_id in pool_ids]
    eligible_node_ids = set(
        session.scalars(
            select(ComputeNode.id).where(
                ComputeNode.id.in_(node_ids),
                ComputeNode.resource_pool_id.in_(pool_ids),
            )
        )
    )
    job_ids = {job.id for job in jobs}
    runs = (
        list(
            session.scalars(
                select(DistributedTrainingRun).where(
                    DistributedTrainingRun.resource_pool_id.in_(pool_ids),
                    DistributedTrainingRun.status.in_(_ACTIVE_TRAINING_RUN_STATUSES),
                    DistributedTrainingRun.training_job_id.in_(job_ids),
                )
            )
        )
        if job_ids
        else []
    )
    active_training_runs = sum(
        bool(eligible_node_ids.intersection(run.node_ids)) for run in runs
    )
    service_ids = [service.id for service in services]
    active_service_instances = (
        int(
            session.scalar(
                select(func.count(DeploymentInstance.id)).where(
                    DeploymentInstance.deployment_service_id.in_(service_ids),
                    DeploymentInstance.node_id.in_(eligible_node_ids),
                    DeploymentInstance.status.in_(_ACTIVE_SERVICE_INSTANCE_STATUSES),
                )
            )
            or 0
        )
        if service_ids and eligible_node_ids
        else 0
    )
    return {
        "policy_count": len(policies),
        "resource_pool_count": len(pool_ids),
        "active_training_runs": active_training_runs,
        "active_service_instances": active_service_instances,
        "active_workloads": active_training_runs + active_service_instances,
        "limitation": _GROUP_ALLOCATION_LIMITATION,
    }


def _visible_group_ids(session: Session, actor: User) -> set[str]:
    if actor.role == ROLE_ADMIN:
        return set(
            session.scalars(
                select(UserGroup.id).where(UserGroup.organization_id == actor.organization_id)
            )
        )
    return set(
        session.scalars(
            select(UserGroupMembership.group_id)
            .join(UserGroup, UserGroup.id == UserGroupMembership.group_id)
            .where(
                UserGroupMembership.user_id == actor.id,
                UserGroup.organization_id == actor.organization_id,
            )
        )
    )


def _empty_group_allocation_usage() -> dict[str, int | str]:
    return {
        "policy_count": 0,
        "resource_pool_count": 0,
        "active_training_runs": 0,
        "active_service_instances": 0,
        "active_workloads": 0,
        "limitation": _GROUP_ALLOCATION_LIMITATION,
    }


def _creation_trend(
    session: Session,
    model: type,
    predicate: Any,
    labels: list[str],
    start: datetime,
    end: datetime,
) -> dict[str, list[int] | list[str]]:
    month = _database_month_label(session, model.created_at)
    rows = session.execute(
        select(month, func.count(model.id))
        .where(predicate, model.created_at >= start, model.created_at < end)
        .group_by(month)
        .order_by(month)
    ).all()
    counts = {str(label): int(value) for label, value in rows}
    return {"labels": labels, "values": [counts.get(label, 0) for label in labels]}


def _identity_totals(session: Session, actor: User) -> dict[str, int]:
    if actor.role == ROLE_ADMIN:
        return {
            "users": int(
                session.scalar(
                    select(func.count(User.id)).where(
                        User.organization_id == actor.organization_id
                    )
                )
                or 0
            ),
            "groups": int(
                session.scalar(
                    select(func.count(UserGroup.id)).where(
                        UserGroup.organization_id == actor.organization_id
                    )
                )
                or 0
            ),
        }

    group_count = session.scalar(
        select(func.count(UserGroupMembership.group_id))
        .join(UserGroup, UserGroup.id == UserGroupMembership.group_id)
        .where(
            UserGroupMembership.user_id == actor.id,
            UserGroup.organization_id == actor.organization_id,
        )
    )
    return {"users": 1, "groups": int(group_count or 0)}


def _database_month_label(session: Session, created_at: Any) -> Any:
    dialect_name = session.get_bind().dialect.name
    if dialect_name == "postgresql":
        utc_timestamp = func.timezone("UTC", created_at)
        return func.to_char(func.date_trunc("month", utc_timestamp), "YYYY-MM")
    return func.strftime("%Y-%m", created_at).cast(String)


def _month_window(now: datetime, months: int) -> tuple[list[str], datetime, datetime]:
    current = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    starts = [_shift_month(current, offset) for offset in range(-(months - 1), 1)]
    return (
        [item.strftime("%Y-%m") for item in starts],
        starts[0],
        _shift_month(current, 1),
    )


def _shift_month(value: datetime, offset: int) -> datetime:
    month_index = value.year * 12 + (value.month - 1) + offset
    year, month_zero_based = divmod(month_index, 12)
    return value.replace(year=year, month=month_zero_based + 1)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
