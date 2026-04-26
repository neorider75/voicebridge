#!/usr/bin/env bash
# Build VoiceBridge.app pour macOS via PyInstaller.
#
# Prérequis (sur ton Mac) :
#   - Python 3.11+
#   - portaudio (pour pyaudio) :  brew install portaudio
#   - venv :  python3 -m venv .venv && source .venv/bin/activate
#             pip install -r requirements.txt
#
# Le bundle produit attend un ``config.json`` à la racine de ``voicebridge_app/``
# avec l'URL du serveur. Si absent, build.sh crée un placeholder qui sera
# écrasé par le script bash d'installation côté VPS au moment du déploiement.
#
# Usage :
#   ./build.sh                  # build local pour test
#   ./build.sh --zip            # build + zip prêt à uploader sur le VPS

set -euo pipefail

cd "$(dirname "$0")"

DO_ZIP=0
for a in "$@"; do
  case "$a" in
    --zip) DO_ZIP=1 ;;
    *) echo "Argument inconnu : $a" >&2; exit 1 ;;
  esac
done

# Placeholder config.json si absent (sera écrasé à l'install)
if [[ ! -f voicebridge_app/config.json ]]; then
  cat > voicebridge_app/config.json <<EOF
{ "server_url": "https://CHANGEME.example.com", "version": "1.0.0" }
EOF
  echo "ℹ️  config.json placeholder créé (sera réécrit par install.sh)"
fi

# Cleanup
rm -rf build dist VoiceBridge.spec

# Build
pyinstaller \
  --noconfirm \
  --windowed \
  --name VoiceBridge \
  --osx-bundle-identifier com.voicebridge.app \
  --add-data "voicebridge_app/config.json:." \
  --hidden-import rumps \
  --hidden-import websockets \
  --hidden-import keyring \
  --hidden-import pyaudio \
  voicebridge_app/main.py

# Patch Info.plist : NSMicrophoneUsageDescription + LSUIElement
PLIST="dist/VoiceBridge.app/Contents/Info.plist"
if [[ -f "$PLIST" ]]; then
  /usr/libexec/PlistBuddy -c "Add :NSMicrophoneUsageDescription string 'VoiceBridge a besoin du micro pour cloner votre voix.'" "$PLIST" 2>/dev/null || \
    /usr/libexec/PlistBuddy -c "Set :NSMicrophoneUsageDescription 'VoiceBridge a besoin du micro pour cloner votre voix.'" "$PLIST"
  /usr/libexec/PlistBuddy -c "Add :LSUIElement bool true" "$PLIST" 2>/dev/null || \
    /usr/libexec/PlistBuddy -c "Set :LSUIElement true" "$PLIST"
fi

echo ""
echo "✅ Build OK : dist/VoiceBridge.app"

if (( DO_ZIP )); then
  ( cd dist && zip -qr VoiceBridge.app.zip VoiceBridge.app )
  echo "✅ Archive : dist/VoiceBridge.app.zip"
  echo ""
  echo "Pour déployer sur ton VPS :"
  echo "  scp dist/VoiceBridge.app.zip root@TON_VPS:/var/voicebridge/data/install/"
  echo "  ssh root@TON_VPS 'chown voicebridge:voicebridge /var/voicebridge/data/install/VoiceBridge.app.zip'"
fi
