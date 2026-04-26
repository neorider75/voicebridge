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
cd Site/macos-app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
./build.sh --zip
# → dist/VoiceBridge.app + dist/VoiceBridge.app.zip
```

ℹ️  La lib audio utilisée est ``sounddevice`` (et non ``pyaudio``) : sa
wheel pip embarque PortAudio binaire, donc pas besoin de
``brew install portaudio`` ni de toolchain C. Le build fonctionne sur un
Mac neuf sans Homebrew.

## Déploiement (via git, sans scp)

Le bundle est **versionné dans le repo** sous
``Site/macos-app/release/VoiceBridge.app.zip`` (24 Mo). Le script
``build.sh --zip`` y copie la dernière version. Pour publier :

```bash
git add Site/macos-app/release/VoiceBridge.app.zip
git commit -m "macos-app : nouveau bundle"
git push origin main
```

Sur le VPS, l'``install.sh`` (phase 9) récupère le bundle depuis le repo
cloné, **patche** le ``config.json`` embarqué pour pointer sur le bon
``server_url`` et le re-zippe dans ``/var/voicebridge/data/install/``.

→ Le front web le sert ensuite à ``Réglages → Installation`` via
``GET /api/install/voicebridge-app``.

> ℹ️ 24 Mo dans git c'est OK pour le POC. Si tu pousses des updates
> fréquentes, bascule sur Git LFS ou GitHub Releases pour garder le repo
> léger.

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
