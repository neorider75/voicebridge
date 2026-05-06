# 08 - Installation (script bash)

## Objectif

Un seul script `install.sh` à lancer sur un VPS Ubuntu fraîchement provisionné. À la fin, l'application est entièrement fonctionnelle, accessible via HTTPS.

## Prérequis utilisateur

L'utilisateur doit avoir préalablement :
- Un VPS Ubuntu 22.04 ou 24.04 fraîchement provisionné
- Un nom de domaine pointé vers l'IP du VPS (DNS A record)
- Un accès SSH root au VPS

## Lancement

```bash
wget https://raw.githubusercontent.com/[user]/voicebridge/main/install.sh
chmod +x install.sh
sudo ./install.sh
```

## Phases du script

### Phase 1 : Vérifications
- Vérifier que l'OS est Ubuntu 22.04 ou 24.04
- Vérifier les droits root
- Vérifier la présence d'au moins 8 Go RAM (recommandé 16 Go)
- Vérifier au moins 20 Go d'espace disque libre
- Vérifier la connexion internet

### Phase 2 : Questions interactives

```
═══════════════════════════════════════════════════════
   VoiceBridge - Installation
═══════════════════════════════════════════════════════

Ce script va installer VoiceBridge sur votre VPS.
Durée estimée : 15 à 30 minutes selon votre connexion.

▸ Nom de domaine pointé vers ce VPS
  (ex: voicebridge.mondomaine.com)
  > _____

▸ Email pour Let's Encrypt (notifications de renouvellement)
  > _____

▸ Mot de passe administrateur du panel web
  (min 8 caractères, sera demandé pour se connecter à l'interface)
  > **********
  
▸ Confirmer le mot de passe
  > **********

▸ Composants à installer (espace pour décocher) :
  [✓] FastAPI + dépendances Python (~200 Mo)
  [✓] NeuTTS Nano Q4 FR + EN (~260 Mo)
  [✓] NeuTTS Nano Q8 FR + EN (~480 Mo)
  [✓] NeuCodec (~50 Mo)
  [✓] Kyutai 1B (STT FR + EN) (~2 Go)
  [✓] Deepfake-audio-detection-V2 (détection deepfake) (~1.5 Go)
  [✓] Silero VAD (~1 Mo)
  [✓] Perth (watermark)
  [✓] ffmpeg + yt-dlp (conversion + extraction URL)
  [✓] Nginx + SSL Let's Encrypt
  [✓] Cron job nettoyage automatique
  [✓] Build VoiceBridge.app pour macOS

  Espace total estimé : ~5 Go

▸ Lancer l'installation ? [O/n]
```

### Phase 3 : Récapitulatif et confirmation

```
═══════════════════════════════════════════════════════
   Récapitulatif
═══════════════════════════════════════════════════════

Domaine        : voicebridge.mondomaine.com
Email SSL      : admin@mondomaine.com
Composants     : Tous installés
Dossier app    : /var/voicebridge
User Linux     : voicebridge
Service        : systemd voicebridge.service
Logs           : /var/voicebridge/data/logs/

Confirmer l'installation ? [O/n]
```

### Phase 4 : Installation système

```bash
# Mise à jour
apt update && apt upgrade -y

# Dépendances système
apt install -y \
  python3.11 python3.11-venv python3-pip \
  build-essential cmake \
  libopenblas-dev \
  ffmpeg \
  nginx certbot python3-certbot-nginx \
  ufw fail2ban \
  git curl wget \
  libmagic1

# Création utilisateur dédié
useradd -r -s /bin/false -m -d /var/voicebridge voicebridge
mkdir -p /var/voicebridge/data/{config,voices,voices/encoded,audio,models,install,logs,tmp}
chown -R voicebridge:voicebridge /var/voicebridge
```

### Phase 5 : Code applicatif

```bash
# Clone du dépôt
cd /var/voicebridge
git clone [REPO] app
cd app

# Virtualenv Python
python3.11 -m venv /var/voicebridge/venv
source /var/voicebridge/venv/bin/activate

# Dépendances Python
pip install --upgrade pip
pip install -r requirements.txt

# llama-cpp-python compilé pour CPU
CMAKE_ARGS="-DGGML_BLAS=ON -DGGML_BLAS_VENDOR=OpenBLAS" \
  pip install llama-cpp-python --force-reinstall --no-cache-dir
```

### Phase 6 : Téléchargement des modèles

