"""Routes ``/api/tts/*`` — synthèse vocale fichier.

Cf. ``Spec/voicebridge_specs/02-features-v1.md`` (Studio TTS) et
``05-backend-api.md`` (POST /api/tts/generate).

Pipeline :
- charge ref_codes depuis voices/encoded/{id}.pt (en RAM en pratique mais ici on
  recharge à chaque génération pour rester import-safe)
- ``NeuTTS.infer(text, ref_codes, ref_text)`` (Q4 si quality=normal, Q8 si high)
- watermark Perth automatique (déjà câblé dans NeuTTS)
- export WAV (par défaut) ou MP3 via ffmpeg
- rétention "session" → stream direct, jamais écrit ; "24h"/"48h" → écrit dans
  ``data/audio/`` et inscrit dans ``recordings.metadata.json``
"""
from __future__ import annotations

import io
import logging
import wave
from pathlib import Path

try:
    import numpy as np  # type: ignore
    import soundfile as sf  # type: ignore
    import torch  # type: ignore
    ML_AVAILABLE = True
except ImportError:  # mode --minimal
    np = None  # type: ignore
    sf = None  # type: ignore
    torch = None  # type: ignore
    ML_AVAILABLE = False

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from .. import config
from ..auth import require_auth
from ..limiter import limiter
from ..models import tts as tts_model
from ..services import audio as audio_svc
from ..services import recordings_store
from ..services import voices_store
from ..utils import files

router = APIRouter(prefix="/api/tts", tags=["tts"], dependencies=[Depends(require_auth)])
log = logging.getLogger("voicebridge.tts")

MAX_TEXT_CHARS = 5000
NEUTTS_SAMPLE_RATE = 24000


class GeneratePayload(BaseModel):
    text: str = Field(..., min_length=1, max_length=MAX_TEXT_CHARS)
    voice_id: str = Field(..., min_length=1, max_length=100)
    format: str = Field(default="wav")  # "wav" | "mp3"
    quality: str = Field(default="high")  # "normal" | "high"
    retention: str = Field(default="session")  # "session" | "24h" | "48h"
    # Engine TTS : "neutts" (NeuTTS Nano, défaut, rapide) ou "xtts"
    # (Coqui XTTS-v2, plus naturel, 5-10x plus lent à inférer).
    engine: str | None = Field(default=None)


def _require_ml() -> None:
    if not ML_AVAILABLE:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail={
            "error": "ml_unavailable",
            "message": "Modèles ML non disponibles (installation minimale détectée). "
                       "Relancez sudo ./install.sh sans --minimal pour activer la synthèse.",
        })


def _validate(payload: GeneratePayload) -> None:
    if payload.format not in ("wav", "mp3"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail={
            "error": "invalid_format", "message": "format doit être wav ou mp3"})
    if payload.quality not in ("normal", "high"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail={
            "error": "invalid_quality", "message": "quality doit être normal ou high"})
    if payload.retention not in ("session", "24h", "48h"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail={
            "error": "invalid_retention", "message": "retention doit être session, 24h ou 48h"})
    if payload.engine is not None and payload.engine not in ("neutts", "xtts"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail={
            "error": "invalid_engine", "message": "engine doit être neutts ou xtts"})


def _resolve_engine(payload: GeneratePayload) -> str:
    """Détermine quel engine TTS utiliser. Si payload.engine est fourni
    explicitement, on l'honore. Sinon on retombe sur le default_tts_engine
    de la config (default = neutts si non set)."""
    if payload.engine in ("neutts", "xtts"):
        return payload.engine
    cfg_default = config.get("default_tts_engine", "neutts")
    if cfg_default not in ("neutts", "xtts"):
        cfg_default = "neutts"
    return cfg_default


