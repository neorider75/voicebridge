"""Chiffrement symétrique des secrets (clés API tierces).

Utilise Fernet (``cryptography``) avec une **master key dédiée** stockée dans
``$VB_DATA_DIR/.master_key`` (chmod 400, propriétaire ``voicebridge``).

Cf. Décision 3 du document ``00-decisions-v3.md`` : master key indépendante
du password utilisateur (un changement de password n'invalide PAS les secrets
chiffrés). Pattern Linux standard (``/etc/shadow``, ``ssh_host_*_key``,
``.pgpass``).

Usage typique :

    from ..services import secrets
    encrypted = secrets.encrypt("sk-monApiKey")
    config.set_many({"openai_api_key_encrypted": encrypted})
    # ... plus tard
    plain = secrets.decrypt(config.get("openai_api_key_encrypted"))
"""
from __future__ import annotations

import logging
import os
import stat
import threading
from pathlib import Path

from .. import config

log = logging.getLogger("voicebridge.secrets")

# Override possible via env (utile pour tests / dev local non-root)
MASTER_KEY_PATH = Path(os.environ.get(
    "VB_MASTER_KEY_PATH",
    str(config.DATA_DIR / ".master_key"),
))

_lock = threading.RLock()
_fernet = None


class SecretsError(Exception):
    """Erreur générique pour les opérations de chiffrement/déchiffrement."""


def _load_or_create_key() -> bytes:
    """Charge la master key depuis le disque, ou la génère au premier appel.

    Permissions enforced à chmod 400 dès la création.
    """
    try:
        from cryptography.fernet import Fernet  # type: ignore
    except ImportError as exc:
        raise SecretsError(
            f"cryptography non installé : {exc}. "
            f"pip install 'cryptography>=42'"
        ) from exc

    if MASTER_KEY_PATH.exists():
        key = MASTER_KEY_PATH.read_bytes().strip()
        if not key:
            raise SecretsError(f"Master key vide : {MASTER_KEY_PATH}")
        log.debug("Master key chargée depuis %s", MASTER_KEY_PATH)
        return key

    # Premier boot : génère et persiste
    log.warning("Master key absente, génération automatique : %s", MASTER_KEY_PATH)
    MASTER_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    # Écriture atomique pour éviter une race condition au boot
    tmp = MASTER_KEY_PATH.with_suffix(".tmp")
    tmp.write_bytes(key)
    os.chmod(tmp, stat.S_IRUSR)  # 400
    tmp.replace(MASTER_KEY_PATH)
    log.warning(
        "⚠️  Master key générée. NE PAS PERDRE ce fichier — toutes les "
        "clés API chiffrées deviendraient illisibles. À sauvegarder avec "
        "data/config.json."
    )
    return key


def _get_fernet():
    """Singleton Fernet pour éviter de relire la clé à chaque appel."""
    global _fernet
    with _lock:
        if _fernet is None:
            from cryptography.fernet import Fernet  # type: ignore
            _fernet = Fernet(_load_or_create_key())
        return _fernet


def encrypt(plaintext: str) -> str:
    """Chiffre une chaîne et retourne le ciphertext base64 (str)."""
    if not plaintext:
        return ""
    f = _get_fernet()
    return f.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str) -> str:
    """Déchiffre une chaîne chiffrée. Retourne "" si l'entrée est vide.

    Raises:
        SecretsError: si la clé est invalide, le ciphertext corrompu, ou
            si la master key actuelle ne peut pas déchiffrer (ex: master
            key régénérée alors que le ciphertext date de l'ancienne).
    """
    if not ciphertext:
        return ""
    try:
        from cryptography.fernet import InvalidToken  # type: ignore
        f = _get_fernet()
        return f.decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise SecretsError(
            "Token Fernet invalide. La master key a peut-être été régénérée "
            "ou le ciphertext est corrompu. Saisissez à nouveau les clés API "
            "depuis Settings."
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise SecretsError(f"Échec déchiffrement : {exc}") from exc


def is_encrypted(value: str) -> bool:
    """Heuristique pour détecter si une chaîne est déjà un ciphertext Fernet.

    Utile pour migrations / reconfig : on évite de re-chiffrer un ciphertext.
    Fernet tokens commencent par "gAAAAA" (base64 de "\\x80\\x00\\x00\\x00\\x00").
    """
    return bool(value) and value.startswith("gAAAAA")
