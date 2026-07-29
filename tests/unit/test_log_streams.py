from datetime import UTC, datetime, timedelta
import gzip
import hashlib
import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from visiox_api.services.log_streams import (
    LogEntry,
    LogStreamExpired,
    append_lines,
    build_download,
    close_stream,
    open_stream,
    read_after_cursor,
)
from visiox_db.base import Base
from visiox_db.models import LogChunk, Organization
from visiox_storage.client import InMemoryObjectStorageClient


def _session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(Organization(id="org-1", name="Org", slug="org", status="active"))
    session.commit()
    return session


def _object_bytes(storage: InMemoryObjectStorageClient, uri: str) -> bytes:
    scheme, remainder = uri.split("://", 1)
    assert scheme == "memory"
    bucket, object_name = remainder.split("/", 1)
    return storage.objects[(bucket, object_name)]


def test_log_stream_chunks_are_ordered_redacted_gzipped_and_utf8_safe() -> None:
    session = _session()
    storage = InMemoryObjectStorageClient()
    stream = open_stream(
        session,
        organization_id="org-1",
        resource_type="training_job",
        resource_id="job-1",
        source="training",
    )
    session.commit()

    entries = [
        LogEntry(
            timestamp=datetime(2026, 7, 28, 8, 0, index, tzinfo=UTC),
            source="stdout",
            level="info",
            message=f"第 {index} 行 密码 password=secret-{index} " + "辣" * 180,
        )
        for index in range(24)
    ]
    append_lines(session, storage, stream.id, entries, target_chunk_bytes=1024)
    session.commit()

    chunks = list(
        session.scalars(
            select(LogChunk)
            .where(LogChunk.stream_id == stream.id)
            .order_by(LogChunk.sequence)
        )
    )
    assert len(chunks) > 1
    assert [chunk.sequence for chunk in chunks] == list(range(len(chunks)))
    assert chunks[0].byte_start == 0
    assert all(
        current.byte_end == following.byte_start
        for current, following in zip(chunks, chunks[1:], strict=False)
    )

    decoded_lines: list[dict[str, object]] = []
    for chunk in chunks:
        compressed = _object_bytes(storage, chunk.object_uri)
        assert hashlib.sha256(compressed).hexdigest() == chunk.checksum_sha256
        raw = gzip.decompress(compressed)
        assert len(raw) == chunk.uncompressed_size_bytes
        decoded_lines.extend(
            json.loads(line) for line in raw.decode("utf-8").splitlines()
        )
    assert [line["message"].split(" 行", 1)[0] for line in decoded_lines] == [
        f"第 {index}" for index in range(24)
    ]
    assert all("secret-" not in str(line["message"]) for line in decoded_lines)
    assert all("[REDACTED]" in str(line["message"]) for line in decoded_lines)


def test_cursor_read_close_and_download_preserve_complete_history() -> None:
    session = _session()
    storage = InMemoryObjectStorageClient()
    stream = open_stream(
        session,
        organization_id="org-1",
        resource_type="deployment_service",
        resource_id="service-1",
        source="deployment",
    )
    append_lines(
        session,
        storage,
        stream.id,
        [f"line-{index}" for index in range(8)],
        target_chunk_bytes=100,
    )
    session.commit()

    first_page = read_after_cursor(
        session, storage, stream.id, cursor=None, max_lines=3
    )
    assert [line["message"] for line in first_page.lines] == [
        "line-0",
        "line-1",
        "line-2",
    ]
    assert first_page.next_cursor is not None
    assert first_page.has_more is True
    second_page = read_after_cursor(
        session,
        storage,
        stream.id,
        cursor=first_page.next_cursor,
        max_lines=20,
    )
    assert [line["message"] for line in second_page.lines] == [
        "line-3",
        "line-4",
        "line-5",
        "line-6",
        "line-7",
    ]

    download = build_download(session, storage, stream.id)
    assert [json.loads(line)["message"] for line in download.decode().splitlines()] == [
        f"line-{index}" for index in range(8)
    ]
    close_stream(session, storage, stream.id, status="completed")
    session.commit()
    session.refresh(stream)
    assert stream.status == "completed"
    assert stream.closed_at is not None
    assert stream.redacted_log_uri is not None
    assert _object_bytes(storage, stream.redacted_log_uri) == download


def test_expired_log_stream_cannot_be_read() -> None:
    session = _session()
    storage = InMemoryObjectStorageClient()
    stream = open_stream(
        session,
        organization_id="org-1",
        resource_type="training_job",
        resource_id="job-expired",
        source="training",
        retention_expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    session.commit()

    with pytest.raises(LogStreamExpired):
        read_after_cursor(session, storage, stream.id)
