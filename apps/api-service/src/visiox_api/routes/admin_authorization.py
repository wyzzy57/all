import base64
import binascii
import hashlib
import hmac
import json
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from visiox_api.dependencies.auth import require_admin
from visiox_api.dependencies.database import get_db_session
from visiox_api.schemas.identity import (
    AuditLogListResponse,
    AuditLogResponse,
    ResourceAllocationListResponse,
    ResourceAllocationResponse,
    ResourceAllocationUpsertRequest,
    ResourceGrantListResponse,
    ResourceGrantResponse,
    ResourceGrantUpsertRequest,
)
from visiox_api.services.audit import record_audit, redact_audit_metadata
from visiox_common.settings import Settings, get_settings
from visiox_db.models.edge_compute import ResourcePool
from visiox_db.models.identity import (
    AUDIT_RESULT_SUCCESS,
    PRINCIPAL_GROUP,
    PRINCIPAL_ORGANIZATION,
    PRINCIPAL_USER,
    STATUS_DELETED,
    AuditLog,
    ResourceAllocationPolicy,
    ResourceGrant,
    User,
    UserGroup,
)


router = APIRouter(
    prefix="/admin",
    tags=["admin-authorization"],
    dependencies=[Depends(require_admin)],
)


def _normalize_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _encode_audit_cursor(created_at: datetime, audit_log_id: str, secret: bytes) -> str:
    normalized_created_at = _normalize_utc(created_at)
    assert normalized_created_at is not None
    payload = json.dumps(
        {"created_at": normalized_created_at.isoformat(), "id": audit_log_id},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    signature = hmac.new(secret, payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(payload + signature).rstrip(b"=").decode("ascii")


def _decode_audit_cursor(cursor: str, secret: bytes) -> tuple[datetime, str]:
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = base64.b64decode(
            cursor + padding,
            altchars=b"-_",
            validate=True,
        )
        if len(decoded) <= hashlib.sha256().digest_size:
            raise ValueError
        payload_bytes = decoded[: -hashlib.sha256().digest_size]
        signature = decoded[-hashlib.sha256().digest_size :]
        expected_signature = hmac.new(secret, payload_bytes, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected_signature):
            raise ValueError
        payload = json.loads(payload_bytes)
        if not isinstance(payload, dict) or set(payload) != {"created_at", "id"}:
            raise ValueError
        created_at_value = payload["created_at"]
        audit_log_id = payload["id"]
        if not isinstance(created_at_value, str) or not isinstance(audit_log_id, str):
            raise ValueError
        created_at = datetime.fromisoformat(created_at_value)
        if created_at.utcoffset() is None or str(UUID(audit_log_id)) != audit_log_id:
            raise ValueError
    except (
        binascii.Error,
        json.JSONDecodeError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid audit log cursor",
        ) from None
    normalized_created_at = _normalize_utc(created_at)
    assert normalized_created_at is not None
    return normalized_created_at, audit_log_id


def _validate_principal(
    session: Session,
    actor: User,
    principal_type: str,
    principal_id: str,
    *,
    allow_organization: bool,
) -> None:
    valid = False
    if principal_type == PRINCIPAL_USER:
        user = session.get(User, principal_id)
        valid = (
            user is not None
            and user.organization_id == actor.organization_id
            and user.status != STATUS_DELETED
        )
    elif principal_type == PRINCIPAL_GROUP:
        group = session.get(UserGroup, principal_id)
        valid = group is not None and group.organization_id == actor.organization_id
    elif allow_organization and principal_type == PRINCIPAL_ORGANIZATION:
        valid = principal_id == actor.organization_id
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Principal does not belong to the organization",
        )


def _commit_or_conflict(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Authorization policy conflicts with an existing policy",
        ) from None


@router.get("/resource-grants", response_model=ResourceGrantListResponse)
def list_resource_grants(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    session: Session = Depends(get_db_session),
    actor: User = Depends(require_admin),
) -> ResourceGrantListResponse:
    predicate = ResourceGrant.organization_id == actor.organization_id
    total = (
        session.scalar(select(func.count()).select_from(ResourceGrant).where(predicate))
        or 0
    )
    grants = list(
        session.scalars(
            select(ResourceGrant)
            .where(predicate)
            .order_by(ResourceGrant.created_at, ResourceGrant.id)
            .offset(offset)
            .limit(limit)
        )
    )
    return ResourceGrantListResponse(
        items=grants, total=total, offset=offset, limit=limit
    )


@router.put("/resource-grants", response_model=ResourceGrantResponse)
def upsert_resource_grant(
    payload: ResourceGrantUpsertRequest,
    request: Request,
    session: Session = Depends(get_db_session),
    actor: User = Depends(require_admin),
) -> ResourceGrant:
    _validate_principal(
        session,
        actor,
        payload.principal_type,
        payload.principal_id,
        allow_organization=True,
    )
    grant = session.scalar(
        select(ResourceGrant).where(
            ResourceGrant.organization_id == actor.organization_id,
            ResourceGrant.resource_type == payload.resource_type,
            ResourceGrant.resource_id == payload.resource_id,
            ResourceGrant.principal_type == payload.principal_type,
            ResourceGrant.principal_id == payload.principal_id,
        )
    )
    action = "admin.resource_grant.update"
    if grant is None:
        action = "admin.resource_grant.create"
        grant = ResourceGrant(
            organization_id=actor.organization_id,
            resource_type=payload.resource_type,
            resource_id=payload.resource_id,
            principal_type=payload.principal_type,
            principal_id=payload.principal_id,
            created_by=actor.id,
        )
        session.add(grant)
    grant.permissions = list(dict.fromkeys(payload.permissions))
    grant.expires_at = payload.expires_at
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Authorization policy conflicts with an existing policy",
        ) from None
    record_audit(
        session,
        actor,
        action,
        "resource_grant",
        grant.id,
        AUDIT_RESULT_SUCCESS,
        request.headers.get("x-request-id"),
        {
            "principal_type": grant.principal_type,
            "permission_count": len(grant.permissions),
        },
    )
    _commit_or_conflict(session)
    return grant


