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
        VB_XTTS_SPEED             (défaut 1.05) — vitesse parole (0.7-1.3)
        VB_XTTS_PITCH_SHIFT       (défaut 0)    — semi-tons de shift post-process
                                                  (négatif = plus grave, ex -1.5)
        VB_XTTS_GPT_COND_LEN      (défaut 30)   — secondes de réf utilisées
                                                  par GPT pour conditionnement.
                                                  Plus haut = identité mieux
                                                  capturée (utilise jusqu'à
                                                  30s de la voix source).
        VB_XTTS_GPT_COND_CHUNK_LEN (défaut 4)
        VB_XTTS_MAX_REF_LEN       (défaut 10)   — secondes de réf utilisées
                                                  pour le décodeur diffusion.
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
        # 0.7 = sweet spot empirique entre 0.65 (défaut Coqui, plus conservateur)
        # et 0.8 (testé "trop généré").
        "temperature": _read_env_float("VB_XTTS_TEMPERATURE", 0.7),
        "length_penalty": _read_env_float("VB_XTTS_LENGTH_PENALTY", 1.0),
        "repetition_penalty": _read_env_float("VB_XTTS_REPETITION_PENALTY", 2.0),
        "top_k": _read_env_int("VB_XTTS_TOP_K", 50),
        "top_p": _read_env_float("VB_XTTS_TOP_P", 0.85),
        "speed": _read_env_float("VB_XTTS_SPEED", 1.10),
        # gpt_cond_len = secondes de réf utilisées par le speaker encoder.
        # Défaut Coqui interne ~6s. Notre WAV est trimé à 15s, donc 10s
        # laisse une marge confortable et améliore la capture d'identité
        # sans risquer le pad-up qui dégrade l'audio (testé avec 30 :
        # étouffé/entrecoupé).
        "gpt_cond_len": _read_env_int("VB_XTTS_GPT_COND_LEN", 10),
    }
    # gpt_cond_chunk_len et max_ref_len : on laisse les défauts Coqui
    # (calibrés pour marcher), override possible via env vars.
    if "VB_XTTS_GPT_COND_CHUNK_LEN" in os.environ:
        params["gpt_cond_chunk_len"] = _read_env_int("VB_XTTS_GPT_COND_CHUNK_LEN", 4)
    if "VB_XTTS_MAX_REF_LEN" in os.environ:
        params["max_ref_len"] = _read_env_int("VB_XTTS_MAX_REF_LEN", 10)

    log.debug("XTTS infer params: %s", params)
    try:
        wav = tts.tts(
            text=text,
            speaker_wav=str(voice_wav_path),
            language=language,
            **params,
        )
    except TypeError as exc:
        # Si la version installée de coqui-tts n'accepte pas tous les
        # kwargs (ex: gpt_cond_len ajouté plus tard), on retombe sur la
        # signature minimale + sampling de base.
        log.warning("XTTS tts() : kwarg refusé (%s) — fallback signature minimale", exc)
        minimal_params = {
            k: params[k]
            for k in ("temperature", "length_penalty", "repetition_penalty",
                      "top_k", "top_p", "speed")
            if k in params
        }
        wav = tts.tts(
            text=text,
            speaker_wav=str(voice_wav_path),
            language=language,
            **minimal_params,
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

    # Compression des silences entre phrases : XTTS surdose souvent les
    # pauses (1-2s entre deux phrases). On cap chaque pause à MAX_PAUSE_S
    # tout en préservant les pauses naturelles plus courtes.
    max_pause_s = _read_env_float("VB_XTTS_MAX_PAUSE_S", 0.4)
    if max_pause_s > 0:
        wav = _compress_silences(wav, max_pause_s, threshold_db=40)

    # Pitch shift post-traitement (XTTS n'expose pas de knob direct sur la
    # hauteur de la voix — elle est dictée par le speaker_wav). Si l'audio
    # généré sonne un poil aigu/grave par rapport à la voix d'origine, on
    # peut compenser avec un shift en demi-tons via librosa.
    # 0 = pas de shift (défaut). -1 = un demi-ton plus grave. -2 = deux demi-tons.
    pitch_shift = _read_env_float("VB_XTTS_PITCH_SHIFT", 0.0)
    if abs(pitch_shift) > 0.05:
        try:
            import librosa  # type: ignore
            import numpy as np  # type: ignore
            wav = librosa.effects.pitch_shift(
                np.asarray(wav, dtype=np.float32),
                sr=XTTS_OUTPUT_SAMPLE_RATE,
                n_steps=pitch_shift,
            )
            log.debug("XTTS pitch shifted by %.2f semitones", pitch_shift)
        except Exception as exc:  # noqa: BLE001
            log.warning("pitch_shift failed: %s", exc)

    return wav


def _compress_silences(wav, max_pause_s: float, threshold_db: float = 40):
    """Plafonne la durée des silences dans `wav` à `max_pause_s` (en secondes).

    Utilise librosa.effects.split pour détecter les segments non-silencieux,
    puis reconstruit l'audio avec des pauses inter-segments capées :
        pause_finale = min(pause_originale, max_pause_s)

    Conserve donc les pauses courtes naturelles (virgules, respirations
    < max_pause_s) intactes — seules les pauses longues (entre phrases,
    fins de paragraphe) sont raccourcies.

    threshold_db : seuil au-dessus du peak en dB pour considérer comme
    "voix" (40 = standard pour la voix humaine).
    """
    try:
        import librosa  # type: ignore
        import numpy as np  # type: ignore
    except ImportError:
        return wav
    arr = np.asarray(wav, dtype=np.float32)
    if arr.ndim > 1:
        arr = arr.squeeze()
    if arr.size == 0:
        return wav
    intervals = librosa.effects.split(arr, top_db=threshold_db)
    if len(intervals) <= 1:
        return wav  # rien à compacter (un seul segment ou aucun)
    max_pause_samples = int(max_pause_s * XTTS_OUTPUT_SAMPLE_RATE)
    parts = []
    last_end = 0
    saved_total = 0
    for i, (start, end) in enumerate(intervals):
        if i > 0:
            pause_len = start - last_end
            kept = min(pause_len, max_pause_samples)
            saved_total += pause_len - kept
            # On utilise le silence original tronqué (peut contenir un peu
            # de bruit ambiant cohérent), pas un zero-fill (qui sonne mort).
            parts.append(arr[last_end:last_end + kept])
        parts.append(arr[start:end])
        last_end = end
    out = np.concatenate(parts) if parts else arr
    if saved_total > 0:
        log.info("compressed silences: saved %.2fs (%d intervals)",
                 saved_total / XTTS_OUTPUT_SAMPLE_RATE, len(intervals) - 1)
    return out
