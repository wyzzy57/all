"""add LLM dataset contract

Revision ID: 20260724_0002
Revises: 20260724_0001
Create Date: 2026-07-24 00:02:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260724_0002"
down_revision: str | None = "20260724_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("datasets") as batch_op:
        batch_op.add_column(
            sa.Column("format", sa.String(length=40), nullable=False, server_default="yolo")
        )
        batch_op.add_column(
            sa.Column("schema_config", sa.JSON(), nullable=False, server_default="{}")
        )
        batch_op.add_column(sa.Column("manifest_checksum", sa.String(length=64), nullable=True))
        batch_op.create_index("ix_datasets_format", ["format"])
        batch_op.create_index("ix_datasets_manifest_checksum", ["manifest_checksum"])


def downgrade() -> None:
    with op.batch_alter_table("datasets") as batch_op:
        batch_op.drop_index("ix_datasets_manifest_checksum")
        batch_op.drop_index("ix_datasets_format")
        batch_op.drop_column("manifest_checksum")
        batch_op.drop_column("schema_config")
        batch_op.drop_column("format")
