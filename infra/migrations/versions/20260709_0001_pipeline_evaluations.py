"""add pipeline evaluation history

Revision ID: 20260709_0001
Revises: 20260708_0001
Create Date: 2026-07-09 18:20:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260709_0001"
down_revision: str | None = "20260708_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def id_column() -> sa.Column:
    return sa.Column("id", sa.String(length=36), nullable=False)


def created_at_column() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def updated_at_column() -> sa.Column:
    return sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def upgrade() -> None:
    op.create_table(
        "pipeline_evaluations",
        id_column(),
        sa.Column("pipeline_id", sa.String(length=36), nullable=False),
        sa.Column("dataset_id", sa.String(length=36), nullable=False),
        sa.Column("evaluation_set", sa.String(length=40), nullable=False),
        sa.Column("model_weight", sa.String(length=160), nullable=False),
        sa.Column("environment", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=False),
        created_at_column(),
        updated_at_column(),
        sa.PrimaryKeyConstraint("id", name="pk_pipeline_evaluations"),
        sa.ForeignKeyConstraint(["pipeline_id"], ["training_pipelines.id"], name="fk_pipeline_evaluations_pipeline_id_training_pipelines"),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], name="fk_pipeline_evaluations_dataset_id_datasets"),
    )
    op.create_index("ix_pipeline_evaluations_pipeline_id", "pipeline_evaluations", ["pipeline_id"])
    op.create_index("ix_pipeline_evaluations_status", "pipeline_evaluations", ["status"])


def downgrade() -> None:
    op.drop_index("ix_pipeline_evaluations_status", table_name="pipeline_evaluations")
    op.drop_index("ix_pipeline_evaluations_pipeline_id", table_name="pipeline_evaluations")
    op.drop_table("pipeline_evaluations")
