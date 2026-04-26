"""Routes ``/api/detection/*`` — STUB (livraison 5)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import require_auth

router = APIRouter(prefix="/api/detection", tags=["detection"], dependencies=[Depends(require_auth)])


@router.post("/analyze")
async def analyze():
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, detail="Disponible en livraison 5")
