from sqlalchemy.dialects import postgresql, sqlite

from visiox_api.services.pipeline_locking import (
    lock_pipeline_to_first_job,
    pipeline_for_update_statement,
)
from visiox_db.models import TrainingPipeline


def test_pipeline_lock_statement_uses_postgresql_row_lock() -> None:
    statement = pipeline_for_update_statement("pipeline-1")

    compiled = str(statement.compile(dialect=postgresql.dialect()))

    assert "FOR UPDATE" in compiled


def test_pipeline_lock_statement_remains_sqlite_compatible() -> None:
    statement = pipeline_for_update_statement("pipeline-1")

    compiled = str(statement.compile(dialect=sqlite.dialect()))

    assert "FOR UPDATE" not in compiled


def test_pipeline_lock_preserves_first_submitted_job() -> None:
    pipeline = TrainingPipeline(
        name="locked-pipeline",
        engine="yolo26",
        task="detect",
        scale="n",
        first_submitted_job_id="job-first",
    )

    lock_pipeline_to_first_job(pipeline, "job-second")

    assert pipeline.first_submitted_job_id == "job-first"
    assert pipeline.framework_locked_at is not None
