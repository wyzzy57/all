"""add response safe agent identity state

Revision ID: 20260717_0001
Revises: 20260715_0001
Create Date: 2026-07-17 00:01:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260717_0001"
down_revision: str | None = "20260715_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_enrollment_tokens") as batch_op:
        batch_op.add_column(sa.Column("enrollment_request_id", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("enrollment_csr_fingerprint", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("enrollment_node_name", sa.String(length=160), nullable=True))
        batch_op.add_column(sa.Column("enrollment_architecture", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("enrollment_platform_kind", sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column("enrollment_agent_version", sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column("enrollment_certificate_pem", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("enrollment_ca_certificate_pem", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("enrollment_gateway_url", sa.String(length=2048), nullable=True))
        batch_op.add_column(sa.Column("enrollment_heartbeat_interval_seconds", sa.Integer(), nullable=True))
        batch_op.create_unique_constraint(
            "uq_agent_enrollment_tokens_enrollment_request_id", ["enrollment_request_id"]
        )

    with op.batch_alter_table("compute_nodes") as batch_op:
        batch_op.add_column(sa.Column("pending_certificate_serial", sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column("pending_certificate_fingerprint", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("pending_certificate_expires_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("pending_certificate_pem", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("pending_renewal_request_id", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("pending_renewal_csr_fingerprint", sa.String(length=64), nullable=True))
        batch_op.create_unique_constraint(
            "uq_compute_nodes_pending_certificate_serial", ["pending_certificate_serial"]
        )
        batch_op.create_unique_constraint(
            "uq_compute_nodes_pending_certificate_fingerprint", ["pending_certificate_fingerprint"]
        )
        batch_op.create_unique_constraint(
            "uq_compute_nodes_pending_renewal_request_id", ["pending_renewal_request_id"]
        )
    op.create_index(
        "ix_compute_nodes_pending_certificate_expires_at",
        "compute_nodes",
        ["pending_certificate_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_compute_nodes_pending_certificate_expires_at", table_name="compute_nodes")
    with op.batch_alter_table("compute_nodes") as batch_op:
        batch_op.drop_constraint("uq_compute_nodes_pending_renewal_request_id", type_="unique")
        batch_op.drop_constraint("uq_compute_nodes_pending_certificate_fingerprint", type_="unique")
        batch_op.drop_constraint("uq_compute_nodes_pending_certificate_serial", type_="unique")
        batch_op.drop_column("pending_renewal_csr_fingerprint")
        batch_op.drop_column("pending_renewal_request_id")
        batch_op.drop_column("pending_certificate_pem")
        batch_op.drop_column("pending_certificate_expires_at")
        batch_op.drop_column("pending_certificate_fingerprint")
        batch_op.drop_column("pending_certificate_serial")

    with op.batch_alter_table("agent_enrollment_tokens") as batch_op:
        batch_op.drop_constraint("uq_agent_enrollment_tokens_enrollment_request_id", type_="unique")
        batch_op.drop_column("enrollment_heartbeat_interval_seconds")
        batch_op.drop_column("enrollment_gateway_url")
        batch_op.drop_column("enrollment_ca_certificate_pem")
        batch_op.drop_column("enrollment_certificate_pem")
        batch_op.drop_column("enrollment_agent_version")
        batch_op.drop_column("enrollment_platform_kind")
        batch_op.drop_column("enrollment_architecture")
        batch_op.drop_column("enrollment_node_name")
        batch_op.drop_column("enrollment_csr_fingerprint")
        batch_op.drop_column("enrollment_request_id")
