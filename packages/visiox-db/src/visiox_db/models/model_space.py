from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
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


LEGACY_ADAPTER_VERSION = "1.0.0"
LEGACY_UNSPECIFIED_MODEL_FAMILY = "legacy-unspecified"
# Compatibility snapshot of Task 1's canonical legacy mappings. visiox-db owns
# this read-only copy so SQLAlchemy defaults remain usable as a standalone package.
LEGACY_PIPELINE_COMPATIBILITY_MAPPINGS: Mapping[str, Mapping[str, str]] = (
    MappingProxyType(
        {
            "yolo26": MappingProxyType(
                {
                    "task_kind": "object_detection",
                    "framework": "ultralytics",
                    "adapter_key": "ultralytics.object_detection.v1",
                    "adapter_version": LEGACY_ADAPTER_VERSION,
                }
            ),
            "llamafactory": MappingProxyType(
                {
                    "task_kind": "llm_sft",
                    "framework": "llamafactory",
                    "adapter_key": "llamafactory.llm_sft.v1",
                    "adapter_version": LEGACY_ADAPTER_VERSION,
                }
            ),
        }
    )
)


def _legacy_pipeline_default(context: Any, field: str) -> str:
    parameters = context.get_current_parameters()
    engine = parameters.get("engine", "yolo26")
    try:
        mapping = LEGACY_PIPELINE_COMPATIBILITY_MAPPINGS[engine]
    except KeyError as exc:
        raise ValueError(f"Unknown legacy engine {engine!r}") from exc
    return mapping[field]


def _legacy_model_family_default(context: Any) -> str:
    parameters = context.get_current_parameters()
    configured = parameters.get("params_template") or {}
    if configured.get("model_family"):
        return str(configured["model_family"])
    if parameters.get("engine", "yolo26") == "yolo26":
        return "yolo26"
    return LEGACY_UNSPECIFIED_MODEL_FAMILY


def _legacy_recipe_default(context: Any) -> dict[str, Any]:
    return dict(context.get_current_parameters().get("params_template") or {})


def _column_value_default(context: Any, source: str) -> str:
    return str(context.get_current_parameters()[source])


def _artifact_format_default(context: Any, source: str) -> str:
    value = str(context.get_current_parameters().get(source) or "")
    suffix = value.rsplit(".", 1)[-1].lower() if "." in value else ""
    return suffix or "unknown"


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
    __table_args__ = (
        UniqueConstraint(
            "framework",
            "family",
            "task",
            "scale",
            name="uq_base_models_framework_family_task_scale",
        ),
    )

    family: Mapped[str] = mapped_column(String(80), nullable=False)
    task: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    scale: Mapped[str] = mapped_column(String(8), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    framework: Mapped[str] = mapped_column(
        String(80), nullable=False, default="ultralytics"
    )
    model_family: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
        default=lambda context: _column_value_default(context, "family"),
    )
    variant: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
        default=lambda context: _column_value_default(context, "scale"),
    )
    artifact_format: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default=lambda context: _artifact_format_default(context, "filename"),
    )
    artifact_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    local_uri: Mapped[str | None] = mapped_column(Text)
    checksum: Mapped[str | None] = mapped_column(String(128))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="ready", index=True
    )
    model_source_id: Mapped[str | None] = mapped_column(ForeignKey("model_sources.id"))


