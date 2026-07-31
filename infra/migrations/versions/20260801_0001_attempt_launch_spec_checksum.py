"""add canonical launch spec checksums to training attempts

Revision ID: 20260801_0001
Revises: 20260731_0001
Create Date: 2026-08-01 00:01:00.000000
"""

from collections.abc import Sequence
import hashlib
import json

from alembic import op
import sqlalchemy as sa


revision: str = "20260801_0001"
down_revision: str | None = "20260731_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BACKFILL_BATCH_SIZE = 500


def _canonical_launch_spec_checksum(launch_spec: object) -> str:
    canonical_json = json.dumps(
        launch_spec,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def upgrade() -> None:
    with op.batch_alter_table("training_job_attempts") as batch_op:
        batch_op.add_column(
            sa.Column("launch_spec_checksum", sa.String(length=128), nullable=True)
        )

    attempts = sa.table(
        "training_job_attempts",
        sa.column("id", sa.String()),
        sa.column("launch_spec", sa.JSON()),
        sa.column("launch_spec_checksum", sa.String()),
    )
    connection = op.get_bind()
    result = connection.execute(
        sa.select(attempts.c.id, attempts.c.launch_spec).order_by(attempts.c.id)
    ).mappings()
    update_statement = (
        sa.update(attempts)
        .where(attempts.c.id == sa.bindparam("_attempt_id"))
        .values(launch_spec_checksum=sa.bindparam("_launch_spec_checksum"))
    )
    while batch := result.fetchmany(BACKFILL_BATCH_SIZE):
        connection.execute(
            update_statement,
            [
                {
                    "_attempt_id": row["id"],
                    "_launch_spec_checksum": _canonical_launch_spec_checksum(
                        row["launch_spec"]
                    ),
                }
                for row in batch
            ],
        )

    with op.batch_alter_table("training_job_attempts") as batch_op:
        batch_op.alter_column(
            "launch_spec_checksum",
            existing_type=sa.String(length=128),
            nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("training_job_attempts") as batch_op:
        batch_op.drop_column("launch_spec_checksum")
