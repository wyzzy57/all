import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError


_password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def issue_access_token(
    secret: bytes,
    user_id: str,
    organization_id: str,
    role: str,
    lifetime: timedelta,
) -> str:
    issued_at = datetime.now(UTC)
    claims = {
        "sub": user_id,
        "org": organization_id,
        "role": role,
        "iat": issued_at,
        "exp": issued_at + lifetime,
        "jti": str(uuid.uuid4()),
        "iss": "visiox",
        "aud": "visiox-api",
    }
    return jwt.encode(claims, secret, algorithm="HS256")


def decode_access_token(token: str, secret: bytes) -> dict[str, Any]:
    claims = jwt.decode(
        token,
        secret,
        algorithms=["HS256"],
        issuer="visiox",
        audience="visiox-api",
        options={
            "require": ["sub", "org", "role", "iat", "exp", "jti", "iss", "aud"],
            "verify_sub": False,
            "verify_jti": False,
        },
    )
    if any(not isinstance(claims[name], str) or not claims[name] for name in ("sub", "org", "role", "jti")):
        raise jwt.InvalidTokenError("Access token claims are invalid")
    return claims


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
