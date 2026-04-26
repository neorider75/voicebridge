"""Service de rétention : nettoyage périodique des fichiers audio expirés.

Réutilise la logique de ``manage.py cleanup-expired`` en l'exposant comme
fonction Python appelable depuis APScheduler (au boot du service uvicorn).

Le cron système ``/etc/cron.d/voicebridge`` reste en place comme ceinture +
bretelles ; le scheduler in-process passe toutes les 10 minutes pour
réagir plus vite aux expirations.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from .. import config
from . import recordings_store

log = logging.getLogger("voicebridge.retention")


def cleanup_expired() -> int:
    """Supprime les enregistrements dont ``expires_at`` est dépassé.

    Retourne le nombre d'éléments supprimés.
    """
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
