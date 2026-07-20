import pytest

from visiox_common.settings import Settings
from visiox_edge_executor_worker.startup import EdgeExecutorSecurityContext, initialize_security


@pytest.mark.parametrize("secret_bytes", [None, b"short"])
def test_initialize_security_fails_before_accepting_ssh_work_for_invalid_secret(
    tmp_path,
    monkeypatch,
    secret_bytes: bytes | None,
) -> None:
    secret_path = tmp_path / "edge-master-key"
    if secret_bytes is not None:
        secret_path.write_bytes(secret_bytes)
    settings = Settings(_env_file=None, edge_credential_master_key_file=secret_path)
    ssh_client_constructed = False

    def fail_if_ssh_client_is_constructed(**kwargs):
        nonlocal ssh_client_constructed
        ssh_client_constructed = True
        raise AssertionError("SSH work became available before secret validation")

    monkeypatch.setattr(
        "visiox_edge_executor_worker.startup.StrictSshClient",
        fail_if_ssh_client_is_constructed,
    )

    with pytest.raises(ValueError, match="unavailable|32 raw bytes"):
        initialize_security(settings)

    assert not ssh_client_constructed


def test_initialize_security_returns_the_unique_worker_security_context(tmp_path) -> None:
    secret_path = tmp_path / "edge-master-key"
    secret_path.write_bytes(b"a" * 32)
    settings = Settings(_env_file=None, edge_credential_master_key_file=secret_path)

    context = initialize_security(settings)

    assert isinstance(context, EdgeExecutorSecurityContext)
    assert context.credential_cipher.key_version == 1
