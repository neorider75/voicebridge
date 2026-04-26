# 06 - Application macOS VoiceBridge

## Objectif

Application macOS légère qui capture le micro local, envoie l'audio au VPS via WebSocket, reçoit l'audio cloné en retour et l'injecte dans BlackHole pour permettre à Teams/Zoom/Meet de récupérer la voix clonée.

## Stack technique

- Python 3.11+
- `rumps` : menu bar macOS native
- `pyaudio` : capture micro + injection BlackHole
- `websockets` : connexion WebSocket persistante
- `keyring` : stockage sécurisé des credentials (macOS Keychain)
- `PyInstaller` : packaging en `.app` standalone

## Comportement général

- App sans fenêtre principale (menu bar only)
- Lancement silencieux
- Connexion automatique au VPS au démarrage
- Standalone .app, pas besoin de Python installé

## Menu bar (rumps)

### Items
```
🟢 VoiceBridge
─────────────────
Voix active
[ Sélecteur via popover ]
─────────────────
⏸ Mettre en pause
─────────────────
Micro source ▶
Sortie virtuelle ▶
─────────────────
Ouvrir les préférences
─────────────────
⏹ Quitter
```

### États

| État | Icône | Comportement |
|---|---|---|
| Connecté + actif | 🟢 | Voix clonée temps réel |
| Connecté + pause | 🟡 | Micro réel passe dans BlackHole (bypass) |
| Déconnecté | 🔴 | Erreur connexion VPS |

## Sélecteur de voix

Au clic sur "Voix active", popover avec liste :
```
🇫🇷 JC - Français     ✓
🇬🇧 JC - Anglais
🇫🇷 Juliette
🇬🇧 Dave
```

Au clic sur une voix : envoi WebSocket `{ "type": "set_voice", "voice_id": "..." }`. Le serveur broadcast à tous les clients connectés (sync front web).

## Préférences (fenêtre native)

### Connexion
- URL serveur (pré-remplie à l'installation)
- Token API (input password)
- Bouton "Tester la connexion"
- Statut de connexion

### Audio
- Micro source (dropdown des micros disponibles)
- Sortie virtuelle (BlackHole 2ch)

### Comportement
- ☑️ Lancer au démarrage Mac
- ☑️ Confirmer avant de quitter
- ☑️ Repasser automatiquement sur micro réel au quit

### Voix par défaut
- Dropdown des voix (récupéré via API)

## Pipeline audio

### Capture
- Format : WAV 16kHz mono
- Chunks de 100ms
- Buffer ring 2s

### Envoi WebSocket
- Encodage Base64 par chunk
- Heartbeat ping toutes les 30s
- Reconnexion auto avec backoff exponentiel

### Réception
- Audio retour : WAV 24kHz mono (NeuTTS)
- Resample 48kHz pour BlackHole
- Injection via PyAudio

### Mode pause (bypass)
- Capture micro toujours active
- WebSocket envoi désactivé
- Vraie voix routée directement vers BlackHole
- Latence ~10ms

## Comportement au quit

Si "Confirmer avant de quitter" activé :
```
Quitter VoiceBridge ?
Teams utilisera le silence sur BlackHole.
[ Annuler ] [ Quitter ]
```

Si "Repasser automatiquement sur micro réel au quit" activé :
- 2s de transition où le vrai micro est routé vers BlackHole
- Évite la coupure brutale dans Teams

## Stockage local (Keychain)

```python
keyring.set_password("VoiceBridge", "server_url", "...")
keyring.set_password("VoiceBridge", "api_token", "...")
keyring.set_password("VoiceBridge", "default_voice", "...")
keyring.set_password("VoiceBridge", "preferences", json.dumps({...}))
```

## Build PyInstaller

```bash
pyinstaller --windowed \
  --name VoiceBridge \
  --icon icon.icns \
  --add-data "config.json:." \
  --osx-bundle-identifier com.voicebridge.app \
  voicebridge_app/main.py
```

### Info.plist requis
```xml
<key>NSMicrophoneUsageDescription</key>
<string>VoiceBridge needs microphone access for voice cloning.</string>
<key>LSUIElement</key>
<true/>
```

`LSUIElement: true` masque l'app du Dock (menu bar uniquement).

## Configuration à l'installation

Le script bash d'installation génère un `config.json` embarqué dans l'app avec :
```json
{
  "server_url": "https://voicebridge.example.com",
  "version": "1.0.0"
}
```

Au premier lancement, l'URL est pré-remplie. L'utilisateur n'a qu'à coller sa clé API.

## Distribution

L'app `.app` est zippée et placée dans `/var/voicebridge/data/install/VoiceBridge.app.zip`, accessible via le front web pour téléchargement.

## Permissions macOS

Au premier lancement :
- Popup système demandant l'accès au micro
- L'utilisateur autorise via Préférences Système → Confidentialité → Microphone
- Si refusé : dialog explicative

## Synchronisation voix active

État partagé serveur :
```json
{ "active_voice": "jc_fr" }
```

- Front web change voix → VoiceBridge.app reçoit le changement
- VoiceBridge.app change voix → front web met à jour son sélecteur
- Source de vérité unique : le serveur

## Latences cibles

| Étape | Latence |
|---|---|
| Capture → envoi VPS | < 100ms |
| Pipeline VPS | 0.6 à 1.4s |
| Retour → BlackHole | < 50ms |
| **Total perçu** | 0.7 à 1.5s |

## Cas d'usage utilisateur

### Premier lancement
1. Télécharger VoiceBridge.app.zip depuis le front web
2. Décompresser + drag dans Applications
3. Lancer (clic-droit Ouvrir si warning Gatekeeper)
4. Autoriser accès micro
5. Préférences ouvertes automatiquement (URL pré-remplie)
6. Coller la clé API → Tester → 🟢
7. Choisir BlackHole + voix par défaut
8. Configurer Teams pour utiliser BlackHole comme micro
9. ✅ Prêt

### Usage quotidien
1. App lancée au démarrage Mac (si configuré)
2. Icône 🟢 dans menu bar
3. Démarrer un appel Teams
4. La voix clonée arrive automatiquement dans Teams
5. Click menu bar pour changer de voix à la volée
6. Mise en pause via menu bar si besoin de zéro latence ponctuel

## Robustesse

- Coupure WiFi 2s : buffer 5s côté VPS, reprise sans coupure
- Coupure WiFi > 5s : message dans Teams perdu, reconnexion auto
- Quit accidentel : auto-bypass évite silence brutal
- Token invalide : ouverture auto des Préférences
