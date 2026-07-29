from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
import re

from sqlalchemy.orm import Session

from visiox_api.services.log_streams import (
    LogEntry,
    append_lines,
    close_stream,
    open_stream,
)
from visiox_db.models import LogStream
from visiox_storage.client import ObjectStorageClient


_CONTAINER_ID = re.compile(r"[0-9a-f]{12,64}")
_DOCKER_SINCE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})"
)


@dataclass(frozen=True)
class CapturedOutput:
    timestamp: datetime | None
    source: str
    message: object
    level: str | None = None


class DurableLogCapture:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        storage: ObjectStorageClient,
        *,
        notifier: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._storage = storage
        self._notifier = notifier

    def open(
        self,
        *,
        organization_id: str,
        resource_type: str,
        resource_id: str,
        source: str,
        retention_expires_at: datetime | None = None,
    ) -> str:
        with self._session_factory() as session:
            stream = open_stream(
                session,
                organization_id=organization_id,
                resource_type=resource_type,
                resource_id=resource_id,
                source=source,
                retention_expires_at=retention_expires_at,
            )
            stream_id = stream.id
            session.commit()
        return stream_id

    def append(
        self,
        stream_id: str,
        output: Iterable[CapturedOutput],
    ) -> int | None:
        entries = [
            LogEntry(
                timestamp=item.timestamp,
                source=item.source,
                level=item.level,
                message=item.message,
            )
            for item in output
        ]
        if not entries:
            return None
        with self._session_factory() as session:
            chunks = append_lines(session, self._storage, stream_id, entries)
            session.commit()
        last_sequence = chunks[-1].sequence if chunks else None
        if last_sequence is not None and self._notifier is not None:
            self._notifier({"stream_id": stream_id, "last_sequence": last_sequence})
        return last_sequence

    def capture_text(
        self,
        stream_id: str,
        *,
        stdout: str | bytes = "",
        stderr: str | bytes = "",
        timestamp: datetime | None = None,
    ) -> int | None:
        events: list[CapturedOutput] = []
        for source, value in (("stdout", stdout), ("stderr", stderr)):
            text = (
                value.decode("utf-8", errors="replace")
                if isinstance(value, bytes)
                else value
            )
            events.extend(
                CapturedOutput(timestamp, source, line) for line in text.splitlines()
            )
        return self.append(stream_id, events)

    def close(self, stream_id: str, *, status: str) -> str | None:
        with self._session_factory() as session:
            stream = close_stream(session, self._storage, stream_id, status=status)
            uri = stream.redacted_log_uri
            session.commit()
        return uri

    def stream(self, stream_id: str) -> LogStream | None:
        with self._session_factory() as session:
            return session.get(LogStream, stream_id)


def docker_logs_command(container_id: str, *, since: str | None = None) -> str:
    if _CONTAINER_ID.fullmatch(container_id) is None:
        raise ValueError("container identifier is invalid")
    command = "docker logs --timestamps"
    if since is not None:
        if _DOCKER_SINCE.fullmatch(since) is None:
            raise ValueError("Docker log cursor is invalid")
        command += f" --since {since}"
    return f"{command} {container_id}"
