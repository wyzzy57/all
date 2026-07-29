from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from visiox_api.dependencies.database import get_db_session
from visiox_common.settings import Settings, get_settings
from visiox_common.tasks import TaskStatus, TaskType
from visiox_db.models import LabelProject, LabelSyncEvent, Task


router = APIRouter(prefix="/webhooks/label-studio", tags=["label-studio-webhooks"])
get_label_webhook_session = get_db_session


class LabelWebhookResponse(BaseModel):
    event_id: str
    status: str
    duplicate: bool


@router.post("", response_model=LabelWebhookResponse, status_code=status.HTTP_202_ACCEPTED)
async def receive_label_studio_webhook(
    request: Request,
    x_label_studio_signature: str | None = Header(default=None),
    session: Session = Depends(get_label_webhook_session),
    settings: Settings = Depends(get_settings),
) -> LabelWebhookResponse:
    body = await request.body()
    _verify_signature(body, x_label_studio_signature, settings.label_studio_webhook_secret)
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Webhook payload must be UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Webhook payload must be an object")
    project_external_id = _project_id(payload)
    project = session.scalar(
        select(LabelProject).where(
            LabelProject.provider == "label_studio",
            LabelProject.external_project_id == project_external_id,
        )
    )
    if project is None:
        raise HTTPException(status_code=404, detail="Label project not found")
    event_key = _event_key(payload, body)
    existing = session.scalar(
        select(LabelSyncEvent).where(
            LabelSyncEvent.provider == "label_studio",
            LabelSyncEvent.event_key == event_key,
        )
    )
    if existing is not None:
        return LabelWebhookResponse(event_id=existing.id, status=existing.status, duplicate=True)

    event = LabelSyncEvent(
        label_project_id=project.id,
        provider="label_studio",
        event_key=event_key,
        event_type=str(payload.get("action") or payload.get("event") or "ANNOTATION_UPDATED"),
        status="queued",
        payload_checksum=hashlib.sha256(body).hexdigest(),
    )
    task = Task(
        task_type=TaskType.IMPORT_LABEL_STUDIO_ANNOTATION.value,
        status=TaskStatus.QUEUED.value,
        progress=0,
        resource_type="label_project",
        resource_id=project.id,
        payload={
            "dataset_id": project.dataset_id,
            "label_project_id": project.id,
            "label_sync_event_id": event.id,
        },
    )
    session.add_all([event, task])
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(
            select(LabelSyncEvent).where(
                LabelSyncEvent.provider == "label_studio",
                LabelSyncEvent.event_key == event_key,
            )
        )
        if existing is None:
            raise
        return LabelWebhookResponse(event_id=existing.id, status=existing.status, duplicate=True)
    return LabelWebhookResponse(event_id=event.id, status=event.status, duplicate=False)


def _verify_signature(body: bytes, supplied: str | None, secret: str) -> None:
    if not secret:
        raise HTTPException(status_code=503, detail="Label Studio webhook secret is not configured")
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    candidate = (supplied or "").removeprefix("sha256=")
    if not hmac.compare_digest(candidate, expected):
        raise HTTPException(status_code=401, detail="Invalid Label Studio webhook signature")


def _project_id(payload: dict[str, Any]) -> str:
    project = payload.get("project")
    if isinstance(project, dict):
        project = project.get("id")
    if project is None and isinstance(payload.get("task"), dict):
        project = payload["task"].get("project")
    if project is None:
        raise HTTPException(status_code=400, detail="Webhook project ID is missing")
    return str(project)


def _event_key(payload: dict[str, Any], body: bytes) -> str:
    explicit = payload.get("event_id") or payload.get("id")
    if explicit is not None:
        return str(explicit)
    return hashlib.sha256(body).hexdigest()
