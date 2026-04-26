"""Routes ``/api/stt/*`` — transcription audio (livraison 3).

Cf. ``02-features-v1.md`` (Studio STT, 4 phases) et ``05-backend-api.md``.

Pipeline :
- Upload WAV (multipart) → conversion WAV 24 kHz mono via ffmpeg
- ``KyutaiSpeechToTextProcessor + ForConditionalGeneration``
- Retour JSON : ``{text, duration_seconds, audio_url}`` (audio_url pour relire)

``/api/stt/generate`` réutilise la logique TTS (le frontend appelle
``/api/tts/generate`` directement à la phase 3 ; on garde l'endpoint pour
fidélité à la spec).
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from tempfile import NamedTemporaryFile

try:
    import soundfile as sf  # type: ignore
    ML_AVAILABLE = True
except ImportError:
    sf = None  # type: ignore
    ML_AVAILABLE = False

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse

from .. import config
from ..auth import require_auth
from ..services import audio as audio_svc
from ..utils import files
from .tts import generate as tts_generate  # alias pour /api/stt/generate

router = APIRouter(prefix="/api/stt", tags=["stt"], dependencies=[Depends(require_auth)])
log = logging.getLogger("voicebridge.stt")

MAX_AUDIO_BYTES = 100 * 1024 * 1024  # 100 Mo (5 min de WAV ≈ 30 Mo)
KYUTAI_SAMPLE_RATE = 24000


def _require_ml() -> None:
    if not ML_AVAILABLE:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail={
            "error": "ml_unavailable",
            "message": "Modèles ML non disponibles (mode minimal). Relancez install.sh sans --minimal.",
        })


@router.post("/transcribe")
async def transcribe(
    request: Request,
    audio: UploadFile = File(...),
    language: str = Form("fr"),
):
    _require_ml()
    if language not in ("fr", "en"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail={
            "error": "invalid_language", "message": "Langue : fr ou en"})

    # Import paresseux du wrapper Kyutai (qui dépend de transformers)
    from ..models import stt as stt_model

    # Sauvegarde upload, valide, convertit en WAV 24k mono
    config.TMP_DIR.mkdir(parents=True, exist_ok=True)
    src_path = None
    wav_path = config.TMP_DIR / (files.new_id("stt_") + ".wav")
    try:
        with NamedTemporaryFile(delete=False, dir=config.TMP_DIR,
                                suffix=Path(audio.filename or "").suffix) as tmp:
            src_path = Path(tmp.name)
            shutil.copyfileobj(audio.file, tmp)

        try:
            audio_svc.validate_upload(src_path, MAX_AUDIO_BYTES)
            audio_svc.to_wav_24k_mono(src_path, wav_path)
        except audio_svc.AudioError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={
                "error": "audio_invalid", "message": str(exc)})

        # Lecture du WAV en np.ndarray
        try:
            data, sr = sf.read(str(wav_path), dtype="float32", always_2d=False)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail={
                "error": "read_audio_failed", "message": str(exc)})

        try:
            text = stt_model.transcribe(data, int(sr))
        except RuntimeError as exc:
            log.exception("Kyutai STT a échoué")
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail={
                "error": "stt_failed", "message": str(exc)})

        duration = round(audio_svc.audio_duration_seconds(wav_path), 2)
        # On garde le WAV en /tmp/ pour permettre le replay côté client.
        return {
            "text": text,
            "duration_seconds": duration,
            "audio_url": f"/api/stt/preview/{wav_path.name}",
        }
    finally:
        if src_path and src_path.exists():
            src_path.unlink(missing_ok=True)


@router.get("/preview/{filename}")
async def stt_preview(filename: str):
    # ``filename`` est de la forme ``stt_xxxx.wav`` ; on valide via files.safe_id
    # sur la partie sans extension pour bloquer les path traversal.
    if not filename.endswith(".wav"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="non trouvé")
    files.safe_id(filename[:-4])
    p = files.ensure_inside(config.TMP_DIR, config.TMP_DIR / filename)
    if not Path(p).exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="non trouvé")
    return FileResponse(p, media_type="audio/wav")


# Alias /api/stt/generate → délègue à /api/tts/generate (même payload)
@router.post("/generate")
async def stt_generate(request: Request):
    # FastAPI ne peut pas re-dispatcher proprement vers tts.generate ici car
    # les paramètres de body sont parsés dans l'endpoint cible. On re-parse.
    from .tts import GeneratePayload, generate as tts_gen  # local import
    body = await request.json()
    payload = GeneratePayload(**body)
    return await tts_gen(request=request, payload=payload)
