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
        except ImportError as e:
            log.error("F5-TTS import failed: %s", e)
            raise

        # L'API F5-TTS a évolué : v1.0+ utilise `model="F5TTS_v1_Base"`
        # (et plus `model_type="F5-TTS"`). On tente la nouvelle signature
        # puis fallback ancienne pour résilience entre versions PyPI.
        try:
            self.engine = F5TTSEngine(
                model="F5TTS_v1_Base",   # nouvelle API (v1.0+)
                ckpt_file="",
                vocab_file="",
            )
            log.info("F5-TTS loaded (model='F5TTS_v1_Base')")
        except TypeError:
            # Fallback ancienne API (versions <1.0 si jamais)
            log.warning("F5-TTS: nouvelle API échouée, tentative ancienne signature")
            self.engine = F5TTSEngine(
                model_type="F5-TTS",
                ckpt_file=None,
                vocab_file=None,
            )
            log.info("F5-TTS loaded (legacy model_type='F5-TTS')")

    # ─── Synthèse complète (sans streaming) — utilisée pour cascade RVC ───

    def synthesize(self, text: str, voice_ref_b64: str,
                   language: str = "fr") -> np.ndarray:
        """Synthétise un audio complet avec une voix de référence donnée.

        Args:
            text: phrase à synthétiser (langue détectée automatiquement par F5-TTS
                  depuis le contenu du texte — F5-TTS V1 est multilingue natif).
            voice_ref_b64: WAV base64 de la voix de référence (clone OU native)
            language: code ISO de la langue cible (informatif — F5-TTS ne le prend
                      pas en param mais on log pour debug)

        Returns:
            np.ndarray float32 mono 24kHz
        """
        import tempfile
        # F5-TTS API v1.0+ : infer attend un PATH de fichier WAV pour la ref,
        # pas un np.ndarray. On écrit la ref en tmp et on passe le chemin.
        ref_audio = self._decode_voice_ref(voice_ref_b64)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            ref_path = f.name
        sf.write(ref_path, ref_audio, TTS_SAMPLE_RATE,
                 format="WAV", subtype="PCM_16")

        try:
            log.debug("F5-TTS.infer text_len=%d lang=%s", len(text), language)
            # Nouvelle API : signature (ref_file, ref_text, gen_text, ...)
            # Retourne typiquement (wav, sample_rate, spectrogram) en v1.0+
            result = self.engine.infer(
                ref_file=ref_path,
                ref_text="",         # auto-détect par F5-TTS
                gen_text=text,
                remove_silence=False,
            )
        finally:
            os.unlink(ref_path)

        # result peut être : np.ndarray (ancien) OU tuple (wav, sr, spect) (v1+)
        if isinstance(result, tuple):
            audio = result[0]
        else:
            audio = result
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