```bash
echo "▸ Téléchargement des modèles ML (peut prendre 10-15 min)..."

# NeuTTS Nano Q4 FR
huggingface-cli download neuphonic/neutts-nano-french-q4-gguf \
  --local-dir /var/voicebridge/data/models/neutts-nano-fr-q4

# NeuTTS Nano Q4 EN
huggingface-cli download neuphonic/neutts-nano-q4-gguf \
  --local-dir /var/voicebridge/data/models/neutts-nano-en-q4

# NeuTTS Nano Q8 FR (haute qualité)
huggingface-cli download neuphonic/neutts-nano-french-q8-gguf \
  --local-dir /var/voicebridge/data/models/neutts-nano-fr-q8

# NeuTTS Nano Q8 EN
huggingface-cli download neuphonic/neutts-nano-q8-gguf \
  --local-dir /var/voicebridge/data/models/neutts-nano-en-q8

# NeuCodec
huggingface-cli download neuphonic/neucodec \
  --local-dir /var/voicebridge/data/models/neucodec

# Kyutai 1B (variante -trfs compatible transformers >= 4.53)
huggingface-cli download kyutai/stt-1b-en_fr-trfs \
  --local-dir /var/voicebridge/data/models/kyutai-1b

# Deepfake-audio-detection-V2
huggingface-cli download MelodyMachine/Deepfake-audio-detection-V2 \
  --local-dir /var/voicebridge/data/models/deepfake-detection-v2

# Silero VAD
python -c "import torch; torch.hub.load('snakers4/silero-vad', 'silero_vad', trust_repo=True)"
```

### Phase 7 : Voix par défaut

Copier les samples NeuTTS dans la bibliothèque de voix :

```bash
cp neutts-samples/juliette.wav /var/voicebridge/data/voices/juliette.wav
cp neutts-samples/juliette.txt /var/voicebridge/data/voices/juliette.txt
cp neutts-samples/dave.wav /var/voicebridge/data/voices/dave.wav
cp neutts-samples/dave.txt /var/voicebridge/data/voices/dave.txt

# Pré-encoder les .pt
python -c "
from neutts import NeuTTS
tts = NeuTTS(backbone_repo='/var/voicebridge/data/models/neutts-nano-fr-q4', codec_repo='/var/voicebridge/data/models/neucodec')
codes = tts.encode_reference('/var/voicebridge/data/voices/juliette.wav')
import torch
torch.save(codes, '/var/voicebridge/data/voices/encoded/juliette.pt')
"

# Idem pour Dave en EN
# ...

# Créer metadata.json initial
cat > /var/voicebridge/data/voices/metadata.json <<EOF
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
```

### Phase 8 : Configuration applicative

```bash
# Génération des secrets
PASSWORD_HASH=$(python -c "from passlib.hash import bcrypt; print(bcrypt.hash('$USER_PASSWORD', rounds=12))")
SESSION_SECRET=$(python -c "import secrets; print(secrets.token_hex(32))")
API_TOKEN=$(python -c "import secrets; print('sk-' + secrets.token_hex(16))")
API_TOKEN_HASH=$(python -c "import hashlib; print(hashlib.sha256('$API_TOKEN'.encode()).hexdigest())")

# Création config.json
cat > /var/voicebridge/data/config.json <<EOF
{
  "domain": "$DOMAIN",
  "password_hash": "$PASSWORD_HASH",
  "api_token_hash": "$API_TOKEN_HASH",
  "api_token_created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "session_secret": "$SESSION_SECRET",
  "default_retention": "session",
  "model_unload_after_minutes": 15
}
EOF

chmod 600 /var/voicebridge/data/config.json
chown voicebridge:voicebridge /var/voicebridge/data/config.json

# Afficher la clé API à l'utilisateur (UNE SEULE FOIS)
echo ""
echo "═══════════════════════════════════════════════════════"
echo "   VOTRE CLÉ API VoiceBridge"
echo "═══════════════════════════════════════════════════════"
echo ""
echo "   $API_TOKEN"
echo ""
echo "   ⚠️  Notez-la maintenant. Elle ne sera plus affichée."
echo "   Elle est nécessaire pour configurer l'app macOS."
echo ""
echo "   Vous pouvez en générer une nouvelle depuis"
echo "   l'interface web : Réglages → API"
echo ""
echo "═══════════════════════════════════════════════════════"
echo "   Appuyez sur Entrée pour continuer..."
read
```

### Phase 9 : Build VoiceBridge.app pour macOS

⚠️ Le build de l'app macOS nécessite un Mac. Le script Linux ne peut **pas** builder un .app macOS.

**Solution** : pré-compiler les .app sur un Mac de dev et les distribuer dans le dépôt git, en remplaçant simplement la config.json embedded.

```bash
# Récupérer le .app pré-compilé
cp /var/voicebridge/app/macos_app/VoiceBridge.app.template.zip /tmp/

# Décompresser
cd /tmp && unzip VoiceBridge.app.template.zip

# Modifier le config.json embedded
cat > /tmp/VoiceBridge.app/Contents/Resources/config.json <<EOF
{
  "server_url": "https://$DOMAIN",
  "version": "1.0.0"
}
EOF

# Re-zipper et placer dans /install/
cd /tmp && zip -r VoiceBridge.app.zip VoiceBridge.app
cp VoiceBridge.app.zip /var/voicebridge/data/install/
rm -rf /tmp/VoiceBridge.app /tmp/VoiceBridge.app.zip /tmp/VoiceBridge.app.template.zip

chown -R voicebridge:voicebridge /var/voicebridge/data/install
```

### Phase 10 : Nginx + SSL

