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
import hashlib
import io
import logging
import os
from typing import Generator

import numpy as np
import soundfile as sf

log = logging.getLogger("voicebridge.f5tts")


def _trim_trailing(audio: np.ndarray, sample_rate: int,
                   peak: float) -> np.ndarray:
    """Tronque le bruit/silence en fin d'un signal float32 mono.

    Algorithme : windowing 40 ms en arrière, on coupe à la dernière window
    dont le RMS dépasse 1.5 % du peak. Garde 80 ms de marge pour ne pas
    couper la fin du dernier mot.

    Effet : enlève typiquement 0.5-1.5 s d'artefact F5-TTS en fin de phrase.
    """
    window_size = max(1, int(sample_rate * 0.04))   # 40 ms
    margin = max(1, int(sample_rate * 0.08))        # 80 ms
    threshold = peak * 0.015

    # Itère de la fin vers le début en sautant par windows
    last_active = len(audio)
    for end in range(len(audio), window_size, -window_size):
        start = max(0, end - window_size)
        window = audio[start:end]
        if window.size == 0:
            continue
        rms = float(np.sqrt(np.mean(window.astype(np.float32) ** 2)))
        if rms > threshold:
            last_active = end
            break

    cut_at = min(len(audio), last_active + margin)
    if cut_at < len(audio):
        log.info("F5-TTS trim trailing: %d → %d samples (-%d / -%.2fs)",
                 len(audio), cut_at, len(audio) - cut_at,
                 (len(audio) - cut_at) / sample_rate)
    return audio[:cut_at]

HF_CACHE = os.environ.get("HF_HOME", "/runpod-volume/hf-cache")
TTS_SAMPLE_RATE = 24000

# Streaming chunk size en ms (balance latence / stabilité réseau)
STREAM_CHUNK_MS = 200

