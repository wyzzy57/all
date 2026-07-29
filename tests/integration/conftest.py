from collections.abc import Generator
from types import SimpleNamespace
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

import visiox_api.main as api_main
from visiox_api.dependencies.database import get_db_session
from visiox_api.dependencies.auth import get_current_user
from visiox_api.services.agent_identity import ensure_agent_ca
from visiox_api.services.bootstrap_admin import bootstrap_default_admin
from visiox_api.services.security import hash_password
from visiox_common.settings import Settings, get_settings
from visiox_db.models.edge_compute import ResourcePool
from visiox_db.models.identity import (
    ROLE_MEMBER,
    STATUS_ACTIVE,
    STATUS_DISABLED,
    Organization,
    User,
    UserSession,
)
from visiox_storage.client import InMemoryObjectStorageClient
from tests.integration.ownership_test_support import install_legacy_ownership


@pytest.fixture()
def authenticated_client(tmp_path) -> Generator[
    tuple[
        TestClient,
        sessionmaker[Session],
        Settings,
        dict[str, Any],
    ]
]:
    database_url = f"sqlite:///{tmp_path / 'authenticated-client.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )

    jwt_secret_file = tmp_path / "auth-jwt-secret"
    jwt_secret_file.write_bytes(b"task-11-shared-jwt-secret-value-0001")
    bootstrap_password_file = tmp_path / "bootstrap-admin-password"
    bootstrap_password_file.write_text("correct-password", encoding="utf-8")
    settings = Settings(
        _env_file=None,
        auth_jwt_secret_file=jwt_secret_file,
        auth_access_token_minutes=15,
        auth_refresh_token_days=7,
        auth_cookie_secure=False,
        bootstrap_admin_username="active-user",
        bootstrap_admin_email="admin@example.test",
        bootstrap_admin_password_file=bootstrap_password_file,
        seed_base_models_on_startup=False,
    )

    with session_factory() as session:
        bootstrap_default_admin(session, settings)
        organization = session.scalar(
            select(Organization).where(Organization.slug == "default")
        )
        admin = session.scalar(
            select(User).where(User.username == settings.bootstrap_admin_username)
        )
        assert organization is not None
        assert admin is not None
        admin.display_name = "Active User"

        other_organization = Organization(
            name="Other",
            slug="other",
            status=STATUS_ACTIVE,
        )
        session.add(other_organization)
        session.flush()
        member = User(
            organization_id=organization.id,
            username="member",
            display_name="Member",
            email="member@example.test",
            password_hash=hash_password("member-password"),
            role=ROLE_MEMBER,
            status=STATUS_ACTIVE,
            must_change_password=False,
        )
        disabled_user = User(
            organization_id=other_organization.id,
            username="disabled-user",
            display_name="Disabled User",
            email="disabled@example.test",
            password_hash=hash_password("correct-password"),
            role=ROLE_MEMBER,
            status=STATUS_DISABLED,
            must_change_password=False,
        )
        outsider = User(
            organization_id=other_organization.id,
            username="outsider",
            display_name="Outsider",
            email="outsider@example.test",
            password_hash=hash_password("outsider-password"),
            role=ROLE_MEMBER,
            status=STATUS_ACTIVE,
            must_change_password=False,
        )
        pool = ResourcePool(
            name="pool-a",
            organization_id=organization.id,
            owner_user_id=admin.id,
            kind="cuda",
            selector={},
            compatibility_policy={},
            enabled=True,
        )
        session.add_all([member, disabled_user, outsider, pool])
        session.commit()
        identity: dict[str, Any] = {
            "organization_id": organization.id,
            "other_organization_id": other_organization.id,
            "admin_id": admin.id,
            "active_user_id": admin.id,
            "member_id": member.id,
            "disabled_user_id": disabled_user.id,
            "outsider_id": outsider.id,
            "pool_id": pool.id,
        }

    def override_session() -> Generator[Session]:
        with session_factory() as session:
            yield session

    def override_settings() -> Settings:
        return settings

    app = api_main.create_app()
    app.state.object_storage = InMemoryObjectStorageClient()
    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_settings] = override_settings

    client = TestClient(app, raise_server_exceptions=False)
    try:
        admin_login = client.post(
            "/auth/login",
            json={"username": "active-user", "password": "correct-password"},
        )
        assert admin_login.status_code == 200, admin_login.text
        member_login = client.post(
            "/auth/login",
            json={"username": "member", "password": "member-password"},
        )
        assert member_login.status_code == 200, member_login.text
        identity["admin_headers"] = {
            "Authorization": f"Bearer {admin_login.json()['access_token']}"
        }
        identity["member_headers"] = {
            "Authorization": f"Bearer {member_login.json()['access_token']}"
        }
        client.cookies.clear()
        with session_factory() as session:
            session.execute(delete(UserSession))
            session.commit()
        yield client, session_factory, settings, identity
    finally:
        client.close()
        app.dependency_overrides.clear()
        engine.dispose()


@pytest.fixture()
def agent_session_factory(tmp_path) -> sessionmaker[Session]:
    database_url = f"sqlite:///{tmp_path / 'visiox-agent.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    install_legacy_ownership(factory)
    with factory() as session:
        organization = Organization(
            id="legacy-org", name="Legacy", slug="default", status=STATUS_ACTIVE
        )
        session.add(organization)
        session.add(
            User(
                id="legacy-admin",
                organization_id=organization.id,
                username="admin",
                display_name="Legacy Admin",
                email="legacy-admin@example.test",
                password_hash="test",
                role="admin",
                status=STATUS_ACTIVE,
                must_change_password=False,
            )
        )
        session.commit()
    return factory


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
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id="legacy-admin", organization_id="legacy-org", role="admin"
    )

    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
