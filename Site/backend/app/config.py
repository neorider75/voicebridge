"""Lecture/écriture de ``config.json`` (filesystem-only, pas de BDD).

Le fichier vit sous ``$VB_DATA_DIR/config.json`` (par défaut
``/var/voicebridge/data/config.json``). Permissions ``0o600`` enforcées.

Le module n'importe **rien** des routes/auth pour rester importable depuis
``manage.py`` et les tests.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

DATA_DIR = Path(os.environ.get("VB_DATA_DIR", "/var/voicebridge/data"))
CONFIG_PATH = DATA_DIR / "config.json"

VOICES_DIR = DATA_DIR / "voices"
VOICES_ENCODED_DIR = VOICES_DIR / "encoded"
AUDIO_DIR = DATA_DIR / "audio"
MODELS_DIR = DATA_DIR / "models"
INSTALL_DIR = DATA_DIR / "install"
LOGS_DIR = DATA_DIR / "logs"
TMP_DIR = DATA_DIR / "tmp"

_lock = threading.RLock()
_cache: dict[str, Any] | None = None


def load() -> dict[str, Any]:
    """Charge ``config.json`` (cache mémoire pour éviter les I/O répétés)."""
    global _cache
    with _lock:
        if _cache is None:
            with CONFIG_PATH.open() as f:
                _cache = json.load(f)
        return _cache


def reload() -> dict[str, Any]:
    """Force un rechargement depuis le disque (après ``save``)."""
    global _cache
    with _lock:
        _cache = None
        return load()


def save(config: dict[str, Any]) -> None:
    """Écriture atomique + chmod 600."""
    global _cache
    with _lock:
        tmp = CONFIG_PATH.with_suffix(".json.tmp")
        with tmp.open("w") as f:
            json.dump(config, f, indent=2)
        tmp.replace(CONFIG_PATH)
        os.chmod(CONFIG_PATH, 0o600)
        _cache = config


def get(key: str, default: Any = None) -> Any:
    return load().get(key, default)


def set_many(updates: dict[str, Any]) -> dict[str, Any]:
    """Merge superficiel dans ``config.json``."""
    with _lock:
        config = dict(load())
        config.update(updates)
        save(config)
        return config
