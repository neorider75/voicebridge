"""Utilitaires audio communs aux différents modèles."""
from __future__ import annotations

import base64
import io
from typing import Tuple

import numpy as np
import soundfile as sf


def decode_wav_b64(audio_b64: str) -> Tuple[np.ndarray, int]:
    """Décode un WAV base64 en (audio_array, sample_rate).

    Returns:
        (np.ndarray float32 mono, sample_rate)
    """
    audio_bytes = base64.b64decode(audio_b64)
    audio, sr = sf.read(io.BytesIO(audio_bytes))
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return audio.astype(np.float32), sr


def encode_wav_b64(audio: np.ndarray, sample_rate: int = 24000,
                   subtype: str = "PCM_16") -> str:
    """Encode un np.ndarray en WAV base64."""
    if audio.ndim > 1:
        audio = audio.squeeze()
    buf = io.BytesIO()
    sf.write(buf, audio, sample_rate, format="WAV", subtype=subtype)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def encode_pcm_b64(audio: np.ndarray, sample_rate: int = 24000) -> str:
    """Encode un np.ndarray en PCM 16-bit raw base64 (sans header WAV).

    Utilisé pour le streaming où le sample rate est connu côté client.
    """
    if audio.ndim > 1:
        audio = audio.squeeze()
    pcm = (audio * 32767.0).astype(np.int16).tobytes()
    return base64.b64encode(pcm).decode("ascii")


def resample_linear(audio: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    """Resample par interpolation linéaire (rapide, qualité OK pour voix)."""
    if sr_in == sr_out:
        return audio
    ratio = sr_out / sr_in
    new_len = int(len(audio) * ratio)
    x_old = np.linspace(0, 1, len(audio), endpoint=False)
    x_new = np.linspace(0, 1, new_len, endpoint=False)
    return np.interp(x_new, x_old, audio).astype(np.float32)


def chunk_audio(audio: np.ndarray, chunk_samples: int):
    """Generator yielding chunks de l'audio."""
    for i in range(0, len(audio), chunk_samples):
        yield audio[i:i + chunk_samples]


def normalize_peak(audio: np.ndarray, target_db: float = -3.0) -> np.ndarray:
    """Normalise l'audio à un peak donné (en dB FS)."""
    peak = np.max(np.abs(audio))
    if peak < 1e-6:
        return audio
    target_linear = 10 ** (target_db / 20)
    gain = target_linear / peak
    return audio * gain
