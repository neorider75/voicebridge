"""Routes ``/api/voices/*``.

Cf. ``Spec/voicebridge_specs/02-features-v1.md`` (Mes voix, Ajout voix)
et ``Spec/voicebridge_specs/05-backend-api.md`` (endpoints).

Pipeline ajout voix :
    upload/URL → WAV 24 kHz mono → ``encode_reference()`` → ``.pt`` sur disque
    + entrée dans ``voices/metadata.json``.

Suppression : refusée si ``protected: true``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import Optional

try:
    import torch  # type: ignore
    ML_AVAILABLE = True
except ImportError:  # mode --minimal sans deps ML
    torch = None  # type: ignore
    ML_AVAILABLE = False

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, HttpUrl

from .. import config
from ..auth import require_auth
from ..limiter import limiter
from ..models import tts as tts_model
from ..services import audio, url_extract
from ..services import voices_store as store
from ..utils import files

router = APIRouter(prefix="/api/voices", tags=["voices"], dependencies=[Depends(require_auth)])
log = logging.getLogger("voicebridge.voices")

MAX_VOICE_BYTES = 10 * 1024 * 1024  # 10 Mo (cf. spec)


def _backbone_label(language: str) -> str:
    return "neutts-nano-french" if language == "fr" else "neutts-nano"


def _validate_language(value: str) -> str:
    if value not in ("fr", "en"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail={
            "error": "invalid_language", "message": "Langue supportée : fr ou en"})
    return value


def _require_ml() -> None:
    if not ML_AVAILABLE:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail={
            "error": "ml_unavailable",
            "message": "Modèles ML non disponibles (installation minimale détectée). "
                       "Relancez sudo ./install.sh sans --minimal pour activer la synthèse.",
        })


def _encode_voice_background(voice_id: str, language: str, ref_text: str | None) -> None:
    """Tâche d'arrière-plan : encode_reference (~30 s à froid) + ref_text + ready.

    Lancée par FastAPI BackgroundTasks après l'envoi de la réponse HTTP. Tourne
    dans le threadpool (fonction sync) → ne bloque pas l'event loop. Met à
    jour le statut de la voix dans metadata.json à la fin (ready ou failed).
    """
    wav = store.wav_path(voice_id)
    try:
        codes = tts_model.encode_reference(wav, language)
        store.encoded_path(voice_id).parent.mkdir(parents=True, exist_ok=True)
        torch.save(codes, store.encoded_path(voice_id))
        if ref_text and ref_text.strip():
            store.write_ref_text(voice_id, ref_text)
        duration = int(audio.audio_duration_seconds(wav))
        store.patch(voice_id, {"status": "ready", "duration_seconds": duration})
        log.info("voice ready id=%s duration=%ds", voice_id, duration)
    except Exception as exc:  # noqa: BLE001
        log.exception("voice encode_reference failed id=%s", voice_id)
        store.patch(voice_id, {
            "status": "failed",
            "error_message": str(exc)[:300],  # tronqué pour metadata.json lisible
        })


# ---------------------------------------------------------------------------
# GET liste
# ---------------------------------------------------------------------------


@router.get("")
async def list_voices() -> dict:
    return {"voices": store.list_voices()}


# ---------------------------------------------------------------------------
# GET audio (référence)
# ---------------------------------------------------------------------------


@router.get("/{voice_id}/audio")
async def voice_audio(voice_id: str):
    voice_id = files.safe_id(voice_id)
    p = files.ensure_inside(config.VOICES_DIR, store.wav_path(voice_id))
    if not p.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="audio introuvable")
    return FileResponse(p, media_type="audio/wav", filename=f"{voice_id}.wav")


# ---------------------------------------------------------------------------
# POST création par upload (multipart)
# ---------------------------------------------------------------------------


@router.post("/preview-clean")
@limiter.limit("20/minute")
async def preview_clean(
    request: Request,
    audio_file: UploadFile = File(...),
):
    """Prend un audio uploadé et retourne sa version nettoyée
    (afftdn + highpass + loudnorm via ffmpeg).

    Permet à l'utilisateur de comparer Original / Nettoyé avant de choisir
    quelle version utiliser pour créer la voix. Pas de side-effect : aucun
    enregistrement persistant côté serveur, juste un round-trip.
    """
    _require_ml()  # pas strictement requis (pas de ML utilisé) mais cohérent
    src_path = None
    cleaned_path = None
    try:
        with NamedTemporaryFile(delete=False, dir=config.TMP_DIR,
                                suffix=Path(audio_file.filename or "").suffix) as tmp:
            src_path = Path(tmp.name)
            shutil.copyfileobj(audio_file.file, tmp)
        try:
            audio.validate_upload(src_path, MAX_VOICE_BYTES)
        except audio.AudioError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={
                "error": "audio_invalid", "message": str(exc)})
        cleaned_path = src_path.with_suffix(".cleaned.wav")
        try:
            audio.clean_light(src_path, cleaned_path)
        except audio.AudioError as exc:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail={
                "error": "clean_failed", "message": str(exc)})
        # Lecture du WAV en mémoire pour le streamer (le tmp sera supprimé
        # tout de suite après).
        data = cleaned_path.read_bytes()
        from fastapi.responses import Response  # local import pour éviter circular
        return Response(content=data, media_type="audio/wav",
                        headers={"Content-Disposition": "inline; filename=cleaned.wav"})
    finally:
        if src_path and src_path.exists():
            src_path.unlink(missing_ok=True)
        if cleaned_path and cleaned_path.exists():
            cleaned_path.unlink(missing_ok=True)


@router.post("", status_code=201)
@limiter.limit("10/minute")
async def create_voice(
    request: Request,
    background_tasks: BackgroundTasks,
    name: str = Form(..., min_length=1, max_length=50),
    language: str = Form(...),
    audio_file: UploadFile = File(...),
    ref_text: str | None = Form(default=None),
):
    """Crée une voix depuis un audio uploadé (record/upload).

    Réponse asynchrone : on valide + convertit en WAV de manière synchrone
    (rapide, ~1-2 s), on inscrit la voix en metadata avec ``status="encoding"``
    et on retourne immédiatement. L'encodage NeuTTS (lent, ~30 s à froid)
    tourne en background_task. Le front peut afficher la voix dans /voices
    avec un badge "encodage en cours" et poller l'état.

    ``ref_text`` (optionnel) = transcription exacte de l'audio source. Sans
    ref_text, NeuTTS retombe sur un fallback générique (cf. routes/tts.py).
    """
    _require_ml()
    language = _validate_language(language)
    voice_id = files.new_id("v_")
    wav_dst = store.wav_path(voice_id)
    wav_dst.parent.mkdir(parents=True, exist_ok=True)

    with NamedTemporaryFile(delete=False, dir=config.TMP_DIR, suffix=Path(audio_file.filename or "").suffix) as tmp:
        tmp_path = Path(tmp.name)
        shutil.copyfileobj(audio_file.file, tmp)

    try:
        audio.validate_upload(tmp_path, MAX_VOICE_BYTES)
        # Conversion + trim 15s max : NeuTTS Air est entraîné sur des références
        # de 3-15s, au-delà la qualité peut se dégrader. trim_first_voiced
        # saute aussi le silence initial éventuel (utile si l'utilisateur a
        # cliqué record puis a marqué une pause avant de parler).
        full_wav_path = tmp_path.with_suffix(".full.wav")
        audio.to_wav_24k_mono(tmp_path, full_wav_path)
        audio.trim_first_voiced(full_wav_path, wav_dst, duration_seconds=15)
        full_wav_path.unlink(missing_ok=True)
    except audio.AudioError as exc:
        wav_dst.unlink(missing_ok=True)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={
            "error": "audio_invalid", "message": str(exc)})
    finally:
        tmp_path.unlink(missing_ok=True)

    # Inscrit immédiatement dans metadata.json avec status="encoding".
    # GET /api/voices la renverra avec ce statut → le front peut l'afficher
    # tout de suite avec un badge "Encodage…".
    voice = store.upsert({
        "id": voice_id,
        "name": name,
        "language": language,
        "backbone": _backbone_label(language),
        "duration_seconds": 0,  # mis à jour par le background task
        "status": "encoding",
    })

    # encode_reference (~30 s à froid) + write ref_text → en background.
    # FastAPI BackgroundTasks exécute les fonctions sync dans son threadpool,
    # donc ça ne bloque pas l'event loop.
    background_tasks.add_task(_encode_voice_background, voice_id, language, ref_text)

    log.info("voice queued id=%s lang=%s has_ref_text=%s",
             voice_id, language, bool(ref_text and ref_text.strip()))
    return voice


# ---------------------------------------------------------------------------
# POST création par URL (SSE pour la progression)
# ---------------------------------------------------------------------------


class FromUrlPayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    language: str
    url: HttpUrl


# Stockage temporaire des extractions en attente de confirmation.
# voice_id -> {wav_path: Path, name: str, language: str}
_PENDING: dict[str, dict] = {}


@router.post("/from-url")
@limiter.limit("10/minute")
async def from_url(request: Request, payload: FromUrlPayload):
    _require_ml()
    language = _validate_language(payload.language)
    voice_id = files.new_id("v_")

    async def event_stream():
        with TemporaryDirectory(prefix="vb-url-") as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            try:
                gen = url_extract.extract(str(payload.url), tmp_dir)
                trimmed: Path | None = None
                while True:
                    try:
                        step, percent = next(gen)
                        yield f"event: progress\ndata: {json.dumps({'step': step, 'percent': percent})}\n\n"
                        await asyncio.sleep(0)
                    except StopIteration as stop:
                        trimmed = stop.value
                        break
                if trimmed is None or not trimmed.exists():
                    raise url_extract.UrlExtractError("Fichier extrait introuvable")

                # Persiste dans tmp_dir VoiceBridge (en attente de confirmation)
                preview_dst = config.TMP_DIR / f"{voice_id}.wav"
                config.TMP_DIR.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(trimmed, preview_dst)
                _PENDING[voice_id] = {
                    "wav_path": preview_dst, "name": payload.name, "language": language,
                }
                result = {
                    "id": voice_id,
                    "preview_url": f"/api/voices/{voice_id}/preview",
                }
                yield f"event: result\ndata: {json.dumps(result)}\n\n"
            except url_extract.UrlExtractError as exc:
                yield f"event: error\ndata: {json.dumps({'error': 'extract_failed', 'message': str(exc)})}\n\n"
            except Exception as exc:  # noqa: BLE001
                log.exception("from_url unexpected error")
                yield f"event: error\ndata: {json.dumps({'error': 'server_error', 'message': str(exc)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/{voice_id}/preview")
async def voice_preview(voice_id: str):
    voice_id = files.safe_id(voice_id)
    pending = _PENDING.get(voice_id)
    if not pending:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="aucun preview en attente")
    p = pending["wav_path"]
    if not Path(p).exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="preview introuvable")
    return FileResponse(p, media_type="audio/wav")


@router.post("/{voice_id}/confirm")
async def confirm_voice(voice_id: str, background_tasks: BackgroundTasks):
    """Confirme l'extraction par URL : déplace le WAV preview vers son
    emplacement final et lance encode_reference en background (même flux
    asynchrone que POST /api/voices).
    """
    _require_ml()
    voice_id = files.safe_id(voice_id)
    pending = _PENDING.pop(voice_id, None)
    if not pending:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="aucun preview en attente")

    wav_dst = store.wav_path(voice_id)
    wav_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(pending["wav_path"]), wav_dst)

    voice = store.upsert({
        "id": voice_id,
        "name": pending["name"],
        "language": pending["language"],
        "backbone": _backbone_label(pending["language"]),
        "duration_seconds": 0,
        "status": "encoding",
    })

    # Pas de ref_text pour les voix extraites par URL (on n'a pas la
    # transcription du contenu). Le fallback safe de routes/tts.py prendra
    # le relais à la première génération.
    background_tasks.add_task(_encode_voice_background, voice_id, pending["language"], None)
    log.info("voice from-url queued id=%s lang=%s", voice_id, pending["language"])
    return voice


# ---------------------------------------------------------------------------
# PUT édition
# ---------------------------------------------------------------------------


@router.put("/{voice_id}")
async def update_voice(
    voice_id: str,
    name: Optional[str] = Form(None),
    language: Optional[str] = Form(None),
    audio_file: Optional[UploadFile] = File(None),
):
    if audio_file is not None and audio_file.filename:
        _require_ml()
    voice_id = files.safe_id(voice_id)
    voice = store.get(voice_id)
    if not voice:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="voix introuvable")
    if voice.get("protected"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="voix protégée")

    if name is not None:
        voice["name"] = name
    if language is not None:
        voice["language"] = _validate_language(language)
        voice["backbone"] = _backbone_label(voice["language"])

    if audio_file is not None and audio_file.filename:
        wav_dst = store.wav_path(voice_id)
        with NamedTemporaryFile(delete=False, dir=config.TMP_DIR, suffix=Path(audio_file.filename).suffix) as tmp:
            tmp_path = Path(tmp.name)
            shutil.copyfileobj(audio_file.file, tmp)
        try:
            audio.validate_upload(tmp_path, MAX_VOICE_BYTES)
            audio.to_wav_24k_mono(tmp_path, wav_dst)
            codes = tts_model.encode_reference(wav_dst, voice["language"])
            torch.save(codes, store.encoded_path(voice_id))
            voice["duration_seconds"] = int(audio.audio_duration_seconds(wav_dst))
        except audio.AudioError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
        finally:
            tmp_path.unlink(missing_ok=True)

    return store.upsert(voice)


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------


@router.delete("/{voice_id}")
async def delete_voice(voice_id: str):
    voice_id = files.safe_id(voice_id)
    try:
        ok = store.delete(voice_id)
    except PermissionError:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="voix protégée")
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="voix introuvable")
    # Supprime les fichiers physiques
    for p in (store.wav_path(voice_id), store.encoded_path(voice_id), store.ref_text_path(voice_id)):
        Path(p).unlink(missing_ok=True)
    log.info("voice deleted id=%s", voice_id)
    return {"success": True}
