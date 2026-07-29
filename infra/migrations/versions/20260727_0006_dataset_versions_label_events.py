"""add immutable dataset versions and label sync events

Revision ID: 20260727_0006
Revises: 20260727_0005
Create Date: 2026-07-27 00:06:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260727_0006"
down_revision: str | None = "20260727_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id_column() -> sa.Column:
    return sa.Column("id", sa.String(length=36), nullable=False)


def _timestamp_columns() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def upgrade() -> None:
    with op.batch_alter_table("datasets") as batch_op:
        batch_op.add_column(
            sa.Column("asset_role", sa.String(length=24), nullable=False, server_default="working")
        )
    op.create_index("ix_datasets_asset_role", "datasets", ["asset_role"])

    op.create_table(
        "dataset_versions",
        _id_column(),
        sa.Column("dataset_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="published"),
        sa.Column("format", sa.String(length=40), nullable=False),
        sa.Column("object_uri", sa.Text(), nullable=False),
        sa.Column("manifest_uri", sa.Text(), nullable=False),
        sa.Column("manifest_checksum", sa.String(length=64), nullable=False),
        sa.Column("source_revision", sa.String(length=160), nullable=True),
        sa.Column("total_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("valid_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("invalid_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("schema_snapshot", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        *_timestamp_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_dataset_versions"),
        sa.ForeignKeyConstraint(
            ["dataset_id"], ["datasets.id"], name="fk_dataset_versions_dataset_id_datasets", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("dataset_id", "version", name="uq_dataset_versions_dataset_version"),
        sa.UniqueConstraint(
            "dataset_id", "manifest_checksum", name="uq_dataset_versions_dataset_manifest_checksum"
        ),
    )
    op.create_index("ix_dataset_versions_dataset_id", "dataset_versions", ["dataset_id"])
    op.create_index("ix_dataset_versions_status", "dataset_versions", ["status"])
    op.create_index("ix_dataset_versions_manifest_checksum", "dataset_versions", ["manifest_checksum"])
    op.create_index("ix_dataset_versions_source_revision", "dataset_versions", ["source_revision"])

    op.create_table(
        "label_sync_events",
        _id_column(),
        sa.Column("label_project_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("event_key", sa.String(length=200), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("payload_uri", sa.Text(), nullable=True),
        sa.Column("payload_checksum", sa.String(length=64), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamp_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_label_sync_events"),
        sa.ForeignKeyConstraint(
            ["label_project_id"],
            ["label_projects.id"],
            name="fk_label_sync_events_label_project_id_label_projects",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("provider", "event_key", name="uq_label_sync_events_provider_event_key"),
    )
    op.create_index("ix_label_sync_events_label_project_id", "label_sync_events", ["label_project_id"])
    op.create_index("ix_label_sync_events_provider", "label_sync_events", ["provider"])
    op.create_index("ix_label_sync_events_event_type", "label_sync_events", ["event_type"])
    op.create_index("ix_label_sync_events_status", "label_sync_events", ["status"])

    connection = op.get_bind()
    connection.execute(
        sa.text("UPDATE datasets SET asset_role = CASE WHEN status = 'validated' THEN 'published' ELSE 'working' END")
    )
    validated = connection.execute(
        sa.text(
            "SELECT id, format, storage_uri, manifest_checksum, sample_count, annotation_count, "
            "class_schema, schema_config, updated_at FROM datasets "
            "WHERE status = 'validated' AND storage_uri IS NOT NULL AND manifest_checksum IS NOT NULL"
        )
    ).mappings()
    import json
    import uuid

    for row in validated:
        snapshot = json.dumps(
            {"class_schema": row["class_schema"] or {}, "schema_config": row["schema_config"] or {}},
            ensure_ascii=True,
            sort_keys=True,
        )
        connection.execute(
            sa.text(
                "INSERT INTO dataset_versions "
                "(id, dataset_id, version, status, format, object_uri, manifest_uri, manifest_checksum, "
                "total_count, valid_count, invalid_count, skipped_count, size_bytes, schema_snapshot, "
                "published_at, created_at, updated_at) VALUES "
                "(:id, :dataset_id, 1, 'published', :format, :object_uri, :manifest_uri, :checksum, "
                ":total, :valid, 0, 0, 0, :snapshot, :published_at, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {
                "id": str(uuid.uuid4()),
                "dataset_id": row["id"],
                "format": row["format"],
                "object_uri": row["storage_uri"],
                "manifest_uri": row["storage_uri"],
                "checksum": row["manifest_checksum"],
                "total": row["sample_count"],
                "valid": row["annotation_count"],
                "snapshot": snapshot,
                "published_at": row["updated_at"],
            },
        )


def downgrade() -> None:
    op.drop_index("ix_label_sync_events_status", table_name="label_sync_events")
    op.drop_index("ix_label_sync_events_event_type", table_name="label_sync_events")
    op.drop_index("ix_label_sync_events_provider", table_name="label_sync_events")
    op.drop_index("ix_label_sync_events_label_project_id", table_name="label_sync_events")
    op.drop_table("label_sync_events")

    op.drop_index("ix_dataset_versions_source_revision", table_name="dataset_versions")
    op.drop_index("ix_dataset_versions_manifest_checksum", table_name="dataset_versions")
    op.drop_index("ix_dataset_versions_status", table_name="dataset_versions")
    op.drop_index("ix_dataset_versions_dataset_id", table_name="dataset_versions")
    op.drop_table("dataset_versions")

    op.drop_index("ix_datasets_asset_role", table_name="datasets")
    with op.batch_alter_table("datasets") as batch_op:
        batch_op.drop_column("asset_role")
