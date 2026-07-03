"""add dataset sample checksum uniqueness

Revision ID: 20260703_0003
Revises: 20260703_0002
Create Date: 2026-07-03 00:03:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260703_0003"
down_revision: str | None = "20260703_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


DATASET_SAMPLE_CHECKSUM_WHERE = sa.text("checksum IS NOT NULL")


def upgrade() -> None:
    op.create_index(
        "uq_dataset_samples_dataset_checksum",
        "dataset_samples",
        ["dataset_id", "checksum"],
        unique=True,
        postgresql_where=DATASET_SAMPLE_CHECKSUM_WHERE,
        sqlite_where=DATASET_SAMPLE_CHECKSUM_WHERE,
    )


def downgrade() -> None:
    op.drop_index("uq_dataset_samples_dataset_checksum", table_name="dataset_samples")
