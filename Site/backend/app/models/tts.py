"""Wrapper NeuTTS Nano (Q4/Q8 × FR/EN).

API officielle (cf. github.com/neuphonic/neutts-air) :

    from neutts import NeuTTS
    tts = NeuTTS(backbone_repo=..., backbone_device='cpu',
                 codec_repo=..., codec_device='cpu')
    ref_codes = tts.encode_reference(wav_path)
    wav = tts.infer(text, ref_codes, ref_text)

Selon la version pip, le module peut s'appeler ``neutts`` ou ``neuttsair`` —
on essaie les deux à l'import.

Ce module ne charge **rien** au moment de son import : les modèles sont
créés via les factories enregistrées sur ``ModelManager``.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from .. import config
from . import manager as mgr

log = logging.getLogger("voicebridge.tts")


def _NeuTTSClass() -> type:
    """Import paresseux du wrapper NeuTTS (selon le nom de package installé)."""
    try:
        from neutts import NeuTTS  # type: ignore
        return NeuTTS
    except ImportError:
        try:
            from neuttsair.neutts import NeuTTSAir  # type: ignore
            return NeuTTSAir
        except ImportError as exc:
            raise RuntimeError(
                "Aucun package NeuTTS installé (essayé : neutts, neuttsair)."
            ) from exc


# ---------------------------------------------------------------------------
# Mapping : clé manager → (repo HuggingFace ID, qualité)
# ---------------------------------------------------------------------------
#
# On passe les repo IDs HuggingFace (et non des chemins filesystem) au
# constructeur NeuTTS. NeuTTS reconnaît "neuphonic/..." comme officiel,
# infère la langue/le format GGUF et résout le téléchargement depuis le
# cache HF local (variables HF_HOME / HUGGINGFACE_HUB_CACHE positionnées
# par voicebridge.service). Pas de re-download → résolution immédiate.

_BACKBONE_REPOS = {
    mgr.MODEL_NEUTTS_FR_Q4: ("neuphonic/neutts-nano-french-q4-gguf", "fr", "normal"),
    mgr.MODEL_NEUTTS_EN_Q4: ("neuphonic/neutts-nano-q4-gguf",        "en", "normal"),
    mgr.MODEL_NEUTTS_FR_Q8: ("neuphonic/neutts-nano-french-q8-gguf", "fr", "high"),
    mgr.MODEL_NEUTTS_EN_Q8: ("neuphonic/neutts-nano-q8-gguf",        "en", "high"),
}

CODEC_REPO = "neuphonic/neucodec"


def model_key_for(language: str, quality: str) -> str:
    quality = "high" if quality == "high" else "normal"
    if language == "fr":
        return mgr.MODEL_NEUTTS_FR_Q8 if quality == "high" else mgr.MODEL_NEUTTS_FR_Q4
    if language == "en":
        return mgr.MODEL_NEUTTS_EN_Q8 if quality == "high" else mgr.MODEL_NEUTTS_EN_Q4
    raise ValueError(f"langue non supportée : {language}")


def _make_loader(model_key: str):
    backbone_repo, _lang, _qual = _BACKBONE_REPOS[model_key]

    def _load() -> Any:
        Cls = _NeuTTSClass()
        device = os.environ.get("VB_DEVICE", "cpu")
        return Cls(
            backbone_repo=backbone_repo,
            backbone_device=device,
            codec_repo=CODEC_REPO,
            codec_device=device,
        )

    return _load


def register_loaders() -> None:
    """À appeler une fois au boot (depuis ``main.py``)."""
    for key in _BACKBONE_REPOS:
        mgr.manager.register_loader(key, _make_loader(key))


# ---------------------------------------------------------------------------
# Helpers pour les routes
# ---------------------------------------------------------------------------


def encode_reference(wav_path: Path, language: str) -> Any:
    """Encode un WAV de référence en ``ref_codes``.

    Utilise le modèle Q4 de la langue (suffisant pour l'encodage, plus rapide
    qu'avec le Q8). Le résultat doit être ``torch.save``-é par l'appelant.
    """
    key = model_key_for(language, "normal")
    tts = mgr.manager.get(key)
    return tts.encode_reference(str(wav_path))


def infer(text: str, ref_codes: Any, ref_text: str, language: str, quality: str):
    """Synthétise un WAV (np.ndarray ou bytes selon la version de NeuTTS)."""
    key = model_key_for(language, quality)
    tts = mgr.manager.get(key)
    return tts.infer(text, ref_codes, ref_text)
