"""WebSocket ``/ws/stream`` Live — STUB (livraison 4).

Pipeline cible : VAD Silero (silence > 400 ms ou chunk > 4 s) → buffer
circulaire 5 s → Kyutai STT → NeuTTS Q4 (ref_codes pré-encodés) → NeuCodec
→ watermark Perth → client.
"""
from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import auth as auth_mod

router = APIRouter(tags=["live"])


@router.websocket("/ws/stream")
async def stream(ws: WebSocket):
    # Auth via cookie session OU Bearer token (header lors du handshake).
    if not (auth_mod._has_valid_session(ws) or auth_mod._has_valid_bearer(ws)):  # type: ignore[arg-type]
        await ws.close(code=4401)
        return
    await ws.accept()
    try:
        await ws.send_json({"type": "ready", "note": "Live disponible en livraison 4"})
        while True:
            await ws.receive_text()  # placeholder : ignore les chunks audio
    except WebSocketDisconnect:
        pass
