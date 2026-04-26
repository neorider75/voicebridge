"""Extraction d'une voix depuis une URL via ``yt-dlp``.

Pipeline :
1. ``yt-dlp --extract-audio`` télécharge la piste audio (m4a/webm/opus selon source).
2. ``ffmpeg`` convertit en WAV 24 kHz mono.
3. ``ffmpeg`` trim les 15 premières secondes de parole nette
   (silencedetect, cf. ``services.audio.trim_first_voiced``).

Les étapes sont yieldées sous forme d'événements ``(step, percent)`` afin de
streamer la progression côté frontend (SSE).

Cookies YouTube : depuis fin 2024, YouTube bloque les requêtes anonymes de
yt-dlp (« Sign in to confirm you're not a bot »). Si un fichier
``data/yt-dlp-cookies.txt`` existe sur le serveur, il est passé à yt-dlp
via ``--cookies``. À l'utilisateur de le déposer (export depuis Firefox
extension « cookies.txt » ou Chrome « Get cookies.txt LOCALLY »).
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from collections.abc import Generator
from pathlib import Path

from .. import config
from . import audio

log = logging.getLogger("voicebridge.url_extract")


class UrlExtractError(Exception):
    """Erreur métier."""


def _ensure_yt_dlp() -> str:
    path = shutil.which("yt-dlp")
    if not path:
        raise UrlExtractError(
            "binaire introuvable : yt-dlp. "
            "Installez-le sur le serveur avec : sudo pip install yt-dlp "
            "(ou sudo apt install yt-dlp si dispo)"
        )
    return path


def _cookies_arg() -> list[str]:
    """Retourne ``["--cookies", path]`` si un fichier cookies est présent
    sur le serveur, sinon liste vide. yt-dlp exige des cookies pour la
    plupart des vidéos YouTube depuis fin 2024.
    """
    p = config.DATA_DIR / "yt-dlp-cookies.txt"
    if p.exists() and p.stat().st_size > 0:
        return ["--cookies", str(p)]
    return []


def _translate_yt_dlp_error(stderr: str) -> str:
    """Convertit les stderr yt-dlp les plus courantes en messages
    actionnables côté UI."""
    msg = stderr.strip()
    low = msg.lower()
    if "sign in to confirm you're not a bot" in low or "use --cookies" in low:
        return (
            "YouTube bloque les requêtes anonymes (anti-bot). Solution : "
            "exportez vos cookies YouTube depuis votre navigateur (extension "
            "« Get cookies.txt LOCALLY ») et déposez le fichier sur le serveur "
            "à l'emplacement /var/voicebridge/data/yt-dlp-cookies.txt "
            "(chmod 600, owner voicebridge:voicebridge). "
            "Alternative : utilisez une autre source (upload direct, ou URL "
            "non-YouTube)."
        )
    if "video unavailable" in low or "private video" in low:
        return "Vidéo indisponible ou privée — choisissez une autre URL."
    if "unsupported url" in low:
        return "URL non supportée par yt-dlp."
    return f"yt-dlp a échoué : {msg[:300]}"


def extract(url: str, work_dir: Path) -> Generator[tuple[str, int], None, Path]:
    """Renvoie le chemin du WAV trimé.

    Yields :
        (step, percent) avec ``step`` ∈ {"download", "extract", "convert", "trim"}
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    yt_dlp = _ensure_yt_dlp()
    raw_audio = work_dir / "raw.m4a"

    cookies_args = _cookies_arg()
    if cookies_args:
        log.info("yt-dlp : utilisation du fichier cookies %s", cookies_args[1])

    # Bypass anti-bot YouTube : on essaye d'abord sans cookies en utilisant
    # des player_clients alternatifs (tv_embedded + web_safari), qui ne
    # déclenchent souvent pas la vérification "you're not a bot". Si
    # l'utilisateur a déposé un cookies.txt, on l'ajoute par sécurité.
    yt_dlp_base = [
        yt_dlp, "-q", "--no-warnings",
        "-f", "bestaudio",
        "-x", "--audio-format", "m4a",
        "-o", str(raw_audio),
        "--extractor-args", "youtube:player_client=tv_embedded,web_safari,android",
        "--user-agent",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.0 Safari/605.1.15",
        *cookies_args,
        url,
    ]

    yield ("download", 5)
    try:
        subprocess.run(yt_dlp_base, check=True, timeout=120, capture_output=True)
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode(errors='replace')
        raise UrlExtractError(_translate_yt_dlp_error(stderr)) from exc
    except subprocess.TimeoutExpired as exc:
        raise UrlExtractError("yt-dlp a dépassé le timeout (120 s)") from exc
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
