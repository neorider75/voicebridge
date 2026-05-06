# 19 - App macOS V3 (extensions)

> **Document V3 nouveau.** Détail des modifications de l'app macOS pour V3.
>
> Le doc `06-voicebridge-app.md` (V1) reste valable pour le comportement existant.

## Vue d'ensemble

L'app macOS V3 ajoute :
- Sélecteur de mode Live (4 modes) dans le menu
- Sélecteur de modèle RVC (visible si mode = hybrid)
- Sélecteur de provider de traduction
- Bouton "🔥 Préchauffer GPU"
- Indicateur de latence cible / état GPU
- Compteur de coût session en temps réel

## Modifications du menu

### Menu actuel V1

```
🟢 VoiceBridge
├ Voix : juliette                  [parent submenu]
├──────────────────────────────────
├ ⏸ Mettre en pause
├──────────────────────────────────
├ Préférences…
├──────────────────────────────────
├ ⏹ Quitter
```

### Menu V3 cible

```
🟢 VoiceBridge · Multilingue ma voix · ~1s
├ Mode Live ▶                     [submenu]
│  ├ ✓ Multilingue ma voix
│  ├ Voix native
│  ├ Hybride accent natif
│  └ ──────────────────
│  └ Authentique CPU FR/EN (lent)
├ Voix : juliette ▶                [submenu]
├ Modèle RVC ▶                     [submenu, visible si mode=hybrid]
│  └ ✓ JC voice v1
├ Traduction ▶                      [submenu]
│  ├ Désactivée
│  ├ ──────────────────
│  ├ ✓ → Anglais (NLLB)
│  ├ → Allemand (NLLB)
│  ├ ──────────────────
│  ├ Provider ▶
│  │  ├ ✓ NLLB-200
│  │  ├ OPUS-MT GPU
│  │  ├ GPT-4o-mini (~0.04€/1000)
│  │  └ GPT-4o (~0.40€/1000)
├──────────────────────────────────
├ 🔥 Préchauffer GPU
├ ⏸ Mettre en pause
├──────────────────────────────────
├ Session : 03:42 · 0.018€
├──────────────────────────────────
├ Préférences…
├──────────────────────────────────
├ ⏹ Quitter
```

## Modifications du code Python

### `Site/macos-app/voicebridge_app/main.py`

