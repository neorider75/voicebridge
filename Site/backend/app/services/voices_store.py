"""Persistance des voix : ``data/voices/metadata.json`` (JSON simple).

Format :

.. code-block:: json

    { "voices": [
        { "id": "juliette", "name": "Juliette", "language": "fr",
          "backbone": "neutts-nano-french", "duration_seconds": 11,
          "created_at": "...", "protected": true }
    ]}

Verrou ``threading.RLock`` pour écritures concurrentes (uvicorn workers=1
mais des tâches asyncio peuvent quand même se chevaucher).
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import config

_lock = threading.RLock()


def _meta_path() -> Path:
    return config.VOICES_DIR / "metadata.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load() -> dict[str, Any]:
    p = _meta_path()
    if not p.exists():
        return {"voices": []}
    with p.open() as f:
        return json.load(f)


def _save(meta: dict[str, Any]) -> None:
    p = _meta_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    with tmp.open("w") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    tmp.replace(p)


def list_voices() -> list[dict[str, Any]]:
    with _lock:
        return list(_load().get("voices", []))


def get(voice_id: str) -> dict[str, Any] | None:
    for v in list_voices():
        if v["id"] == voice_id:
            return v
    return None


def upsert(voice: dict[str, Any]) -> dict[str, Any]:
    """Crée ou remplace ``voice`` (par son ``id``). Retourne la voix stockée."""
    if "created_at" not in voice:
        voice["created_at"] = _now_iso()
    voice.setdefault("protected", False)
    with _lock:
        meta = _load()
        voices = meta.setdefault("voices", [])
        for i, v in enumerate(voices):
            if v["id"] == voice["id"]:
                voices[i] = voice
                break
        else:
            voices.append(voice)
        _save(meta)
        return voice


def delete(voice_id: str) -> bool:
    with _lock:
        meta = _load()
        voices = meta.setdefault("voices", [])
        for i, v in enumerate(voices):
            if v["id"] == voice_id:
                if v.get("protected"):
                    raise PermissionError("voix protégée")
                voices.pop(i)
                _save(meta)
                return True
        return False


def wav_path(voice_id: str) -> Path:
    return config.VOICES_DIR / f"{voice_id}.wav"


def encoded_path(voice_id: str) -> Path:
    return config.VOICES_ENCODED_DIR / f"{voice_id}.pt"


def ref_text_path(voice_id: str) -> Path:
    return config.VOICES_DIR / f"{voice_id}.txt"


def read_ref_text(voice_id: str) -> str:
    p = ref_text_path(voice_id)
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    return ""
