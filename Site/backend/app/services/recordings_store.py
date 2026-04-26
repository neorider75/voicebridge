"""Persistance des enregistrements générés (TTS / STT).

``data/audio/metadata.json`` :

.. code-block:: json

    { "recordings": [
        { "id": "rec_xxx", "mode": "tts", "voice_id": "juliette",
          "voice_name": "Juliette", "voice_language": "fr",
          "created_at": "...", "expires_at": "...",
          "duration_seconds": 12, "format": "wav", "quality": "high",
          "size_mb": 0.4 }
    ]}

Les fichiers vivent côte à côte : ``rec_xxx.wav`` ou ``rec_xxx.mp3``.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .. import config

_lock = threading.RLock()


def _meta_path() -> Path:
    return config.AUDIO_DIR / "metadata.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _load() -> dict[str, Any]:
    p = _meta_path()
    if not p.exists():
        return {"recordings": []}
    with p.open() as f:
        return json.load(f)


def _save(meta: dict[str, Any]) -> None:
    p = _meta_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    with tmp.open("w") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    tmp.replace(p)


def list_recordings(mode: str = "all") -> list[dict[str, Any]]:
    with _lock:
        recs = list(_load().get("recordings", []))
    if mode == "all":
        return recs
    return [r for r in recs if r.get("mode") == mode]


def get(rec_id: str) -> dict[str, Any] | None:
    for r in list_recordings():
        if r["id"] == rec_id:
            return r
    return None


def add(rec: dict[str, Any], retention: str) -> dict[str, Any]:
    """Calcule ``expires_at`` selon ``retention`` ∈ {"24h", "48h"}."""
    rec["created_at"] = _iso(_now())
    if retention == "24h":
        rec["expires_at"] = _iso(_now() + timedelta(hours=24))
    elif retention == "48h":
        rec["expires_at"] = _iso(_now() + timedelta(hours=48))
    else:
        raise ValueError("retention doit être '24h' ou '48h' pour add()")
    with _lock:
        meta = _load()
        meta.setdefault("recordings", []).append(rec)
        _save(meta)
    return rec


def delete(rec_id: str) -> bool:
    with _lock:
        meta = _load()
        recs = meta.setdefault("recordings", [])
        for i, r in enumerate(recs):
            if r["id"] == rec_id:
                recs.pop(i)
                _save(meta)
                # Suppression des fichiers physiques
                for ext in (".wav", ".mp3"):
                    fp = config.AUDIO_DIR / f"{rec_id}{ext}"
                    if fp.exists():
                        fp.unlink()
                return True
        return False


def file_path(rec_id: str, fmt: str) -> Path:
    suffix = ".mp3" if fmt == "mp3" else ".wav"
    return config.AUDIO_DIR / f"{rec_id}{suffix}"
