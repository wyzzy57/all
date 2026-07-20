"""add ssh edge runtime persistence

Revision ID: 20260720_0001
Revises: 20260717_0001
Create Date: 2026-07-20 00:01:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260720_0001"
down_revision: str | None = "20260717_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id_column() -> sa.Column:
    return sa.Column("id", sa.String(length=36), nullable=False)


def _created_at_column() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def _updated_at_column() -> sa.Column:
    return sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def upgrade() -> None:
    op.create_table(
        "edge_ssh_credentials",
        _id_column(),
        sa.Column("node_id", sa.String(length=36), nullable=False),
        sa.Column("ssh_host", sa.String(length=255), nullable=False),
        sa.Column("ssh_port", sa.Integer(), nullable=False),
        sa.Column("ssh_user", sa.String(length=64), nullable=False),
        sa.Column("host_key_type", sa.String(length=40), nullable=False),
        sa.Column("host_key_fingerprint", sa.String(length=128), nullable=False),
        sa.Column("public_key", sa.Text(), nullable=False),
        sa.Column("encrypted_private_key", sa.LargeBinary(), nullable=False),
        sa.Column("encryption_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        _created_at_column(),
        _updated_at_column(),
        sa.PrimaryKeyConstraint("id", name="pk_edge_ssh_credentials"),
        sa.ForeignKeyConstraint(["node_id"], ["compute_nodes.id"], name="fk_edge_ssh_credentials_node_id_compute_nodes"),
        sa.UniqueConstraint("node_id", name="uq_edge_ssh_credentials_node_id"),
        sa.UniqueConstraint(
            "ssh_host",
            "ssh_port",
            name="uq_edge_ssh_credentials_host_port",
        ),
    )

    op.create_table(
        "remote_executions",
        _id_column(),
        sa.Column("node_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("deployment_service_id", sa.String(length=36), nullable=True),
        sa.Column("training_job_id", sa.String(length=36), nullable=True),
        sa.Column("resource_type", sa.String(length=80), nullable=True),
        sa.Column("resource_id", sa.String(length=36), nullable=True),
        sa.Column("operation", sa.String(length=80), nullable=False),
        sa.Column("phase", sa.String(length=80), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("redacted_log_uri", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        _created_at_column(),
        _updated_at_column(),
        sa.PrimaryKeyConstraint("id", name="pk_remote_executions"),
        sa.ForeignKeyConstraint(["node_id"], ["compute_nodes.id"], name="fk_remote_executions_node_id_compute_nodes"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], name="fk_remote_executions_task_id_tasks"),
        sa.ForeignKeyConstraint(
            ["deployment_service_id"],
            ["deployment_services.id"],
            name="fk_remote_executions_service",
        ),
        sa.ForeignKeyConstraint(
            ["training_job_id"],
            ["training_jobs.id"],
            name="fk_remote_executions_training_job",
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_remote_executions_idempotency_key"),
    )
    op.create_index("ix_remote_executions_node_id", "remote_executions", ["node_id"])
    op.create_index("ix_remote_executions_task_id", "remote_executions", ["task_id"])
    op.create_index("ix_remote_executions_deployment_service_id", "remote_executions", ["deployment_service_id"])
    op.create_index("ix_remote_executions_training_job_id", "remote_executions", ["training_job_id"])
    op.create_index("ix_remote_executions_resource_id", "remote_executions", ["resource_id"])
    op.create_index("ix_remote_executions_operation", "remote_executions", ["operation"])
    op.create_index("ix_remote_executions_status", "remote_executions", ["status"])

    op.create_table(
        "deployment_instances",
        _id_column(),
        sa.Column("deployment_service_id", sa.String(length=36), nullable=False),
        sa.Column("node_id", sa.String(length=36), nullable=False),
        sa.Column("instance_name", sa.String(length=160), nullable=False),
        sa.Column("container_id", sa.String(length=128), nullable=True),
        sa.Column("image_digest", sa.String(length=255), nullable=True),
        sa.Column("model_checksum", sa.String(length=128), nullable=True),
        sa.Column("engine", sa.String(length=80), nullable=False),
        sa.Column("engine_digest", sa.String(length=255), nullable=True),
        sa.Column("port", sa.Integer(), nullable=True),
        sa.Column("endpoint", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("health_status", sa.String(length=32), nullable=True),
        sa.Column("health_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rollback_metadata", sa.JSON(), nullable=False),
        _created_at_column(),
        _updated_at_column(),
        sa.PrimaryKeyConstraint("id", name="pk_deployment_instances"),
        sa.ForeignKeyConstraint(
            ["deployment_service_id"],
            ["deployment_services.id"],
            name="fk_deployment_instances_service",
        ),
        sa.ForeignKeyConstraint(["node_id"], ["compute_nodes.id"], name="fk_deployment_instances_node_id_compute_nodes"),
        sa.UniqueConstraint("container_id", name="uq_deployment_instances_container_id"),
        sa.UniqueConstraint(
            "deployment_service_id",
            "node_id",
            "instance_name",
            name="uq_deployment_instances_deployment_service_id",
        ),
    )
    op.create_index("ix_deployment_instances_deployment_service_id", "deployment_instances", ["deployment_service_id"])
    op.create_index("ix_deployment_instances_node_id", "deployment_instances", ["node_id"])
    op.create_index("ix_deployment_instances_status", "deployment_instances", ["status"])

    op.create_table(
        "distributed_training_runs",
        _id_column(),
        sa.Column("training_job_id", sa.String(length=36), nullable=False),
        sa.Column("resource_pool_id", sa.String(length=36), nullable=False),
        sa.Column("node_ids", sa.JSON(), nullable=False),
        sa.Column("ranks", sa.JSON(), nullable=False),
        sa.Column("master_addr", sa.String(length=255), nullable=False),
        sa.Column("master_port", sa.Integer(), nullable=False),
        sa.Column("world_size", sa.Integer(), nullable=False),
        sa.Column("rendezvous_backend", sa.String(length=40), nullable=False),
        sa.Column("training_image_digest", sa.String(length=255), nullable=True),
        sa.Column("container_ids", sa.JSON(), nullable=False),
        sa.Column("checkpoint_uri", sa.Text(), nullable=True),
        sa.Column("checkpoint_checksum", sa.String(length=128), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        _created_at_column(),
        _updated_at_column(),
        sa.PrimaryKeyConstraint("id", name="pk_distributed_training_runs"),
        sa.ForeignKeyConstraint(
            ["training_job_id"],
            ["training_jobs.id"],
            name="fk_distributed_runs_training_job",
        ),
        sa.ForeignKeyConstraint(
            ["resource_pool_id"],
            ["resource_pools.id"],
            name="fk_distributed_runs_resource_pool",
        ),
    )
    op.create_index("ix_distributed_training_runs_training_job_id", "distributed_training_runs", ["training_job_id"])
    op.create_index("ix_distributed_training_runs_resource_pool_id", "distributed_training_runs", ["resource_pool_id"])
    op.create_index("ix_distributed_training_runs_status", "distributed_training_runs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_distributed_training_runs_status", table_name="distributed_training_runs")
    op.drop_index("ix_distributed_training_runs_resource_pool_id", table_name="distributed_training_runs")
    op.drop_index("ix_distributed_training_runs_training_job_id", table_name="distributed_training_runs")
    op.drop_table("distributed_training_runs")

    op.drop_index("ix_deployment_instances_status", table_name="deployment_instances")
    op.drop_index("ix_deployment_instances_node_id", table_name="deployment_instances")
    op.drop_index("ix_deployment_instances_deployment_service_id", table_name="deployment_instances")
    op.drop_table("deployment_instances")

    op.drop_index("ix_remote_executions_status", table_name="remote_executions")
    op.drop_index("ix_remote_executions_operation", table_name="remote_executions")
    op.drop_index("ix_remote_executions_resource_id", table_name="remote_executions")
    op.drop_index("ix_remote_executions_training_job_id", table_name="remote_executions")
    op.drop_index("ix_remote_executions_deployment_service_id", table_name="remote_executions")
    op.drop_index("ix_remote_executions_task_id", table_name="remote_executions")
    op.drop_index("ix_remote_executions_node_id", table_name="remote_executions")
    op.drop_table("remote_executions")

    op.drop_table("edge_ssh_credentials")
