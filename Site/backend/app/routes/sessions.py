"""Routes API pour l'historique des sessions Live.

Endpoints :
- ``GET /api/sessions``           → liste paginée
- ``GET /api/sessions/summary``   → agrégats sur N jours
- ``GET /api/sessions/{id}``      → détails d'une session
- ``DELETE /api/sessions/{id}``   → supprime une session
- ``DELETE /api/sessions``        → purge tout l'historique

Toutes les routes sont protégées par ``require_auth``.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from ..auth import require_auth
from ..limiter import limiter
from ..services import sessions_store

log = logging.getLogger("voicebridge.sessions_route")

router = APIRouter(prefix="/api/sessions", tags=["sessions"],
                    dependencies=[Depends(require_auth)])


@router.get("")
@limiter.limit("60/minute")
async def list_sessions(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    """Liste paginée des sessions, plus récentes d'abord."""
    return sessions_store.list_sessions(limit=limit, offset=offset)


@router.get("/summary")
@limiter.limit("60/minute")
async def session_summary(
    request: Request,
    days: int = Query(30, ge=1, le=365),
) -> dict:
    """Agrégats des N derniers jours : n_sessions, durée totale, coût,
    répartition par mode. Utilisé pour le widget récap sur la page.
    """
    return sessions_store.summary(period_days=days)


@router.get("/{session_id}")
@limiter.limit("60/minute")
async def get_session(request: Request, session_id: str) -> dict:
    session = sessions_store.get(session_id)
    if not session:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={
            "error": "not_found", "message": "Session introuvable"})
    return session


@router.delete("/{session_id}")
@limiter.limit("30/minute")
async def delete_session(request: Request, session_id: str) -> dict:
    ok = sessions_store.delete(session_id)
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={
            "error": "not_found", "message": "Session introuvable"})
    return {"deleted": True, "id": session_id}


@router.delete("")
@limiter.limit("5/minute")
async def delete_all(request: Request) -> dict:
    """Purge complète. À utiliser avec précaution — destructif et irréversible."""
    n = sessions_store.delete_all()
    return {"deleted": n}