```python
"""V3 ajouts au main.py de l'app macOS.

Diff explicite par rapport au code V1 existant.
"""

# ─── Ajouts en tête ───────────────────────────────────────────────
import time

# Constantes V3
LIVE_MODES = [
    ("gpu-clone", "Multilingue ma voix"),
    ("gpu-native", "Voix native"),
    ("gpu-hybrid", "Hybride accent natif"),
    ("cpu-fr-en", "Authentique CPU FR/EN (lent)"),
]

TRANSLATION_PROVIDERS = [
    ("nllb", "NLLB-200 (200+ langues)"),
    ("opus-mt-gpu", "OPUS-MT GPU"),
    ("opus-mt-cpu", "OPUS-MT CPU"),
    ("gpt-4o-mini", "GPT-4o-mini (~0.04€/1000)"),
    ("gpt-4o", "GPT-4o (~0.40€/1000)"),
]

LATENCY_HINTS = {
    "cpu-fr-en": "5-15s ⚠️",
    "gpu-clone": "~1s",
    "gpu-native": "~1s",
    "gpu-hybrid": "~1.2s",
}


# ─── Modification de VoiceBridgeApp.__init__ ──────────────────────
class VoiceBridgeApp(rumps.App):
    def __init__(self) -> None:
        super().__init__("VoiceBridge", title="🔴 VoiceBridge", quit_button=None)
        self.bundle_cfg = cfg.load_bundle_config()
        self.server_url = cfg.kr_get("server_url") or self.bundle_cfg.get("server_url", "")
        self.api_token = cfg.kr_get("api_token") or ""
        self.voice_id = cfg.kr_get("default_voice") or "juliette"
        self.language = "fr"
        
        # ─── V3 : nouvelles propriétés ─────────────────
        self.mode = cfg.kr_get("default_mode") or "gpu-clone"
        self.translation_provider = cfg.kr_get("default_translation_provider") or "nllb"
        self.translate_to = cfg.kr_get("default_translate_to") or ""  # vide = désactivée
        self.rvc_model_id = cfg.kr_get("default_rvc_model") or ""
        self.is_gpu_warmed = False
        self.session_started_at = None
        self.session_cost_eur = 0.0
        self.rvc_models_cache = []  # liste des modèles depuis le serveur
        
        self.audio = audio_mod.AudioPipeline(on_chunk_captured=self._on_capture)
        self.ws: WSClient | None = None
        
        # ─── Menu V3 ────────────────────────────────────
        self._build_menu()
        
        # Démarrage : refresh voices + RVC models
        if not self.api_token:
            rumps.alert("Bienvenue !", "Configurez la clé API dans les Préférences.")
        else:
            self._refresh_voice_submenu()
            self._refresh_rvc_submenu()
            self._connect_ws()
    
    def _build_menu(self):
        """Build le menu V3 complet."""
        # Menu mode
        self.mode_item = rumps.MenuItem(self._mode_label())
        self.mode_submenu = []
        for mode_id, mode_name in LIVE_MODES:
            item = rumps.MenuItem(
                f"{'✓ ' if mode_id == self.mode else '  '}{mode_name}",
                callback=lambda sender, m=mode_id: self._on_mode_change(m)
            )
            self.mode_submenu.append(item)
            self.mode_item.add(item)
        
        # Menu voix (V1 existant)
        self.voice_item = rumps.MenuItem(f"Voix : {self.voice_id}")
        
        # Menu RVC (V3 nouveau)
        self.rvc_item = rumps.MenuItem("Modèle RVC")
        # Visible uniquement si mode = hybrid
        if self.mode != "gpu-hybrid":
            self.rvc_item._menuitem.setHidden_(True) if hasattr(self.rvc_item, '_menuitem') else None
        
        # Menu traduction
        self.translate_item = rumps.MenuItem("Traduction")
        self._build_translate_submenu()
        
        # Boutons d'action
        self.warmup_item = rumps.MenuItem("🔥 Préchauffer GPU", callback=self._on_warmup_gpu)
        self.pause_item = rumps.MenuItem("⏸ Mettre en pause", callback=self._toggle_pause)
        
        # Indicateur session (read-only, mis à jour périodiquement)
        self.session_indicator = rumps.MenuItem("Session : --:-- · 0.00€")
        self.session_indicator._menuitem.setEnabled_(False) if hasattr(self.session_indicator, '_menuitem') else None
        
        # Préférences + Quitter
        self.preferences_item = rumps.MenuItem("Préférences…", callback=self._open_preferences)
        self.quit_item = rumps.MenuItem("⏹ Quitter", callback=self._on_quit)
        
        self.menu = [
            self.mode_item,
            self.voice_item,
            self.rvc_item,
            self.translate_item,
            None,  # separator
            self.warmup_item,
            self.pause_item,
            None,
            self.session_indicator,
            None,
            self.preferences_item,
            None,
            self.quit_item,
        ]
        
        # Mise à jour du titre
        self._update_title()
    
    def _build_translate_submenu(self):
        """Submenu traduction : on/off + langues + provider."""
        # Désactivée
        off_item = rumps.MenuItem(
            f"{'✓ ' if not self.translate_to else '  '}Désactivée",
            callback=lambda s: self._on_translate_change(None, None)
        )
        self.translate_item.add(off_item)
        self.translate_item.add(None)  # separator
        
        # Langues cibles courantes
        for lang_code, flag, name in [
            ("en", "🇬🇧", "Anglais"),
            ("de", "🇩🇪", "Allemand"),
            ("es", "🇪🇸", "Espagnol"),
            ("it", "🇮🇹", "Italien"),
            ("ja", "🇯🇵", "Japonais"),
            ("zh", "🇨🇳", "Chinois"),
        ]:
            item = rumps.MenuItem(
                f"{'✓ ' if lang_code == self.translate_to else '  '}→ {name} {flag}",
                callback=lambda s, l=lang_code: self._on_translate_change(l, self.translation_provider)
            )
            self.translate_item.add(item)
        
        self.translate_item.add(None)  # separator
        
        # Submenu provider
        provider_submenu = rumps.MenuItem("Provider")
        for prov_id, prov_name in TRANSLATION_PROVIDERS:
            p_item = rumps.MenuItem(
                f"{'✓ ' if prov_id == self.translation_provider else '  '}{prov_name}",
                callback=lambda s, p=prov_id: self._on_translate_change(self.translate_to, p)
            )
            provider_submenu.add(p_item)
        self.translate_item.add(provider_submenu)
    
    def _mode_label(self) -> str:
        for mid, mname in LIVE_MODES:
            if mid == self.mode:
                return f"Mode : {mname}"
        return f"Mode : {self.mode}"
    
    def _update_title(self):
        """Met à jour le title du menu bar."""
        mode_name = next((mname for mid, mname in LIVE_MODES if mid == self.mode), self.mode)
        latency = LATENCY_HINTS.get(self.mode, "?")
        gpu_icon = "🔥" if self.is_gpu_warmed else ""
        ws_icon = ICON_CONNECTED if (self.ws and self.ws._ws) else ICON_DISCONNECTED
        self.title = f"{ws_icon} {gpu_icon} {mode_name[:20]} · {latency}"
    
    # ─── Callbacks V3 ────────────────────────────────────
    
    def _on_mode_change(self, new_mode: str):
        """Change le mode Live et reconnecte le WS."""
        self.mode = new_mode
        cfg.kr_set("default_mode", new_mode)
        
        # Mise à jour des coches dans le submenu
        for item, (mid, mname) in zip(self.mode_submenu, LIVE_MODES):
            item.title = f"{'✓ ' if mid == new_mode else '  '}{mname}"
        
        # Visibilité du menu RVC
        # Note: rumps ne supporte pas directement setHidden, on remove/add
        # En pratique, on peut désactiver l'item à la place
        try:
            if new_mode == "gpu-hybrid":
                self.rvc_item._menuitem.setHidden_(False)
            else:
                self.rvc_item._menuitem.setHidden_(True)
        except AttributeError:
            pass  # rumps version sans setHidden_
        
        self._update_title()
        
        # Reconnect WS avec nouveau mode
        if self.ws:
            self.ws.set_mode(new_mode, self.translation_provider, self.rvc_model_id)
        
        rumps.notification(
            "VoiceBridge",
            "Mode Live changé",
            f"Mode : {next(mname for mid, mname in LIVE_MODES if mid == new_mode)}",
        )
    
    def _on_translate_change(self, target_lang: str | None, provider: str | None):
        """Change la traduction (langue + provider)."""
        self.translate_to = target_lang or ""
        if provider:
            self.translation_provider = provider
        cfg.kr_set("default_translate_to", self.translate_to)
        cfg.kr_set("default_translation_provider", self.translation_provider)
        
        self._build_translate_submenu()  # rebuild pour les coches
        
        if self.ws:
            self.ws.set_translation(target_lang, provider)
    
    def _on_warmup_gpu(self, _):
        """Lance le warmup GPU."""
        self.warmup_item.title = "⏳ Préchauffage en cours..."
        self.warmup_item._menuitem.setEnabled_(False) if hasattr(self.warmup_item, '_menuitem') else None
        
        # Lancer dans un thread pour ne pas bloquer le UI
        import threading
        threading.Thread(target=self._do_warmup, daemon=True).start()
    
    def _do_warmup(self):
        """Tâche de warmup en background."""
        import requests
        try:
            r = requests.post(
                f"{self.server_url}/api/cloud/runpod/warmup",
                headers={"Authorization": f"Bearer {self.api_token}"},
                json={"components": ["whisper", "f5tts", "nllb"]},
                timeout=10,
            )
            if r.status_code == 200:
                # Poll status until done (les détails sont dans le doc 16)
                task_id = r.json()["task_id"]
                self._poll_warmup_status(task_id)
            else:
                rumps.notification("VoiceBridge", "Erreur warmup",
                                    f"HTTP {r.status_code}")
        except Exception as e:
            rumps.notification("VoiceBridge", "Erreur warmup", str(e))
        finally:
            # Reset UI
            self.warmup_item.title = "🔥 Préchauffer GPU"
            try:
                self.warmup_item._menuitem.setEnabled_(True)
            except AttributeError:
                pass
    
    def _poll_warmup_status(self, task_id: str):
        """Poll /api/tasks/{task_id}/status jusqu'à done."""
        import requests
        for _ in range(60):  # max 60 essais × 1s = 1 min
            try:
                r = requests.get(
                    f"{self.server_url}/api/tasks/{task_id}/status",
                    headers={"Authorization": f"Bearer {self.api_token}"},
                    timeout=5,
                )
                if r.status_code == 200:
                    data = r.json()
                    if data["status"] == "done":
                        self.is_gpu_warmed = True
                        self._update_title()
                        rumps.notification("VoiceBridge", "✅ GPU prêt",
                                            "Latence optimale activée")
                        return
                    elif data["status"] == "error":
                        rumps.notification("VoiceBridge", "Erreur warmup",
                                            data.get("error", "unknown"))
                        return
            except Exception as e:
                pass
            time.sleep(1)
    
    def _refresh_rvc_submenu(self):
        """Récupère la liste des modèles RVC depuis le serveur."""
        import requests
        try:
            r = requests.get(
                f"{self.server_url}/api/rvc/models",
                headers={"Authorization": f"Bearer {self.api_token}"},
                timeout=5,
            )
            if r.status_code == 200:
                data = r.json()
                self.rvc_models_cache = data.get("models", [])
                
                # Rebuild submenu
                # Note: rumps ne supporte pas le clear direct, on doit
                # itérer ou utiliser une astuce. Pour l'instant simplifié.
                # En pratique, garder l'item parent et remplacer ses enfants.
                # ...
        except Exception as e:
            log.warning("Failed to load RVC models: %s", e)
    
    def _on_rvc_change(self, rvc_id: str):
        """Change le modèle RVC actif."""
        self.rvc_model_id = rvc_id
        cfg.kr_set("default_rvc_model", rvc_id)
        if self.ws:
            self.ws.set_rvc_model(rvc_id)
    
    def _update_session_indicator(self):
        """Met à jour l'indicateur Session : XX:XX · 0.00€."""
        if not self.session_started_at:
            self.session_indicator.title = "Session inactive"
            return
        elapsed = int(time.time() - self.session_started_at)
        m = elapsed // 60
        s = elapsed % 60
        self.session_indicator.title = (
            f"Session : {m:02d}:{s:02d} · {self.session_cost_eur:.3f}€"
        )
```

