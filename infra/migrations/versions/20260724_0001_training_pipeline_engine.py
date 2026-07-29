"""add training engine to pipelines

Revision ID: 20260724_0001
Revises: 20260720_0001
Create Date: 2026-07-24 00:01:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260724_0001"
down_revision: str | None = "20260720_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("training_pipelines") as batch_op:
        batch_op.add_column(
            sa.Column(
                "engine",
                sa.String(length=40),
                nullable=False,
                server_default="yolo26",
            )
        )
        batch_op.create_index("ix_training_pipelines_engine", ["engine"])


def downgrade() -> None:
    with op.batch_alter_table("training_pipelines") as batch_op:
        batch_op.drop_index("ix_training_pipelines_engine")
        batch_op.drop_column("engine")
