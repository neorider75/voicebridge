"""VoiceBridge Worker — handler unifié RunPod Serverless.

Endpoints (sélectionnés via ``operation`` dans le payload) :

- ``operation="warmup"``        : pré-charge les modèles en VRAM
- ``operation="translate"``     : traduction texte simple (OPUS-MT GPU ou NLLB)
- ``operation="live_pipeline"`` : cascade STT + Trad + TTS [+ RVC] avec streaming
- ``operation="rvc_convert"``   : conversion RVC d'un audio (mode fichier)

Démarré par RunPod via ``runpod.serverless.start({...})``.

Imports paresseux : les modèles ne sont chargés qu'au premier appel pour
minimiser le cold start (FlashBoot).

Modes Live (cf. Décision 2 du document 00-decisions-v3.md) :
- ``gpu-clone``  : voix CLONÉE de l'utilisateur en input (voice_ref WAV b64)
- ``gpu-native`` : voix NATIVE de la bibliothèque /voices côté Hostinger
                   (voice_ref WAV b64 — c'est le sample de la voix native
                   sélectionnée dans la lib unifiée)
- ``gpu-hybrid`` : voix native (voice_ref) → F5-TTS → RVC (rvc_model_id)
                   → ta voix avec accent natif
"""
from __future__ import annotations

import logging
import os
from typing import Generator