# Cache mondial : hash WAV ref → texte transcrit. Évite de retranscrire
# la même voix de référence à chaque phrase synthétisée.
_REF_TEXT_CACHE: dict[str, str] = {}


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

    def _get_ref_text(self, voice_ref_b64: str, ref_path: str,
                      language: str) -> str:
        """Transcrit la voix de référence avec Whisper (multilingue, langue
        forcée) et cache le résultat par hash WAV.

        Sans cette étape, F5-TTS auto-transcrit en interne via son propre
        Whisper sans hint de langue → détecte EN par défaut → la prosodie
        générée a un accent anglais même en mode FR→FR.
        """
        key = hashlib.sha1(voice_ref_b64.encode("ascii")).hexdigest()[:16]
        cached = _REF_TEXT_CACHE.get(key)
        if cached is not None:
            log.debug("F5-TTS ref_text cache hit key=%s lang=%s", key, language)
            return cached

        try:
            # On utilise notre Whisper (déjà chargé pour le STT) qui supporte
            # plus de langues et qu'on peut forcer sur la bonne langue.
            from .whisper import WhisperSTT  # noqa: PLC0415
            # Lazy : si Whisper pas encore chargé, F5-TTS auto-fallback
            # (le shared singleton est géré par handler.py)
            from faster_whisper import WhisperModel  # type: ignore
            # On lit le WAV qu'on vient d'écrire et on le passe en numpy
            audio_array, sr = sf.read(ref_path)
            if audio_array.ndim > 1:
                audio_array = audio_array.mean(axis=1)
            if sr != 16000:
                ratio = 16000 / sr
                new_len = int(len(audio_array) * ratio)
                x_old = np.linspace(0, 1, len(audio_array), endpoint=False)
                x_new = np.linspace(0, 1, new_len, endpoint=False)
                audio_array = np.interp(x_new, x_old, audio_array)
            audio_array = audio_array.astype(np.float32)
            # On accède au singleton Whisper via le getter du handler
            import handler  # type: ignore
            whisper = handler.get_whisper()
            segments, _ = whisper.model.transcribe(
                audio_array,
                language=language,
                beam_size=1,
                vad_filter=False,
                condition_on_previous_text=False,
                without_timestamps=True,
            )
            ref_text = " ".join(s.text.strip() for s in segments).strip()
        except Exception as exc:  # noqa: BLE001
            log.warning("F5-TTS ref transcription failed (%s) — fallback empty",
                        exc)
            ref_text = ""

        _REF_TEXT_CACHE[key] = ref_text
        log.info("F5-TTS ref_text computed key=%s lang=%s text=%r",
                 key, language, ref_text[:80])
        return ref_text

    def synthesize(self, text: str, voice_ref_b64: str,
                   language: str = "fr") -> tuple[np.ndarray, int]:
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
            # Pré-transcription de la ref avec NOTRE Whisper (langue forcée)
            # → évite l'auto-detect EN-biased de F5-TTS qui dégrade la prosodie
            # et tronque les phrases longues sur du non-anglais.
            ref_text = self._get_ref_text(voice_ref_b64, ref_path, language)

            log.debug("F5-TTS.infer text_len=%d lang=%s ref_text_len=%d",
                      len(text), language, len(ref_text))
            # Nouvelle API : signature (ref_file, ref_text, gen_text, ...)
            # Retourne typiquement (wav, sample_rate, spectrogram) en v1.0+
            result = self.engine.infer(
                ref_file=ref_path,
                ref_text=ref_text,   # NON vide : prosodie correcte + génération complète
                gen_text=text,
                # remove_silence=True : F5-TTS retire les blancs internes
                # ET tronque les silences finals. Sans ça, F5-TTS génère
                # un "trail" de 0.5-1.5 s de bruit en fin de phrase qui
                # boucle dans la queue audio jusqu'au chunk suivant.
                remove_silence=True,
                # cross_fade_duration : F5-TTS split auto les longs gen_text
                # par phrases. Ce paramètre adoucit les jonctions inter-chunks.
                cross_fade_duration=0.15,
            )
        finally:
            os.unlink(ref_path)

        # result peut être : np.ndarray (ancien) OU tuple (wav, sr, spect) (v1+)
        sr_out = TTS_SAMPLE_RATE
        if isinstance(result, tuple):
            audio = result[0]
            if len(result) >= 2 and isinstance(result[1], (int, float)):
                sr_out = int(result[1])
        else:
            audio = result

        # F5-TTS peut renvoyer un torch.Tensor — on bascule en numpy.
        if hasattr(audio, "detach") and hasattr(audio, "cpu"):
            audio = audio.detach().cpu().numpy()
        audio = np.asarray(audio)
        if audio.ndim > 1:
            audio = audio.squeeze()
            if audio.ndim > 1:
                audio = audio.mean(axis=0)  # multi-canal → mono

        # Normalise vers float32 ∈ [-1, 1]. Si l'audio est déjà int16, on
        # divise ; sinon on caste en float32.
        if audio.dtype == np.int16:
            audio = audio.astype(np.float32) / 32768.0
        else:
            audio = audio.astype(np.float32)

        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        log.info("F5-TTS.infer done: samples=%d sr=%d dtype=%s peak=%.3f",
                 audio.size, sr_out, audio.dtype, peak)
        if audio.size == 0:
            log.error("F5-TTS produced empty audio for text=%r", text[:80])
        elif peak < 1e-4:
            log.warning("F5-TTS produced near-silent audio (peak=%.5f)", peak)

        # ── Trim trailing artifact ────────────────────────────────────
        # F5-TTS produit parfois un "tail" de bruit basse amplitude (0.5-
        # 1.5 s) après la fin du speech utile. remove_silence=True dans
        # infer() retire les SILENCES mais pas le bruit (RMS > son seuil).
        # On fait ici un trim RMS-based plus agressif :
        # - Window de 40 ms glissante en arrière depuis la fin
        # - On coupe à la dernière window dont le RMS > 1.5 % du peak
        # - + 80 ms de marge pour ne pas couper la fin du mot
        if audio.size > 0 and peak > 1e-3:
            audio = _trim_trailing(audio, sr_out, peak)

        return audio, sr_out

    # ─── Synthèse streaming (chunk par chunk) ───

    def synthesize_streaming(
        self, text: str, voice_ref_b64: str, language: str = "fr"
    ) -> Generator[tuple[str, int, int], None, None]:
        """Synthétise en streaming. Yield (chunk_b64, seq, sample_rate) tuples.

        Pour V3.0 : on synthétise complet puis on chunke. Pour V3.1, hooker
        la génération autoregressive de F5-TTS pour vrai streaming
        (cf. Décision 5 du doc 00-decisions-v3.md).
        """
        full_audio, sr = self.synthesize(text, voice_ref_b64, language)
        yield from self._chunk_and_encode(full_audio, sr)

    # ─── Helpers ───

    def _chunk_and_encode(
        self, audio: np.ndarray, sample_rate: int = TTS_SAMPLE_RATE
    ) -> Generator[tuple[str, int, int], None, None]:
        """Découpe l'audio en chunks de STREAM_CHUNK_MS ms et encode en base64.

        Yield (chunk_b64, seq, sample_rate) — le sample_rate est propagé pour
        que le client puisse jouer l'audio à la bonne cadence.
        """
        chunk_samples = max(1, int(sample_rate * STREAM_CHUNK_MS / 1000))
        # Clip [-1, 1] pour éviter wrap-around lors du cast int16
        audio = np.clip(audio, -1.0, 1.0)
        seq = 0
        n_chunks = 0
        for i in range(0, len(audio), chunk_samples):
            chunk = audio[i:i + chunk_samples]
            pcm = (chunk * 32767.0).astype(np.int16).tobytes()
            chunk_b64 = base64.b64encode(pcm).decode("ascii")
            yield chunk_b64, seq, sample_rate
            seq += 1
            n_chunks += 1
        log.info("_chunk_and_encode: %d chunks (%d samples @ %d Hz)",
                 n_chunks, len(audio), sample_rate)

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
