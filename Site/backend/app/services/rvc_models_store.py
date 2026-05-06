"""Persistance des modèles RVC utilisateur — ``data/rvc_models/metadata.json``.

Format ``metadata.json`` :

.. code-block:: json

    {
      "models": [
        {
          "id": "rvc_xxx",
          "name": "JC voice v1",
          "description": "Entraîné sur 18 min d'audio le 26/04/2026",
          "voice_id": "v_jc_fr",
          "sample_rate": 40000,
          "f0": true,
          "version": "v2",
          "size_mb": 142,
          "created_at": "...",
          "trained_on_kaggle_at": null,
          "status": "validating" | "uploading" | "active" | "failed",
          "error_message": null,
          "runpod_volume_path": "rvc_models/rvc_xxx/",
          "test_audio_path": null
        }
      ]
    }

Cf. Décision 1 du doc 00-decisions-v3.md : les .pth sont stockés sur le
RunPod Network Volume via API S3 (boto3). Hostinger ne garde QUE la
metadata + un éventuel fichier de test audio.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import config
from ..utils import files

log = logging.getLogger("voicebridge.rvc_store")

_lock = threading.RLock()

MAX_PTH_BYTES = 500 * 1024 * 1024     # 500 Mo
MAX_INDEX_BYTES = 200 * 1024 * 1024   # 200 Mo


# ────────────────────────────────────────────────────────────────────
# Paths
# ────────────────────────────────────────────────────────────────────


def models_dir() -> Path:
    return config.DATA_DIR / "rvc_models"


def _meta_path() -> Path:
    return models_dir() / "metadata.json"


def model_dir(model_id: str) -> Path:
    return models_dir() / files.safe_id(model_id)


def test_audio_path(model_id: str) -> Path:
    return model_dir(model_id) / "sample_test.wav"


def runpod_pth_key(model_id: str) -> str:
    """Chemin distant dans le Volume RunPod : ``rvc_models/{id}/model.pth``."""
    return f"rvc_models/{files.safe_id(model_id)}/model.pth"


def runpod_index_key(model_id: str) -> str:
    return f"rvc_models/{files.safe_id(model_id)}/added.index"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ────────────────────────────────────────────────────────────────────
# Persistance JSON
# ────────────────────────────────────────────────────────────────────


def _load() -> dict[str, Any]:
    p = _meta_path()
    if not p.exists():
        return {"models": []}
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return {"models": []}


def _save(data: dict[str, Any]) -> None:
    p = _meta_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(p)


# ────────────────────────────────────────────────────────────────────
# CRUD
# ────────────────────────────────────────────────────────────────────


def list_models() -> list[dict]:
    with _lock:
        data = _load()
        items = list(data.get("models", []))
    items.sort(key=lambda m: m.get("created_at", ""), reverse=True)
    return items


def get(model_id: str) -> dict | None:
    mid = files.safe_id(model_id)
    with _lock:
        for m in _load().get("models", []):
            if m.get("id") == mid:
                return m
    return None


def add(meta: dict) -> dict:
    """Insère une nouvelle metadata. ``id`` doit être déjà présent."""
    if "id" not in meta:
        raise ValueError("meta.id requis")
    meta.setdefault("created_at", _now_iso())
    meta.setdefault("status", "validating")
    with _lock:
        data = _load()
        # Replace si existe déjà
        data["models"] = [m for m in data.get("models", [])
                          if m.get("id") != meta["id"]]
        data["models"].append(meta)
        _save(data)
    return meta


def patch(model_id: str, updates: dict) -> dict | None:
    """Met à jour les champs d'un modèle. Retourne None si non trouvé."""
    mid = files.safe_id(model_id)
    with _lock:
        data = _load()
        for m in data.get("models", []):
            if m.get("id") == mid:
                m.update(updates)
                _save(data)
                return m
    return None


def delete(model_id: str) -> bool:
    """Supprime la metadata locale. Le fichier RunPod doit être nettoyé séparément."""
    mid = files.safe_id(model_id)
    with _lock:
        data = _load()
        before = len(data.get("models", []))
        data["models"] = [m for m in data.get("models", [])
                          if m.get("id") != mid]
        if len(data["models"]) == before:
            return False
        _save(data)
    return True


# ────────────────────────────────────────────────────────────────────
# Validation .pth
# ────────────────────────────────────────────────────────────────────


def validate_pth_file(path: Path) -> dict:
    """Vérifie qu'un fichier est bien un .pth RVC valide.

    Vérifications :
    - Taille raisonnable (max 500 Mo)
    - PyTorch checkpoint chargeable
    - Présence des keys attendues (weight, config)
    - Sample rate plausible (32k, 40k, 48k)

    Raises:
        ValueError: si le fichier n'est pas valide.

    Returns:
        ``{"valid": True, "sample_rate": int, "version": str, "f0": bool}``
    """
    if not path.exists():
        raise ValueError(f"Fichier introuvable : {path}")

    size = path.stat().st_size
    if size > MAX_PTH_BYTES:
        raise ValueError(f".pth trop gros ({size / 1e6:.1f} Mo, max "
                         f"{MAX_PTH_BYTES / 1e6:.0f} Mo)")
    if size < 1024:
        raise ValueError(f".pth trop petit ({size} bytes)")

    # Magic bytes : PyTorch zip-based (PK) ou pickle legacy (\x80)
    head = path.read_bytes()[:4]
    if head[:2] != b"PK" and head[:1] != b"\x80":
        raise ValueError("Magic bytes incorrects (pas un fichier PyTorch valide)")

    try:
        import torch  # type: ignore
    except ImportError as exc:
        raise ValueError(f"torch non disponible : {exc}") from exc

    try:
        ckpt = torch.load(path, map_location="cpu", weights_only=False)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Chargement PyTorch échoué : {exc}") from exc

    if not isinstance(ckpt, dict):
        raise ValueError("Le checkpoint n'est pas un dict (format RVC inattendu)")

    if "weight" not in ckpt:
        raise ValueError("Clé 'weight' manquante (pas un .pth RVC)")

    cfg = ckpt.get("config")
    sample_rate = 40000  # défaut RVC v2
    if isinstance(cfg, list) and len(cfg) >= 16:
        sr_candidate = cfg[15]
        if isinstance(sr_candidate, int) and sr_candidate > 0:
            sample_rate = sr_candidate

    if sample_rate not in (32000, 40000, 48000):
        log.warning("sample_rate inhabituel : %d", sample_rate)

    return {
        "valid": True,
        "sample_rate": sample_rate,
        "version": str(ckpt.get("version", "unknown")),
        "f0": bool(ckpt.get("f0", True)),
    }


def validate_index_file(path: Path) -> dict:
    """Validation légère du .index FAISS.

    On ne le charge pas (lourd, dépend de faiss-cpu) — on vérifie juste
    la taille et le magic byte FAISS (``IxFI`` ou similaire).
    """
    if not path.exists():
        raise ValueError(f"Fichier introuvable : {path}")
    size = path.stat().st_size
    if size > MAX_INDEX_BYTES:
        raise ValueError(f".index trop gros ({size / 1e6:.1f} Mo, max "
                         f"{MAX_INDEX_BYTES / 1e6:.0f} Mo)")
    if size < 100:
        raise ValueError(f".index trop petit ({size} bytes)")
    return {"valid": True, "size_bytes": size}
