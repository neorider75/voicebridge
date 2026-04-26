# VoiceBridge

Plateforme **auto-hébergée** de clonage vocal, TTS, STT et synthèse Live,
accessible via un panel web sécurisé et une application macOS qui injecte la
voix clonée dans Teams / Zoom / Meet via [BlackHole](https://existential.audio/blackhole/).

> 100 % self-hosted · aucune donnée ne sort du VPS (sauf téléchargement
> initial des modèles depuis HuggingFace).

## Composants

```
┌───────────────────┐      HTTPS      ┌──────────────────────────┐
│  Mac (utilisateur)│◄───────────────►│  VPS Ubuntu 22.04/24.04  │
│                   │   wss://…       │                           │
│  ┌─────────────┐  │                 │  ┌───────────────────┐   │
│  │VoiceBridge  │  │                 │  │  FastAPI          │   │
│  │.app (rumps) │──┼─── PCM 16k ────►│  │  + WebSocket Live │   │
│  └─────┬───────┘  │   binaire       │  │                   │   │
│        │          │                 │  │  Modèles ML :     │   │
│  ┌─────▼───────┐  │   WAV 24k       │  │  • NeuTTS Q4/Q8   │   │
│  │ BlackHole   │◄─┼─── b64 ─────────┤  │  • NeuCodec       │   │
│  │ → Teams/Zoom│  │                 │  │  • Kyutai 1B STT  │   │
│  └─────────────┘  │                 │  │  • Silero VAD     │   │
│                   │                 │  │  • Deepfake-V2    │   │
│  ┌─────────────┐  │                 │  │  • Perth (WM)     │   │
│  │ Navigateur  │◄─┼─── HTTPS ───────┤  └───────────────────┘   │
│  │ (panel web) │  │                 │                           │
│  └─────────────┘  │                 │  Nginx + Let's Encrypt    │
└───────────────────┘                 └──────────────────────────┘
```

| Composant | Stack | Localisation |
|---|---|---|
| **Backend** | FastAPI + uvicorn (Python 3.11/3.12) | `Site/backend/` |
| **Frontend web** | HTML / CSS / JS vanilla (pas de framework) | `Site/frontend/` |
| **App macOS** | rumps + sounddevice + websockets + PyInstaller | `Site/macos-app/` |
| **Installateur** | Bash interactif idempotent (avec reprise) | `Site/install/` |
| **Specs** | Markdown FR (10 docs + maquette HTML) | `Spec/` |

## Démarrer (VPS Ubuntu vierge)

```bash
# Sur le VPS, en root
wget https://raw.githubusercontent.com/neorider75/voicebridge/main/Site/install/install.sh
chmod +x install.sh
sudo ./install.sh
```

L'install demande : domaine, email Let's Encrypt, mot de passe admin.
Durée : 15-30 min selon bande passante (téléchargement de ~5 Go de modèles ML).

À la fin, ouvre `https://ton-domaine` et logue-toi.

### Options du script

| Flag | Effet |
|---|---|
| (par défaut) | Tout — login + sécurité + modèles ML + voix par défaut |
| `--minimal` | Saute le téléchargement ML (utile pour tester juste login) |
| `--with-ufw` | Active UFW (off par défaut pour ne pas bloquer d'autres services) |
| `--fresh` | Efface le checkpoint et repart de zéro |

Le script est **reprenable** : si une phase plante, relancez-le et il
saute les phases déjà complétées (cf. `/var/voicebridge/.install_state/`).

## Build de l'app macOS

```bash
cd Site/macos-app
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
./build.sh --zip
```

Le bundle est versionné dans `Site/macos-app/release/VoiceBridge.app.zip`
et déployé automatiquement par `install.sh` (phase 9) avec l'URL serveur
patchée au moment du déploiement.

Limitations V1 : signature ad-hoc seulement (Gatekeeper "Apple ne peut
pas vérifier", clic-droit → Ouvrir au 1er lancement) ; build arm64
uniquement (Apple Silicon M1/M2/M3/M4).

## Commandes d'admin courantes

```bash
# Logs en direct
sudo tail -f /var/voicebridge/data/logs/app.log

# État du service
sudo systemctl status voicebridge

# Reset mot de passe (CLI uniquement, pas d'interface web par design)
sudo -u voicebridge /var/voicebridge/venv/bin/python \
    /var/voicebridge/app/Site/backend/manage.py reset-password

# Régénérer la clé API
sudo -u voicebridge /var/voicebridge/venv/bin/python \
    /var/voicebridge/app/Site/backend/manage.py regenerate-api-key

# Pull la dernière version + restart
cd /var/voicebridge/app
sudo -u voicebridge git pull origin main
sudo systemctl restart voicebridge
```

## Sécurité (V1, dès l'install)

- Mot de passe : bcrypt (cost 12) — **un seul mot de passe** (mono-utilisateur)
- Sessions : cookie `vb_session` `HttpOnly` + `Secure` + `SameSite=Strict`,
  signé via `itsdangerous`, 8 h d'inactivité, **pas de JWT**
- Rate limiting `slowapi` : 5 logins / 15 min par IP, 10 voix POST / min,
  60 TTS / min, 20 détections / min
- Anti-bruteforce : délai progressif 0/0/2/5/10 s puis lockout IP 1 h après
  10 échecs
- Headers : CSP, HSTS, X-Frame-Options DENY, X-Content-Type-Options nosniff
- Path traversal : whitelist `^[A-Za-z0-9_-]+$` sur tous les IDs, `Path.resolve()`
  systématique
- Uploads : taille max + magic bytes via `python-magic`
- HTTPS forcé (Let's Encrypt + redirection 301)
- `fail2ban` SSH activé par défaut
- UFW **désactivé** par défaut (opt-in via `--with-ufw`)
- Pas de SQL → pas d'injection SQL par construction

## Privacy by design

- **Mode "session"** (TTS/STT) : audio jamais écrit sur disque, stream direct
- **Mode 24h/48h** : retention propre via APScheduler (sweep toutes les 10 min)
- **Détection deepfake** : upload supprimé immédiatement après l'analyse
- **STT temp** : nettoyage périodique de `data/tmp/` toutes les 15 min (>1 h)
- **Mode Live** : aucun fichier disque, buffer 5 s en RAM uniquement
- **Logs** : IP + résultat login uniquement, **jamais** le contenu textuel/audio
- **Watermark Perth** : automatique sur chaque audio généré

## Specs

Documentation complète en français dans `Spec/voicebridge_specs/` :

| Fichier | Contenu |
|---|---|
| `01-architecture.md` | Stack, pipelines audio, gestion mémoire |
| `02-features-v1.md` | Features V1 exhaustives par page |
| `03-features-v2-v3.md` | Roadmap V2/V3 (entrées grisées dans l'UI V1) |
| `04-frontend-specs.md` | Specs front page par page |
| `05-backend-api.md` | Endpoints REST + WebSocket complets |
| `06-voicebridge-app.md` | Application macOS |
| `07-security.md` | Sécurité détaillée |
| `08-installation.md` | Détail des 14 phases du script bash |
| `09-data-storage.md` | Arborescence `/var/voicebridge/data/` |
| `10-models-and-tools.md` | Modèles ML + outils tiers |

`Spec/voicebridge_v8.html` est la **maquette canonique** (cible UX/UI).

## Licence

(à définir)

## Contributing

Ce projet est mono-utilisateur par conception (V1). Les PRs sont bienvenues
mais le scope V1 est volontairement contraint — voir
`Spec/voicebridge_specs/03-features-v2-v3.md` pour la roadmap.

Avant de proposer une PR :
1. Lire les specs concernées dans `Spec/voicebridge_specs/`
2. Tester en local : `python3 -m compileall app/` côté backend, charge
   manuelle des pages côté frontend
3. Le commit message doit décrire le **pourquoi** (pas juste le quoi)
