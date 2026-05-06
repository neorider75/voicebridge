"""Wrapper F5-TTS multilingue.

Le worker reçoit toujours une voix de référence en input (`voice_ref` base64
WAV), que ce soit pour le mode clone (sample de l'utilisateur) ou pour le
mode native (sample d'une voix native sélectionnée par l'utilisateur dans
sa bibliothèque /voices côté Hostinger).

→ Cf. Décision 2 du document 00-decisions-v3.md : pas de NATIVE_VOICES
  hardcodés côté worker, l'unification se fait dans le store de voix
  Hostinger via le champ ``kind: "clone" | "native"``.

Streaming = chunking post-synthèse pour V3.0 (cf. Décision 5). Vrai streaming
F5-TTS prévu pour V3.1.
"""
from __future__ import annotations

import base64
import io
import logging
import os
from typing import Generator

import numpy as np
import soundfile as sf

log = logging.getLogger("voicebridge.f5tts")

HF_CACHE = os.environ.get("HF_HOME", "/runpod-volume/hf-cache")
TTS_SAMPLE_RATE = 24000

# Streaming chunk size en ms (balance latence / stabilité réseau)
STREAM_CHUNK_MS = 200


class F5TTS:
    """Wrapper F5-TTS pour synthèse + clonage multilingue."""

    def __init__(self):
        try:
            from f5_tts.api import F5TTS as F5TTSEngine
            self.engine = F5TTSEngine(
                model_type="F5-TTS",
                ckpt_file=None,   # télécharge depuis HF si absent (premier appel)
                vocab_file=None,
            )
            log.info("F5-TTS loaded")
        except ImportError as e:
            log.error("F5-TTS import failed: %s", e)
            raise

    # ─── Synthèse complète (sans streaming) — utilisée pour cascade RVC ───

    def synthesize(self, text: str, voice_ref_b64: str,
                   language: str = "fr") -> np.ndarray:
        """Synthétise un audio complet avec une voix de référence donnée.

        Args:
            text: phrase à synthétiser (langue ``language``)
            voice_ref_b64: WAV base64 de la voix de référence (clone OU native)
            language: code ISO de la langue cible

        Returns:
            np.ndarray float32 mono 24kHz
        """
        ref_audio = self._decode_voice_ref(voice_ref_b64)
        audio = self.engine.infer(
            ref_audio=ref_audio,
            ref_text="",          # F5-TTS auto-détecte
            gen_text=text,
            target_lang=language,
        )
        return audio

    # ─── Synthèse streaming (chunk par chunk) ───

    def synthesize_streaming(
        self, text: str, voice_ref_b64: str, language: str = "fr"
    ) -> Generator[tuple[str, int], None, None]:
        """Synthétise en streaming. Yield (chunk_b64, seq) tuples.

        Pour V3.0 : on synthétise complet puis on chunke. Pour V3.1, hooker
        la génération autoregressive de F5-TTS pour vrai streaming
        (cf. Décision 5 du doc 00-decisions-v3.md).
        """
        full_audio = self.synthesize(text, voice_ref_b64, language)
        yield from self._chunk_and_encode(full_audio)

    # ─── Helpers ───

    def _chunk_and_encode(
        self, audio: np.ndarray
    ) -> Generator[tuple[str, int], None, None]:
        """Découpe l'audio en chunks de STREAM_CHUNK_MS ms et encode en base64."""
        chunk_samples = int(TTS_SAMPLE_RATE * STREAM_CHUNK_MS / 1000)
        seq = 0
        for i in range(0, len(audio), chunk_samples):
            chunk = audio[i:i + chunk_samples]
            pcm = (chunk * 32767.0).astype(np.int16).tobytes()
            chunk_b64 = base64.b64encode(pcm).decode("ascii")
            yield chunk_b64, seq
            seq += 1

    def _decode_voice_ref(self, voice_ref_b64: str) -> np.ndarray:
        """Décode un WAV base64 et retourne un np.ndarray float32 24kHz mono."""
        audio_bytes = base64.b64decode(voice_ref_b64)
        audio, sr = sf.read(io.BytesIO(audio_bytes))
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        if sr != TTS_SAMPLE_RATE:
            audio = self._resample(audio, sr, TTS_SAMPLE_RATE)
        return audio.astype(np.float32)

    @staticmethod
    def _resample(audio: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
        ratio = sr_out / sr_in
        new_len = int(len(audio) * ratio)
        x_old = np.linspace(0, 1, len(audio), endpoint=False)
        x_new = np.linspace(0, 1, new_len, endpoint=False)
        return np.interp(x_new, x_old, audio)
