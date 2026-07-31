from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from visiox_db.models import TrainingPipeline


def pipeline_for_update_statement(
    pipeline_id: str,
) -> Select[tuple[TrainingPipeline]]:
    return (
        select(TrainingPipeline)
        .where(TrainingPipeline.id == pipeline_id)
        .with_for_update()
    )


def get_pipeline_for_update(
    session: Session,
    pipeline_id: str,
) -> TrainingPipeline | None:
    return session.scalar(pipeline_for_update_statement(pipeline_id))


def lock_pipeline_to_first_job(
    pipeline: TrainingPipeline,
    job_id: str,
) -> None:
    if pipeline.first_submitted_job_id is None:
        pipeline.first_submitted_job_id = job_id
    if pipeline.framework_locked_at is None:
        pipeline.framework_locked_at = datetime.now(UTC)
