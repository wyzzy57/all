from datetime import UTC, datetime
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from visiox_api.dependencies.auth import require_admin
from visiox_api.dependencies.database import get_db_session
from visiox_api.schemas.identity import (
    PasswordResetRequest,
    PasswordResetResponse,
    Role,
    UserCreateRequest,
    UserCreateResponse,
    UserFilterStatus,
    UserListResponse,
    UserResponse,
    UserUpdateRequest,
)
from visiox_api.services.audit import record_audit
from visiox_api.services.security import hash_password
from visiox_db.models.identity import (
    AUDIT_RESULT_SUCCESS,
    ROLE_ADMIN,
    STATUS_ACTIVE,
    STATUS_DELETED,
    Organization,
    User,
    UserSession,
)


router = APIRouter(
    prefix="/admin/users",
    tags=["admin-users"],
    dependencies=[Depends(require_admin)],
)


def _request_id(request: Request) -> str | None:
    return request.headers.get("x-request-id")


def _user(
    session: Session, actor: User, user_id: str, *, populate_existing: bool = False
) -> User:
    statement = select(User).where(
        User.id == user_id,
        User.organization_id == actor.organization_id,
    )
    if populate_existing:
        statement = statement.execution_options(populate_existing=True)
    user = session.scalar(statement)
    if user is None or user.status == STATUS_DELETED:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    return user


def _lock_organization_and_reload_actor(session: Session, actor: User) -> User:
    # PostgreSQL uses this organization row as the continuity mutex. SQLite ignores
    # FOR UPDATE, so its local suite verifies behavior but cannot emulate lock races.
    organization = session.scalar(
        select(Organization)
        .where(Organization.id == actor.organization_id)
        .with_for_update()
    )
    if organization is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access is no longer active",
        )
    refreshed_actor = session.scalar(
        select(User)
        .where(
            User.id == actor.id,
            User.organization_id == actor.organization_id,
        )
        .execution_options(populate_existing=True)
    )
    if (
        refreshed_actor is None
        or refreshed_actor.role != ROLE_ADMIN
        or refreshed_actor.status != STATUS_ACTIVE
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access is no longer active",
        )
    return refreshed_actor


def _active_admin_count(session: Session, organization_id: str) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(User)
            .where(
                User.organization_id == organization_id,
                User.role == ROLE_ADMIN,
                User.status == STATUS_ACTIVE,
            )
        )
        or 0
    )


def _protect_administrator_continuity(
    actor: User,
    user: User,
    changes: dict[str, object],
    *,
    deleting: bool = False,
    active_admin_count: int,
) -> None:
    is_active_admin = user.role == ROLE_ADMIN and user.status == STATUS_ACTIVE
    resulting_role = changes.get("role", user.role)
    resulting_status = changes.get("status", user.status)
    removes_active_admin = deleting or (
        resulting_role != ROLE_ADMIN or resulting_status != STATUS_ACTIVE
    )
    if is_active_admin and removes_active_admin and active_admin_count <= 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Organization must retain at least one active administrator",
        )

    if user.id == actor.id:
        if deleting:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Administrators cannot delete themselves",
            )
        changes_own_access = (
            "role" in changes and changes["role"] != user.role
        ) or ("status" in changes and changes["status"] != user.status)
        if changes_own_access:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Administrators cannot change their own role or status",
            )


def _revoke_sessions(session: Session, user_id: str) -> None:
    session.execute(
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )


def _commit_or_conflict(session: Session, detail: str) -> None:
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=detail
        ) from None


@router.get("", response_model=UserListResponse)
def list_users(
    search: str | None = Query(default=None, max_length=320),
    status_filter: UserFilterStatus | None = Query(default=None, alias="status"),
    role: Role | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    session: Session = Depends(get_db_session),
    actor: User = Depends(require_admin),
) -> UserListResponse:
    statement = select(User).where(User.organization_id == actor.organization_id)
    if search:
        pattern = f"%{search}%"
        statement = statement.where(
            or_(
                User.username.ilike(pattern),
                User.display_name.ilike(pattern),
                User.email.ilike(pattern),
            )
        )
    if status_filter:
        statement = statement.where(User.status == status_filter)
    if role:
        statement = statement.where(User.role == role)
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    users = list(
        session.scalars(
            statement.order_by(User.username, User.id).offset(offset).limit(limit)
        )
    )
    return UserListResponse(items=users, total=total, offset=offset, limit=limit)


