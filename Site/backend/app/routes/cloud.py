"""Routes ``/api/cloud/*`` — configuration et test des providers cloud V3.

Endpoints :
- ``GET  /api/cloud/status``               : état (runpod_configured, openai_configured)
- ``POST /api/cloud/runpod/configure``     : saisir les clés RunPod (chiffrées Fernet)
- ``POST /api/cloud/runpod/test``          : ping endpoint RunPod
- ``POST /api/cloud/runpod/warmup``        : pré-charge des modèles GPU (Décision 5)
- ``POST /api/cloud/openai/configure``     : saisir la clé OpenAI
- ``POST /api/cloud/openai/test``          : valider la clé OpenAI (models.list)
- ``GET  /api/cloud/providers``            : liste des providers de traduction

Tous les endpoints sont protégés par ``require_auth`` et soumis au rate
limiting ``slowapi`` (10/min).

Cf. Décisions 1, 3, 5, 7 du document ``00-decisions-v3.md``.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from .. import config
from ..auth import require_auth
from ..limiter import limiter
from ..services import openai_client, runpod_client, secrets, translation_router

router = APIRouter(prefix="/api/cloud", tags=["cloud"],
                   dependencies=[Depends(require_auth)])
log = logging.getLogger("voicebridge.cloud")


# ════════════════════════════════════════════════════════════════════
# GET /status
# ════════════════════════════════════════════════════════════════════


@router.get("/status")
async def cloud_status() -> dict:
    """État global des intégrations cloud.

    Utilisé par :
    - L'app macOS au démarrage (Décision 4 : default mode = cpu-fr-en
      si runpod_configured == false)
    - Le front Studio Live (greyer les modes GPU si non configurés)
    - Settings UI pour afficher les badges ✅/❌
    """
    return {
        "runpod_configured": runpod_client.is_configured(),
        "openai_configured": openai_client.is_configured(),
        "datacenter": runpod_client.get_datacenter() if runpod_client.is_configured() else None,
        "default_live_mode": config.get("default_live_mode", "cpu-fr-en"),
        "default_translation_provider": translation_router.get_default_provider(),
    }


# ════════════════════════════════════════════════════════════════════
# POST /runpod/configure
# ════════════════════════════════════════════════════════════════════


class RunPodConfig(BaseModel):
    # max_length généreux : les nouveaux formats de clés API (RunPod
    # rpa_..., AWS-style S3 secrets, OpenAI sk-proj-/sk-svcacct-) peuvent
    # dépasser 200 caractères selon les comptes / scopes.
    api_key: str | None = Field(None, min_length=1, max_length=500)
    endpoint_id: str | None = Field(None, max_length=100)
    volume_id: str | None = Field(None, max_length=100)
    datacenter: str | None = Field(None, max_length=20)
    s3_access_key: str | None = Field(None, max_length=500)
    s3_secret_key: str | None = Field(None, max_length=500)


@router.post("/runpod/configure")
@limiter.limit("10/minute")
async def runpod_configure(request: Request, payload: RunPodConfig):
    """Saisit ou met à jour les clés RunPod. Champs ``None`` ignorés."""
    updates = {}

    if payload.api_key:
        updates["runpod_api_key_encrypted"] = secrets.encrypt(payload.api_key)
    if payload.endpoint_id is not None:
        updates["runpod_endpoint_id"] = payload.endpoint_id.strip()
    if payload.volume_id is not None:
        updates["runpod_volume_id"] = payload.volume_id.strip()
    if payload.datacenter is not None:
        updates["runpod_datacenter"] = payload.datacenter.strip() or "EU-FR-1"
    if payload.s3_access_key:
        updates["runpod_s3_access_key_encrypted"] = secrets.encrypt(payload.s3_access_key)
    if payload.s3_secret_key:
        updates["runpod_s3_secret_key_encrypted"] = secrets.encrypt(payload.s3_secret_key)

    if not updates:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={
            "error": "no_changes",
            "message": "Aucun champ fourni",
        })

    config.set_many(updates)
    log.info("runpod config updated: keys=%s", list(updates.keys()))
    return {"updated": list(updates.keys()),
            "configured": runpod_client.is_configured()}


# ════════════════════════════════════════════════════════════════════
# POST /runpod/test
# ════════════════════════════════════════════════════════════════════


@router.post("/runpod/test")
@limiter.limit("10/minute")
async def runpod_test(request: Request) -> dict:
    """Ping l'endpoint RunPod. Confirme que la clé est valide et l'endpoint actif."""
    if not runpod_client.is_configured():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={
            "error": "not_configured",
            "message": "Saisir d'abord les clés RunPod via /api/cloud/runpod/configure",
        })
    try:
        result = runpod_client.ping()
        return {"ok": True, **result}
    except runpod_client.RunPodError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail={
            "error": "runpod_unreachable",
            "message": str(exc),
        })


# ════════════════════════════════════════════════════════════════════
# POST /runpod/warmup
# ════════════════════════════════════════════════════════════════════


class WarmupPayload(BaseModel):
    components: list[str] = Field(
        default_factory=lambda: ["whisper", "f5tts", "nllb"],
    )


@router.post("/runpod/warmup")
@limiter.limit("20/minute")
async def runpod_warmup(request: Request, payload: WarmupPayload) -> dict:
    """Pré-charge les modèles GPU spécifiés en VRAM côté worker.

    Utilisé par :
    - Bouton "🔥 Préchauffer GPU" dans le studio Live (Phase E)
    - Auto-warmup au démarrage de session (Décision 5 — pré-warming agressif)

    Composants supportés : ``whisper``, ``f5tts``, ``nllb``, ``opus-mt``, ``rvc``.
    """
    if not runpod_client.is_configured():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={
            "error": "not_configured",
            "message": "RunPod non configuré",
        })
    try:
        result = runpod_client.runsync(
            {"operation": "warmup", "components": payload.components},
            timeout=120.0,  # cold start GPU peut être long
        )
        return {"ok": True, "loaded": result.get("loaded", []),
                "components_requested": payload.components}
    except runpod_client.RunPodError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail={
            "error": "warmup_failed",
            "message": str(exc),
        })


# ════════════════════════════════════════════════════════════════════
# POST /openai/configure
# ════════════════════════════════════════════════════════════════════


class OpenAIConfig(BaseModel):
    # Les clés modernes (sk-proj-..., sk-svcacct-...) peuvent dépasser 200
    # chars. On laisse de la marge.
    api_key: str = Field(..., min_length=10, max_length=500)


@router.post("/openai/configure")
@limiter.limit("10/minute")
async def openai_configure(request: Request, payload: OpenAIConfig):
    """Saisit la clé OpenAI (chiffrée Fernet)."""
    config.set_many({
        "openai_api_key_encrypted": secrets.encrypt(payload.api_key),
    })
    log.info("openai config updated")
    return {"configured": True}


# ════════════════════════════════════════════════════════════════════
# POST /openai/test
# ════════════════════════════════════════════════════════════════════


@router.post("/openai/test")
@limiter.limit("10/minute")
async def openai_test(request: Request) -> dict:
    """Valide la clé OpenAI via un appel léger ``models.list``."""
    if not openai_client.is_configured():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={
            "error": "not_configured",
            "message": "Saisir d'abord la clé OpenAI",
        })
    try:
        result = openai_client.ping()
        return {"ok": True, **result}
    except openai_client.OpenAIError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail={
            "error": "openai_unreachable",
            "message": str(exc),
        })


# ════════════════════════════════════════════════════════════════════
# GET /providers
# ════════════════════════════════════════════════════════════════════


@router.get("/providers")
async def list_providers() -> dict:
    """Liste des providers de traduction avec leur statut (available, latence,
    coût). Utilisé par le sélecteur du studio Live."""
    return {
        "providers": translation_router.list_providers(),
        "default": translation_router.get_default_provider(),
    }
