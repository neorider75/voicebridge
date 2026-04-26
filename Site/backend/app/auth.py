"""Middleware FastAPI auth + dépendances ``Depends(...)``.

Deux mécanismes acceptés :
- Cookie ``vb_session`` signé (front web)
- Header ``Authorization: Bearer sk-...`` (app macOS)
"""
from __future__ import annotations

from fastapi import HTTPException, Request, status

from . import config
from .utils import security

# Routes publiques (pas d'auth requise)
PUBLIC_PATHS: set[str] = {
    "/api/auth/login",
    "/api/system/status",
    "/login",
    "/healthz",
}

# Préfixes de routes publiques (assets statiques, robots, etc.)
PUBLIC_PREFIXES: tuple[str, ...] = (
    "/static/",
    "/css/",
    "/js/",
    "/assets/",
    "/favicon",
)


def _has_valid_session(request: Request) -> bool:
    cookie = request.cookies.get(security.SESSION_COOKIE)
    return security.verify_session_token(cookie)


def _has_valid_bearer(request: Request) -> bool:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return False
    token = auth.split(" ", 1)[1].strip()
    expected_hash = config.get("api_token_hash")
    if not expected_hash:
        return False
    return security.verify_api_token(token, expected_hash)


def is_authenticated(request: Request) -> bool:
    return _has_valid_session(request) or _has_valid_bearer(request)


def is_public_path(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES)


# ---------------------------------------------------------------------------
# Dépendance FastAPI réutilisable pour les routers
# ---------------------------------------------------------------------------


def require_auth(request: Request) -> None:
    """À utiliser via ``Depends(require_auth)`` sur les routes /api/* protégées."""
    if not is_authenticated(request):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "unauthorized", "message": "Authentification requise"},
            headers={"WWW-Authenticate": "Bearer"},
        )
