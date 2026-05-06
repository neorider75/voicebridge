"""Route ``/ws/progress/{task_id}`` — barre de progression universelle.

Pattern systématique V3 (cf. doc 16-progress-ux-pattern.md) : toute
opération > 1s côté backend expose son état via cette route. Le frontend
se connecte au WebSocket avec le ``task_id`` retourné par la route qui a
créé la tâche, et reçoit des updates JSON tant que ``status`` n'est pas
``done`` ou ``error``.

Format des messages émis (cf. ``services/progress_tasks.snapshot()``) :

.. code-block:: json

    {
      "task_id": "task_xxx",
      "status": "queued|running|done|error",
      "progress_percent": 0-100,
      "current_step": "Découpage en clips (3/6)",
      "elapsed_seconds": 23,
      "estimated_remaining_seconds": 120,
      "details": {...},
      "result": {...} | null,
      "error": "..." | null
    }

Throttle : 1 message toutes les 500 ms tant que running.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import auth as auth_mod
from ..services import progress_tasks

router = APIRouter(tags=["progress"])
log = logging.getLogger("voicebridge.progress_route")

POLL_INTERVAL_S = 0.5
MAX_DURATION_S = 1800   # 30 min max — sécurité contre tâches zombies


def _ws_authenticated(ws: WebSocket) -> bool:
    return auth_mod._has_valid_session(ws) or auth_mod._has_valid_bearer(ws)


@router.websocket("/ws/progress/{task_id}")
async def progress_ws(ws: WebSocket, task_id: str):
    if not _ws_authenticated(ws):
        await ws.close(code=4401)
        return
    await ws.accept()

    elapsed = 0.0
    last_seen_status = None
    try:
        while elapsed < MAX_DURATION_S:
            snapshot = progress_tasks.snapshot(task_id)
            if not snapshot:
                await ws.send_json({"task_id": task_id, "status": "not_found"})
                break

            await ws.send_json(snapshot)
            last_seen_status = snapshot["status"]

            if last_seen_status in ("done", "error"):
                break

            await asyncio.sleep(POLL_INTERVAL_S)
            elapsed += POLL_INTERVAL_S
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        log.exception("WebSocket progress crashed task_id=%s", task_id)
        try:
            await ws.send_json({"task_id": task_id, "status": "error",
                                "error": str(exc)})
        except Exception:  # noqa: BLE001
            pass
    finally:
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
