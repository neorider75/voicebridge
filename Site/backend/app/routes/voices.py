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


@router.post("", status_code=201)
@limiter.limit("10/minute")
async def create_voice(
    request: Request,
    name: str = Form(..., min_length=1, max_length=50),
    language: str = Form(...),
    audio_file: UploadFile = File(...),
):
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
        audio.to_wav_24k_mono(tmp_path, wav_dst)
    except audio.AudioError as exc:
        wav_dst.unlink(missing_ok=True)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={
            "error": "audio_invalid", "message": str(exc)})
    finally:
        tmp_path.unlink(missing_ok=True)

    # Pré-encodage ref_codes
    try:
        codes = tts_model.encode_reference(wav_dst, language)
        store.encoded_path(voice_id).parent.mkdir(parents=True, exist_ok=True)
        torch.save(codes, store.encoded_path(voice_id))
    except Exception as exc:  # noqa: BLE001
        log.exception("encode_reference failed")
        wav_dst.unlink(missing_ok=True)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail={
            "error": "encode_failed", "message": str(exc)})

    duration = int(audio.audio_duration_seconds(wav_dst))
    voice = store.upsert({
        "id": voice_id,
        "name": name,
        "language": language,
        "backbone": _backbone_label(language),
        "duration_seconds": duration,
    })
    log.info("voice created id=%s lang=%s duration=%ds", voice_id, language, duration)
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
async def confirm_voice(voice_id: str):
    _require_ml()
    voice_id = files.safe_id(voice_id)
    pending = _PENDING.pop(voice_id, None)
    if not pending:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="aucun preview en attente")

    wav_dst = store.wav_path(voice_id)
    wav_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(pending["wav_path"]), wav_dst)

    try:
        codes = tts_model.encode_reference(wav_dst, pending["language"])
        store.encoded_path(voice_id).parent.mkdir(parents=True, exist_ok=True)
        torch.save(codes, store.encoded_path(voice_id))
    except Exception as exc:  # noqa: BLE001
        log.exception("encode_reference failed (confirm)")
        wav_dst.unlink(missing_ok=True)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail={
            "error": "encode_failed", "message": str(exc)})

    duration = int(audio.audio_duration_seconds(wav_dst))
    voice = store.upsert({
        "id": voice_id,
        "name": pending["name"],
        "language": pending["language"],
        "backbone": _backbone_label(pending["language"]),
        "duration_seconds": duration,
    })
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
