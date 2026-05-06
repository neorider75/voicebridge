"""Routes ``/api/settings/*`` (livraison 6 — versions complètes).

- GET  /api/settings                 : retention par défaut, model_unload, domaine
- PUT  /api/settings                 : maj partielle (default_retention,
  model_unload_after_minutes)
- POST /api/settings/password        : change le mot de passe (vérif courant)
- GET  /api/settings/api-key         : info masquée
- POST /api/settings/api-key/generate : génère + retourne en clair (UNE FOIS)
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from .. import config
from ..auth import require_auth
from ..utils import security

router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[Depends(require_auth)])
log = logging.getLogger("voicebridge.settings")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@router.get("")
async def get_settings():
    cfg = config.load()
    return {
        "default_retention": cfg.get("default_retention", "session"),
        "model_unload_after_minutes": cfg.get("model_unload_after_minutes", 15),
        "default_tts_engine": cfg.get("default_tts_engine", "neutts"),
        "domain": cfg.get("domain", ""),
        # V3
        "default_live_mode": cfg.get("default_live_mode", "cpu-fr-en"),
        "default_translation_provider": cfg.get("default_translation_provider", "opus-mt-cpu"),
        "translation_glossary": cfg.get("translation_glossary", {}),
        "translation_history_size": cfg.get("translation_history_size", 5),
    }


_VALID_LIVE_MODES = ("cpu-fr-en", "gpu-clone", "gpu-native", "gpu-hybrid")
_VALID_PROVIDERS = ("opus-mt-cpu", "opus-mt-gpu", "nllb", "gpt-4o-mini", "gpt-4o")


class SettingsUpdate(BaseModel):
    default_retention: str | None = Field(default=None, pattern="^(session|24h|48h)$")
    model_unload_after_minutes: int | None = Field(default=None, ge=5, le=240)
    default_tts_engine: str | None = Field(default=None, pattern="^(neutts|xtts)$")
    # V3
    default_live_mode: str | None = Field(default=None)
    default_translation_provider: str | None = Field(default=None)
    translation_glossary: dict[str, str] | None = Field(default=None)
    translation_history_size: int | None = Field(default=None, ge=0, le=20)


@router.put("")
async def update_settings(payload: SettingsUpdate):
    updates: dict = {}
    if payload.default_retention is not None:
        updates["default_retention"] = payload.default_retention
    if payload.model_unload_after_minutes is not None:
        updates["model_unload_after_minutes"] = payload.model_unload_after_minutes
    if payload.default_tts_engine is not None:
        updates["default_tts_engine"] = payload.default_tts_engine
    # V3 :
    if payload.default_live_mode is not None:
        if payload.default_live_mode not in _VALID_LIVE_MODES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail={
                "error": "invalid_default_live_mode",
                "message": f"valeurs autorisées : {_VALID_LIVE_MODES}"})
        updates["default_live_mode"] = payload.default_live_mode
    if payload.default_translation_provider is not None:
        if payload.default_translation_provider not in _VALID_PROVIDERS:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail={
                "error": "invalid_translation_provider",
                "message": f"valeurs autorisées : {_VALID_PROVIDERS}"})
        updates["default_translation_provider"] = payload.default_translation_provider
    if payload.translation_glossary is not None:
        # Limite raisonnable : 100 entrées max, ~120 chars par entrée
        gl = payload.translation_glossary
        if len(gl) > 100:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail={
                "error": "glossary_too_large",
                "message": "max 100 entrées dans le glossaire"})
        for k, v in gl.items():
            if len(k) > 100 or len(v) > 200:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail={
                    "error": "glossary_entry_too_long",
                    "message": f"entrée trop longue : {k!r}"})
        updates["translation_glossary"] = gl
    if payload.translation_history_size is not None:
        updates["translation_history_size"] = payload.translation_history_size

    if updates:
        config.set_many(updates)
        log.info("settings updated keys=%s", list(updates.keys()))
    return {"success": True, "updated": updates}


# ---------------------------------------------------------------------------
# Mot de passe
# ---------------------------------------------------------------------------


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=8, max_length=200)


@router.post("/password")
async def change_password(payload: PasswordChange):
    cfg = config.load()
    expected_hash = cfg.get("password_hash", "")
    if not security.verify_password(payload.current_password, expected_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={
            "error": "invalid_current_password", "message": "Mot de passe actuel incorrect"})
    if payload.new_password == payload.current_password:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail={
            "error": "same_password", "message": "Le nouveau mot de passe doit différer de l'actuel"})
    config.set_many({"password_hash": security.hash_password(payload.new_password)})
    log.info("password changed")
    return {"success": True}


# ---------------------------------------------------------------------------
# Clé API Bearer
# ---------------------------------------------------------------------------


@router.get("/api-key")
async def get_api_key_info():
    cfg = config.load()
    h = cfg.get("api_token_hash", "")
    return {
        "masked": "sk-" + "•" * 28 + (h[-4:] if h else "????"),
        "created_at": cfg.get("api_token_created_at"),
    }


@router.post("/api-key/generate")
async def generate_api_key():
    """Génère et **retourne en clair** une nouvelle clé. L'ancienne devient
    immédiatement invalide. La clé en clair n'est plus jamais accessible.
    """
    token = "sk-" + secrets.token_hex(16)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    config.set_many({
        "api_token_hash": token_hash,
        "api_token_created_at": _now_iso(),
    })
    log.info("api key regenerated")
    return {"key": token, "created_at": _now_iso()}
