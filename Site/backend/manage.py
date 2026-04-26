#!/usr/bin/env python3
"""CLI VoiceBridge.

Commandes :
  - reset-password         : redéfinit le mot de passe admin (bcrypt cost 12)
  - cleanup-expired        : supprime les fichiers audio expirés (rétention)
  - regenerate-api-key     : régénère la clé API Bearer et l'affiche une fois

Toutes les commandes opèrent sur ``$VB_DATA_DIR/config.json``
(par défaut ``/var/voicebridge/data/config.json``).
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

from passlib.hash import bcrypt

DATA_DIR = Path(os.environ.get("VB_DATA_DIR", "/var/voicebridge/data"))
CONFIG_PATH = DATA_DIR / "config.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        print(f"❌ {CONFIG_PATH} introuvable. L'installation a-t-elle été menée ?")
        sys.exit(1)
    with CONFIG_PATH.open() as f:
        return json.load(f)


def _save_config(config: dict) -> None:
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    with tmp.open("w") as f:
        json.dump(config, f, indent=2)
    tmp.replace(CONFIG_PATH)
    os.chmod(CONFIG_PATH, 0o600)


def reset_password() -> None:
    new_pw = getpass.getpass("Nouveau mot de passe : ")
    confirm = getpass.getpass("Confirmer            : ")
    if new_pw != confirm:
        print("❌ Les mots de passe ne correspondent pas")
        sys.exit(1)
    if len(new_pw) < 8:
        print("❌ Mot de passe trop court (min 8 caractères)")
        sys.exit(1)

    config = _load_config()
    config["password_hash"] = bcrypt.hash(new_pw, rounds=12)
    _save_config(config)
    print("✅ Mot de passe mis à jour")


def regenerate_api_key() -> None:
    token = "sk-" + secrets.token_hex(16)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    config = _load_config()
    config["api_token_hash"] = token_hash
    config["api_token_created_at"] = _now_iso()
    _save_config(config)
    print()
    print("═══════════════════════════════════════════════════════")
    print("   NOUVELLE CLÉ API VoiceBridge")
    print("═══════════════════════════════════════════════════════")
    print()
    print(f"   {token}")
    print()
    print("   ⚠️  Notez-la maintenant. Elle ne sera plus affichée.")
    print("   L'ancienne clé est immédiatement invalide.")
    print()
    print("═══════════════════════════════════════════════════════")


def cleanup_expired() -> None:
    """Supprime les fichiers audio dont ``expires_at`` est dépassé.

    Lit ``data/audio/metadata.json`` (un objet ``{"recordings": [...]}``).
    Chaque entrée :
      - ``id`` : str (nom de fichier sans extension)
      - ``expires_at`` : str ISO 8601 (UTC) ou null pour rétention "session" (mais
        une rétention session ne devrait jamais écrire sur disque, voir TTS).
    """
    audio_dir = DATA_DIR / "audio"
    meta_path = audio_dir / "metadata.json"
    if not meta_path.exists():
        return

    with meta_path.open() as f:
        meta = json.load(f)

    now = datetime.now(timezone.utc)
    kept: list[dict] = []
    removed = 0
    for rec in meta.get("recordings", []):
        expires_at_raw = rec.get("expires_at")
        if expires_at_raw is None:
            kept.append(rec)
            continue
        # ISO 8601 with trailing Z
        try:
            expires_at = datetime.fromisoformat(expires_at_raw.replace("Z", "+00:00"))
        except ValueError:
            kept.append(rec)
            continue
        if expires_at <= now:
            for ext in (".wav", ".mp3", ".json"):
                p = audio_dir / f"{rec['id']}{ext}"
                if p.exists():
                    p.unlink()
            removed += 1
        else:
            kept.append(rec)

    meta["recordings"] = kept
    tmp = meta_path.with_suffix(".json.tmp")
    with tmp.open("w") as f:
        json.dump(meta, f, indent=2)
    tmp.replace(meta_path)

    if removed:
        print(f"✅ {removed} enregistrement(s) supprimé(s)")


def main() -> None:
    parser = argparse.ArgumentParser(description="VoiceBridge CLI")
    parser.add_argument(
        "command",
        choices=["reset-password", "cleanup-expired", "regenerate-api-key"],
    )
    args = parser.parse_args()

    if args.command == "reset-password":
        reset_password()
    elif args.command == "cleanup-expired":
        cleanup_expired()
    elif args.command == "regenerate-api-key":
        regenerate_api_key()


if __name__ == "__main__":
    main()