```bash
# Configuration Nginx
cat > /etc/nginx/sites-available/voicebridge <<EOF
server {
    listen 80;
    server_name $DOMAIN;
    return 301 https://\$host\$request_uri;
}

server {
    listen 443 ssl http2;
    server_name $DOMAIN;
    
    # SSL configuré par certbot
    
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Frame-Options "DENY" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    
    autoindex off;
    server_tokens off;
    
    client_max_body_size 60M;  # Pour upload détection (50 Mo)
    
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

ln -sf /etc/nginx/sites-available/voicebridge /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

# SSL Let's Encrypt
certbot --nginx -d $DOMAIN --email $EMAIL --agree-tos --non-interactive --redirect

# Tester la config
nginx -t && systemctl reload nginx
```

### Phase 11 : systemd service

```bash
cat > /etc/systemd/system/voicebridge.service <<EOF
[Unit]
Description=VoiceBridge FastAPI service
After=network.target

[Service]
Type=simple
User=voicebridge
Group=voicebridge
WorkingDirectory=/var/voicebridge/app
Environment="PATH=/var/voicebridge/venv/bin"
ExecStart=/var/voicebridge/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
Restart=always
RestartSec=10

# Sécurité
ProtectSystem=strict
ProtectHome=true
NoNewPrivileges=true
PrivateTmp=true
ReadWritePaths=/var/voicebridge/data

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable voicebridge
systemctl start voicebridge
```

### Phase 12 : Firewall et fail2ban

```bash
# UFW
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp     # SSH
ufw allow 80/tcp     # HTTP (redirige vers HTTPS)
ufw allow 443/tcp    # HTTPS
ufw --force enable

# fail2ban pour SSH
cat > /etc/fail2ban/jail.local <<EOF
[sshd]
enabled = true
port = 22
maxretry = 5
bantime = 3600
EOF

systemctl enable fail2ban
systemctl restart fail2ban
```

### Phase 13 : Cron jobs

```bash
# Cron de nettoyage des fichiers expirés (toutes les heures)
cat > /etc/cron.d/voicebridge <<EOF
0 * * * * voicebridge /var/voicebridge/venv/bin/python /var/voicebridge/app/manage.py cleanup-expired
0 3 * * * root certbot renew --quiet --post-hook "systemctl reload nginx"
EOF
```

### Phase 14 : Récapitulatif final

```
═══════════════════════════════════════════════════════
   ✅ Installation terminée avec succès !
═══════════════════════════════════════════════════════

   Interface web : https://$DOMAIN
   
   Identifiants :
     - Mot de passe : (celui que vous avez choisi)
   
   Clé API VoiceBridge.app :
     - Affichée plus haut dans le terminal
     - Régénérable depuis Réglages → API
   
   Logs : /var/voicebridge/data/logs/app.log
   Service : systemctl status voicebridge
   
   Étapes suivantes :
   1. Ouvrez https://$DOMAIN dans votre navigateur
   2. Connectez-vous avec votre mot de passe
   3. Téléchargez VoiceBridge.app pour macOS depuis
      Réglages → Installation
   4. Configurez Teams pour utiliser BlackHole comme micro
   
   En cas de problème :
   - Logs : tail -f /var/voicebridge/data/logs/app.log
   - Status : systemctl status voicebridge
   - Restart : systemctl restart voicebridge

═══════════════════════════════════════════════════════
```

## Script manage.py CLI

Commandes utilitaires disponibles via `python manage.py [cmd]` :

```python
# manage.py
import argparse
import getpass
import sys
from passlib.hash import bcrypt
import json

def reset_password():
    new_pw = getpass.getpass("Nouveau mot de passe: ")
    confirm = getpass.getpass("Confirmer: ")
    if new_pw != confirm:
        print("❌ Les mots de passe ne correspondent pas")
        sys.exit(1)
    if len(new_pw) < 8:
        print("❌ Mot de passe trop court (min 8 caractères)")
        sys.exit(1)
    
    config_path = "/var/voicebridge/data/config.json"
    with open(config_path) as f:
        config = json.load(f)
    config["password_hash"] = bcrypt.hash(new_pw, rounds=12)
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
    print("✅ Mot de passe mis à jour")

def cleanup_expired():
    # Supprimer les fichiers audio expirés
    # Logique de comparaison des dates
    pass

def regenerate_api_key():
    # Régénère une nouvelle clé et l'affiche
    pass

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["reset-password", "cleanup-expired", "regenerate-api-key"])
    args = parser.parse_args()
    
    if args.command == "reset-password":
        reset_password()
    elif args.command == "cleanup-expired":
        cleanup_expired()
    elif args.command == "regenerate-api-key":
        regenerate_api_key()
```

## Désinstallation

Script `uninstall.sh` à fournir aussi :
```bash
systemctl stop voicebridge
systemctl disable voicebridge
rm /etc/systemd/system/voicebridge.service
rm /etc/nginx/sites-enabled/voicebridge
rm /etc/nginx/sites-available/voicebridge
rm /etc/cron.d/voicebridge
rm -rf /var/voicebridge
userdel voicebridge
nginx -s reload
```
