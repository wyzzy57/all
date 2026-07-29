import asyncio
from datetime import UTC, datetime, timedelta

from alembic import command
from alembic.config import Config
from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

import visiox_api.main as api_main
from visiox_api.services.bootstrap_admin import bootstrap_default_admin
from visiox_api.services.security import (
    issue_access_token,
    verify_password,
)
from visiox_common.settings import Settings
from visiox_db.models.identity import (
    ROLE_ADMIN,
    ROLE_MEMBER,
    STATUS_ACTIVE,
    STATUS_DELETED,
    STATUS_DISABLED,
    Organization,
    User,
    UserSession,
)


def test_bootstrap_default_admin_is_transactional_and_idempotent(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'auth.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    password = "temporary-bootstrap-password"
    password_file = tmp_path / "bootstrap-admin-password"
    password_file.write_text(password, encoding="utf-8")
    settings = Settings(
        _env_file=None,
        bootstrap_admin_username="configured-admin",
        bootstrap_admin_email="configured-admin@example.test",
        bootstrap_admin_password_file=password_file,
    )
    session_factory = sessionmaker(
        bind=create_engine(database_url),
        autoflush=False,
        expire_on_commit=False,
    )

    with session_factory() as session:
        first_result = bootstrap_default_admin(session, settings)
        second_result = bootstrap_default_admin(session, settings)

        assert first_result.organization_created is True
        assert first_result.admin_created is True
        assert second_result.organization_created is False
        assert second_result.admin_created is False
        assert session.scalar(select(func.count()).select_from(Organization)) == 1
        assert session.scalar(select(func.count()).select_from(User)) == 1

        organization = session.scalar(select(Organization))
        admin = session.scalar(select(User))

    assert organization is not None
    assert organization.slug == "default"
    assert organization.status == STATUS_ACTIVE
    assert admin is not None
    assert admin.organization_id == organization.id
    assert admin.username == "configured-admin"
    assert admin.email == "configured-admin@example.test"
    assert admin.display_name == "configured-admin"
    assert admin.role == ROLE_ADMIN
    assert admin.status == STATUS_ACTIVE
    assert admin.must_change_password is True
    assert verify_password(admin.password_hash, password) is True


@pytest.mark.parametrize(
    ("existing_username", "existing_email"),
    [
        ("configured-admin", "different@example.test"),
        ("different-admin", "configured-admin@example.test"),
    ],
    ids=["username-only", "email-only"],
)
def test_bootstrap_rejects_partial_identity_collisions_without_mutation(
    tmp_path,
    existing_username: str,
    existing_email: str,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'collision.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    session_factory = sessionmaker(
        bind=create_engine(database_url),
        autoflush=False,
        expire_on_commit=False,
    )
    settings = Settings(
        _env_file=None,
        bootstrap_admin_username="configured-admin",
        bootstrap_admin_email="configured-admin@example.test",
        bootstrap_admin_password_file=tmp_path / "missing-password",
    )

    with session_factory() as session:
        organization = Organization(
            name="Default",
            slug="default",
            status=STATUS_ACTIVE,
        )
        session.add(organization)
        session.flush()
        existing_user = User(
            organization_id=organization.id,
            username=existing_username,
            display_name="Preserved Name",
            email=existing_email,
            password_hash="preserved-password-hash",
            role=ROLE_MEMBER,
            status=STATUS_DISABLED,
            must_change_password=False,
        )
        session.add(existing_user)
        session.commit()
        existing_user_id = existing_user.id

        with pytest.raises(
            ValueError,
            match="bootstrap admin username or email conflicts with an existing user",
        ):
            bootstrap_default_admin(session, settings)

        persisted_user = session.get(User, existing_user_id)
        assert persisted_user is not None
        assert persisted_user.username == existing_username
        assert persisted_user.email == existing_email
        assert persisted_user.display_name == "Preserved Name"
        assert persisted_user.password_hash == "preserved-password-hash"
        assert persisted_user.role == ROLE_MEMBER
        assert persisted_user.status == STATUS_DISABLED
        assert persisted_user.must_change_password is False
        assert session.scalar(select(func.count()).select_from(User)) == 1


def test_bootstrap_retries_one_commit_time_integrity_error(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'retry.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    password_file = tmp_path / "bootstrap-admin-password"
    password_file.write_text("retry-bootstrap-password", encoding="utf-8")
    settings = Settings(
        _env_file=None,
        bootstrap_admin_password_file=password_file,
    )

    class OneTimeIntegrityErrorSession(Session):
        failed_once = False

        def commit(self) -> None:
            if not self.failed_once:
                self.failed_once = True
                raise IntegrityError(
                    "simulated bootstrap race",
                    {},
                    RuntimeError("simulated uniqueness conflict"),
                )
            super().commit()

    session_factory = sessionmaker(
        bind=create_engine(database_url),
        class_=OneTimeIntegrityErrorSession,
        autoflush=False,
        expire_on_commit=False,
    )

    with session_factory() as session:
        result = bootstrap_default_admin(session, settings)

        assert result.organization_created is True
        assert result.admin_created is True
        assert session.scalar(select(func.count()).select_from(Organization)) == 1
        assert session.scalar(select(func.count()).select_from(User)) == 1


def test_lifespan_bootstraps_admin_before_base_model_seed(
    tmp_path, monkeypatch
) -> None:
    database_url = f"sqlite:///{tmp_path / 'lifespan-auth.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    session_factory = sessionmaker(
        bind=create_engine(database_url),
        autoflush=False,
        expire_on_commit=False,
    )
    password_file = tmp_path / "bootstrap-admin-password"
    password_file.write_text("lifespan-bootstrap-password", encoding="utf-8")
    settings = Settings(
        _env_file=None,
        bootstrap_admin_password_file=password_file,
        seed_base_models_on_startup=True,
    )
    seed_calls: list[str] = []

    class FakeRedisClient:
        async def aclose(self) -> None:
            pass

    def fake_seed(session: Session, storage: object) -> None:
        assert storage is fake_storage
        assert session.scalar(select(func.count()).select_from(Organization)) == 1
        assert session.scalar(select(func.count()).select_from(User)) == 1
        seed_calls.append("seeded")

    fake_storage = object()
    monkeypatch.setattr(api_main, "get_settings", lambda: settings)
    monkeypatch.setattr(api_main, "create_session_factory", lambda: session_factory)
    monkeypatch.setattr(
        api_main, "MinioObjectStorageClient", lambda **kwargs: fake_storage
    )
    monkeypatch.setattr(api_main, "seed_yolo26_base_models", fake_seed)

    from redis import asyncio as redis_asyncio

    monkeypatch.setattr(
        redis_asyncio, "from_url", lambda *args, **kwargs: FakeRedisClient()
    )

    async def run_lifespan() -> None:
        app = api_main.create_app()
        async with api_main.lifespan(app):
            pass

    asyncio.run(run_lifespan())

    assert seed_calls == ["seeded"]


def test_local_lifespan_skips_bootstrap_without_password_secret(
    tmp_path, monkeypatch
) -> None:
    settings = Settings(
        _env_file=None,
        bootstrap_admin_password_file=tmp_path / "missing-password",
        redis_url="redis://127.0.0.1:1/0",
        minio_endpoint="127.0.0.1:1",
        seed_base_models_on_startup=False,
    )
    monkeypatch.setattr(api_main, "get_settings", lambda: settings)

    def fail_if_database_is_opened():
        raise AssertionError("local bootstrap should be skipped")

    monkeypatch.setattr(api_main, "create_session_factory", fail_if_database_is_opened)

    async def run_lifespan() -> None:
        app = api_main.create_app()
        async with api_main.lifespan(app):
            pass

    asyncio.run(run_lifespan())


def test_non_local_lifespan_requires_bootstrap_password_for_first_admin(
    tmp_path, monkeypatch
) -> None:
    database_url = f"sqlite:///{tmp_path / 'startup-auth.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    session_factory = sessionmaker(
        bind=create_engine(database_url),
        autoflush=False,
        expire_on_commit=False,
    )
    management_token_file = tmp_path / "management-token"
    management_token_file.write_text("a" * 64, encoding="ascii")
    monkeypatch.setenv("VISIOX_ENV", "production")
    settings = Settings(
        _env_file=None,
        management_proxy_auth_token_file=management_token_file,
        bootstrap_admin_password_file=tmp_path / "missing-password",
        seed_base_models_on_startup=False,
    )
    monkeypatch.setattr(api_main, "get_settings", lambda: settings)
    monkeypatch.setattr(api_main, "create_session_factory", lambda: session_factory)

    async def run_lifespan() -> None:
        app = api_main.create_app()
        async with api_main.lifespan(app):
            pass

    with pytest.raises(ValueError, match="bootstrap admin password is unavailable"):
        asyncio.run(run_lifespan())


