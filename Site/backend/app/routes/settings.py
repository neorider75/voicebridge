"""Routes ``/api/settings/*`` — partiellement impl. (full en livraison 6)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import config
from ..auth import require_auth

router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[Depends(require_auth)])


@router.get("")
async def get_settings():
    cfg = config.load()
    return {
        "default_retention": cfg.get("default_retention", "session"),
        "model_unload_after_minutes": cfg.get("model_unload_after_minutes", 15),
        "domain": cfg.get("domain", ""),
    }


@router.get("/api-key")
async def get_api_key_info():
    cfg = config.load()
    h = cfg.get("api_token_hash", "")
    return {
        "masked": "sk-" + "•" * 28 + (h[-4:] if h else "????"),
        "created_at": cfg.get("api_token_created_at"),
    }
