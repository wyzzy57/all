from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from visiox_api.services.resource_scheduler import (
    ResourceSchedulingError,
    WorkloadRequirements,
    assert_allocation_available,
    list_schedulable_nodes,
)
from visiox_db.base import Base
from visiox_db.models import (
    ComputeNode,
    Organization,
    ResourceAllocationPolicy,
    ResourceGrant,
    ResourcePool,
    User,
)


def _session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


def _seed(session: Session, *, role: str = "member") -> tuple[User, ResourcePool, ComputeNode]:
    org = Organization(name="Default", slug="default")
    session.add(org)
    session.flush()
    user = User(
        organization_id=org.id,
        username="member",
        display_name="Member",
        email="member@example.test",
        password_hash="hash",
        role=role,
        status="active",
        must_change_password=False,
    )
    pool = ResourcePool(
        name="gpu-pool",
        kind="x86_nvidia",
        selector={},
        compatibility_policy={"platform_kind": "x86_nvidia", "architecture": "x86_64"},
        enabled=True,
    )
    session.add_all([user, pool])
    session.flush()
    node = ComputeNode(
        name="gpu-node",
        resource_pool_id=pool.id,
        status="online",
        enabled=True,
        architecture="x86_64",
        platform_kind="x86_nvidia",
        capabilities={},
        resources={"gpu_count": 1, "gpu_memory_total_mib": 24576},
        fingerprint={},
        agent_version="ssh-bootstrap",
    )
    session.add(node)
    session.commit()
    return user, pool, node


def test_member_needs_unexpired_use_grant_and_allocation() -> None:
    session = _session()
    user, pool, _ = _seed(session)
    requirements = WorkloadRequirements(gpu_count=1, architecture="x86_64")

    try:
        assert_allocation_available(session, user, pool.id, requirements)
    except ResourceSchedulingError as error:
        assert error.code == "NO_COMPATIBLE_NODE"
    else:
        raise AssertionError("missing use grant must be rejected")

    session.add_all(
        [
            ResourceGrant(
                organization_id=user.organization_id,
                resource_type="resource_pool",
                resource_id=pool.id,
                principal_type="user",
                principal_id=user.id,
                permissions=["use"],
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            ),
            ResourceAllocationPolicy(
                organization_id=user.organization_id,
                principal_type="user",
                principal_id=user.id,
                resource_pool_id=pool.id,
                max_concurrent_training_jobs=1,
                max_gpu_count=1,
                max_service_instances=1,
            ),
        ]
    )
    session.commit()

    assert list_schedulable_nodes(session, user, requirements) == [session.query(ComputeNode).one()]


def test_disabled_offline_or_insufficient_vram_nodes_are_excluded() -> None:
    session = _session()
    admin, _, node = _seed(session, role="admin")
    requirements = WorkloadRequirements(gpu_count=1, min_gpu_memory_mib=32768)

    assert list_schedulable_nodes(session, admin, requirements) == []
    node.resources = {"gpu_count": 1, "gpu_memory_total_mib": 49152}
    node.status = "offline"
    session.commit()
    assert list_schedulable_nodes(session, admin, requirements) == []
