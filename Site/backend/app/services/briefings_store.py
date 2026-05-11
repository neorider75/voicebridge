"""Persistance des briefings GPT — ``data/briefings/metadata.json`` (JSON simple).

Cf. Décision 7 du document ``00-decisions-v3.md`` : briefings sauvegardés
réutilisables (templates) + édition par session libre. Trois niveaux de
contexte au total :

1. **Glossaire métier** (config.translation_glossary, permanent global)
2. **Mémoire conversationnelle** (automatique côté backend, RAM)
3. **Briefing de session** (ce module — sauvegardés OU édités à la volée)

Format ``data/briefings/metadata.json`` :

.. code-block:: json

    { "briefings": [
        { "id": "br_xxx", "name": "CODIR mensuel",
          "content": "Réunion CODIR mensuelle, agenda standard, présents : ...",
          "created_at": "...", "updated_at": "..." }
    ]}

Pattern identique à voices_store.py (CRUD + RLock + JSON atomique).
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

# Limites raisonnables pour éviter d'envoyer un mémoire entier à GPT
MAX_NAME_LEN = 80
MAX_CONTENT_LEN = 4000


def _meta_path() -> Path:
    return config.DATA_DIR / "briefings" / "metadata.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load() -> dict[str, Any]:
    p = _meta_path()
    if not p.exists():
        return {"briefings": []}
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return {"briefings": []}


def _save(data: dict[str, Any]) -> None:
    p = _meta_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(p)


# ────────────────────────────────────────────────────────────────────
# CRUD
# ────────────────────────────────────────────────────────────────────


def list_briefings() -> list[dict]:
    """Liste tous les briefings, triés par updated_at desc."""
    with _lock:
        data = _load()
        items = list(data.get("briefings", []))
    items.sort(key=lambda b: b.get("updated_at", b.get("created_at", "")),
               reverse=True)
    return items


def get(briefing_id: str) -> dict | None:
    bid = files.safe_id(briefing_id)
    with _lock:
        for b in _load().get("briefings", []):
            if b.get("id") == bid:
                return b
    return None


def create(name: str, content: str) -> dict:
    """Crée un nouveau briefing. Génère un ID unique ``br_xxx``."""
    name = (name or "").strip()
    content = (content or "").strip()
    if not name:
        raise ValueError("name requis")
    if len(name) > MAX_NAME_LEN:
        raise ValueError(f"name trop long (max {MAX_NAME_LEN} caractères)")
    if len(content) > MAX_CONTENT_LEN:
        raise ValueError(f"content trop long (max {MAX_CONTENT_LEN} caractères)")

    briefing = {
        "id": files.new_id("br_"),
        "name": name,
        "content": content,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    with _lock:
        data = _load()
        data.setdefault("briefings", []).append(briefing)
        _save(data)
    return briefing


def update(briefing_id: str, name: str | None = None,
           content: str | None = None) -> dict | None:
    """Met à jour un briefing existant. Retourne None si non trouvé."""
    bid = files.safe_id(briefing_id)
    with _lock:
        data = _load()
        for b in data.get("briefings", []):
            if b.get("id") == bid:
                if name is not None:
                    name = name.strip()
                    if not name:
                        raise ValueError("name ne peut pas être vide")
                    if len(name) > MAX_NAME_LEN:
                        raise ValueError(f"name trop long (max {MAX_NAME_LEN})")
                    b["name"] = name
                if content is not None:
                    content = content.strip()
                    if len(content) > MAX_CONTENT_LEN:
                        raise ValueError(f"content trop long (max {MAX_CONTENT_LEN})")
                    b["content"] = content
                b["updated_at"] = _now_iso()
                _save(data)
                return b
    return None


def delete(briefing_id: str) -> bool:
    """Supprime un briefing. Retourne True si supprimé, False si non trouvé."""
    bid = files.safe_id(briefing_id)
    with _lock:
        data = _load()
        before = len(data.get("briefings", []))
        data["briefings"] = [b for b in data.get("briefings", [])
                             if b.get("id") != bid]
        if len(data["briefings"]) == before:
            return False
        _save(data)
    return True
