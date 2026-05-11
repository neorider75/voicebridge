"""WebSocket ``/ws/stream`` — Live (V1 CPU + V3 GPU multi-modes).

Quatre modes Live coexistent (sélectionnés via ``configure.mode``) :

| Mode          | Pipeline                                          | Latence  |
|---------------|---------------------------------------------------|----------|
| ``cpu-fr-en`` | V1 inchangé : Kyutai → NeuTTS Q4 (CPU local)     | 5-15 s   |
| ``gpu-clone`` | Whisper → trad → F5-TTS clone (RunPod GPU)        | ~1.2 s   |
| ``gpu-native``| Whisper → trad → F5-TTS native (RunPod GPU)       | ~1.2 s   |
| ``gpu-hybrid``| Whisper → trad → F5-TTS native → RVC (RunPod GPU) | ~2 s     |

**Mode V1 cpu-fr-en (par défaut, intact) :**

    client → WS                    : chunks PCM 16 kHz mono int16 (~100 ms)
    ↓ Silero VAD (chunks 32 ms)
    ↓ flush condition             : silence > 400 ms OU speech > 4 s
    ↓ resample 16k→24k
    ↓ Kyutai STT                  → texte
    ↓ [traduction OPUS-MT optionnelle]
    ↓ NeuTTS Q4 (ref_codes)       → WAV 24 kHz mono + watermark Perth
    ↓ WS                          → audio_pcm JSON au client

**Modes V3 gpu-* :**

    client → WS                    : chunks PCM 16 kHz mono int16
    ↓ Silero VAD côté Hostinger
    ↓ flush condition
    ↓ encode WAV 16 kHz b64
    ↓ RunPod /run live_pipeline   : payload {mode, audio, voice_ref,
                                              translation_provider, ...}
    ↓ RunPod /stream/{job_id}     : Whisper → trad → F5-TTS [→ RVC]
    ↓ chaque chunk PCM 24 kHz du worker → forward au client

**Briefings GPT (Décision 7) :**
Si le provider est ``gpt-4o-mini`` ou ``gpt-4o``, la traduction est faite
côté Hostinger via ``openai_client.TranslationSession`` (qui maintient une
mémoire conversationnelle des N dernières phrases) AVANT d'envoyer le job
au worker (qui reçoit ``pre_translated`` au lieu de traduire lui-même).

**Coût en temps réel :** un message ``cost_update`` est émis périodiquement
(toutes les flush en mode GPU) avec le coût cumulé de la session (RTX 4090
~0.34€/h + tokens GPT cumulés).

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
SILENCE_FLUSH_TICKS_CPU = 13    # ~400 ms (V1, marge contre faux flush sur respirations)
SILENCE_FLUSH_TICKS_GPU = 16    # ~500 ms (V3 — initialement 8 (250 ms) pour gain
                                # latence Décision Q2.1, mais coupait au milieu
                                # des phrases sur les pauses inter-mots normales
                                # (200-300 ms typiques en français). Aligné sur
                                # CPU + marge pour fiabilité.
SPEECH_FLUSH_TICKS = 250        # ~8 s de parole continue → flush forcé
                                # (initialement 125 ~4 s, coupait les phrases
                                # longues naturelles : "Donc en fait l'idée
                                # serait de pouvoir présenter le projet aux
                                # équipes lundi prochain" fait ~6 s).
VAD_CHUNK_SAMPLES = 512         # taille de bloc attendue par Silero VAD

# Modes Live V3 (cf. doc 00-decisions-v3.md)
MODE_CPU_V1 = "cpu-fr-en"
MODE_GPU_CLONE = "gpu-clone"
MODE_GPU_NATIVE = "gpu-native"
MODE_GPU_HYBRID = "gpu-hybrid"
VALID_MODES = (MODE_CPU_V1, MODE_GPU_CLONE, MODE_GPU_NATIVE, MODE_GPU_HYBRID)

# Coût RunPod RTX 4090 (mai 2026, vérifier régulièrement)
RUNPOD_RTX4090_EUR_PER_SEC = 0.34 / 3600.0    # 0.34€/h


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


def _wav_b64_from_pcm_array(pcm_array, sample_rate: int = VAD_SAMPLE_RATE) -> str:
    """Encode un np.ndarray float32 en WAV PCM 16-bit base64 (pour envoi RunPod)."""
    arr = np.asarray(pcm_array)
    if arr.ndim > 1:
        arr = arr.squeeze()
    buf = io.BytesIO()
    sf.write(buf, arr, sample_rate, format="WAV", subtype="PCM_16")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _read_voice_wav_b64(voice_id: str) -> str | None:
    """Lit le WAV de référence d'une voix et le retourne encodé base64.

    Utilisé pour les modes GPU (clone/native/hybrid) où le worker reçoit
    le sample comme ``voice_ref`` dans chaque appel ``live_pipeline``.
    """
    wav_path = voices_store.wav_path(voice_id)
    if not wav_path.exists():
        return None
    return base64.b64encode(wav_path.read_bytes()).decode("ascii")


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
    translate_to: str = "en"     # langue cible de la traduction + TTS
    ref_codes = None             # CPU V1 only — chargé depuis voices/encoded
    ref_text = ""                # CPU V1 only
    vad_iter = None
    speech_buf: deque = deque(maxlen=BUFFER_TICKS)
    speech_count = 0
    silence_count = 0
    in_speech = False
    # Petit accumulateur pour empiler ce que l'on reçoit, et découper en
    # blocs de 512 samples (ce que veut Silero VAD).
    pcm_carry = np.zeros(0, dtype=np.float32)

    # ── État V3 (modes GPU) ─────────────────────────────────────────
    mode: str = MODE_CPU_V1
    translation_provider: str = "opus-mt-cpu"
    rvc_model_id: str | None = None
    voice_ref_b64: str | None = None   # WAV b64 de la voix sélectionnée (encodé une fois)
    briefing: str = ""                 # contexte de session GPT (Décision 7)
    gpt_session = None                 # openai_client.TranslationSession (mémoire conv)
    session_start_ts: float = 0.0
    session_cost_eur: float = 0.0      # cumul des phrases GPT (RunPod géré séparément)
    last_cost_emit_ts: float = 0.0
    # Compteur pour l'historique des sessions : nombre d'utterances (flush
    # déclenchés par la VAD) sur la durée de vie de la WS.
    n_utterances: int = 0
    voice_name: str = ""  # mémorisé au configure pour l'historique

    async def configure(payload: dict) -> bool:
        nonlocal voice_id, language, ref_codes, ref_text, vad_iter
        nonlocal translate, translate_to
        nonlocal mode, translation_provider, rvc_model_id, voice_ref_b64
        nonlocal briefing, gpt_session, session_start_ts, last_cost_emit_ts
        nonlocal voice_name

        # ── 1. Mode (V1 par défaut, rétrocompat totale) ──────────────
        mode = str(payload.get("mode", MODE_CPU_V1))
        if mode not in VALID_MODES:
            await _send_json(ws, {"type": "error",
                                  "message": f"mode invalide : {mode!r}. "
                                             f"Valides : {VALID_MODES}"})
            return False

        # ── 2. Voix ──────────────────────────────────────────────────
        try:
            voice_id = files.safe_id(str(payload.get("voice_id", "")))
        except ValueError:
            await _send_json(ws, {"type": "error", "message": "voice_id invalide"})
            return False
        voice = voices_store.get(voice_id)
        if not voice:
            await _send_json(ws, {"type": "error", "message": "voix introuvable"})
            return False
        voice_name = voice.get("name", voice_id) or voice_id

        # ── 3. Langue source (micro + STT) ───────────────────────────
        language = payload.get("language", "fr")
        # Mode V1 : seulement fr/en. Modes GPU : Whisper accepte 90+ langues.
        if mode == MODE_CPU_V1 and language not in ("fr", "en"):
            await _send_json(ws, {"type": "error",
                                  "message": "Mode cpu-fr-en : language doit être fr ou en"})
            return False

        # ── 4. Traduction (V1 = bool ; V3 = via translation_provider) ─
        # Compat V1 : champs translate + translate_to (langue cible)
        translate = bool(payload.get("translate", False))
        translate_to = str(payload.get("translate_to",
                                       payload.get("target_lang", "en")))

        # V3 : translation_provider + target_lang (target_lang = translate_to renommé)
        translation_provider = str(payload.get("translation_provider", "opus-mt-cpu"))
        if translation_provider not in (
            "opus-mt-cpu", "opus-mt-gpu", "nllb", "gpt-4o-mini", "gpt-4o"
        ):
            await _send_json(ws, {"type": "error",
                                  "message": f"translation_provider invalide : "
                                             f"{translation_provider!r}"})
            return False

        if translate and translate_to == language:
            translate = False  # même langue source/cible → no-op silencieux

        # ── 5. Validations spécifiques aux modes GPU ─────────────────
        if mode != MODE_CPU_V1:
            from ..services import runpod_client
            if not runpod_client.is_configured():
                await _send_json(ws, {"type": "error",
                                      "message": "RunPod non configuré — "
                                                 "rendez-vous dans Réglages → Cloud"})
                return False
            # Sample voix → encodé une fois en b64 (réutilisé à chaque flush)
            voice_ref_b64 = _read_voice_wav_b64(voice_id)
            if not voice_ref_b64:
                await _send_json(ws, {"type": "error",
                                      "message": "WAV de référence introuvable pour cette voix"})
                return False

        if mode == MODE_GPU_HYBRID:
            rvc_model_id = payload.get("rvc_model_id")
            if not rvc_model_id:
                await _send_json(ws, {"type": "error",
                                      "message": "rvc_model_id requis pour le mode hybride"})
                return False
            try:
                rvc_model_id = files.safe_id(str(rvc_model_id))
            except ValueError:
                await _send_json(ws, {"type": "error",
                                      "message": "rvc_model_id invalide"})
                return False

        # ── 6. Briefing + provider GPT (Décision 7) ──────────────────
        briefing = str(payload.get("briefing", "")).strip()
        if translation_provider in ("gpt-4o-mini", "gpt-4o"):
            from ..services import openai_client
            if not openai_client.is_configured():
                await _send_json(ws, {"type": "error",
                                      "message": "OpenAI non configuré — "
                                                 "rendez-vous dans Réglages → Cloud"})
                return False
            try:
                gpt_session = openai_client.TranslationSession(
                    provider=translation_provider,
                    briefing=briefing,
                )
            except Exception as exc:  # noqa: BLE001
                await _send_json(ws, {"type": "error",
                                      "message": f"GPT init failed: {exc}"})
                return False

        # ── 7. Préparatifs spécifiques au mode CPU V1 ────────────────
        if mode == MODE_CPU_V1:
            encoded = voices_store.encoded_path(voice_id)
            if not encoded.exists():
                await _send_json(ws, {"type": "error",
                                      "message": "ref_codes manquants pour la voix"})
                return False
            try:
                ref_codes = torch.load(encoded, weights_only=False)
            except Exception as exc:  # noqa: BLE001
                await _send_json(ws, {"type": "error",
                                      "message": f"chargement ref_codes : {exc}"})
                return False
            ref_text = voices_store.read_ref_text(voice_id)

        # ── 8. VAD (commun à tous les modes) ─────────────────────────
        try:
            vad_iter = vad_model.make_iterator(threshold=0.5)
        except Exception as exc:  # noqa: BLE001
            await _send_json(ws, {"type": "error",
                                  "message": f"VAD indisponible : {exc}"})
            return False

        # ── 9. Init compteurs de coût ─────────────────────────────────
        import time as _t
        session_start_ts = _t.time()
        last_cost_emit_ts = session_start_ts

        log.info("live: configured mode=%s voice_id=%s lang=%s provider=%s "
                 "translate=%s→%s rvc=%s briefing=%s",
                 mode, voice_id, language, translation_provider,
                 "on" if translate else "off", translate_to,
                 rvc_model_id or "-", "yes" if briefing else "no")
        await _send_json(ws, {"type": "ready", "mode": mode})
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

    async def emit_cost_update(force: bool = False) -> None:
        """Émet un message ``cost_update`` au client.

        Calcul : RTX 4090 GPU time × tarif + cumul tokens GPT (gpt_session.total_cost_eur).
        En mode V1 (cpu-fr-en), n'émet rien (zéro coût).

        Throttle : pas plus d'1 message toutes les 2 s sauf si ``force=True``.
        """
        nonlocal last_cost_emit_ts
        if mode == MODE_CPU_V1:
            return
        import time as _t
        now = _t.time()
        if not force and (now - last_cost_emit_ts) < 2.0:
            return
        last_cost_emit_ts = now

        elapsed = now - session_start_ts
        runpod_eur = elapsed * RUNPOD_RTX4090_EUR_PER_SEC
        gpt_eur = gpt_session.total_cost_eur if gpt_session is not None else 0.0
        total = runpod_eur + gpt_eur

        await _send_json(ws, {
            "type": "cost_update",
            "session_cost_eur": round(total, 5),
            "duration_seconds": int(elapsed),
            "provider_breakdown": {
                "runpod_gpu": round(runpod_eur, 5),
                "openai": round(gpt_eur, 5),
            },
        })

    async def flush_speech() -> None:
        """Concat le buffer parlé puis route vers V1 (CPU) ou V3 (GPU)."""
        nonlocal speech_buf, speech_count, silence_count, in_speech, n_utterances
        if not speech_buf:
            return
        audio = np.concatenate(list(speech_buf), axis=0)
        speech_buf.clear()
        speech_count = 0
        silence_count = 0
        in_speech = False
        n_utterances += 1

        duration_s = len(audio) / VAD_SAMPLE_RATE
        log.info("live: flush_speech %.2fs mode=%s", duration_s, mode)

        if mode == MODE_CPU_V1:
            await flush_speech_cpu_v1(audio)
        else:
            await flush_speech_gpu(audio)

        await emit_cost_update()

    async def flush_speech_cpu_v1(audio) -> None:
        """Pipeline V1 inchangé : Kyutai STT → trad OPUS-MT → NeuTTS Q4 (CPU)."""
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

        # ── Traduction optionnelle (V1 — OPUS-MT CPU) ────────────────
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
                log.warning("live: translation failed (%s) — fallback original", exc)
                await _send_json(ws, {"type": "translation_error",
                                      "message": f"Traduction échouée : {exc}"})

        t1 = _t.time()
        await stream_tts_chunks(tts_text, tts_lang=tts_lang)
        log.info("live: TTS streaming done in %.2fs", _t.time() - t1)

    async def flush_speech_gpu(audio) -> None:
        """Pipeline V3 GPU : encode WAV → RunPod live_pipeline → forward chunks.

        - Pré-traduction côté Hostinger si provider GPT (préserve la mémoire conv).
        - Sinon : le worker traduit (NLLB ou OPUS-MT GPU).
        """
        from ..services import runpod_client

        # Encode audio source en WAV b64 16 kHz pour Whisper
        audio_b64 = _wav_b64_from_pcm_array(audio, sample_rate=VAD_SAMPLE_RATE)

        target_lang = translate_to if translate else language

        payload = {
            "operation": "live_pipeline",
            "mode": mode,
            "audio": audio_b64,
            "src_lang": language,
            "target_lang": target_lang,
            "voice_ref": voice_ref_b64,
            "translation_provider": (
                "opus-mt" if translation_provider == "opus-mt-gpu" else translation_provider
            ),
        }
        if mode == MODE_GPU_HYBRID:
            payload["rvc_model_id"] = rvc_model_id

        # ── Pré-traduction GPT (Décision 7) ──────────────────────────
        # On traduit ici pour bénéficier de la mémoire conversationnelle de
        # gpt_session, puis on transmet ``pre_translated`` au worker (qui
        # skipera l'étape de traduction côté GPU).
        if (translate and target_lang != language and gpt_session is not None
                and translation_provider in ("gpt-4o-mini", "gpt-4o")):
            # Le worker fait STT en premier ; on n'a pas encore le texte.
            # Stratégie : on laisse Whisper transcrire côté worker, on récupère
            # le transcript via le stream, on traduit ici, et... non, ça
            # impliquerait deux jobs RunPod. Plus simple : transcrire en local
            # AVANT (Kyutai V1, déjà chargé) et passer pre_translated.
            try:
                ratio = TTS_SAMPLE_RATE / VAD_SAMPLE_RATE
                new_len = int(len(audio) * ratio)
                x_old = np.linspace(0, 1, len(audio), endpoint=False)
                x_new = np.linspace(0, 1, new_len, endpoint=False)
                audio_24k = np.interp(x_new, x_old, audio).astype(np.float32)
                text_local = await asyncio.to_thread(
                    stt_model.transcribe, audio_24k, TTS_SAMPLE_RATE
                )
                if text_local:
                    await _send_json(ws, {"type": "transcript", "text": text_local})
                    res = await asyncio.to_thread(
                        gpt_session.translate, text_local, language, target_lang
                    )
                    payload["pre_translated"] = res.translated
                    await _send_json(ws, {"type": "translated",
                                          "text": res.translated,
                                          "src_lang": language,
                                          "tgt_lang": target_lang})
            except Exception as exc:  # noqa: BLE001
                log.warning("GPT pre-translation failed (%s) — let worker handle", exc)
                # On laisse le worker traduire (il a un fallback nllb).

        # ── Appel RunPod en async + stream ───────────────────────────
        try:
            job_id = await asyncio.to_thread(runpod_client.run_async, payload)
        except runpod_client.RunPodError as exc:
            log.exception("RunPod run_async failed")
            await _send_json(ws, {"type": "error", "message": f"RunPod : {exc}"})
            return

        # Polling /stream dans un thread → forward chaque chunk au client
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=128)

        def producer():
            try:
                for item in runpod_client.stream(job_id):
                    asyncio.run_coroutine_threadsafe(queue.put(item), loop)
            except Exception as exc:  # noqa: BLE001
                asyncio.run_coroutine_threadsafe(queue.put(exc), loop)
            asyncio.run_coroutine_threadsafe(queue.put(None), loop)

        threading.Thread(target=producer, daemon=True).start()

        seq = 0
        sent_audio_end = False
        types_received: dict[str, int] = {}
        first_chunk_bytes = 0
        while True:
            item = await queue.get()
            if item is None:
                break
            if isinstance(item, Exception):
                log.exception("RunPod stream failed: %s", item)
                await _send_json(ws, {"type": "error", "message": f"RunPod : {item}"})
                return

            if not isinstance(item, dict):
                log.warning("live.gpu stream: non-dict item type=%s value=%r",
                            type(item).__name__, str(item)[:120])
                continue
            t = item.get("type")
            types_received[t or "unknown"] = types_received.get(t or "unknown", 0) + 1
            if t == "transcript":
                # Si on a pré-traduit en local, on a déjà émis le transcript.
                if "pre_translated" not in payload:
                    await _send_json(ws, {"type": "transcript", "text": item.get("text", "")})
            elif t == "translated":
                if "pre_translated" not in payload:
                    await _send_json(ws, item)
            elif t == "audio_pcm":
                # Forward tel quel (data b64 + sample_rate déjà inclus)
                data = item.get("data", "")
                if seq == 0:
                    first_chunk_bytes = len(data)
                    log.info("live.gpu forwarding 1st audio_pcm chunk: "
                             "b64_len=%d sr=%s", first_chunk_bytes,
                             item.get("sample_rate"))
                pkt = {"type": "audio_pcm",
                       "data": data,
                       "sample_rate": item.get("sample_rate", TTS_SAMPLE_RATE),
                       "seq": seq}
                await _send_json(ws, pkt)
                seq += 1
            elif t == "audio_end":
                await _send_json(ws, {"type": "audio_end", "seq": seq})
                sent_audio_end = True
            elif t == "error":
                await _send_json(ws, {"type": "error",
                                      "message": item.get("message", "worker error")})
                return

        log.info("live.gpu stream summary: types=%s total_audio_chunks=%d",
                 types_received, seq)
        if seq == 0:
            log.warning("live.gpu: NO audio_pcm chunks forwarded — "
                        "worker yielded %s", types_received)

        if not sent_audio_end:
            await _send_json(ws, {"type": "audio_end", "seq": seq})

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

            silence_threshold = (SILENCE_FLUSH_TICKS_GPU
                                 if mode != MODE_CPU_V1
                                 else SILENCE_FLUSH_TICKS_CPU)
            if (silence_count >= silence_threshold and speech_count > 0) \
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
                    await emit_cost_update(force=True)
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
        # ── Enregistrement dans l'historique des sessions ──
        # Best-effort : on n'empêche pas la fermeture WS si ça plante.
        try:
            if voice_id and session_start_ts > 0:
                import time as _t
                from ..services import sessions_store
                ended_ts = _t.time()
                elapsed = ended_ts - session_start_ts
                runpod_eur = (elapsed * RUNPOD_RTX4090_EUR_PER_SEC
                               if mode != MODE_CPU_V1 else 0.0)
                gpt_eur = (gpt_session.total_cost_eur
                            if gpt_session is not None else 0.0)
                sessions_store.record(
                    mode=mode,
                    voice_id=voice_id,
                    voice_name=voice_name or voice_id,
                    started_at_ts=session_start_ts,
                    ended_at_ts=ended_ts,
                    translate=translate,
                    source_lang=language,
                    target_lang=translate_to if translate else language,
                    translation_provider=translation_provider,
                    rvc_model_id=rvc_model_id,
                    cost_total_eur=runpod_eur + gpt_eur,
                    cost_runpod_eur=runpod_eur,
                    cost_openai_eur=gpt_eur,
                    n_utterances=n_utterances,
                )
        except Exception:  # noqa: BLE001
            log.exception("session_store.record failed (non-fatal)")
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
