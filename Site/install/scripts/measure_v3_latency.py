#!/usr/bin/env python3
"""Mesure la latence end-to-end des modes Live V3 (Phase K).

Usage typique :

    cd /var/voicebridge/app
    sudo -u voicebridge ./venv/bin/python \\
        Site/install/scripts/measure_v3_latency.py \\
        --backend-url https://votre-domaine.com \\
        --api-token sk-votre-token \\
        --voice-id moi \\
        --mode gpu-clone \\
        --runs 5

Mesure les composants :
- Cold start (1er flush) vs warm (flushes suivants)
- Décomposition : VAD flush, STT, traduction, TTS premier chunk
- Latence end-to-end "1er mot audible"

Produit un tableau Markdown que tu peux coller dans
``docs/rvc-user-guide.md`` § 1 si écart > 30%.

Le script utilise un fichier WAV de référence injecté via WebSocket
au lieu d'un vrai micro (pas de capture). 3 phrases tests fournies
(courte, moyenne, longue).
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

# Phrases tests calibrées (durées approx en parlant à débit normal)
TEST_PHRASES = {
    "short": ("Bonjour comment ça va ?", 1.5),         # ~5 mots, ~1.5s parlée
    "medium": (
        "Aujourd'hui je voudrais discuter du projet sur lequel nous "
        "travaillons ensemble depuis plusieurs semaines.",
        4.5,                                            # ~14 mots, ~4.5s
    ),
    "long": (
        "Mesdames messieurs bonjour, je vais maintenant aborder le bilan "
        "trimestriel de notre activité avec une attention particulière sur "
        "les marges et les perspectives stratégiques pour les prochains mois.",
        9.0,                                            # ~30 mots, ~9s
    ),
}


@dataclass
class FlushMeasurement:
    """Une mesure d'un seul flush (1 phrase = 1 flush)."""
    phrase_label: str
    cold_start: bool
    transcript_ms: int = 0
    translated_ms: int = 0
    first_audio_ms: int = 0
    audio_end_ms: int = 0
    error: Optional[str] = None


@dataclass
class RunResults:
    mode: str
    voice_id: str
    measurements: list[FlushMeasurement] = field(default_factory=list)


