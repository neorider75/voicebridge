#!/usr/bin/env bash
# Désinstallation complète VoiceBridge.
# Usage : sudo ./uninstall.sh [--keep-data]
#
# Sans argument : supprime tout, y compris voices/ et config.json.
# --keep-data    : conserve /var/voicebridge/data (au cas où vous voudriez réinstaller).

set -euo pipefail

KEEP_DATA=0
for arg in "$@"; do
  case "$arg" in
    --keep-data) KEEP_DATA=1 ;;
    *) echo "Argument inconnu : $arg" >&2; exit 1 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "❌ À lancer en root (sudo)" >&2
  exit 1
fi

echo "Arrêt et désactivation du service…"
systemctl stop voicebridge 2>/dev/null || true
systemctl disable voicebridge 2>/dev/null || true
rm -f /etc/systemd/system/voicebridge.service
systemctl daemon-reload

echo "Suppression Nginx vhost + cron…"
rm -f /etc/nginx/sites-enabled/voicebridge /etc/nginx/sites-available/voicebridge
rm -f /etc/cron.d/voicebridge
nginx -s reload 2>/dev/null || true

if (( KEEP_DATA )); then
  echo "→ /var/voicebridge/data conservé"
  rm -rf /var/voicebridge/app /var/voicebridge/venv
else
  echo "Suppression de /var/voicebridge complet…"
  rm -rf /var/voicebridge
  userdel voicebridge 2>/dev/null || true
fi

echo "✅ Désinstallation terminée"
