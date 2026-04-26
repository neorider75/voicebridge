"""Wrapper Coqui XTTS-v2 — engine alternatif à NeuTTS pour le TTS fichier.

XTTS-v2 (~1.7B params) donne un clonage de voix plus naturel que NeuTTS
Nano (0.2B), au prix d'une inférence plus lente et d'un modèle ~3 Go en
RAM. Idéal pour le studio TTS quand on veut la meilleure qualité.

API officielle (cf. https://docs.coqui.ai/) :

    from TTS.api import TTS
    tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to("cpu")
    wav = tts.tts(
        text="Bonjour le monde",
        speaker_wav="reference.wav",
        language="fr",
    )
    # wav = list de floats à 24 kHz mono

Pas de pré-encodage de la voix : XTTS lit directement le WAV de
référence à chaque inférence. Du coup les voix créées pour NeuTTS
fonctionnent telles quelles avec XTTS (on utilise le même WAV).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from . import manager as mgr

log = logging.getLogger("voicebridge.tts.xtts")

# Modèle Coqui XTTS-v2. Nom registry standard ; aussi disponible sur
# Hugging Face sous "coqui/XTTS-v2".
XTTS_MODEL_NAME = "tts_models/multilingual/multi-dataset/xtts_v2"

# Langues supportées par XTTS-v2 (cf. doc Coqui). On expose juste FR/EN
# pour rester aligné avec le reste de la stack.
SUPPORTED_LANGUAGES = {"fr", "en"}

# Sample rate de sortie de XTTS-v2 (24 kHz mono — même que NeuTTS donc
# pas de resampling additionnel pour les routes TTS file).
XTTS_OUTPUT_SAMPLE_RATE = 24000


def _load_xtts() -> Any:
    """Factory invoquée par ModelManager au premier usage."""
    try:
        from TTS.api import TTS  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "Coqui XTTS-v2 non installé. Vérifiez que `coqui-tts` est dans "
            "le venv (sudo -u voicebridge /var/voicebridge/venv/bin/pip install "
            "coqui-tts)."
        ) from exc

    device = os.environ.get("VB_DEVICE", "cpu")
    log.info("XTTS-v2 loading (model=%s device=%s)…", XTTS_MODEL_NAME, device)
    tts = TTS(XTTS_MODEL_NAME).to(device)
    log.info("XTTS-v2 loaded device=%s", device)
    return tts


def register_loaders() -> None:
    """À appeler au boot (depuis main.py)."""
    mgr.manager.register_loader(mgr.MODEL_XTTS_V2, _load_xtts)


def infer(text: str, voice_wav_path: Path, language: str) -> Any:
    """Synthétise un WAV (np.ndarray float32 à 24 kHz mono).

    Args:
        text: texte à synthétiser
        voice_wav_path: chemin vers le WAV de référence (= la voix à cloner)
        language: code langue ISO (fr, en, …)

    Returns:
        np.ndarray float32 [-1, 1] mono à 24 kHz.
    """
    if language not in SUPPORTED_LANGUAGES:
        raise ValueError(
            f"langue non supportée par XTTS-v2 (notre wrapper) : {language}"
        )
    if not Path(voice_wav_path).exists():
        raise FileNotFoundError(f"voice wav introuvable : {voice_wav_path}")

    tts = mgr.manager.get(mgr.MODEL_XTTS_V2)
    wav = tts.tts(
        text=text,
        speaker_wav=str(voice_wav_path),
        language=language,
    )
    # `tts.tts()` peut retourner list[float] ou np.ndarray selon la version.
    # On normalise en np.ndarray float32.
    try:
        import numpy as np  # type: ignore
        if not hasattr(wav, "dtype"):
            wav = np.asarray(wav, dtype=np.float32)
        elif wav.dtype != np.float32:
            wav = wav.astype(np.float32)
    except ImportError:
        pass  # numpy n'est pas dispo (très improbable)
    return wav
