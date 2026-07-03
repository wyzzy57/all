"""create database core schema

Revision ID: 20260703_0001
Revises:
Create Date: 2026-07-03 00:01:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260703_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def id_column() -> sa.Column[str]:
    return sa.Column("id", sa.String(length=36), nullable=False)


def created_at_column() -> sa.Column[str]:
    return sa.Column("created_at", sa.DateTime(timezone=True), nullable=False)


def updated_at_column() -> sa.Column[str]:
    return sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False)


def pk(table_name: str) -> sa.PrimaryKeyConstraint:
    return sa.PrimaryKeyConstraint("id", name=f"pk_{table_name}")


def upgrade() -> None:
    op.create_table(
        "tasks",
        id_column(),
        sa.Column("task_type", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("resource_type", sa.String(length=80), nullable=True),
        sa.Column("resource_id", sa.String(length=36), nullable=True),
        sa.Column("stage", sa.String(length=120), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        created_at_column(),
        updated_at_column(),
        pk("tasks"),
    )
    op.create_index("ix_tasks_task_type", "tasks", ["task_type"])
    op.create_index("ix_tasks_status", "tasks", ["status"])

    op.create_table(
        "model_sources",
        id_column(),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("type", sa.String(length=40), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=True),
        sa.Column("bucket", sa.String(length=120), nullable=True),
        sa.Column("mount_path", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        created_at_column(),
        updated_at_column(),
        pk("model_sources"),
        sa.UniqueConstraint("name", name="uq_model_sources_name"),
    )

    op.create_table(
        "datasets",
        id_column(),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("task", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("class_schema", sa.JSON(), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("annotation_count", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=True),
        sa.Column("storage_uri", sa.Text(), nullable=True),
        created_at_column(),
        updated_at_column(),
        pk("datasets"),
        sa.UniqueConstraint("name", name="uq_datasets_name"),
    )
    op.create_index("ix_datasets_task", "datasets", ["task"])
    op.create_index("ix_datasets_status", "datasets", ["status"])

    op.create_table(
        "devices",
        id_column(),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("endpoint_url", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("token_ref", sa.String(length=160), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resource_info", sa.JSON(), nullable=False),
        created_at_column(),
        updated_at_column(),
        pk("devices"),
        sa.UniqueConstraint("name", name="uq_devices_name"),
    )
    op.create_index("ix_devices_status", "devices", ["status"])

    op.create_table(
        "edge_apps",
        id_column(),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        created_at_column(),
        updated_at_column(),
        pk("edge_apps"),
        sa.UniqueConstraint("name", name="uq_edge_apps_name"),
    )
    op.create_index("ix_edge_apps_status", "edge_apps", ["status"])

    op.create_table(
        "base_models",
        id_column(),
        sa.Column("family", sa.String(length=80), nullable=False),
        sa.Column("task", sa.String(length=40), nullable=False),
        sa.Column("scale", sa.String(length=8), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("local_uri", sa.Text(), nullable=True),
        sa.Column("checksum", sa.String(length=128), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("model_source_id", sa.String(length=36), nullable=True),
        created_at_column(),
        updated_at_column(),
        pk("base_models"),
        sa.ForeignKeyConstraint(["model_source_id"], ["model_sources.id"], name="fk_base_models_model_source_id_model_sources"),
        sa.UniqueConstraint("family", "task", "scale", name="uq_base_models_family"),
        sa.UniqueConstraint("filename", name="uq_base_models_filename"),
    )
    op.create_index("ix_base_models_task", "base_models", ["task"])
    op.create_index("ix_base_models_status", "base_models", ["status"])

    op.create_table(
        "dataset_samples",
        id_column(),
        sa.Column("dataset_id", sa.String(length=36), nullable=False),
        sa.Column("file_uri", sa.Text(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("checksum", sa.String(length=128), nullable=True),
        sa.Column("split", sa.String(length=24), nullable=True),
        sa.Column("annotation_status", sa.String(length=40), nullable=False),
        created_at_column(),
        updated_at_column(),
        pk("dataset_samples"),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], name="fk_dataset_samples_dataset_id_datasets"),
    )
    op.create_index("ix_dataset_samples_dataset_id", "dataset_samples", ["dataset_id"])
    op.create_index("ix_dataset_samples_split", "dataset_samples", ["split"])
    op.create_index("ix_dataset_samples_annotation_status", "dataset_samples", ["annotation_status"])

    op.create_table(
        "label_projects",
        id_column(),
        sa.Column("dataset_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("external_project_id", sa.String(length=120), nullable=True),
        sa.Column("sync_status", sa.String(length=40), nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        created_at_column(),
        updated_at_column(),
        pk("label_projects"),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], name="fk_label_projects_dataset_id_datasets"),
    )
    op.create_index("ix_label_projects_dataset_id", "label_projects", ["dataset_id"])
    op.create_index("ix_label_projects_sync_status", "label_projects", ["sync_status"])

    op.create_table(
        "training_pipelines",
        id_column(),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("task", sa.String(length=40), nullable=False),
        sa.Column("scale", sa.String(length=8), nullable=False),
        sa.Column("base_model_id", sa.String(length=36), nullable=True),
        sa.Column("dataset_id", sa.String(length=36), nullable=True),
        sa.Column("params_template", sa.JSON(), nullable=False),
        sa.Column("default_environment", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        created_at_column(),
        updated_at_column(),
        pk("training_pipelines"),
        sa.ForeignKeyConstraint(["base_model_id"], ["base_models.id"], name="fk_training_pipelines_base_model_id_base_models"),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], name="fk_training_pipelines_dataset_id_datasets"),
        sa.UniqueConstraint("name", name="uq_training_pipelines_name"),
    )
    op.create_index("ix_training_pipelines_task", "training_pipelines", ["task"])
    op.create_index("ix_training_pipelines_status", "training_pipelines", ["status"])

    op.create_table(
        "annotations",
        id_column(),
        sa.Column("dataset_sample_id", sa.String(length=36), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("raw_payload_uri", sa.Text(), nullable=True),
        sa.Column("internal_payload", sa.JSON(), nullable=False),
        sa.Column("validation_status", sa.String(length=40), nullable=False),
        created_at_column(),
        updated_at_column(),
        pk("annotations"),
        sa.ForeignKeyConstraint(["dataset_sample_id"], ["dataset_samples.id"], name="fk_annotations_dataset_sample_id_dataset_samples"),
    )
    op.create_index("ix_annotations_dataset_sample_id", "annotations", ["dataset_sample_id"])
    op.create_index("ix_annotations_validation_status", "annotations", ["validation_status"])

    op.create_table(
        "cameras",
        id_column(),
        sa.Column("device_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("rtsp_url", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("last_snapshot_uri", sa.Text(), nullable=True),
        created_at_column(),
        updated_at_column(),
        pk("cameras"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], name="fk_cameras_device_id_devices"),
    )
    op.create_index("ix_cameras_device_id", "cameras", ["device_id"])
    op.create_index("ix_cameras_status", "cameras", ["status"])

    op.create_table(
        "training_jobs",
        id_column(),
        sa.Column("pipeline_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("trained_model_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("log_uri", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        created_at_column(),
        updated_at_column(),
        pk("training_jobs"),
        sa.ForeignKeyConstraint(["pipeline_id"], ["training_pipelines.id"], name="fk_training_jobs_pipeline_id_training_pipelines"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], name="fk_training_jobs_task_id_tasks"),
        sa.ForeignKeyConstraint(["trained_model_id"], ["trained_models.id"], name="fk_training_jobs_trained_model_id_trained_models"),
    )
    op.create_index("ix_training_jobs_status", "training_jobs", ["status"])

    op.create_table(
        "trained_models",
        id_column(),
        sa.Column("pipeline_id", sa.String(length=36), nullable=True),
        sa.Column("training_job_id", sa.String(length=36), nullable=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("version", sa.String(length=80), nullable=False),
        sa.Column("task", sa.String(length=40), nullable=False),
        sa.Column("artifact_uri", sa.Text(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        created_at_column(),
        updated_at_column(),
        pk("trained_models"),
        sa.ForeignKeyConstraint(["pipeline_id"], ["training_pipelines.id"], name="fk_trained_models_pipeline_id_training_pipelines"),
        sa.ForeignKeyConstraint(["training_job_id"], ["training_jobs.id"], name="fk_trained_models_training_job_id_training_jobs"),
    )
    op.create_index("ix_trained_models_task", "trained_models", ["task"])
    op.create_index("ix_trained_models_status", "trained_models", ["status"])
    op.create_table(
        "edge_app_versions",
        id_column(),
        sa.Column("edge_app_id", sa.String(length=36), nullable=False),
        sa.Column("trained_model_id", sa.String(length=36), nullable=True),
        sa.Column("version", sa.String(length=80), nullable=False),
        sa.Column("package_uri", sa.Text(), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("checksum", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        created_at_column(),
        updated_at_column(),
        pk("edge_app_versions"),
        sa.ForeignKeyConstraint(["edge_app_id"], ["edge_apps.id"], name="fk_edge_app_versions_edge_app_id_edge_apps"),
        sa.ForeignKeyConstraint(["trained_model_id"], ["trained_models.id"], name="fk_edge_app_versions_trained_model_id_trained_models"),
    )
    op.create_index("ix_edge_app_versions_edge_app_id", "edge_app_versions", ["edge_app_id"])
    op.create_index("ix_edge_app_versions_status", "edge_app_versions", ["status"])

    op.create_table(
        "deployments",
        id_column(),
        sa.Column("device_id", sa.String(length=36), nullable=False),
        sa.Column("edge_app_version_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("deployed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("logs_uri", sa.Text(), nullable=True),
        created_at_column(),
        updated_at_column(),
        pk("deployments"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], name="fk_deployments_device_id_devices"),
        sa.ForeignKeyConstraint(
            ["edge_app_version_id"],
            ["edge_app_versions.id"],
            name="fk_deployments_edge_app_version_id_edge_app_versions",
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], name="fk_deployments_task_id_tasks"),
    )
    op.create_index("ix_deployments_device_id", "deployments", ["device_id"])
    op.create_index("ix_deployments_status", "deployments", ["status"])
    op.create_index("ix_deployments_active", "deployments", ["active"])


def downgrade() -> None:
    op.drop_table("deployments")
    op.drop_table("edge_app_versions")
    op.drop_table("trained_models")
    op.drop_table("training_jobs")
    op.drop_table("cameras")
    op.drop_table("annotations")
    op.drop_table("training_pipelines")
    op.drop_table("label_projects")
    op.drop_table("dataset_samples")
    op.drop_table("base_models")
    op.drop_table("edge_apps")
    op.drop_table("devices")
    op.drop_table("datasets")
    op.drop_table("model_sources")
    op.drop_table("tasks")
