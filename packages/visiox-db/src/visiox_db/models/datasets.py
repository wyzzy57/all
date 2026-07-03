from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from visiox_db.base import Base, IdMixin, TimestampMixin


class Dataset(IdMixin, TimestampMixin, Base):
    __tablename__ = "datasets"

    name: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    task: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="created", index=True)
    class_schema: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    annotation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source: Mapped[str | None] = mapped_column(String(80))
    storage_uri: Mapped[str | None] = mapped_column(Text)


class DatasetSample(IdMixin, TimestampMixin, Base):
    __tablename__ = "dataset_samples"

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

    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    external_project_id: Mapped[str | None] = mapped_column(String(120))
    sync_status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending", index=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
