"""add identity and RBAC foundation

Revision ID: 20260727_0001
Revises: 20260724_0002
Create Date: 2026-07-27 00:01:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260727_0001"
down_revision: str | None = "20260724_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id_column() -> sa.Column:
    return sa.Column("id", sa.String(length=36), nullable=False)


def _created_at_column() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


def _updated_at_column() -> sa.Column:
    return sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


def upgrade() -> None:
    op.create_table(
        "organizations",
        _id_column(),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        _created_at_column(),
        _updated_at_column(),
        sa.PrimaryKeyConstraint("id", name="pk_organizations"),
        sa.UniqueConstraint("slug", name="uq_organizations_slug"),
    )

    op.create_table(
        "users",
        _id_column(),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("username", sa.String(length=120), nullable=False),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("must_change_password", sa.Boolean(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        _created_at_column(),
        _updated_at_column(),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_users_organization_id_organizations",
        ),
        sa.UniqueConstraint(
            "organization_id", "username", name="uq_users_org_username"
        ),
        sa.UniqueConstraint("organization_id", "email", name="uq_users_org_email"),
    )
    op.create_index("ix_users_status", "users", ["status"])

    op.create_table(
        "user_groups",
        _id_column(),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        _created_at_column(),
        _updated_at_column(),
        sa.PrimaryKeyConstraint("id", name="pk_user_groups"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_user_groups_organization_id_organizations",
        ),
        sa.UniqueConstraint("organization_id", "name", name="uq_user_groups_org_name"),
    )

    op.create_table(
        "user_group_memberships",
        _id_column(),
        sa.Column("group_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        _created_at_column(),
        sa.PrimaryKeyConstraint("id", name="pk_user_group_memberships"),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["user_groups.id"],
            name="fk_user_group_memberships_group_id_user_groups",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_user_group_memberships_user_id_users",
        ),
        sa.UniqueConstraint("group_id", "user_id", name="uq_user_group_membership"),
    )

    op.create_table(
        "user_sessions",
        _id_column(),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("refresh_token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        _created_at_column(),
        sa.PrimaryKeyConstraint("id", name="pk_user_sessions"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_user_sessions_user_id_users"
        ),
    )
    op.create_index("ix_user_sessions_expires_at", "user_sessions", ["expires_at"])

    op.create_table(
        "resource_grants",
        _id_column(),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("resource_type", sa.String(length=80), nullable=False),
        sa.Column("resource_id", sa.String(length=36), nullable=False),
        sa.Column("principal_type", sa.String(length=32), nullable=False),
        sa.Column("principal_id", sa.String(length=36), nullable=False),
        sa.Column("permissions", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        _created_at_column(),
        _updated_at_column(),
        sa.PrimaryKeyConstraint("id", name="pk_resource_grants"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_resource_grants_organization_id_organizations",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_resource_grants_created_by_users"
        ),
        sa.UniqueConstraint(
            "organization_id",
            "resource_type",
            "resource_id",
            "principal_type",
            "principal_id",
            name="uq_resource_grant_principal",
        ),
    )
    op.create_index(
        "ix_resource_grants_resource_identity",
        "resource_grants",
        ["resource_type", "resource_id"],
    )

    op.create_table(
        "resource_allocation_policies",
        _id_column(),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("principal_type", sa.String(length=32), nullable=False),
        sa.Column("principal_id", sa.String(length=36), nullable=False),
        sa.Column("resource_pool_id", sa.String(length=36), nullable=False),
        sa.Column("max_concurrent_training_jobs", sa.Integer(), nullable=True),
        sa.Column("max_gpu_count", sa.Integer(), nullable=True),
        sa.Column("max_service_instances", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        _created_at_column(),
        _updated_at_column(),
        sa.PrimaryKeyConstraint("id", name="pk_resource_allocation_policies"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_resource_allocation_policies_org",
        ),
        sa.ForeignKeyConstraint(
            ["resource_pool_id"],
            ["resource_pools.id"],
            name="fk_resource_allocation_policies_pool",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_resource_allocation_policies_creator",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "principal_type",
            "principal_id",
            "resource_pool_id",
            name="uq_resource_allocation_policy_identity",
        ),
    )
    op.create_index(
        "ix_resource_allocation_policies_principal",
        "resource_allocation_policies",
        ["organization_id", "principal_type", "principal_id"],
    )

    op.create_table(
        "audit_logs",
        _id_column(),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("actor_user_id", sa.String(length=36), nullable=True),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("resource_type", sa.String(length=80), nullable=True),
        sa.Column("resource_id", sa.String(length=36), nullable=True),
        sa.Column("result", sa.String(length=32), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        _created_at_column(),
        sa.PrimaryKeyConstraint("id", name="pk_audit_logs"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_audit_logs_organization_id_organizations",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_audit_logs_actor_user_id_users",
        ),
    )
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_index(
        "ix_resource_allocation_policies_principal",
        table_name="resource_allocation_policies",
    )
    op.drop_table("resource_allocation_policies")

    op.drop_index("ix_resource_grants_resource_identity", table_name="resource_grants")
    op.drop_table("resource_grants")

    op.drop_index("ix_user_sessions_expires_at", table_name="user_sessions")
    op.drop_table("user_sessions")

    op.drop_table("user_group_memberships")
    op.drop_table("user_groups")

    op.drop_index("ix_users_status", table_name="users")
    op.drop_table("users")
    op.drop_table("organizations")
