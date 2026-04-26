"""Routes ``/api/system/*`` (livraison 6 — versions complètes).

- ``/status`` (public) : RAM, stockage, état des modèles via ModelManager
- ``/prechauffage`` (auth) : préchargement à la carte (TTS / Live)
- ``/unload`` (auth) : décharge un modèle individuel (clé dans le body)
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


VALID_PROFILES = ("tts", "live")


@router.post("/prechauffage", dependencies=[Depends(require_auth)])
async def prechauffage(payload: dict):
    """Body ``{"language": "fr"|"en", "profiles": ["tts","live"], "voice_id"?: "..."}``.

    Préchargement à la carte selon les profils demandés :

    - ``tts``  → charge NeuTTS Q8 (haute qualité) de la langue → Studio TTS fichier
    - ``live`` → charge NeuTTS Q4 (rapide) + Kyutai STT + Silero VAD → mode Live

    Les deux modèles EN ne sont jamais préchargés depuis FR (et inversement)
    pour éviter de gaspiller la RAM. La détection deepfake n'est jamais
    préchargée (chargée à la première requête sur ``/api/detection``).

    Rétrocompat : si ``profiles`` n'est pas fourni, on prend ``["tts","live"]``.
    """
    payload = payload or {}
    language = payload.get("language", "fr")
    voice_id_raw = payload.get("voice_id", "")
    profiles_raw = payload.get("profiles")

    if language not in ("fr", "en"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail={
            "error": "invalid_language", "message": "Langue : fr ou en"})

    if profiles_raw is None:
        profiles = ["tts", "live"]
    elif isinstance(profiles_raw, list) and all(isinstance(p, str) for p in profiles_raw):
        profiles = [p for p in profiles_raw if p in VALID_PROFILES]
        if not profiles:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail={
                "error": "invalid_profiles",
                "message": f"Au moins un profil parmi {VALID_PROFILES} doit être coché"})
    else:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail={
            "error": "invalid_profiles", "message": "profiles doit être une liste de chaînes"})

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
        from ..models import stt as stt_model  # noqa: F401  (loader register déjà fait au boot)
        from ..models import tts as tts_model
        from ..models import vad as vad_model  # noqa: F401
    except ImportError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail={
            "error": "ml_unavailable", "message": str(exc)})

    # Calcul de la liste des clés à charger (set pour dédupliquer si plusieurs
    # profils partagent un modèle ; aujourd'hui ils ne partagent rien mais
    # autant être robuste si ça évolue).
    keys_before = {k for k, v in _models_snapshot().items() if v == "loaded"}
    to_load: list[str] = []
    if "tts" in profiles:
        to_load.append(tts_model.model_key_for(language, "high"))
    if "live" in profiles:
        to_load.append(tts_model.model_key_for(language, "normal"))
        to_load.append(mgr.MODEL_KYUTAI)
        to_load.append(mgr.MODEL_SILERO_VAD)
    # Dédupe en gardant l'ordre (pour des logs lisibles).
    seen: set[str] = set()
    plan: list[str] = []
    for k in to_load:
        if k not in seen:
            seen.add(k)
            plan.append(k)

    # Le chargement effectif (NeuTTS + Kyutai) prend 30–60 s à froid et est
    # purement CPU-bound + bloquant (lock + import torch, JIT librosa, etc.).
    # On délègue au threadpool pour ne pas geler l'event loop (sinon le polling
    # GET /api/system/status reste en attente derrière, et la page paraît figée).
    def _warm() -> None:
        for key in plan:
            mgr.manager.get(key)

    try:
        await asyncio.to_thread(_warm)
    except Exception as exc:  # noqa: BLE001
        log.exception("prechauffage failed")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail={
            "error": "warmup_failed", "message": str(exc)})

    snap = _models_snapshot()
    keys_after = {k for k, v in snap.items() if v == "loaded"}
    newly_loaded = sorted(keys_after - keys_before)
    return {
        "success": True,
        "duration_ms": int((time.time() - t0) * 1000),
        "profiles": profiles,
        "planned": plan,
        "newly_loaded": newly_loaded,
        "loaded_count": len(keys_after),
        "total_count": len(snap),
        "models": snap,
    }


# ---------------------------------------------------------------------------
# Décharge ciblée d'un modèle
# ---------------------------------------------------------------------------


@router.post("/unload", dependencies=[Depends(require_auth)])
async def unload_one(payload: dict):
    """Body ``{"key": "neutts_fr_q4"}``. Décharge un modèle individuel."""
    payload = payload or {}
    key = payload.get("key", "")
    if not isinstance(key, str) or not key:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail={
            "error": "missing_key", "message": "clé du modèle requise"})
    if key not in mgr.ALL_MODEL_KEYS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={
            "error": "unknown_key",
            "message": f"clé inconnue : {key} (valides : {list(mgr.ALL_MODEL_KEYS)})"})

    was_loaded = mgr.manager.is_loaded(key)
    # unload est rapide (pas d'I/O), pas besoin de threadpool.
    unloaded = mgr.manager.unload(key)
    return {
        "success": True,
        "key": key,
        "was_loaded": was_loaded,
        "unloaded": unloaded,
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
