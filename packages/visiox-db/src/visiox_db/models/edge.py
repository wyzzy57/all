from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from visiox_db.base import Base, IdMixin, TimestampMixin


class Device(IdMixin, TimestampMixin, Base):
    __tablename__ = "devices"

    name: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    endpoint_url: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="offline", index=True)
    token_ref: Mapped[str | None] = mapped_column(String(160))
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resource_info: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class Camera(IdMixin, TimestampMixin, Base):
    __tablename__ = "cameras"

    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    rtsp_url: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="inactive", index=True)
    last_snapshot_uri: Mapped[str | None] = mapped_column(Text)


class EdgeApp(IdMixin, TimestampMixin, Base):
    __tablename__ = "edge_apps"

    name: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="draft", index=True)


class EdgeAppVersion(IdMixin, TimestampMixin, Base):
    __tablename__ = "edge_app_versions"
    __table_args__ = (Index("uq_edge_app_versions_edge_app_id_version", "edge_app_id", "version", unique=True),)

    edge_app_id: Mapped[str] = mapped_column(ForeignKey("edge_apps.id"), nullable=False, index=True)
    trained_model_id: Mapped[str | None] = mapped_column(ForeignKey("trained_models.id"))
    version: Mapped[str] = mapped_column(String(80), nullable=False)
    package_uri: Mapped[str] = mapped_column(Text, nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    checksum: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="created", index=True)


class Deployment(IdMixin, TimestampMixin, Base):
    __tablename__ = "deployments"

    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), nullable=False, index=True)
    edge_app_version_id: Mapped[str] = mapped_column(ForeignKey("edge_app_versions.id"), nullable=False)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending", index=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    deployed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    logs_uri: Mapped[str | None] = mapped_column(Text)
