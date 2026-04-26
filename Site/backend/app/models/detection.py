"""Wrapper détection deepfake : MelodyMachine/Deepfake-audio-detection-V2
+ détection du watermark Perth (Resemble AI, inclus avec NeuTTS).

Le modèle est un wav2vec2-base fine-tuné, classification binaire (real/fake)
à 16 kHz. ``id2label`` lu depuis ``config.json`` du modèle (au cas où les
labels seraient inversés selon la version).
"""
from __future__ import annotations

import logging

from .. import config
from . import manager as mgr

log = logging.getLogger("voicebridge.detection")

DETECTION_SAMPLE_RATE = 16000


DEEPFAKE_REPO = "MelodyMachine/Deepfake-audio-detection-V2"


def _load_deepfake_v2():
    """Factory invoquée par ``ModelManager``. Charge depuis le cache HF
    (variable HF_HOME positionnée par voicebridge.service).

    Le modèle est un wav2vec2 fine-tuné, classification binaire 16 kHz.
    id2label = {0: "fake", 1: "real"} (vérifié config.json HF).
    """
    from transformers import (  # type: ignore
        AutoFeatureExtractor,
        AutoModelForAudioClassification,
    )
    extractor = AutoFeatureExtractor.from_pretrained(DEEPFAKE_REPO)
    model = AutoModelForAudioClassification.from_pretrained(DEEPFAKE_REPO)
    model.eval()
    return {"extractor": extractor, "model": model}


def register_loaders() -> None:
    mgr.manager.register_loader(mgr.MODEL_DEEPFAKE_V2, _load_deepfake_v2)


def analyze_spectral(audio_array, sample_rate: int) -> dict:
    """Retourne ``{"label": "real"|"fake", "confidence": float (0-100)}``."""
    if sample_rate != DETECTION_SAMPLE_RATE:
        raise ValueError(f"sample_rate attendu {DETECTION_SAMPLE_RATE}, reçu {sample_rate}")

    import torch  # type: ignore

    bundle = mgr.manager.get(mgr.MODEL_DEEPFAKE_V2)
    extractor = bundle["extractor"]
    model = bundle["model"]
    inputs = extractor(audio_array, sampling_rate=DETECTION_SAMPLE_RATE, return_tensors="pt")
    with torch.no_grad():
        outputs = model(**inputs)
    probs = torch.softmax(outputs.logits, dim=-1).squeeze().tolist()
    if not isinstance(probs, list):
        probs = [probs]
    pred_idx = max(range(len(probs)), key=lambda i: probs[i])

    raw_label = (model.config.id2label or {}).get(pred_idx, str(pred_idx)).lower()
    # Normalisation : "fake"/"spoof"/"synthetic" → fake, sinon → real
    if any(k in raw_label for k in ("fake", "spoof", "synth", "ai")):
        normalized = "fake"
    else:
        normalized = "real"

    return {
        "label": normalized,
        "raw_label": raw_label,
        "confidence": round(probs[pred_idx] * 100, 1),
    }


def detect_perth_watermark(wav_path) -> dict:
    """Détecte le watermark Perth dans un WAV. Retourne :

    .. code-block:: python

        {"checked": True, "detected": bool, "tampered": bool}

    API officielle (cf. resemble-ai/Perth README + spec
    Spec/voicebridge_specs/10-models-and-tools.md) :

        from perth.perth_net.perth_net_implicit.perth_watermarker import (
            PerthImplicitWatermarker,
        )
        watermarker = PerthImplicitWatermarker(device="cpu")
        detected = watermarker.detect(audio_array, sample_rate=24000)  # → bool

    Selon la version Perth installée, l'import peut être à différents niveaux
    (perth.perth_net, resemble_perth.…). On tente les variantes connues.
    """
    PerthCls = None
    for import_path in (
        "perth.perth_net.perth_net_implicit.perth_watermarker",
        "perth",
        "resemble_perth",
    ):
        try:
            module = __import__(import_path, fromlist=["PerthImplicitWatermarker"])
            PerthCls = getattr(module, "PerthImplicitWatermarker", None)
            if PerthCls is not None:
                break
        except ImportError:
            continue

    if PerthCls is None:
        return {"checked": False, "detected": False, "tampered": False,
                "error": "perth_unavailable"}

    try:
        import soundfile as sf  # type: ignore
        audio, sr = sf.read(str(wav_path), dtype="float32", always_2d=False)
        watermarker = PerthCls(device="cpu")
        # API selon version : preferred .detect() → bool ; fallback heuristique
        # via .get_watermark() qui retourne un tableau de scores.
        if hasattr(watermarker, "detect"):
            detected = bool(watermarker.detect(audio, sample_rate=int(sr)))
            return {"checked": True, "detected": detected, "tampered": False}
        if hasattr(watermarker, "get_watermark"):
            import numpy as np  # type: ignore
            wm = watermarker.get_watermark(audio, sample_rate=int(sr))
            score = float(np.abs(np.asarray(wm)).mean())
            return {"checked": True, "detected": score > 0.001,
                    "tampered": False, "score": round(score, 4)}
        return {"checked": False, "detected": False, "tampered": False,
                "error": "perth_api_unknown"}
    except Exception as exc:  # noqa: BLE001
        log.exception("Perth watermark check failed")
        return {"checked": False, "detected": False, "tampered": False, "error": str(exc)}