@router.delete("/resource-grants/{grant_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_resource_grant(
    grant_id: str,
    request: Request,
    session: Session = Depends(get_db_session),
    actor: User = Depends(require_admin),
) -> None:
    grant = session.scalar(
        select(ResourceGrant).where(
            ResourceGrant.id == grant_id,
            ResourceGrant.organization_id == actor.organization_id,
        )
    )
    if grant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Grant not found"
        )
    session.delete(grant)
    record_audit(
        session,
        actor,
        "admin.resource_grant.delete",
        "resource_grant",
        grant.id,
        AUDIT_RESULT_SUCCESS,
        request.headers.get("x-request-id"),
        {},
    )
    session.commit()


@router.get("/resource-allocations", response_model=ResourceAllocationListResponse)
def list_resource_allocations(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    session: Session = Depends(get_db_session),
    actor: User = Depends(require_admin),
) -> ResourceAllocationListResponse:
    predicate = ResourceAllocationPolicy.organization_id == actor.organization_id
    total = (
        session.scalar(
            select(func.count()).select_from(ResourceAllocationPolicy).where(predicate)
        )
        or 0
    )
    policies = list(
        session.scalars(
            select(ResourceAllocationPolicy)
            .where(predicate)
            .order_by(ResourceAllocationPolicy.created_at, ResourceAllocationPolicy.id)
            .offset(offset)
            .limit(limit)
        )
    )
    return ResourceAllocationListResponse(
        items=policies, total=total, offset=offset, limit=limit
    )


