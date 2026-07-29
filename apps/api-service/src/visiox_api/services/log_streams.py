from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import gzip
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from visiox_db.models import LogChunk, LogStream
from visiox_edge_executor_worker.redaction import redact_recursive
from visiox_storage.client import ObjectStorageClient


DEFAULT_CHUNK_BYTES = 256 * 1024


class LogStreamError(RuntimeError):
    pass


class LogStreamNotFound(LogStreamError):
    pass


class LogStreamClosed(LogStreamError):
    pass


class LogStreamExpired(LogStreamError):
    pass


@dataclass(frozen=True)
class LogEntry:
    message: object
    timestamp: datetime | None = None
    source: str | None = None
    level: str | None = None


@dataclass(frozen=True)
class LogReadPage:
    lines: list[dict[str, object]]
    next_cursor: str | None
    has_more: bool
    bytes_read: int


def open_stream(
    session: Session,
    *,
    organization_id: str,
    resource_type: str,
    resource_id: str,
    source: str,
    retention_expires_at: datetime | None = None,
) -> LogStream:
    stream = LogStream(
        organization_id=organization_id,
        resource_type=resource_type,
        resource_id=resource_id,
        source=source,
        status="open",
        encoding="utf-8",
        retention_expires_at=retention_expires_at,
    )
    session.add(stream)
    session.flush()
    return stream


def append_lines(
    session: Session,
    storage: ObjectStorageClient,
    stream_id: str,
    lines: Iterable[LogEntry | str | bytes | dict[str, object]],
    *,
    target_chunk_bytes: int = DEFAULT_CHUNK_BYTES,
) -> list[LogChunk]:
    if target_chunk_bytes <= 0:
        raise ValueError("target_chunk_bytes must be positive")
    stream = _stream_for_update(session, stream_id)
    _ensure_active(stream)
    serialized = [_serialize_entry(stream.source, line) for line in lines]
    if not serialized:
        return []

    groups: list[list[tuple[bytes, datetime | None]]] = []
    current: list[tuple[bytes, datetime | None]] = []
    current_size = 0
    for line, timestamp in serialized:
        if current and current_size + len(line) > target_chunk_bytes:
            groups.append(current)
            current = []
            current_size = 0
        current.append((line, timestamp))
        current_size += len(line)
    if current:
        groups.append(current)

    chunks: list[LogChunk] = []
    byte_offset = stream.total_bytes
    sequence = stream.next_sequence
    for group in groups:
        raw = b"".join(line for line, _ in group)
        compressed = gzip.compress(raw, mtime=0)
        object_name = (
            f"{stream.organization_id}/{stream.resource_type}/{stream.resource_id}/"
            f"{stream.id}/{sequence:012d}.log.gz"
        )
        object_uri = _put_bytes(
            storage,
            "logs",
            object_name,
            compressed,
            content_type="application/gzip",
        )
        timestamps = [timestamp for _, timestamp in group if timestamp is not None]
        chunk = LogChunk(
            stream_id=stream.id,
            sequence=sequence,
            object_uri=object_uri,
            checksum_sha256=hashlib.sha256(compressed).hexdigest(),
            compressed_size_bytes=len(compressed),
            uncompressed_size_bytes=len(raw),
            byte_start=byte_offset,
            byte_end=byte_offset + len(raw),
            line_count=len(group),
            first_timestamp=min(timestamps) if timestamps else None,
            last_timestamp=max(timestamps) if timestamps else None,
        )
        session.add(chunk)
        chunks.append(chunk)
        byte_offset += len(raw)
        sequence += 1

    stream.next_sequence = sequence
    stream.total_bytes = byte_offset
    stream.line_count += len(serialized)
    session.flush()
    return chunks


def read_after_cursor(
    session: Session,
    storage: ObjectStorageClient,
    stream_id: str,
    *,
    cursor: str | None = None,
    max_lines: int = 2_000,
    max_bytes: int = 1024 * 1024,
) -> LogReadPage:
    if max_lines <= 0 or max_bytes <= 0:
        raise ValueError("read limits must be positive")
    stream = session.get(LogStream, stream_id)
    if stream is None:
        raise LogStreamNotFound(stream_id)
    _ensure_retained(stream)
    sequence, line_offset = _parse_cursor(cursor)
    chunks = list(
        session.scalars(
            select(LogChunk)
            .where(LogChunk.stream_id == stream.id, LogChunk.sequence >= sequence)
            .order_by(LogChunk.sequence)
        )
    )

    output: list[dict[str, object]] = []
    bytes_read = 0
    next_cursor: str | None = cursor
    has_more = False
    for chunk_index, chunk in enumerate(chunks):
        raw = _read_gzip_object(storage, chunk.object_uri)
        encoded_lines = raw.splitlines(keepends=True)
        start = line_offset if chunk.sequence == sequence else 0
        for index in range(start, len(encoded_lines)):
            encoded = encoded_lines[index]
            if output and (
                len(output) >= max_lines or bytes_read + len(encoded) > max_bytes
            ):
                return LogReadPage(
                    lines=output,
                    next_cursor=f"{chunk.sequence}:{index}",
                    has_more=True,
                    bytes_read=bytes_read,
                )
            output.append(json.loads(encoded.decode("utf-8")))
            bytes_read += len(encoded)
            next_cursor = f"{chunk.sequence}:{index + 1}"
        next_cursor = f"{chunk.sequence + 1}:0"
        line_offset = 0
        has_more = chunk_index < len(chunks) - 1
    return LogReadPage(
        lines=output,
        next_cursor=next_cursor,
        has_more=has_more,
        bytes_read=bytes_read,
    )


