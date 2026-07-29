from __future__ import annotations

import html
import os
import re
from urllib.parse import quote

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse, Response


UPSTREAM = os.getenv("LABEL_STUDIO_UPSTREAM", "http://label-studio:8080").rstrip("/")
USERNAME = os.getenv("LABEL_STUDIO_USERNAME", "")
PASSWORD = os.getenv("LABEL_STUDIO_PASSWORD", "")
VISIOX_API_INTERNAL_URL = os.getenv("VISIOX_API_INTERNAL_URL", "").rstrip("/")
_CSRF_PATTERN = re.compile(r'name="csrfmiddlewaretoken"\s+value="([^"]+)"')
_HOP_HEADERS = {
    "connection",
    "content-encoding",
    "content-length",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}

app = FastAPI(title="VisioX Label Studio Gateway", docs_url=None, redoc_url=None)


@app.get("/visiox-gateway-health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/visiox-auth")
async def automatic_login(next: str = "/", launch_token: str | None = None) -> RedirectResponse:
    destination = _safe_destination(next)
    if VISIOX_API_INTERNAL_URL:
        if not launch_token:
            raise HTTPException(status_code=401, detail="VisiOX launch token is required")
        async with httpx.AsyncClient(base_url=VISIOX_API_INTERNAL_URL, timeout=10) as visiox:
            exchange = await visiox.post(
                "/internal/label-studio/launch/exchange",
                json={"token": launch_token},
            )
        if exchange.status_code != 200:
            raise HTTPException(status_code=401, detail="VisiOX launch token is invalid or expired")
        destination = _safe_destination(str(exchange.json().get("destination") or "/"))
    if not USERNAME or not PASSWORD:
        raise HTTPException(status_code=503, detail="Label Studio service account is not configured")

    async with httpx.AsyncClient(base_url=UPSTREAM, follow_redirects=False, timeout=20) as client:
        login_page = await client.get(f"/user/login/?next={quote(destination, safe='/')}")
        login_page.raise_for_status()
        match = _CSRF_PATTERN.search(login_page.text)
        if match is None:
            raise HTTPException(status_code=502, detail="Label Studio login form is unavailable")
        login = await client.post(
            f"/user/login/?next={quote(destination, safe='/')}",
            data={
                "csrfmiddlewaretoken": html.unescape(match.group(1)),
                "email": USERNAME,
                "password": PASSWORD,
                "persist_session": "on",
            },
            headers={"Referer": f"{UPSTREAM}/user/login/"},
        )
        if login.status_code not in {301, 302, 303, 307, 308}:
            raise HTTPException(status_code=502, detail="Label Studio automatic login failed")

        response = RedirectResponse(destination, status_code=303)
        csrf = client.cookies.get("csrftoken")
        session = client.cookies.get("sessionid")
        if not csrf or not session:
            raise HTTPException(status_code=502, detail="Label Studio did not create an authenticated session")
        response.set_cookie("csrftoken", csrf, max_age=31_449_600, path="/", samesite="lax")
        response.set_cookie("sessionid", session, max_age=1_209_600, path="/", httponly=True, samesite="lax")
        return response


@app.api_route("/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def proxy(path: str, request: Request) -> Response:
    target = f"/{path}"
    if request.url.query:
        target = f"{target}?{request.url.query}"
    headers = {key: value for key, value in request.headers.items() if key.lower() not in _HOP_HEADERS}
    headers["x-forwarded-host"] = request.headers.get("host", "")
    headers["x-forwarded-proto"] = request.url.scheme
    async with httpx.AsyncClient(base_url=UPSTREAM, follow_redirects=False, timeout=120) as client:
        upstream = await client.request(
            request.method,
            target,
            headers=headers,
            content=await request.body(),
        )
    response_headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower() not in _HOP_HEADERS and key.lower() != "set-cookie"
    }
    response = Response(content=upstream.content, status_code=upstream.status_code, headers=response_headers)
    for cookie in upstream.headers.get_list("set-cookie"):
        response.raw_headers.append((b"set-cookie", cookie.encode("latin-1")))
    return response


def _safe_destination(value: str) -> str:
    if not value.startswith("/") or value.startswith("//") or "\\" in value or "\r" in value or "\n" in value:
        return "/"
    return value
