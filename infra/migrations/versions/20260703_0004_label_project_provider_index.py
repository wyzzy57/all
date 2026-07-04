"""add label project provider uniqueness

Revision ID: 20260703_0004
Revises: 20260703_0003
Create Date: 2026-07-03 00:04:00.000000
"""

from collections.abc import Sequence

from alembic import op


revision: str = "20260703_0004"
down_revision: str | None = "20260703_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_label_projects_dataset_provider",
        "label_projects",
        ["dataset_id", "provider"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_label_projects_dataset_provider", table_name="label_projects")