def make_silent_wav(duration_s: float, sr: int = 16000) -> bytes:
    """Génère un WAV de silence pour simuler un flush VAD côté client.

    Note : ne déclenche pas vraiment un flush (le VAD ignore le silence).
    Pour une vraie mesure, utiliser un WAV avec contenu vocal — voir
    ``--audio-file``.
    """
    try:
        import numpy as np  # type: ignore
        import soundfile as sf  # type: ignore
    except ImportError as exc:
        sys.exit(f"❌ {exc}. pip install numpy soundfile")
    silence = (
        (np.random.randn(int(duration_s * sr)) * 0.001)
        .astype("float32")
    )
    buf = io.BytesIO()
    sf.write(buf, silence, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


async def measure_one_run(args, phrase_label: str, run_idx: int,
                          is_cold: bool) -> FlushMeasurement:
    """Mesure une seule phrase via WebSocket /ws/stream."""
    try:
        import websockets  # type: ignore
    except ImportError as exc:
        sys.exit(f"❌ {exc}. pip install websockets")

    # Construit le WAV à envoyer
    if args.audio_file:
        from pathlib import Path
        wav_bytes = Path(args.audio_file).read_bytes()
        duration = TEST_PHRASES[phrase_label][1]
    else:
        # Génère un signal noise (faux flush, mais pour mesurer le pipeline)
        duration = TEST_PHRASES[phrase_label][1]
        wav_bytes = make_silent_wav(duration)

    ws_url = args.backend_url.rstrip("/").replace(
        "https://", "wss://").replace("http://", "ws://") + "/ws/stream"

    measurement = FlushMeasurement(
        phrase_label=phrase_label,
        cold_start=is_cold,
    )
    t_flush_start = None

    try:
        async with websockets.connect(
            ws_url,
            additional_headers={"Authorization": f"Bearer {args.api_token}"},
            open_timeout=15, ping_interval=30,
        ) as ws:
            # Configure
            cfg = {
                "type": "configure",
                "voice_id": args.voice_id,
                "language": args.language,
                "mode": args.mode,
                "translation_provider": args.provider,
                "target_lang": args.target_lang or args.language,
                "rvc_model_id": args.rvc_model_id,
                "format": "pcm16",
            }
            await ws.send(json.dumps(cfg))

            # Wait for ready
            ready = False
            t_config = time.time()
            while not ready and (time.time() - t_config) < 60:
                msg = await ws.recv()
                try:
                    payload = json.loads(msg)
                    if payload.get("type") == "ready":
                        ready = True
                    elif payload.get("type") == "error":
                        measurement.error = payload.get("message", "config error")
                        return measurement
                except json.JSONDecodeError:
                    pass

            if not ready:
                measurement.error = "configure timeout"
                return measurement

            # Envoie l'audio en chunks PCM 16k mono int16 (~100ms par chunk)
            # On extrait juste le PCM brut du WAV
            try:
                import numpy as np  # type: ignore
                import soundfile as sf  # type: ignore
                arr, sr = sf.read(io.BytesIO(wav_bytes))
                if sr != 16000:
                    raise ValueError(f"SR={sr} attendu 16000")
                if arr.ndim > 1:
                    arr = arr.mean(axis=1)
                pcm_int16 = (arr * 32767.0).astype(np.int16).tobytes()
            except Exception as exc:
                measurement.error = f"audio prep: {exc}"
                return measurement

            t_flush_start = time.time()
            chunk_bytes = 16000 * 2 // 10  # 100ms à 16kHz int16 = 3200 bytes
            for i in range(0, len(pcm_int16), chunk_bytes):
                await ws.send(pcm_int16[i:i + chunk_bytes])
                await asyncio.sleep(0.05)

            # Lit les messages retour pour mesurer chaque étape
            audio_end_seen = False
            try:
                while not audio_end_seen:
                    msg = await asyncio.wait_for(ws.recv(), timeout=30.0)
                    try:
                        payload = json.loads(msg)
                    except json.JSONDecodeError:
                        continue
                    t_now = time.time()
                    elapsed_ms = int((t_now - t_flush_start) * 1000)
                    ptype = payload.get("type")
                    if ptype == "transcript" and not measurement.transcript_ms:
                        measurement.transcript_ms = elapsed_ms
                    elif ptype == "translated" and not measurement.translated_ms:
                        measurement.translated_ms = elapsed_ms
                    elif ptype == "audio_pcm" and not measurement.first_audio_ms:
                        measurement.first_audio_ms = elapsed_ms
                    elif ptype == "audio_end":
                        measurement.audio_end_ms = elapsed_ms
                        audio_end_seen = True
                    elif ptype == "error":
                        measurement.error = payload.get("message", "?")
                        break
            except asyncio.TimeoutError:
                measurement.error = "timeout receive"

            await ws.send(json.dumps({"type": "stop"}))

    except Exception as exc:  # noqa: BLE001
        measurement.error = f"ws error: {exc}"

    return measurement


def render_markdown_table(results: RunResults) -> str:
    """Produit un tableau Markdown avec les chiffres pour la doc."""
    lines = []
    lines.append(f"## Mesures — mode `{results.mode}` voix `{results.voice_id}`")
    lines.append("")
    lines.append("| Phrase | Cold | STT (ms) | Trad (ms) | 1er audio (ms) | Audio fin (ms) | Erreur |")
    lines.append("|---|---|---|---|---|---|---|")
    for m in results.measurements:
        lines.append(
            f"| {m.phrase_label} | {'❄️' if m.cold_start else '🔥'} | "
            f"{m.transcript_ms} | {m.translated_ms} | "
            f"{m.first_audio_ms} | {m.audio_end_ms} | "
            f"{m.error or '—'} |"
        )
    # Stats agrégées
    valid = [m for m in results.measurements if not m.error and m.first_audio_ms]
    if valid:
        lines.append("")
        avg_first = sum(m.first_audio_ms for m in valid) / len(valid)
        lines.append(
            f"**Latence moyenne 1er audio (warm) : {int(avg_first)} ms**"
        )
    return "\n".join(lines)


async def main_async(args):
    print(f"Mode: {args.mode} · Voice: {args.voice_id} · Provider: {args.provider}")
    print(f"Backend: {args.backend_url}")
    print(f"Runs: {args.runs} (1 cold + {args.runs - 1} warm)")
    print()

    results = RunResults(mode=args.mode, voice_id=args.voice_id)

    phrases = ["short"] if args.runs == 1 else ["short", "medium", "long"]

    for run_idx in range(args.runs):
        for phrase_label in phrases:
            is_cold = (run_idx == 0 and phrase_label == "short")
            print(f"  Run {run_idx + 1} / {args.runs} — phrase '{phrase_label}'"
                  f" {'(cold)' if is_cold else '(warm)'}…", flush=True)
            m = await measure_one_run(args, phrase_label, run_idx, is_cold)
            results.measurements.append(m)
            if m.error:
                print(f"    ❌ {m.error}")
            else:
                print(f"    ✅ STT={m.transcript_ms}ms 1er audio={m.first_audio_ms}ms "
                      f"end={m.audio_end_ms}ms")
            await asyncio.sleep(1)  # cool-down entre runs

    print()
    print(render_markdown_table(results))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--backend-url", required=True,
                   help="URL backend (https://...)")
    p.add_argument("--api-token", required=True,
                   help="Token Bearer VoiceBridge")
    p.add_argument("--voice-id", required=True,
                   help="ID de la voix (ex: moi, juliette)")
    p.add_argument("--mode",
                   choices=["cpu-fr-en", "gpu-clone", "gpu-native", "gpu-hybrid"],
                   default="gpu-clone")
    p.add_argument("--language", default="fr",
                   help="Langue source (défaut: fr)")
    p.add_argument("--target-lang", default=None,
                   help="Langue cible traduction (défaut: même que source)")
    p.add_argument("--provider", default="opus-mt-cpu",
                   choices=["opus-mt-cpu", "opus-mt-gpu", "nllb",
                            "gpt-4o-mini", "gpt-4o"])
    p.add_argument("--rvc-model-id", default=None,
                   help="ID modèle RVC (requis si mode=gpu-hybrid)")
    p.add_argument("--runs", type=int, default=5,
                   help="Nombre de runs (défaut: 5)")
    p.add_argument("--audio-file", default=None,
                   help="Path vers un WAV 16kHz mono (sinon noise généré)")
    args = p.parse_args()

    if args.mode == "gpu-hybrid" and not args.rvc_model_id:
        sys.exit("❌ --rvc-model-id requis pour mode gpu-hybrid")

    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
