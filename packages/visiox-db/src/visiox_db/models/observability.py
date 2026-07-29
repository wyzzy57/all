from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from visiox_db.base import Base, IdMixin, TimestampMixin


class LogSource(StrEnum):
    TRAINING = "training"
    REMOTE_EXECUTION = "remote_execution"
    DEPLOYMENT = "deployment"
    INFERENCE = "inference"
    RECONCILIATION = "reconciliation"


class LogStreamStatus(StrEnum):
    OPEN = "open"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class LogStream(IdMixin, TimestampMixin, Base):
    __tablename__ = "log_streams"
    __table_args__ = (
        Index("ix_log_streams_resource", "resource_type", "resource_id"),
        Index("ix_log_streams_created_at", "created_at"),
        Index("ix_log_streams_retention_expires_at", "retention_expires_at"),
    )

    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=LogStreamStatus.OPEN.value, index=True
    )
    encoding: Mapped[str] = mapped_column(String(24), nullable=False, default="utf-8")
    next_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    line_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    redacted_log_uri: Mapped[str | None] = mapped_column(Text)
    retention_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LogChunk(IdMixin, TimestampMixin, Base):
    __tablename__ = "log_chunks"
    __table_args__ = (
        UniqueConstraint("stream_id", "sequence", name="uq_log_chunks_stream_sequence"),
        Index("ix_log_chunks_stream_sequence", "stream_id", "sequence"),
        Index("ix_log_chunks_time_range", "first_timestamp", "last_timestamp"),
    )

    stream_id: Mapped[str] = mapped_column(
        ForeignKey("log_streams.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    object_uri: Mapped[str] = mapped_column(Text, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    compressed_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    uncompressed_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    byte_start: Mapped[int] = mapped_column(BigInteger, nullable=False)
    byte_end: Mapped[int] = mapped_column(BigInteger, nullable=False)
    line_count: Mapped[int] = mapped_column(Integer, nullable=False)
    first_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