def _to_wav_bytes(wav_data) -> bytes:
    """Sérialise un np.ndarray (float32 ou int16) ou tensor torch en WAV bytes."""
    if isinstance(wav_data, torch.Tensor):
        wav_data = wav_data.detach().cpu().numpy()
    arr = np.asarray(wav_data)
    if arr.ndim > 1:
        arr = arr.squeeze()
    # Normalisation : si float, on suppose [-1, 1] ; sf le gère.
    buf = io.BytesIO()
    sf.write(buf, arr, NEUTTS_SAMPLE_RATE, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def _synthesize(payload: GeneratePayload) -> tuple[Path | None, bytes, str]:
    """Synthétise et retourne (wav_path_temporaire_ou_None, wav_bytes, voice_name).

    Ne retourne jamais le MP3 ; la conversion MP3 se fait dans la route, après
    écriture WAV temp.

    Dispatch sur l'engine choisi : NeuTTS (rapide, défaut) ou XTTS-v2
    (plus naturel, plus lent).
    """
    voice_id = files.safe_id(payload.voice_id)
    voice = voices_store.get(voice_id)
    if not voice:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={
            "error": "voice_not_found", "message": "Voix introuvable"})

    # Voix en cours d'encodage en arrière-plan ou échec.
    voice_status = voice.get("status", "ready")
    if voice_status == "encoding":
        raise HTTPException(status.HTTP_409_CONFLICT, detail={
            "error": "voice_encoding",
            "message": "Cette voix est en cours d'encodage — réessayez dans quelques secondes."})
    if voice_status == "failed":
        raise HTTPException(status.HTTP_409_CONFLICT, detail={
            "error": "voice_failed",
            "message": "L'encodage de cette voix a échoué : "
                       + (voice.get("error_message") or "raison inconnue")
                       + ". Supprimez-la et recréez-la."})

    engine = _resolve_engine(payload)
    log.info("tts.generate engine=%s voice_id=%s lang=%s quality=%s",
             engine, voice_id, voice.get("language"), payload.quality)

    if engine == "xtts":
        wav_data = _synthesize_xtts(payload, voice, voice_id)
    else:
        wav_data = _synthesize_neutts(payload, voice, voice_id)

    wav_bytes = _to_wav_bytes(wav_data)
    return None, wav_bytes, voice["name"]


def _synthesize_neutts(payload: GeneratePayload, voice: dict, voice_id: str):
    """Pipeline NeuTTS : ref_codes (.pt) + ref_text (.txt) → infer."""
    encoded = voices_store.encoded_path(voice_id)
    if not encoded.exists():
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail={
            "error": "ref_codes_missing",
            "message": "ref_codes (.pt) introuvables — la voix doit être ré-encodée pour NeuTTS"})
    try:
        ref_codes = torch.load(encoded, weights_only=False)
    except Exception as exc:  # noqa: BLE001
        log.exception("torch.load ref_codes échoué")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail={
            "error": "ref_codes_load_failed", "message": str(exc)})

    ref_text = voices_store.read_ref_text(voice_id)
    if not ref_text:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={
            "error": "ref_text_missing",
            "message": (
                "Cette voix a été créée sans texte de référence — la synthèse "
                "NeuTTS produirait un audio incorrect. Supprimez la voix et "
                "recréez-la (en mode Enregistrement, le texte est sauvé "
                "automatiquement) — ou utilisez l'engine XTTS qui n'en a pas "
                "besoin."
            ),
        })

    try:
        return tts_model.infer(
            text=payload.text,
            ref_codes=ref_codes,
            ref_text=ref_text,
            language=voice["language"],
            quality=payload.quality,
        )
    except RuntimeError as exc:
        log.exception("NeuTTS.infer a échoué")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail={
            "error": "model_error", "message": str(exc)})


