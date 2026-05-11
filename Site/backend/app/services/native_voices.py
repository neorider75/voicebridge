"""Catalogue + installation automatique des voix natives publiques.

Pour les modes ``gpu-native`` et ``gpu-hybrid`` (cf. Décision 2 du doc
``00-decisions-v3.md``), on a besoin d'une voix de référence neutre dans
la langue cible. Plutôt que de demander à l'utilisateur d'importer ses
propres samples, on télécharge un set par défaut depuis des sources
publiques (Wikimedia Commons / Internet Archive — domaine public).

Sources sélectionnées : Spoken Wikipedia (lecteurs natifs, débit clair,
qualité podcast). Format source : OGG Vorbis → on convertit en WAV
mono 24 kHz 16-bit (compatible avec ce qu'attend F5-TTS).

Catalogue volontairement court (1 voix/langue) pour V1. L'utilisateur
peut ajouter d'autres samples manuellement en suivant le même pattern.
"""
from __future__ import annotations

import io
import logging
import threading
from pathlib import Path
from urllib.request import Request, urlopen

import numpy as np
import soundfile as sf

from .. import config
from ..utils import files
from . import voices_store

log = logging.getLogger("voicebridge.native_voices")

# Catalogue par défaut — 6 langues couvertes.
# URLs VÉRIFIÉES (HTTP 200, contenu audio/* présent au moins).
#
# Sources :
# - EN : whisper.cpp samples (Public Domain, US Government work) — clip
#        JFK "We choose to go to the moon", court (~11 s) mais qualité
#        studio, ne nécessite pas de User-Agent spécial.
# - FR/ES/DE/IT/PT : Wikimedia Commons (Spoken Wikipedia, lecteurs
#        volontaires natifs). Fichiers OGG de plusieurs minutes — on
#        tronque les 20 premières secondes au download. Wikimedia
#        EXIGE un User-Agent descriptif (sinon HTTP 429) — déjà géré
#        par _download_url().
#
# Licences :
# - PD (EN), CC0 (IT) : aucune attribution requise
# - CC-BY-SA 3.0 (FR/ES/DE/PT) : attribution stockée dans le champ
#   ``license`` de la voix → exposable via /api/voices.
NATIVE_VOICE_CATALOG: dict[str, dict] = {
    "en": {
        "name": "English Native (JFK)",
        "url": "https://github.com/ggml-org/whisper.cpp/raw/master/samples/jfk.wav",
        "language": "en",
        "duration_target_s": 11,
        "license": "Public Domain (US Government)",
        "format": "wav",
    },
    "fr": {
        "name": "Français Natif (Wikipédia · Braille)",
        "url": "https://upload.wikimedia.org/wikipedia/commons/0/0e/Fr-braille.ogg",
        "language": "fr",
        "duration_target_s": 20,
        "license": "CC-BY-SA 3.0 (Wikipédia parlé · Braille)",
        "format": "ogg",
    },
    "es": {
        "name": "Español Nativo (Wikipedia · Aaron Swartz)",
        "url": "https://upload.wikimedia.org/wikipedia/commons/3/31/Es-Aaron_Swartz-article.ogg",
        "language": "es",
        "duration_target_s": 20,
        "license": "CC-BY-SA 3.0 (Wikipedia hablada · Aaron Swartz)",
        "format": "ogg",
    },
    "de": {
        "name": "Deutsche Stimme (Wikipedia · Albert Einstein)",
        "url": "https://upload.wikimedia.org/wikipedia/commons/4/44/De-Albert_Einstein.ogg",
        "language": "de",
        "duration_target_s": 20,
        "license": "CC-BY-SA 3.0 (Gesprochene Wikipedia · Albert Einstein)",
        "format": "ogg",
    },
    "it": {
        "name": "Voce Italiana (Wikipedia · Argentina)",
        "url": "https://upload.wikimedia.org/wikipedia/commons/2/2f/It-argentina-article.ogg",
        "language": "it",
        "duration_target_s": 20,
        "license": "CC0 (Wikipedia parlata · Argentina)",
        "format": "ogg",
    },
    "pt": {
        "name": "Voz Portuguesa (Wikipédia · Andrelândia)",
        "url": "https://upload.wikimedia.org/wikipedia/commons/f/ff/Andrel%C3%A2ndia_intro.ogg",
        "language": "pt",
        "duration_target_s": 20,
        "license": "CC-BY-SA 3.0 (Wikipédia falada · Andrelândia)",
        "format": "ogg",
    },
}