@router.post("", response_model=UserCreateResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreateRequest,
    request: Request,
    session: Session = Depends(get_db_session),
    actor: User = Depends(require_admin),
) -> UserCreateResponse:
    temporary_password = payload.temporary_password or secrets.token_urlsafe(24)
    user = User(
        organization_id=actor.organization_id,
        username=payload.username,
        display_name=payload.display_name,
        email=payload.email,
        password_hash=hash_password(temporary_password),
        role=payload.role,
        status=STATUS_ACTIVE,
        must_change_password=True,
    )
    try:
        session.add(user)
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username or email already exists",
        ) from None
    record_audit(
        session,
        actor,
        "admin.user.create",
        "user",
        user.id,
        AUDIT_RESULT_SUCCESS,
        _request_id(request),
        {"username": user.username, "role": user.role},
    )
    _commit_or_conflict(session, "Username or email already exists")
    return UserCreateResponse(
        **UserResponse.model_validate(user).model_dump(),
        temporary_password=temporary_password,
    )


@router.patch("/{user_id}", response_model=UserResponse)
def update_user(
    user_id: str,
    payload: UserUpdateRequest,
    request: Request,
    session: Session = Depends(get_db_session),
    actor: User = Depends(require_admin),
) -> User:
    changes = payload.model_dump(exclude_unset=True)
    if "role" in changes or "status" in changes:
        actor = _lock_organization_and_reload_actor(session, actor)
    user = _user(
        session,
        actor,
        user_id,
        populate_existing="role" in changes or "status" in changes,
    )
    _protect_administrator_continuity(
        actor,
        user,
        changes,
        active_admin_count=_active_admin_count(session, actor.organization_id),
    )
    old_role = user.role
    old_status = user.status
    for field, value in changes.items():
        setattr(user, field, value)
    if old_status == STATUS_ACTIVE and user.status != STATUS_ACTIVE:
        _revoke_sessions(session, user.id)
    record_audit(
        session,
        actor,
        "admin.user.update",
        "user",
        user.id,
        AUDIT_RESULT_SUCCESS,
        _request_id(request),
        {
            "fields": sorted(changes),
            "role_changed": old_role != user.role,
            "status_changed": old_status != user.status,
        },
    )
    _commit_or_conflict(session, "Email already exists")
    return user


@router.post("/{user_id}/reset-password", response_model=PasswordResetResponse)
def reset_password(
    user_id: str,
    payload: PasswordResetRequest,
    request: Request,
    session: Session = Depends(get_db_session),
    actor: User = Depends(require_admin),
) -> PasswordResetResponse:
    user = _user(session, actor, user_id)
    temporary_password = payload.temporary_password or secrets.token_urlsafe(24)
    user.password_hash = hash_password(temporary_password)
    user.must_change_password = True
    _revoke_sessions(session, user.id)
    record_audit(
        session,
        actor,
        "admin.user.reset_password",
        "user",
        user.id,
        AUDIT_RESULT_SUCCESS,
        _request_id(request),
        {"generated": payload.temporary_password is None},
    )
    session.commit()
    return PasswordResetResponse(
        user=UserResponse.model_validate(user), temporary_password=temporary_password
    )


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: str,
    request: Request,
    session: Session = Depends(get_db_session),
    actor: User = Depends(require_admin),
) -> None:
    actor = _lock_organization_and_reload_actor(session, actor)
    user = _user(session, actor, user_id, populate_existing=True)
    _protect_administrator_continuity(
        actor,
        user,
        {},
        deleting=True,
        active_admin_count=_active_admin_count(session, actor.organization_id),
    )
    user.status = STATUS_DELETED
    _revoke_sessions(session, user.id)
    record_audit(
        session,
        actor,
        "admin.user.delete",
        "user",
        user.id,
        AUDIT_RESULT_SUCCESS,
        _request_id(request),
        {},
    )
    session.commit()
