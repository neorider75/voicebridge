"""Wrapper Kyutai STT 1B (FR + EN).

API officielle (cf. huggingface.co/kyutai/stt-1b-en_fr-trfs) :

    from transformers import (
        KyutaiSpeechToTextProcessor,
        KyutaiSpeechToTextForConditionalGeneration,
    )
    processor = KyutaiSpeechToTextProcessor.from_pretrained(model_id)
    model = KyutaiSpeechToTextForConditionalGeneration.from_pretrained(
        model_id, device_map='cpu', torch_dtype='auto')
    inputs = processor(audio_array)  # sample rate 24 kHz attendu
    tokens = model.generate(**inputs)
    text = processor.batch_decode(tokens, skip_special_tokens=True)[0]

Requiert ``transformers >= 4.53``.
"""
from __future__ import annotations

import logging
import os

from .. import config
from . import manager as mgr

log = logging.getLogger("voicebridge.stt")

# Sample rate attendu par Kyutai (différent de la spec — confirmé via HF model card)
KYUTAI_SAMPLE_RATE = 24000


def _load_kyutai():
    """Factory invoquée par ``ModelManager`` au premier usage."""
    from transformers import (  # type: ignore
        KyutaiSpeechToTextForConditionalGeneration,
        KyutaiSpeechToTextProcessor,
    )

    model_dir = config.MODELS_DIR / "kyutai-1b"
    device = os.environ.get("VB_DEVICE", "cpu")
    processor = KyutaiSpeechToTextProcessor.from_pretrained(str(model_dir))
    model = KyutaiSpeechToTextForConditionalGeneration.from_pretrained(
        str(model_dir), device_map=device, torch_dtype="auto",
    )
    return {"processor": processor, "model": model, "device": device}


def register_loaders() -> None:
    """À appeler au boot (depuis ``main.py``)."""
    mgr.manager.register_loader(mgr.MODEL_KYUTAI, _load_kyutai)


def transcribe(audio_array, sample_rate: int) -> str:
    """Transcrit un ``np.ndarray`` (mono, float32 ou int16). Le sample_rate
    fourni doit être ``KYUTAI_SAMPLE_RATE`` (24 kHz) ; sinon l'appelant doit
    avoir resamplé en amont via ffmpeg.
    """
    if sample_rate != KYUTAI_SAMPLE_RATE:
        raise ValueError(
            f"sample_rate attendu {KYUTAI_SAMPLE_RATE} Hz, reçu {sample_rate}"
        )
    bundle = mgr.manager.get(mgr.MODEL_KYUTAI)
    processor = bundle["processor"]
    model = bundle["model"]
    inputs = processor(audio_array)
    if hasattr(inputs, "to"):
        inputs = inputs.to(bundle["device"])
    tokens = model.generate(**inputs)
    decoded = processor.batch_decode(tokens, skip_special_tokens=True)
    return decoded[0] if decoded else ""
