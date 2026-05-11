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
    """Liste toutes les voix.

    Backfill : les voix sans champ ``kind`` sont marquées "clone" (rétrocompat).
    """
    with _lock:
        voices = list(_load().get("voices", []))
    for v in voices:
        v.setdefault("kind", "clone")
    return voices


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
    # Statut : "encoding" pendant le pré-encodage (background task), "ready"
    # quand utilisable, "failed" si l'encodage a échoué.
    # Rétrocompat : les voix créées avant le statut sont considérées "ready".
    voice.setdefault("status", "ready")
    # kind : "clone" (voix clonée à partir d'un sample utilisateur) ou
    # "native" (voix générique dans une langue cible, utilisée comme
    # référence prosodique pour les modes gpu-native / gpu-hybrid).
    # Rétrocompat : les voix créées avant ce champ sont "clone".
    voice.setdefault("kind", "clone")
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


def patch(voice_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    """Met à jour les champs ``updates`` de la voix, sans écraser le reste.

    Utilisé par le background task d'encodage pour passer status=encoding →
    status=ready (ou failed) à la fin du traitement, sans avoir à reconstruire
    tout le payload.
    """
    with _lock:
        meta = _load()
        voices = meta.setdefault("voices", [])
        for i, v in enumerate(voices):
            if v["id"] == voice_id:
                v.update(updates)
                voices[i] = v
                _save(meta)
                return v
        return None


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


def write_ref_text(voice_id: str, text: str) -> None:
    """Persiste le texte de référence (ce que dit l'audio source).

    Indispensable pour NeuTTS qui phonémise ce texte avant de cloner —
    sans ref_text, ``_to_phones("")`` rend une liste vide et l'inférence
    plante avec IndexError.
    """
    p = ref_text_path(voice_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text((text or "").strip(), encoding="utf-8")
