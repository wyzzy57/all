"""enforce non-null resource ownership

Revision ID: 20260727_0004
Revises: 20260727_0003
Create Date: 2026-07-27 00:04:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260727_0004"
down_revision: str | None = "20260727_0003"
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
    connection = op.get_bind()
    unowned = {
        table: int(
            connection.execute(
                sa.text(
                    f"SELECT COUNT(*) FROM {table} "
                    "WHERE organization_id IS NULL OR owner_user_id IS NULL"
                )
            ).scalar_one()
        )
        for table in RESOURCE_TABLES
    }
    remaining = {table: count for table, count in unowned.items() if count}
    if remaining:
        details = ", ".join(f"{table}={count}" for table, count in remaining.items())
        raise RuntimeError(
            "Resource ownership backfill is required before migration "
            f"20260727_0004: {details}"
        )

    for table in RESOURCE_TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column(
                "organization_id",
                existing_type=sa.String(length=36),
                nullable=False,
            )
            batch_op.alter_column(
                "owner_user_id",
                existing_type=sa.String(length=36),
                nullable=False,
            )


def downgrade() -> None:
    for table in reversed(RESOURCE_TABLES):
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column(
                "owner_user_id",
                existing_type=sa.String(length=36),
                nullable=True,
            )
            batch_op.alter_column(
                "organization_id",
                existing_type=sa.String(length=36),
                nullable=True,
            )
