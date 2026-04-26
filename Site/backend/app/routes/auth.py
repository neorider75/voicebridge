"""Routes ``/api/auth/*`` : login, logout, check.

Implémente l'anti-bruteforce de la spec V1 :
- bcrypt cost 12 (via ``utils.security``)
- Délai progressif après échec (2s/5s/10s)
- Lockout IP : 10 échecs / 1h ⇒ blocage 1h
- Rate limit slowapi : 5 tentatives / 15 min / IP
- Logging IP + résultat (jamais le mot de passe)
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import config
from ..limiter import limiter
from ..utils import security

router = APIRouter(prefix="/api/auth", tags=["auth"])
log = logging.getLogger("voicebridge.auth")


class LoginPayload(BaseModel):
    password: str = Field(min_length=1, max_length=200)


def _client_ip(request: Request) -> str:
    # Nginx pose X-Forwarded-For ; sinon fallback sur ``request.client``.
    fwd = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if fwd:
        return fwd
    return request.client.host if request.client else "unknown"


@router.post("/login")
@limiter.limit("5/15minute")
async def login(request: Request, payload: LoginPayload, response: Response):  # noqa: D401
    # ``request`` est requis en première position pour slowapi.
    ip = _client_ip(request)
    ua = request.headers.get("user-agent", "-")

    locked, retry_after = security.is_locked(ip)
    if locked:
        log.warning("login locked ip=%s retry_after=%ds ua=%s", ip, retry_after, ua)
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={
                "error": "ip_locked",
                "message": "Trop de tentatives. Réessayez plus tard.",
                "retry_after": retry_after,
            },
            headers={"Retry-After": str(retry_after)},
        )

    expected_hash = config.get("password_hash")
    if not expected_hash:
        log.error("login impossible : password_hash manquant dans config.json")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "server_misconfigured", "message": "Configuration serveur incomplète"},
        )

    if security.verify_password(payload.password, expected_hash):
        security.record_success(ip)
        log.info("login success ip=%s ua=%s", ip, ua)
        token = security.create_session_token()
        response.set_cookie(
            key=security.SESSION_COOKIE,
            value=token,
            max_age=security.SESSION_INACTIVITY_SECONDS,
            httponly=True,
            secure=True,
            samesite="strict",
            path="/",
        )
        return {"success": True}

    delay, now_locked = security.record_failure(ip)
    if delay:
        await asyncio.sleep(delay)
    log.warning("login failed ip=%s locked=%s ua=%s", ip, now_locked, ua)
    remaining = security.remaining_attempts(ip)
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={
            "error": "invalid_password",
            "message": "Mot de passe invalide",
            "remaining_attempts": remaining,
        },
    )


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(security.SESSION_COOKIE, path="/")
    return {"success": True}


@router.get("/check")
async def check(request: Request):
    from ..auth import is_authenticated
    return {"authenticated": is_authenticated(request)}
