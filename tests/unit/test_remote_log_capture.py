from datetime import UTC, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from visiox_db.base import Base
from visiox_db.models import LogChunk, Organization
from visiox_edge_executor_worker.log_capture import (
    CapturedOutput,
    DurableLogCapture,
    docker_logs_command,
)
from visiox_storage.client import InMemoryObjectStorageClient


def _factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as session:
        session.add(Organization(id="org-1", name="Org", slug="org", status="active"))
        session.commit()
    return factory


def test_capture_preserves_interleaved_sources_and_publishes_metadata_only() -> None:
    factory = _factory()
    storage = InMemoryObjectStorageClient()
    notifications: list[dict[str, object]] = []
    capture = DurableLogCapture(factory, storage, notifier=notifications.append)
    stream_id = capture.open(
        organization_id="org-1",
        resource_type="remote_execution",
        resource_id="execution-1",
        source="remote_execution",
    )

    capture.append(
        stream_id,
        [
            CapturedOutput(datetime(2026, 7, 28, 1, 0, 0, tzinfo=UTC), "stdout", "one"),
            CapturedOutput(datetime(2026, 7, 28, 1, 0, 1, tzinfo=UTC), "stderr", "two"),
            CapturedOutput(
                datetime(2026, 7, 28, 1, 0, 2, tzinfo=UTC), "stdout", "token=secret"
            ),
        ],
    )
    uri = capture.close(stream_id, status="completed")

    assert uri is not None
    assert notifications
    assert set(notifications[-1]) == {"stream_id", "last_sequence"}
    assert "secret" not in repr(notifications)
    with factory() as session:
        chunks = list(
            session.scalars(
                select(LogChunk)
                .where(LogChunk.stream_id == stream_id)
                .order_by(LogChunk.sequence)
            )
        )
    assert chunks


def test_docker_logs_command_uses_validated_container_and_since_cursor() -> None:
    command = docker_logs_command(
        "a" * 64,
        since="2026-07-28T08:00:00.000000Z",
    )

    assert command == (
        "docker logs --timestamps --since 2026-07-28T08:00:00.000000Z " + "a" * 64
    )


def test_docker_logs_command_rejects_shell_input() -> None:
    try:
        docker_logs_command("container; rm -rf /")
    except ValueError as error:
        assert "container" in str(error)
    else:
        raise AssertionError("unsafe container identifier was accepted")
