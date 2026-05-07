"""Wrapper Whisper Distil-Large-V3 pour STT multilingue.

Utilise faster-whisper (CTranslate2) pour des perfs ~2x meilleures
que la version transformers.
"""
from __future__ import annotations

import base64
import io
import logging
import os
from pathlib import Path

import numpy as np
import soundfile as sf

log = logging.getLogger("voicebridge.whisper")


# Modèle distillé : qualité ~équivalente à Large V3 mais 6x plus rapide
MODEL_ID = "Systran/faster-distil-whisper-large-v3"
HF_CACHE = os.environ.get("HF_HOME", "/runpod-volume/hf-cache")


class WhisperSTT:
    """Wrapper unifié pour la transcription audio.
    
    Utilise faster-whisper (recommandé) ou fallback vers transformers.
    """
    
    def __init__(self):
        try:
            from faster_whisper import WhisperModel
            self.model = WhisperModel(
                MODEL_ID,
                device="cuda",
                compute_type="float16",
                download_root=HF_CACHE,
            )
            self.backend = "faster-whisper"
        except ImportError:
            log.warning("faster-whisper non disponible, fallback transformers")
            self._init_transformers()
            self.backend = "transformers"
        
        log.info("Whisper loaded backend=%s", self.backend)
    
    def _init_transformers(self):
        from transformers import AutoProcessor, AutoModelForSpeechSeq2Seq
        import torch
        self.processor = AutoProcessor.from_pretrained(MODEL_ID, cache_dir=HF_CACHE)
        self.model = AutoModelForSpeechSeq2Seq.from_pretrained(
            MODEL_ID,
            torch_dtype=torch.float16,
            cache_dir=HF_CACHE,
        ).to("cuda")
        self.model.eval()
    
    def transcribe(self, audio_b64: str, language: str = "fr") -> str:
        """Transcrit un audio encodé en base64.
        
        Args:
            audio_b64: WAV encodé en base64 (PCM 16-bit, mono, 16kHz ou 24kHz)
            language: code langue ISO (fr, en, de, es, it, ja, zh, etc.)
        
        Returns:
            Texte transcrit
        """
        # Décoder le base64
        audio_bytes = base64.b64decode(audio_b64)
        audio_array, sample_rate = sf.read(io.BytesIO(audio_bytes))
        
        # Mono
        if audio_array.ndim > 1:
            audio_array = audio_array.mean(axis=1)
        
        # Resample à 16kHz si nécessaire (faster-whisper attend 16kHz)
        if sample_rate != 16000:
            audio_array = self._resample(audio_array, sample_rate, 16000)
        
        audio_array = audio_array.astype(np.float32)
        
        if self.backend == "faster-whisper":
            return self._transcribe_faster(audio_array, language)
        else:
            return self._transcribe_transformers(audio_array, language)
    
    def _transcribe_faster(self, audio: np.ndarray, language: str) -> str:
        segments, info = self.model.transcribe(
            audio,
            language=language,
            beam_size=1,                # rapide
            vad_filter=False,           # déjà fait côté Hostinger
            condition_on_previous_text=False,
            without_timestamps=True,
        )
        text = " ".join([s.text.strip() for s in segments])
        return text.strip()
    
    def _transcribe_transformers(self, audio: np.ndarray, language: str) -> str:
        import torch
        inputs = self.processor(audio, sampling_rate=16000, return_tensors="pt").to("cuda")
        forced_decoder_ids = self.processor.get_decoder_prompt_ids(
            language=language, task="transcribe"
        )
        with torch.no_grad():
            generated = self.model.generate(
                inputs.input_features.half(),
                forced_decoder_ids=forced_decoder_ids,
                max_length=440,
            )
        text = self.processor.batch_decode(generated, skip_special_tokens=True)[0]
        return text.strip()
    
    @staticmethod
    def _resample(audio: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
        """Resample naïf via interpolation linéaire (suffisant pour STT)."""
        ratio = sr_out / sr_in
        new_len = int(len(audio) * ratio)
        x_old = np.linspace(0, 1, len(audio), endpoint=False)
        x_new = np.linspace(0, 1, new_len, endpoint=False)
        return np.interp(x_new, x_old, audio)
