"""add edge compute nodes

Revision ID: 20260715_0001
Revises: 20260710_0002
Create Date: 2026-07-15 00:01:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260715_0001"
down_revision: str | None = "20260710_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "resource_pools",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("selector", sa.JSON(), nullable=False),
        sa.Column("compatibility_policy", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_resource_pools"),
        sa.UniqueConstraint("name", name="uq_resource_pools_name"),
    )
    op.create_index("ix_resource_pools_kind", "resource_pools", ["kind"])

    op.create_table(
        "compute_nodes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("resource_pool_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("architecture", sa.String(length=32), nullable=False),
        sa.Column("platform_kind", sa.String(length=40), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("resources", sa.JSON(), nullable=False),
        sa.Column("fingerprint", sa.JSON(), nullable=False),
        sa.Column("agent_version", sa.String(length=40), nullable=False),
        sa.Column("certificate_serial", sa.String(length=80), nullable=True),
        sa.Column("certificate_fingerprint", sa.String(length=128), nullable=True),
        sa.Column("certificate_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_compute_nodes"),
        sa.ForeignKeyConstraint(["resource_pool_id"], ["resource_pools.id"], name="fk_compute_nodes_resource_pool_id_resource_pools"),
        sa.UniqueConstraint("name", name="uq_compute_nodes_name"),
        sa.UniqueConstraint("certificate_serial", name="uq_compute_nodes_certificate_serial"),
        sa.UniqueConstraint("certificate_fingerprint", name="uq_compute_nodes_certificate_fingerprint"),
    )
    op.create_index("ix_compute_nodes_resource_pool_id", "compute_nodes", ["resource_pool_id"])
    op.create_index("ix_compute_nodes_status", "compute_nodes", ["status"])
    op.create_index("ix_compute_nodes_platform_kind", "compute_nodes", ["platform_kind"])
    op.create_index("ix_compute_nodes_certificate_expires_at", "compute_nodes", ["certificate_expires_at"])
    op.create_index("ix_compute_nodes_last_seen_at", "compute_nodes", ["last_seen_at"])

    op.create_table(
        "agent_enrollment_tokens",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("node_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_agent_enrollment_tokens"),
        sa.ForeignKeyConstraint(["node_id"], ["compute_nodes.id"], name="fk_agent_enrollment_tokens_node_id_compute_nodes"),
        sa.UniqueConstraint("token_hash", name="uq_agent_enrollment_tokens_token_hash"),
    )
    op.create_index("ix_agent_enrollment_tokens_expires_at", "agent_enrollment_tokens", ["expires_at"])

    op.create_table(
        "node_commands",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("command_id", sa.String(length=36), nullable=False),
        sa.Column("node_id", sa.String(length=36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("desired_state", sa.String(length=40), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_node_commands"),
        sa.ForeignKeyConstraint(["node_id"], ["compute_nodes.id"], name="fk_node_commands_node_id_compute_nodes"),
        sa.UniqueConstraint("command_id", name="uq_node_commands_command_id"),
    )
    op.create_index("ix_node_commands_node_id", "node_commands", ["node_id"])
    op.create_index("ix_node_commands_status", "node_commands", ["status"])

    op.create_table(
        "node_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("node_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("command_id", sa.String(length=36), nullable=True),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("stage", sa.String(length=120), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_node_events"),
        sa.ForeignKeyConstraint(["node_id"], ["compute_nodes.id"], name="fk_node_events_node_id_compute_nodes"),
        sa.UniqueConstraint("node_id", "sequence", name="uq_node_events_node_id"),
    )
    op.create_index("ix_node_events_node_id", "node_events", ["node_id"])
    op.create_index("ix_node_events_command_id", "node_events", ["command_id"])
    op.create_index("ix_node_events_event_type", "node_events", ["event_type"])
    op.create_index("ix_node_events_node_sequence", "node_events", ["node_id", "sequence"])


def downgrade() -> None:
    op.drop_index("ix_node_events_node_sequence", table_name="node_events")
    op.drop_index("ix_node_events_event_type", table_name="node_events")
    op.drop_index("ix_node_events_command_id", table_name="node_events")
    op.drop_index("ix_node_events_node_id", table_name="node_events")
    op.drop_table("node_events")

    op.drop_index("ix_node_commands_status", table_name="node_commands")
    op.drop_index("ix_node_commands_node_id", table_name="node_commands")
    op.drop_table("node_commands")

    op.drop_index("ix_agent_enrollment_tokens_expires_at", table_name="agent_enrollment_tokens")
    op.drop_table("agent_enrollment_tokens")

    op.drop_index("ix_compute_nodes_last_seen_at", table_name="compute_nodes")
    op.drop_index("ix_compute_nodes_certificate_expires_at", table_name="compute_nodes")
    op.drop_index("ix_compute_nodes_platform_kind", table_name="compute_nodes")
    op.drop_index("ix_compute_nodes_status", table_name="compute_nodes")
    op.drop_index("ix_compute_nodes_resource_pool_id", table_name="compute_nodes")
    op.drop_table("compute_nodes")

    op.drop_index("ix_resource_pools_kind", table_name="resource_pools")
    op.drop_table("resource_pools")
