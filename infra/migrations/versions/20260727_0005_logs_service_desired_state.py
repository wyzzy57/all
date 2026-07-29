"""add durable logs and service desired state

Revision ID: 20260727_0005
Revises: 20260727_0004
Create Date: 2026-07-27 00:05:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260727_0005"
down_revision: str | None = "20260727_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id_column() -> sa.Column:
    return sa.Column("id", sa.String(length=36), nullable=False)


def _timestamp_columns() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def upgrade() -> None:
    op.create_table(
        "log_streams",
        _id_column(),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("resource_type", sa.String(length=80), nullable=False),
        sa.Column("resource_id", sa.String(length=36), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="open"),
        sa.Column("encoding", sa.String(length=24), nullable=False, server_default="utf-8"),
        sa.Column("next_sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("line_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("redacted_log_uri", sa.Text(), nullable=True),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamp_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_log_streams"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_log_streams_organization_id_organizations",
        ),
    )
    op.create_index("ix_log_streams_organization_id", "log_streams", ["organization_id"])
    op.create_index("ix_log_streams_source", "log_streams", ["source"])
    op.create_index("ix_log_streams_status", "log_streams", ["status"])
    op.create_index("ix_log_streams_resource", "log_streams", ["resource_type", "resource_id"])
    op.create_index("ix_log_streams_created_at", "log_streams", ["created_at"])
    op.create_index(
        "ix_log_streams_retention_expires_at",
        "log_streams",
        ["retention_expires_at"],
    )

    op.create_table(
        "log_chunks",
        _id_column(),
        sa.Column("stream_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("object_uri", sa.Text(), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("compressed_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("uncompressed_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("byte_start", sa.BigInteger(), nullable=False),
        sa.Column("byte_end", sa.BigInteger(), nullable=False),
        sa.Column("line_count", sa.Integer(), nullable=False),
        sa.Column("first_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_timestamp", sa.DateTime(timezone=True), nullable=True),
        *_timestamp_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_log_chunks"),
        sa.ForeignKeyConstraint(
            ["stream_id"],
            ["log_streams.id"],
            name="fk_log_chunks_stream_id_log_streams",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "stream_id",
            "sequence",
            name="uq_log_chunks_stream_sequence",
        ),
    )
    op.create_index(
        "ix_log_chunks_stream_sequence", "log_chunks", ["stream_id", "sequence"]
    )
    op.create_index(
        "ix_log_chunks_time_range",
        "log_chunks",
        ["first_timestamp", "last_timestamp"],
    )

    with op.batch_alter_table("deployment_services") as batch_op:
        batch_op.add_column(
            sa.Column(
                "desired_state",
                sa.String(length=24),
                nullable=False,
                server_default="running",
            )
        )
        batch_op.add_column(sa.Column("active_revision", sa.Integer(), nullable=True))
    op.create_index(
        "ix_deployment_services_desired_state",
        "deployment_services",
        ["desired_state"],
    )

    with op.batch_alter_table("deployment_instances") as batch_op:
        batch_op.add_column(
            sa.Column(
                "deployment_revision",
                sa.Integer(),
                nullable=False,
                server_default="1",
            )
        )

    connection = op.get_bind()
    connection.execute(
        sa.text(
            "UPDATE deployment_services SET desired_state = CASE "
            "WHEN status = 'stopped' THEN 'stopped' "
            "WHEN status = 'failed' AND EXISTS ("
            "SELECT 1 FROM deployment_instances di "
            "WHERE di.deployment_service_id = deployment_services.id "
            "AND di.status = 'stopped'"
            ") THEN 'stopped' ELSE 'running' END"
        )
    )
    connection.execute(
        sa.text(
            "UPDATE deployment_services SET active_revision = ("
            "SELECT MAX(di.deployment_revision) FROM deployment_instances di "
            "WHERE di.deployment_service_id = deployment_services.id"
            ")"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("deployment_instances") as batch_op:
        batch_op.drop_column("deployment_revision")
    op.drop_index(
        "ix_deployment_services_desired_state",
        table_name="deployment_services",
    )
    with op.batch_alter_table("deployment_services") as batch_op:
        batch_op.drop_column("active_revision")
        batch_op.drop_column("desired_state")

    op.drop_index("ix_log_chunks_time_range", table_name="log_chunks")
    op.drop_index("ix_log_chunks_stream_sequence", table_name="log_chunks")
    op.drop_table("log_chunks")

    op.drop_index("ix_log_streams_retention_expires_at", table_name="log_streams")
    op.drop_index("ix_log_streams_created_at", table_name="log_streams")
    op.drop_index("ix_log_streams_resource", table_name="log_streams")
    op.drop_index("ix_log_streams_status", table_name="log_streams")
    op.drop_index("ix_log_streams_source", table_name="log_streams")
    op.drop_index("ix_log_streams_organization_id", table_name="log_streams")
    op.drop_table("log_streams")
