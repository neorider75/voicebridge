"""Routes ``/api/voices/*`` — STUB (livraison 2).

À implémenter :
- GET    /api/voices                   liste
- POST   /api/voices                   création (multipart)
- POST   /api/voices/from-url          création par URL (SSE)
- GET    /api/voices/{id}/audio        sert le WAV de référence
- GET    /api/voices/{id}/preview      preview après extraction URL
- POST   /api/voices/{id}/confirm      confirme + encode .pt
- PUT    /api/voices/{id}              édition
- DELETE /api/voices/{id}              suppression (refuse si protected)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import require_auth

router = APIRouter(prefix="/api/voices", tags=["voices"], dependencies=[Depends(require_auth)])


@router.get("")
async def list_voices():
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, detail="Disponible en livraison 2")