class TrainingPipeline(IdMixin, TimestampMixin, Base):
    __tablename__ = "training_pipelines"

    name: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    organization_id: Mapped[str | None] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    owner_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), index=True
    )
    visibility: Mapped[str] = mapped_column(
        String(24), nullable=False, default="private", index=True
    )
    engine: Mapped[str] = mapped_column(
        String(40), nullable=False, default="yolo26", index=True
    )
    task: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    scale: Mapped[str] = mapped_column(String(8), nullable=False)
    task_kind: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
        default=lambda context: _legacy_pipeline_default(context, "task_kind"),
    )
    framework: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
        default=lambda context: _legacy_pipeline_default(context, "framework"),
    )
    adapter_key: Mapped[str] = mapped_column(
        String(160),
        nullable=False,
        default=lambda context: _legacy_pipeline_default(context, "adapter_key"),
    )
    adapter_version: Mapped[str] = mapped_column(
        String(40), nullable=False, default=LEGACY_ADAPTER_VERSION
    )
    model_family: Mapped[str] = mapped_column(
        String(120), nullable=False, default=_legacy_model_family_default
    )
    recipe: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=_legacy_recipe_default
    )
    framework_locked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    first_submitted_job_id: Mapped[str | None] = mapped_column(
        ForeignKey("training_jobs.id"), index=True
    )
    cloned_from_pipeline_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "training_pipelines.id",
            name="fk_training_pipelines_cloned_from_id_training_pipelines",
        ),
        index=True,
    )
    base_model_id: Mapped[str | None] = mapped_column(ForeignKey("base_models.id"))
    dataset_id: Mapped[str | None] = mapped_column(ForeignKey("datasets.id"))
    params_template: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    default_environment: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="draft", index=True
    )
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    public_scope: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    is_favorite: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class TrainingJob(IdMixin, TimestampMixin, Base):
    __tablename__ = "training_jobs"

    pipeline_id: Mapped[str] = mapped_column(
        ForeignKey("training_pipelines.id"), nullable=False
    )
    organization_id: Mapped[str | None] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    owner_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), index=True
    )
    visibility: Mapped[str] = mapped_column(
        String(24), nullable=False, default="private", index=True
    )
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    trained_model_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="pending", index=True
    )
    params: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    resolved_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    launch_spec_checksum: Mapped[str | None] = mapped_column(String(128))
    log_uri: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PipelineEvaluation(IdMixin, TimestampMixin, Base):
    __tablename__ = "pipeline_evaluations"

    pipeline_id: Mapped[str] = mapped_column(
        ForeignKey("training_pipelines.id"), nullable=False
    )
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"), nullable=False)
    evaluation_set: Mapped[str] = mapped_column(String(40), nullable=False)
    model_weight: Mapped[str] = mapped_column(String(160), nullable=False)
    environment: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="completed", index=True
    )
    score: Mapped[float | None] = mapped_column()
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class TrainingJobAttempt(IdMixin, TimestampMixin, Base):
    __tablename__ = "training_job_attempts"
    __table_args__ = (
        UniqueConstraint(
            "training_job_id",
            "attempt_number",
            name="uq_training_job_attempts_job_attempt",
        ),
    )

    training_job_id: Mapped[str] = mapped_column(
        ForeignKey("training_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="pending", index=True
    )
    launch_spec: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    log_uri: Mapped[str | None] = mapped_column(Text)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    artifact_manifest: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    container_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TrainedModel(IdMixin, TimestampMixin, Base):
    __tablename__ = "trained_models"
    __table_args__ = (
        Index("ix_trained_models_training_job_id", "training_job_id"),
        Index(
            "uq_trained_models_job_role_no_attempt",
            "training_job_id",
            "artifact_role",
            unique=True,
            postgresql_where=text(
                "training_job_id IS NOT NULL AND training_job_attempt_id IS NULL"
            ),
            sqlite_where=text(
                "training_job_id IS NOT NULL AND training_job_attempt_id IS NULL"
            ),
        ),
        Index(
            "uq_trained_models_attempt_role",
            "training_job_attempt_id",
            "artifact_role",
            unique=True,
            postgresql_where=text("training_job_attempt_id IS NOT NULL"),
            sqlite_where=text("training_job_attempt_id IS NOT NULL"),
        ),
    )

    pipeline_id: Mapped[str | None] = mapped_column(ForeignKey("training_pipelines.id"))
    organization_id: Mapped[str | None] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    owner_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), index=True
    )
    visibility: Mapped[str] = mapped_column(
        String(24), nullable=False, default="private", index=True
    )
    training_job_id: Mapped[str | None] = mapped_column(ForeignKey("training_jobs.id"))
    training_job_attempt_id: Mapped[str | None] = mapped_column(
        ForeignKey("training_job_attempts.id")
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    display_name: Mapped[str] = mapped_column(
        String(160),
        nullable=False,
        default=lambda context: _column_value_default(context, "name"),
    )
    version: Mapped[str] = mapped_column(String(80), nullable=False)
    task: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    framework: Mapped[str | None] = mapped_column(String(80))
    adapter_key: Mapped[str | None] = mapped_column(String(160))
    model_family: Mapped[str | None] = mapped_column(String(120))
    model_format: Mapped[str | None] = mapped_column(String(40))
    artifact_role: Mapped[str] = mapped_column(
        String(80), nullable=False, default="unassigned"
    )
    artifact_uri: Mapped[str] = mapped_column(Text, nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(128))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    evaluation_report_uri: Mapped[str | None] = mapped_column(Text)
    deployment_compatibility: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    artifact_manifest: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="created", index=True
    )


