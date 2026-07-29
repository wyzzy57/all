"""add unified node inventory state

Revision ID: 20260727_0002
Revises: 20260727_0001
Create Date: 2026-07-27 00:02:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260727_0002"
down_revision: str | None = "20260727_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("compute_nodes") as batch_op:
        batch_op.add_column(
            sa.Column(
                "enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )
        batch_op.add_column(
            sa.Column(
                "labels",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "connection_method",
                sa.String(length=16),
                nullable=False,
                server_default="ssh",
            )
        )
        batch_op.add_column(
            sa.Column("inventory_refreshed_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "resource_revision",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )

    op.execute(
        sa.text(
            "UPDATE compute_nodes SET connection_method = "
            "CASE WHEN agent_version LIKE 'ssh-%' THEN 'ssh' ELSE 'agent' END"
        )
    )
    op.create_index("ix_compute_nodes_enabled", "compute_nodes", ["enabled"])
    op.create_index(
        "ix_compute_nodes_inventory_refreshed_at",
        "compute_nodes",
        ["inventory_refreshed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_compute_nodes_inventory_refreshed_at", table_name="compute_nodes")
    op.drop_index("ix_compute_nodes_enabled", table_name="compute_nodes")
    with op.batch_alter_table("compute_nodes") as batch_op:
        batch_op.drop_column("resource_revision")
        batch_op.drop_column("inventory_refreshed_at")
        batch_op.drop_column("connection_method")
        batch_op.drop_column("labels")
        batch_op.drop_column("enabled")
