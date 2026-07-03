from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from visiox_db.base import Base, IdMixin, TimestampMixin


class ModelSource(IdMixin, TimestampMixin, Base):
    __tablename__ = "model_sources"

    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    type: Mapped[str] = mapped_column(String(40), nullable=False)
    base_url: Mapped[str | None] = mapped_column(Text)
    bucket: Mapped[str | None] = mapped_column(String(120))
    mount_path: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class BaseModel(IdMixin, TimestampMixin, Base):
    __tablename__ = "base_models"
    __table_args__ = (UniqueConstraint("family", "task", "scale"),)

    family: Mapped[str] = mapped_column(String(80), nullable=False)
    task: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    scale: Mapped[str] = mapped_column(String(8), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    local_uri: Mapped[str | None] = mapped_column(Text)
    checksum: Mapped[str | None] = mapped_column(String(128))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="remote_available", index=True)
    model_source_id: Mapped[str | None] = mapped_column(ForeignKey("model_sources.id"))


class TrainingPipeline(IdMixin, TimestampMixin, Base):
    __tablename__ = "training_pipelines"

    name: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    task: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    scale: Mapped[str] = mapped_column(String(8), nullable=False)
    base_model_id: Mapped[str | None] = mapped_column(ForeignKey("base_models.id"))
    dataset_id: Mapped[str | None] = mapped_column(ForeignKey("datasets.id"))
    params_template: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    default_environment: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="draft", index=True)


class TrainingJob(IdMixin, TimestampMixin, Base):
    __tablename__ = "training_jobs"

    pipeline_id: Mapped[str] = mapped_column(ForeignKey("training_pipelines.id"), nullable=False)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    trained_model_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending", index=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    log_uri: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TrainedModel(IdMixin, TimestampMixin, Base):
    __tablename__ = "trained_models"

    pipeline_id: Mapped[str | None] = mapped_column(ForeignKey("training_pipelines.id"))
    training_job_id: Mapped[str | None] = mapped_column(ForeignKey("training_jobs.id"))
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[str] = mapped_column(String(80), nullable=False)
    task: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    artifact_uri: Mapped[str] = mapped_column(Text, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="created", index=True)
