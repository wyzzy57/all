"""local compatibility checkpoint

Revision ID: 20260703_0006
Revises: 20260703_0005
Create Date: 2026-07-03 00:06:00.000000
"""

from collections.abc import Sequence


revision: str = "20260703_0006"
down_revision: str | None = "20260703_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
