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


KYUTAI_REPO = "kyutai/stt-1b-en_fr-trfs"


def _load_kyutai():
    """Factory invoquée par ``ModelManager`` au premier usage.

    Charge depuis le cache HF (HF_HOME positionné par voicebridge.service).
    """
    import torch  # type: ignore
    from transformers import (  # type: ignore
        KyutaiSpeechToTextForConditionalGeneration,
        KyutaiSpeechToTextProcessor,
    )
    device = os.environ.get("VB_DEVICE", "cpu")
    # Sur CPU, on force float32 plutôt que "auto" (qui peut tomber sur bfloat16).
    # bf16 sans AVX-512_BF16 hardware est dramatiquement plus lent que fp32 sur
    # CPU classique → STT à 13s au lieu de 1s. Sur GPU/CUDA, "auto" reste OK
    # (bf16 native).
    if device == "cpu":
        dtype = torch.float32
    else:
        dtype = "auto"
    processor = KyutaiSpeechToTextProcessor.from_pretrained(KYUTAI_REPO)
    model = KyutaiSpeechToTextForConditionalGeneration.from_pretrained(
        KYUTAI_REPO, device_map=device, torch_dtype=dtype,
    )
    log.info("Kyutai loaded device=%s dtype=%s", device, dtype)
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
