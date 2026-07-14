"""add deployment instance name

Revision ID: 20260710_0002
Revises: 20260710_0001
Create Date: 2026-07-10 16:10:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260710_0002"
down_revision: str | None = "20260710_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "deployment_services",
        sa.Column("instance_name", sa.String(length=160), nullable=False, server_default="default"),
    )
    op.create_index("ix_deployment_services_instance_name", "deployment_services", ["instance_name"])


def downgrade() -> None:
    op.drop_index("ix_deployment_services_instance_name", table_name="deployment_services")
    op.drop_column("deployment_services", "instance_name")
