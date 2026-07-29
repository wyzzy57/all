from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from visiox_api.dependencies.auth import get_current_user
from visiox_api.dependencies.database import get_db_session
from visiox_api.schemas.identity import AccessResponse, LoginRequest, UserResponse
from visiox_api.services.security import (
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    issue_access_token,
    verify_password,
)
from visiox_common.settings import Settings, get_settings
from visiox_db.models.identity import STATUS_ACTIVE, User, UserSession


router = APIRouter(prefix="/auth", tags=["auth"])
_DUMMY_PASSWORD_HASH = hash_password(generate_refresh_token())


def _unauthorized(detail: str = "Invalid refresh token") -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _refresh_lifetime(settings: Settings) -> timedelta:
    return timedelta(days=settings.auth_refresh_token_days)


def _set_refresh_cookie(
    response: Response,
    token: str,
    settings: Settings,
) -> None:
    response.set_cookie(
        key=settings.auth_refresh_cookie_name,
        value=token,
        max_age=int(_refresh_lifetime(settings).total_seconds()),
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/auth",
    )


def _delete_refresh_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.auth_refresh_cookie_name,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/auth",
    )


def _refresh_unauthorized_response(settings: Settings) -> JSONResponse:
    response = JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"detail": "Invalid refresh token"},
    )
    _delete_refresh_cookie(response, settings)
    return response


def _access_response(user: User, settings: Settings) -> AccessResponse:
    lifetime = timedelta(minutes=settings.auth_access_token_minutes)
    return AccessResponse(
        access_token=issue_access_token(
            settings.read_auth_jwt_secret(),
            user.id,
            user.organization_id,
            user.role,
            lifetime,
        ),
        expires_in=int(lifetime.total_seconds()),
        user=UserResponse.model_validate(user),
    )


def _new_user_session(
    user: User, request: Request, now: datetime, settings: Settings
) -> tuple[UserSession, str]:
    raw_token = generate_refresh_token()
    client_host = request.client.host if request.client is not None else None
    user_agent = request.headers.get("user-agent")
    return (
        UserSession(
            user_id=user.id,
            refresh_token_hash=hash_refresh_token(raw_token),
            expires_at=now + _refresh_lifetime(settings),
            ip_address=client_host,
            user_agent=user_agent,
        ),
        raw_token,
    )


@router.post("/login", response_model=AccessResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> AccessResponse:
    users = list(session.scalars(select(User).where(User.username == payload.username)))
    user = users[0] if len(users) == 1 and users[0].status == STATUS_ACTIVE else None
    password_hash = user.password_hash if user is not None else _DUMMY_PASSWORD_HASH
    password_valid = verify_password(password_hash, payload.password)
    if user is None or not password_valid:
        raise _unauthorized("Invalid credentials")

    access_response = _access_response(user, settings)
    now = datetime.now(UTC)
    refresh_session, refresh_token = _new_user_session(user, request, now, settings)
    user.last_login_at = now
    session.add(refresh_session)
    session.commit()
    _set_refresh_cookie(response, refresh_token, settings)
    return access_response


@router.get("/me", response_model=UserResponse)
def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.post("/refresh", response_model=AccessResponse)
def refresh(
    request: Request,
    response: Response,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> AccessResponse | JSONResponse:
    raw_token = request.cookies.get(settings.auth_refresh_cookie_name)
    if not raw_token:
        return _refresh_unauthorized_response(settings)

    now = datetime.now(UTC)
    persisted_session = session.scalar(
        select(UserSession)
        .where(UserSession.refresh_token_hash == hash_refresh_token(raw_token))
        .with_for_update()
    )
    if (
        persisted_session is None
        or persisted_session.revoked_at is not None
        or _utc(persisted_session.expires_at) <= now
    ):
        return _refresh_unauthorized_response(settings)

    user = session.get(User, persisted_session.user_id)
    if user is None or user.status != STATUS_ACTIVE:
        return _refresh_unauthorized_response(settings)

    access_response = _access_response(user, settings)
    replacement, replacement_token = _new_user_session(user, request, now, settings)
    persisted_session.revoked_at = now
    session.add(replacement)
    session.commit()
    _set_refresh_cookie(response, replacement_token, settings)
    return access_response


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> None:
    raw_token = request.cookies.get(settings.auth_refresh_cookie_name)
    if raw_token:
        now = datetime.now(UTC)
        persisted_session = session.scalar(
            select(UserSession)
            .where(UserSession.refresh_token_hash == hash_refresh_token(raw_token))
            .with_for_update()
        )
        if (
            persisted_session is not None
            and persisted_session.revoked_at is None
            and _utc(persisted_session.expires_at) > now
        ):
            persisted_session.revoked_at = now
            session.commit()
    _delete_refresh_cookie(response, settings)
