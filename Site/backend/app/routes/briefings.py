"""Routes ``/api/briefings/*`` — CRUD des briefings GPT (Décision 7).

Endpoints :
- ``GET    /api/briefings``               : liste tous les briefings sauvegardés
- ``POST   /api/briefings``               : crée un briefing
- ``GET    /api/briefings/{id}``          : détail d'un briefing
- ``PUT    /api/briefings/{id}``          : met à jour
- ``DELETE /api/briefings/{id}``          : supprime
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..auth import require_auth
from ..limiter import limiter
from ..services import briefings_store

router = APIRouter(prefix="/api/briefings", tags=["briefings"],
                   dependencies=[Depends(require_auth)])
log = logging.getLogger("voicebridge.briefings")


class BriefingCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=briefings_store.MAX_NAME_LEN)
    content: str = Field(default="", max_length=briefings_store.MAX_CONTENT_LEN)


class BriefingUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=briefings_store.MAX_NAME_LEN)
    content: str | None = Field(None, max_length=briefings_store.MAX_CONTENT_LEN)


@router.get("")
async def list_briefings() -> dict:
    return {"briefings": briefings_store.list_briefings()}


@router.get("/{briefing_id}")
async def get_briefing(briefing_id: str) -> dict:
    b = briefings_store.get(briefing_id)
    if not b:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="briefing introuvable")
    return b


@router.post("", status_code=201)
@limiter.limit("30/minute")
async def create_briefing(request: Request, payload: BriefingCreate) -> dict:
    try:
        b = briefings_store.create(payload.name, payload.content)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
    log.info("briefing created id=%s name=%r", b["id"], b["name"])
    return b


@router.put("/{briefing_id}")
@limiter.limit("60/minute")
async def update_briefing(request: Request, briefing_id: str,
                          payload: BriefingUpdate) -> dict:
    try:
        b = briefings_store.update(briefing_id,
                                    name=payload.name,
                                    content=payload.content)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if not b:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="briefing introuvable")
    return b


@router.delete("/{briefing_id}")
async def delete_briefing(briefing_id: str) -> dict:
    if not briefings_store.delete(briefing_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="briefing introuvable")
    return {"success": True}
