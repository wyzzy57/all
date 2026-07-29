"""add nullable resource ownership

Revision ID: 20260727_0003
Revises: 20260727_0002
Create Date: 2026-07-27 00:03:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260727_0003"
down_revision: str | None = "20260727_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RESOURCE_TABLES = (
    "datasets",
    "training_pipelines",
    "training_jobs",
    "trained_models",
    "deployment_services",
    "resource_pools",
    "compute_nodes",
)


def upgrade() -> None:
    for table in RESOURCE_TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column("organization_id", sa.String(length=36), nullable=True))
            batch_op.add_column(sa.Column("owner_user_id", sa.String(length=36), nullable=True))
            batch_op.add_column(
                sa.Column(
                    "visibility",
                    sa.String(length=24),
                    nullable=False,
                    server_default="private",
                )
            )
            batch_op.create_foreign_key(
                f"fk_{table}_organization_id",
                "organizations",
                ["organization_id"],
                ["id"],
            )
            batch_op.create_foreign_key(
                f"fk_{table}_owner_user_id",
                "users",
                ["owner_user_id"],
                ["id"],
            )
        op.create_index(f"ix_{table}_organization_id", table, ["organization_id"])
        op.create_index(f"ix_{table}_owner_user_id", table, ["owner_user_id"])
        op.create_index(f"ix_{table}_visibility", table, ["visibility"])


def downgrade() -> None:
    for table in reversed(RESOURCE_TABLES):
        op.drop_index(f"ix_{table}_visibility", table_name=table)
        op.drop_index(f"ix_{table}_owner_user_id", table_name=table)
        op.drop_index(f"ix_{table}_organization_id", table_name=table)
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_constraint(f"fk_{table}_owner_user_id", type_="foreignkey")
            batch_op.drop_constraint(f"fk_{table}_organization_id", type_="foreignkey")
            batch_op.drop_column("visibility")
            batch_op.drop_column("owner_user_id")
            batch_op.drop_column("organization_id")
