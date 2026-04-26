"""Service de rétention : nettoyage périodique.

Deux nettoyages distincts :

1. ``cleanup_expired()`` : supprime les enregistrements TTS/STT dont
   ``expires_at`` est dépassé (rétention 24 h ou 48 h choisie par l'utilisateur).

2. ``cleanup_tmp(max_age_minutes)`` : supprime les fichiers résiduels dans
   ``data/tmp/`` plus vieux que ``max_age_minutes`` (par défaut 60 min).
   Privacy by design : la détection deepfake supprime ses uploads dans son
   ``finally``, mais le STT laisse ``data/tmp/stt_*.wav`` en attente du
   replay client. Si l'utilisateur abandonne, ce fichier doit dégager.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from .. import config
from . import recordings_store

log = logging.getLogger("voicebridge.retention")


def cleanup_expired() -> int:
    """Supprime les enregistrements dont ``expires_at`` est dépassé."""
    now = datetime.now(timezone.utc)
    removed = 0
    for rec in list(recordings_store.list_recordings()):
        expires_at_raw = rec.get("expires_at")
        if not expires_at_raw:
            continue
        try:
            expires_at = datetime.fromisoformat(expires_at_raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if expires_at <= now and recordings_store.delete(rec["id"]):
            removed += 1
    if removed:
        log.info("retention cleanup removed=%d", removed)
    return removed


def cleanup_tmp(max_age_minutes: int = 60) -> int:
    """Supprime les fichiers de ``data/tmp/`` plus vieux que ``max_age_minutes``.

    Cible :
    - ``stt_*.wav`` : WAV temp servi pour le replay STT (peut être abandonné)
    - extractions URL (``vb-url-*``) : si yt-dlp/ffmpeg crash sans cleanup
    - tout autre résidu tmpfile non nettoyé par un ``finally``

    Ne touche PAS aux entrées ``voices/``, ``audio/`` (rétention propre) ou
    ``models/`` — uniquement ``tmp/``.
    """
    tmp_dir = config.TMP_DIR
    if not tmp_dir.exists():
        return 0
    cutoff = time.time() - max_age_minutes * 60
    removed = 0
    for entry in tmp_dir.iterdir():
        try:
            if entry.is_file() and entry.stat().st_mtime < cutoff:
                entry.unlink()
                removed += 1
            elif entry.is_dir() and entry.stat().st_mtime < cutoff:
                # Dossier (ex: extractions URL en TemporaryDirectory non cleanées)
                import shutil
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
        except OSError:
            pass
    if removed:
        log.info("tmp cleanup removed=%d (>%d min)", removed, max_age_minutes)
    return removed
