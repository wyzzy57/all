from datetime import UTC, datetime

from visiox_db.models import ComputeNode


ACTIVE_REFRESH_INTERVAL_SECONDS = 15
IDLE_REFRESH_INTERVAL_SECONDS = 60


def inventory_refresh_interval(
    node: ComputeNode,
    *,
    active_workloads: int,
) -> int:
    if node.enabled is not False and active_workloads > 0:
        return ACTIVE_REFRESH_INTERVAL_SECONDS
    return IDLE_REFRESH_INTERVAL_SECONDS


def mark_inventory_failure(
    node: ComputeNode,
    message: str,
    *,
    now: datetime | None = None,
    offline_after_seconds: int,
) -> None:
    current_time = now or datetime.now(UTC)
    refreshed_at = node.inventory_refreshed_at
    if refreshed_at is not None and refreshed_at.tzinfo is None:
        refreshed_at = refreshed_at.replace(tzinfo=UTC)
    node.fingerprint = {
        **node.fingerprint,
        "inventory_error": {
            "message": message,
            "occurred_at": current_time.isoformat(),
        },
    }
    if (
        refreshed_at is None
        or (current_time - refreshed_at).total_seconds() >= offline_after_seconds
    ) and node.status not in {"disabled", "draining"}:
        node.status = "offline"
