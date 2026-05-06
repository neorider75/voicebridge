"""Registre central des tâches async + helper de progression UX.

Pattern systématique V3 : toute opération > 1s expose une barre de
progression côté client via WebSocket ``/ws/progress/{task_id}``.

Format du payload émis :

.. code-block:: json

    {
      "task_id": "task_xxx",
      "status": "queued" | "running" | "done" | "error",
      "progress_percent": 0-100,
      "current_step": "Découpage en clips (3/6)",
      "elapsed_seconds": 23,
      "estimated_remaining_seconds": 120,
      "details": { ... },
      "result": { ... } | null,
      "error": "..." | null
    }

Usage côté backend (depuis une route) :

    from ..services import progress_tasks
    task_id = progress_tasks.create("rvc_upload")
    threading.Thread(
        target=lambda: long_blocking_work(progress_tasks.updater(task_id)),
        daemon=True,
    ).start()
    return {"task_id": task_id}

    # Dans long_blocking_work :
    def long_blocking_work(update):
        update(progress=10, step="Validation")
        # ...
        update(progress=80, step="Upload S3")
        # ...
        update(status="done", progress=100, result={"size_mb": 142})

Usage côté client : WebSocket vers ``/ws/progress/{task_id}``, lit les
messages JSON jusqu'à status in (done, error).
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Optional

from ..utils import files

log = logging.getLogger("voicebridge.progress")


_lock = threading.RLock()
_tasks: dict[str, dict[str, Any]] = {}

# TTL d'une tâche après "done"/"error" : on garde 5 min pour que le client
# ait le temps de récupérer le résultat même si sa connexion WS a coupé.
DONE_TTL_SECONDS = 300


# ────────────────────────────────────────────────────────────────────
# API publique
# ────────────────────────────────────────────────────────────────────


def create(kind: str = "task", details: Optional[dict] = None) -> str:
    """Crée une nouvelle tâche en état ``queued``. Retourne son ``task_id``."""
    task_id = files.new_id("task_")
    now = time.time()
    with _lock:
        _gc_old()
        _tasks[task_id] = {
            "task_id": task_id,
            "kind": kind,
            "status": "queued",
            "progress_percent": 0,
            "current_step": "En attente",
            "started_at": now,
            "updated_at": now,
            "details": details or {},
            "result": None,
            "error": None,
        }
    log.info("progress.create kind=%s task_id=%s", kind, task_id)
    return task_id


def get(task_id: str) -> dict | None:
    """Lit l'état courant d'une tâche (snapshot copie)."""
    with _lock:
        task = _tasks.get(task_id)
        return dict(task) if task else None


def updater(task_id: str) -> Callable[..., None]:
    """Retourne une closure ``update(**kwargs)`` qui met à jour la tâche.

    Pratique pour passer un seul callable au worker thread.
    """
    def _update(**kwargs):
        update(task_id, **kwargs)
    return _update


def update(task_id: str, *,
           status: Optional[str] = None,
           progress: Optional[float] = None,
           step: Optional[str] = None,
           details: Optional[dict] = None,
           result: Optional[dict] = None,
           error: Optional[str] = None,
           eta_seconds: Optional[int] = None) -> None:
    """Met à jour les champs d'une tâche existante."""
    with _lock:
        task = _tasks.get(task_id)
        if not task:
            log.warning("progress.update task absent: %s", task_id)
            return
        if status is not None:
            task["status"] = status
        if progress is not None:
            task["progress_percent"] = max(0, min(100, int(progress)))
        if step is not None:
            task["current_step"] = step
        if details is not None:
            task["details"].update(details)
        if result is not None:
            task["result"] = result
        if error is not None:
            task["error"] = error
            task["status"] = "error"
        if eta_seconds is not None:
            task["estimated_remaining_seconds"] = max(0, int(eta_seconds))
        task["updated_at"] = time.time()
        # Auto-mark done at 100%
        if task["progress_percent"] >= 100 and task["status"] == "running":
            task["status"] = "done"


def snapshot(task_id: str) -> dict | None:
    """Snapshot enrichi avec elapsed_seconds calculé à la lecture."""
    task = get(task_id)
    if not task:
        return None
    task["elapsed_seconds"] = int(time.time() - task["started_at"])
    return task


def list_active() -> list[dict]:
    """Liste les tâches en queued/running pour debug/monitoring."""
    with _lock:
        return [dict(t) for t in _tasks.values()
                if t["status"] in ("queued", "running")]


def _gc_old() -> None:
    """Garbage-collect : supprime les tâches finies depuis > DONE_TTL_SECONDS."""
    now = time.time()
    to_remove = [
        tid for tid, task in _tasks.items()
        if task["status"] in ("done", "error")
        and (now - task["updated_at"]) > DONE_TTL_SECONDS
    ]
    for tid in to_remove:
        del _tasks[tid]
    if to_remove:
        log.debug("progress.gc removed %d tasks", len(to_remove))
