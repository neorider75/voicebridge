"""WebSocket ``/ws/stream`` — Live (livraison 4, refacto latence).

Pipeline (côté serveur) :

    client → WS                    : chunks PCM 16 kHz mono int16 (~100 ms)
    ↓ np.frombuffer (en RAM)      → float32
    ↓ Silero VAD (chunks 32 ms)   → tag speech/silence
    ↓ buffer circulaire 5 s       → deque
    ↓ flush condition             → silence > 400 ms OU speech > 4 s
    ↓ resample np.interp 16k→24k
    ↓ Kyutai STT                  → texte
    ↓ NeuTTS Q4 (ref_codes)       → WAV 24 kHz mono
    ↓ Perth watermark (auto)
    ↓ WS                          → audio_chunk JSON (WAV b64) au client

Différence avec la version POC initiale :
- AVANT : MediaRecorder webm 1 s + ffmpeg subprocess par chunk côté serveur
  → latence ~1,5-2 s
- MAINTENANT : AudioWorklet PCM raw 100 ms + np.frombuffer en RAM
  → latence cible 0,6-1,4 s (spec atteinte)

Auth : cookie session OU Bearer token au handshake.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
from collections import deque

try:
    import numpy as np  # type: ignore
    import soundfile as sf  # type: ignore
    import torch  # type: ignore
    ML_AVAILABLE = True
except ImportError:
    np = None  # type: ignore
    sf = None  # type: ignore
    torch = None  # type: ignore
    ML_AVAILABLE = False

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import auth as auth_mod
from ..services import voices_store
from ..utils import files

router = APIRouter(tags=["live"])
log = logging.getLogger("voicebridge.live")

VAD_SAMPLE_RATE = 16000
TTS_SAMPLE_RATE = 24000

# 32 ms / chunk Silero VAD → 5 s de buffer ≈ 156 chunks
BUFFER_TICKS = 156
SILENCE_FLUSH_TICKS = 13   # ~400 ms de silence → flush
SPEECH_FLUSH_TICKS = 125   # ~4 s de parole continue → flush forcé
VAD_CHUNK_SAMPLES = 512    # taille de bloc attendue par Silero VAD


def _ws_authenticated(ws: WebSocket) -> bool:
    return auth_mod._has_valid_session(ws) or auth_mod._has_valid_bearer(ws)  # type: ignore[arg-type]


async def _send_json(ws: WebSocket, payload: dict) -> None:
    try:
        await ws.send_json(payload)
    except Exception:  # noqa: BLE001
        pass


def _pcm_int16_bytes_to_float32(raw: bytes):
    """Convertit du PCM 16-bit signed little-endian en float32 normalisé [-1, 1]."""
    if not raw:
        return None
    arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return arr


def _wav_bytes_24k(audio_array) -> bytes:
    """Sérialise un np.ndarray (NeuTTS infer) en WAV 24 kHz mono PCM 16-bit."""
    if isinstance(audio_array, torch.Tensor):
        audio_array = audio_array.detach().cpu().numpy()
    arr = np.asarray(audio_array)
    if arr.ndim > 1:
        arr = arr.squeeze()
    buf = io.BytesIO()
    sf.write(buf, arr, TTS_SAMPLE_RATE, format="WAV", subtype="PCM_16")
    return buf.getvalue()


@router.websocket("/ws/stream")
async def stream(ws: WebSocket):
    if not _ws_authenticated(ws):
        await ws.close(code=4401)
        return
    await ws.accept()

    if not ML_AVAILABLE:
        await _send_json(ws, {
            "type": "error",
            "error": "ml_unavailable",
            "message": "Modèles ML non installés (mode minimal). Relancez install.sh sans --minimal.",
        })
        await ws.close(code=4503)
        return

    # Imports paresseux (lourds : torch, transformers, neutts)
    try:
        from ..models import stt as stt_model
        from ..models import tts as tts_model
        from ..models import vad as vad_model
    except ImportError as exc:
        await _send_json(ws, {"type": "error", "error": "import_failed", "message": str(exc)})
        await ws.close(code=4503)
        return

    # ── État de session ─────────────────────────────────────────────
    voice_id: str | None = None
    language: str = "fr"
    quality: str = "normal"  # Live → toujours Q4 (latence)
    ref_codes = None
    ref_text = ""
    vad_iter = None
    speech_buf: deque = deque(maxlen=BUFFER_TICKS)
    speech_count = 0
    silence_count = 0
    in_speech = False
    # Petit accumulateur pour empiler ce que l'on reçoit, et découper en
    # blocs de 512 samples (ce que veut Silero VAD).
    pcm_carry = np.zeros(0, dtype=np.float32)

    async def configure(payload: dict) -> bool:
        nonlocal voice_id, language, ref_codes, ref_text, vad_iter
        try:
            voice_id = files.safe_id(str(payload.get("voice_id", "")))
        except ValueError:
            await _send_json(ws, {"type": "error", "message": "voice_id invalide"})
            return False
        language = payload.get("language", "fr")
        if language not in ("fr", "en"):
            await _send_json(ws, {"type": "error", "message": "language fr ou en"})
            return False
        voice = voices_store.get(voice_id)
        if not voice:
            await _send_json(ws, {"type": "error", "message": "voix introuvable"})
            return False
        encoded = voices_store.encoded_path(voice_id)
        if not encoded.exists():
            await _send_json(ws, {"type": "error", "message": "ref_codes manquants pour la voix"})
            return False
        try:
            ref_codes = torch.load(encoded, weights_only=False)
        except Exception as exc:  # noqa: BLE001
            await _send_json(ws, {"type": "error", "message": f"chargement ref_codes : {exc}"})
            return False
        ref_text = voices_store.read_ref_text(voice_id)
        try:
            vad_iter = vad_model.make_iterator(threshold=0.5)
        except Exception as exc:  # noqa: BLE001
            await _send_json(ws, {"type": "error", "message": f"VAD indisponible : {exc}"})
            return False
        await _send_json(ws, {"type": "ready"})
        return True

    async def flush_speech() -> None:
        """Concat le buffer parlé, STT → TTS, renvoi du WAV au client."""
        nonlocal speech_buf, speech_count, silence_count, in_speech
        if not speech_buf:
            return
        audio = np.concatenate(list(speech_buf), axis=0)
        speech_buf.clear()
        speech_count = 0
        silence_count = 0
        in_speech = False

        # Resample 16k → 24k (interp linéaire — qualité voix OK)
        ratio = TTS_SAMPLE_RATE / VAD_SAMPLE_RATE
        new_len = int(len(audio) * ratio)
        x_old = np.linspace(0, 1, len(audio), endpoint=False)
        x_new = np.linspace(0, 1, new_len, endpoint=False)
        audio_24k = np.interp(x_new, x_old, audio).astype(np.float32)

        try:
            text = stt_model.transcribe(audio_24k, TTS_SAMPLE_RATE)
        except Exception as exc:  # noqa: BLE001
            log.exception("STT live failed")
            await _send_json(ws, {"type": "error", "message": f"STT : {exc}"})
            return
        if not text:
            return

        try:
            wav = tts_model.infer(text, ref_codes, ref_text, language, quality)
        except Exception as exc:  # noqa: BLE001
            log.exception("TTS live failed")
            await _send_json(ws, {"type": "error", "message": f"TTS : {exc}"})
            return

        wav_bytes = _wav_bytes_24k(wav)
        b64 = base64.b64encode(wav_bytes).decode("ascii")
        await _send_json(ws, {"type": "transcript", "text": text})
        await _send_json(ws, {"type": "audio_chunk", "data": b64, "sample_rate": TTS_SAMPLE_RATE})

    async def consume_pcm(pcm_chunk):
        """Empile ``pcm_chunk`` dans le carry, découpe en blocs de 512 samples
        et fait passer chacun par Silero VAD + buffer + flush conditionnel.
        """
        nonlocal pcm_carry, silence_count, speech_count, in_speech
        # Concat
        pcm_carry = np.concatenate([pcm_carry, pcm_chunk])
        # Découpe en blocs de 512 samples (32 ms)
        n_full = len(pcm_carry) // VAD_CHUNK_SAMPLES
        if n_full == 0:
            return
        usable_len = n_full * VAD_CHUNK_SAMPLES
        full = pcm_carry[:usable_len]
        pcm_carry = pcm_carry[usable_len:]

        for i in range(0, usable_len, VAD_CHUNK_SAMPLES):
            sub = full[i:i + VAD_CHUNK_SAMPLES]
            try:
                ev = vad_iter(torch.from_numpy(sub), return_seconds=False)
            except Exception:  # noqa: BLE001
                ev = None
            if ev is not None and "start" in ev:
                in_speech = True
            if in_speech:
                speech_buf.append(sub)
                speech_count += 1
                silence_count = 0
            elif speech_count > 0:
                silence_count += 1
            if ev is not None and "end" in ev:
                in_speech = False

            if (silence_count >= SILENCE_FLUSH_TICKS and speech_count > 0) \
                    or speech_count >= SPEECH_FLUSH_TICKS:
                await flush_speech()

    # ── Boucle principale ──────────────────────────────────────────
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("text"):
                # Message JSON de contrôle
                try:
                    payload = json.loads(msg["text"])
                except json.JSONDecodeError:
                    continue
                ptype = payload.get("type")
                if ptype == "configure":
                    await configure(payload)
                elif ptype == "stop":
                    await flush_speech()
                    await _send_json(ws, {"type": "stopped"})
                    break
            elif msg.get("bytes"):
                # Chunk PCM 16-bit mono 16 kHz envoyé par l'AudioWorklet
                if vad_iter is None:
                    await _send_json(ws, {"type": "error", "message": "envoyez d'abord un message configure"})
                    continue
                pcm = _pcm_int16_bytes_to_float32(msg["bytes"])
                if pcm is None or len(pcm) == 0:
                    continue
                await consume_pcm(pcm)
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        log.exception("WebSocket /ws/stream a planté")
        await _send_json(ws, {"type": "error", "message": str(exc)})
    finally:
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
