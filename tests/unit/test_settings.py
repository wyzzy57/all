import pytest
from pydantic import ValidationError

from visiox_common.settings import Settings


@pytest.mark.parametrize("heartbeat_seconds", [4, 301])
def test_agent_heartbeat_interval_matches_agent_protocol_bounds(heartbeat_seconds: int) -> None:
    with pytest.raises(ValidationError, match="agent_heartbeat_interval_seconds"):
        Settings(
            _env_file=None,
            environment="local",
            agent_heartbeat_interval_seconds=heartbeat_seconds,
        )


@pytest.mark.parametrize(
    ("heartbeat_seconds", "offline_after_seconds"),
    [(5, 14), (15, 44), (300, 899)],
)
def test_agent_offline_threshold_requires_three_heartbeat_intervals(
    heartbeat_seconds: int,
    offline_after_seconds: int,
) -> None:
    with pytest.raises(ValidationError, match="at least three heartbeat intervals"):
        Settings(
            _env_file=None,
            environment="local",
            agent_heartbeat_interval_seconds=heartbeat_seconds,
            agent_offline_after_seconds=offline_after_seconds,
        )


@pytest.mark.parametrize(
    ("heartbeat_seconds", "offline_after_seconds"),
    [(5, 15), (15, 45), (300, 900)],
)
def test_agent_heartbeat_and_offline_threshold_accept_safe_boundaries(
    heartbeat_seconds: int,
    offline_after_seconds: int,
) -> None:
    settings = Settings(
        _env_file=None,
        environment="local",
        agent_heartbeat_interval_seconds=heartbeat_seconds,
        agent_offline_after_seconds=offline_after_seconds,
    )

    assert settings.agent_heartbeat_interval_seconds == heartbeat_seconds
    assert settings.agent_offline_after_seconds == offline_after_seconds
