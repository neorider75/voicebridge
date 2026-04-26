"""Helpers filesystem : path safety, IDs, métadonnées audio."""
from __future__ import annotations

import re
import uuid
from pathlib import Path

ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def safe_id(value: str) -> str:
    """Valide un identifiant (whitelist), sinon ``ValueError``.

    Bloque ``..``, ``/``, espaces, etc. — pour sécuriser tout chemin construit
    à partir d'un input utilisateur.
    """
    if not value or not ID_RE.match(value):
        raise ValueError(f"identifiant invalide: {value!r}")
    return value


def new_id(prefix: str = "") -> str:
    """Identifiant interne court (UUID4 hex tronqué)."""
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def ensure_inside(parent: Path, candidate: Path) -> Path:
    """Garantit que ``candidate`` est sous ``parent`` après ``resolve()``.

    Retourne le chemin absolu résolu, lève ``ValueError`` sinon (path traversal).
    """
    parent_resolved = parent.resolve()
    candidate_resolved = candidate.resolve()
    try:
        candidate_resolved.relative_to(parent_resolved)
    except ValueError as exc:
        raise ValueError("path traversal détecté") from exc
    return candidate_resolved