def _native_dir() -> Path:
    return config.VOICES_DIR / "native"


def _voice_id_for(lang: str) -> str:
    return f"native_{lang}"


def _download_url(url: str, timeout: float = 60.0) -> bytes:
    """Télécharge un URL en bytes. Identifie un User-Agent pour passer
    les filtres de Wikimedia (qui rejette python-urllib brut)."""
    req = Request(url, headers={
        "User-Agent": "VoiceBridge/1.0 (https://voice-bridge.app native-voice-installer)",
        "Accept": "audio/ogg, audio/*",
    })
    with urlopen(req, timeout=timeout) as r:
        return r.read()


def import_wav_bytes(*, lang: str, name: str, wav_bytes: bytes,
                     license_str: str = "User-provided") -> dict:
    """Import direct depuis un WAV en bytes (cas upload manuel UI).

    Convertit en 24 kHz mono PCM 16-bit si nécessaire, tronque à 30 s
    max (largement assez pour une ref prosodique). Enregistre dans
    voices_store avec kind="native" et protected=True.
    """
    if lang not in {"fr", "en", "es", "de", "it", "pt", "nl", "ja", "zh"}:
        raise ValueError(f"langue {lang!r} non supportée (fr/en/es/de/it/pt/nl/ja/zh)")
    if not name or not name.strip():
        raise ValueError("name requis")
    name = name.strip()[:80]

    # Lecture audio
    audio, sr = sf.read(io.BytesIO(wav_bytes))
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    # Tronque à 30 s max
    max_samples = int(30.0 * sr)
    if len(audio) > max_samples:
        audio = audio[:max_samples]
    # Resample → 24 kHz
    target_sr = 24000
    if sr != target_sr:
        ratio = target_sr / sr
        new_len = int(len(audio) * ratio)
        x_old = np.linspace(0, 1, len(audio), endpoint=False)
        x_new = np.linspace(0, 1, new_len, endpoint=False)
        audio = np.interp(x_new, x_old, audio)
    audio_int16 = np.clip(audio * 32767.0, -32768, 32767).astype(np.int16)
    duration = len(audio_int16) / target_sr

    # Sauvegarde WAV
    voice_id = _voice_id_for(lang)
    _native_dir().mkdir(parents=True, exist_ok=True)
    wav_path = _native_dir() / f"{voice_id}.wav"
    buf = io.BytesIO()
    sf.write(buf, audio_int16, target_sr, format="WAV", subtype="PCM_16")
    wav_path.write_bytes(buf.getvalue())

    # Registre dans voices_store
    voices_store.upsert({
        "id": voice_id,
        "name": name,
        "language": lang,
        "duration_seconds": round(duration, 2),
        "kind": "native",
        "license": license_str,
        "wav_path": str(wav_path),
        "status": "ready",
        "protected": True,
    })
    return {
        "voice_id": voice_id,
        "duration": round(duration, 2),
        "size_bytes": wav_path.stat().st_size,
    }


def _audio_to_wav_24k(audio_bytes: bytes, max_duration_s: float = 20.0) -> tuple[bytes, float]:
    """Convertit un audio (OGG/WAV/MP3/FLAC) en WAV PCM 16-bit mono 24 kHz.

    Tronque à ``max_duration_s`` secondes pour ne garder que le début
    propre du sample (évite les artefacts de fin et limite l'empreinte).
    """
    # soundfile lit ogg/wav/flac via libsndfile (compilé avec Vorbis sur
    # Debian/Ubuntu par défaut). Pour MP3, libsndfile >=1.1 supporte aussi.
    audio, sr = sf.read(io.BytesIO(audio_bytes))
    # Mono
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    # Tronque
    max_samples = int(max_duration_s * sr)
    if len(audio) > max_samples:
        audio = audio[:max_samples]
    # Resample 24 kHz si nécessaire (interp linéaire — suffisant pour
    # une ref prosodique, F5-TTS est tolérant)
    target_sr = 24000
    if sr != target_sr:
        ratio = target_sr / sr
        new_len = int(len(audio) * ratio)
        x_old = np.linspace(0, 1, len(audio), endpoint=False)
        x_new = np.linspace(0, 1, new_len, endpoint=False)
        audio = np.interp(x_new, x_old, audio)
    # int16 PCM
    audio_int16 = np.clip(audio * 32767.0, -32768, 32767).astype(np.int16)
    duration = len(audio_int16) / target_sr
    buf = io.BytesIO()
    sf.write(buf, audio_int16, target_sr, format="WAV", subtype="PCM_16")
    return buf.getvalue(), duration