def _synthesize_xtts(payload: GeneratePayload, voice: dict, voice_id: str):
    """Pipeline XTTS-v2 : pas de pré-encodage, on lit directement le WAV."""
    from ..models import tts_xtts  # noqa: WPS433  (lazy : coqui-tts est lourd)

    wav_path = voices_store.wav_path(voice_id)
    if not wav_path.exists():
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail={
            "error": "voice_wav_missing",
            "message": "WAV de référence introuvable pour cette voix"})

    try:
        return tts_xtts.infer(
            text=payload.text,
            voice_wav_path=wav_path,
            language=voice["language"],
        )
    except FileNotFoundError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail={
            "error": "xtts_wav_missing", "message": str(exc)})
    except RuntimeError as exc:
        log.exception("XTTS.infer a échoué")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail={
            "error": "model_error", "message": str(exc)})
    except Exception as exc:  # noqa: BLE001
        log.exception("XTTS unexpected error")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail={
            "error": "xtts_failed", "message": str(exc)})


def _wav_duration_seconds(wav_bytes: bytes) -> float:
    buf = io.BytesIO(wav_bytes)
    with wave.open(buf, "rb") as wf:
        frames = wf.getnframes()
        rate = wf.getframerate() or NEUTTS_SAMPLE_RATE
    return frames / float(rate) if rate else 0.0


@router.post("/generate")
@limiter.limit("60/minute")
async def generate(request: Request, payload: GeneratePayload):
    _require_ml()
    _validate(payload)

    _, wav_bytes, voice_name = _synthesize(payload)
    duration = _wav_duration_seconds(wav_bytes)

    if payload.retention == "session":
        # Stream direct, aucun fichier sur disque.
        if payload.format == "mp3":
            # Conversion via ffmpeg : on passe par tmp.
            with audio_svc._ensure_tmp(config.TMP_DIR) if hasattr(audio_svc, "_ensure_tmp") else _noop():
                pass  # pragma: no cover (helper noop)
            tmp_wav = config.TMP_DIR / f"{files.new_id('s_')}.wav"
            tmp_mp3 = tmp_wav.with_suffix(".mp3")
            tmp_wav.parent.mkdir(parents=True, exist_ok=True)
            tmp_wav.write_bytes(wav_bytes)
            try:
                audio_svc.wav_to_mp3(tmp_wav, tmp_mp3)
                mp3_bytes = tmp_mp3.read_bytes()
            finally:
                tmp_wav.unlink(missing_ok=True)
                tmp_mp3.unlink(missing_ok=True)
            return StreamingResponse(
                iter([mp3_bytes]),
                media_type="audio/mpeg",
                headers={"Content-Disposition": "attachment; filename=voicebridge.mp3"},
            )
        return StreamingResponse(
            iter([wav_bytes]),
            media_type="audio/wav",
            headers={"Content-Disposition": "attachment; filename=voicebridge.wav"},
        )

    # Rétention 24h / 48h : on persiste sur disque.
    rec_id = files.new_id("rec_")
    wav_path = recordings_store.file_path(rec_id, "wav")
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    wav_path.write_bytes(wav_bytes)

    if payload.format == "mp3":
        mp3_path = recordings_store.file_path(rec_id, "mp3")
        try:
            audio_svc.wav_to_mp3(wav_path, mp3_path)
        except audio_svc.AudioError as exc:
            wav_path.unlink(missing_ok=True)
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail={
                "error": "encode_mp3_failed", "message": str(exc)})
        # On garde le WAV ET le MP3, mais l'utilisateur a demandé MP3.
        size_mb = round(mp3_path.stat().st_size / 1e6, 2)
    else:
        size_mb = round(wav_path.stat().st_size / 1e6, 2)

    voice = voices_store.get(payload.voice_id) or {}
    rec = recordings_store.add(
        {
            "id": rec_id,
            "mode": "tts",
            "voice_id": payload.voice_id,
            "voice_name": voice.get("name", voice_name),
            "voice_language": voice.get("language"),
            "duration_seconds": round(duration, 1),
            "format": payload.format,
            "quality": payload.quality,
            "size_mb": size_mb,
        },
        retention=payload.retention,
    )
    return JSONResponse({
        "id": rec_id,
        "url": f"/api/recordings/{rec_id}/audio",
        "expires_at": rec["expires_at"],
    })


def _noop():  # pragma: no cover
    class _N:
        def __enter__(self): return self
        def __exit__(self, *a): return False
    return _N()
