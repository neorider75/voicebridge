"""Entry point de l'app menu bar VoiceBridge.

Lancement :
    python -m voicebridge_app                       # dev
    /Applications/VoiceBridge.app/Contents/...      # production (PyInstaller)

Architecture :
- Main thread : rumps (boucle Cocoa)
- Thread audio : pyaudio capture/output
- Thread WebSocket : asyncio + websockets

L'app est sans dock (Info.plist LSUIElement: true), uniquement menu bar.

Imports : on utilise des imports **plats** (sibling modules) au lieu de
``from .`` parce que PyInstaller lance ``main.py`` comme script standalone
et non comme membre du package ``voicebridge_app`` — les imports relatifs
lèveraient ImportError au démarrage du bundle.
"""
from __future__ import annotations

import json
import logging
import sys
import threading
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import rumps  # type: ignore

try:
    # PyObjCTools.AppHelper.callAfter() dispatch un callable sur le run-loop
    # principal Cocoa. Indispensable pour toute modif AppKit (StatusBar,
    # NSMenuItem.title …) déclenchée depuis un thread Python non-main, sinon
    # NSInternalInconsistencyException ("layout engine modified from
    # background thread").
    from PyObjCTools.AppHelper import callAfter as _call_after_main  # type: ignore
except ImportError:
    # Fallback en cas d'environnement minimal (tests) : appel direct.
    def _call_after_main(fn, *args, **kwargs):
        fn(*args, **kwargs)

# Ajout du répertoire de main.py dans sys.path pour que les imports plats
# (audio, config, ws_client) résolvent en mode dev (python main.py) comme
# en mode bundle (PyInstaller flatten déjà mais on sécurise).
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import audio as audio_mod  # noqa: E402
import config as cfg  # noqa: E402
from ws_client import WSClient  # noqa: E402

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("voicebridge.app")

ICON_CONNECTED = "🟢"
ICON_PAUSED = "🟡"
ICON_DISCONNECTED = "🔴"

# Modes Live V3 (cf. doc 00-decisions-v3.md)
MODE_CPU_V1 = "cpu-fr-en"
MODE_GPU_CLONE = "gpu-clone"
MODE_GPU_NATIVE = "gpu-native"
MODE_GPU_HYBRID = "gpu-hybrid"
MODES_LABELS = {
    MODE_CPU_V1: "🔵 Authentique CPU FR/EN",
    MODE_GPU_CLONE: "🟣 Multilingue – ma voix",
    MODE_GPU_NATIVE: "🟢 Voix native",
    MODE_GPU_HYBRID: "⭐ Hybride accent natif",
}

# Langues cibles pour la traduction (label affiché dans le sous-menu)
TARGET_LANGS = {
    "off": "🚫 Désactivée (parle comme à la source)",
    "fr": "🇫🇷 Français",
    "en": "🇬🇧 Anglais",
    "es": "🇪🇸 Espagnol",
    "de": "🇩🇪 Allemand",
    "it": "🇮🇹 Italien",
    "pt": "🇵🇹 Portugais",
}