@router.put("/resource-allocations", response_model=ResourceAllocationResponse)
def upsert_resource_allocation(
    payload: ResourceAllocationUpsertRequest,
    request: Request,
    session: Session = Depends(get_db_session),
    actor: User = Depends(require_admin),
) -> ResourceAllocationPolicy:
    _validate_principal(
        session,
        actor,
        payload.principal_type,
        payload.principal_id,
        allow_organization=False,
    )
    if session.get(ResourcePool, payload.resource_pool_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource pool not found"
        )
    policy = session.scalar(
        select(ResourceAllocationPolicy).where(
            ResourceAllocationPolicy.organization_id == actor.organization_id,
            ResourceAllocationPolicy.principal_type == payload.principal_type,
            ResourceAllocationPolicy.principal_id == payload.principal_id,
            ResourceAllocationPolicy.resource_pool_id == payload.resource_pool_id,
        )
    )
    action = "admin.resource_allocation.update"
    if policy is None:
        action = "admin.resource_allocation.create"
        policy = ResourceAllocationPolicy(
            organization_id=actor.organization_id,
            principal_type=payload.principal_type,
            principal_id=payload.principal_id,
            resource_pool_id=payload.resource_pool_id,
            created_by=actor.id,
        )
        session.add(policy)
    policy.max_concurrent_training_jobs = payload.max_concurrent_training_jobs
    policy.max_gpu_count = payload.max_gpu_count
    policy.max_service_instances = payload.max_service_instances
    policy.expires_at = payload.expires_at
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Authorization policy conflicts with an existing policy",
        ) from None
    record_audit(
        session,
        actor,
        action,
        "resource_allocation",
        policy.id,
        AUDIT_RESULT_SUCCESS,
        request.headers.get("x-request-id"),
        {
            "principal_type": policy.principal_type,
            "resource_pool_id": policy.resource_pool_id,
        },
    )
    _commit_or_conflict(session)
    return policy


@router.delete(
    "/resource-allocations/{policy_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_resource_allocation(
    policy_id: str,
    request: Request,
    session: Session = Depends(get_db_session),
    actor: User = Depends(require_admin),
) -> None:
    policy = session.scalar(
        select(ResourceAllocationPolicy).where(
            ResourceAllocationPolicy.id == policy_id,
            ResourceAllocationPolicy.organization_id == actor.organization_id,
        )
    )
    if policy is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Allocation policy not found"
        )
    session.delete(policy)
    record_audit(
        session,
        actor,
        "admin.resource_allocation.delete",
        "resource_allocation",
        policy.id,
        AUDIT_RESULT_SUCCESS,
        request.headers.get("x-request-id"),
        {},
    )
    session.commit()


@router.get("/audit-logs", response_model=AuditLogListResponse)
def list_audit_logs(
    actor_user_id: str | None = Query(default=None, max_length=36),
    resource_type: str | None = Query(default=None, max_length=80),
    resource_id: str | None = Query(default=None, max_length=36),
    action: str | None = Query(default=None, max_length=120),
    result: str | None = Query(default=None, max_length=32),
    created_from: datetime | None = Query(default=None),
    created_to: datetime | None = Query(default=None),
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_db_session),
    actor: User = Depends(require_admin),
    settings: Settings = Depends(get_settings),
) -> AuditLogListResponse:
    created_from = _normalize_utc(created_from)
    created_to = _normalize_utc(created_to)
    if created_from is not None and created_to is not None and created_from > created_to:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="created_from must not be later than created_to",
        )
    predicates = [AuditLog.organization_id == actor.organization_id]
    filters = (
        (AuditLog.actor_user_id, actor_user_id),
        (AuditLog.resource_type, resource_type),
        (AuditLog.resource_id, resource_id),
        (AuditLog.action, action),
        (AuditLog.result, result),
    )
    predicates.extend(column == value for column, value in filters if value is not None)
    if created_from is not None:
        predicates.append(AuditLog.created_at >= created_from)
    if created_to is not None:
        predicates.append(AuditLog.created_at < created_to)
    total = (
        session.scalar(select(func.count()).select_from(AuditLog).where(*predicates)) or 0
    )
    page_predicates = list(predicates)
    secret = settings.read_auth_jwt_secret()
    if cursor is not None:
        cursor_created_at, cursor_id = _decode_audit_cursor(cursor, secret)
        page_predicates.append(
            or_(
                AuditLog.created_at < cursor_created_at,
                and_(
                    AuditLog.created_at == cursor_created_at,
                    AuditLog.id < cursor_id,
                ),
            )
        )
    logs = list(
        session.scalars(
            select(AuditLog)
            .where(*page_predicates)
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(limit + 1)
        )
    )
    has_next_page = len(logs) > limit
    logs = logs[:limit]
    items = [
        AuditLogResponse.model_validate(log).model_copy(
            update={"metadata_json": redact_audit_metadata(log.metadata_json)}
        )
        for log in logs
    ]
    next_cursor = None
    if has_next_page:
        last_log = logs[-1]
        next_cursor = _encode_audit_cursor(last_log.created_at, last_log.id, secret)
    return AuditLogListResponse(
        items=items,
        total=total,
        next_cursor=next_cursor,
    )
