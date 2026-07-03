from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Index, Integer, JSON, String, Text, and_
from sqlalchemy.orm import Mapped, mapped_column

from visiox_db.base import Base, IdMixin, TimestampMixin


ACTIVE_TASK_STATUSES = ("PENDING", "QUEUED", "RUNNING")


class Task(IdMixin, TimestampMixin, Base):
    __tablename__ = "tasks"

    task_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING", index=True)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    resource_type: Mapped[str | None] = mapped_column(String(80))
    resource_id: Mapped[str | None] = mapped_column(String(36))
    stage: Mapped[str | None] = mapped_column(String(120))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index(
    "uq_tasks_active_resource_command",
    Task.task_type,
    Task.resource_type,
    Task.resource_id,
    unique=True,
    postgresql_where=and_(
        Task.status.in_(ACTIVE_TASK_STATUSES),
        Task.resource_type.is_not(None),
        Task.resource_id.is_not(None),
    ),
    sqlite_where=and_(
        Task.status.in_(ACTIVE_TASK_STATUSES),
        Task.resource_type.is_not(None),
        Task.resource_id.is_not(None),
    ),
)