def _login(client: TestClient):
    return client.post(
        "/auth/login",
        json={"username": "active-user", "password": "correct-password"},
    )


def test_login_returns_access_response_and_sets_opaque_refresh_cookie(
    authenticated_client,
) -> None:
    client, session_factory, settings, identity = authenticated_client

    response = _login(client)

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 15 * 60
    assert body["access_token"]
    assert body["user"] == {
        "id": identity["active_user_id"],
        "username": "active-user",
        "display_name": "Active User",
        "email": "admin@example.test",
        "role": ROLE_ADMIN,
        "status": STATUS_ACTIVE,
        "must_change_password": True,
    }
    refresh_token = client.cookies.get(settings.auth_refresh_cookie_name)
    assert refresh_token
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "samesite=lax" in response.headers["set-cookie"].lower()
    assert "path=/auth" in response.headers["set-cookie"].lower()
    assert refresh_token not in response.text

    with session_factory() as session:
        persisted_user = session.get(User, identity["active_user_id"])
        persisted_session = session.scalar(select(UserSession))
        assert persisted_user is not None
        assert persisted_user.last_login_at is not None
        assert persisted_session is not None
        assert persisted_session.refresh_token_hash != refresh_token


@pytest.mark.parametrize(
    ("username", "password"),
    [
        ("active-user", "wrong-password"),
        ("missing-user", "correct-password"),
    ],
)
def test_login_rejects_wrong_credentials_generically(
    authenticated_client, username: str, password: str
) -> None:
    client, _, _, _ = authenticated_client

    response = client.post(
        "/auth/login", json={"username": username, "password": password}
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid credentials"}


def test_login_rejects_disabled_user(authenticated_client) -> None:
    client, _, _, _ = authenticated_client

    response = client.post(
        "/auth/login",
        json={"username": "disabled-user", "password": "correct-password"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid credentials"}


@pytest.mark.parametrize(
    ("username", "password"),
    [
        ("active-user", "wrong-password"),
        ("missing-user", "wrong-password"),
        ("disabled-user", "wrong-password"),
    ],
)
def test_login_always_performs_exactly_one_password_verification(
    authenticated_client, monkeypatch, username: str, password: str
) -> None:
    client, _, _, _ = authenticated_client
    import visiox_api.routes.auth as auth_routes

    original_verify_password = auth_routes.verify_password
    verified_hashes: list[str] = []

    def recording_verify(password_hash: str, candidate: str) -> bool:
        verified_hashes.append(password_hash)
        return original_verify_password(password_hash, candidate)

    monkeypatch.setattr(auth_routes, "verify_password", recording_verify)

    response = client.post(
        "/auth/login", json={"username": username, "password": password}
    )

    assert response.status_code == 401
    assert len(verified_hashes) == 1


def test_me_returns_current_persisted_user(authenticated_client) -> None:
    client, _, _, identity = authenticated_client
    access_token = _login(client).json()["access_token"]

    response = client.get(
        "/auth/me", headers={"Authorization": f"Bearer {access_token}"}
    )

    assert response.status_code == 200
    assert response.json()["id"] == identity["active_user_id"]
    assert response.json()["role"] == ROLE_ADMIN
    assert "password_hash" not in response.text


@pytest.mark.parametrize(
    "authorization",
    [None, "Bearer not-a-jwt", "Basic credentials"],
)
def test_me_rejects_missing_or_invalid_access_token(
    authenticated_client, authorization
) -> None:
    client, _, _, _ = authenticated_client
    headers = {} if authorization is None else {"Authorization": authorization}

    response = client.get("/auth/me", headers=headers)

    assert response.status_code == 401


def test_me_rejects_expired_access_token(authenticated_client) -> None:
    client, _, settings, identity = authenticated_client
    expired_token = issue_access_token(
        settings.read_auth_jwt_secret(),
        identity["active_user_id"],
        identity["organization_id"],
        ROLE_ADMIN,
        timedelta(seconds=-1),
    )

    response = client.get(
        "/auth/me", headers={"Authorization": f"Bearer {expired_token}"}
    )

    assert response.status_code == 401


@pytest.mark.parametrize("new_status", [STATUS_DISABLED, STATUS_DELETED])
def test_me_rejects_user_disabled_or_deleted_after_token_issuance(
    authenticated_client, new_status: str
) -> None:
    client, session_factory, _, identity = authenticated_client
    access_token = _login(client).json()["access_token"]
    with session_factory() as session:
        user = session.get(User, identity["active_user_id"])
        assert user is not None
        user.status = new_status
        session.commit()

    response = client.get(
        "/auth/me", headers={"Authorization": f"Bearer {access_token}"}
    )

    assert response.status_code == 401


def test_me_rejects_token_with_mismatched_organization_claim(
    authenticated_client,
) -> None:
    client, _, settings, identity = authenticated_client
    mismatched_token = issue_access_token(
        settings.read_auth_jwt_secret(),
        identity["active_user_id"],
        "different-organization-id",
        ROLE_ADMIN,
        timedelta(minutes=15),
    )

    response = client.get(
        "/auth/me", headers={"Authorization": f"Bearer {mismatched_token}"}
    )

    assert response.status_code == 401


def test_require_admin_rejects_member(authenticated_client) -> None:
    _, session_factory, _, identity = authenticated_client
    from visiox_api.dependencies.auth import require_admin

    with session_factory() as session:
        member = session.get(User, identity["disabled_user_id"])
        assert member is not None
        member.status = STATUS_ACTIVE

        with pytest.raises(HTTPException) as error:
            require_admin(member)

    assert error.value.status_code == 403


def test_refresh_rotates_session_and_rejects_reuse(authenticated_client) -> None:
    client, session_factory, settings, _ = authenticated_client
    login_response = _login(client)
    old_refresh_token = client.cookies.get(settings.auth_refresh_cookie_name)
    assert login_response.status_code == 200
    assert old_refresh_token

    refresh_response = client.post("/auth/refresh")

    assert refresh_response.status_code == 200
    assert refresh_response.json()["access_token"]
    new_refresh_token = client.cookies.get(settings.auth_refresh_cookie_name)
    assert new_refresh_token
    assert new_refresh_token != old_refresh_token
    with session_factory() as session:
        sessions = list(
            session.scalars(select(UserSession).order_by(UserSession.created_at))
        )
        assert len(sessions) == 2
        assert sessions[0].revoked_at is not None
        assert sessions[1].revoked_at is None

    client.cookies.clear()
    client.cookies.set(
        settings.auth_refresh_cookie_name,
        old_refresh_token,
        domain="testserver.local",
        path="/auth",
    )
    replay_response = client.post("/auth/refresh")

    assert replay_response.status_code == 401
    assert replay_response.json() == {"detail": "Invalid refresh token"}
    assert settings.auth_refresh_cookie_name in replay_response.headers["set-cookie"]
    assert "max-age=0" in replay_response.headers["set-cookie"].lower()
    assert client.cookies.get(settings.auth_refresh_cookie_name) is None


def test_invalid_refresh_token_is_rejected_and_cookie_is_deleted(
    authenticated_client,
) -> None:
    client, _, settings, _ = authenticated_client
    client.cookies.set(
        settings.auth_refresh_cookie_name,
        "invalid-refresh-token",
        domain="testserver.local",
        path="/auth",
    )

    response = client.post("/auth/refresh")

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid refresh token"}
    assert "max-age=0" in response.headers["set-cookie"].lower()
    assert client.cookies.get(settings.auth_refresh_cookie_name) is None


def test_expired_refresh_token_is_rejected_and_cookie_is_deleted(
    authenticated_client,
) -> None:
    client, session_factory, settings, _ = authenticated_client
    assert _login(client).status_code == 200
    with session_factory() as session:
        persisted_session = session.scalar(select(UserSession))
        assert persisted_session is not None
        persisted_session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()

    response = client.post("/auth/refresh")

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid refresh token"}
    assert "max-age=0" in response.headers["set-cookie"].lower()
    assert client.cookies.get(settings.auth_refresh_cookie_name) is None


def test_inactive_user_refresh_is_rejected_and_cookie_is_deleted(
    authenticated_client,
) -> None:
    client, session_factory, settings, identity = authenticated_client
    assert _login(client).status_code == 200
    with session_factory() as session:
        user = session.get(User, identity["active_user_id"])
        assert user is not None
        user.status = STATUS_DISABLED
        session.commit()

    response = client.post("/auth/refresh")

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid refresh token"}
    assert "max-age=0" in response.headers["set-cookie"].lower()
    assert client.cookies.get(settings.auth_refresh_cookie_name) is None


def test_logout_revokes_active_refresh_session_and_deletes_cookie(
    authenticated_client,
) -> None:
    client, session_factory, settings, _ = authenticated_client
    assert _login(client).status_code == 200

    response = client.post("/auth/logout")

    assert response.status_code == 204
    assert client.cookies.get(settings.auth_refresh_cookie_name) is None
    with session_factory() as session:
        persisted_session = session.scalar(select(UserSession))
        assert persisted_session is not None
        assert persisted_session.revoked_at is not None


def test_logout_without_refresh_cookie_is_idempotent(authenticated_client) -> None:
    client, _, _, _ = authenticated_client

    response = client.post("/auth/logout")

    assert response.status_code == 204
