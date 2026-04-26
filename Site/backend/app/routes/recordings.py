"""Routes ``/api/recordings/*`` — STUB (livraison 6)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import require_auth

router = APIRouter(prefix="/api/recordings", tags=["recordings"], dependencies=[Depends(require_auth)])


@router.get("")
async def list_recordings():
    return {"recordings": [], "total_count": 0, "total_size_mb": 0}
