from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from visiox_api.dependencies.auth import get_current_user
from visiox_api.dependencies.database import get_db_session
from visiox_api.schemas.identity import (
    PasswordChangeRequest,
    ProfileUpdateRequest,
    UserResponse,
)
from visiox_api.services.audit import record_audit
from visiox_api.services.security import hash_password, verify_password
from visiox_db.models.identity import AUDIT_RESULT_SUCCESS, User, UserSession


router = APIRouter(prefix="/account", tags=["account"])


@router.patch("/profile", response_model=UserResponse)
def update_profile(
    payload: ProfileUpdateRequest,
    request: Request,
    session: Session = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> User:
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(user, field, value)
    record_audit(
        session,
        user,
        "account.profile.update",
        "user",
        user.id,
        AUDIT_RESULT_SUCCESS,
        request.headers.get("x-request-id"),
        {"fields": sorted(changes)},
    )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already exists"
        ) from None
    return user


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    session: Session = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> None:
    if not verify_password(user.password_hash, payload.current_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    session.execute(
        update(UserSession)
        .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    record_audit(
        session,
        user,
        "account.password.change",
        "user",
        user.id,
        AUDIT_RESULT_SUCCESS,
        request.headers.get("x-request-id"),
        {},
    )
    session.commit()
