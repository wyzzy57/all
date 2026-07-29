from datetime import UTC, datetime, timedelta

from visiox_api.services.node_inventory import (
    ACTIVE_REFRESH_INTERVAL_SECONDS,
    IDLE_REFRESH_INTERVAL_SECONDS,
    inventory_refresh_interval,
    mark_inventory_failure,
)
from visiox_db.models import ComputeNode


def _node() -> ComputeNode:
    return ComputeNode(
        name="node-a",
        status="online",
        architecture="x86_64",
        platform_kind="x86_nvidia",
        capabilities={},
        resources={"gpu_count": 1, "gpu_utilization_percent": 50.0},
        fingerprint={},
        agent_version="ssh-bootstrap",
    )


def test_active_and_idle_nodes_use_different_refresh_intervals() -> None:
    node = _node()
    assert inventory_refresh_interval(node, active_workloads=1) == ACTIVE_REFRESH_INTERVAL_SECONDS
    assert inventory_refresh_interval(node, active_workloads=0) == IDLE_REFRESH_INTERVAL_SECONDS


def test_transient_failure_preserves_resources_and_eventually_marks_offline() -> None:
    node = _node()
    original_resources = dict(node.resources)
    now = datetime.now(UTC)
    node.inventory_refreshed_at = now - timedelta(seconds=61)

    mark_inventory_failure(node, "SSH probe failed", now=now, offline_after_seconds=60)

    assert node.resources == original_resources
    assert node.status == "offline"
    assert node.fingerprint["inventory_error"]["message"] == "SSH probe failed"
