import json
from collections.abc import Iterator
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from visiox_api.services.audit import REDACTED, record_audit
from visiox_api.services.authorization import (
    ALL_PERMISSIONS,
    AuthorizationDeniedError,
    assert_permission,
    resolve_permissions,
)
from visiox_db.models.identity import (
    PERMISSION_DELETE,
    PERMISSION_EDIT,
    PERMISSION_INVOKE,
    PERMISSION_MANAGE,
    PERMISSION_USE,
    PERMISSION_VIEW,
    PRINCIPAL_GROUP,
    PRINCIPAL_ORGANIZATION,
    PRINCIPAL_USER,
    ROLE_ADMIN,
    ROLE_MEMBER,
    AuditLog,
    Organization,
    ResourceGrant,
    User,
    UserGroup,
    UserGroupMembership,
)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    tables = [
        Organization.__table__,
        User.__table__,
        UserGroup.__table__,
        UserGroupMembership.__table__,
        ResourceGrant.__table__,
        AuditLog.__table__,
    ]
    Organization.metadata.create_all(engine, tables=tables)
    with Session(engine) as db_session:
        yield db_session
    engine.dispose()


def _organization(session: Session, organization_id: str) -> Organization:
    organization = Organization(
        id=organization_id,
        name=f"Organization {organization_id}",
        slug=organization_id,
    )
    session.add(organization)
    session.flush()
    return organization


def _user(
    session: Session,
    organization_id: str,
    user_id: str,
    *,
    role: str = ROLE_MEMBER,
) -> User:
    user = User(
        id=user_id,
        organization_id=organization_id,
        username=user_id,
        display_name=user_id,
        email=f"{user_id}@example.com",
        password_hash="hashed",
        role=role,
    )
    session.add(user)
    session.flush()
    return user


def _grant(
    session: Session,
    *,
    organization_id: str,
    principal_type: str,
    principal_id: str,
    permissions: list[str],
    resource_type: str = "dataset",
    resource_id: str = "dataset-1",
    expires_at: datetime | None = None,
) -> None:
    session.add(
        ResourceGrant(
            organization_id=organization_id,
            resource_type=resource_type,
            resource_id=resource_id,
            principal_type=principal_type,
            principal_id=principal_id,
            permissions=permissions,
            expires_at=expires_at,
        )
    )
    session.flush()


def _resolve(session: Session, actor: User) -> set[str]:
    return resolve_permissions(
        session,
        actor,
        resource_type="dataset",
        resource_id="dataset-1",
        owner_user_id="owner-1",
    )


def test_all_permissions_uses_identity_permission_constants() -> None:
    assert ALL_PERMISSIONS == {
        PERMISSION_VIEW,
        PERMISSION_USE,
        PERMISSION_EDIT,
        PERMISSION_DELETE,
        PERMISSION_MANAGE,
        PERMISSION_INVOKE,
    }


def test_administrator_has_all_permissions(session: Session) -> None:
    _organization(session, "org-1")
    admin = _user(session, "org-1", "admin-1", role=ROLE_ADMIN)

    assert _resolve(session, admin) == ALL_PERMISSIONS


def test_resource_owner_has_all_permissions(session: Session) -> None:
    _organization(session, "org-1")
    owner = _user(session, "org-1", "owner-1")

    assert _resolve(session, owner) == ALL_PERMISSIONS


def test_direct_user_grant_is_applied(session: Session) -> None:
    _organization(session, "org-1")
    actor = _user(session, "org-1", "member-1")
    _grant(
        session,
        organization_id="org-1",
        principal_type=PRINCIPAL_USER,
        principal_id=actor.id,
        permissions=[PERMISSION_VIEW],
    )

    assert _resolve(session, actor) == {PERMISSION_VIEW}


