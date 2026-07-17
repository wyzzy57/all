from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from visiox_db.base import Base, IdMixin, TimestampMixin


class ResourcePool(IdMixin, TimestampMixin, Base):
    __tablename__ = "resource_pools"
    name: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    selector: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    compatibility_policy: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ComputeNode(IdMixin, TimestampMixin, Base):
    __tablename__ = "compute_nodes"
    name: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    resource_pool_id: Mapped[str | None] = mapped_column(ForeignKey("resource_pools.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="enrolling", index=True)
    architecture: Mapped[str] = mapped_column(String(32), nullable=False)
    platform_kind: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    resources: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    fingerprint: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    agent_version: Mapped[str] = mapped_column(String(40), nullable=False)
    certificate_serial: Mapped[str | None] = mapped_column(String(80), unique=True)
    certificate_fingerprint: Mapped[str | None] = mapped_column(String(128), unique=True)
    certificate_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    pending_certificate_serial: Mapped[str | None] = mapped_column(String(80), unique=True)
    pending_certificate_fingerprint: Mapped[str | None] = mapped_column(String(128), unique=True)
    pending_certificate_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    pending_certificate_pem: Mapped[str | None] = mapped_column(Text)
    pending_renewal_request_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    pending_renewal_csr_fingerprint: Mapped[str | None] = mapped_column(String(64))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class AgentEnrollmentToken(IdMixin, TimestampMixin, Base):
    __tablename__ = "agent_enrollment_tokens"
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    node_id: Mapped[str | None] = mapped_column(ForeignKey("compute_nodes.id"))
    enrollment_request_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    enrollment_csr_fingerprint: Mapped[str | None] = mapped_column(String(64))
    enrollment_node_name: Mapped[str | None] = mapped_column(String(160))
    enrollment_architecture: Mapped[str | None] = mapped_column(String(32))
    enrollment_platform_kind: Mapped[str | None] = mapped_column(String(40))
    enrollment_agent_version: Mapped[str | None] = mapped_column(String(40))
    enrollment_certificate_pem: Mapped[str | None] = mapped_column(Text)
    enrollment_ca_certificate_pem: Mapped[str | None] = mapped_column(Text)
    enrollment_gateway_url: Mapped[str | None] = mapped_column(String(2048))
    enrollment_heartbeat_interval_seconds: Mapped[int | None] = mapped_column(Integer)


class NodeCommand(IdMixin, TimestampMixin, Base):
    __tablename__ = "node_commands"
    command_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("compute_nodes.id"), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    desired_state: Mapped[str | None] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    error: Mapped[str | None] = mapped_column(Text)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NodeEvent(IdMixin, TimestampMixin, Base):
    __tablename__ = "node_events"
    __table_args__ = (UniqueConstraint("node_id", "sequence"),)
    node_id: Mapped[str] = mapped_column(ForeignKey("compute_nodes.id"), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    command_id: Mapped[str | None] = mapped_column(String(36), index=True)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    stage: Mapped[str | None] = mapped_column(String(120))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index("ix_node_events_node_sequence", NodeEvent.node_id, NodeEvent.sequence)
