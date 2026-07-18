import pytest
from fastapi.testclient import TestClient

from visiox_api.main import create_app
from visiox_common.settings import Settings


def test_local_lifespan_generates_agent_ca_when_enabled(tmp_path, monkeypatch):
    settings = Settings(
        VISIOX_ENV="local",
        agent_gateway_enabled=True,
        agent_auto_generate_ca=True,
        agent_ca_cert_path=tmp_path / "ca.crt",
        agent_ca_key_path=tmp_path / "ca.key",
        seed_base_models_on_startup=False,
    )
    monkeypatch.setattr("visiox_api.main.get_settings", lambda: settings)

    with TestClient(create_app()):
        assert settings.agent_ca_cert_path.exists()
        assert settings.agent_ca_key_path.exists()


def test_production_lifespan_refuses_to_generate_missing_ca(tmp_path, monkeypatch):
    management_proxy_token = tmp_path / "management-proxy-token"
    management_proxy_token.write_text("a" * 64, encoding="ascii")
    settings = Settings(
        VISIOX_ENV="production",
        agent_gateway_enabled=True,
        agent_auto_generate_ca=True,
        agent_public_ws_url="wss://platform.example/agent/v1/connect",
        agent_ca_cert_path=tmp_path / "missing-ca.crt",
        agent_ca_key_path=tmp_path / "missing-ca.key",
        management_proxy_auth_token_file=management_proxy_token,
        seed_base_models_on_startup=False,
    )
    monkeypatch.setattr("visiox_api.main.get_settings", lambda: settings)

    with pytest.raises(RuntimeError, match="Agent CA material is required"):
        with TestClient(create_app()):
            pass


def test_production_api_lifespan_requires_management_proxy_secret(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None,
        VISIOX_ENV="production",
        agent_gateway_enabled=False,
        agent_public_ws_url="wss://platform.example/agent/v1/connect",
        management_proxy_auth_token_file=tmp_path / "missing-management-proxy-token",
        seed_base_models_on_startup=False,
    )
    monkeypatch.setattr("visiox_api.main.get_settings", lambda: settings)

    with pytest.raises(ValueError, match="management proxy authentication token"):
        with TestClient(create_app()):
            pass
