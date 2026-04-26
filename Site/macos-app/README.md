# VoiceBridge.app — application macOS menu bar

App standalone qui capture le micro, stream vers le VPS via WebSocket et
injecte la voix clonée dans BlackHole pour Teams/Zoom/Meet.

## Architecture

- `voicebridge_app/main.py` — entry point (`rumps`)
- `voicebridge_app/audio.py` — pipeline PyAudio (capture + injection BlackHole)
- `voicebridge_app/ws_client.py` — WebSocket asyncio dans un thread dédié
- `voicebridge_app/config.py` — chargement bundle + persistance Keychain (`keyring`)

## Build local (sur Mac)

```bash
brew install portaudio
cd Site/macos-app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
./build.sh --zip
# → dist/VoiceBridge.app + dist/VoiceBridge.app.zip
```

## Déploiement sur le VPS

```bash
scp dist/VoiceBridge.app.zip root@TON_VPS:/var/voicebridge/data/install/
ssh root@TON_VPS 'chown voicebridge:voicebridge /var/voicebridge/data/install/VoiceBridge.app.zip'
```

L'archive est ensuite servie au front web depuis Réglages → Installation.
**Le `config.json` embarqué dans le bundle est réécrit côté VPS à l'install
pour pointer sur le bon `server_url`.**

## Test local sans build

```bash
cd Site/macos-app
source .venv/bin/activate
VB_SERVER_URL=https://voice.exemple.com python -m voicebridge_app
```

## Permissions macOS

Au premier lancement, macOS demande l'accès au micro. Si refusé :
*Préférences Système → Confidentialité → Microphone* → cocher VoiceBridge.

`Info.plist` contient `NSMicrophoneUsageDescription` (justification) et
`LSUIElement: true` (cache l'app du dock).

## Limitations POC

- **Pas de signature/notarization** : Gatekeeper affichera "Apple ne peut
  pas vérifier l'application" au premier lancement (clic-droit → Ouvrir).
- **Sélecteur de voix** : actuellement libre via Préférences. La synchro
  voix active (broadcast WebSocket) est branchée mais le menu déroulant
  n'est pas encore peuplé dynamiquement depuis `/api/voices`.
- **Préférences riches** (micro source, options "lancer au démarrage")
  non implémentées : viendront sur une itération ultérieure.
