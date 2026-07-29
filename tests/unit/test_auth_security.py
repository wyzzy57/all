import hashlib
import re
from datetime import timedelta

import jwt
import pytest

from visiox_api.services.security import (
    decode_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    issue_access_token,
    verify_password,
)


def test_password_hash_and_verify_round_trip() -> None:
    password_hash = hash_password("S3cure-password")

    assert password_hash.startswith("$argon2id$")
    assert verify_password(password_hash, "S3cure-password") is True
    assert verify_password(password_hash, "wrong") is False


def test_password_verification_rejects_malformed_hash() -> None:
    assert verify_password("not-an-argon2-hash", "S3cure-password") is False


def test_access_token_contains_required_claims() -> None:
    secret = b"s" * 32
    token = issue_access_token(
        secret=secret,
        user_id="user-1",
        organization_id="org-1",
        role="member",
        lifetime=timedelta(minutes=15),
    )

    claims = decode_access_token(token, secret)

    assert jwt.get_unverified_header(token)["alg"] == "HS256"
    assert claims["sub"] == "user-1"
    assert claims["org"] == "org-1"
    assert claims["role"] == "member"
    assert claims["iss"] == "visiox"
    assert claims["aud"] == "visiox-api"
    assert claims["exp"] - claims["iat"] == 15 * 60
    assert isinstance(claims["jti"], str)
    assert claims["jti"]


def test_access_token_rejects_expired_token() -> None:
    secret = b"s" * 32
    token = issue_access_token(
        secret=secret,
        user_id="user-1",
        organization_id="org-1",
        role="member",
        lifetime=timedelta(seconds=-1),
    )

    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(token, secret)


def test_access_token_rejects_invalid_signature() -> None:
    token = issue_access_token(
        secret=b"s" * 32,
        user_id="user-1",
        organization_id="org-1",
        role="member",
        lifetime=timedelta(minutes=15),
    )

    with pytest.raises(jwt.InvalidSignatureError):
        decode_access_token(token, b"x" * 32)


def _custom_claim_token(secret: bytes, **overrides: object) -> str:
    claims = {
        "sub": "user-1",
        "org": "org-1",
        "role": "member",
        "iat": 1,
        "exp": 4_102_444_800,
        "jti": "token-1",
        "iss": "visiox",
        "aud": "visiox-api",
        **overrides,
    }
    return jwt.encode(claims, secret, algorithm="HS256")


@pytest.mark.parametrize("claim_name", ["sub", "org", "role", "jti"])
def test_access_token_rejects_missing_custom_claim(claim_name: str) -> None:
    secret = b"s" * 32
    claims = jwt.decode(
        _custom_claim_token(secret),
        secret,
        algorithms=["HS256"],
        options={"verify_aud": False},
    )
    del claims[claim_name]
    token = jwt.encode(claims, secret, algorithm="HS256")

    with pytest.raises(jwt.MissingRequiredClaimError):
        decode_access_token(token, secret)


@pytest.mark.parametrize("claim_name", ["sub", "org", "role", "jti"])
@pytest.mark.parametrize("invalid_value", ["", 123], ids=["empty", "wrong-type"])
def test_access_token_rejects_invalid_custom_claim(
    claim_name: str,
    invalid_value: object,
) -> None:
    secret = b"s" * 32
    token = _custom_claim_token(secret, **{claim_name: invalid_value})

    with pytest.raises(jwt.InvalidTokenError, match="^Access token claims are invalid$"):
        decode_access_token(token, secret)


@pytest.mark.parametrize(
    ("overrides", "error_type"),
    [
        ({"iss": "other-issuer"}, jwt.InvalidIssuerError),
        ({"aud": "other-audience"}, jwt.InvalidAudienceError),
    ],
    ids=["issuer", "audience"],
)
def test_access_token_rejects_invalid_registered_claims(
    overrides: dict[str, str],
    error_type: type[jwt.InvalidTokenError],
) -> None:
    secret = b"s" * 32
    claims = {
        "sub": "user-1",
        "org": "org-1",
        "role": "member",
        "iat": 1,
        "exp": 4_102_444_800,
        "jti": "token-1",
        "iss": "visiox",
        "aud": "visiox-api",
        **overrides,
    }
    token = jwt.encode(claims, secret, algorithm="HS256")

    with pytest.raises(error_type):
        decode_access_token(token, secret)


def test_refresh_tokens_are_opaque_long_and_random() -> None:
    first = generate_refresh_token()
    second = generate_refresh_token()

    assert len(first) >= 64
    assert re.fullmatch(r"[A-Za-z0-9_-]+", first)
    assert first != second


def test_refresh_token_hash_is_deterministic_sha256_hex() -> None:
    token = "opaque-refresh-token"
    expected = hashlib.sha256(token.encode()).hexdigest()

    assert hash_refresh_token(token) == expected
    assert hash_refresh_token(token) == hash_refresh_token(token)