### `Site/macos-app/voicebridge_app/ws_client.py`

```python
"""V3 ajouts au ws_client.py.

Le client WebSocket envoie le mode + provider + rvc_model_id dans le payload
configure, et reçoit les nouveaux types de messages (cost_update, warmup_progress).
"""

class WSClient:
    def __init__(self, server_url, api_token, audio_pipeline,
                 voice_id, language="fr",
                 # NOUVEAU V3
                 mode="gpu-clone",
                 translation_provider="nllb",
                 translate_to=None,
                 rvc_model_id=None,
                 on_cost_update=None,  # callback(session_cost_eur, duration_s)
                 # ... autres params V1
                 on_state_change=None):
        self.server_url = server_url
        self.api_token = api_token
        self.audio = audio_pipeline
        self.voice_id = voice_id
        self.language = language
        
        # V3
        self.mode = mode
        self.translation_provider = translation_provider
        self.translate_to = translate_to
        self.rvc_model_id = rvc_model_id
        self.on_cost_update = on_cost_update or (lambda *a: None)
        
        self.on_state_change = on_state_change or (lambda *a, **kw: None)
        # ...
    
    async def _send_configure(self, ws):
        """Envoie le payload configure avec champs V3."""
        payload = {
            "type": "configure",
            "voice_id": self.voice_id,
            "language": self.language,
            "output": "blackhole",
            
            # V3
            "mode": self.mode,
            "translation_provider": self.translation_provider,
        }
        
        if self.translate_to:
            payload["translate"] = True
            payload["translate_to"] = self.translate_to
        
        if self.mode == "gpu-hybrid":
            if not self.rvc_model_id:
                log.warning("gpu-hybrid mode without rvc_model_id")
            else:
                payload["rvc_model_id"] = self.rvc_model_id
        
        await ws.send(json.dumps(payload))
    
    async def _handle_message(self, msg):
        """V3 : handle nouveaux types de messages."""
        # Code V1 existant pour audio_pcm, audio_chunk, transcript, ready, error
        # ...
        
        # NOUVEAU V3
        if isinstance(msg, str):
            try:
                payload = json.loads(msg)
            except:
                return
            
            ptype = payload.get("type")
            
            if ptype == "cost_update":
                # Notif callback pour update UI macOS
                self.on_cost_update(
                    payload.get("session_cost_eur", 0),
                    payload.get("duration_seconds", 0),
                )
            elif ptype == "warmup_progress":
                # Affichage progression cold start
                self.on_state_change("warming", payload.get("step"))
            elif ptype == "translated":
                # V1 a déjà ce type, mais V3 ajoute src/tgt
                pass
    
    def set_mode(self, new_mode: str, provider: str = None,
                 rvc_model_id: str = None):
        """Change le mode en cours de session : reconnecte le WS."""
        self.mode = new_mode
        if provider:
            self.translation_provider = provider
        if rvc_model_id is not None:
            self.rvc_model_id = rvc_model_id
        # Forcer reconnexion pour nouveau configure
        # (car le serveur ne supporte pas le re-configure runtime pour l'instant)
        self.stop()
        self.start()
    
    def set_translation(self, target_lang: str | None, provider: str | None):
        self.translate_to = target_lang
        if provider:
            self.translation_provider = provider
        self.stop()
        self.start()
    
    def set_rvc_model(self, rvc_id: str):
        self.rvc_model_id = rvc_id
        if self.mode == "gpu-hybrid":
            self.stop()
            self.start()
```

