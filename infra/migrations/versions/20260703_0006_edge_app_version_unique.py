"""add edge app version uniqueness

Revision ID: 20260703_0006
Revises: 20260703_0005
Create Date: 2026-07-03 00:06:00.000000
"""

from collections.abc import Sequence

from alembic import op


revision: str = "20260703_0006"
down_revision: str | None = "20260703_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_edge_app_versions_edge_app_id_version",
        "edge_app_versions",
        ["edge_app_id", "version"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_edge_app_versions_edge_app_id_version", table_name="edge_app_versions")
