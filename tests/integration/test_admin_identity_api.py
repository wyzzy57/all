from datetime import UTC, datetime, timedelta
import os
import threading
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from visiox_api.routes.admin_users import _lock_organization_and_reload_actor
from visiox_api.services.security import verify_password
from visiox_db.models.identity import (
    ROLE_ADMIN,
    STATUS_ACTIVE,
    STATUS_DELETED,
    AuditLog,
    Organization,
    ResourceAllocationPolicy,
    ResourceGrant,
    User,
    UserGroup,
    UserGroupMembership,
    UserSession,
)


def _assert_no_secrets(response) -> None:
    assert "password_hash" not in response.text
    assert "refresh_token_hash" not in response.text


def test_admin_routes_reject_member_and_list_users_with_filters(
    authenticated_client,
) -> None:
    client, _, _, identity = authenticated_client
    for path in (
        "/admin/users",
        "/admin/groups",
        "/admin/resource-grants",
        "/admin/resource-allocations",
        "/admin/audit-logs",
    ):
        assert client.get(path, headers=identity["member_headers"]).status_code == 403

    response = client.get(
        "/admin/users?search=mem&status=active&role=member",
        headers=identity["admin_headers"],
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert [item["username"] for item in response.json()["items"]] == ["member"]
    _assert_no_secrets(response)


def test_create_users_returns_explicit_or_generated_password_once_and_conflicts(
    authenticated_client,
) -> None:
    client, _, _, identity = authenticated_client
    explicit = client.post(
        "/admin/users",
        headers=identity["admin_headers"],
        json={
            "username": "explicit",
            "display_name": "Explicit",
            "email": "explicit@example.test",
            "role": "member",
            "temporary_password": "temporary-password",
        },
    )
    assert explicit.status_code == 201
    assert explicit.json()["temporary_password"] == "temporary-password"
    assert explicit.json()["must_change_password"] is True
    generated = client.post(
        "/admin/users",
        headers=identity["admin_headers"],
        json={
            "username": "generated",
            "display_name": "Generated",
            "email": "generated@example.test",
            "role": "member",
        },
    )
    assert generated.status_code == 201
    generated_password = generated.json()["temporary_password"]
    assert len(generated_password) >= 20
    listing = client.get("/admin/users", headers=identity["admin_headers"])
    assert generated_password not in listing.text
    _assert_no_secrets(explicit)
    _assert_no_secrets(generated)
    _assert_no_secrets(listing)

    for payload in (
        {
            "username": "explicit",
            "display_name": "Duplicate",
            "email": "new@example.test",
            "role": "member",
        },
        {
            "username": "new-name",
            "display_name": "Duplicate",
            "email": "explicit@example.test",
            "role": "member",
        },
    ):
        assert (
            client.post(
                "/admin/users", headers=identity["admin_headers"], json=payload
            ).status_code
            == 409
        )


def test_user_update_reset_and_soft_delete_revoke_sessions(
    authenticated_client,
) -> None:
    client, session_factory, _, identity = authenticated_client
    with session_factory() as session:
        session.add(
            UserSession(
                user_id=identity["member_id"],
                refresh_token_hash="a" * 64,
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        session.commit()

    changed = client.patch(
        f"/admin/users/{identity['member_id']}",
        headers=identity["admin_headers"],
        json={"role": "admin", "status": "disabled", "display_name": "Changed"},
    )
    assert changed.status_code == 200
    assert changed.json()["role"] == "admin"
    assert changed.json()["status"] == "disabled"
    enabled = client.patch(
        f"/admin/users/{identity['member_id']}",
        headers=identity["admin_headers"],
        json={"status": "active"},
    )
    assert enabled.status_code == 200
    duplicate_email = client.patch(
        f"/admin/users/{identity['member_id']}",
        headers=identity["admin_headers"],
        json={"email": "admin@example.test"},
    )
    assert duplicate_email.status_code == 409
    reset = client.post(
        f"/admin/users/{identity['member_id']}/reset-password",
        headers=identity["admin_headers"],
        json={},
    )
    assert reset.status_code == 200
    assert reset.json()["temporary_password"]
    deleted = client.delete(
        f"/admin/users/{identity['member_id']}", headers=identity["admin_headers"]
    )
    assert deleted.status_code == 204

    with session_factory() as session:
        user = session.get(User, identity["member_id"])
        persisted_session = session.scalar(
            select(UserSession).where(UserSession.user_id == identity["member_id"])
        )
        assert user is not None and user.status == STATUS_DELETED
        assert user.must_change_password is True
        assert verify_password(user.password_hash, reset.json()["temporary_password"])
        assert (
            persisted_session is not None and persisted_session.revoked_at is not None
        )

    reactivation = client.patch(
        f"/admin/users/{identity['member_id']}",
        headers=identity["admin_headers"],
        json={"status": "active", "display_name": "Resurrected"},
    )
    assert reactivation.status_code == 404
    with session_factory() as session:
        user = session.get(User, identity["member_id"])
        assert user is not None
        assert user.status == STATUS_DELETED
        assert user.display_name == "Changed"


def test_admin_can_edit_own_profile_but_cannot_change_own_access_or_delete_self(
    authenticated_client,
) -> None:
    client, session_factory, _, identity = authenticated_client

    backup_admin = client.post(
        "/admin/users",
        headers=identity["admin_headers"],
        json={
            "username": "self-protection-backup",
            "display_name": "Self Protection Backup",
            "email": "self-protection-backup@example.test",
            "role": "admin",
            "temporary_password": "temporary-password",
        },
    )
    assert backup_admin.status_code == 201

    with session_factory() as session:
        session.add(
            UserSession(
                user_id=identity["admin_id"],
                refresh_token_hash="c" * 64,
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        session.commit()

    profile = client.patch(
        f"/admin/users/{identity['admin_id']}",
        headers=identity["admin_headers"],
        json={
            "display_name": "Updated Administrator",
            "email": "updated-admin@example.test",
        },
    )
    assert profile.status_code == 200
    assert profile.json()["display_name"] == "Updated Administrator"
    assert profile.json()["email"] == "updated-admin@example.test"

    with session_factory() as session:
        audit_count = session.scalar(select(func.count()).select_from(AuditLog))
        active_sessions = session.scalar(
            select(func.count())
            .select_from(UserSession)
            .where(
                UserSession.user_id == identity["admin_id"],
                UserSession.revoked_at.is_(None),
            )
        )

    for payload in ({"role": "member"}, {"status": "disabled"}):
        response = client.patch(
            f"/admin/users/{identity['admin_id']}",
            headers=identity["admin_headers"],
            json=payload,
        )
        assert response.status_code == 409
        assert response.json()["detail"] == "Administrators cannot change their own role or status"

    deleted = client.delete(
        f"/admin/users/{identity['admin_id']}",
        headers=identity["admin_headers"],
    )
    assert deleted.status_code == 409
    assert deleted.json()["detail"] == "Administrators cannot delete themselves"

    with session_factory() as session:
        admin = session.get(User, identity["admin_id"])
        assert admin is not None
        assert admin.role == "admin"
        assert admin.status == STATUS_ACTIVE
        assert (
            session.scalar(select(func.count()).select_from(AuditLog)) == audit_count
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(UserSession)
                .where(
                    UserSession.user_id == identity["admin_id"],
                    UserSession.revoked_at.is_(None),
                )
            )
            == active_sessions
        )


def test_last_active_admin_cannot_be_demoted_disabled_or_deleted(
    authenticated_client,
) -> None:
    client, session_factory, _, identity = authenticated_client

    with session_factory() as session:
        session.add(
            UserSession(
                user_id=identity["admin_id"],
                refresh_token_hash="d" * 64,
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        session.commit()
        audit_count = session.scalar(select(func.count()).select_from(AuditLog))

    for payload in ({"role": "member"}, {"status": "disabled"}):
        response = client.patch(
            f"/admin/users/{identity['admin_id']}",
            headers=identity["admin_headers"],
            json=payload,
        )
        assert response.status_code == 409
        assert (
            response.json()["detail"]
            == "Organization must retain at least one active administrator"
        )

    deleted = client.delete(
        f"/admin/users/{identity['admin_id']}",
        headers=identity["admin_headers"],
    )
    assert deleted.status_code == 409
    assert (
        deleted.json()["detail"]
        == "Organization must retain at least one active administrator"
    )

    with session_factory() as session:
        admin = session.get(User, identity["admin_id"])
        assert admin is not None
        assert admin.role == "admin"
        assert admin.status == STATUS_ACTIVE
        assert (
            session.scalar(select(func.count()).select_from(AuditLog)) == audit_count
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(UserSession)
                .where(
                    UserSession.user_id == identity["admin_id"],
                    UserSession.revoked_at.is_(None),
                )
            )
            == 1
        )


def test_admin_may_remove_another_active_admin_when_one_will_remain(
    authenticated_client,
) -> None:
    client, session_factory, _, identity = authenticated_client

    def create_and_login_admin(username: str) -> tuple[str, dict[str, str]]:
        response = client.post(
            "/admin/users",
            headers=identity["admin_headers"],
            json={
                "username": username,
                "display_name": username.title(),
                "email": f"{username}@example.test",
                "role": "admin",
                "temporary_password": "temporary-password",
            },
        )
        assert response.status_code == 201
        login = client.post(
            "/auth/login",
            json={"username": username, "password": "temporary-password"},
        )
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        assert client.get("/auth/me", headers=headers).status_code == 200
        return response.json()["id"], headers

    demoted_id, demoted_headers = create_and_login_admin("demoted-admin")
    demoted = client.patch(
        f"/admin/users/{demoted_id}",
        headers=identity["admin_headers"],
        json={"role": "member"},
    )
    assert demoted.status_code == 200
    assert demoted.json()["role"] == "member"
    assert client.get("/auth/me", headers=demoted_headers).status_code == 200
    with session_factory() as session:
        demoted_user = session.get(User, demoted_id)
        assert demoted_user is not None
        assert demoted_user.role == "member"
        assert demoted_user.status == STATUS_ACTIVE
        assert (
            session.scalar(
                select(func.count())
                .select_from(UserSession)
                .where(
                    UserSession.user_id == demoted_id,
                    UserSession.revoked_at.is_(None),
                )
            )
            == 1
        )

    disabled_id, disabled_headers = create_and_login_admin("disabled-admin")
    disabled = client.patch(
        f"/admin/users/{disabled_id}",
        headers=identity["admin_headers"],
        json={"status": "disabled"},
    )
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"
    with session_factory() as session:
        disabled_user = session.get(User, disabled_id)
        assert disabled_user is not None
        assert disabled_user.role == "admin"
        assert disabled_user.status == "disabled"
        assert (
            session.scalar(
                select(func.count())
                .select_from(UserSession)
                .where(
                    UserSession.user_id == disabled_id,
                    UserSession.revoked_at.is_(None),
                )
            )
            == 0
        )
    assert client.get("/auth/me", headers=disabled_headers).status_code == 401

    deleted_id, deleted_headers = create_and_login_admin("deleted-admin")
    deleted = client.delete(
        f"/admin/users/{deleted_id}",
        headers=identity["admin_headers"],
    )
    assert deleted.status_code == 204
    with session_factory() as session:
        deleted_user = session.get(User, deleted_id)
        assert deleted_user is not None
        assert deleted_user.status == STATUS_DELETED
        assert (
            session.scalar(
                select(func.count())
                .select_from(UserSession)
                .where(
                    UserSession.user_id == deleted_id,
                    UserSession.revoked_at.is_(None),
                )
            )
            == 0
        )
    assert client.get("/auth/me", headers=deleted_headers).status_code == 401

    with session_factory() as session:
        original = session.get(User, identity["admin_id"])
        assert original is not None and original.status == STATUS_ACTIVE
        assert original.role == "admin"


def test_organization_lock_reloads_and_revalidates_stale_actor(
    authenticated_client,
) -> None:
    _, session_factory, _, identity = authenticated_client

    with session_factory() as stale_session:
        actor = stale_session.get(User, identity["admin_id"])
        assert actor is not None and actor.status == STATUS_ACTIVE

        with session_factory() as concurrent_session:
            concurrent_actor = concurrent_session.get(User, identity["admin_id"])
            assert concurrent_actor is not None
            concurrent_actor.status = "disabled"
            concurrent_session.commit()

        with pytest.raises(HTTPException) as error:
            _lock_organization_and_reload_actor(stale_session, actor)
        assert error.value.status_code == 403
        assert error.value.detail == "Administrator access is no longer active"
        assert actor.status == "disabled"


@pytest.mark.skipif(
    not os.getenv("VISIOX_TEST_POSTGRES_DSN"),
    reason="VISIOX_TEST_POSTGRES_DSN is not configured",
)
def test_postgres_organization_lock_serializes_continuity_decisions() -> None:
    database_url = os.environ["VISIOX_TEST_POSTGRES_DSN"]
    engine = create_engine(database_url)
    if engine.dialect.name != "postgresql":
        pytest.skip("VISIOX_TEST_POSTGRES_DSN must use PostgreSQL")
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex

    with session_factory.begin() as session:
        organization = Organization(
            name=f"Continuity {suffix}",
            slug=f"continuity-{suffix}",
            status=STATUS_ACTIVE,
        )
        session.add(organization)
        session.flush()
        organization_id = organization.id
        users = [
            User(
                organization_id=organization_id,
                username=f"admin-{index}-{suffix}",
                display_name=f"Admin {index}",
                email=f"admin-{index}-{suffix}@example.test",
                password_hash="not-used",
                role=ROLE_ADMIN,
                status=STATUS_ACTIVE,
                must_change_password=False,
            )
            for index in (1, 2)
        ]
        session.add_all(users)
        session.flush()
        actor_ids = [user.id for user in users]

    first_acquired = threading.Event()
    release_first = threading.Event()
    second_acquired = threading.Event()
    errors: list[BaseException] = []

    def hold_first_lock() -> None:
        try:
            with session_factory() as session:
                actor = session.get(User, actor_ids[0])
                assert actor is not None
                _lock_organization_and_reload_actor(session, actor)
                first_acquired.set()
                assert release_first.wait(timeout=10)
                session.commit()
        except BaseException as error:
            errors.append(error)
            first_acquired.set()

    def wait_for_same_lock() -> None:
        try:
            assert first_acquired.wait(timeout=10)
            with session_factory() as session:
                actor = session.get(User, actor_ids[1])
                assert actor is not None
                _lock_organization_and_reload_actor(session, actor)
                second_acquired.set()
                session.commit()
        except BaseException as error:
            errors.append(error)

    first = threading.Thread(target=hold_first_lock)
    second = threading.Thread(target=wait_for_same_lock)
    first.start()
    second_started = False
    try:
        assert first_acquired.wait(timeout=10)
        second.start()
        second_started = True
        assert not second_acquired.wait(timeout=0.5)
    finally:
        release_first.set()
        first.join(timeout=10)
        if second_started:
            second.join(timeout=10)

    try:
        assert second_started
        assert not first.is_alive()
        assert not second.is_alive()
        assert second_acquired.is_set()
        assert errors == []
    finally:
        with session_factory.begin() as session:
            session.execute(delete(User).where(User.organization_id == organization_id))
            session.execute(
                delete(Organization).where(Organization.id == organization_id)
            )
        engine.dispose()


def test_group_crud_membership_replacement_and_org_validation(
    authenticated_client,
) -> None:
    client, session_factory, _, identity = authenticated_client
    created = client.post(
        "/admin/groups",
        headers=identity["admin_headers"],
        json={"name": "operators", "description": "Operators"},
    )
    assert created.status_code == 201
    group_id = created.json()["id"]
    assert created.json()["member_count"] == 0
    listing = client.get("/admin/groups", headers=identity["admin_headers"])
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert (
        client.post(
            "/admin/groups",
            headers=identity["admin_headers"],
            json={"name": "operators"},
        ).status_code
        == 409
    )
    replaced = client.put(
        f"/admin/groups/{group_id}/members",
        headers=identity["admin_headers"],
        json={"user_ids": [identity["member_id"], identity["admin_id"]]},
    )
    assert replaced.status_code == 200
    assert replaced.json()["member_count"] == 2
    assert set(replaced.json()["member_ids"]) == {
        identity["member_id"],
        identity["admin_id"],
    }
    invalid = client.put(
        f"/admin/groups/{group_id}/members",
        headers=identity["admin_headers"],
        json={"user_ids": [identity["outsider_id"]]},
    )
    assert invalid.status_code == 422
    assert (
        client.patch(
            f"/admin/groups/{group_id}",
            headers=identity["admin_headers"],
            json={"name": "renamed"},
        ).status_code
        == 200
    )
    grant = client.put(
        "/admin/resource-grants",
        headers=identity["admin_headers"],
        json={
            "resource_type": "dataset",
            "resource_id": "group-resource",
            "principal_type": "group",
            "principal_id": group_id,
            "permissions": ["view"],
        },
    )
    allocation = client.put(
        "/admin/resource-allocations",
        headers=identity["admin_headers"],
        json={
            "principal_type": "group",
            "principal_id": group_id,
            "resource_pool_id": identity["pool_id"],
        },
    )
    assert grant.status_code == 200
    assert allocation.status_code == 200
    assert (
        client.delete(
            f"/admin/groups/{group_id}", headers=identity["admin_headers"]
        ).status_code
        == 204
    )
    with session_factory() as session:
        assert session.get(UserGroup, group_id) is None
        assert (
            session.scalar(select(func.count()).select_from(UserGroupMembership)) == 0
        )
        assert session.get(ResourceGrant, grant.json()["id"]) is None
        assert session.get(ResourceAllocationPolicy, allocation.json()["id"]) is None


def test_resource_grant_upsert_validation_and_revoke(authenticated_client) -> None:
    client, session_factory, _, identity = authenticated_client
    payload = {
        "resource_type": "dataset",
        "resource_id": "resource-1",
        "principal_type": "user",
        "principal_id": identity["member_id"],
        "permissions": ["view", "use"],
    }
    created = client.put(
        "/admin/resource-grants", headers=identity["admin_headers"], json=payload
    )
    assert created.status_code == 200
    grant_id = created.json()["id"]
    payload["permissions"] = ["manage"]
    updated = client.put(
        "/admin/resource-grants", headers=identity["admin_headers"], json=payload
    )
    assert updated.status_code == 200
    assert updated.json()["id"] == grant_id
    assert updated.json()["permissions"] == ["manage"]
    listing = client.get("/admin/resource-grants", headers=identity["admin_headers"])
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert (
        client.put(
            "/admin/resource-grants",
            headers=identity["admin_headers"],
            json={**payload, "permissions": ["invalid"]},
        ).status_code
        == 422
    )
    assert (
        client.put(
            "/admin/resource-grants",
            headers=identity["admin_headers"],
            json={
                **payload,
                "principal_type": "organization",
                "principal_id": identity["other_organization_id"],
            },
        ).status_code
        == 422
    )
    assert (
        client.put(
            "/admin/resource-grants",
            headers=identity["admin_headers"],
            json={**payload, "principal_id": identity["outsider_id"]},
        ).status_code
        == 422
    )
    assert (
        client.delete(
            f"/admin/resource-grants/{grant_id}", headers=identity["admin_headers"]
        ).status_code
        == 204
    )
    with session_factory() as session:
        assert session.get(ResourceGrant, grant_id) is None


def test_resource_allocation_upsert_validation_and_revoke(
    authenticated_client,
) -> None:
    client, session_factory, _, identity = authenticated_client
    payload = {
        "principal_type": "user",
        "principal_id": identity["member_id"],
        "resource_pool_id": identity["pool_id"],
        "max_gpu_count": 1,
    }
    created = client.put(
        "/admin/resource-allocations", headers=identity["admin_headers"], json=payload
    )
    assert created.status_code == 200
    policy_id = created.json()["id"]
    with session_factory() as session:
        session.add(
            ResourceAllocationPolicy(
                organization_id=identity["organization_id"],
                principal_type="user",
                principal_id=identity["member_id"],
                resource_pool_id=identity["pool_id"],
                max_gpu_count=99,
                created_by=identity["admin_id"],
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
    updated = client.put(
        "/admin/resource-allocations",
        headers=identity["admin_headers"],
        json={**payload, "max_gpu_count": 2},
    )
    assert updated.status_code == 200
    assert updated.json()["id"] == policy_id
    assert updated.json()["max_gpu_count"] == 2
    with session_factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(ResourceAllocationPolicy)
                .where(
                    ResourceAllocationPolicy.organization_id
                    == identity["organization_id"],
                    ResourceAllocationPolicy.principal_type == "user",
                    ResourceAllocationPolicy.principal_id == identity["member_id"],
                    ResourceAllocationPolicy.resource_pool_id == identity["pool_id"],
                )
            )
            == 1
        )
    assert (
        client.put(
            "/admin/resource-allocations",
            headers=identity["admin_headers"],
            json={**payload, "max_gpu_count": -1},
        ).status_code
        == 422
    )
    assert (
        client.put(
            "/admin/resource-allocations",
            headers=identity["admin_headers"],
            json={**payload, "resource_pool_id": "missing"},
        ).status_code
        == 404
    )
    listing = client.get(
        "/admin/resource-allocations", headers=identity["admin_headers"]
    )
    assert listing.json()["total"] == 1
    assert (
        client.delete(
            f"/admin/resource-allocations/{policy_id}",
            headers=identity["admin_headers"],
        ).status_code
        == 204
    )
    with session_factory() as session:
        assert session.get(ResourceAllocationPolicy, policy_id) is None


def test_list_pagination_filters_and_boundaries(authenticated_client) -> None:
    client, _, _, identity = authenticated_client
    for name in ("group-a", "group-b"):
        assert (
            client.post(
                "/admin/groups",
                headers=identity["admin_headers"],
                json={"name": name},
            ).status_code
            == 201
        )
    for resource_id in ("resource-a", "resource-b"):
        assert (
            client.put(
                "/admin/resource-grants",
                headers=identity["admin_headers"],
                json={
                    "resource_type": "dataset",
                    "resource_id": resource_id,
                    "principal_type": "user",
                    "principal_id": identity["member_id"],
                    "permissions": ["view"],
                },
            ).status_code
            == 200
        )
    for principal_id in (identity["admin_id"], identity["member_id"]):
        assert (
            client.put(
                "/admin/resource-allocations",
                headers=identity["admin_headers"],
                json={
                    "principal_type": "user",
                    "principal_id": principal_id,
                    "resource_pool_id": identity["pool_id"],
                },
            ).status_code
            == 200
        )

    for path in (
        "/admin/users",
        "/admin/groups",
        "/admin/resource-grants",
        "/admin/resource-allocations",
    ):
        page = client.get(f"{path}?offset=1&limit=1", headers=identity["admin_headers"])
        assert page.status_code == 200
        assert page.json()["total"] == 2
        assert page.json()["offset"] == 1
        assert page.json()["limit"] == 1
        assert len(page.json()["items"]) == 1
        for query in ("offset=-1", "limit=0", "limit=201"):
            assert (
                client.get(
                    f"{path}?{query}", headers=identity["admin_headers"]
                ).status_code
                == 422
            )

    deleted_filter = client.get(
        "/admin/users?status=deleted", headers=identity["admin_headers"]
    )
    assert deleted_filter.status_code == 200
    assert deleted_filter.json()["total"] == 0
    for query in ("role=owner", "role=", "status=", "status=unknown"):
        assert (
            client.get(
                f"/admin/users?{query}", headers=identity["admin_headers"]
            ).status_code
            == 422
        )


def test_patch_rejects_explicit_null_for_non_nullable_fields(
    authenticated_client,
) -> None:
    client, _, _, identity = authenticated_client
    group = client.post(
        "/admin/groups",
        headers=identity["admin_headers"],
        json={"name": "null-test"},
    ).json()
    cases = [
        (f"/admin/users/{identity['member_id']}", identity["admin_headers"], field)
        for field in ("display_name", "email", "role", "status")
    ]
    cases += [
        ("/account/profile", identity["member_headers"], field)
        for field in ("display_name", "email")
    ]
    cases.append((f"/admin/groups/{group['id']}", identity["admin_headers"], "name"))

    for path, headers, field in cases:
        response = client.patch(path, headers=headers, json={field: None})
        assert response.status_code == 422, (path, field, response.text)


def test_cross_tenant_mutations_return_not_found(authenticated_client) -> None:
    client, session_factory, _, identity = authenticated_client
    with session_factory() as session:
        foreign_group = UserGroup(
            organization_id=identity["other_organization_id"],
            name="foreign-group",
            status=STATUS_ACTIVE,
        )
        session.add(foreign_group)
        session.flush()
        foreign_grant = ResourceGrant(
            organization_id=identity["other_organization_id"],
            resource_type="dataset",
            resource_id="foreign-resource",
            principal_type="user",
            principal_id=identity["outsider_id"],
            permissions=["view"],
            created_by=identity["outsider_id"],
        )
        foreign_allocation = ResourceAllocationPolicy(
            organization_id=identity["other_organization_id"],
            principal_type="user",
            principal_id=identity["outsider_id"],
            resource_pool_id=identity["pool_id"],
            created_by=identity["outsider_id"],
        )
        session.add_all([foreign_grant, foreign_allocation])
        session.commit()
        foreign_ids = (foreign_group.id, foreign_grant.id, foreign_allocation.id)

    requests = (
        ("patch", f"/admin/users/{identity['outsider_id']}", {"display_name": "X"}),
        ("delete", f"/admin/users/{identity['outsider_id']}", None),
        ("patch", f"/admin/groups/{foreign_ids[0]}", {"name": "X"}),
        ("delete", f"/admin/groups/{foreign_ids[0]}", None),
        ("delete", f"/admin/resource-grants/{foreign_ids[1]}", None),
        ("delete", f"/admin/resource-allocations/{foreign_ids[2]}", None),
    )
    for method, path, payload in requests:
        response = client.request(
            method,
            path,
            headers=identity["admin_headers"],
            json=payload,
        )
        assert response.status_code == 404, (method, path, response.text)


def test_conflict_rolls_back_mutation_and_audit(authenticated_client) -> None:
    client, session_factory, _, identity = authenticated_client
    with session_factory() as session:
        before_audits = session.scalar(select(func.count()).select_from(AuditLog))
    response = client.patch(
        f"/admin/users/{identity['member_id']}",
        headers=identity["admin_headers"],
        json={"display_name": "Should Roll Back", "email": "admin@example.test"},
    )
    assert response.status_code == 409
    with session_factory() as session:
        member = session.get(User, identity["member_id"])
        assert member is not None
        assert member.display_name == "Member"
        assert (
            session.scalar(select(func.count()).select_from(AuditLog)) == before_audits
        )


def test_account_profile_and_password_change(authenticated_client) -> None:
    client, session_factory, _, identity = authenticated_client
    with session_factory() as session:
        session.add(
            UserSession(
                user_id=identity["member_id"],
                refresh_token_hash="b" * 64,
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        session.commit()
    profile = client.patch(
        "/account/profile",
        headers=identity["member_headers"],
        json={"display_name": "Updated Member", "email": "updated@example.test"},
    )
    assert profile.status_code == 200
    assert profile.json()["display_name"] == "Updated Member"
    assert (
        client.patch(
            "/account/profile",
            headers=identity["member_headers"],
            json={"email": "admin@example.test"},
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/account/change-password",
            headers=identity["member_headers"],
            json={"current_password": "wrong", "new_password": "new-password-123"},
        ).status_code
        == 400
    )
    changed = client.post(
        "/account/change-password",
        headers=identity["member_headers"],
        json={
            "current_password": "member-password",
            "new_password": "new-password-123",
        },
    )
    assert changed.status_code == 204
    with session_factory() as session:
        member = session.get(User, identity["member_id"])
        assert member is not None
        assert verify_password(member.password_hash, "new-password-123")
        assert member.must_change_password is False
        persisted_session = session.scalar(
            select(UserSession).where(UserSession.user_id == identity["member_id"])
        )
        assert (
            persisted_session is not None and persisted_session.revoked_at is not None
        )
    _assert_no_secrets(profile)
    _assert_no_secrets(changed)


def test_successful_mutations_are_audited_and_logs_are_listed(
    authenticated_client,
) -> None:
    client, session_factory, _, identity = authenticated_client
    response = client.patch(
        "/account/profile",
        headers=identity["member_headers"],
        json={"display_name": "Audited Member"},
    )
    assert response.status_code == 200
    response = client.post(
        "/admin/groups",
        headers=identity["admin_headers"],
        json={"name": "audited-group"},
    )
    assert response.status_code == 201
    logs = client.get("/admin/audit-logs", headers=identity["admin_headers"])
    assert logs.status_code == 200
    assert logs.json()["total"] >= 2
    assert {item["action"] for item in logs.json()["items"]} >= {
        "account.profile.update",
        "admin.group.create",
    }
    _assert_no_secrets(logs)
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AuditLog)) >= 2
