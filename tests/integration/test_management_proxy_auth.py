from __future__ import annotations

from pathlib import Path

import pytest

from visiox_common.settings import Settings, get_settings


MANAGEMENT_PROXY_HEADER = "X-Visiox-Management-Proxy-Token"


def _production_settings(secret_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        VISIOX_ENV="production",
        agent_public_ws_url="wss://visiox-control.example.internal/agent/v1/connect",
        management_proxy_auth_token_file=secret_path,
    )


def test_production_worker_settings_do_not_require_management_proxy_secret(tmp_path: Path) -> None:
    settings = _production_settings(tmp_path / "missing-proxy-token")

    assert settings.environment == "production"
    with pytest.raises(ValueError, match="management proxy authentication token"):
        settings.read_management_proxy_auth_token()


def test_production_settings_reject_management_proxy_secret_with_whitespace(tmp_path: Path) -> None:
    secret_path = tmp_path / "management-proxy-token"
    secret_path.write_text(f"{'a' * 64} \n", encoding="ascii")

    settings = _production_settings(secret_path)

    with pytest.raises(ValueError, match="management proxy authentication token is invalid"):
        settings.read_management_proxy_auth_token()


def test_management_routes_require_the_proxy_secret_but_public_enrollment_remains_public(
    agent_api_client,
    tmp_path: Path,
) -> None:
    secret = "a" * 64
    secret_path = tmp_path / "management-proxy-token"
    secret_path.write_text(f"{secret}\n", encoding="ascii")
    settings = _production_settings(secret_path)
    agent_api_client.app.dependency_overrides[get_settings] = lambda: settings
    headers = {MANAGEMENT_PROXY_HEADER: secret}

    for method_name, path in (
        ("get", "/nodes"),
        ("get", "/resource-pools"),
        ("post", "/agent/v1/enrollment-tokens"),
    ):
        method = getattr(agent_api_client, method_name)
        kwargs = {"json": {"name": "edge-auth"}} if method_name == "post" else {}
        missing = method(path, **kwargs)
        wrong = method(path, headers={MANAGEMENT_PROXY_HEADER: "b" * 64}, **kwargs)
        assert missing.status_code in {401, 403}
        assert wrong.status_code in {401, 403}

    token_response = agent_api_client.post(
        "/agent/v1/enrollment-tokens",
        headers=headers,
        json={"name": "edge-auth"},
    )
    assert token_response.status_code == 201

    enrollment = agent_api_client.post(
        "/agent/v1/enroll",
        json={
            "protocol_version": 1,
            "token": token_response.json()["token"],
            "enrollment_request_id": "proxy-auth-public-enrollment-001",
            "node_name": "edge-auth",
            "architecture": "arm64",
            "platform_kind": "jetson",
            "agent_version": "0.1.0",
            "csr_pem": "not-a-valid-csr",
        },
    )
    assert enrollment.status_code == 422

    node_id = "missing-node"
    assert agent_api_client.get(f"/nodes/{node_id}").status_code in {401, 403}
    assert agent_api_client.post(f"/nodes/{node_id}/drain").status_code in {401, 403}
    assert agent_api_client.get(f"/nodes/{node_id}", headers=headers).status_code == 404
    assert agent_api_client.post(f"/nodes/{node_id}/drain", headers=headers).status_code == 404


def test_local_environment_explicitly_bypasses_management_proxy_secret(agent_api_client) -> None:
    settings = Settings(_env_file=None, VISIOX_ENV="local")
    agent_api_client.app.dependency_overrides[get_settings] = lambda: settings

    assert agent_api_client.get("/nodes").status_code == 200