class VoiceBridgeApp(rumps.App):
    def __init__(self) -> None:
        super().__init__("VoiceBridge", title="🔴 VoiceBridge", quit_button=None)
        self.bundle_cfg = cfg.load_bundle_config()
        self.server_url = cfg.kr_get("server_url") or self.bundle_cfg.get("server_url", "")
        self.api_token = cfg.kr_get("api_token") or ""
        self.voice_id = cfg.kr_get("default_voice") or "juliette"
        self.language = "fr"

        # ── État cloud V3 (alimenté par _refresh_cloud_status au boot) ──
        self.runpod_configured = False
        self.openai_configured = False
        self.cloud_default_mode = MODE_CPU_V1
        self.cloud_default_provider = "opus-mt-cpu"

        # Mode Live V3 — Décision 4 :
        # 1) premier lancement → cpu-fr-en
        # 2) sinon last_mode mémorisé localement
        # 3) validation : si mode gpu-* mais runpod absent → fallback cpu-fr-en
        last_mode = cfg.kr_get("last_live_mode") or MODE_CPU_V1
        self.mode = last_mode if last_mode in MODES_LABELS else MODE_CPU_V1

        # Traduction Live : "off" = pas de traduction, sinon code ISO langue cible
        # (mémorisé entre sessions). Par défaut désactivée.
        last_target = cfg.kr_get("last_target_lang") or "off"
        self.target_lang_choice = last_target if last_target in TARGET_LANGS else "off"
        self.target_lang = (self.language if self.target_lang_choice == "off"
                            else self.target_lang_choice)
        # Provider auto : opus-mt-cpu pour mode CPU, nllb pour modes GPU.
        # (cohérent avec le frontend web ; pas exposé en menu pour rester simple)
        self.translation_provider = self._auto_provider(self.mode)
        self.rvc_model_id: str | None = cfg.kr_get("default_rvc_model_id") or None

        # ── État coût session (alimenté par cost_update WS) ─────────────
        self.session_cost_eur = 0.0
        self.session_duration_s = 0

        self.audio = audio_mod.AudioPipeline(on_chunk_captured=self._on_capture)
        self.ws: WSClient | None = None

        # ── Menu items ──
        # voice_item est un PARENT de sous-menu : la liste des voix
        # disponibles est peuplée par _refresh_voice_submenu() (fetch HTTP).
        self.voice_item = rumps.MenuItem(f"Voix : {self.voice_id}")
        # V3 — sous-menu Mode (4 modes, parent submenu)
        self.mode_item = rumps.MenuItem(f"Mode : {MODES_LABELS.get(self.mode, self.mode)}")
        # V3 — sous-menu Traduction (parent submenu)
        self.translate_item = rumps.MenuItem(
            f"Traduction : {TARGET_LANGS.get(self.target_lang_choice, 'off')}")
        # V3 — sous-menu Modèle RVC (visible si mode=gpu-hybrid)
        self.rvc_item = rumps.MenuItem("Modèle RVC : (aucun)")
        # V3 — bouton préchauffe GPU
        self.warmup_item = rumps.MenuItem("🔥 Préchauffer GPU", callback=self._on_warmup_gpu)
        # V3 — affichage coût session (read-only)
        self.cost_item = rumps.MenuItem("💰 Coût session : 0.0000€ · 0s")
        self.pause_item = rumps.MenuItem("⏸ Mettre en pause", callback=self._toggle_pause)
        self.preferences_item = rumps.MenuItem("Préférences…", callback=self._open_preferences)
        self.quit_item = rumps.MenuItem("⏹ Quitter", callback=self._on_quit)

        self.menu = [
            self.mode_item,
            self.voice_item,
            self.translate_item,
            self.rvc_item,
            self.warmup_item,
            None,
            self.cost_item,
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
            # 1. Récupère l'état cloud → ajuste les visibilités
            self._refresh_cloud_status()
            # 2. Valide le mode mémorisé selon état cloud
            self._validate_mode_against_cloud()
            # 3. Construit les sous-menus + lance pipeline
            self._refresh_mode_submenu()
            self._refresh_translate_submenu()
            self._refresh_rvc_submenu()
            self._update_v3_menu_visibility()
            self._start_pipeline()
            self._refresh_voice_submenu()

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
            self._refresh_cloud_status()
            self._validate_mode_against_cloud()
            self._refresh_mode_submenu()
            self._refresh_rvc_submenu()
            self._update_v3_menu_visibility()
            self._start_pipeline()
            self._refresh_voice_submenu()
        else:
            rumps.alert("Connexion impossible",
                        "Vérifiez l'URL et la clé API. L'app reste en mode déconnecté.")
            self._set_status(ICON_DISCONNECTED)

    # ── Sous-menu Voix ─────────────────────────────────────────────

    def _fetch_voices(self) -> list:
        """GET /api/voices → liste des voix actuelles avec status."""
        if not self.server_url or not self.api_token:
            return []
        try:
            url = self.server_url.rstrip("/") + "/api/voices"
            req = Request(url, headers={"Authorization": f"Bearer {self.api_token}"})
            with urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode("utf-8"))
                return data.get("voices", []) if isinstance(data, dict) else []
        except Exception as exc:  # noqa: BLE001
            log.warning("fetch voices failed: %s", exc)
            return []

    def _refresh_voice_submenu(self, _sender=None) -> None:
        """Reconstruit le sous-menu Voix avec la liste fraîche du serveur.

        Auto-fallback : si self.voice_id mémorisé n'existe plus côté
        serveur (voix supprimée, install fraîche, etc.), on bascule sur la
        première voix "ready" disponible — sinon push_audio resterait
        bloqué (configure rejetté avec "voix introuvable").
        """
        # Vide le submenu existant
        for k in list(self.voice_item.keys()):
            del self.voice_item[k]
        # Action "Rafraîchir" en premier (utile si l'utilisateur vient
        # d'ajouter une voix côté web).
        refresh = rumps.MenuItem("🔄 Rafraîchir la liste", callback=self._refresh_voice_submenu)
        self.voice_item["__refresh"] = refresh
        self.voice_item["__sep1"] = None  # séparateur
        voices = self._fetch_voices()
        if not voices:
            self.voice_item["__none"] = rumps.MenuItem("(aucune voix disponible)")
            return

        # Détection : la voix mémorisée existe-t-elle ?
        # On ne fallback QUE sur des voix utilisateur réelles (id préfixé "v_")
        # — pas sur les voix démo exposées par /api/voices qui peuvent être
        # listées sans fichier WAV exploitable (cas "juliette" sur certaines
        # installs → le live route rejette quand même avec "voix introuvable").
        ready_voices = [
            v for v in voices
            if v.get("status", "ready") == "ready"
            and str(v.get("id", "")).startswith("v_")
        ]
        current_exists = any(v.get("id") == self.voice_id for v in ready_voices)
        if not current_exists and ready_voices:
            fallback = ready_voices[0]
            log.info("voice_id '%s' introuvable → fallback '%s'",
                     self.voice_id, fallback.get("id"))
            self.voice_id = fallback["id"]
            self.language = fallback.get("language", "fr")
            if self.target_lang_choice == "off":
                self.target_lang = self.language
            cfg.kr_set("default_voice", self.voice_id)
            self.voice_item.title = f"Voix : {fallback.get('name', self.voice_id)}"
            # Re-pousse le configure au serveur avec la voix corrigée
            if self.ws:
                self.ws.set_voice(self.voice_id, self.language)
        elif not current_exists and not ready_voices:
            # Aucune voix utilisateur dispo → on notifie pour que l'user
            # crée/importe une voix sur le panel web.
            log.warning("aucune voix utilisateur (v_*) disponible côté serveur")
            try:
                rumps.notification(
                    "VoiceBridge",
                    "Aucune voix disponible",
                    "Crée une voix sur le panel web (/voices) puis "
                    "rafraîchis la liste depuis le sous-menu Voix.",
                )
            except Exception:  # noqa: BLE001
                pass

        for v in voices:
            # Skip les voix non prêtes (encoding en cours, ou failed)
            status = v.get("status", "ready")
            if status != "ready":
                disabled_label = ("⏳ " if status == "encoding" else "❌ ") + v.get("name", v.get("id", "?"))
                self.voice_item["__sk_" + v.get("id", "x")] = rumps.MenuItem(disabled_label)
                continue
            flag = "🇫🇷 " if v.get("language") == "fr" else "🇬🇧 "
            mark = "✓ " if v.get("id") == self.voice_id else "    "
            label = mark + flag + v.get("name", v.get("id", "?"))
            cb = self._make_voice_select_cb(
                v["id"], v.get("language", "fr"), v.get("name", v["id"])
            )
            self.voice_item[v["id"]] = rumps.MenuItem(label, callback=cb)

    def _make_voice_select_cb(self, voice_id: str, language: str, name: str):
        """Factory pour fixer le voice_id dans la closure (sinon Python late
        binding ferait que toutes les entrées sélectionneraient la dernière)."""
        def _cb(_sender) -> None:
            self.voice_id = voice_id
            self.language = language
            cfg.kr_set("default_voice", voice_id)
            self.voice_item.title = f"Voix : {name}"
            # Si traduction = "off", target_lang doit suivre la nouvelle langue
            # source pour ne pas déclencher une traduction parasite.
            if self.target_lang_choice == "off":
                self.target_lang = language
            if self.ws:
                self.ws.set_voice(voice_id, language)
                # Re-push target_lang au cas où il a bougé
                self.ws.set_mode(self.mode,
                                  translation_provider=self.translation_provider,
                                  target_lang=self.target_lang,
                                  rvc_model_id=self.rvc_model_id)
            log.info("voice changed to id=%s lang=%s", voice_id, language)
            # Reconstruit pour rafraîchir la coche ✓
            self._refresh_voice_submenu()
            try:
                rumps.notification("VoiceBridge", "Voix active", name)
            except Exception:  # noqa: BLE001
                pass
        return _cb

    # ── V3 : Cloud status ──────────────────────────────────────────

    def _refresh_cloud_status(self) -> None:
        """GET /api/cloud/status → alimente runpod_configured / openai_configured.

        Si l'endpoint n'existe pas (serveur V1), tout reste False et seul
        le mode cpu-fr-en sera disponible.
        """
        if not self.server_url or not self.api_token:
            return
        try:
            url = self.server_url.rstrip("/") + "/api/cloud/status"
            req = Request(url, headers={"Authorization": f"Bearer {self.api_token}"})
            with urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode("utf-8"))
                self.runpod_configured = bool(data.get("runpod_configured"))
                self.openai_configured = bool(data.get("openai_configured"))
                self.cloud_default_mode = data.get("default_live_mode") or MODE_CPU_V1
                self.cloud_default_provider = data.get("default_translation_provider") or "opus-mt-cpu"
        except Exception as exc:  # noqa: BLE001
            log.info("cloud status indisponible (serveur V1 ?) : %s", exc)
            self.runpod_configured = False
            self.openai_configured = False

    def _validate_mode_against_cloud(self) -> None:
        """Si mode mémorisé est GPU mais RunPod pas configuré → fallback cpu-fr-en."""
        if self.mode != MODE_CPU_V1 and not self.runpod_configured:
            log.info("mode %s indisponible (runpod absent) → fallback cpu-fr-en", self.mode)
            self.mode = MODE_CPU_V1
            cfg.kr_set("last_live_mode", MODE_CPU_V1)

    # ── V3 : Sous-menu Mode ────────────────────────────────────────

    def _refresh_mode_submenu(self, _sender=None) -> None:
        for k in list(self.mode_item.keys()):
            del self.mode_item[k]
        for mode_id, label in MODES_LABELS.items():
            mark = "✓ " if mode_id == self.mode else "    "
            disabled = (mode_id != MODE_CPU_V1 and not self.runpod_configured)
            display = mark + label + (" (non configuré)" if disabled else "")
            cb = None if disabled else self._make_mode_select_cb(mode_id)
            self.mode_item[mode_id] = rumps.MenuItem(display, callback=cb)
        self.mode_item.title = f"Mode : {MODES_LABELS.get(self.mode, self.mode)}"

    def _make_mode_select_cb(self, mode_id: str):
        def _cb(_sender):
            if mode_id == MODE_GPU_HYBRID and not self.rvc_model_id:
                rumps.alert("Modèle RVC requis",
                            "Le mode hybride exige un modèle RVC. "
                            "Sélectionnes-en un dans le sous-menu \"Modèle RVC\" "
                            "ou importes-en un sur la page /rvc-import.")
                return
            self.mode = mode_id
            cfg.kr_set("last_live_mode", mode_id)
            # Provider de traduction auto selon mode (CPU=opus-mt-cpu, GPU=nllb)
            self.translation_provider = self._auto_provider(mode_id)
            self._refresh_mode_submenu()
            self._update_v3_menu_visibility()
            if self.ws:
                self.ws.set_mode(self.mode,
                                  translation_provider=self.translation_provider,
                                  target_lang=self.target_lang,
                                  rvc_model_id=self.rvc_model_id)
            log.info("mode → %s (provider=%s)", mode_id, self.translation_provider)
            try:
                rumps.notification("VoiceBridge", "Mode actif", MODES_LABELS.get(mode_id, mode_id))
            except Exception:  # noqa: BLE001
                pass
        return _cb

    # ── V3 : Sous-menu Traduction ──────────────────────────────────

    @staticmethod
    def _auto_provider(mode: str) -> str:
        """Provider de traduction approprié au mode courant.

        CPU : OPUS-MT local (rapide, gratuit, FR↔EN seulement).
        GPU : NLLB sur le worker RunPod (200+ langues, qualité supérieure).
        Cohérent avec frontend web. Pas exposé en menu — choix implicite.
        """
        return "nllb" if mode != MODE_CPU_V1 else "opus-mt-cpu"

    def _refresh_translate_submenu(self, _sender=None) -> None:
        for k in list(self.translate_item.keys()):
            del self.translate_item[k]
        for lang_code, label in TARGET_LANGS.items():
            # Filtre : en CPU mode, seules FR/EN/off sont supportées par OPUS-MT
            if self.mode == MODE_CPU_V1 and lang_code not in ("off", "fr", "en"):
                continue
            mark = "✓ " if lang_code == self.target_lang_choice else "    "
            display = mark + label
            self.translate_item[lang_code] = rumps.MenuItem(
                display, callback=self._make_target_lang_cb(lang_code))
        self.translate_item.title = (
            f"Traduction : {TARGET_LANGS.get(self.target_lang_choice, 'off')}")

    def _make_target_lang_cb(self, lang_code: str):
        def _cb(_sender):
            self.target_lang_choice = lang_code
            cfg.kr_set("last_target_lang", lang_code)
            # target_lang réel envoyé au serveur : "off" → langue source (=
            # pas de traduction côté backend grâce au check src==tgt)
            self.target_lang = (self.language if lang_code == "off" else lang_code)
            self._refresh_translate_submenu()
            if self.ws:
                self.ws.set_mode(self.mode,
                                  translation_provider=self.translation_provider,
                                  target_lang=self.target_lang,
                                  rvc_model_id=self.rvc_model_id)
            log.info("target_lang → %s (envoyé : %s)", lang_code, self.target_lang)
            try:
                rumps.notification("VoiceBridge", "Traduction",
                                   TARGET_LANGS.get(lang_code, lang_code))
            except Exception:  # noqa: BLE001
                pass
        return _cb

    # ── V3 : Sous-menu Modèle RVC ──────────────────────────────────

    def _fetch_rvc_models(self) -> list:
        if not self.server_url or not self.api_token:
            return []
        try:
            url = self.server_url.rstrip("/") + "/api/rvc/models"
            req = Request(url, headers={"Authorization": f"Bearer {self.api_token}"})
            with urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode("utf-8"))
                return data.get("models", []) if isinstance(data, dict) else []
        except Exception as exc:  # noqa: BLE001
            log.info("fetch RVC models failed: %s", exc)
            return []

    def _refresh_rvc_submenu(self, _sender=None) -> None:
        for k in list(self.rvc_item.keys()):
            del self.rvc_item[k]
        # Action "Rafraîchir" en premier
        self.rvc_item["__refresh"] = rumps.MenuItem(
            "🔄 Rafraîchir la liste", callback=self._refresh_rvc_submenu)
        self.rvc_item["__sep"] = None

        models = [m for m in self._fetch_rvc_models() if m.get("status") == "active"]
        if not models:
            self.rvc_item["__none"] = rumps.MenuItem(
                "(aucun modèle — voir /rvc-import)")
            self.rvc_item.title = "Modèle RVC : (aucun)"
            return

        # Si le modèle mémorisé n'existe plus, on l'oublie
        if self.rvc_model_id and not any(m["id"] == self.rvc_model_id for m in models):
            self.rvc_model_id = None
            cfg.kr_set("default_rvc_model_id", None)

        active_name = "(aucun)"
        for m in models:
            mark = "✓ " if m["id"] == self.rvc_model_id else "    "
            label = mark + m.get("name", m["id"])
            self.rvc_item[m["id"]] = rumps.MenuItem(
                label, callback=self._make_rvc_select_cb(m["id"], m.get("name", m["id"])))
            if m["id"] == self.rvc_model_id:
                active_name = m.get("name", m["id"])
        self.rvc_item.title = f"Modèle RVC : {active_name}"

    def _make_rvc_select_cb(self, model_id: str, name: str):
        def _cb(_sender):
            self.rvc_model_id = model_id
            cfg.kr_set("default_rvc_model_id", model_id)
            self._refresh_rvc_submenu()
            if self.ws and self.mode == MODE_GPU_HYBRID:
                self.ws.set_mode(self.mode, rvc_model_id=model_id)
            try:
                rumps.notification("VoiceBridge", "Modèle RVC actif", name)
            except Exception:  # noqa: BLE001
                pass
        return _cb

    # ── V3 : Préchauffe GPU ────────────────────────────────────────

    def _on_warmup_gpu(self, _sender) -> None:
        if not self.runpod_configured:
            rumps.alert("RunPod non configuré",
                        "Va dans Réglages → Cloud sur le panel web pour saisir ta clé RunPod.")
            return
        components = ["whisper", "f5tts", "nllb"]
        if self.mode == MODE_GPU_HYBRID:
            components.append("rvc")

        # Run en thread : un cold-start RunPod peut prendre 2-5 min
        # (pull image + load modèles depuis le Volume). Sur main thread
        # ça freezerait toute la menu bar.
        self.warmup_item.title = "⏳ Préchauffage GPU en cours…"
        try:
            rumps.notification("VoiceBridge", "Préchauffage lancé",
                               "Chargement des modèles GPU — peut prendre 1-3 min.")
        except Exception:  # noqa: BLE001
            pass

        threading.Thread(
            target=self._warmup_thread,
            args=(components,),
            daemon=True,
        ).start()

    def _warmup_thread(self, components: list[str]) -> None:
        """Appel HTTP bloquant /api/cloud/runpod/warmup en thread.

        Timeout client : 320 s (le backend route bloque jusqu'à 300 s sur
        /runsync + polling /status, on garde 20 s de marge réseau).
        """
        result: dict = {}
        error_msg: str | None = None
        try:
            url = self.server_url.rstrip("/") + "/api/cloud/runpod/warmup"
            body = json.dumps({"components": components}).encode("utf-8")
            req = Request(url, data=body, method="POST",
                           headers={"Authorization": f"Bearer {self.api_token}",
                                    "Content-Type": "application/json"})
            with urlopen(req, timeout=320) as r:
                result = json.loads(r.read().decode("utf-8"))
        except HTTPError as exc:
            # Le backend renvoie 503 avec detail={error, message} en cas d'échec
            try:
                detail = json.loads(exc.read().decode("utf-8"))
                error_msg = (detail.get("detail", {}).get("message")
                             if isinstance(detail.get("detail"), dict)
                             else str(detail)[:200])
            except Exception:  # noqa: BLE001
                error_msg = f"HTTP {exc.code} : {exc.reason}"
        except URLError as exc:
            error_msg = f"Erreur réseau : {exc.reason}"
        except Exception as exc:  # noqa: BLE001
            error_msg = str(exc)

        # Retour UI sur main thread
        _call_after_main(self._apply_warmup_result, result, error_msg)

    def _apply_warmup_result(self, result: dict, error_msg: str | None) -> None:
        is_gpu = self.mode != MODE_CPU_V1
        self.warmup_item.title = ("🔥 Préchauffer GPU" if is_gpu
                                   else "🔥 Préchauffer GPU (mode CPU — inutile)")
        if error_msg:
            log.warning("warmup failed: %s", error_msg)
            rumps.alert("Préchauffe échouée", error_msg)
            return
        loaded = ", ".join(result.get("loaded", []))
        try:
            rumps.notification("VoiceBridge", "✅ GPU prêt",
                               "Modèles chargés : " + (loaded or "(aucun)"))
        except Exception:  # noqa: BLE001
            pass

    # ── V3 : Visibilité conditionnelle des items menu ──────────────

    def _update_v3_menu_visibility(self) -> None:
        """Active/désactive les items selon le mode actuel.

        rumps ne permet pas de retirer/réinsérer dynamiquement un item du menu
        principal proprement. On change le titre + on grise via callback=None
        pour les items non pertinents.
        """
        is_gpu = self.mode != MODE_CPU_V1
        is_hybrid = self.mode == MODE_GPU_HYBRID
        # Préchauffage seulement utile en mode GPU
        self.warmup_item.title = ("🔥 Préchauffer GPU" if is_gpu
                                   else "🔥 Préchauffer GPU (mode CPU — inutile)")
        # Item RVC : visible mais désactivé si pas hybride
        if not is_hybrid:
            self.rvc_item.title = "Modèle RVC : (mode hybride uniquement)"
        else:
            # Reconstruit le titre à partir de la sélection courante
            self._refresh_rvc_submenu()
        # Coût : visible uniquement en mode GPU (CPU = gratuit)
        if is_gpu:
            # Restaure le coût courant (ou 0.0000€ si pas encore d'update WS)
            self.cost_item.title = (
                f"💰 Coût session : {self.session_cost_eur:.4f}€"
                f" · {self.session_duration_s}s")
        else:
            self.cost_item.title = "💰 Coût session : 0.0000€ (mode CPU gratuit)"
        # Reconstruit le sous-menu traduction (les langues dispo dépendent du mode)
        self._refresh_translate_submenu()

    def _on_cost_update(self, cost_eur: float, duration_s: int) -> None:
        """Callback alimenté par WSClient quand un cost_update arrive.

        Appelé depuis le thread asyncio → marshalle vers main thread Cocoa.
        """
        _call_after_main(self._apply_cost_update, cost_eur, duration_s)

    def _apply_cost_update(self, cost_eur: float, duration_s: int) -> None:
        self.session_cost_eur = cost_eur
        self.session_duration_s = duration_s
        if self.mode != MODE_CPU_V1:
            self.cost_item.title = (
                f"💰 Coût session : {cost_eur:.4f}€ · {duration_s}s")

    def _test_connection(self) -> bool:
        try:
            url = self.server_url.rstrip("/") + "/api/auth/check"
            req = Request(url, headers={"Authorization": f"Bearer {self.api_token}"})
            with urlopen(req, timeout=15) as r:
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
            mode=self.mode,
            translation_provider=self.translation_provider,
            target_lang=self.target_lang,
            rvc_model_id=self.rvc_model_id,
            on_state_change=self._on_ws_state,
            on_cost_update=self._on_cost_update,
        )
        self.ws.start()

    def _on_capture(self, raw_bytes: bytes) -> None:
        if self.ws:
            self.ws.push_audio(raw_bytes)

    def _on_ws_state(self, state: str, *args) -> None:
        # Callback appelé depuis le thread asyncio de WSClient. On marshalle
        # vers le main thread Cocoa (sinon AppKit lève
        # NSInternalInconsistencyException sur tout setTitle).
        _call_after_main(self._apply_ws_state, state, args)

    def _apply_ws_state(self, state: str, args: tuple) -> None:
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
