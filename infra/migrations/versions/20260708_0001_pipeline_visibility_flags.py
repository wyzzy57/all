"""add pipeline visibility flags

Revision ID: 20260708_0001
Revises: 20260703_0006
Create Date: 2026-07-08 09:45:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260708_0001"
down_revision: str | None = "20260703_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("training_pipelines", sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("training_pipelines", sa.Column("public_scope", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.add_column("training_pipelines", sa.Column("is_favorite", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("training_pipelines", "is_favorite")
    op.drop_column("training_pipelines", "public_scope")
    op.drop_column("training_pipelines", "is_public")
