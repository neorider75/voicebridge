"""Routes ``/api/detection/*`` (livraison 5).

POST /api/detection/analyze :
- multipart audio (max 50 Mo)
- mode : "watermark" | "spectral" | "both" (défaut "both")
- retourne verdict combiné + détails

Logique de verdict (cf. spec V1 §02-features-v1.md tableau "Logique
de verdict combiné") :

    Watermark | Spectral   | Verdict
    ----------|-----------|--------------------------------------
    Présent   | Synthetic | 🤖 Généré par IA (VoiceBridge)
    Présent   | Real      | 🤖 Généré par IA (VoiceBridge, audio préservé)
    Absent    | Synthetic | 🤖 Généré par IA (origine inconnue)
    Absent    | Real      | ✅ Non généré par IA
"""
from __future__ import annotations

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

try:
    import soundfile as sf  # type: ignore
    ML_AVAILABLE = True
except ImportError:
    sf = None  # type: ignore
    ML_AVAILABLE = False

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status

from .. import config
from ..auth import require_auth
from ..limiter import limiter
from ..services import audio as audio_svc

router = APIRouter(prefix="/api/detection", tags=["detection"], dependencies=[Depends(require_auth)])
log = logging.getLogger("voicebridge.detection")

MAX_DETECTION_BYTES = 50 * 1024 * 1024  # 50 Mo (cf. spec)
DETECTION_SAMPLE_RATE = 16000


def _require_ml() -> None:
    if not ML_AVAILABLE:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail={
            "error": "ml_unavailable",
            "message": "Modèles ML non disponibles (mode minimal). Relancez install.sh sans --minimal.",
        })


def _combined_verdict(watermark: dict, spectral: dict | None) -> tuple[str, float, str]:
    """Retourne ``(verdict, confidence, message_court)``."""
    wm_detected = bool(watermark.get("detected"))
    spectral_label = (spectral or {}).get("label", "real")
    spectral_conf = (spectral or {}).get("confidence", 0)

    if wm_detected and spectral_label == "fake":
        return "ai_generated", spectral_conf, "Généré par IA (watermark VoiceBridge présent)"
    if wm_detected and spectral_label == "real":
        return "ai_generated", spectral_conf, "Généré par IA (VoiceBridge, audio préservé)"
    if not wm_detected and spectral_label == "fake":
        return "ai_generated", spectral_conf, "Généré par IA (origine inconnue)"
    return "human", spectral_conf, "Non généré par IA"


@router.post("/analyze")
@limiter.limit("20/minute")
async def analyze(
    request: Request,
    audio: UploadFile = File(...),
    mode: str = Form("both"),
):
    _require_ml()
    if mode not in ("watermark", "spectral", "both"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail={
            "error": "invalid_mode", "message": "mode : watermark, spectral ou both"})

    config.TMP_DIR.mkdir(parents=True, exist_ok=True)
    src_path = None
    wav_path = None
    try:
        with NamedTemporaryFile(delete=False, dir=config.TMP_DIR,
                                suffix=Path(audio.filename or "").suffix) as tmp:
            src_path = Path(tmp.name)
            shutil.copyfileobj(audio.file, tmp)

        try:
            audio_svc.validate_upload(src_path, MAX_DETECTION_BYTES)
        except audio_svc.AudioError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={
                "error": "audio_invalid", "message": str(exc)})

        # Watermark Perth nécessite le sample rate d'origine ; on convertit
        # tout de même en WAV propre 16 kHz mono pour le spectral.
        wav_path = config.TMP_DIR / (src_path.stem + ".det.wav")
        try:
            audio_svc.to_wav_16k_mono(src_path, wav_path)
        except audio_svc.AudioError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={
                "error": "audio_convert_failed", "message": str(exc)})

        watermark_result = {"checked": False, "detected": False, "tampered": False}
        spectral_result = None

        from ..models import detection as det_model  # import paresseux

        if mode in ("watermark", "both"):
            watermark_result = det_model.detect_perth_watermark(wav_path)

        if mode in ("spectral", "both"):
            try:
                data, sr = sf.read(str(wav_path), dtype="float32", always_2d=False)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail={
                    "error": "read_audio_failed", "message": str(exc)})
            try:
                spectral_result = det_model.analyze_spectral(data, int(sr))
            except RuntimeError as exc:
                log.exception("Spectral analysis failed")
                raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail={
                    "error": "spectral_failed", "message": str(exc)})

        verdict, confidence, msg = _combined_verdict(
            watermark_result, spectral_result if spectral_result else {"label": "real"},
        )

        return {
            "verdict": verdict,
            "confidence": confidence,
            "summary": msg,
            "watermark": watermark_result,
            "spectral": (
                {**spectral_result, "model": "Deepfake-audio-detection-V2"}
                if spectral_result else {"checked": False}
            ),
            "metadata": {
                "filename": audio.filename or "",
                "duration_seconds": round(audio_svc.audio_duration_seconds(wav_path), 2),
                "analyzed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "mode": mode,
            },
        }
    finally:
        if src_path and src_path.exists():
            src_path.unlink(missing_ok=True)
        if wav_path and wav_path.exists():
            wav_path.unlink(missing_ok=True)
