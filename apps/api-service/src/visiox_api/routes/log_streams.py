from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from visiox_api.dependencies.auth import get_current_user
from visiox_api.dependencies.authorization import require_resource_permission
from visiox_api.dependencies.database import get_db_session
from visiox_api.services.log_streams import (
    LogStreamExpired,
    build_download,
    read_after_cursor,
)
from visiox_db.models import LogStream, RemoteExecution, User
from visiox_db.models.identity import PERMISSION_VIEW
from visiox_storage.client import ObjectStorageClient


router = APIRouter(prefix="/log-streams", tags=["log-streams"])


class LogStreamResponse(BaseModel):
    id: str
    organization_id: str
    resource_type: str
    resource_id: str
    source: str
    status: str
    encoding: str
    next_sequence: int
    total_bytes: int
    line_count: int
    redacted_log_uri: str | None
    retention_expires_at: str | None
    closed_at: str | None
    created_at: str
    updated_at: str


class LogChunkPageResponse(BaseModel):
    lines: list[dict[str, object]]
    next_cursor: str | None
    has_more: bool
    bytes_read: int


def get_log_storage(request: Request) -> ObjectStorageClient:
    storage = getattr(request.app.state, "object_storage", None)
    if storage is None:
        raise RuntimeError("Object storage is not configured")
    return storage


@router.get("/{stream_id}", response_model=LogStreamResponse)
def get_log_stream(
    stream_id: str,
    session: Session = Depends(get_db_session),
    actor: User = Depends(get_current_user),
) -> LogStreamResponse:
    stream = _authorized_stream(session, actor, stream_id)
    return _response(stream)


@router.get("/{stream_id}/chunks", response_model=LogChunkPageResponse)
def get_log_chunks(
    stream_id: str,
    cursor: str | None = Query(default=None),
    max_lines: int = Query(default=2_000, ge=1, le=2_000),
    max_bytes: int = Query(default=1024 * 1024, ge=1, le=1024 * 1024),
    session: Session = Depends(get_db_session),
    actor: User = Depends(get_current_user),
    storage: ObjectStorageClient = Depends(get_log_storage),
) -> LogChunkPageResponse:
    _authorized_stream(session, actor, stream_id)
    try:
        page = read_after_cursor(
            session,
            storage,
            stream_id,
            cursor=cursor,
            max_lines=max_lines,
            max_bytes=max_bytes,
        )
    except LogStreamExpired as exc:
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail="Log stream expired"
        ) from exc
    except (KeyError, FileNotFoundError) as exc:
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail="Log object is unavailable"
        ) from exc
    return LogChunkPageResponse(
        lines=page.lines,
        next_cursor=page.next_cursor,
        has_more=page.has_more,
        bytes_read=page.bytes_read,
    )


@router.get("/{stream_id}/events")
def stream_log_events(
    stream_id: str,
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    session: Session = Depends(get_db_session),
    actor: User = Depends(get_current_user),
    storage: ObjectStorageClient = Depends(get_log_storage),
) -> StreamingResponse:
    _authorized_stream(session, actor, stream_id)

    async def events() -> AsyncIterator[str]:
        cursor = last_event_id
        heartbeat_elapsed = 0
        while not await request.is_disconnected():
            stream = session.get(LogStream, stream_id)
            if stream is None:
                yield "event: end\ndata: {}\n\n"
                return
            try:
                page = read_after_cursor(
                    session,
                    storage,
                    stream_id,
                    cursor=cursor,
                )
            except (LogStreamExpired, KeyError, FileNotFoundError):
                yield "event: unavailable\ndata: {}\n\n"
                return
            if page.lines:
                cursor = page.next_cursor
                data = json.dumps(
                    {"lines": page.lines, "cursor": cursor},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                yield f"id: {cursor}\nevent: lines\ndata: {data}\n\n"
                heartbeat_elapsed = 0
                continue
            if stream.status != "open":
                yield f"id: {cursor or ''}\nevent: end\ndata: {{}}\n\n"
                return
            await asyncio.sleep(1)
            session.expire(stream)
            heartbeat_elapsed += 1
            if heartbeat_elapsed >= 15:
                yield ": heartbeat\n\n"
                heartbeat_elapsed = 0

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{stream_id}/download")
def download_log_stream(
    stream_id: str,
    session: Session = Depends(get_db_session),
    actor: User = Depends(get_current_user),
    storage: ObjectStorageClient = Depends(get_log_storage),
) -> Response:
    stream = _authorized_stream(session, actor, stream_id)
    try:
        content = build_download(session, storage, stream.id)
    except LogStreamExpired as exc:
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail="Log stream expired"
        ) from exc
    except (KeyError, FileNotFoundError) as exc:
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail="Log object is unavailable"
        ) from exc
    filename = f"{stream.source}-{stream.id}.log"
    return Response(
        content=content,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _authorized_stream(session: Session, actor: User, stream_id: str) -> LogStream:
    stream = session.get(LogStream, stream_id)
    if stream is None or stream.organization_id != actor.organization_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Log stream not found"
        )
    resource_type, resource_id = _authorization_parent(session, stream)
    require_resource_permission(
        session,
        actor,
        resource_type,
        resource_id,
        PERMISSION_VIEW,
    )
    return stream


def _authorization_parent(session: Session, stream: LogStream) -> tuple[str, str]:
    aliases = {
        "deployment_service": "service",
        "service": "service",
        "training_job": "training_job",
        "pipeline": "pipeline",
        "node": "node",
    }
    resource_type = aliases.get(stream.resource_type)
    if resource_type is not None:
        return resource_type, stream.resource_id
    if stream.resource_type == "remote_execution":
        execution = session.get(RemoteExecution, stream.resource_id)
        if execution is None:
            raise HTTPException(status_code=404, detail="Log parent not found")
        if execution.training_job_id is not None:
            return "training_job", execution.training_job_id
        if execution.deployment_service_id is not None:
            return "service", execution.deployment_service_id
        if execution.node_id is not None:
            return "node", execution.node_id
    raise HTTPException(status_code=404, detail="Log parent not found")


def _response(stream: LogStream) -> LogStreamResponse:
    return LogStreamResponse(
        id=stream.id,
        organization_id=stream.organization_id,
        resource_type=stream.resource_type,
        resource_id=stream.resource_id,
        source=stream.source,
        status=stream.status,
        encoding=stream.encoding,
        next_sequence=stream.next_sequence,
        total_bytes=stream.total_bytes,
        line_count=stream.line_count,
        redacted_log_uri=stream.redacted_log_uri,
        retention_expires_at=(
            stream.retention_expires_at.isoformat()
            if stream.retention_expires_at is not None
            else None
        ),
        closed_at=stream.closed_at.isoformat()
        if stream.closed_at is not None
        else None,
        created_at=stream.created_at.isoformat(),
        updated_at=stream.updated_at.isoformat(),
    )
