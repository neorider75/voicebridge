"""Persistance de l'historique des sessions Live.

Stocke les sessions terminées dans ``data/sessions/metadata.json`` (JSON
simple, pas de SQL — cf. CLAUDE.md). Une session = un échange WebSocket
``/ws/stream`` du début (configure accepté) à la fin (stop ou disconnect),
avec ses métadonnées (mode, voix, traduction, coût, durée).

Format ``data/sessions/metadata.json`` :

.. code-block:: json

    { "sessions": [
        {
          "id": "sess_xxx",
          "started_at": "2026-05-11T16:30:00Z",
          "ended_at":   "2026-05-11T16:35:42Z",
          "duration_s": 342,
          "mode": "gpu-clone",
          "voice_id": "v_7ce1e5566392",
          "voice_name": "Jean-Christophe",
          "translation": {
            "enabled": true,
            "source_lang": "fr",
            "target_lang": "en",
            "provider": "nllb"
          },
          "rvc_model_id": null,
          "cost_eur": {
            "total": 0.0234,
            "runpod_gpu": 0.0234,
            "openai": 0.0
          },
          "n_utterances": 12
        }
    ]}

Pattern identique à briefings_store.py (CRUD + RLock + écriture atomique).
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import config
from ..utils import files

_lock = threading.RLock()

# Limite de sécurité — on ne garde pas l'historique éternellement en mémoire.
# Au-delà, on tronque les plus anciennes (FIFO). 10 000 sessions = ~3 Mo JSON.
MAX_SESSIONS = 10_000


def _meta_path() -> Path:
    return config.DATA_DIR / "sessions" / "metadata.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load() -> dict[str, Any]:
    p = _meta_path()
    if not p.exists():
        return {"sessions": []}
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return {"sessions": []}


def _save(data: dict[str, Any]) -> None:
    p = _meta_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(p)


# ────────────────────────────────────────────────────────────────────
# CRUD
# ────────────────────────────────────────────────────────────────────


def record(
    *,
    mode: str,
    voice_id: str,
    voice_name: str,
    started_at_ts: float,
    ended_at_ts: float,
    translate: bool = False,
    source_lang: str = "fr",
    target_lang: str = "fr",
    translation_provider: str = "",
    rvc_model_id: str | None = None,
    cost_total_eur: float = 0.0,
    cost_runpod_eur: float = 0.0,
    cost_openai_eur: float = 0.0,
    n_utterances: int = 0,
) -> dict:
    """Enregistre une session terminée. Idempotent — ne plante pas si la
    durée est nulle (l'utilisateur a juste connecté/disconnecté).

    Retourne la session enregistrée (avec son id).
    """
    duration_s = max(0, int(ended_at_ts - started_at_ts))
    # Skip les sessions vides (juste connect/disconnect, pas de parole)
    if duration_s < 1 and n_utterances == 0:
        return {}

    started_iso = datetime.fromtimestamp(
        started_at_ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ended_iso = datetime.fromtimestamp(
        ended_at_ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    session = {
        "id": files.new_id("sess_"),
        "started_at": started_iso,
        "ended_at": ended_iso,
        "duration_s": duration_s,
        "mode": mode,
        "voice_id": voice_id,
        "voice_name": voice_name,
        "translation": {
            "enabled": bool(translate),
            "source_lang": source_lang,
            "target_lang": target_lang,
            "provider": translation_provider if translate else "",
        },
        "rvc_model_id": rvc_model_id,
        "cost_eur": {
            "total": round(cost_total_eur, 5),
            "runpod_gpu": round(cost_runpod_eur, 5),
            "openai": round(cost_openai_eur, 5),
        },
        "n_utterances": n_utterances,
    }

    with _lock:
        data = _load()
        sessions = list(data.get("sessions", []))
        sessions.append(session)
        # FIFO si on dépasse la limite (garde les plus récentes)
        if len(sessions) > MAX_SESSIONS:
            sessions = sessions[-MAX_SESSIONS:]
        data["sessions"] = sessions
        _save(data)

    return session


def list_sessions(limit: int = 100, offset: int = 0) -> dict:
    """Liste les sessions, triées par ended_at desc (plus récentes d'abord).

    Retourne ``{"sessions": [...], "total": N, "limit": L, "offset": O}``.
    """
    limit = max(1, min(500, int(limit)))
    offset = max(0, int(offset))
    with _lock:
        all_sessions = list(_load().get("sessions", []))
    all_sessions.sort(key=lambda s: s.get("ended_at", ""), reverse=True)
    total = len(all_sessions)
    page = all_sessions[offset:offset + limit]
    return {"sessions": page, "total": total, "limit": limit, "offset": offset}


def get(session_id: str) -> dict | None:
    sid = files.safe_id(session_id)
    with _lock:
        for s in _load().get("sessions", []):
            if s.get("id") == sid:
                return s
    return None


def delete(session_id: str) -> bool:
    sid = files.safe_id(session_id)
    with _lock:
        data = _load()
        before = len(data.get("sessions", []))
        data["sessions"] = [s for s in data.get("sessions", [])
                             if s.get("id") != sid]
        after = len(data["sessions"])
        if after == before:
            return False
        _save(data)
    return True


def delete_all() -> int:
    """Supprime tout l'historique. Retourne le nombre de sessions supprimées."""
    with _lock:
        data = _load()
        n = len(data.get("sessions", []))
        data["sessions"] = []
        _save(data)
    return n


# ────────────────────────────────────────────────────────────────────
# Agrégats (pour widget "ce mois")
# ────────────────────────────────────────────────────────────────────


def summary(period_days: int = 30) -> dict:
    """Retourne un résumé sur les N derniers jours :
    nombre de sessions, durée totale, coût total, répartition par mode.
    """
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=period_days)
    cutoff_iso = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    with _lock:
        sessions = list(_load().get("sessions", []))

    recent = [s for s in sessions if s.get("ended_at", "") >= cutoff_iso]

    total_duration_s = sum(s.get("duration_s", 0) for s in recent)
    total_cost = sum(s.get("cost_eur", {}).get("total", 0) for s in recent)
    runpod_cost = sum(s.get("cost_eur", {}).get("runpod_gpu", 0)
                      for s in recent)
    openai_cost = sum(s.get("cost_eur", {}).get("openai", 0) for s in recent)

    by_mode: dict[str, int] = {}
    for s in recent:
        m = s.get("mode", "?")
        by_mode[m] = by_mode.get(m, 0) + 1

    return {
        "period_days": period_days,
        "n_sessions": len(recent),
        "total_duration_s": total_duration_s,
        "total_cost_eur": round(total_cost, 5),
        "cost_runpod_eur": round(runpod_cost, 5),
        "cost_openai_eur": round(openai_cost, 5),
        "by_mode": by_mode,
    }
