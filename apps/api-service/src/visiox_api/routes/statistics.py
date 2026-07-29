from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from threading import RLock
from typing import Any, Callable

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from visiox_api.dependencies.auth import get_current_user, require_admin
from visiox_api.dependencies.database import get_db_session
from visiox_api.services.statistics import (
    collect_admin_overview_statistics,
    collect_live_resource_statistics,
    collect_scoped_statistics,
)
from visiox_db.models.datasets import Dataset
from visiox_db.models.edge_compute import ComputeNode, ResourcePool
from visiox_db.models.identity import ResourceGrant, User, UserGroupMembership
from visiox_db.models.model_space import (
    DeploymentService,
    TrainingJob,
    TrainingPipeline,
)


router = APIRouter(tags=["statistics"])

_CACHE_TTL = timedelta(seconds=5)
_AUTHORIZATION_MODELS = (
    Dataset,
    TrainingPipeline,
    TrainingJob,
    DeploymentService,
    ComputeNode,
    ResourcePool,
)


@dataclass(frozen=True)
class _CacheEntry:
    expires_at: datetime
    payload: dict[str, Any]


class StatisticsCache:
    """Small copy-safe in-process cache for permission-scoped statistics."""

    def __init__(
        self,
        *,
        now: Callable[[], datetime] | None = None,
        ttl: timedelta = _CACHE_TTL,
        max_entries: int = 512,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self._now = now or (lambda: datetime.now(UTC))
        self._ttl = ttl
        self._max_entries = max_entries
        self._entries: dict[tuple[str, ...], _CacheEntry] = {}
        self._lock = RLock()

    def get_or_create(
        self,
        session: Session,
        actor: User,
        payload_kind: str,
        factory: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        now = _as_utc(self._now())
        key = (
            actor.organization_id,
            actor.id,
            actor.role,
            payload_kind,
            _authorization_revision(session, actor),
        )
        with self._lock:
            self._prune(now)
            entry = self._entries.get(key)
            if entry is not None and entry.expires_at > now:
                return deepcopy(entry.payload)

        payload = factory()
        with self._lock:
            self._prune(now)
            if key not in self._entries and len(self._entries) >= self._max_entries:
                oldest_key = min(
                    self._entries,
                    key=lambda candidate: self._entries[candidate].expires_at,
                )
                self._entries.pop(oldest_key, None)
            self._entries[key] = _CacheEntry(
                expires_at=now + self._ttl,
                payload=deepcopy(payload),
            )
        return deepcopy(payload)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def _prune(self, now: datetime) -> None:
        expired = [key for key, entry in self._entries.items() if entry.expires_at <= now]
        for key in expired:
            self._entries.pop(key, None)


@router.get("/statistics/workbench")
def get_workbench_statistics(
    request: Request,
    session: Session = Depends(get_db_session),
    actor: User = Depends(get_current_user),
) -> dict[str, Any]:
    return _cache(request).get_or_create(
        session,
        actor,
        "workbench-overview",
        lambda: collect_scoped_statistics(session, actor),
    )


@router.get("/statistics/resources")
def get_resource_statistics(
    request: Request,
    session: Session = Depends(get_db_session),
    actor: User = Depends(get_current_user),
) -> dict[str, Any]:
    return _cache(request).get_or_create(
        session,
        actor,
        "workbench-resources",
        lambda: collect_live_resource_statistics(session, actor),
    )


@router.get("/admin/statistics/overview")
def get_admin_overview_statistics(
    request: Request,
    session: Session = Depends(get_db_session),
    actor: User = Depends(require_admin),
) -> dict[str, Any]:
    return _cache(request).get_or_create(
        session,
        actor,
        "admin-overview",
        lambda: collect_admin_overview_statistics(session, actor),
    )


@router.get("/admin/statistics/resources")
def get_admin_resource_statistics(
    request: Request,
    session: Session = Depends(get_db_session),
    actor: User = Depends(require_admin),
) -> dict[str, Any]:
    return _cache(request).get_or_create(
        session,
        actor,
        "admin-resources",
        lambda: collect_live_resource_statistics(session, actor),
    )


def _cache(request: Request) -> StatisticsCache:
    cache = getattr(request.app.state, "statistics_cache", None)
    if cache is None:
        cache = StatisticsCache()
        request.app.state.statistics_cache = cache
    return cache


def _authorization_revision(session: Session, actor: User) -> str:
    """Fingerprint persisted authorization and ownership state without exposing it."""
    parts: list[str] = []
    parts.append(_revision_part(session, ResourceGrant, ResourceGrant.organization_id == actor.organization_id))
    parts.append(_membership_revision(session, actor))
    for model in _AUTHORIZATION_MODELS:
        parts.append(
            _revision_part(session, model, model.organization_id == actor.organization_id)
        )
    return sha256("|".join(parts).encode("utf-8")).hexdigest()


def _membership_revision(session: Session, actor: User) -> str:
    statement = (
        select(func.count(UserGroupMembership.id), func.max(UserGroupMembership.created_at))
        .join_from(UserGroupMembership, User)
        .where(User.organization_id == actor.organization_id)
    )
    return _revision_values(session.execute(statement).one())


def _revision_part(session: Session, model: type, predicate: Any) -> str:
    row = session.execute(
        select(func.count(model.id), func.max(model.updated_at)).where(predicate)
    ).one()
    return _revision_values(row)


def _revision_values(row: tuple[Any, Any]) -> str:
    count, latest = row
    timestamp = _as_utc(latest).isoformat() if isinstance(latest, datetime) else ""
    return f"{int(count or 0)}:{timestamp}"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