def build_download(
    session: Session,
    storage: ObjectStorageClient,
    stream_id: str,
) -> bytes:
    stream = session.get(LogStream, stream_id)
    if stream is None:
        raise LogStreamNotFound(stream_id)
    _ensure_retained(stream)
    chunks = session.scalars(
        select(LogChunk)
        .where(LogChunk.stream_id == stream.id)
        .order_by(LogChunk.sequence)
    )
    return b"".join(_read_gzip_object(storage, chunk.object_uri) for chunk in chunks)


def close_stream(
    session: Session,
    storage: ObjectStorageClient,
    stream_id: str,
    *,
    status: str,
) -> LogStream:
    if status not in {"completed", "failed", "cancelled"}:
        raise ValueError("invalid terminal log stream status")
    stream = _stream_for_update(session, stream_id)
    if stream.status != "open":
        return stream
    content = build_download(session, storage, stream.id)
    object_name = (
        f"{stream.organization_id}/{stream.resource_type}/{stream.resource_id}/"
        f"{stream.id}/full.log"
    )
    stream.redacted_log_uri = _put_bytes(
        storage,
        "logs",
        object_name,
        content,
        content_type="text/plain; charset=utf-8",
    )
    stream.status = status
    stream.closed_at = datetime.now(UTC)
    session.flush()
    return stream


def _serialize_entry(
    default_source: str,
    value: LogEntry | str | bytes | dict[str, object],
) -> tuple[bytes, datetime | None]:
    entry = value if isinstance(value, LogEntry) else LogEntry(message=value)
    timestamp = entry.timestamp
    payload = {
        "timestamp": timestamp.isoformat() if timestamp else None,
        "source": entry.source or default_source,
        "level": entry.level,
        "message": redact_recursive(entry.message),
    }
    return (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        + b"\n",
        timestamp,
    )


def _stream_for_update(session: Session, stream_id: str) -> LogStream:
    stream = session.scalar(
        select(LogStream).where(LogStream.id == stream_id).with_for_update()
    )
    if stream is None:
        raise LogStreamNotFound(stream_id)
    return stream


def _ensure_active(stream: LogStream) -> None:
    _ensure_retained(stream)
    if stream.status != "open":
        raise LogStreamClosed(stream.id)


def _ensure_retained(stream: LogStream) -> None:
    expires_at = stream.retention_expires_at
    if expires_at is None:
        return
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= datetime.now(UTC):
        raise LogStreamExpired(stream.id)


def _parse_cursor(cursor: str | None) -> tuple[int, int]:
    if cursor is None:
        return 0, 0
    try:
        sequence_text, offset_text = cursor.split(":", 1)
        sequence = int(sequence_text)
        offset = int(offset_text)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("invalid log cursor") from exc
    if sequence < 0 or offset < 0:
        raise ValueError("invalid log cursor")
    return sequence, offset


def _put_bytes(
    storage: ObjectStorageClient,
    bucket: str,
    object_name: str,
    content: bytes,
    *,
    content_type: str,
) -> str:
    with TemporaryDirectory(prefix="visiox-log-") as temp_dir:
        path = Path(temp_dir) / "object"
        path.write_bytes(content)
        return storage.put_file(bucket, object_name, path, content_type=content_type)


def _read_gzip_object(storage: ObjectStorageClient, uri: str) -> bytes:
    bucket, object_name = _parse_storage_uri(uri)
    with TemporaryDirectory(prefix="visiox-log-") as temp_dir:
        path = storage.get_file(bucket, object_name, Path(temp_dir) / "chunk.gz")
        compressed = path.read_bytes()
    return gzip.decompress(compressed)


def _parse_storage_uri(uri: str) -> tuple[str, str]:
    for scheme in ("minio://", "memory://"):
        if uri.startswith(scheme):
            remainder = uri[len(scheme) :]
            if "/" in remainder:
                return tuple(remainder.split("/", 1))  # type: ignore[return-value]
    raise ValueError("unsupported log object URI")
