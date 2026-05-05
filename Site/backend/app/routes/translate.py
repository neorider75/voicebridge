"""Routes ``/api/translate/*`` — pré-chargement du modèle de traduction Live.

Endpoint unique : GET /api/translate/warmup?src=fr&tgt=en
Charge (ou confirme que le modèle est déjà en RAM) le modèle Helsinki-NLP
OPUS-MT pour la paire de langues demandée. Utilisé par le frontend avant de
démarrer une session Live avec traduction, afin d'éviter la latence de
chargement sur la première phrase.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..auth import require_auth
from ..services import translation as trans_svc

router = APIRouter(prefix="/api/translate", tags=["translate"],
                   dependencies=[Depends(require_auth)])
log = logging.getLogger("voicebridge.translate")

_SUPPORTED = ("fr", "en")


@router.get("/warmup")
async def warmup(
    src: str = Query(..., description="Langue source (fr ou en)"),
    tgt: str = Query(..., description="Langue cible (fr ou en)"),
):
    """Pré-charge le modèle de traduction src→tgt en RAM.

    - Si le modèle est déjà en cache RAM → retourne immédiatement
      ``{"status": "cached"}``.
    - Sinon → charge depuis le disque (ou télécharge depuis HuggingFace au
      premier usage) dans un thread, puis retourne ``{"status": "ready"}``.

    Latences typiques :
    - Cache RAM         : < 10 ms
    - Chargement disque : 3 – 8 s
    - Téléchargement    : selon la connexion (~300 Mo par paire)
    """
    if src not in _SUPPORTED or tgt not in _SUPPORTED:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail={
            "error": "unsupported_language",
            "message": f"V1 supporte uniquement fr et en. Reçu : src={src!r} tgt={tgt!r}",
        })
    if src == tgt:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail={
            "error": "same_language",
            "message": "src et tgt doivent être différents",
        })

    # Vérification du cache RAM (sync, rapide).
    if (src, tgt) in trans_svc._models:
        log.debug("translate warmup: cache hit %s→%s", src, tgt)
        return {"status": "cached", "src": src, "tgt": tgt}

    # Chargement bloquant dans un thread pour ne pas bloquer l'event loop.
    log.info("translate warmup: loading model %s→%s", src, tgt)
    try:
        await asyncio.to_thread(trans_svc._load, src, tgt)
    except trans_svc.TranslationError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail={
            "error": "model_load_failed",
            "message": str(exc),
        })
    except Exception as exc:  # noqa: BLE001
        log.exception("translate warmup unexpected error")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail={
            "error": "warmup_failed",
            "message": str(exc),
        })

    log.info("translate warmup: model %s→%s ready", src, tgt)
    return {"status": "ready", "src": src, "tgt": tgt}
