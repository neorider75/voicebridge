"""Routes ``/api/stt/*`` — STUB (livraison 3)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import require_auth

router = APIRouter(prefix="/api/stt", tags=["stt"], dependencies=[Depends(require_auth)])


@router.post("/transcribe")
async def transcribe():
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, detail="Disponible en livraison 3")


@router.post("/generate")
async def generate():
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, detail="Disponible en livraison 3")
