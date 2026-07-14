"""add deployment services

Revision ID: 20260710_0001
Revises: 20260709_0001
Create Date: 2026-07-10 15:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260710_0001"
down_revision: str | None = "20260709_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "deployment_services",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("pipeline_id", sa.String(length=36), nullable=False),
        sa.Column("trained_model_id", sa.String(length=36), nullable=True),
        sa.Column("model_name", sa.String(length=160), nullable=False),
        sa.Column("model_weight", sa.String(length=160), nullable=False),
        sa.Column("environment", sa.String(length=120), nullable=False),
        sa.Column("instance_count", sa.Integer(), nullable=False),
        sa.Column("resource_summary", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("calls", sa.BigInteger(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_deployment_services"),
        sa.UniqueConstraint("name", name="uq_deployment_services_name"),
        sa.ForeignKeyConstraint(["pipeline_id"], ["training_pipelines.id"], name="fk_deployment_services_pipeline_id_training_pipelines"),
        sa.ForeignKeyConstraint(["trained_model_id"], ["trained_models.id"], name="fk_deployment_services_trained_model_id_trained_models"),
    )
    op.create_index("ix_deployment_services_pipeline_id", "deployment_services", ["pipeline_id"])
    op.create_index("ix_deployment_services_status", "deployment_services", ["status"])


def downgrade() -> None:
    op.drop_index("ix_deployment_services_status", table_name="deployment_services")
    op.drop_index("ix_deployment_services_pipeline_id", table_name="deployment_services")
    op.drop_table("deployment_services")
