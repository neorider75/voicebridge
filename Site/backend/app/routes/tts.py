"""Routes ``/api/tts/*`` — STUB (livraison 2)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import require_auth

router = APIRouter(prefix="/api/tts", tags=["tts"], dependencies=[Depends(require_auth)])


@router.post("/generate")
async def generate():
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, detail="Disponible en livraison 2")