_install_lock = threading.Lock()


def install_all(force: bool = False) -> dict:
    """Télécharge + installe TOUTES les voix natives du catalogue.

    Args:
        force: si True, ré-installe même les voix déjà présentes.

    Retourne ``{"installed": [...], "skipped": [...], "failed": [{lang, err}]}``.
    """
    with _install_lock:
        installed: list[str] = []
        skipped: list[str] = []
        failed: list[dict] = []
        _native_dir().mkdir(parents=True, exist_ok=True)

        for lang, spec in NATIVE_VOICE_CATALOG.items():
            voice_id = _voice_id_for(lang)
            existing = voices_store.get(voice_id)
            if existing and not force:
                skipped.append(lang)
                continue
            try:
                log.info("Installing native voice %s (%s)…", lang, spec["url"])
                ogg = _download_url(spec["url"])
                wav_bytes, duration = _audio_to_wav_24k(
                    ogg, max_duration_s=spec.get("duration_target_s", 15))
                wav_path = _native_dir() / f"{voice_id}.wav"
                wav_path.write_bytes(wav_bytes)
                voices_store.upsert({
                    "id": voice_id,
                    "name": spec["name"],
                    "language": spec["language"],
                    "duration_seconds": round(duration, 2),
                    "kind": "native",
                    "license": spec.get("license", ""),
                    "source_url": spec["url"],
                    "wav_path": str(wav_path),
                    "status": "ready",
                    "protected": True,  # pas supprimable depuis l'UI standard
                })
                installed.append(lang)
                log.info("Native voice %s installed (%.1f s).", lang, duration)
            except Exception as exc:  # noqa: BLE001
                log.warning("Native voice %s install failed: %s", lang, exc)
                failed.append({"lang": lang, "name": spec.get("name", ""),
                               "error": str(exc)})

        return {
            "installed": installed,
            "skipped": skipped,
            "failed": failed,
            "total_catalog": len(NATIVE_VOICE_CATALOG),
        }


def install_one(lang: str, force: bool = False) -> dict:
    """Installe une seule voix native par code langue.

    Retourne ``{"installed": bool, "voice_id": str, "error": str|None}``.
    """
    if lang not in NATIVE_VOICE_CATALOG:
        return {"installed": False, "voice_id": "",
                "error": f"Langue {lang!r} pas dans le catalogue"}
    spec = NATIVE_VOICE_CATALOG[lang]
    voice_id = _voice_id_for(lang)
    existing = voices_store.get(voice_id)
    if existing and not force:
        return {"installed": False, "voice_id": voice_id,
                "error": "already_installed"}
    try:
        ogg = _download_url(spec["url"])
        wav_bytes, duration = _audio_to_wav_24k(
            ogg, max_duration_s=spec.get("duration_target_s", 15))
        _native_dir().mkdir(parents=True, exist_ok=True)
        wav_path = _native_dir() / f"{voice_id}.wav"
        wav_path.write_bytes(wav_bytes)
        voices_store.upsert({
            "id": voice_id,
            "name": spec["name"],
            "language": spec["language"],
            "duration_seconds": round(duration, 2),
            "kind": "native",
            "license": spec.get("license", ""),
            "source_url": spec["url"],
            "wav_path": str(wav_path),
            "status": "ready",
            "protected": True,
        })
        return {"installed": True, "voice_id": voice_id, "error": None}
    except Exception as exc:  # noqa: BLE001
        return {"installed": False, "voice_id": voice_id, "error": str(exc)}


def catalog_summary() -> list[dict]:
    """Liste résumée du catalogue, indique ce qui est déjà installé."""
    out = []
    for lang, spec in NATIVE_VOICE_CATALOG.items():
        existing = voices_store.get(_voice_id_for(lang))
        out.append({
            "lang": lang,
            "name": spec["name"],
            "voice_id": _voice_id_for(lang),
            "url": spec["url"],
            "license": spec.get("license", ""),
            "installed": existing is not None,
        })
    return out
