"""persist multi-framework training identities and artifacts

Revision ID: 20260731_0001
Revises: 20260727_0006
Create Date: 2026-07-31 00:01:00.000000
"""

from collections.abc import Iterator, Mapping, Sequence
from pathlib import PurePosixPath
from types import MappingProxyType
from urllib.parse import urlsplit

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import Connection, RowMapping
from sqlalchemy.sql import Executable

revision: str = "20260731_0001"
down_revision: str | None = "20260727_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen from visiox_training.registry at revision creation. Historical
# migrations must not depend on mutable application package imports.
LEGACY_ENGINE_MAPPINGS: Mapping[str, Mapping[str, str]] = MappingProxyType(
    {
        "yolo26": MappingProxyType(
            {
                "task_kind": "object_detection",
                "framework": "ultralytics",
                "adapter_key": "ultralytics.object_detection.v1",
                "adapter_version": "1.0.0",
            }
        ),
        "llamafactory": MappingProxyType(
            {
                "task_kind": "llm_sft",
                "framework": "llamafactory",
                "adapter_key": "llamafactory.llm_sft.v1",
                "adapter_version": "1.0.0",
            }
        ),
    }
)
# Old LLaMA-Factory rows did not require a model family. This value records that
# absence without claiming a concrete architecture or checkpoint.
LEGACY_UNSPECIFIED_MODEL_FAMILY = "legacy-unspecified"
BACKFILL_BATCH_SIZE = 500


def _id_column() -> sa.Column:
    return sa.Column("id", sa.String(length=36), nullable=False)


def _timestamp_columns() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def _artifact_format(uri_or_filename: str | None) -> str:
    path = urlsplit(uri_or_filename or "").path
    suffix = PurePosixPath(path).suffix.removeprefix(".").lower()
    return suffix or "unknown"


def _legacy_engine_mapping(engine: str) -> Mapping[str, str]:
    try:
        return LEGACY_ENGINE_MAPPINGS[engine]
    except KeyError as exc:
        raise RuntimeError(f"Unknown legacy engine {engine!r}") from exc


def _mapping_batches(
    connection: Connection,
    statement: Executable,
) -> Iterator[list[RowMapping]]:
    result = connection.execute(statement).mappings()
    while batch := result.fetchmany(BACKFILL_BATCH_SIZE):
        yield batch


