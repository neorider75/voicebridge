"""Routes ``/api/system/*`` (livraison 6 — versions complètes).

- ``/status`` (public) : RAM, stockage, état des modèles via ModelManager
- ``/prechauffage`` (auth) : déclenche le chargement des modèles d'une voix
- ``/clean`` (auth) : décharge tous les modèles + nettoie ``data/audio/``
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import time

import psutil
from fastapi import APIRouter, Depends, HTTPException, status

from .. import __version__
from .. import config
from ..auth import require_auth
from ..models import manager as mgr
from ..services import recordings_store
from ..services import voices_store
from ..utils import files

router = APIRouter(prefix="/api/system", tags=["system"])
log = logging.getLogger("voicebridge.system")

_BOOT_TIME = time.time()


def _models_snapshot() -> dict[str, str]:
    return mgr.manager.status_snapshot()


@router.get("/status")
async def status_public():
    vm = psutil.virtual_memory()
    disk = shutil.disk_usage("/")
    snap = _models_snapshot()
    # Statut global
    if any(v == "loaded" for v in snap.values()):
        global_status = "ready"
    else:
        global_status = "idle"
    return {
        "version": __version__,
        "ram": {
            "used_gb": round((vm.total - vm.available) / 1e9, 2),
            "total_gb": round(vm.total / 1e9, 2),
            "percent": vm.percent,
        },
        "storage": {
            "used_gb": round(disk.used / 1e9, 2),
            "total_gb": round(disk.total / 1e9, 2),
            "percent": round(disk.used / disk.total * 100, 1),
        },
        "models": snap,
        "latency_ms": None,
        "voicebridge_connected": False,
        "uptime_seconds": int(time.time() - _BOOT_TIME),
        "status": global_status,
    }


@router.post("/prechauffage", dependencies=[Depends(require_auth)])
async def prechauffage(payload: dict):
    """Body ``{"language": "fr"|"en", "voice_id": "..."}``.

    Charge le NeuTTS Q4 pour la langue + le NeuTTS Q8 (haute qualité) pour
    avoir une réponse rapide en TTS fichier.
    """
    language = (payload or {}).get("language", "fr")
    voice_id_raw = (payload or {}).get("voice_id", "")
    if language not in ("fr", "en"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail={
            "error": "invalid_language", "message": "Langue : fr ou en"})

    if voice_id_raw:
        try:
            voice_id = files.safe_id(voice_id_raw)
        except ValueError:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail={
                "error": "invalid_voice_id", "message": "voice_id invalide"})
        if not voices_store.get(voice_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail={
                "error": "voice_not_found", "message": "Voix introuvable"})

    t0 = time.time()
    # Import paresseux : ces modules peuvent ne pas être disponibles (mode minimal).
    try:
        from ..models import stt as stt_model
        from ..models import tts as tts_model
    except ImportError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail={
            "error": "ml_unavailable", "message": str(exc)})

    # Le chargement effectif (NeuTTS + Kyutai) prend 30–60 s à froid et est
    # purement CPU-bound + bloquant (lock + import torch, JIT librosa, etc.).
    # On délègue au threadpool pour ne pas geler l'event loop (sinon le polling
    # GET /api/system/status reste en attente derrière, et la page paraît figée).
    def _warm() -> None:
        # Q4 + Q8 pour la langue choisie
        mgr.manager.get(tts_model.model_key_for(language, "normal"))
        mgr.manager.get(tts_model.model_key_for(language, "high"))
        # Kyutai (utile au Live et au STT fichier)
        mgr.manager.get(mgr.MODEL_KYUTAI)

    try:
        await asyncio.to_thread(_warm)
    except Exception as exc:  # noqa: BLE001
        log.exception("prechauffage failed")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail={
            "error": "warmup_failed", "message": str(exc)})

    return {
        "success": True,
        "duration_ms": int((time.time() - t0) * 1000),
        "models": _models_snapshot(),
    }


@router.post("/clean", dependencies=[Depends(require_auth)])
async def clean():
    """Décharge tous les modèles + supprime tous les fichiers ``data/audio/``."""
    # 1. Mémoire RAM avant déchargement (pour calculer le delta approximatif)
    ram_before = (psutil.virtual_memory().total - psutil.virtual_memory().available) / 1e9

    # 2. Décharge tous les modèles
    n_unloaded = mgr.manager.unload_all()

    # 3. Supprime tous les fichiers audio générés (garde la liste vide)
    freed_mb = 0.0
    for rec in list(recordings_store.list_recordings()):
        size = rec.get("size_mb", 0) or 0
        if recordings_store.delete(rec["id"]):
            freed_mb += float(size)

    ram_after = (psutil.virtual_memory().total - psutil.virtual_memory().available) / 1e9
    return {
        "success": True,
        "models_unloaded": n_unloaded,
        "freed_ram_gb": round(max(0.0, ram_before - ram_after), 2),
        "freed_storage_mb": round(freed_mb, 2),
    }
