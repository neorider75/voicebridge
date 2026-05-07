"""Tests services/secrets.py — chiffrement Fernet + master key auto-bootstrap."""
from __future__ import annotations

import os
import stat

import pytest


def test_master_key_autobootstrap(isolated_data_dir):
    """Au premier appel, la master key doit être générée et persistée."""
    from app.services import secrets as sec
    key_path = isolated_data_dir / ".master_key"
    assert not key_path.exists()

    # Premier encrypt → déclenche la génération de la master key
    ct = sec.encrypt("hello")
    assert ct.startswith("gAAAAA")
    assert key_path.exists()

    # Permissions 400 (read-only par owner)
    mode = os.stat(key_path).st_mode & 0o777
    assert mode == 0o400, f"Master key permissions = {oct(mode)} (attendu 0o400)"

    # Master key non vide et de longueur Fernet (44 bytes URL-safe base64)
    raw = key_path.read_bytes()
    assert len(raw) == 44


def test_round_trip_encrypt_decrypt(isolated_data_dir):
    from app.services import secrets as sec
    plain = "sk-monApiKey-OpenAI-12345"
    ct = sec.encrypt(plain)
    assert ct != plain
    assert sec.decrypt(ct) == plain


def test_empty_string_passthrough(isolated_data_dir):
    """encrypt("") doit retourner "" sans appeler Fernet."""
    from app.services import secrets as sec
    assert sec.encrypt("") == ""
    assert sec.decrypt("") == ""


def test_unicode_round_trip(isolated_data_dir):
    from app.services import secrets as sec
    plain = "clé spécial éàü 中文 🔐"
    assert sec.decrypt(sec.encrypt(plain)) == plain


def test_decrypt_invalid_token_raises(isolated_data_dir):
    """Un ciphertext corrompu doit lever SecretsError clair."""
    from app.services import secrets as sec
    sec.encrypt("init")  # bootstrap master key
    with pytest.raises(sec.SecretsError):
        sec.decrypt("gAAAAA-corrupted-junk")


def test_is_encrypted_heuristic(isolated_data_dir):
    from app.services import secrets as sec
    sec.encrypt("init")  # bootstrap
    real = sec.encrypt("real")
    assert sec.is_encrypted(real) is True
    assert sec.is_encrypted("plaintext") is False
    assert sec.is_encrypted("") is False
    assert sec.is_encrypted("sk-OpenAI-pas-chiffrée") is False


def test_master_key_persistence_across_imports(isolated_data_dir, monkeypatch):
    """La master key persiste — un déchiffrement plus tard avec un nouveau
    Fernet doit fonctionner tant que .master_key n'a pas changé."""
    from app.services import secrets as sec
    ct = sec.encrypt("persistent-secret")

    # Force le reload du module → nouveau Fernet
    import importlib
    importlib.reload(sec)

    assert sec.decrypt(ct) == "persistent-secret"


def test_master_key_change_breaks_decryption(isolated_data_dir, monkeypatch):
    """Si la master key est régénérée, les anciens ciphertexts deviennent
    indéchiffrables → c'est exactement le comportement attendu (Décision 3).
    """
    from app.services import secrets as sec
    ct = sec.encrypt("doomed-secret")

    # Simule la perte du fichier master key
    (isolated_data_dir / ".master_key").unlink()

    # Reload → nouveau Fernet avec nouvelle master key
    import importlib
    importlib.reload(sec)
    sec.encrypt("dummy")  # déclenche bootstrap

    with pytest.raises(sec.SecretsError):
        sec.decrypt(ct)
