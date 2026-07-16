from collections.abc import Generator

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import visiox_api.main as api_main
from visiox_api.services.agent_identity import ensure_agent_ca
from visiox_common.settings import Settings, get_settings


@pytest.fixture()
def agent_session_factory(tmp_path) -> sessionmaker[Session]:
    database_url = f"sqlite:///{tmp_path / 'visiox-agent.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture()
def agent_api_client(
    tmp_path,
    agent_session_factory: sessionmaker[Session],
    monkeypatch,
) -> Generator[TestClient]:
    settings = Settings(
        _env_file=None,
        agent_ca_cert_path=tmp_path / "ca.crt",
        agent_ca_key_path=tmp_path / "ca.key",
        agent_auto_generate_ca=True,
        agent_gateway_enabled=True,
        minio_endpoint="127.0.0.1:1",
        redis_url="redis://127.0.0.1:1/0",
        seed_base_models_on_startup=False,
    )
    ensure_agent_ca(settings)

    def override_session() -> Generator[Session]:
        with agent_session_factory() as session:
            yield session

    def override_settings() -> Settings:
        return settings

    monkeypatch.setattr(api_main, "get_settings", override_settings)
    app = api_main.create_app()

    from visiox_api.routes.agent_enrollment import get_agent_enrollment_session
    from visiox_api.routes.nodes import get_node_session
    from visiox_api.ws.agents import get_agent_gateway_session_factory

    def override_gateway_session_factory() -> sessionmaker[Session]:
        return agent_session_factory

    app.dependency_overrides[get_agent_enrollment_session] = override_session
    app.dependency_overrides[get_agent_gateway_session_factory] = override_gateway_session_factory
    app.dependency_overrides[get_node_session] = override_session
    app.dependency_overrides[get_settings] = override_settings

    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