class DeploymentService(IdMixin, TimestampMixin, Base):
    __tablename__ = "deployment_services"

    name: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    organization_id: Mapped[str | None] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    owner_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), index=True
    )
    visibility: Mapped[str] = mapped_column(
        String(24), nullable=False, default="private", index=True
    )
    pipeline_id: Mapped[str] = mapped_column(
        ForeignKey("training_pipelines.id"), nullable=False, index=True
    )
    trained_model_id: Mapped[str | None] = mapped_column(
        ForeignKey("trained_models.id")
    )
    model_name: Mapped[str] = mapped_column(String(160), nullable=False)
    model_weight: Mapped[str] = mapped_column(String(160), nullable=False)
    environment: Mapped[str] = mapped_column(String(120), nullable=False)
    instance_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    instance_name: Mapped[str] = mapped_column(
        String(160), nullable=False, default="default"
    )
    resource_summary: Mapped[str] = mapped_column(
        String(255), nullable=False, default=""
    )
    status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="running", index=True
    )
    desired_state: Mapped[str] = mapped_column(
        String(24), nullable=False, default="running", index=True
    )
    active_revision: Mapped[int | None] = mapped_column(Integer)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    calls: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class DeploymentInstance(IdMixin, TimestampMixin, Base):
    __tablename__ = "deployment_instances"
    __table_args__ = (
        UniqueConstraint("deployment_service_id", "node_id", "instance_name"),
    )

    deployment_service_id: Mapped[str] = mapped_column(
        ForeignKey("deployment_services.id"), nullable=False, index=True
    )
    deployment_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    node_id: Mapped[str] = mapped_column(
        ForeignKey("compute_nodes.id"), nullable=False, index=True
    )
    instance_name: Mapped[str] = mapped_column(String(160), nullable=False)
    container_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    image_digest: Mapped[str | None] = mapped_column(String(255))
    model_checksum: Mapped[str | None] = mapped_column(String(128))
    engine: Mapped[str] = mapped_column(String(80), nullable=False)
    engine_digest: Mapped[str | None] = mapped_column(String(255))
    port: Mapped[int | None] = mapped_column(Integer)
    endpoint: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="queued", index=True
    )
    health_status: Mapped[str | None] = mapped_column(String(32))
    health_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rollback_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )


class DistributedTrainingRun(IdMixin, TimestampMixin, Base):
    __tablename__ = "distributed_training_runs"

    training_job_id: Mapped[str] = mapped_column(
        ForeignKey("training_jobs.id"), nullable=False, index=True
    )
    resource_pool_id: Mapped[str] = mapped_column(
        ForeignKey("resource_pools.id"), nullable=False, index=True
    )
    node_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    ranks: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    master_addr: Mapped[str] = mapped_column(String(255), nullable=False)
    master_port: Mapped[int] = mapped_column(Integer, nullable=False)
    world_size: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    rendezvous_backend: Mapped[str] = mapped_column(
        String(40), nullable=False, default="c10d"
    )
    training_image_digest: Mapped[str | None] = mapped_column(String(255))
    container_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    checkpoint_uri: Mapped[str | None] = mapped_column(Text)
    checkpoint_checksum: Mapped[str | None] = mapped_column(String(128))
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="queued", index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