import runpod

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
logging.basicConfig(
    level=LOG_LEVEL,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("voicebridge.worker")


# ============================================================================
# Lazy initialization des modèles (1ère utilisation = chargement)
# ============================================================================

_whisper = None
_f5tts = None
_nllb = None
_opus_mt = None
_rvc_router = None


def get_whisper():
    global _whisper
    if _whisper is None:
        log.info("Loading Whisper Distil-Large-V3 ...")
        from models.whisper import WhisperSTT
        _whisper = WhisperSTT()
    return _whisper


def get_f5tts():
    global _f5tts
    if _f5tts is None:
        log.info("Loading F5-TTS ...")
        from models.f5tts import F5TTS
        _f5tts = F5TTS()
    return _f5tts


def get_nllb():
    global _nllb
    if _nllb is None:
        log.info("Loading NLLB-200 distilled 1.3B ...")
        from models.nllb import NLLB
        _nllb = NLLB()
    return _nllb


def get_opus_mt():
    global _opus_mt
    if _opus_mt is None:
        log.info("Loading OPUS-MT models ...")
        from models.opus_mt import OpusMT
        _opus_mt = OpusMT()
    return _opus_mt


def get_rvc_router():
    global _rvc_router
    if _rvc_router is None:
        log.info("Initializing RVC router (LRU cache for .pth files)")
        from models.rvc import RVCRouter
        _rvc_router = RVCRouter()
    return _rvc_router


# ============================================================================
# Handler principal
# ============================================================================

def handler(job):
    """Entry point appelé par RunPod pour chaque job.

    Cette fonction est **toujours un générateur** (contient yield from /
    yield). C'est nécessaire pour que RunPod SDK la traite comme handler
    streaming via inspect.isgeneratorfunction() au moment de l'enregistrement
    runpod.serverless.start().

    Comportement :
      - live_pipeline : yield les chunks au fur et à mesure (vrai streaming)
      - autres ops    : yield UN seul dict (RunPod l'agrège en array de 1
        item, le client Hostinger déballe via runpod_client.runsync)

    Config associée à start() : return_aggregate_stream=True → /runsync
    matérialise les yields en array dans la réponse.
    """
    inp = job.get("input", {})
    op = inp.get("operation", "live_pipeline")

    log.info("handler op=%s", op)

    # Streaming : yield from le générateur de live_pipeline
    if op == "live_pipeline":
        yield from handle_live_pipeline(inp)
        return

    # Opérations synchrones : yield le résultat une seule fois
    try:
        if op == "warmup":
            yield handle_warmup(inp)
        elif op == "translate":
            yield handle_translate(inp)
        elif op == "rvc_convert":
            yield handle_rvc_convert(inp)
        else:
            yield {"error": "unknown_operation", "received": op}
    except Exception as e:  # noqa: BLE001
        log.exception("handler error op=%s", op)
        yield {"error": "handler_failed", "message": str(e)}


# ============================================================================
# Operation: warmup
# ============================================================================

def handle_warmup(inp: dict) -> dict:
    """Pré-charge les modèles spécifiés en VRAM."""
    components = inp.get("components", ["whisper", "f5tts", "nllb"])
    loaded = []

    for c in components:
        if c == "whisper":
            get_whisper()
            loaded.append("whisper")
        elif c == "f5tts":
            get_f5tts()
            loaded.append("f5tts")
        elif c == "nllb":
            get_nllb()
            loaded.append("nllb")
        elif c == "opus-mt":
            get_opus_mt()
            loaded.append("opus-mt")
        elif c == "rvc":
            get_rvc_router()
            loaded.append("rvc")
        else:
            log.warning("warmup: unknown component %s", c)

    return {"loaded": loaded, "ok": True}


# ============================================================================
# Operation: translate
# ============================================================================

def handle_translate(inp: dict) -> dict:
    """Traduction texte simple. Synchrone (pas de streaming)."""
    provider = inp.get("provider", "nllb")
    text = inp.get("text", "")
    src = inp.get("src_lang", "fr")
    tgt = inp.get("tgt_lang", "en")

    if not text or not text.strip():
        return {"translated": text, "provider": provider}
    if src == tgt:
        return {"translated": text, "provider": provider}

    if provider == "opus-mt":
        translated = get_opus_mt().translate(text, src, tgt)
    elif provider == "nllb":
        translated = get_nllb().translate(text, src, tgt)
    else:
        return {"error": "unknown_provider", "provider": provider}

    return {
        "translated": translated,
        "provider": provider,
        "src": src,
        "tgt": tgt,
    }


# ============================================================================
# Operation: live_pipeline
# ============================================================================

def handle_live_pipeline(inp: dict) -> Generator[dict, None, None]:
    """Pipeline cascadé : STT → Trad → TTS [→ RVC] avec streaming.

    Yield des messages JSON au client :
      - {"type": "transcript", "text": "..."}
      - {"type": "translated", "text": "...", "src_lang": "...", "tgt_lang": "..."}
      - {"type": "audio_pcm", "data": "<base64>", "seq": int, "sample_rate": int}
      - {"type": "audio_end"}
      - {"type": "error", "message": "..."}
    """
    mode = inp.get("mode", "gpu-clone")
    audio_b64 = inp.get("audio")
    src_lang = inp.get("src_lang", "fr")
    target_lang = inp.get("target_lang", "en")
    voice_ref_b64 = inp.get("voice_ref")
    rvc_model_id = inp.get("rvc_model_id")
    translation_provider = inp.get("translation_provider", "nllb")
    pre_translated = inp.get("pre_translated")  # cas GPT (trad faite côté Hostinger)

    if not audio_b64:
        yield {"type": "error", "message": "audio is required"}
        return

    # ────────── 1. STT ──────────
    try:
        text = get_whisper().transcribe(audio_b64, src_lang)
    except Exception as e:
        log.exception("STT failed")
        yield {"type": "error", "message": f"STT failed: {e}"}
        return

    if not text or not text.strip():
        yield {"type": "error", "code": "empty_transcription",
               "message": "empty_transcription"}
        return

    yield {"type": "transcript", "text": text}

    # ────────── 2. Traduction (si nécessaire) ──────────
    if target_lang != src_lang:
        if pre_translated:
            translated = pre_translated  # GPT-4o(-mini) fait côté Hostinger
        else:
            try:
                if translation_provider == "opus-mt":
                    translated = get_opus_mt().translate(text, src_lang, target_lang)
                elif translation_provider == "nllb":
                    translated = get_nllb().translate(text, src_lang, target_lang)
                else:
                    translated = text  # fallback
                    log.warning("Unknown trad provider %s, no translation",
                                translation_provider)
            except Exception as e:
                log.exception("translation failed")
                yield {"type": "error", "message": f"translation failed: {e}"}
                return

        yield {"type": "translated", "text": translated,
               "src_lang": src_lang, "tgt_lang": target_lang}
        text_to_speak = translated
    else:
        text_to_speak = text

    # ────────── 3. TTS (streaming) ──────────
    # Cf. Décision 2 du doc 00-decisions-v3.md :
    # Les modes gpu-clone et gpu-native ont la même API côté worker, c'est
    # juste l'origine du voice_ref qui change côté Hostinger (voix kind=clone
    # ou kind=native sélectionnée dans la lib unifiée).
    try:
        if mode in ("gpu-clone", "gpu-native"):
            if not voice_ref_b64:
                yield {"type": "error",
                       "message": f"voice_ref required for {mode} mode"}
                return
            for chunk_b64, seq in get_f5tts().synthesize_streaming(
                text_to_speak, voice_ref_b64, target_lang
            ):
                yield {"type": "audio_pcm", "data": chunk_b64, "seq": seq,
                       "sample_rate": 24000}

        elif mode == "gpu-hybrid":
            if not voice_ref_b64:
                yield {"type": "error",
                       "message": "voice_ref required for gpu-hybrid mode "
                                  "(voix native source)"}
                return
            if not rvc_model_id:
                yield {"type": "error",
                       "message": "rvc_model_id required for gpu-hybrid mode"}
                return

            # Phase 1 : F5-TTS avec la voix native source (synthèse complète,
            # pas de streaming car RVC traite l'audio entier)
            native_audio_array = get_f5tts().synthesize(
                text_to_speak, voice_ref_b64, target_lang
            )

            # Phase 2 : RVC convertit le timbre vers la voix de l'utilisateur
            rvc_model = get_rvc_router().load(rvc_model_id)
            for chunk_b64, seq in rvc_model.convert_streaming(native_audio_array):
                yield {"type": "audio_pcm", "data": chunk_b64, "seq": seq,
                       "sample_rate": 24000}

        else:
            yield {"type": "error", "message": f"unknown mode: {mode}"}
            return

    except Exception as e:
        log.exception("TTS/RVC failed")
        yield {"type": "error", "message": f"synthesis failed: {e}"}
        return

    yield {"type": "audio_end"}


# ============================================================================
# Operation: rvc_convert (mode fichier, hors live)
# ============================================================================

def handle_rvc_convert(inp: dict) -> dict:
    """Conversion RVC d'un audio complet (mode fichier, pas streaming)."""
    rvc_model_id = inp.get("rvc_model_id")
    audio_b64 = inp.get("audio")
    pitch_shift = inp.get("pitch_shift", 0)
    index_rate = inp.get("index_rate", 0.7)

    if not rvc_model_id or not audio_b64:
        return {"error": "rvc_model_id and audio are required"}

    try:
        rvc_model = get_rvc_router().load(rvc_model_id)
        converted_b64 = rvc_model.convert(
            audio_b64,
            pitch_shift=pitch_shift,
            index_rate=index_rate,
        )
        return {
            "audio": converted_b64,
            "sample_rate": 24000,
            "model_id": rvc_model_id,
        }
    except Exception as e:
        log.exception("rvc_convert failed")
        return {"error": "rvc_failed", "message": str(e)}


# ============================================================================
# Démarrage
# ============================================================================

if __name__ == "__main__":
    log.info("VoiceBridge worker starting ...")
    runpod.serverless.start({
        "handler": handler,
        "return_aggregate_stream": True,  # streaming activé
    })
