#!/usr/bin/env bash
# VoiceBridge — installateur interactif pour Ubuntu 22.04 / 24.04 (VPS).
#
# Usage :
#   wget https://raw.githubusercontent.com/neorider75/voicebridge/main/Site/install/install.sh
#   chmod +x install.sh
#   sudo ./install.sh
#
# Doit être lancé en root sur un VPS fraîchement provisionné.
# Le script suit fidèlement Spec/voicebridge_specs/08-installation.md.

set -euo pipefail

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

REPO_URL="https://github.com/neorider75/voicebridge.git"
APP_DIR="/var/voicebridge/app"
DATA_DIR="/var/voicebridge/data"
VENV_DIR="/var/voicebridge/venv"
SERVICE_USER="voicebridge"
# HF_HOME : cache HuggingFace partagé installation/runtime. Permet de
# référencer les modèles par repo ID (ex: neuphonic/neutts-nano-french-q4-gguf)
# tout en gardant le contrôle de l'emplacement disque.
HF_CACHE_DIR="$DATA_DIR/models/hf-cache"
# PYTHON_BIN / PYTHON_PKG / PYTHON_VENV_PKG sont détectés dynamiquement
# en début de phase 4 (selon la version d'Ubuntu).
# Ubuntu 22.04 → python3.11 par défaut, 24.04 → python3.12 par défaut.
PYTHON_BIN=""
PYTHON_PKG=""
PYTHON_VENV_PKG=""
LOG_INSTALL="/var/log/voicebridge-install.log"

# Couleurs ANSI
C_RESET='\033[0m'
C_BOLD='\033[1m'
C_RED='\033[31m'
C_GREEN='\033[32m'
C_YELLOW='\033[33m'
C_BLUE='\033[34m'
C_CYAN='\033[36m'

# Composants installables (tous activés par défaut)
declare -A COMPONENTS=(
  [neutts_q4]=1   # NeuTTS Nano Q4 FR + EN
  [neutts_q8]=1   # NeuTTS Nano Q8 FR + EN
  [neucodec]=1    # NeuCodec
  [kyutai]=1      # Kyutai 1B STT
  [detection]=1   # Deepfake-audio-detection-V2
  [silero]=1      # Silero VAD
  [perth]=1       # Perth watermark (inclus avec neutts)
  [audio_tools]=1 # ffmpeg + yt-dlp
  [nginx_ssl]=1   # Nginx + Let's Encrypt
  [cron]=1        # Cron nettoyage
  [macos_app]=1   # Préparation VoiceBridge.app
)

# Variables remplies par les questions
DOMAIN=""
EMAIL=""
USER_PASSWORD=""
API_TOKEN=""

# Flags
MINIMAL=0
WITH_UFW=0
FRESH=0
for arg in "$@"; do
  case "$arg" in
    --minimal)
      MINIMAL=1
      ;;
    --with-ufw)
      WITH_UFW=1
      ;;
    --fresh)
      FRESH=1
      ;;
    -h|--help)
      cat <<EOF
Usage : sudo ./install.sh [--minimal] [--with-ufw] [--fresh]

Par défaut le script REPREND là où il s'est arrêté (sauf si --fresh).
Les phases déjà complétées (cf. /var/voicebridge/.install_state/) sont
sautées avec un message "déjà fait — skip".

  --minimal     N'installe que la livraison 1 (login + sécurité +
                Nginx/SSL + systemd) en sautant le téléchargement des
                modèles ML et la compilation de llama-cpp-python.
                Pour compléter l'installation plus tard, relancer le
                script SANS ce flag (idempotent).

  --with-ufw    Active le firewall UFW (par défaut : désactivé). Refuse
                tout en entrée sauf 22, 80, 443. ATTENTION : si un autre
                service expose un port public sur ce VPS (Docker, Node…),
                il deviendra inaccessible. Faites un audit avec
                'sudo ss -tlnp' avant de l'activer.

  --fresh       Repart de zéro : supprime l'état de checkpoint et
                réexécute toutes les phases. Ne supprime PAS les modèles
                déjà téléchargés ni /var/voicebridge/data/. Pour une
                désinstallation complète, utiliser uninstall.sh.