def test_group_grant_is_applied(session: Session) -> None:
    _organization(session, "org-1")
    actor = _user(session, "org-1", "member-1")
    group = UserGroup(id="group-1", organization_id="org-1", name="Reviewers")
    session.add_all([group, UserGroupMembership(group_id=group.id, user_id=actor.id)])
    session.flush()
    _grant(
        session,
        organization_id="org-1",
        principal_type=PRINCIPAL_GROUP,
        principal_id=group.id,
        permissions=[PERMISSION_USE],
    )

    assert _resolve(session, actor) == {PERMISSION_USE}


def test_organization_public_grant_is_applied(session: Session) -> None:
    _organization(session, "org-1")
    actor = _user(session, "org-1", "member-1")
    _grant(
        session,
        organization_id="org-1",
        principal_type=PRINCIPAL_ORGANIZATION,
        principal_id="org-1",
        permissions=[PERMISSION_VIEW],
    )

    assert _resolve(session, actor) == {PERMISSION_VIEW}


def test_expired_grant_is_excluded_with_sqlite_naive_datetime(
    session: Session,
) -> None:
    _organization(session, "org-1")
    actor = _user(session, "org-1", "member-1")
    _grant(
        session,
        organization_id="org-1",
        principal_type=PRINCIPAL_USER,
        principal_id=actor.id,
        permissions=[PERMISSION_VIEW],
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    session.expire_all()

    assert _resolve(session, actor) == set()


def test_permissions_are_unioned_and_unknown_values_are_discarded(
    session: Session,
) -> None:
    _organization(session, "org-1")
    actor = _user(session, "org-1", "member-1")
    group = UserGroup(id="group-1", organization_id="org-1", name="Reviewers")
    session.add_all([group, UserGroupMembership(group_id=group.id, user_id=actor.id)])
    session.flush()
    _grant(
        session,
        organization_id="org-1",
        principal_type=PRINCIPAL_USER,
        principal_id=actor.id,
        permissions=[PERMISSION_EDIT, "corrupted-permission"],
    )
    _grant(
        session,
        organization_id="org-1",
        principal_type=PRINCIPAL_GROUP,
        principal_id=group.id,
        permissions=[PERMISSION_USE],
    )
    _grant(
        session,
        organization_id="org-1",
        principal_type=PRINCIPAL_ORGANIZATION,
        principal_id="org-1",
        permissions=[PERMISSION_VIEW, PERMISSION_EDIT],
    )

    assert _resolve(session, actor) == {
        PERMISSION_VIEW,
        PERMISSION_USE,
        PERMISSION_EDIT,
    }


def test_cross_organization_grant_is_not_applied(session: Session) -> None:
    _organization(session, "org-1")
    _organization(session, "org-2")
    actor = _user(session, "org-1", "member-1")
    _grant(
        session,
        organization_id="org-2",
        principal_type=PRINCIPAL_USER,
        principal_id=actor.id,
        permissions=[PERMISSION_MANAGE],
    )

    assert _resolve(session, actor) == set()


def test_cross_organization_group_membership_is_not_applied(session: Session) -> None:
    _organization(session, "org-1")
    _organization(session, "org-2")
    actor = _user(session, "org-1", "member-1")
    foreign_group = UserGroup(
        id="foreign-group",
        organization_id="org-2",
        name="Foreign reviewers",
    )
    session.add_all(
        [
            foreign_group,
            UserGroupMembership(group_id=foreign_group.id, user_id=actor.id),
        ]
    )
    session.flush()
    _grant(
        session,
        organization_id="org-1",
        principal_type=PRINCIPAL_GROUP,
        principal_id=foreign_group.id,
        permissions=[PERMISSION_MANAGE],
    )

    assert _resolve(session, actor) == set()


def test_assert_permission_raises_structured_domain_error(session: Session) -> None:
    _organization(session, "org-1")
    actor = _user(session, "org-1", "member-1")

    with pytest.raises(AuthorizationDeniedError) as caught:
        assert_permission(
            session,
            actor,
            resource_type="dataset",
            resource_id="dataset-1",
            owner_user_id="owner-1",
            permission=PERMISSION_DELETE,
        )

    error = caught.value
    assert error.code == "permission_denied"
    assert error.resource_type == "dataset"
    assert error.resource_id == "dataset-1"
    assert error.permission == PERMISSION_DELETE
    assert actor.id not in str(error)
    assert actor.email not in str(error)


def test_assert_permission_returns_when_permission_is_granted(session: Session) -> None:
    _organization(session, "org-1")
    actor = _user(session, "org-1", "member-1")
    _grant(
        session,
        organization_id="org-1",
        principal_type=PRINCIPAL_USER,
        principal_id=actor.id,
        permissions=[PERMISSION_VIEW],
    )

    assert (
        assert_permission(
            session,
            actor,
            resource_type="dataset",
            resource_id="dataset-1",
            owner_user_id="owner-1",
            permission=PERMISSION_VIEW,
        )
        is None
    )


def test_record_audit_redacts_nested_secrets_without_mutating_metadata(
    session: Session,
) -> None:
    _organization(session, "org-1")
    actor = _user(session, "org-1", "member-1")
    metadata = {
        "operation": "share",
        "Password": "password-value",
        "nested": {
            "TOKEN": "token-value",
            "safe": "kept",
            "items": [
                {"secret": "secret-value", "count": 2},
                {"private_key": "private-key-value"},
            ],
        },
        "cookie": "cookie-value",
        "Authorization": "Bearer authorization-value",
    }

    audit_log = record_audit(
        session,
        actor,
        action="resource.share",
        resource_type="dataset",
        resource_id="dataset-1",
        result="success",
        request_id="request-1",
        metadata=metadata,
    )
    session.flush()
    persisted = session.scalar(select(AuditLog).where(AuditLog.id == audit_log.id))

    assert persisted is audit_log
    assert persisted.organization_id == actor.organization_id
    assert persisted.actor_user_id == actor.id
    assert persisted.action == "resource.share"
    assert persisted.resource_type == "dataset"
    assert persisted.resource_id == "dataset-1"
    assert persisted.result == "success"
    assert persisted.request_id == "request-1"
    assert persisted.metadata_json == {
        "operation": "share",
        "Password": REDACTED,
        "nested": {
            "TOKEN": REDACTED,
            "safe": "kept",
            "items": [
                {"secret": REDACTED, "count": 2},
                {"private_key": REDACTED},
            ],
        },
        "cookie": REDACTED,
        "Authorization": REDACTED,
    }
    assert metadata["Password"] == "password-value"
    assert metadata["nested"]["items"][0]["secret"] == "secret-value"

    serialized = json.dumps(persisted.metadata_json)
    for secret_value in (
        "password-value",
        "token-value",
        "secret-value",
        "private-key-value",
        "cookie-value",
        "authorization-value",
    ):
        assert secret_value not in serialized
        assert secret_value not in repr(persisted.metadata_json)


def test_record_audit_redacts_compound_sensitive_keys_and_preserves_safe_fields(
    session: Session,
) -> None:
    _organization(session, "org-1")
    actor = _user(session, "org-1", "member-1")
    metadata = {
        "access_token": "access-token-value",
        "refreshToken": "refresh-token-value",
        "credentials": {
            "bootstrap_password": "bootstrap-password-value",
            "clientSecret": "client-secret-value",
            "privateKey": "camel-private-key-value",
            "private_key": "snake-private-key-value",
            "client_name": "worker-1",
        },
        "request": {
            "authorization_header": "Bearer authorization-header-value",
            "headers": [
                {"cookie_value": "cookie-value"},
                {"header_name": "content-type"},
            ],
        },
        "public_key": "public-key-value",
        "refresh_interval": 30,
    }
    original_metadata = deepcopy(metadata)

    audit_log = record_audit(
        session,
        actor,
        action="resource.invoke",
        resource_type="service",
        resource_id="service-1",
        result="success",
        request_id="request-2",
        metadata=metadata,
    )
    session.flush()
    session.expire(audit_log)
    persisted = session.scalar(select(AuditLog).where(AuditLog.id == audit_log.id))

    assert persisted is not None
    assert persisted.metadata_json == {
        "access_token": REDACTED,
        "refreshToken": REDACTED,
        "credentials": {
            "bootstrap_password": REDACTED,
            "clientSecret": REDACTED,
            "privateKey": REDACTED,
            "private_key": REDACTED,
            "client_name": "worker-1",
        },
        "request": {
            "authorization_header": REDACTED,
            "headers": [
                {"cookie_value": REDACTED},
                {"header_name": "content-type"},
            ],
        },
        "public_key": "public-key-value",
        "refresh_interval": 30,
    }
    assert metadata == original_metadata

    serialized = json.dumps(persisted.metadata_json)
    for secret_value in (
        "access-token-value",
        "refresh-token-value",
        "bootstrap-password-value",
        "client-secret-value",
        "camel-private-key-value",
        "snake-private-key-value",
        "authorization-header-value",
        "cookie-value",
    ):
        assert secret_value not in serialized


def test_record_audit_redacts_passphrases_and_database_connections_recursively(
    session: Session,
) -> None:
    _organization(session, "org-1")
    actor = _user(session, "org-1", "member-1")
    metadata = {
        "passphrase": "top-level-passphrase",
        "database_url": "postgresql://audit-user:database-password@db:5432/visiox",
        "credentials": {
            "username": "audit-user",
            "host": "db.internal",
            "port": 5432,
            "database": "visiox",
            "client_name": "audit-worker",
            "passphrase": "nested-passphrase",
            "DSN": "host=db user=audit-user password=dsn-password dbname=visiox",
            "nested": [
                {
                    "connection_string": (
                        "postgresql://nested-user:nested-password@db:5432/visiox"
                    )
                }
            ],
        },
        "dashboard_url": "https://monitoring.example.test/audits",
    }
    original_metadata = deepcopy(metadata)

    audit_log = record_audit(
        session,
        actor,
        action="resource.connect",
        resource_type="node",
        resource_id="node-1",
        result="success",
        request_id="request-connection-redaction",
        metadata=metadata,
    )
    session.flush()
    session.expire(audit_log)
    persisted = session.scalar(select(AuditLog).where(AuditLog.id == audit_log.id))

    assert persisted is not None
    assert persisted.metadata_json == {
        "passphrase": REDACTED,
        "database_url": REDACTED,
        "credentials": {
            "username": "audit-user",
            "host": "db.internal",
            "port": 5432,
            "database": "visiox",
            "client_name": "audit-worker",
            "passphrase": REDACTED,
            "DSN": REDACTED,
            "nested": [{"connection_string": REDACTED}],
        },
        "dashboard_url": "https://monitoring.example.test/audits",
    }
    assert metadata == original_metadata

    serialized = json.dumps(persisted.metadata_json)
    for secret_value in (
        "top-level-passphrase",
        "database-password",
        "nested-passphrase",
        "dsn-password",
        "nested-password",
    ):
        assert secret_value not in serialized


def test_record_audit_converts_tuples_and_isolates_nested_metadata(
    session: Session,
) -> None:
    _organization(session, "org-1")
    actor = _user(session, "org-1", "member-1")
    tuple_item = {
        "access_token": "tuple-token-value",
        "safe": {"state": "before"},
    }
    metadata = {"entries": (tuple_item, {"count": 2})}

    audit_log = record_audit(
        session,
        actor,
        action="resource.inspect",
        resource_type="dataset",
        resource_id="dataset-1",
        result="success",
        request_id="request-3",
        metadata=metadata,
    )
    tuple_item["safe"]["state"] = "after"
    tuple_item["added_later"] = "must-not-appear"
    session.flush()
    session.expire(audit_log)
    persisted = session.scalar(select(AuditLog).where(AuditLog.id == audit_log.id))

    assert persisted is not None
    assert persisted.metadata_json == {
        "entries": [
            {
                "access_token": REDACTED,
                "safe": {"state": "before"},
            },
            {"count": 2},
        ]
    }
    assert isinstance(persisted.metadata_json["entries"], list)
    assert "tuple-token-value" not in json.dumps(persisted.metadata_json)
