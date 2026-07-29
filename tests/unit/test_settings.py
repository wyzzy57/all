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


def test_edge_credential_master_key_requires_existing_exact_32_byte_docker_secret(tmp_path) -> None:
    secret_path = tmp_path / "edge-credential-master-key"
    secret_path.write_bytes(b"a" * 32)
    settings = Settings(_env_file=None, edge_credential_master_key_file=secret_path)

    assert settings.read_edge_credential_master_key() == b"a" * 32

    secret_path.write_bytes(b"short")
    with pytest.raises(ValueError, match="32 raw bytes") as exc_info:
        settings.read_edge_credential_master_key()

    assert "short" not in str(exc_info.value)

    missing_settings = Settings(_env_file=None, edge_credential_master_key_file=tmp_path / "missing")
    with pytest.raises(ValueError, match="unavailable"):
        missing_settings.read_edge_credential_master_key()


def test_auth_secrets_are_read_from_docker_secret_files(tmp_path) -> None:
    jwt_secret_path = tmp_path / "auth-jwt-secret"
    admin_password_path = tmp_path / "bootstrap-admin-password"
    jwt_secret_path.write_bytes(b"j" * 32)
    admin_password_path.write_text("  correct horse battery staple\n", encoding="utf-8")
    settings = Settings(
        _env_file=None,
        auth_jwt_secret_file=jwt_secret_path,
        bootstrap_admin_password_file=admin_password_path,
    )

    assert settings.read_auth_jwt_secret() == b"j" * 32
    assert settings.read_bootstrap_admin_password() == "correct horse battery staple"


def test_auth_secret_readers_reject_missing_files(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        auth_jwt_secret_file=tmp_path / "missing-jwt-secret",
        bootstrap_admin_password_file=tmp_path / "missing-admin-password",
    )

    with pytest.raises(ValueError, match="JWT secret is unavailable"):
        settings.read_auth_jwt_secret()
    with pytest.raises(ValueError, match="bootstrap admin password is unavailable"):
        settings.read_bootstrap_admin_password()


def test_auth_jwt_secret_requires_at_least_32_bytes_without_leaking_value(tmp_path) -> None:
    secret_path = tmp_path / "auth-jwt-secret"
    secret_path.write_bytes(b"too-short-secret")
    settings = Settings(_env_file=None, auth_jwt_secret_file=secret_path)

    with pytest.raises(ValueError, match="at least 32 bytes") as exc_info:
        settings.read_auth_jwt_secret()

    assert "too-short-secret" not in str(exc_info.value)


@pytest.mark.parametrize("password", ["", " \t\r\n"])
def test_bootstrap_admin_password_rejects_empty_values_without_leaking_value(
    tmp_path,
    password: str,
) -> None:
    password_path = tmp_path / "bootstrap-admin-password"
    password_path.write_text(password, encoding="utf-8")
    settings = Settings(_env_file=None, bootstrap_admin_password_file=password_path)

    with pytest.raises(ValueError, match="bootstrap admin password is invalid") as exc_info:
        settings.read_bootstrap_admin_password()

    assert str(exc_info.value) == "bootstrap admin password is invalid"


@pytest.mark.parametrize("max_output_bytes", [0, 4 * 1024 * 1024 + 1])
def test_edge_ssh_max_output_bytes_rejects_values_outside_hard_bounds(
    max_output_bytes: int,
) -> None:
    with pytest.raises(ValidationError, match="edge_ssh_max_output_bytes"):
        Settings(_env_file=None, edge_ssh_max_output_bytes=max_output_bytes)


@pytest.mark.parametrize("max_output_bytes", [1, 128, 4 * 1024 * 1024])
def test_edge_ssh_max_output_bytes_accepts_inclusive_hard_bounds(max_output_bytes: int) -> None:
    settings = Settings(_env_file=None, edge_ssh_max_output_bytes=max_output_bytes)

    assert settings.edge_ssh_max_output_bytes == max_output_bytes