EOF
      exit 0
      ;;
    *)
      echo "Argument inconnu : $arg" >&2
      exit 1
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Checkpoint / reprise
# ---------------------------------------------------------------------------

STATE_DIR="/var/voicebridge/.install_state"

state_init() {
  if (( FRESH )); then
    rm -rf "$STATE_DIR"
  fi
  mkdir -p "$STATE_DIR"
  chmod 700 "$STATE_DIR"
}

is_done() {
  [[ -f "$STATE_DIR/$1.done" ]]
}

mark_done() {
  : > "$STATE_DIR/$1.done"
}

state_save_var() {
  printf '%s' "$2" > "$STATE_DIR/$1.txt"
  chmod 600 "$STATE_DIR/$1.txt"
}

state_load_var() {
  [[ -f "$STATE_DIR/$1.txt" ]] && cat "$STATE_DIR/$1.txt"
}

# Wrapper qui skip une phase si déjà faite
run_phase() {
  local key="$1"
  local fn="$2"
  if is_done "$key"; then
    echo -e "${C_GREEN}↷${C_RESET} $key déjà complétée — skip"
    return 0
  fi
  "$fn"
  mark_done "$key"
}

# ---------------------------------------------------------------------------
# Helpers d'affichage
# ---------------------------------------------------------------------------

banner() {
  echo
  echo -e "${C_CYAN}═══════════════════════════════════════════════════════${C_RESET}"
  echo -e "${C_CYAN}   $1${C_RESET}"
  echo -e "${C_CYAN}═══════════════════════════════════════════════════════${C_RESET}"
  echo
}

step()  { echo -e "${C_BLUE}▸${C_RESET} $1"; }
ok()    { echo -e "${C_GREEN}✅${C_RESET} $1"; }
warn()  { echo -e "${C_YELLOW}⚠${C_RESET}  $1"; }
fail()  { echo -e "${C_RED}❌ $1${C_RESET}" >&2; }

trap 'fail "Échec en ligne $LINENO. Voir $LOG_INSTALL pour les détails."' ERR

# ---------------------------------------------------------------------------
# Phase 1 — Vérifications préalables
# ---------------------------------------------------------------------------

phase1_checks() {
  banner "Phase 1 / 14 — Vérifications"

  if [[ $EUID -ne 0 ]]; then
    fail "Ce script doit être lancé en root (sudo)."
    exit 1
  fi
  ok "Lancé en root"

  if ! grep -qE 'Ubuntu (22\.04|24\.04)' /etc/os-release; then
    fail "OS non supporté. Requis : Ubuntu 22.04 ou 24.04."
    exit 1
  fi
  local ubuntu_version
  ubuntu_version=$(grep VERSION_ID /etc/os-release | cut -d'"' -f2)
  ok "Ubuntu $ubuntu_version détecté"

  local ram_gb
  ram_gb=$(awk '/MemTotal/ {printf "%.0f", $2/1024/1024}' /proc/meminfo)
  if (( ram_gb < 8 )); then
    fail "RAM insuffisante : ${ram_gb} Go (min 8 Go, recommandé 16 Go)."
    exit 1
  elif (( ram_gb < 16 )); then
    warn "RAM = ${ram_gb} Go (recommandé : 16 Go pour le confort)."
  else
    ok "RAM = ${ram_gb} Go"
  fi

  local free_gb
  free_gb=$(df -BG --output=avail / | tail -1 | tr -dc '0-9')
  if (( free_gb < 20 )); then
    fail "Espace disque insuffisant : ${free_gb} Go (min 20 Go)."
    exit 1
  fi
  ok "Espace disque libre = ${free_gb} Go"

  if ! ping -c 1 -W 3 huggingface.co >/dev/null 2>&1; then
    fail "Pas d'accès internet (huggingface.co injoignable)."
    exit 1
  fi
  ok "Connexion internet OK"
}

# ---------------------------------------------------------------------------
# Phase 2 — Questions interactives
# ---------------------------------------------------------------------------

