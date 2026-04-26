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


def _read_env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _read_env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def infer(text: str, voice_wav_path: Path, language: str) -> Any:
    """Synthétise un WAV (np.ndarray float32 à 24 kHz mono).

    Args:
        text: texte à synthétiser
        voice_wav_path: chemin vers le WAV de référence (= la voix à cloner)
        language: code langue ISO (fr, en, …)

    Returns:
        np.ndarray float32 [-1, 1] mono à 24 kHz.

    Paramètres ajustables via env vars (cf. README) :
        VB_XTTS_TEMPERATURE       (défaut 0.7)  — diversité prosodique
        VB_XTTS_TOP_K             (défaut 50)   — pool de candidats
        VB_XTTS_TOP_P             (défaut 0.85) — nucleus sampling
        VB_XTTS_LENGTH_PENALTY    (défaut 1.0)
        VB_XTTS_REPETITION_PENALTY (défaut 2.0) — anti-répétitions
        VB_XTTS_SPEED             (défaut 1.0)  — vitesse parole (0.7-1.3)
    """
    if language not in SUPPORTED_LANGUAGES:
        raise ValueError(
            f"langue non supportée par XTTS-v2 (notre wrapper) : {language}"
        )
    if not Path(voice_wav_path).exists():
        raise FileNotFoundError(f"voice wav introuvable : {voice_wav_path}")

    tts = mgr.manager.get(mgr.MODEL_XTTS_V2)

    # Lecture des paramètres tunables au moment de l'inférence (pas au load
    # du modèle) → permet de bouger via env var sans restart, ou plus tard
    # via un payload UI sans redéploiement.
    params = {
        "temperature": _read_env_float("VB_XTTS_TEMPERATURE", 0.7),
        "length_penalty": _read_env_float("VB_XTTS_LENGTH_PENALTY", 1.0),
        "repetition_penalty": _read_env_float("VB_XTTS_REPETITION_PENALTY", 2.0),
        "top_k": _read_env_int("VB_XTTS_TOP_K", 50),
        "top_p": _read_env_float("VB_XTTS_TOP_P", 0.85),
        "speed": _read_env_float("VB_XTTS_SPEED", 1.0),
    }

    log.debug("XTTS infer params: %s", params)
    wav = tts.tts(
        text=text,
        speaker_wav=str(voice_wav_path),
        language=language,
        **params,
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
