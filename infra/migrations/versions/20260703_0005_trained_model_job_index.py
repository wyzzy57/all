"""add trained model training job uniqueness

Revision ID: 20260703_0005
Revises: 20260703_0004
Create Date: 2026-07-03 00:05:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260703_0005"
down_revision: str | None = "20260703_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TRAINED_MODEL_JOB_WHERE = sa.text("training_job_id IS NOT NULL")


def upgrade() -> None:
    op.create_index(
        "uq_trained_models_training_job",
        "trained_models",
        ["training_job_id"],
        unique=True,
        postgresql_where=TRAINED_MODEL_JOB_WHERE,
        sqlite_where=TRAINED_MODEL_JOB_WHERE,
    )


def downgrade() -> None:
    op.drop_index("uq_trained_models_training_job", table_name="trained_models")
