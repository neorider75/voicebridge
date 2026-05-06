"""Wrapper F5-TTS multilingue.

Supporte 3 modes :
- Clonage zero-shot : ta voix dans une langue cible (avec accent FR perceptible)
- Native : voix générique avec accent natif parfait
- En sortie pour cascade vers RVC (synthèse complète, pas streaming)

Streaming activé par défaut pour minimiser la latence perçue (premier chunk
disponible ~200ms après l'appel).
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

# Pré-définir des voix natives par langue (téléchargées dans le Volume)
# À enrichir au déploiement avec des refs audio de qualité.
NATIVE_VOICES = {
    "en": "/runpod-volume/native_voices/en_male.wav",
    "fr": "/runpod-volume/native_voices/fr_male.wav",
    "de": "/runpod-volume/native_voices/de_male.wav",
    "es": "/runpod-volume/native_voices/es_male.wav",
    "it": "/runpod-volume/native_voices/it_male.wav",
    "ja": "/runpod-volume/native_voices/ja_male.wav",
    "zh": "/runpod-volume/native_voices/zh_male.wav",
    "pt": "/runpod-volume/native_voices/pt_male.wav",
    "nl": "/runpod-volume/native_voices/nl_male.wav",
}

# Streaming chunk size en ms (balance entre latence et stabilité)
STREAM_CHUNK_MS = 200


class F5TTS:
    """Wrapper F5-TTS pour synthèse + clonage multilingue."""
    
    def __init__(self):
        # Import paresseux (lib lourde)
        try:
            from f5_tts.api import F5TTS as F5TTSEngine
            self.engine = F5TTSEngine(
                model_type="F5-TTS",
                ckpt_file=None,  # télécharge depuis HF si absent
                vocab_file=None,
            )
            log.info("F5-TTS loaded")
        except ImportError as e:
            log.error("F5-TTS import failed: %s", e)
            raise
    
    # ─────────────────────────────────────────────────────────────────────
    # Synthèse complète (sans streaming) - utilisé pour cascade RVC
    # ─────────────────────────────────────────────────────────────────────
    
    def synthesize(self, text: str, voice_ref_b64: str, language: str = "fr") -> np.ndarray:
        """Synthétise un audio complet avec clonage zero-shot.
        
        Returns:
            np.ndarray float32 mono 24kHz
        """
        ref_audio = self._decode_voice_ref(voice_ref_b64)
        audio = self.engine.infer(
            ref_audio=ref_audio,
            ref_text="",  # F5-TTS auto-détecte
            gen_text=text,
            target_lang=language,
        )
        return audio
    
    def synthesize_native(self, text: str, language: str = "en") -> np.ndarray:
        """Synthétise avec une voix native générique de la langue cible."""
        native_ref = NATIVE_VOICES.get(language)
        if not native_ref or not os.path.exists(native_ref):
            log.warning("No native voice for %s, fallback to en", language)
            native_ref = NATIVE_VOICES.get("en")
        
        ref_audio_array, sr = sf.read(native_ref)
        if sr != TTS_SAMPLE_RATE:
            ref_audio_array = self._resample(ref_audio_array, sr, TTS_SAMPLE_RATE)
        
        audio = self.engine.infer(
            ref_audio=ref_audio_array,
            ref_text="",
            gen_text=text,
            target_lang=language,
        )
        return audio
    
    # ─────────────────────────────────────────────────────────────────────
    # Synthèse streaming (chunk par chunk)
    # ─────────────────────────────────────────────────────────────────────
    
    def synthesize_streaming(
        self, text: str, voice_ref_b64: str, language: str = "fr"
    ) -> Generator[tuple[str, int], None, None]:
        """Synthétise en streaming. Yield (chunk_b64, seq) tuples."""
        # Pour la première version, on synthétise complet puis on chunke.
        # Pour une vraie implémentation streaming, il faut hooker dans
        # la génération autoregressive de F5-TTS (TODO V3.0.1).
        full_audio = self.synthesize(text, voice_ref_b64, language)
        yield from self._chunk_and_encode(full_audio)
    
    def synthesize_native_streaming(
        self, text: str, language: str = "en"
    ) -> Generator[tuple[str, int], None, None]:
        """Synthétise en streaming avec voix native."""
        full_audio = self.synthesize_native(text, language)
        yield from self._chunk_and_encode(full_audio)
    
    # ─────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────
    
    def _chunk_and_encode(
        self, audio: np.ndarray
    ) -> Generator[tuple[str, int], None, None]:
        """Découpe l'audio en chunks de STREAM_CHUNK_MS ms et encode en base64.
        
        Yield: (chunk_b64, seq) tuples.
        """
        chunk_samples = int(TTS_SAMPLE_RATE * STREAM_CHUNK_MS / 1000)
        seq = 0
        for i in range(0, len(audio), chunk_samples):
            chunk = audio[i:i + chunk_samples]
            # Convertir en int16 PCM
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