## Modifications de `config.py`

Ajouter les nouvelles clés persistantes :

```python
# Site/macos-app/voicebridge_app/config.py

KEYRING_KEYS_V3 = [
    # V1 existants
    "server_url",
    "api_token",
    "default_voice",
    
    # V3 ajouts
    "default_mode",
    "default_translation_provider",
    "default_translate_to",
    "default_rvc_model",
]
```

## Build PyInstaller

`build.sh` reste identique. Le fichier `VoiceBridge.app.zip` est régénéré et déployé via `install.sh` phase 9 (existant).

Tester sur :
- Apple Silicon M1/M2/M3/M4 (cible V3)
- Apple Intel (V3.5+, optionnel)

## Tests manuels

| Scénario | Expected |
|---|---|
| Lancer l'app fraîchement compilée | Menu bar avec 🔴 + indicateur mode |
| Click sur "Mode Live" | Submenu avec 4 modes |
| Sélectionner "Hybride accent natif" | Si pas de RVC, alerte ; sinon submenu RVC visible |
| Bouton "🔥 Préchauffer GPU" | Notification "GPU prêt" après ~30s |
| Pendant session | Compteur "Session : XX:XX · 0.XXX€" qui se met à jour |
| Switch provider trad pendant session | Reconnect WS, message "Provider changé" |
| Quit | Stop WS proprement |

## Compatibilité

L'app V3 reste **rétrocompatible avec un serveur V1** : si le serveur ne supporte pas les nouveaux modes, l'app retombe sur le mode `cpu-fr-en` (V1) silencieusement.
