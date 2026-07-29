from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from visiox_db.base import Base, IdMixin, TimestampMixin


class Dataset(IdMixin, TimestampMixin, Base):
    __tablename__ = "datasets"

    name: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    organization_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.id"), index=True)
    owner_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)
    visibility: Mapped[str] = mapped_column(String(24), nullable=False, default="private", index=True)
    asset_role: Mapped[str] = mapped_column(String(24), nullable=False, default="working", index=True)
    task: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="created", index=True)
    format: Mapped[str] = mapped_column(String(40), nullable=False, default="yolo", index=True)
    class_schema: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    schema_config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    manifest_checksum: Mapped[str | None] = mapped_column(String(64), index=True)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    annotation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source: Mapped[str | None] = mapped_column(String(80))
    storage_uri: Mapped[str | None] = mapped_column(Text)


class DatasetVersion(IdMixin, TimestampMixin, Base):
    __tablename__ = "dataset_versions"
    __table_args__ = (
        UniqueConstraint("dataset_id", "version", name="uq_dataset_versions_dataset_version"),
        UniqueConstraint(
            "dataset_id",
            "manifest_checksum",
            name="uq_dataset_versions_dataset_manifest_checksum",
        ),
    )

    dataset_id: Mapped[str] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="published", index=True)
    format: Mapped[str] = mapped_column(String(40), nullable=False)
    object_uri: Mapped[str] = mapped_column(Text, nullable=False)
    manifest_uri: Mapped[str] = mapped_column(Text, nullable=False)
    manifest_checksum: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_revision: Mapped[str | None] = mapped_column(String(160), index=True)
    total_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    valid_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    invalid_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    schema_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LabelSyncEvent(IdMixin, TimestampMixin, Base):
    __tablename__ = "label_sync_events"
    __table_args__ = (
        UniqueConstraint("provider", "event_key", name="uq_label_sync_events_provider_event_key"),
    )

    label_project_id: Mapped[str] = mapped_column(
        ForeignKey("label_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    event_key: Mapped[str] = mapped_column(String(200), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    payload_uri: Mapped[str | None] = mapped_column(Text)
    payload_checksum: Mapped[str | None] = mapped_column(String(64))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DatasetSample(IdMixin, TimestampMixin, Base):
    __tablename__ = "dataset_samples"
    __table_args__ = (
        Index(
            "uq_dataset_samples_dataset_checksum",
            "dataset_id",
            "checksum",
            unique=True,
            postgresql_where=text("checksum IS NOT NULL"),
            sqlite_where=text("checksum IS NOT NULL"),
        ),
    )

    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"), nullable=False, index=True)
    file_uri: Mapped[str] = mapped_column(Text, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    checksum: Mapped[str | None] = mapped_column(String(128))
    split: Mapped[str | None] = mapped_column(String(24), index=True)
    annotation_status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending", index=True)


class Annotation(IdMixin, TimestampMixin, Base):
    __tablename__ = "annotations"

    dataset_sample_id: Mapped[str] = mapped_column(ForeignKey("dataset_samples.id"), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    raw_payload_uri: Mapped[str | None] = mapped_column(Text)
    internal_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    validation_status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending", index=True)


class LabelProject(IdMixin, TimestampMixin, Base):
    __tablename__ = "label_projects"
    __table_args__ = (
        Index(
            "uq_label_projects_dataset_provider",
            "dataset_id",
            "provider",
            unique=True,
        ),
    )

    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    external_project_id: Mapped[str | None] = mapped_column(String(120))
    sync_status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending", index=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
