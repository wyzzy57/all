from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from visiox_db.base import Base
from visiox_db.models import AgentEnrollmentToken, ComputeNode, NodeEvent, ResourcePool


def test_edge_compute_models_persist_inventory_and_event_sequence():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        pool = ResourcePool(name="jetson-orin", kind="jetson", selector={}, compatibility_policy={})
        session.add(pool)
        session.flush()
        node = ComputeNode(
            name="edge-01",
            resource_pool_id=pool.id,
            status="online",
            architecture="arm64",
            platform_kind="jetson",
            capabilities={"tasks": ["detect"]},
            resources={"gpu_memory_bytes": 8589934592},
            fingerprint={"jetpack": "6.2"},
            agent_version="0.1.0",
        )
        session.add(node)
        session.flush()
        session.add(NodeEvent(node_id=node.id, sequence=1, event_type="inventory", payload={"ok": True}))
        session.commit()

        assert session.scalar(select(ComputeNode).where(ComputeNode.name == "edge-01")) is not None


def test_enrollment_token_hash_is_unique():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    expires_at = datetime.now(UTC) + timedelta(minutes=10)
    with Session(engine) as session:
        session.add_all([
            AgentEnrollmentToken(name="first", token_hash="same", expires_at=expires_at),
            AgentEnrollmentToken(name="second", token_hash="same", expires_at=expires_at),
        ])
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
        else:
            raise AssertionError("duplicate token hash must fail")
