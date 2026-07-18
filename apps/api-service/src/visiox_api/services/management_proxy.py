from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from visiox_common.settings import Settings, get_settings


MANAGEMENT_PROXY_AUTH_TOKEN_HEADER = "X-Visiox-Management-Proxy-Token"


def require_management_proxy(
    proxy_token: Annotated[
        str | None,
        Header(alias=MANAGEMENT_PROXY_AUTH_TOKEN_HEADER, include_in_schema=False),
    ] = None,
    settings: Settings = Depends(get_settings),
) -> None:
    if settings.is_local_environment:
        return
    try:
        expected_token = settings.read_management_proxy_auth_token()
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Management proxy authentication is unavailable",
        ) from None
    if proxy_token is None or not hmac.compare_digest(expected_token, proxy_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Management proxy authentication is required",
        )
