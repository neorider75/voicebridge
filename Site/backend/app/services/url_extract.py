"""Extraction d'une voix depuis une URL via ``yt-dlp``.

Pipeline :
1. ``yt-dlp --extract-audio`` télécharge la piste audio (m4a/webm/opus selon source).
2. ``ffmpeg`` convertit en WAV 24 kHz mono.
3. ``ffmpeg`` trim les 15 premières secondes de parole nette
   (silencedetect, cf. ``services.audio.trim_first_voiced``).

Les étapes sont yieldées sous forme d'événements ``(step, percent)`` afin de
streamer la progression côté frontend (SSE).
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from collections.abc import Generator
from pathlib import Path

from . import audio

log = logging.getLogger("voicebridge.url_extract")


class UrlExtractError(Exception):
    """Erreur métier."""


def _ensure_yt_dlp() -> str:
    path = shutil.which("yt-dlp")
    if not path:
        raise UrlExtractError("yt-dlp n'est pas installé")
    return path


def extract(url: str, work_dir: Path) -> Generator[tuple[str, int], None, Path]:
    """Renvoie le chemin du WAV trimé.

    Yields :
        (step, percent) avec ``step`` ∈ {"download", "extract", "convert", "trim"}
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    yt_dlp = _ensure_yt_dlp()
    raw_audio = work_dir / "raw.m4a"

    yield ("download", 5)
    try:
        subprocess.run(
            [
                yt_dlp, "-q", "--no-warnings",
                "-f", "bestaudio",
                "-x", "--audio-format", "m4a",
                "-o", str(raw_audio),
                url,
            ],
            check=True, timeout=120, capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        raise UrlExtractError(f"yt-dlp a échoué : {exc.stderr.decode(errors='replace')}") from exc
    except subprocess.TimeoutExpired as exc:
        raise UrlExtractError("yt-dlp a dépassé le timeout") from exc
    yield ("download", 40)

    if not raw_audio.exists():
        # yt-dlp a peut-être suffixé ; on cherche le premier fichier produit.
        candidates = list(work_dir.glob("raw.*"))
        if not candidates:
            raise UrlExtractError("Aucun fichier audio téléchargé")
        raw_audio = candidates[0]

    yield ("extract", 50)
    full_wav = work_dir / "full.wav"
    audio.to_wav_24k_mono(raw_audio, full_wav)
    yield ("convert", 75)

    trimmed = work_dir / "trimmed.wav"
    audio.trim_first_voiced(full_wav, trimmed, duration_seconds=15)
    yield ("trim", 100)

    return trimmed
