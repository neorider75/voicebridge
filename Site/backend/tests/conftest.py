"""Configuration pytest commune : isole VB_DATA_DIR + VB_MASTER_KEY_PATH
dans un dossier temporaire pour ne pas polluer /var/voicebridge/data/.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Force le backend à utiliser un dossier temporaire pour ses données."""
    data_dir = tmp_path / "vb-data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "voices").mkdir(exist_ok=True)
    (data_dir / "audio").mkdir(exist_ok=True)
    (data_dir / "models").mkdir(exist_ok=True)
    (data_dir / "tmp").mkdir(exist_ok=True)
    (data_dir / "logs").mkdir(exist_ok=True)

    # config.json minimal pour que config.load() ne plante pas
    cfg_path = data_dir / "config.json"
    cfg_path.write_text(json.dumps({
        "domain": "test.local",
        "session_secret": "x" * 64,
        "password_hash": "$2b$12$dummy",
        "api_token_hash": "abc",
        "api_token_created_at": "2026-01-01T00:00:00Z",
    }))

    monkeypatch.setenv("VB_DATA_DIR", str(data_dir))
    monkeypatch.setenv("VB_MASTER_KEY_PATH", str(data_dir / ".master_key"))

    # Reset des caches modules entre tests
    # Important : config a un cache global, on le bust pour que les tests
    # successifs soient isolés.
    repo_root = Path(__file__).resolve().parents[3]
    backend_root = repo_root / "Site" / "backend"
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))

    # Reload config avec le nouveau VB_DATA_DIR
    if "app.config" in sys.modules:
        import importlib
        import app.config as cfg
        importlib.reload(cfg)

    # Idem pour secrets (cache du Fernet)
    if "app.services.secrets" in sys.modules:
        import importlib
        import app.services.secrets as sec
        importlib.reload(sec)

    yield data_dir
