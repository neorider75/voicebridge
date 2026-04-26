"""Conversions audio (ffmpeg) + validation des uploads.

Toutes les conversions passent par le binaire ``ffmpeg`` du système (préférable
à ``ffmpeg-python`` pour rester simple). Les uploads sont validés par
*magic bytes* via ``python-magic`` (la simple extension ne suffit pas).
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from .. import config

log = logging.getLogger("voicebridge.audio")

ALLOWED_AUDIO_MIMES: set[str] = {
    "audio/wav",
    "audio/x-wav",
    "audio/mpeg",      # MP3
    "audio/mp4",       # M4A
    "audio/x-m4a",
    "audio/m4a",
    "audio/ogg",
    "audio/x-flac",
    "audio/flac",
    "audio/webm",      # MediaRecorder par défaut sur Chrome/Firefox
    "video/webm",      # certains libmagic taggent webm en video/* même sans piste vidéo
}


class AudioError(Exception):
    """Erreur métier (à transformer en HTTPException côté route)."""


def _run(cmd: list[str], timeout: int = 60) -> None:
    log.debug("ffmpeg cmd=%s", " ".join(cmd))
    try:
        subprocess.run(cmd, check=True, timeout=timeout, capture_output=True)
    except FileNotFoundError as exc:
        # Binaire absent (ex : ffmpeg non installé). On remonte une AudioError
        # explicite plutôt que de laisser cascader un 500 "Internal Server Error"
        # opaque côté client.
        raise AudioError(
            f"binaire introuvable : {cmd[0]}. "
            f"Installez-le sur le serveur avec : sudo apt install {cmd[0]}"
        ) from exc
    except subprocess.CalledProcessError as exc:  # pragma: no cover
        raise AudioError(f"{cmd[0]} a échoué : {exc.stderr.decode(errors='replace')}") from exc
    except subprocess.TimeoutExpired as exc:
        raise AudioError(f"{cmd[0]} a dépassé le timeout") from exc


def detect_mime(path: Path) -> str:
    """Détection par magic bytes (refuse le simple suffixe)."""
    try:
        import magic  # type: ignore
    except ImportError:  # pragma: no cover
        # Fallback minimaliste : on lit les premiers octets manuellement
        with path.open("rb") as f:
            head = f.read(12)
        if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
            return "audio/wav"
        if head.startswith(b"ID3") or head[0:2] == b"\xff\xfb":
            return "audio/mpeg"
        if head[4:8] == b"ftyp":
            return "audio/mp4"
        if head.startswith(b"OggS"):
            return "audio/ogg"
        if head.startswith(b"fLaC"):
            return "audio/flac"
        if head.startswith(b"\x1a\x45\xdf\xa3"):  # EBML magic = Matroska/WebM
            return "audio/webm"
        return "application/octet-stream"
    return magic.from_file(str(path), mime=True)


def validate_upload(path: Path, max_bytes: int) -> str:
    """Vérifie taille + type MIME. Retourne le MIME détecté."""
    size = path.stat().st_size
    if size > max_bytes:
        raise AudioError(f"Fichier trop volumineux : {size} > {max_bytes}")
    mime = detect_mime(path)
    if mime not in ALLOWED_AUDIO_MIMES:
        raise AudioError(f"Type audio non supporté : {mime}")
    return mime


def to_wav_24k_mono(src: Path, dst: Path) -> None:
    """Convertit n'importe quel audio en WAV 24 kHz mono PCM s16 (format NeuTTS)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run([
        "ffmpeg", "-y", "-i", str(src),
        "-ac", "1", "-ar", "24000", "-sample_fmt", "s16",
        str(dst),
    ])


def to_wav_16k_mono(src: Path, dst: Path) -> None:
    """Pour Kyutai STT (16 kHz mono PCM s16)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run([
        "ffmpeg", "-y", "-i", str(src),
        "-ac", "1", "-ar", "16000", "-sample_fmt", "s16",
        str(dst),
    ])


def wav_to_mp3(src: Path, dst: Path, bitrate: str = "128k") -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run([
        "ffmpeg", "-y", "-i", str(src),
        "-codec:a", "libmp3lame", "-b:a", bitrate,
        str(dst),
    ])


def trim_first_voiced(src: Path, dst: Path, duration_seconds: int = 15) -> None:
    """Récupère les ``duration_seconds`` premières secondes de **parole nette**.

    Utilise ``silencedetect`` pour sauter les silences en tête, puis trim.
    En cas d'échec de la détection, on fait un trim brut depuis 0.

    Seuils tunés pour clonage de voix : -40 dB / 200 ms attrape les courtes
    respirations et hésitations qu'un seuil plus laxiste (-30 dB / 400 ms)
    laisserait passer. Important pour XTTS qui prend les premières secondes
    comme conditioning speaker — du silence en tête dégrade l'identité.
    On ajoute aussi 50 ms de marge avant l'attaque pour ne pas couper les
    consonnes douces (h, f, s).
    """
    dst.parent.mkdir(parents=True, exist_ok=True)

    # 1) Détection du premier silence_end. Best-effort : si ça plante on
    # retombe sur offset 0, le trim brut suivant lèvera une AudioError claire
    # via _run() si ffmpeg manque vraiment.
    offset = 0.0
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-i", str(src),
                "-af", "silencedetect=noise=-40dB:d=0.2",
                "-f", "null", "-",
            ],
            capture_output=True, text=True, timeout=60,
        )
        for line in (proc.stderr or "").splitlines():
            if "silence_end" in line:
                # Format : ... silence_end: 1.234 ...
                try:
                    raw = float(line.split("silence_end:")[1].strip().split(" ")[0])
                    # Recule de 50 ms pour ne pas couper l'attaque des
                    # consonnes douces (h, f, s) qui sont plus basses en énergie.
                    offset = max(0.0, raw - 0.05)
                    break
                except (IndexError, ValueError):
                    continue
        if offset > 0:
            log.info("trim_first_voiced: skipping %.2fs of leading silence", offset)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        offset = 0.0

    _run([
        "ffmpeg", "-y", "-ss", f"{offset:.2f}", "-i", str(src),
        "-t", str(duration_seconds),
        "-ac", "1", "-ar", "24000", "-sample_fmt", "s16",
        str(dst),
    ])


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def audio_duration_seconds(path: Path) -> float:
    """Renvoie la durée en secondes via ``ffprobe``.

    Ne lève pas d'erreur (la durée est de la métadonnée non-bloquante) — on
    se contente de logger les cas anormaux.
    """
    try:
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(path),
            ],
            capture_output=True, text=True, timeout=10, check=True,
        )
        return float(proc.stdout.strip())
    except FileNotFoundError:
        log.warning("ffprobe introuvable — durée renvoyée à 0 (apt install ffmpeg ?)")
        return 0.0
    except Exception:  # noqa: BLE001
        return 0.0
