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

Optimisations latence Live (cible spec 0,6-1,4 s) :

1. AudioWorklet PCM 16 kHz raw côté client (au lieu de MediaRecorder webm 1 s)
   → suppression du timeslice 1 s + plus de subprocess ffmpeg côté serveur

2. NeuTTS ``infer_stream`` côté serveur (au lieu de ``infer`` synchrone)
   → premier chunk audio envoyé au client ~50-150 ms après la fin du STT,
     au lieu d'attendre la fin de toute la synthèse (~1× temps réel parlé)

3. Côté client : chaque chunk décodé en AudioBuffer et scheduled sur
   un AudioContext (pas de re-parsing WAV par chunk)

Décomposition typique pour une phrase de 2 s :
- silence flush  : ~400 ms (incompressible)
- transport WS   : ~50-100 ms
- Kyutai STT     : ~200-300 ms
- NeuTTS premier chunk Q4 : ~50-150 ms
- transport + scheduling client : ~50 ms

→ premier mot audible côté interlocuteur après ~750-1000 ms,
   dans la cible spec V1.

Auth : cookie session OU Bearer token au handshake.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import threading
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
    language: str = "fr"         # langue source (micro + STT)
    quality: str = "normal"      # Live → toujours Q4 (latence)
    translate: bool = False      # traduction Live activée
    translate_to: str = "en"    # langue cible de la traduction + TTS
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
        nonlocal voice_id, language, ref_codes, ref_text, vad_iter, translate, translate_to
        try:
            voice_id = files.safe_id(str(payload.get("voice_id", "")))
        except ValueError:
            await _send_json(ws, {"type": "error", "message": "voice_id invalide"})
            return False
        language = payload.get("language", "fr")
        if language not in ("fr", "en"):
            await _send_json(ws, {"type": "error", "message": "language fr ou en"})
            return False
        # Traduction Live (optionnel)
        translate = bool(payload.get("translate", False))
        translate_to = str(payload.get("translate_to", "en"))
        if translate_to not in ("fr", "en"):
            await _send_json(ws, {"type": "error", "message": "translate_to fr ou en"})
            return False
        if translate and translate_to == language:
            # Incohérent : même langue source et cible → désactiver silencieusement
            translate = False
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
        log.info("live: configured voice_id=%s lang=%s ref_text=%s translate=%s→%s",
                 voice_id, language, "yes" if ref_text else "NO (will fallback)",
                 "on" if translate else "off", translate_to)
        await _send_json(ws, {"type": "ready"})
        return True

    async def stream_tts_chunks(text: str, tts_lang: str | None = None) -> None:
        """Lance ``tts.infer_stream`` dans un thread (bloquant) et pousse les
        chunks dans une asyncio.Queue. Coroutine consume + ws.send_json.

        ``tts_lang`` : langue à passer à NeuTTS (peut différer de ``language``
        quand la traduction est activée — on synthétise dans la langue cible).
        """
        lang = tts_lang or language
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=64)

        def producer():
            try:
                for chunk_f32 in tts_model.infer_stream(
                    text, ref_codes, ref_text, lang, quality,
                ):
                    arr = np.asarray(chunk_f32)
                    if arr.ndim > 1:
                        arr = arr.squeeze()
                    pcm = (arr * 32767.0).astype(np.int16).tobytes()
                    asyncio.run_coroutine_threadsafe(queue.put(pcm), loop)
            except Exception as exc:  # noqa: BLE001
                asyncio.run_coroutine_threadsafe(queue.put(exc), loop)
            asyncio.run_coroutine_threadsafe(queue.put(None), loop)

        threading.Thread(target=producer, daemon=True).start()

        seq = 0
        while True:
            item = await queue.get()
            if item is None:
                break
            if isinstance(item, Exception):
                log.exception("TTS streaming failed: %s", item)
                await _send_json(ws, {"type": "error", "message": f"TTS : {item}"})
                return
            await _send_json(ws, {
                "type": "audio_pcm",
                "data": base64.b64encode(item).decode("ascii"),
                "sample_rate": TTS_SAMPLE_RATE,
                "seq": seq,
            })
            seq += 1
        await _send_json(ws, {"type": "audio_end", "seq": seq})

    async def flush_speech() -> None:
        """Concat le buffer parlé, STT → TTS streaming, renvoi des chunks PCM."""
        nonlocal speech_buf, speech_count, silence_count, in_speech
        if not speech_buf:
            return
        audio = np.concatenate(list(speech_buf), axis=0)
        speech_buf.clear()
        speech_count = 0
        silence_count = 0
        in_speech = False

        duration_s = len(audio) / VAD_SAMPLE_RATE
        log.info("live: flush_speech %.2fs of audio (16k → 24k → STT)", duration_s)

        # Resample 16k → 24k (interp linéaire — qualité voix OK)
        ratio = TTS_SAMPLE_RATE / VAD_SAMPLE_RATE
        new_len = int(len(audio) * ratio)
        x_old = np.linspace(0, 1, len(audio), endpoint=False)
        x_new = np.linspace(0, 1, new_len, endpoint=False)
        audio_24k = np.interp(x_new, x_old, audio).astype(np.float32)

        import time as _t
        t0 = _t.time()
        try:
            text = stt_model.transcribe(audio_24k, TTS_SAMPLE_RATE)
        except Exception as exc:  # noqa: BLE001
            log.exception("STT live failed")
            await _send_json(ws, {"type": "error", "message": f"STT : {exc}"})
            return
        log.info("live: STT done in %.2fs → text=%r", _t.time() - t0, (text or "")[:80])
        if not text:
            return

        await _send_json(ws, {"type": "transcript", "text": text})

        # ── Traduction optionnelle ──────────────────────────────────
        tts_text = text
        tts_lang = language
        if translate:
            try:
                from ..services import translation as trans_svc
                t_tr = _t.time()
                translated = await asyncio.to_thread(
                    trans_svc.translate, text, language, translate_to
                )
                log.info("live: translation done in %.2fs → %r",
                         _t.time() - t_tr, translated[:80])
                await _send_json(ws, {"type": "translated", "text": translated,
                                      "src_lang": language, "tgt_lang": translate_to})
                tts_text = translated
                tts_lang = translate_to
            except Exception as exc:  # noqa: BLE001
                log.warning("live: translation failed (%s) — falling back to original", exc)
                await _send_json(ws, {"type": "translation_error",
                                      "message": f"Traduction échouée : {exc}"})
                # On continue avec le texte original dans la langue source.

        t1 = _t.time()
        await stream_tts_chunks(tts_text, tts_lang=tts_lang)
        log.info("live: TTS streaming done in %.2fs", _t.time() - t1)

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
