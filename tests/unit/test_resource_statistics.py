from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from hashlib import sha256

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from visiox_api.services.statistics import collect_live_resource_statistics
from visiox_db.base import Base
from visiox_db.models.edge_compute import ComputeNode, ResourcePool
from visiox_db.models.identity import (
    PERMISSION_USE,
    PRINCIPAL_GROUP,
    PRINCIPAL_USER,
    ROLE_ADMIN,
    Organization,
    ResourceAllocationPolicy,
    ResourceGrant,
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


NOW = datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db_session:
        yield db_session
    engine.dispose()


def test_live_resource_statistics_preserve_zero_values_and_mark_stale_or_unknown_samples(
    session: Session,
) -> None:
    organization = _organization(session)
    admin = _user(session, organization.id, "admin", role=ROLE_ADMIN)
    pool = _pool(session, organization.id, admin.id, "pool-main")
    _node(
        session,
        organization.id,
        admin.id,
        "node-telemetry",
        pool.id,
        status="online",
        refreshed_at=NOW - timedelta(minutes=2),
        resources={
            "cpu_utilization_percent": 0.0,
            "memory_total_kib": 1000,
            "memory_available_kib": 500,
            "disk_total_bytes": 1000,
            "disk_available_bytes": 250,
        },
        fingerprint={
            "inventory_snapshot": {
                "nvidia": {
                    "gpus": [
                        {
                            "uuid": "GPU-fresh-0",
                            "index": 0,
                            "utilization_percent": 0.0,
                            "memory_total_mib": 8192,
                            "memory_used_mib": 0,
                        },
                        {
                            "index": 1,
                            "utilization_percent": 72.0,
                            "memory_total_mib": 8192,
                            "memory_used_mib": 4096,
                        },
                    ]
                }
            }
        },
    )
    _node(
        session,
        organization.id,
        admin.id,
        "offline",
        pool.id,
        status="offline",
        refreshed_at=NOW - timedelta(minutes=3),
        resources={},
        fingerprint={},
    )
    _node(
        session,
        organization.id,
        admin.id,
        "stale",
        pool.id,
        status="online",
        refreshed_at=NOW - timedelta(minutes=31),
        resources={"cpu_utilization_percent": 88.0},
        fingerprint={
            "inventory_snapshot": {
                "nvidia": {
                    "gpus": [
                        {
                            "uuid": "GPU-stale",
                            "utilization_percent": 99.0,
                            "memory_total_mib": 8192,
                            "memory_used_mib": 8192,
                        }
                    ]
                }
            }
        },
    )
    _node(
        session,
        organization.id,
        admin.id,
        "unknown",
        pool.id,
        status="online",
        refreshed_at=None,
        resources={},
        fingerprint={},
    )
    session.commit()

    result = collect_live_resource_statistics(
        session,
        admin,
        now=NOW,
        stale_after=timedelta(minutes=30),
    )

    assert result["nodes"]["status_buckets"] == [
        {"label": "offline", "value": 1},
        {"label": "online", "value": 3},
    ]
    assert result["nodes"]["freshness"] == {
        "fresh": 2,
        "stale": 1,
        "unknown": 1,
        "oldest_fresh_at": NOW - timedelta(minutes=3),
        "newest_fresh_at": NOW - timedelta(minutes=2),
    }
    assert result["nodes"]["resource_usage"] == {
        "cpu_utilization_percent": {"value": 0.0, "available": 1, "unavailable": 1},
        "memory_utilization_percent": {"value": 50.0, "available": 1, "unavailable": 1},
        "disk_utilization_percent": {"value": 75.0, "available": 1, "unavailable": 1},
    }
    assert result["gpus"]["series"] == [
        {
            "key": result["gpus"]["series"][0]["key"],
            "refreshed_at": NOW - timedelta(minutes=2),
            "utilization_percent": {"value": 0.0, "available": True},
            "memory_used_mib": {"value": 0.0, "available": True},
            "memory_total_mib": {"value": 8192.0, "available": True},
            "memory_utilization_percent": {"value": 0.0, "available": True},
        },
        {
            "key": result["gpus"]["series"][1]["key"],
            "refreshed_at": NOW - timedelta(minutes=2),
            "utilization_percent": {"value": 72.0, "available": True},
            "memory_used_mib": {"value": 4096.0, "available": True},
            "memory_total_mib": {"value": 8192.0, "available": True},
            "memory_utilization_percent": {"value": 50.0, "available": True},
        },
    ]
    assert "GPU-stale" not in repr(result)
    assert "GPU-fresh-0" not in repr(result)
    assert all(item["key"].startswith("gpu:") for item in result["gpus"]["series"])
    assert len({item["key"] for item in result["gpus"]["series"]}) == 2
    assert "node-telemetry" not in repr(result)


def test_live_resource_statistics_namespace_index_only_gpus_per_stable_node_position(
    session: Session,
) -> None:
    organization = _organization(session)
    admin = _user(session, organization.id, "admin", role=ROLE_ADMIN)
    pool = _pool(session, organization.id, admin.id, "pool-main")
    _node(
        session,
        organization.id,
        admin.id,
        "node-one",
        pool.id,
        status="online",
        refreshed_at=NOW,
        resources={},
        fingerprint=_index_only_gpu_snapshot(),
    )
    session.commit()

    initial_key = collect_live_resource_statistics(session, admin, now=NOW)["gpus"]["series"][0]["key"]

    node_two = _node(
        session,
        organization.id,
        admin.id,
        "node-two",
        pool.id,
        status="online",
        refreshed_at=NOW,
        resources={},
        fingerprint=_index_only_gpu_snapshot(),
    )
    session.commit()

    result = collect_live_resource_statistics(session, admin, now=NOW)

    keys = [item["key"] for item in result["gpus"]["series"]]
    assert len(keys) == 2
    assert len(set(keys)) == 2
    assert all(key.startswith("gpu:") for key in keys)
    assert initial_key == keys[0]
    assert len(keys) == len(set(keys))
    assert "node-one" not in repr(result)
    assert "node-two" not in repr(result)

    session.delete(node_two)
    session.commit()

    after_removal = collect_live_resource_statistics(session, admin, now=NOW)
    assert after_removal["gpus"]["series"][0]["key"] == initial_key


def test_live_resource_statistics_use_authorized_services_for_health_and_persisted_calls(
    session: Session,
) -> None:
    organization = _organization(session)
    owner = _user(session, organization.id, "owner")
    member = _user(session, organization.id, "member")
    pool = _pool(session, organization.id, owner.id, "pool-main")
    node = _node(
        session,
        organization.id,
        owner.id,
        "shared-node",
        pool.id,
        status="online",
        refreshed_at=NOW,
        resources={},
        fingerprint={},
    )
    pipeline = _pipeline(session, organization.id, owner.id, "shared-pipeline")
    visible_service = _service(organization.id, owner.id, pipeline.id, "visible", calls=41)
    hidden_service = _service(organization.id, owner.id, pipeline.id, "hidden", calls=99)
    session.add_all([visible_service, hidden_service])
    session.flush()
    session.add_all(
        [
            ResourceGrant(
                organization_id=organization.id,
                resource_type="node",
                resource_id=node.id,
                principal_type=PRINCIPAL_USER,
                principal_id=member.id,
                permissions=["view"],
            ),
            ResourceGrant(
                organization_id=organization.id,
                resource_type="service",
                resource_id=visible_service.id,
                principal_type=PRINCIPAL_USER,
                principal_id=member.id,
                permissions=["view"],
            ),
            DeploymentInstance(
                deployment_service_id=visible_service.id,
                node_id=node.id,
                instance_name="healthy",
                engine="tensorrt",
                status="running",
                health_status="healthy",
                health_checked_at=NOW - timedelta(minutes=1),
            ),
            DeploymentInstance(
                deployment_service_id=visible_service.id,
                node_id=node.id,
                instance_name="failed",
                engine="tensorrt",
                status="failed",
                health_status="unhealthy",
                health_checked_at=NOW - timedelta(minutes=1),
            ),
            DeploymentInstance(
                deployment_service_id=visible_service.id,
                node_id=node.id,
                instance_name="unknown",
                engine="tensorrt",
                status="queued",
                health_status=None,
                health_checked_at=None,
            ),
            DeploymentInstance(
                deployment_service_id=hidden_service.id,
                node_id=node.id,
                instance_name="hidden",
                engine="tensorrt",
                status="running",
                health_status="healthy",
                health_checked_at=NOW,
            ),
        ]
    )
    session.commit()

    result = collect_live_resource_statistics(session, member, now=NOW)

    assert result["services"] == {
        "calls": 41,
        "health_buckets": [
            {"label": "healthy", "value": 1},
            {"label": "unhealthy", "value": 1},
            {"label": "unknown", "value": 1},
        ],
        "instances": 3,
        "latest_health_checked_at": NOW - timedelta(minutes=1),
    }
    assert "hidden" not in repr(result)


def test_group_allocation_usage_counts_active_workloads_without_resource_identifiers(
    session: Session,
) -> None:
    organization = _organization(session)
    owner = _user(session, organization.id, "owner")
    member = _user(session, organization.id, "member")
    group = UserGroup(id="group-operators", organization_id=organization.id, name="Operators")
    session.add_all([group, UserGroupMembership(group_id=group.id, user_id=member.id)])
    pool = _pool(session, organization.id, owner.id, "pool-group")
    unrelated_pool = _pool(session, organization.id, owner.id, "pool-other")
    group_node = _node(
        session,
        organization.id,
        owner.id,
        "group-node",
        pool.id,
        status="online",
        refreshed_at=NOW,
        resources={},
        fingerprint={},
    )
    unrelated_node = _node(
        session,
        organization.id,
        owner.id,
        "other-node",
        unrelated_pool.id,
        status="online",
        refreshed_at=NOW,
        resources={},
        fingerprint={},
    )
    pipeline = _pipeline(session, organization.id, owner.id, "pipeline")
    running_job = TrainingJob(
        pipeline_id=pipeline.id,
        organization_id=organization.id,
        owner_user_id=owner.id,
        status="running",
    )
    completed_job = TrainingJob(
        pipeline_id=pipeline.id,
        organization_id=organization.id,
        owner_user_id=owner.id,
        status="success",
    )
    service = _service(organization.id, owner.id, pipeline.id, "service", calls=0)
    session.add_all([running_job, completed_job, service])
    session.flush()
    session.add_all(
        [
            ResourceAllocationPolicy(
                organization_id=organization.id,
                principal_type=PRINCIPAL_GROUP,
                principal_id=group.id,
                resource_pool_id=pool.id,
                created_by=owner.id,
                max_concurrent_training_jobs=3,
                max_service_instances=4,
            ),
            DistributedTrainingRun(
                training_job_id=running_job.id,
                resource_pool_id=pool.id,
                node_ids=[group_node.id],
                ranks=[],
                master_addr="10.0.0.1",
                master_port=29500,
                status="running",
            ),
            DistributedTrainingRun(
                training_job_id=completed_job.id,
                resource_pool_id=pool.id,
                node_ids=[group_node.id],
                ranks=[],
                master_addr="10.0.0.1",
                master_port=29501,
                status="succeeded",
            ),
            DeploymentInstance(
                deployment_service_id=service.id,
                node_id=group_node.id,
                instance_name="in-group-pool",
                engine="tensorrt",
                status="running",
            ),
            ResourceGrant(
                organization_id=organization.id,
                resource_type="node",
                resource_id=group_node.id,
                principal_type=PRINCIPAL_USER,
                principal_id=member.id,
                permissions=["view"],
            ),
            ResourceGrant(
                organization_id=organization.id,
                resource_type="service",
                resource_id=service.id,
                principal_type=PRINCIPAL_USER,
                principal_id=member.id,
                permissions=["view"],
            ),
            ResourceGrant(
                organization_id=organization.id,
                resource_type="training_job",
                resource_id=running_job.id,
                principal_type=PRINCIPAL_USER,
                principal_id=member.id,
                permissions=["view"],
            ),
            DeploymentInstance(
                deployment_service_id=service.id,
                node_id=unrelated_node.id,
                instance_name="outside-group-pool",
                engine="tensorrt",
                status="running",
            ),
        ]
    )
    session.commit()

    without_pool_use = collect_live_resource_statistics(session, member, now=NOW)

    assert without_pool_use["group_allocation_usage"] == {
        "policy_count": 0,
        "resource_pool_count": 0,
        "active_training_runs": 0,
        "active_service_instances": 0,
        "active_workloads": 0,
        "limitation": (
            "Usage is aggregated by the union of allocated pools because the current schema "
            "does not record which group principal scheduled each workload."
        ),
    }

    session.add(
        ResourceGrant(
            organization_id=organization.id,
            resource_type="resource_pool",
            resource_id=pool.id,
            principal_type=PRINCIPAL_GROUP,
            principal_id=group.id,
            permissions=[PERMISSION_USE],
        )
    )
    session.commit()

    result = collect_live_resource_statistics(session, member, now=NOW)

    assert result["group_allocation_usage"] == {
        "policy_count": 1,
        "resource_pool_count": 1,
        "active_training_runs": 1,
        "active_service_instances": 1,
        "active_workloads": 2,
        "limitation": (
            "Usage is aggregated by the union of allocated pools because the current schema "
            "does not record which group principal scheduled each workload."
        ),
    }
    assert group.id not in repr(result)
    assert pool.id not in repr(result)
    assert group_node.id not in repr(result)


def _organization(session: Session) -> Organization:
    organization = Organization(id="org-1", name="Organization", slug="organization")
    session.add(organization)
    session.flush()
    return organization


def _user(session: Session, organization_id: str, user_id: str, *, role: str = "member") -> User:
    user = User(
        id=user_id,
        organization_id=organization_id,
        username=user_id,
        display_name=user_id,
        email=f"{user_id}@example.test",
        password_hash="hash",
        role=role,
        must_change_password=False,
    )
    session.add(user)
    session.flush()
    return user


def _pool(session: Session, organization_id: str, owner_user_id: str, name: str) -> ResourcePool:
    pool = ResourcePool(
        name=name,
        organization_id=organization_id,
        owner_user_id=owner_user_id,
        kind="x86_nvidia",
        selector={},
        compatibility_policy={},
    )
    session.add(pool)
    session.flush()
    return pool


def _node(
    session: Session,
    organization_id: str,
    owner_user_id: str,
    name: str,
    resource_pool_id: str,
    *,
    status: str,
    refreshed_at: datetime | None,
    resources: dict[str, object],
    fingerprint: dict[str, object],
) -> ComputeNode:
    node = ComputeNode(
        name=name,
        organization_id=organization_id,
        owner_user_id=owner_user_id,
        resource_pool_id=resource_pool_id,
        status=status,
        architecture="x86_64",
        platform_kind="x86_nvidia",
        capabilities={},
        resources=resources,
        fingerprint=fingerprint,
        inventory_refreshed_at=refreshed_at,
        agent_version="1.0",
    )
    session.add(node)
    session.flush()
    return node


def _pipeline(
    session: Session,
    organization_id: str,
    owner_user_id: str,
    name: str,
) -> TrainingPipeline:
    pipeline = TrainingPipeline(
        name=name,
        organization_id=organization_id,
        owner_user_id=owner_user_id,
        task="detect",
        scale="n",
        status="running",
    )
    session.add(pipeline)
    session.flush()
    return pipeline


def _service(
    organization_id: str,
    owner_user_id: str,
    pipeline_id: str,
    name: str,
    *,
    calls: int,
) -> DeploymentService:
    return DeploymentService(
        name=name,
        organization_id=organization_id,
        owner_user_id=owner_user_id,
        pipeline_id=pipeline_id,
        model_name="model",
        model_weight="best.pt",
        environment="gpu",
        endpoint="pending",
        calls=calls,
    )


def _index_only_gpu_snapshot() -> dict[str, object]:
    return {
        "inventory_snapshot": {
            "nvidia": {
                "gpus": [
                    {
                        "index": 0,
                        "utilization_percent": 10.0,
                        "memory_total_mib": 8192,
                        "memory_used_mib": 1024,
                    }
                ]
            }
        }
    }


def _anonymous_node_key(node_id: str) -> str:
    return sha256(node_id.encode("utf-8")).hexdigest()[:12]
