"""Lecture du ``config.json`` embarqué dans le bundle .app et persistance des
credentials utilisateur via macOS Keychain (``keyring``).

L'URL du serveur est figée dans le bundle au moment de l'install (script
bash sur le VPS). L'utilisateur ne saisit que sa clé API au premier
lancement.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

KEYRING_SERVICE = "VoiceBridge"

# ``keyring`` peut ne pas être disponible en environnement de dev brut ;
# on fallback sur un dict mémoire (perdu au quit) pour ne pas casser.
try:
    import keyring  # type: ignore
    _KEYRING_OK = True
except ImportError:
    keyring = None  # type: ignore
    _KEYRING_OK = False
    _MEM_STORE: dict[str, str] = {}


def bundle_resources_dir() -> Path:
    """Renvoie le dossier ``Resources/`` à l'intérieur du .app, ou le dossier
    courant en mode dev (``python -m voicebridge_app``)."""
    if getattr(sys, "frozen", False):
        # PyInstaller / py2app : Contents/Resources/
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent


def load_bundle_config() -> dict[str, Any]:
    candidates = [
        bundle_resources_dir() / "config.json",
        Path(__file__).resolve().parent.parent / "config.json",
    ]
    for c in candidates:
        if c.exists():
            with c.open() as f:
                return json.load(f)
    # Defaults dev
    return {"server_url": os.environ.get("VB_SERVER_URL", "https://localhost"),
            "version": "dev"}


def kr_get(key: str) -> str | None:
    if _KEYRING_OK:
        return keyring.get_password(KEYRING_SERVICE, key)
    return _MEM_STORE.get(key)


def kr_set(key: str, value: str) -> None:
    if _KEYRING_OK:
        keyring.set_password(KEYRING_SERVICE, key, value)
    else:
        _MEM_STORE[key] = value


def kr_delete(key: str) -> None:
    if _KEYRING_OK:
        try:
            keyring.delete_password(KEYRING_SERVICE, key)
        except Exception:  # noqa: BLE001
            pass
    else:
        _MEM_STORE.pop(key, None)