def upgrade() -> None:
    with op.batch_alter_table("base_models") as batch_op:
        batch_op.add_column(sa.Column("framework", sa.String(length=80), nullable=True))
        batch_op.add_column(
            sa.Column("model_family", sa.String(length=120), nullable=True)
        )
        batch_op.add_column(sa.Column("variant", sa.String(length=120), nullable=True))
        batch_op.add_column(
            sa.Column("artifact_format", sa.String(length=40), nullable=True)
        )
        batch_op.add_column(sa.Column("artifact_metadata", sa.JSON(), nullable=True))

    with op.batch_alter_table("training_pipelines") as batch_op:
        batch_op.add_column(sa.Column("task_kind", sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column("framework", sa.String(length=80), nullable=True))
        batch_op.add_column(
            sa.Column("adapter_key", sa.String(length=160), nullable=True)
        )
        batch_op.add_column(
            sa.Column("adapter_version", sa.String(length=40), nullable=True)
        )
        batch_op.add_column(
            sa.Column("model_family", sa.String(length=120), nullable=True)
        )
        batch_op.add_column(sa.Column("recipe", sa.JSON(), nullable=True))
        batch_op.add_column(
            sa.Column("framework_locked_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("first_submitted_job_id", sa.String(length=36), nullable=True)
        )
        batch_op.add_column(
            sa.Column("cloned_from_pipeline_id", sa.String(length=36), nullable=True)
        )

    with op.batch_alter_table("training_jobs") as batch_op:
        batch_op.add_column(sa.Column("resolved_snapshot", sa.JSON(), nullable=True))
        batch_op.add_column(
            sa.Column("launch_spec_checksum", sa.String(length=128), nullable=True)
        )

    op.create_table(
        "training_job_attempts",
        _id_column(),
        sa.Column("training_job_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("launch_spec", sa.JSON(), nullable=False),
        sa.Column("launch_spec_checksum", sa.String(length=128), nullable=False),
        sa.Column("log_uri", sa.Text(), nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("artifact_manifest", sa.JSON(), nullable=False),
        sa.Column("container_ids", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamp_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_training_job_attempts"),
        sa.ForeignKeyConstraint(
            ["training_job_id"],
            ["training_jobs.id"],
            name="fk_training_job_attempts_training_job_id_training_jobs",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "training_job_id",
            "attempt_number",
            name="uq_training_job_attempts_job_attempt",
        ),
    )
    op.create_index(
        "ix_training_job_attempts_training_job_id",
        "training_job_attempts",
        ["training_job_id"],
    )
    op.create_index(
        "ix_training_job_attempts_status", "training_job_attempts", ["status"]
    )
    op.create_index(
        "ix_training_job_attempts_started_at",
        "training_job_attempts",
        ["started_at"],
    )

    op.drop_index("uq_trained_models_training_job", table_name="trained_models")
    with op.batch_alter_table("trained_models") as batch_op:
        batch_op.add_column(sa.Column("framework", sa.String(length=80), nullable=True))
        batch_op.add_column(
            sa.Column("adapter_key", sa.String(length=160), nullable=True)
        )
        batch_op.add_column(
            sa.Column("model_family", sa.String(length=120), nullable=True)
        )
        batch_op.add_column(
            sa.Column("model_format", sa.String(length=40), nullable=True)
        )
        batch_op.add_column(
            sa.Column("artifact_role", sa.String(length=80), nullable=True)
        )
        batch_op.add_column(sa.Column("checksum", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("size_bytes", sa.BigInteger(), nullable=True))
        batch_op.add_column(
            sa.Column("evaluation_report_uri", sa.Text(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("deployment_compatibility", sa.JSON(), nullable=True)
        )
        batch_op.add_column(sa.Column("artifact_manifest", sa.JSON(), nullable=True))
        batch_op.add_column(
            sa.Column("display_name", sa.String(length=160), nullable=True)
        )
        batch_op.add_column(
            sa.Column("training_job_attempt_id", sa.String(length=36), nullable=True)
        )

    connection = op.get_bind()
    base_models = sa.table(
        "base_models",
        sa.column("id", sa.String()),
        sa.column("family", sa.String()),
        sa.column("scale", sa.String()),
        sa.column("filename", sa.String()),
        sa.column("framework", sa.String()),
        sa.column("model_family", sa.String()),
        sa.column("variant", sa.String()),
        sa.column("artifact_format", sa.String()),
        sa.column("artifact_metadata", sa.JSON()),
    )
    pipelines = sa.table(
        "training_pipelines",
        sa.column("id", sa.String()),
        sa.column("engine", sa.String()),
        sa.column("task", sa.String()),
        sa.column("scale", sa.String()),
        sa.column("base_model_id", sa.String()),
        sa.column("dataset_id", sa.String()),
        sa.column("params_template", sa.JSON()),
        sa.column("task_kind", sa.String()),
        sa.column("framework", sa.String()),
        sa.column("adapter_key", sa.String()),
        sa.column("adapter_version", sa.String()),
        sa.column("model_family", sa.String()),
        sa.column("recipe", sa.JSON()),
        sa.column("framework_locked_at", sa.DateTime(timezone=True)),
        sa.column("first_submitted_job_id", sa.String()),
    )
    jobs = sa.table(
        "training_jobs",
        sa.column("id", sa.String()),
        sa.column("pipeline_id", sa.String()),
        sa.column("task_id", sa.String()),
        sa.column("params", sa.JSON()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("resolved_snapshot", sa.JSON()),
    )
    tasks = sa.table(
        "tasks",
        sa.column("id", sa.String()),
        sa.column("payload", sa.JSON()),
    )
    trained_models = sa.table(
        "trained_models",
        sa.column("id", sa.String()),
        sa.column("pipeline_id", sa.String()),
        sa.column("training_job_id", sa.String()),
        sa.column("name", sa.String()),
        sa.column("artifact_uri", sa.Text()),
        sa.column("framework", sa.String()),
        sa.column("adapter_key", sa.String()),
        sa.column("model_family", sa.String()),
        sa.column("model_format", sa.String()),
        sa.column("artifact_role", sa.String()),
        sa.column("deployment_compatibility", sa.JSON()),
        sa.column("artifact_manifest", sa.JSON()),
        sa.column("display_name", sa.String()),
    )

    connection.execute(
        sa.update(base_models).values(
            framework="ultralytics",
            model_family=base_models.c.family,
            variant=base_models.c.scale,
            artifact_metadata={},
        )
    )
    base_format_update = (
        sa.update(base_models)
        .where(base_models.c.id == sa.bindparam("_base_id"))
        .values(artifact_format=sa.bindparam("_artifact_format"))
    )
    for batch in _mapping_batches(
        connection,
        sa.select(base_models.c.id, base_models.c.filename),
    ):
        connection.execute(
            base_format_update,
            [
                {
                    "_base_id": row["id"],
                    "_artifact_format": _artifact_format(row["filename"]),
                }
                for row in batch
            ],
        )

    for batch in _mapping_batches(
        connection,
        sa.select(pipelines.c.engine).distinct(),
    ):
        for row in batch:
            _legacy_engine_mapping(row["engine"])

    first_job_id = (
        sa.select(jobs.c.id)
        .where(jobs.c.pipeline_id == pipelines.c.id)
        .order_by(jobs.c.created_at, jobs.c.id)
        .limit(1)
        .correlate(pipelines)
        .scalar_subquery()
    )
    first_job_created_at = (
        sa.select(jobs.c.created_at)
        .where(jobs.c.pipeline_id == pipelines.c.id)
        .order_by(jobs.c.created_at, jobs.c.id)
        .limit(1)
        .correlate(pipelines)
        .scalar_subquery()
    )
    connection.execute(
        sa.update(pipelines).values(
            task_kind=sa.case(
                (pipelines.c.engine == "yolo26", "object_detection"),
                (pipelines.c.engine == "llamafactory", "llm_sft"),
            ),
            framework=sa.case(
                (pipelines.c.engine == "yolo26", "ultralytics"),
                (pipelines.c.engine == "llamafactory", "llamafactory"),
            ),
            adapter_key=sa.case(
                (
                    pipelines.c.engine == "yolo26",
                    "ultralytics.object_detection.v1",
                ),
                (
                    pipelines.c.engine == "llamafactory",
                    "llamafactory.llm_sft.v1",
                ),
            ),
            adapter_version="1.0.0",
            recipe=pipelines.c.params_template,
            framework_locked_at=first_job_created_at,
            first_submitted_job_id=first_job_id,
        )
    )
    pipeline_family_update = (
        sa.update(pipelines)
        .where(pipelines.c.id == sa.bindparam("_pipeline_id"))
        .values(model_family=sa.bindparam("_model_family"))
    )
    pipeline_family_query = sa.select(
        pipelines.c.id,
        pipelines.c.engine,
        pipelines.c.params_template,
        base_models.c.family.label("base_family"),
    ).select_from(
        pipelines.outerjoin(
            base_models,
            base_models.c.id == pipelines.c.base_model_id,
        )
    )
    for batch in _mapping_batches(connection, pipeline_family_query):
        updates = []
        for row in batch:
            configured_family = (row["params_template"] or {}).get("model_family")
            model_family = row["base_family"]
            if model_family is None and configured_family:
                model_family = str(configured_family)
            if model_family is None and row["engine"] == "yolo26":
                model_family = "yolo26"
            if model_family is None:
                model_family = LEGACY_UNSPECIFIED_MODEL_FAMILY
            updates.append({"_pipeline_id": row["id"], "_model_family": model_family})
        connection.execute(pipeline_family_update, updates)

    job_snapshot_update = (
        sa.update(jobs)
        .where(jobs.c.id == sa.bindparam("_job_id"))
        .values(resolved_snapshot=sa.bindparam("_resolved_snapshot", type_=sa.JSON()))
    )
    job_snapshot_query = (
        sa.select(
            jobs.c.id,
            jobs.c.pipeline_id,
            jobs.c.params,
            pipelines.c.task_kind,
            pipelines.c.framework,
            pipelines.c.adapter_key,
            pipelines.c.adapter_version,
            pipelines.c.model_family,
            pipelines.c.engine.label("legacy_engine"),
            pipelines.c.task.label("legacy_task"),
            pipelines.c.scale.label("legacy_scale"),
            tasks.c.payload.label("task_payload"),
        )
        .select_from(
            jobs.join(pipelines, pipelines.c.id == jobs.c.pipeline_id).outerjoin(
                tasks, tasks.c.id == jobs.c.task_id
            )
        )
        .order_by(jobs.c.id)
    )
    for batch in _mapping_batches(connection, job_snapshot_query):
        updates = []
        for row in batch:
            snapshot = {
                "pipeline_id": row["pipeline_id"],
                "task_kind": row["task_kind"],
                "framework": row["framework"],
                "adapter_key": row["adapter_key"],
                "adapter_version": row["adapter_version"],
                "model_family": row["model_family"],
                "legacy_engine": row["legacy_engine"],
                "legacy_task": row["legacy_task"],
                "legacy_scale": row["legacy_scale"],
                "params": row["params"] or {},
            }
            task_payload = row["task_payload"] or {}
            for key in ("base_model_id", "dataset_id"):
                value = task_payload.get(key)
                if isinstance(value, str) and value.strip():
                    snapshot[key] = value
            updates.append({"_job_id": row["id"], "_resolved_snapshot": snapshot})
        connection.execute(job_snapshot_update, updates)

    connection.execute(
        sa.update(trained_models).values(
            artifact_role="legacy_primary",
            deployment_compatibility={},
            artifact_manifest={},
            display_name=trained_models.c.name,
        )
    )
    direct_pipeline = pipelines.alias("direct_pipeline")
    job_pipeline = pipelines.alias("job_pipeline")
    model_identity_query = (
        sa.select(
            trained_models.c.id,
            trained_models.c.artifact_uri,
            sa.func.coalesce(
                direct_pipeline.c.framework, job_pipeline.c.framework
            ).label("framework"),
            sa.func.coalesce(
                direct_pipeline.c.adapter_key, job_pipeline.c.adapter_key
            ).label("adapter_key"),
            sa.func.coalesce(
                direct_pipeline.c.model_family, job_pipeline.c.model_family
            ).label("model_family"),
        )
        .select_from(
            trained_models.outerjoin(
                jobs, jobs.c.id == trained_models.c.training_job_id
            )
            .outerjoin(
                direct_pipeline,
                direct_pipeline.c.id == trained_models.c.pipeline_id,
            )
            .outerjoin(
                job_pipeline,
                job_pipeline.c.id == jobs.c.pipeline_id,
            )
        )
        .order_by(trained_models.c.id)
    )
    model_identity_update = (
        sa.update(trained_models)
        .where(trained_models.c.id == sa.bindparam("_model_id"))
        .values(
            framework=sa.bindparam("_framework"),
            adapter_key=sa.bindparam("_adapter_key"),
            model_family=sa.bindparam("_model_family"),
            model_format=sa.bindparam("_model_format"),
        )
    )
    for batch in _mapping_batches(connection, model_identity_query):
        connection.execute(
            model_identity_update,
            [
                {
                    "_model_id": row["id"],
                    "_framework": row["framework"] or "legacy-unknown",
                    "_adapter_key": row["adapter_key"] or "legacy-unknown",
                    "_model_family": row["model_family"]
                    or LEGACY_UNSPECIFIED_MODEL_FAMILY,
                    "_model_format": _artifact_format(row["artifact_uri"]),
                }
                for row in batch
            ],
        )

    with op.batch_alter_table("base_models") as batch_op:
        batch_op.drop_constraint("uq_base_models_family", type_="unique")
        for column, column_type in (
            ("framework", sa.String(length=80)),
            ("model_family", sa.String(length=120)),
            ("variant", sa.String(length=120)),
            ("artifact_format", sa.String(length=40)),
            ("artifact_metadata", sa.JSON()),
        ):
            batch_op.alter_column(column, existing_type=column_type, nullable=False)
        batch_op.create_unique_constraint(
            "uq_base_models_framework_family_task_scale",
            ["framework", "family", "task", "scale"],
        )

    with op.batch_alter_table("training_pipelines") as batch_op:
        for column, column_type in (
            ("task_kind", sa.String(length=80)),
            ("framework", sa.String(length=80)),
            ("adapter_key", sa.String(length=160)),
            ("adapter_version", sa.String(length=40)),
            ("model_family", sa.String(length=120)),
            ("recipe", sa.JSON()),
        ):
            batch_op.alter_column(column, existing_type=column_type, nullable=False)
        batch_op.create_foreign_key(
            "fk_training_pipelines_first_submitted_job_id_training_jobs",
            "training_jobs",
            ["first_submitted_job_id"],
            ["id"],
        )
        batch_op.create_foreign_key(
            "fk_training_pipelines_cloned_from_id_training_pipelines",
            "training_pipelines",
            ["cloned_from_pipeline_id"],
            ["id"],
        )
    op.create_index(
        "ix_training_pipelines_first_submitted_job_id",
        "training_pipelines",
        ["first_submitted_job_id"],
    )
    op.create_index(
        "ix_training_pipelines_cloned_from_pipeline_id",
        "training_pipelines",
        ["cloned_from_pipeline_id"],
    )

    with op.batch_alter_table("training_jobs") as batch_op:
        batch_op.alter_column(
            "resolved_snapshot", existing_type=sa.JSON(), nullable=False
        )

    with op.batch_alter_table("trained_models") as batch_op:
        for column, column_type in (
            ("artifact_role", sa.String(length=80)),
            ("deployment_compatibility", sa.JSON()),
            ("artifact_manifest", sa.JSON()),
            ("display_name", sa.String(length=160)),
        ):
            batch_op.alter_column(column, existing_type=column_type, nullable=False)
        batch_op.create_foreign_key(
            "fk_trained_models_training_job_attempt_id_training_job_attempts",
            "training_job_attempts",
            ["training_job_attempt_id"],
            ["id"],
        )

    no_attempt = sa.text(
        "training_job_id IS NOT NULL AND training_job_attempt_id IS NULL"
    )
    has_attempt = sa.text("training_job_attempt_id IS NOT NULL")
    op.create_index(
        "ix_trained_models_training_job_id",
        "trained_models",
        ["training_job_id"],
    )
    op.create_index(
        "uq_trained_models_job_role_no_attempt",
        "trained_models",
        ["training_job_id", "artifact_role"],
        unique=True,
        postgresql_where=no_attempt,
        sqlite_where=no_attempt,
    )
    op.create_index(
        "uq_trained_models_attempt_role",
        "trained_models",
        ["training_job_attempt_id", "artifact_role"],
        unique=True,
        postgresql_where=has_attempt,
        sqlite_where=has_attempt,
    )


def downgrade() -> None:
    connection = op.get_bind()
    attempt_count = connection.execute(
        sa.text("SELECT COUNT(*) FROM training_job_attempts")
    ).scalar_one()
    jobs_with_multiple_models = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM ("
            "SELECT training_job_id FROM trained_models "
            "WHERE training_job_id IS NOT NULL GROUP BY training_job_id "
            "HAVING COUNT(*) > 1"
            ") AS incompatible_jobs"
        )
    ).scalar_one()
    base_model_tuple_collisions = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM ("
            "SELECT family, task, scale FROM base_models "
            "GROUP BY family, task, scale HAVING COUNT(*) > 1"
            ") AS incompatible_base_models"
        )
    ).scalar_one()
    if attempt_count or jobs_with_multiple_models or base_model_tuple_collisions:
        problems = []
        if attempt_count:
            problems.append(
                f"{attempt_count} training job attempts exist; remove attempts only after "
                "preserving their history externally"
            )
        if jobs_with_multiple_models:
            problems.append(
                f"{jobs_with_multiple_models} training jobs have multiple trained models; "
                "reconcile artifacts to one model per job"
            )
        if base_model_tuple_collisions:
            problems.append(
                f"{base_model_tuple_collisions} legacy base model tuple collisions exist; "
                "reconcile base models to one row per (family, task, scale)"
            )
        raise RuntimeError(
            "Cannot downgrade 20260731_0001 without data loss: " + "; ".join(problems)
        )

    op.drop_index("uq_trained_models_attempt_role", table_name="trained_models")
    op.drop_index("uq_trained_models_job_role_no_attempt", table_name="trained_models")
    op.drop_index("ix_trained_models_training_job_id", table_name="trained_models")

    with op.batch_alter_table("trained_models") as batch_op:
        batch_op.drop_constraint(
            "fk_trained_models_training_job_attempt_id_training_job_attempts",
            type_="foreignkey",
        )
        for column in (
            "training_job_attempt_id",
            "display_name",
            "artifact_manifest",
            "deployment_compatibility",
            "evaluation_report_uri",
            "size_bytes",
            "checksum",
            "artifact_role",
            "model_format",
            "model_family",
            "adapter_key",
            "framework",
        ):
            batch_op.drop_column(column)

    op.drop_index(
        "ix_training_job_attempts_started_at", table_name="training_job_attempts"
    )
    op.drop_index("ix_training_job_attempts_status", table_name="training_job_attempts")
    op.drop_index(
        "ix_training_job_attempts_training_job_id", table_name="training_job_attempts"
    )
    op.drop_table("training_job_attempts")

    with op.batch_alter_table("training_jobs") as batch_op:
        batch_op.drop_column("launch_spec_checksum")
        batch_op.drop_column("resolved_snapshot")

    op.drop_index(
        "ix_training_pipelines_cloned_from_pipeline_id",
        table_name="training_pipelines",
    )
    op.drop_index(
        "ix_training_pipelines_first_submitted_job_id",
        table_name="training_pipelines",
    )
    with op.batch_alter_table("training_pipelines") as batch_op:
        batch_op.drop_constraint(
            "fk_training_pipelines_cloned_from_id_training_pipelines",
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            "fk_training_pipelines_first_submitted_job_id_training_jobs",
            type_="foreignkey",
        )
        for column in (
            "cloned_from_pipeline_id",
            "first_submitted_job_id",
            "framework_locked_at",
            "recipe",
            "model_family",
            "adapter_version",
            "adapter_key",
            "framework",
            "task_kind",
        ):
            batch_op.drop_column(column)

    with op.batch_alter_table("base_models") as batch_op:
        batch_op.drop_constraint(
            "uq_base_models_framework_family_task_scale",
            type_="unique",
        )
        for column in (
            "artifact_metadata",
            "artifact_format",
            "variant",
            "model_family",
            "framework",
        ):
            batch_op.drop_column(column)
        batch_op.create_unique_constraint(
            "uq_base_models_family",
            ["family", "task", "scale"],
        )

    where = sa.text("training_job_id IS NOT NULL")
    op.create_index(
        "uq_trained_models_training_job",
        "trained_models",
        ["training_job_id"],
        unique=True,
        postgresql_where=where,
        sqlite_where=where,
    )
