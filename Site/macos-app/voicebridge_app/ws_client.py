"""Client WebSocket asynchrone pour l'app macOS.

Tourne dans un thread dédié avec sa propre boucle asyncio. Envoie les
chunks audio capturés (binary frames) au serveur ``/ws/stream`` et joue
les chunks audio retours via le pipeline audio.

Reconnexion auto avec backoff exponentiel (1, 2, 5, 10, 20 s, plafonné).
"""
from __future__ import annotations

import asyncio
import base64
import logging
import threading
import json

try:
    import websockets  # type: ignore
    WS_OK = True
except ImportError:
    websockets = None  # type: ignore
    WS_OK = False

log = logging.getLogger("voicebridge.ws")


class WSClient:
    def __init__(self, server_url: str, api_token: str, audio_pipeline,
                 voice_id: str, language: str = "fr",
                 on_state_change=None) -> None:
        self.server_url = server_url
        self.api_token = api_token
        self.audio = audio_pipeline
        self.voice_id = voice_id
        self.language = language
        self.on_state_change = on_state_change or (lambda *a, **kw: None)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ws = None  # référence courante pour broadcast send

    # ── Public ───────────────────────────────────────────────────────

    def start(self) -> None:
        if not WS_OK:
            log.warning("websockets non disponible — client désactivé")
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._loop:
            self._loop.call_soon_threadsafe(lambda: None)
        if self._thread:
            self._thread.join(timeout=3)

    def push_audio(self, raw_bytes: bytes) -> None:
        """Appelé par AudioPipeline (callback). Envoie le chunk en binary frame."""
        ws = self._ws
        loop = self._loop
        if not ws or not loop:
            return
        try:
            asyncio.run_coroutine_threadsafe(ws.send(raw_bytes), loop)
        except Exception:  # noqa: BLE001
            pass

    def set_voice(self, voice_id: str, language: str) -> None:
        self.voice_id = voice_id
        self.language = language
        ws = self._ws
        loop = self._loop
        if ws and loop:
            payload = {"type": "configure", "voice_id": voice_id,
                       "language": language, "output": "blackhole"}
            asyncio.run_coroutine_threadsafe(ws.send(json.dumps(payload)), loop)

    # ── Internal ────────────────────────────────────────────────────

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect_loop())
        finally:
            self._loop.close()

    async def _connect_loop(self) -> None:
        delays = [1, 2, 5, 10, 20]
        attempt = 0
        while not self._stop.is_set():
            ws_url = self._ws_url()
            try:
                self.on_state_change("connecting")
                async with websockets.connect(
                    ws_url,
                    additional_headers={"Authorization": f"Bearer {self.api_token}"},
                    open_timeout=10,
                    ping_interval=30,
                    ping_timeout=15,
                ) as ws:
                    self._ws = ws
                    attempt = 0
                    self.on_state_change("connected")
                    # Configure
                    await ws.send(json.dumps({
                        "type": "configure",
                        "voice_id": self.voice_id,
                        "language": self.language,
                        "output": "blackhole",
                    }))
                    async for msg in ws:
                        await self._handle_message(msg)
            except Exception as exc:  # noqa: BLE001
                log.warning("WS connect/loop error: %s", exc)
                self.on_state_change("disconnected")
                self._ws = None
                if self._stop.is_set():
                    break
                d = delays[min(attempt, len(delays) - 1)]
                attempt += 1
                await asyncio.sleep(d)

    def _ws_url(self) -> str:
        url = self.server_url
        if url.startswith("https://"):
            return "wss://" + url[len("https://"):] + "/ws/stream"
        if url.startswith("http://"):
            return "ws://" + url[len("http://"):] + "/ws/stream"
        return url

    async def _handle_message(self, msg) -> None:
        # Texte JSON ou binaire selon le serveur
        if isinstance(msg, (bytes, bytearray)):
            self.audio.play_response(bytes(msg))
            return
        try:
            payload = json.loads(msg)
        except json.JSONDecodeError:
            return
        ptype = payload.get("type")
        if ptype == "audio_pcm":
            # Streaming Live : chunks PCM 16-bit mono 24 kHz, à pousser tels
            # quels dans le RawOutputStream BlackHole (qui est configuré au
            # même format).
            data = payload.get("data") or ""
            try:
                raw = base64.b64decode(data)
            except Exception:  # noqa: BLE001
                return
            self.audio.play_response(raw)
        elif ptype == "audio_end":
            # Fin de la phrase synthétisée — rien à faire de spécial côté
            # PyAudio (la queue se draine d'elle-même).
            pass
        elif ptype == "audio_chunk":
            # Rétro-compat : ancien format WAV complet b64. On saute le header
            # RIFF (44 octets) pour ne pousser que le PCM dans BlackHole.
            data = payload.get("data") or ""
            try:
                raw = base64.b64decode(data)
            except Exception:  # noqa: BLE001
                return
            if raw[:4] == b"RIFF" and raw[8:12] == b"WAVE":
                idx = raw.find(b"data")
                if idx > 0 and idx + 8 < len(raw):
                    raw = raw[idx + 8:]
            self.audio.play_response(raw)
        elif ptype == "transcript":
            # Texte transcrit — purement informatif côté app macOS.
            pass
        elif ptype == "ready":
            self.on_state_change("ready")
        elif ptype == "error":
            log.warning("server error: %s", payload.get("message"))
            self.on_state_change("error", payload.get("message"))
        elif ptype == "state_update":
            voice = payload.get("active_voice")
            if voice:
                self.voice_id = voice
                self.on_state_change("voice_changed", voice)
