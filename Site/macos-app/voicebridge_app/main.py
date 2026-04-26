"""Entry point de l'app menu bar VoiceBridge.

Lancement :
    python -m voicebridge_app                       # dev
    /Applications/VoiceBridge.app/Contents/...      # production (PyInstaller)

Architecture :
- Main thread : rumps (boucle Cocoa)
- Thread audio : pyaudio capture/output
- Thread WebSocket : asyncio + websockets

L'app est sans dock (Info.plist LSUIElement: true), uniquement menu bar.
"""
from __future__ import annotations

import json
import logging
import sys
from urllib.request import Request, urlopen

import rumps  # type: ignore

from . import __version__
from . import audio as audio_mod
from . import config as cfg
from .ws_client import WSClient

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("voicebridge.app")

ICON_CONNECTED = "🟢"
ICON_PAUSED = "🟡"
ICON_DISCONNECTED = "🔴"


class VoiceBridgeApp(rumps.App):
    def __init__(self) -> None:
        super().__init__("VoiceBridge", title="🔴 VoiceBridge", quit_button=None)
        self.bundle_cfg = cfg.load_bundle_config()
        self.server_url = cfg.kr_get("server_url") or self.bundle_cfg.get("server_url", "")
        self.api_token = cfg.kr_get("api_token") or ""
        self.voice_id = cfg.kr_get("default_voice") or "juliette"
        self.language = "fr"

        self.audio = audio_mod.AudioPipeline(on_chunk_captured=self._on_capture)
        self.ws: WSClient | None = None

        # ── Menu items ──
        self.voice_item = rumps.MenuItem(f"Voix : {self.voice_id}")
        self.pause_item = rumps.MenuItem("⏸ Mettre en pause", callback=self._toggle_pause)
        self.preferences_item = rumps.MenuItem("Préférences…", callback=self._open_preferences)
        self.quit_item = rumps.MenuItem("⏹ Quitter", callback=self._on_quit)

        self.menu = [
            self.voice_item,
            None,
            self.pause_item,
            None,
            self.preferences_item,
            None,
            self.quit_item,
        ]

        # Au démarrage : si pas de token → ouvrir préférences. Sinon → connecter.
        if not self.api_token:
            rumps.alert("Bienvenue !", "Configurez la clé API dans les Préférences pour démarrer.")
            self._open_preferences(None)
        else:
            self._start_pipeline()

    # ── Callbacks UI ────────────────────────────────────────────────

    def _toggle_pause(self, sender: rumps.MenuItem) -> None:
        if self.audio.is_paused():
            self.audio.pause(False)
            sender.title = "⏸ Mettre en pause"
            self._set_status(ICON_CONNECTED)
        else:
            self.audio.pause(True)
            sender.title = "▶ Reprendre"
            self._set_status(ICON_PAUSED)

    def _open_preferences(self, _sender) -> None:
        # Préférences minimales : URL + token (rumps.Window pour chaque champ)
        win = rumps.Window(
            title="Préférences VoiceBridge",
            message=f"URL serveur (actuelle : {self.server_url or '(non configurée)'})",
            default_text=self.server_url, dimensions=(400, 24), ok="Suivant", cancel="Annuler",
        )
        r1 = win.run()
        if not r1.clicked:
            return
        self.server_url = r1.text.strip() or self.server_url
        cfg.kr_set("server_url", self.server_url)

        win = rumps.Window(
            title="Préférences VoiceBridge",
            message="Clé API (Bearer token, ex: sk-…). Trouvée dans Réglages → API du panel web.",
            default_text=self.api_token, dimensions=(400, 24), secure=True,
            ok="Tester + enregistrer", cancel="Annuler",
        )
        r2 = win.run()
        if not r2.clicked:
            return
        self.api_token = r2.text.strip()
        cfg.kr_set("api_token", self.api_token)

        if self._test_connection():
            rumps.alert("Connexion OK", "La clé est valide. Le pipeline démarre.")
            self._start_pipeline()
        else:
            rumps.alert("Connexion impossible",
                        "Vérifiez l'URL et la clé API. L'app reste en mode déconnecté.")
            self._set_status(ICON_DISCONNECTED)

    def _test_connection(self) -> bool:
        try:
            url = self.server_url.rstrip("/") + "/api/auth/check"
            req = Request(url, headers={"Authorization": f"Bearer {self.api_token}"})
            with urlopen(req, timeout=5) as r:
                data = json.loads(r.read().decode("utf-8"))
                return bool(data.get("authenticated"))
        except Exception as exc:  # noqa: BLE001
            log.warning("connection test failed: %s", exc)
            return False

    def _on_quit(self, _sender) -> None:
        # Bypass 2 s avant de quitter pour éviter une coupure brutale
        self.audio.pause(True)
        rumps.quit_application()

    # ── Pipeline ────────────────────────────────────────────────────

    def _start_pipeline(self) -> None:
        if not self.server_url or not self.api_token:
            self._set_status(ICON_DISCONNECTED)
            return
        bh = audio_mod.find_blackhole_index()
        if bh is None:
            rumps.alert("BlackHole introuvable",
                        "Téléchargez et installez BlackHole 2ch (existential.audio/blackhole) "
                        "puis relancez l'app.")
            return
        self.audio.start(input_idx=None, output_idx=bh)

        self.ws = WSClient(
            server_url=self.server_url,
            api_token=self.api_token,
            audio_pipeline=self.audio,
            voice_id=self.voice_id,
            language=self.language,
            on_state_change=self._on_ws_state,
        )
        self.ws.start()

    def _on_capture(self, raw_bytes: bytes) -> None:
        if self.ws:
            self.ws.push_audio(raw_bytes)

    def _on_ws_state(self, state: str, *args) -> None:
        if state in ("connected", "ready"):
            self._set_status(ICON_PAUSED if self.audio.is_paused() else ICON_CONNECTED)
        elif state in ("disconnected", "error", "connecting"):
            self._set_status(ICON_DISCONNECTED)
        elif state == "voice_changed":
            self.voice_id = args[0] if args else self.voice_id
            self.voice_item.title = f"Voix : {self.voice_id}"

    def _set_status(self, icon: str) -> None:
        self.title = f"{icon} VoiceBridge"


def main() -> None:
    VoiceBridgeApp().run()


if __name__ == "__main__":
    main()
