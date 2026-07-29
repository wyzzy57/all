from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from visiox_db.base import Base, IdMixin, TimestampMixin, utc_now


ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"

STATUS_ACTIVE = "active"
STATUS_DISABLED = "disabled"
STATUS_DELETED = "deleted"

PRINCIPAL_USER = "user"
PRINCIPAL_GROUP = "group"
PRINCIPAL_ORGANIZATION = "organization"

PERMISSION_VIEW = "view"
PERMISSION_USE = "use"
PERMISSION_EDIT = "edit"
PERMISSION_DELETE = "delete"
PERMISSION_MANAGE = "manage"
PERMISSION_INVOKE = "invoke"

AUDIT_RESULT_SUCCESS = "success"
AUDIT_RESULT_DENIED = "denied"
AUDIT_RESULT_FAILED = "failed"


class Organization(IdMixin, TimestampMixin, Base):
    __tablename__ = "organizations"
    __table_args__ = (UniqueConstraint("slug", name="uq_organizations_slug"),)

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=STATUS_ACTIVE
    )


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("organization_id", "username", name="uq_users_org_username"),
        UniqueConstraint("organization_id", "email", name="uq_users_org_email"),
    )

    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    username: Mapped[str] = mapped_column(String(120), nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default=ROLE_MEMBER)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=STATUS_ACTIVE, index=True
    )
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserGroup(IdMixin, TimestampMixin, Base):
    __tablename__ = "user_groups"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_user_groups_org_name"),
    )

    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=STATUS_ACTIVE
    )


class UserGroupMembership(IdMixin, Base):
    __tablename__ = "user_group_memberships"
    __table_args__ = (
        UniqueConstraint("group_id", "user_id", name="uq_user_group_membership"),
    )

    group_id: Mapped[str] = mapped_column(ForeignKey("user_groups.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class UserSession(IdMixin, Base):
    __tablename__ = "user_sessions"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    refresh_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class ResourceGrant(IdMixin, TimestampMixin, Base):
    __tablename__ = "resource_grants"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "resource_type",
            "resource_id",
            "principal_type",
            "principal_id",
            name="uq_resource_grant_principal",
        ),
        Index("ix_resource_grants_resource_identity", "resource_type", "resource_id"),
    )

    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(36), nullable=False)
    principal_type: Mapped[str] = mapped_column(String(32), nullable=False)
    principal_id: Mapped[str] = mapped_column(String(36), nullable=False)
    permissions: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class ResourceAllocationPolicy(IdMixin, TimestampMixin, Base):
    __tablename__ = "resource_allocation_policies"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "principal_type",
            "principal_id",
            "resource_pool_id",
            name="uq_resource_allocation_policy_identity",
        ),
        Index(
            "ix_resource_allocation_policies_principal",
            "organization_id",
            "principal_type",
            "principal_id",
        ),
    )

    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    principal_type: Mapped[str] = mapped_column(String(32), nullable=False)
    principal_id: Mapped[str] = mapped_column(String(36), nullable=False)
    resource_pool_id: Mapped[str] = mapped_column(
        ForeignKey("resource_pools.id"), nullable=False
    )
    max_concurrent_training_jobs: Mapped[int | None] = mapped_column(Integer)
    max_gpu_count: Mapped[int | None] = mapped_column(Integer)
    max_service_instances: Mapped[int | None] = mapped_column(Integer)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class AuditLog(IdMixin, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_created_at", "created_at"),)

    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    actor_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(80))
    resource_id: Mapped[str | None] = mapped_column(String(36))
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(128))
    ip_address: Mapped[str | None] = mapped_column(String(45))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
