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
SKIP_CLOUD=0
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
    --skip-cloud)
      SKIP_CLOUD=1
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

  --skip-cloud  Saute la phase 14 (configuration RunPod + OpenAI). Les
                clés peuvent être saisies plus tard via l'UI Réglages →
                Cloud, ou via "manage.py set-runpod-config".
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
  banner "Phase 1 / 15 — Vérifications"

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
  banner "Phase 2 / 15 — Configuration"

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
  banner "Phase 3 / 15 — Récapitulatif"
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
  banner "Phase 4 / 15 — Paquets système (apt)"

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

  # Vérification finale : si l'un des binaires runtime critiques manque,
  # on bail tout de suite (mieux qu'un 500 opaque au runtime des mois plus tard).
  step "Vérification des binaires runtime"
  local missing=()
  for bin in ffmpeg nginx; do
    if ! command -v "$bin" >/dev/null 2>&1; then
      missing+=("$bin")
    fi
  done
  if (( ${#missing[@]} > 0 )); then
    fail "Binaires critiques manquants : ${missing[*]} — apt install a probablement échoué partiellement."
    exit 1
  fi
  ok "ffmpeg, nginx présents dans le PATH"

  step "Création de l'utilisateur Linux dédié '$SERVICE_USER'"
  if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    useradd -r -s /bin/false -m -d "/var/$SERVICE_USER" "$SERVICE_USER"
  fi

  step "Création de l'arborescence /var/voicebridge"
  mkdir -p "$DATA_DIR"/{voices,voices/encoded,audio,models,install,logs,tmp}
  mkdir -p "$HF_CACHE_DIR"
  # Caches inscriptibles (numba/librosa, matplotlib, XTTS, etc.) — le venv
  # est en lecture seule sous ProtectSystem=strict, il faut un emplacement
  # dédié pour les libs qui écrivent dans ~/.cache, ~/.local/share, ~/.config
  # ou directement HOME (cas de Coqui XTTS au premier load_model).
  mkdir -p "$DATA_DIR"/cache/{numba,matplotlib,xdg-data,xdg-config,home}
  mkdir -p "$DATA_DIR"/models/tts-cache
  chown -R "$SERVICE_USER:$SERVICE_USER" /var/voicebridge
  ok "Arborescence créée"
}

# ---------------------------------------------------------------------------
# Phase 5 — Code applicatif
# ---------------------------------------------------------------------------

phase5_app_code() {
  banner "Phase 5 / 15 — Récupération du code applicatif"

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
    # On installe coqui-tts en deux temps à cause du conflit fondamental sur
    # transformers : neutts veut ~=4.56, coqui-tts force >=4.57. En pratique
    # coqui-tts marche avec 4.56 (isin_mps_friendly existe depuis 4.45),
    # mais pip ne nous laisse pas faire en mode normal. On installe d'abord
    # tout sauf coqui-tts (avec --no-deps + filtrage) puis coqui-tts en
    # --no-deps pour conserver transformers 4.56.x.
    step "Dépendances principales (sans coqui-tts)"
    sudo -u "$SERVICE_USER" "$VENV_DIR/bin/pip" install \
      $(grep -v "^coqui-tts" "$APP_DIR/Site/backend/requirements.txt" | grep -v "^#" | grep -v "^$")

    step "coqui-tts (XTTS-v2 engine alternatif) avec --no-deps pour préserver transformers 4.56"
    sudo -u "$SERVICE_USER" "$VENV_DIR/bin/pip" install --no-deps coqui-tts==0.27.5
    # Deps de coqui-tts qu'on doit installer manuellement (sans transformers
    # qui resterait sur 4.56). gruut[de,es,fr] = phonemizers multilingues
    # utilisés par XTTS. torchcodec est requis depuis PyTorch 2.9 pour
    # l'audio IO de TTS.api (sinon ImportError au runtime).
    sudo -u "$SERVICE_USER" "$VENV_DIR/bin/pip" install \
      coqpit-config coqui-tts-trainer pysbd inflect num2words anyascii \
      monotonic-alignment-search "gruut[de,es,fr]" matplotlib torchcodec

    # Re-pin transformers/numpy si une dep secondaire les a bumpés.
    step "Re-pin transformers/numpy/fsspec après cascade pip"
    sudo -u "$SERVICE_USER" "$VENV_DIR/bin/pip" install --force-reinstall \
      "transformers~=4.56.1" "numpy~=2.2.6" "fsspec==2026.2.0" "huggingface-hub<1.0"

    # NB : la recompilation llama-cpp-python avec OpenBLAS échoue parfois
    # selon la combinaison Ubuntu/CMake/llama.cpp (ggml-blas CMakeLists).
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
  banner "Phase 6 / 15 — Téléchargement des modèles ML (10-15 min)"

  if (( MINIMAL )); then
    warn "Mode --minimal : phase 6 sautée. Relance sans --minimal pour télécharger les modèles."
    return 0
  fi

  sudo -u "$SERVICE_USER" "$VENV_DIR/bin/pip" install --quiet huggingface-hub

  # Tout télécharger dans le cache HF standard ($HF_CACHE_DIR/hub).
  # Les libs Python résolvent ensuite par repo ID, sans connaître le chemin disque.
  # On reste sur la famille nano (FR + EN) — c'est ce qui a été utilisé
  # pour la démo officielle Neuphonic (NeuTTS-Nano-V4.mp4). Air (0.7B)
  # est dispo mais on garde Nano (0.2B) comme baseline.
  hf_download neuphonic/neutts-nano-french-q4-gguf
  hf_download neuphonic/neutts-nano-french-q8-gguf
  hf_download neuphonic/neutts-nano-q4-gguf
  hf_download neuphonic/neutts-nano-q8-gguf
  hf_download neuphonic/neucodec
  hf_download kyutai/stt-1b-en_fr-trfs
  hf_download MelodyMachine/Deepfake-audio-detection-V2

  step "Pré-chargement Silero VAD via torch.hub"
  sudo -u "$SERVICE_USER" "$VENV_DIR/bin/python" -c \
    "import torch; torch.hub.load('snakers4/silero-vad', 'silero_vad', trust_repo=True)"

  # XTTS-v2 (Coqui) — engine TTS alternatif, bien plus naturel que NeuTTS
  # Nano. Téléchargement par la lib TTS elle-même au premier load(), donc on
  # fait un load à blanc ici pour pré-cacher (~3 Go dans ~/.local/share/tts/).
  step "Pré-téléchargement Coqui XTTS-v2 (~3 Go)"
  sudo -u "$SERVICE_USER" \
    env "HF_HOME=$HF_CACHE_DIR" "HUGGINGFACE_HUB_CACHE=$HF_CACHE_DIR/hub" \
        "TTS_HOME=$DATA_DIR/models/tts-cache" \
    "$VENV_DIR/bin/python" -c \
    "from TTS.api import TTS; TTS('tts_models/multilingual/multi-dataset/xtts_v2')" \
    || warn "XTTS-v2 download échoué — non bloquant, sera retenté à la 1re inférence"

  ok "Tous les modèles téléchargés"
}

# ---------------------------------------------------------------------------
# Phase 7 — Voix par défaut (Juliette FR + Dave EN)
# ---------------------------------------------------------------------------

phase7_default_voices() {
  banner "Phase 7 / 15 — Voix par défaut (Juliette + Dave)"

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
  banner "Phase 8 / 15 — Configuration applicative"

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
  banner "Phase 9 / 15 — Préparation VoiceBridge.app pour macOS"

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
  banner "Phase 10 / 15 — Nginx + SSL"

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
  banner "Phase 11 / 15 — Service systemd"

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
  banner "Phase 12 / 15 — fail2ban + UFW (optionnel)"

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
  banner "Phase 13 / 15 — Cron jobs"

  cat > /etc/cron.d/voicebridge <<EOF
# Nettoyage horaire des fichiers audio expirés
0 * * * * $SERVICE_USER $VENV_DIR/bin/python $APP_DIR/Site/backend/manage.py cleanup-expired

# Renouvellement Let's Encrypt (3h du matin)
0 3 * * * root certbot renew --quiet --post-hook "systemctl reload nginx"
EOF
  ok "Cron jobs installés"
}

# ---------------------------------------------------------------------------
# Phase 14 — Configuration cloud (RunPod + OpenAI, optionnel V3)
# ---------------------------------------------------------------------------

# Helper Python : chiffre une chaîne avec services/secrets.py et patche
# config.json (utilise la master key auto-bootstrappée).
_vb_set_encrypted() {
  # Args : <config_key> <plaintext>
  # On passe le plaintext via stdin pour ne pas fuiter dans la liste des
  # arguments visible par 'ps'.
  local key="$1"
  local plaintext="$2"
  printf '%s' "$plaintext" | sudo -u "$SERVICE_USER" \
    "$VENV_DIR/bin/python" -c "
import sys
sys.path.insert(0, '$APP_DIR/Site/backend')
from app.services import secrets as s
from app import config as cfg
plain = sys.stdin.read()
cfg.set_many({'$key': s.encrypt(plain)})
print('OK')
" >/dev/null
}

_vb_set_plain() {
  local key="$1"
  local val="$2"
  sudo -u "$SERVICE_USER" \
    "$VENV_DIR/bin/python" -c "
import sys
sys.path.insert(0, '$APP_DIR/Site/backend')
from app import config as cfg
cfg.set_many({'$key': '''${val//\'/\\\'}'''})
" >/dev/null
}

_vb_test_runpod() {
  sudo -u "$SERVICE_USER" \
    "$VENV_DIR/bin/python" -c "
import sys
sys.path.insert(0, '$APP_DIR/Site/backend')
from app.services import runpod_client
try:
    r = runpod_client.ping()
    print('OK', r.get('latency_ms'), r.get('datacenter'))
except Exception as e:
    print('FAIL', e)
    sys.exit(1)
"
}

_vb_test_openai() {
  sudo -u "$SERVICE_USER" \
    "$VENV_DIR/bin/python" -c "
import sys
sys.path.insert(0, '$APP_DIR/Site/backend')
from app.services import openai_client
try:
    r = openai_client.ping()
    print('OK', r.get('latency_ms'))
except Exception as e:
    print('FAIL', e)
    sys.exit(1)
"
}

phase14_cloud_config() {
  banner "Phase 14 / 15 — Configuration cloud (RunPod + OpenAI, optionnel)"

  if (( SKIP_CLOUD )); then
    warn "Mode --skip-cloud : phase sautée. Configurez plus tard via UI Réglages → Cloud."
    return 0
  fi

  cat <<EOF
La V3 ajoute trois modes Live qui requièrent un GPU (RunPod) :
  • gpu-clone   — ta voix multilingue
  • gpu-native  — voix native dans la langue cible
  • gpu-hybrid  — ta voix avec accent natif (RVC)

Et la traduction haute qualité GPT-4o(-mini) via OpenAI (optionnel).

Si tu n'as pas encore de compte RunPod / OpenAI, tu peux saisir les clés
plus tard depuis Réglages → Cloud (web UI). Le mode V1 cpu-fr-en marche
sans aucune de ces clés.

EOF

  read -rp "Configurer maintenant ? [Y/n] : " configure_now
  configure_now=${configure_now:-Y}
  if [[ ! "$configure_now" =~ ^[Yy]$ ]]; then
    warn "Phase cloud sautée — configurable plus tard via UI."
    return 0
  fi

  # ── RunPod ──────────────────────────────────────────────────────
  echo
  echo -e "${C_BOLD}── RunPod Serverless ──${C_RESET}"
  echo
  echo "Pré-requis (à faire dans la console runpod.io AVANT de continuer) :"
  echo "  1. Créer un compte sur https://runpod.io"
  echo "  2. Storage → New Network Volume — taille 30 Go suffit, datacenter"
  echo "     EU-FR-1 (recommandé pour latence FR) ou EU-RO-1"
  echo "  3. ⚠️  Au déploiement de l'endpoint Serverless, configurer le"
  echo "     mount path explicitement à /runpod-volume (PAS le défaut UI"
  echo "     /workspace — sinon le worker ne trouve aucun modèle)"
  echo "  4. Settings → API Keys → Create"
  echo "  5. Storage → ton Volume → S3 Credentials → Create"
  echo "  6. Pré-télécharger les modèles (~17 Go) dans le Volume :"
  echo "     voir runpod-worker/README.md (utiliser hf download avec --include)"
  echo

  read -rp "As-tu un compte RunPod prêt ? [y/N] : " has_runpod
  if [[ "$has_runpod" =~ ^[Yy]$ ]]; then
    local rp_key rp_endpoint rp_volume rp_dc rp_s3a rp_s3s

    read -rsp "▸ RunPod API key (rpa_…) : " rp_key; echo
    read -rp  "▸ Endpoint ID Serverless        : " rp_endpoint
    read -rp  "▸ Volume ID                     : " rp_volume
    read -rp  "▸ Datacenter [EU-FR-1] : " rp_dc
    rp_dc=${rp_dc:-EU-FR-1}
    read -rsp "▸ S3 access key                 : " rp_s3a; echo
    read -rsp "▸ S3 secret key                 : " rp_s3s; echo

    if [[ -n "$rp_key" ]]; then
      step "Chiffrement RunPod API key"
      _vb_set_encrypted "runpod_api_key_encrypted" "$rp_key"
    fi
    if [[ -n "$rp_endpoint" ]]; then
      _vb_set_plain "runpod_endpoint_id" "$rp_endpoint"
    fi
    if [[ -n "$rp_volume" ]]; then
      _vb_set_plain "runpod_volume_id" "$rp_volume"
    fi
    if [[ -n "$rp_dc" ]]; then
      _vb_set_plain "runpod_datacenter" "$rp_dc"
    fi
    if [[ -n "$rp_s3a" ]]; then
      _vb_set_encrypted "runpod_s3_access_key_encrypted" "$rp_s3a"
    fi
    if [[ -n "$rp_s3s" ]]; then
      _vb_set_encrypted "runpod_s3_secret_key_encrypted" "$rp_s3s"
    fi
    ok "Configuration RunPod sauvegardée (chiffrée Fernet)"

    echo
    step "Test de connexion RunPod (peut prendre 10-30s avec cold start)…"
    if _vb_test_runpod; then
      ok "RunPod OK"
    else
      warn "Test échoué. Vérifie les clés depuis Réglages → Cloud → Tester."
      warn "L'installation continue (les clés sont stockées, juste non testées)."
    fi
  else
    warn "RunPod non configuré — modes GPU indisponibles tant que les clés ne"
    warn "sont pas saisies via Réglages → Cloud."
  fi

  # ── OpenAI ──────────────────────────────────────────────────────
  echo
  echo -e "${C_BOLD}── OpenAI (traduction GPT-4o-mini / GPT-4o) ──${C_RESET}"
  echo
  echo "OpenAI est optionnel : il améliore la qualité de traduction Live"
  echo "avec contexte conversationnel et briefings métier. Sans clé, NLLB"
  echo "(GPU) et OPUS-MT (CPU local) restent disponibles gratuitement."
  echo

  read -rp "As-tu une clé OpenAI ? [y/N] : " has_openai
  if [[ "$has_openai" =~ ^[Yy]$ ]]; then
    local oai_key
    read -rsp "▸ Clé OpenAI (sk-…) : " oai_key; echo
    if [[ -n "$oai_key" ]]; then
      _vb_set_encrypted "openai_api_key_encrypted" "$oai_key"
      ok "Clé OpenAI sauvegardée (chiffrée Fernet)"
      step "Test de connexion OpenAI…"
      if _vb_test_openai; then
        ok "OpenAI OK"
      else
        warn "Test échoué. Vérifie la clé depuis Réglages → Cloud → Tester."
      fi
    fi
  else
    warn "OpenAI non configuré — providers gpt-4o(-mini) indisponibles tant"
    warn "que la clé n'est pas saisie via Réglages → Cloud."
  fi

  # ── Mode Live par défaut ───────────────────────────────────────
  echo
  echo -e "${C_BOLD}── Mode Live par défaut ──${C_RESET}"
  echo "  1) cpu-fr-en  (V1, gratuit, FR/EN seulement) [recommandé si pas de RunPod]"
  echo "  2) gpu-clone  (multilingue, ta voix)"
  echo "  3) gpu-native (multilingue, voix générique)"
  echo "  4) gpu-hybrid (multilingue, ta voix + accent natif via RVC)"
  echo
  read -rp "Mode par défaut [1] : " default_mode_choice
  case "${default_mode_choice:-1}" in
    1) _vb_set_plain "default_live_mode" "cpu-fr-en" ;;
    2) _vb_set_plain "default_live_mode" "gpu-clone" ;;
    3) _vb_set_plain "default_live_mode" "gpu-native" ;;
    4) _vb_set_plain "default_live_mode" "gpu-hybrid" ;;
    *) _vb_set_plain "default_live_mode" "cpu-fr-en" ;;
  esac
  ok "Mode Live par défaut configuré"

  # Force un reload du backend pour qu'il prenne la nouvelle config
  if systemctl is-active --quiet voicebridge 2>/dev/null; then
    step "Redémarrage de voicebridge.service pour appliquer la config"
    systemctl restart voicebridge
    sleep 2
    ok "Service redémarré"
  fi
}

# ---------------------------------------------------------------------------
# Phase 15 — Récap final
# ---------------------------------------------------------------------------

phase15_recap() {
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

  ⚠️  Sauvegarde critique :
    - $DATA_DIR/.master_key — Master key Fernet (chmod 400)
      Sans ce fichier, les clés API tierces (RunPod, OpenAI, S3)
      stockées dans config.json deviennent indéchiffrables.
      À inclure dans tout backup de $DATA_DIR.

  Étapes suivantes :
  1. Ouvrez https://$DOMAIN dans votre navigateur
  2. Connectez-vous avec votre mot de passe
  3. Téléchargez VoiceBridge.app (Réglages → Installation)
  4. Configurez Teams pour utiliser BlackHole comme micro
  5. (V3) Si tu n'as pas configuré RunPod en phase 14, fais-le depuis
     Réglages → Cloud pour activer les modes GPU.

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
  run_phase phase14 phase14_cloud_config

  phase15_recap
}

main "$@"
