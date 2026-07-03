"""add active task resource uniqueness

Revision ID: 20260703_0002
Revises: 20260703_0001
Create Date: 2026-07-03 00:02:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260703_0002"
down_revision: str | None = "20260703_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ACTIVE_TASK_RESOURCE_WHERE = sa.text(
    "status IN ('PENDING', 'QUEUED', 'RUNNING') "
    "AND resource_type IS NOT NULL "
    "AND resource_id IS NOT NULL"
)


def upgrade() -> None:
    op.create_index(
        "uq_tasks_active_resource_command",
        "tasks",
        ["task_type", "resource_type", "resource_id"],
        unique=True,
        postgresql_where=ACTIVE_TASK_RESOURCE_WHERE,
        sqlite_where=ACTIVE_TASK_RESOURCE_WHERE,
    )


def downgrade() -> None:
    op.drop_index("uq_tasks_active_resource_command", table_name="tasks")
