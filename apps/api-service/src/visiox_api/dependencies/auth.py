import hmac

import jwt
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from visiox_api.dependencies.database import get_db_session
from visiox_api.services.security import decode_access_token
from visiox_common.settings import Settings, get_settings
from visiox_db.models.identity import ROLE_ADMIN, STATUS_ACTIVE, User


_AUTHENTICATION_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid authentication credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> User:
    if authorization is None:
        raise _AUTHENTICATION_ERROR
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token or " " in token:
        raise _AUTHENTICATION_ERROR

    try:
        claims = decode_access_token(token, settings.read_auth_jwt_secret())
    except (jwt.PyJWTError, ValueError):
        raise _AUTHENTICATION_ERROR from None

    user = session.get(User, claims["sub"])
    if (
        user is None
        or user.status != STATUS_ACTIVE
        or not hmac.compare_digest(user.organization_id, claims["org"])
    ):
        raise _AUTHENTICATION_ERROR
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != ROLE_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required"
        )
    return user