ask_password() {
  while true; do
    read -rsp "▸ Mot de passe administrateur du panel web (min 8 caractères) : " p1; echo
    read -rsp "▸ Confirmer le mot de passe : " p2; echo
    if [[ "$p1" != "$p2" ]]; then
      warn "Les mots de passe ne correspondent pas."
      continue
    fi
    if (( ${#p1} < 8 )); then
      warn "Mot de passe trop court (min 8 caractères)."
      continue
    fi
    USER_PASSWORD="$p1"
    break
  done
  ok "Mot de passe accepté"
}

phase2_questions() {
  banner "Phase 2 / 14 — Configuration"

  # Reprise : si phase déjà passée, on restaure DOMAIN/EMAIL depuis l'état
  # et on ne re-pose le mdp QUE si phase 8 (config.json) est à refaire.
  if is_done phase2; then
    DOMAIN=$(state_load_var domain)
    EMAIL=$(state_load_var email)
    if [[ -z "$DOMAIN" || -z "$EMAIL" ]]; then
      warn "État corrompu — relancer avec --fresh"
      exit 1
    fi
    echo -e "${C_GREEN}↷${C_RESET} Reprise — domaine=$DOMAIN, email=$EMAIL"
    if ! is_done phase8; then
      warn "Phase 8 (config) à refaire : nouveau mot de passe nécessaire"
      ask_password
    fi
    return 0
  fi

  echo "Ce script va installer VoiceBridge sur ce VPS."
  echo "Durée estimée : 15 à 30 minutes selon votre connexion."
  echo

  while true; do
    read -rp "▸ Nom de domaine pointé vers ce VPS (ex: voicebridge.exemple.com) : " DOMAIN
    if [[ "$DOMAIN" =~ ^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$ ]]; then
      break
    fi
    warn "Domaine invalide. Réessayez."
  done

  while true; do
    read -rp "▸ Email pour Let's Encrypt (notifications de renouvellement) : " EMAIL
    if [[ "$EMAIL" =~ ^[^@]+@[^@]+\.[^@]+$ ]]; then
      break
    fi
    warn "Email invalide. Réessayez."
  done

  ask_password
  state_save_var domain "$DOMAIN"
  state_save_var email "$EMAIL"

  echo
  echo "Composants à installer (tous activés par défaut) :"
  echo "  - NeuTTS Nano Q4 FR + EN  (~260 Mo)"
  echo "  - NeuTTS Nano Q8 FR + EN  (~480 Mo)"
  echo "  - NeuCodec                (~50 Mo)"
  echo "  - Kyutai 1B (STT FR + EN) (~2 Go)"
  echo "  - Deepfake-audio-detection-V2 (~1.5 Go)"
  echo "  - Silero VAD              (~1 Mo)"
  echo "  - Perth (watermark)       (inclus avec NeuTTS)"
  echo "  - ffmpeg + yt-dlp"
  echo "  - Nginx + SSL Let's Encrypt"
  echo "  - Cron job nettoyage automatique"
  echo "  - VoiceBridge.app pour macOS (zip à embarquer)"
  echo
  echo "  Espace total estimé : ~5 Go"
  echo
  read -rp "▸ Confirmer ce choix et lancer l'installation ? [O/n] " confirm
  if [[ "${confirm,,}" == "n" ]]; then
    echo "Installation annulée."
    exit 0
  fi
}

# ---------------------------------------------------------------------------
# Phase 3 — Récapitulatif
# ---------------------------------------------------------------------------

phase3_recap() {
  banner "Phase 3 / 14 — Récapitulatif"
  cat <<EOF
  Domaine        : $DOMAIN
  Email SSL      : $EMAIL
  Composants     : tous installés
  Dossier app    : $APP_DIR
  Dossier data   : $DATA_DIR
  User Linux     : $SERVICE_USER
  Service        : systemd voicebridge.service
  Logs           : $DATA_DIR/logs/

EOF
  read -rp "Confirmer l'installation ? [O/n] " confirm
  if [[ "${confirm,,}" == "n" ]]; then
    echo "Installation annulée."
    exit 0
  fi
}

# ---------------------------------------------------------------------------
# Phase 4 — Installation système
# ---------------------------------------------------------------------------

detect_python() {
  # Sélectionne la première version Python ≥ 3.11 disponible dans apt
  # (le code applicatif est compatible 3.11 et 3.12). N'utilise PAS de PPA tiers.
  for v in 3.12 3.11; do
    if apt-cache show "python$v" >/dev/null 2>&1; then
      PYTHON_BIN="python$v"
      PYTHON_PKG="python$v"
      PYTHON_VENV_PKG="python$v-venv"
      ok "Python détecté : $PYTHON_BIN (paquet $PYTHON_PKG)"
      return 0
    fi
  done
  fail "Aucun python3.12 ni python3.11 disponible dans les dépôts apt."
  fail "Sur Ubuntu < 22.04 il faudrait ajouter le PPA deadsnakes — non supporté ici."
  exit 1
}

phase4_system() {
  banner "Phase 4 / 14 — Paquets système (apt)"

  step "apt update + upgrade"
  apt-get update -y
  DEBIAN_FRONTEND=noninteractive apt-get upgrade -y

  # detect_python est désormais appelé depuis main() (avant run_phase) pour
  # rester valide même à la reprise (phase 4 skippée).

  step "Installation des paquets système"
  DEBIAN_FRONTEND=noninteractive apt-get install -y \
    "$PYTHON_PKG" "$PYTHON_VENV_PKG" python3-pip \
    build-essential cmake pkg-config \
    libopenblas-dev libblas-dev liblapack-dev \
    ffmpeg \
    espeak-ng \
    nginx certbot python3-certbot-nginx \
    ufw fail2ban \
    git curl wget unzip zip \
    libmagic1
  ok "Paquets système installés"

  step "Création de l'utilisateur Linux dédié '$SERVICE_USER'"
  if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    useradd -r -s /bin/false -m -d "/var/$SERVICE_USER" "$SERVICE_USER"
  fi

  step "Création de l'arborescence /var/voicebridge"
  mkdir -p "$DATA_DIR"/{voices,voices/encoded,audio,models,install,logs,tmp}
  mkdir -p "$HF_CACHE_DIR"
  chown -R "$SERVICE_USER:$SERVICE_USER" /var/voicebridge
  ok "Arborescence créée"
}

# ---------------------------------------------------------------------------
# Phase 5 — Code applicatif
# ---------------------------------------------------------------------------

phase5_app_code() {
  banner "Phase 5 / 14 — Récupération du code applicatif"

  if [[ -d "$APP_DIR/.git" ]]; then
    step "Mise à jour du dépôt existant"
    sudo -u "$SERVICE_USER" git -C "$APP_DIR" fetch --all
    sudo -u "$SERVICE_USER" git -C "$APP_DIR" reset --hard origin/main
  else
    step "Clone de $REPO_URL"
    rm -rf "$APP_DIR"
    sudo -u "$SERVICE_USER" git clone "$REPO_URL" "$APP_DIR"
  fi
  ok "Code récupéré dans $APP_DIR"

  step "Création du virtualenv Python"
  $PYTHON_BIN -m venv "$VENV_DIR"
  chown -R "$SERVICE_USER:$SERVICE_USER" "$VENV_DIR"

  step "Installation des dépendances Python"
  sudo -u "$SERVICE_USER" "$VENV_DIR/bin/pip" install --upgrade pip
  if (( MINIMAL )); then
    warn "Mode --minimal : seulement les deps légères (login + sécurité + Nginx)."
    sudo -u "$SERVICE_USER" "$VENV_DIR/bin/pip" install -r "$APP_DIR/Site/backend/requirements-minimal.txt"
  else
    sudo -u "$SERVICE_USER" "$VENV_DIR/bin/pip" install -r "$APP_DIR/Site/backend/requirements.txt"
    # NB : la recompilation llama-cpp-python avec OpenBLAS échoue parfois
    # selon la combinaison Ubuntu/CMake/llama.cpp (ggml-blas CMakeLists). Le
    # wheel par défaut a déjà les optimisations CPU x86_64 standard.
    # → À ré-évaluer si la perf TTS Q8 est insuffisante :
    #     CMAKE_ARGS="-DGGML_BLAS=ON -DGGML_BLAS_VENDOR=OpenBLAS" \
    #       pip install llama-cpp-python --force-reinstall --no-cache-dir
    #   nécessite : pkg-config + libblas-dev + liblapack-dev installés (déjà fait).
    step "llama-cpp-python : wheel par défaut (optimisations CPU standard)"
  fi
  ok "Dépendances Python installées"
}

# ---------------------------------------------------------------------------
# Phase 6 — Téléchargement des modèles ML
# ---------------------------------------------------------------------------

hf_download() {
  # Télécharge un repo HF dans le cache standard (HF_HOME) — pas via
  # --local-dir. Permet aux libs (neutts, transformers) de résoudre le
  # repo par ID au lieu de devoir leur passer un chemin filesystem.
  local repo="$1"
  step "↓ $repo (cache : $HF_CACHE_DIR)"
  if [[ -x "$VENV_DIR/bin/hf" ]]; then
    sudo -u "$SERVICE_USER" \
      env "HF_HOME=$HF_CACHE_DIR" "HUGGINGFACE_HUB_CACHE=$HF_CACHE_DIR/hub" \
      "$VENV_DIR/bin/hf" download "$repo" --quiet
  else
    sudo -u "$SERVICE_USER" \
      env "HF_HOME=$HF_CACHE_DIR" "HUGGINGFACE_HUB_CACHE=$HF_CACHE_DIR/hub" \
      "$VENV_DIR/bin/huggingface-cli" download "$repo" --quiet
  fi
}

phase6_models() {
  banner "Phase 6 / 14 — Téléchargement des modèles ML (10-15 min)"

  if (( MINIMAL )); then
    warn "Mode --minimal : phase 6 sautée. Relance sans --minimal pour télécharger les modèles."
    return 0
  fi

  sudo -u "$SERVICE_USER" "$VENV_DIR/bin/pip" install --quiet huggingface-hub

  # Tout télécharger dans le cache HF standard ($HF_CACHE_DIR/hub).
  # Les libs Python résolvent ensuite par repo ID, sans connaître le chemin disque.
  hf_download neuphonic/neutts-nano-french-q4-gguf
  hf_download neuphonic/neutts-nano-q4-gguf
  hf_download neuphonic/neutts-nano-french-q8-gguf
  hf_download neuphonic/neutts-nano-q8-gguf
  hf_download neuphonic/neucodec
  hf_download kyutai/stt-1b-en_fr-trfs
  hf_download MelodyMachine/Deepfake-audio-detection-V2

  step "Pré-chargement Silero VAD via torch.hub"
  sudo -u "$SERVICE_USER" "$VENV_DIR/bin/python" -c \
    "import torch; torch.hub.load('snakers4/silero-vad', 'silero_vad', trust_repo=True)"

  ok "Tous les modèles téléchargés"
}

# ---------------------------------------------------------------------------
# Phase 7 — Voix par défaut (Juliette FR + Dave EN)
# ---------------------------------------------------------------------------

phase7_default_voices() {
  banner "Phase 7 / 14 — Voix par défaut (Juliette + Dave)"

  if (( MINIMAL )); then
    warn "Mode --minimal : phase 7 sautée."
    # On crée tout de même un metadata.json vide pour que /api/voices ne crashe pas.
    if [[ ! -f "$DATA_DIR/voices/metadata.json" ]]; then
      echo '{"voices": []}' > "$DATA_DIR/voices/metadata.json"
      chown "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR/voices/metadata.json"
    fi
    return 0
  fi

  local samples_dir="/tmp/neutts-air-samples"
  step "Récupération des samples NeuTTS officiels"
  rm -rf "$samples_dir"
  git clone --depth 1 https://github.com/neuphonic/neutts-air "$samples_dir"

  for voice in juliette dave; do
    if [[ -f "$samples_dir/samples/${voice}.wav" ]]; then
      cp "$samples_dir/samples/${voice}.wav" "$DATA_DIR/voices/${voice}.wav"
      [[ -f "$samples_dir/samples/${voice}.txt" ]] && \
        cp "$samples_dir/samples/${voice}.txt" "$DATA_DIR/voices/${voice}.txt"
      ok "Voix $voice copiée"
    else
      warn "samples/${voice}.wav introuvable dans neutts-air"
    fi
  done

  step "Pré-encodage des ref_codes (.pt) — peut prendre 1-2 min"
  # HF_HOME est exporté pour que NeuTTS résolve les repo IDs depuis le cache
  # local (sinon il tenterait de re-télécharger).
  sudo -u "$SERVICE_USER" \
    env "VB_DATA_DIR=$DATA_DIR" "HF_HOME=$HF_CACHE_DIR" \
        "HUGGINGFACE_HUB_CACHE=$HF_CACHE_DIR/hub" \
    "$VENV_DIR/bin/python" - <<'PY'
import os
import torch
from pathlib import Path
DATA = Path(os.environ["VB_DATA_DIR"])
try:
    from neutts import NeuTTS  # type: ignore
except ImportError:
    from neuttsair.neutts import NeuTTSAir as NeuTTS  # type: ignore
# On passe les repo IDs HuggingFace : NeuTTS reconnaît "neuphonic/..." comme
# officiel, infère le format GGUF + la langue, et utilise le cache HF local
# (HF_HOME ci-dessus) sans tenter un re-download.
mapping = {
    "juliette": "neuphonic/neutts-nano-french-q4-gguf",
    "dave":     "neuphonic/neutts-nano-q4-gguf",
}
for voice, backbone_repo in mapping.items():
    wav = DATA / f"voices/{voice}.wav"
    out = DATA / f"voices/encoded/{voice}.pt"
    if not wav.exists():
        print(f"[skip] {wav} absent")
        continue
    tts = NeuTTS(
        backbone_repo=backbone_repo,
        codec_repo="neuphonic/neucodec",
    )
    codes = tts.encode_reference(str(wav))
    out.parent.mkdir(parents=True, exist_ok=True)
    torch.save(codes, out)
    print(f"[ok] {out}")
PY

  step "Création de voices/metadata.json"
  cat > "$DATA_DIR/voices/metadata.json" <<EOF
{
  "voices": [
    {
      "id": "juliette",
      "name": "Juliette",
      "language": "fr",
      "backbone": "neutts-nano-french",
      "duration_seconds": 11,
      "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
      "protected": true
    },
    {
      "id": "dave",
      "name": "Dave",
      "language": "en",
      "backbone": "neutts-nano",
      "duration_seconds": 13,
      "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
      "protected": true
    }
  ]
}
EOF
  chown -R "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR/voices"
  chmod 700 "$DATA_DIR/voices"
  rm -rf "$samples_dir"
  ok "Voix par défaut installées"
}

# ---------------------------------------------------------------------------
# Phase 8 — Configuration applicative (config.json + clé API)
# ---------------------------------------------------------------------------

phase8_config() {
  banner "Phase 8 / 14 — Configuration applicative"

  step "Génération des secrets"
  local pw_hash sess_secret api_token api_token_hash
  pw_hash=$("$VENV_DIR/bin/python" -c "from passlib.hash import bcrypt; print(bcrypt.hash('$USER_PASSWORD', rounds=12))")
  sess_secret=$("$VENV_DIR/bin/python" -c "import secrets; print(secrets.token_hex(32))")
  api_token=$("$VENV_DIR/bin/python" -c "import secrets; print('sk-' + secrets.token_hex(16))")
  api_token_hash=$("$VENV_DIR/bin/python" -c "import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" "$api_token")
  API_TOKEN="$api_token"

  step "Écriture de $DATA_DIR/config.json (chmod 600)"
  cat > "$DATA_DIR/config.json" <<EOF
{
  "domain": "$DOMAIN",
  "password_hash": "$pw_hash",
  "api_token_hash": "$api_token_hash",
  "api_token_created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "session_secret": "$sess_secret",
  "default_retention": "session",
  "model_unload_after_minutes": 15
}
EOF
  chown "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR/config.json"
  chmod 600 "$DATA_DIR/config.json"
  ok "config.json créé"

  echo
  echo -e "${C_CYAN}═══════════════════════════════════════════════════════${C_RESET}"
  echo -e "${C_CYAN}   VOTRE CLÉ API VoiceBridge${C_RESET}"
  echo -e "${C_CYAN}═══════════════════════════════════════════════════════${C_RESET}"
  echo
  echo -e "   ${C_BOLD}$API_TOKEN${C_RESET}"
  echo
  echo "   ⚠️  Notez-la maintenant. Elle ne sera plus affichée."
  echo "   Elle est nécessaire pour configurer l'app macOS."
  echo
  echo "   Vous pouvez en générer une nouvelle depuis"
  echo "   l'interface web : Réglages → API"
  echo
  echo -e "${C_CYAN}═══════════════════════════════════════════════════════${C_RESET}"
  echo
  read -rp "   Appuyez sur Entrée pour continuer..."
}

# ---------------------------------------------------------------------------
# Phase 9 — VoiceBridge.app pour macOS (livraison 7 — placeholder)
# ---------------------------------------------------------------------------

phase9_macos_app() {
  banner "Phase 9 / 14 — Préparation VoiceBridge.app pour macOS"

  # Le bundle est versionné dans le repo : Site/macos-app/release/VoiceBridge.app.zip
  # (généré par Site/macos-app/build.sh --zip côté Mac).
  local bundle="$APP_DIR/Site/macos-app/release/VoiceBridge.app.zip"
  if [[ ! -f "$bundle" ]]; then
    warn "release/VoiceBridge.app.zip absent du repo."
    warn "Build local : Site/macos-app/build.sh --zip puis git push."
    return 0
  fi

  step "Patch du config.json embarqué (server_url = https://$DOMAIN)"
  local tmp_app="/tmp/VoiceBridge.app.build"
  rm -rf "$tmp_app"
  mkdir -p "$tmp_app"
  unzip -q "$bundle" -d "$tmp_app"

  # Le config.json embarqué dans le bundle est patché avec l'URL réelle du VPS.
  cat > "$tmp_app/VoiceBridge.app/Contents/Resources/config.json" <<EOF
{
  "server_url": "https://$DOMAIN",
  "version": "1.0.0"
}
EOF

  ( cd "$tmp_app" && zip -qr VoiceBridge.app.zip VoiceBridge.app )
  cp "$tmp_app/VoiceBridge.app.zip" "$DATA_DIR/install/"
  rm -rf "$tmp_app"
  chown -R "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR/install"
  ok "VoiceBridge.app.zip déposé dans $DATA_DIR/install/ (config.json patché)"
}

# ---------------------------------------------------------------------------
# Phase 10 — Nginx + SSL Let's Encrypt
# ---------------------------------------------------------------------------

phase10_nginx() {
  banner "Phase 10 / 14 — Nginx + SSL"

  step "Écriture de /etc/nginx/sites-available/voicebridge"
  cat > /etc/nginx/sites-available/voicebridge <<EOF
server {
    listen 80;
    server_name $DOMAIN;
    return 301 https://\$host\$request_uri;
}

server {
    listen 443 ssl http2;
    server_name $DOMAIN;

    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Frame-Options "DENY" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    autoindex off;
    server_tokens off;

    client_max_body_size 60M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 300s;
    }

    location /ws/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_read_timeout 86400;
    }
}
EOF

  ln -sf /etc/nginx/sites-available/voicebridge /etc/nginx/sites-enabled/voicebridge
  rm -f /etc/nginx/sites-enabled/default

  step "Émission du certificat Let's Encrypt"
  certbot --nginx -d "$DOMAIN" --email "$EMAIL" --agree-tos --non-interactive --redirect

  step "Test config Nginx + reload"
  nginx -t
  systemctl reload nginx
  ok "Nginx + SSL prêts"
}

# ---------------------------------------------------------------------------
# Phase 11 — systemd voicebridge.service
# ---------------------------------------------------------------------------

phase11_systemd() {
  banner "Phase 11 / 14 — Service systemd"

  install -m 0644 "$APP_DIR/Site/backend/voicebridge.service" /etc/systemd/system/voicebridge.service
  systemctl daemon-reload
  systemctl enable voicebridge
  systemctl restart voicebridge

  sleep 3
  if systemctl is-active --quiet voicebridge; then
    ok "Service voicebridge actif"
  else
    fail "Le service voicebridge n'a pas démarré. Voir : journalctl -u voicebridge"
    exit 1
  fi
}

# ---------------------------------------------------------------------------
# Phase 12 — Firewall + fail2ban
# ---------------------------------------------------------------------------

phase12_firewall() {
  banner "Phase 12 / 14 — fail2ban + UFW (optionnel)"

  # fail2ban (toujours activé : ne change rien d'autre que la défense SSH)
  step "Configuration fail2ban (anti-bruteforce SSH)"
  cat > /etc/fail2ban/jail.local <<EOF
[sshd]
enabled = true
port = 22
maxretry = 5
bantime = 3600
EOF
  systemctl enable fail2ban
  systemctl restart fail2ban
  ok "fail2ban configuré"

  # UFW : opt-in via --with-ufw
  if (( WITH_UFW )); then
    step "Activation UFW (firewall) — flag --with-ufw"
    warn "Vérifiez que vos autres services écoutent sur 22/80/443 ou sont"
    warn "publiés via Docker (qui contourne UFW). En cas de doute :"
    warn "  sudo ss -tlnp | grep LISTEN"
    ufw default deny incoming
    ufw default allow outgoing
    ufw allow 22/tcp
    ufw allow 80/tcp
    ufw allow 443/tcp
    ufw --force enable
    ok "UFW actif"
  else
    warn "UFW NON activé (par défaut). Pour l'activer plus tard :"
    warn "  sudo ufw default deny incoming && sudo ufw default allow outgoing"
    warn "  sudo ufw allow 22/tcp 80/tcp 443/tcp && sudo ufw --force enable"
    warn "Ou relancer : sudo ./install.sh --with-ufw"
  fi
}

# ---------------------------------------------------------------------------
# Phase 13 — Cron jobs
# ---------------------------------------------------------------------------

phase13_cron() {
  banner "Phase 13 / 14 — Cron jobs"

  cat > /etc/cron.d/voicebridge <<EOF
# Nettoyage horaire des fichiers audio expirés
0 * * * * $SERVICE_USER $VENV_DIR/bin/python $APP_DIR/Site/backend/manage.py cleanup-expired

# Renouvellement Let's Encrypt (3h du matin)
0 3 * * * root certbot renew --quiet --post-hook "systemctl reload nginx"
EOF
  ok "Cron jobs installés"
}

# ---------------------------------------------------------------------------
# Phase 14 — Récap final
# ---------------------------------------------------------------------------

phase14_recap() {
  banner "✅ Installation terminée"
  cat <<EOF

  Interface web : https://$DOMAIN

  Identifiants :
    - Mot de passe : (celui que vous avez choisi)

  Clé API VoiceBridge.app :
    - Affichée plus haut dans le terminal
    - Régénérable depuis Réglages → API

  Logs    : tail -f $DATA_DIR/logs/app.log
  Service : systemctl status voicebridge
  Restart : systemctl restart voicebridge

  Étapes suivantes :
  1. Ouvrez https://$DOMAIN dans votre navigateur
  2. Connectez-vous avec votre mot de passe
  3. Téléchargez VoiceBridge.app (Réglages → Installation)
  4. Configurez Teams pour utiliser BlackHole comme micro

EOF
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
  : > "$LOG_INSTALL"
  exec > >(tee -a "$LOG_INSTALL") 2>&1

  banner "VoiceBridge — Installation"
  state_init
  if (( FRESH )); then
    warn "Mode --fresh : checkpoint effacé, réexécution complète"
  elif [[ -d "$STATE_DIR" && -n "$(ls -A "$STATE_DIR" 2>/dev/null)" ]]; then
    echo -e "${C_GREEN}↷${C_RESET} Reprise détectée (cf. $STATE_DIR)"
  fi

  # phase1 : non checkpointée (vérifs idempotentes, toujours exécutées)
  phase1_checks
  # phase2 : gère elle-même la reprise (mdp re-demandé si phase8 à refaire)
  phase2_questions
  mark_done phase2
  # phase3 (récap + confirm) : skippée en reprise (l'utilisateur a déjà confirmé)
  if ! is_done phase3; then
    phase3_recap
    mark_done phase3
  fi

  # detect_python doit s'exécuter à chaque run (sert aux phases 5+ qui peuvent
  # être ré-exécutées en reprise même si phase 4 est déjà skippée).
  detect_python

  run_phase phase4  phase4_system
  run_phase phase5  phase5_app_code
  run_phase phase6  phase6_models
  run_phase phase7  phase7_default_voices
  run_phase phase8  phase8_config
  run_phase phase9  phase9_macos_app
  run_phase phase10 phase10_nginx
  run_phase phase11 phase11_systemd
  run_phase phase12 phase12_firewall
  run_phase phase13 phase13_cron

  phase14_recap
}

main "$@"
