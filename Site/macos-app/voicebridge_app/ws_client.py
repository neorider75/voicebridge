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
                 mode: str = "cpu-fr-en",
                 translation_provider: str = "opus-mt-cpu",
                 target_lang: str | None = None,
                 rvc_model_id: str | None = None,
                 on_state_change=None,
                 on_cost_update=None,
                 on_transcript=None,
                 on_translated=None,
                 on_server_error=None) -> None:
        self.server_url = server_url
        self.api_token = api_token
        self.audio = audio_pipeline
        self.voice_id = voice_id
        self.language = language
        # V3 (Phase H)
        self.mode = mode
        self.translation_provider = translation_provider
        self.target_lang = target_lang or language
        self.rvc_model_id = rvc_model_id
        self.on_state_change = on_state_change or (lambda *a, **kw: None)
        self.on_cost_update = on_cost_update or (lambda *a, **kw: None)
        # Callbacks pour le panneau Live (texte transcrit + traduit)
        self.on_transcript = on_transcript or (lambda *a, **kw: None)
        self.on_translated = on_translated or (lambda *a, **kw: None)
        self.on_server_error = on_server_error or (lambda *a, **kw: None)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ws = None  # référence courante pour broadcast send
        # Flag levé sur réception "ready" du serveur (configure accepté).
        # Tant qu'il est False, push_audio() drop les frames pour ne pas
        # spammer le serveur de chunks rejetés "envoyez d'abord un configure".
        self._ready = False

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
        """Appelé par AudioPipeline (callback). Envoie le chunk en binary frame.

        Drop silencieusement si la WS n'est pas connectée OU si le serveur
        n'a pas encore acquitté le configure (state "ready"). Évite la
        boucle de spam "envoyez d'abord un message configure" quand le
        voice_id mémorisé n'existe pas côté serveur.
        """
        ws = self._ws
        loop = self._loop
        if not ws or not loop or not self._ready:
            return
        try:
            asyncio.run_coroutine_threadsafe(ws.send(raw_bytes), loop)
        except Exception:  # noqa: BLE001
            pass

    def set_voice(self, voice_id: str, language: str) -> None:
        self.voice_id = voice_id
        self.language = language
        self._send_reconfigure()

    def set_mode(self, mode: str, translation_provider: str | None = None,
                 target_lang: str | None = None,
                 rvc_model_id: str | None = None) -> None:
        """V3 : change le mode Live (cpu-fr-en / gpu-clone / gpu-native /
        gpu-hybrid) et les paramètres associés. Renvoie un configure au serveur."""
        self.mode = mode
        if translation_provider is not None:
            self.translation_provider = translation_provider
        if target_lang is not None:
            self.target_lang = target_lang
        if rvc_model_id is not None:
            self.rvc_model_id = rvc_model_id
        self._send_reconfigure()

    def _build_configure(self) -> dict:
        # `translate` est dérivé de la divergence target_lang / language :
        # le backend traite translate=False quand target == language de toute
        # façon, mais on l'envoie explicitement pour aligner sur le frontend
        # web (parser strict côté backend).
        translate = bool(self.target_lang and self.target_lang != self.language)
        return {
            "type": "configure",
            "voice_id": self.voice_id,
            "language": self.language,
            "output": "blackhole",
            # V1 rétrocompat
            "translate": translate,
            "translate_to": self.target_lang,
            # V3
            "mode": self.mode,
            "translation_provider": self.translation_provider,
            "target_lang": self.target_lang,
            "rvc_model_id": self.rvc_model_id,
        }

    def _send_reconfigure(self) -> None:
        ws = self._ws
        loop = self._loop
        if ws and loop:
            # Bloque les push audio jusqu'à reception d'un nouveau "ready"
            # (le serveur revalide tout le payload sur chaque configure).
            self._ready = False
            try:
                asyncio.run_coroutine_threadsafe(
                    ws.send(json.dumps(self._build_configure())), loop)
            except Exception:  # noqa: BLE001
                pass

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
                    open_timeout=20,    # +10s pour cold-start serveur derrière Cloudflare/Nginx
                    ping_interval=60,   # +30s — keepalive moins agressif (l'app est souvent
                                         #        idle pendant des minutes entre 2 phrases)
                    ping_timeout=30,    # +15s tolérance pour pongs
                ) as ws:
                    self._ws = ws
                    self._ready = False  # ré-armé à chaque (re)connexion
                    attempt = 0
                    self.on_state_change("connected")
                    # Configure (payload complet V3 : mode + provider + RVC)
                    await ws.send(json.dumps(self._build_configure()))
                    async for msg in ws:
                        await self._handle_message(msg)
            except Exception as exc:  # noqa: BLE001
                log.warning("WS connect/loop error: %s", exc)
                self.on_state_change("disconnected")
                self._ws = None
                self._ready = False
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
            seq = payload.get("seq", 0)
            sr = payload.get("sample_rate", 24000)
            # Log diagnostic : confirmer la réception des chunks audio.
            # 1er chunk + tous les 10 pour ne pas spammer.
            if seq == 0 or seq % 10 == 0:
                log.info("recv audio_pcm seq=%d sr=%d bytes=%d (b64=%d)",
                         seq, sr, len(raw), len(data))
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
            text = payload.get("text", "")
            log.info("transcript: %r", text[:80])
            try:
                self.on_transcript(text)
            except Exception:  # noqa: BLE001
                pass
        elif ptype == "translated":
            text = payload.get("text", "")
            tgt = payload.get("tgt_lang") or payload.get("target_lang") or ""
            log.info("translated [%s]: %r", tgt, text[:80])
            try:
                self.on_translated(text, tgt)
            except Exception:  # noqa: BLE001
                pass
        elif ptype == "cost_update":
            # V3 : coût session en mode GPU — remonté à l'app pour affichage
            # dans le menu bar.
            cost_eur = float(payload.get("session_cost_eur", 0) or 0)
            duration_s = int(payload.get("duration_seconds", 0) or 0)
            log.info("cost: %.4f€ (durée %ds, GPU=%s, GPT=%s)",
                     cost_eur, duration_s,
                     payload.get("provider_breakdown", {}).get("runpod_gpu", 0),
                     payload.get("provider_breakdown", {}).get("openai", 0))
            try:
                self.on_cost_update(cost_eur, duration_s)
            except Exception:  # noqa: BLE001
                pass
        elif ptype == "ready":
            self._ready = True
            self.on_state_change("ready")
        elif ptype == "error":
            # En cas d'erreur côté serveur, on bloque les push audio tant
            # qu'on n'a pas reçu un nouveau "ready". L'app pourra réémettre
            # un configure avec une voix valide.
            self._ready = False
            msg = payload.get("message", "")
            log.warning("server error: %s", msg)
            self.on_state_change("error", msg)
            try:
                self.on_server_error(msg)
            except Exception:  # noqa: BLE001
                pass
        elif ptype == "state_update":
            voice = payload.get("active_voice")
            if voice:
                self.voice_id = voice
                self.on_state_change("voice_changed", voice)
