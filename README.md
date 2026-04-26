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
│  ┌─────▼───────┐  │   WAV 24k       │  │  • NeuTTS Nano    │   │
│  │ BlackHole   │◄─┼─── b64 ─────────┤  │  • XTTS-v2 (Coqui)│   │
│  │ → Teams/Zoom│  │                 │  │  • NeuCodec       │   │
│  └─────────────┘  │                 │  │  • Kyutai 1B STT  │   │
│                   │                 │  │  • Silero VAD     │   │
│  ┌─────────────┐  │                 │  │  • Deepfake-V2    │   │
│  │ Navigateur  │◄─┼─── HTTPS ───────┤  │  • Perth (WM)     │   │
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

## Moteurs TTS

VoiceBridge embarque deux moteurs de clonage vocal qui cohabitent dans le
même venv. À chaque génération depuis `/studio`, l'utilisateur choisit
lequel utiliser via le radio « Moteur TTS » (et il y a un défaut
configurable dans `/settings`).

| Moteur | Taille | Qualité de clonage | Vitesse CPU | Langues |
|---|---|---|---|---|
| **NeuTTS Nano** (Q4/Q8 GGUF) | ~120 M | Bonne | Rapide (~1× temps réel) | FR + EN (modèles séparés) |
| **XTTS-v2** (Coqui, FP16) | ~1.7 B | Très bonne, très naturelle | Lent (~5-10× temps réel sans GPU) | 17 langues sur le même modèle |

NeuTTS est utilisé pour le **mode Live** (le streaming nécessite du quasi
temps-réel). Pour le **TTS fichier**, XTTS-v2 est généralement préférable
si la qualité prime sur la vitesse.

Les deux moteurs partagent les **mêmes voix** : la création d'une voix
sauve le WAV 24 kHz mono utilisable par l'un comme par l'autre. NeuTTS
exige en plus un fichier `ref_codes.pt` pré-encodé (calculé automatiquement
à la création) et un `ref_text.txt` (transcription de l'audio source).
XTTS n'a pas ces prérequis : il lit directement le WAV à chaque inférence.

### Paramètres ajustables (env vars dans `voicebridge.service`)

Tous overrides via `Environment="VB_..."` dans
`/etc/systemd/system/voicebridge.service`, suivi de `daemon-reload` +
`restart`.

**NeuTTS** (sampling) :

| Variable | Défaut | Effet |
|---|---|---|
| `VB_NEUTTS_TEMPERATURE` | 1.1 | Diversité prosodique (0.8 = monotone, 1.3 = expressif mais peut dériver) |
| `VB_NEUTTS_TOP_K` | 120 | Pool de candidats (50 = défaut Neuphonic, 150+ = très varié) |
| `VB_NEUTTS_MAX_CONTEXT` | 4096 | Tokens max → durée audio max (~80 s à 50 tokens/s) |
| `VB_TORCH_THREADS` | nproc | Threads PyTorch intra-op (= 4 sur ce VPS) |

**XTTS-v2** (sampling + identité + post-traitement) :

| Variable | Défaut | Effet |
|---|---|---|
| `VB_XTTS_TEMPERATURE` | 0.7 | Diversité prosodique. 0.65 défaut Coqui, 0.7 sweet spot empirique, ↑ = plus expressif mais risque effet "généré" |
| `VB_XTTS_TOP_K` | 50 | Pool de candidats |
| `VB_XTTS_TOP_P` | 0.85 | Nucleus sampling |
| `VB_XTTS_LENGTH_PENALTY` | 1.0 | Pondération longueur des séquences |
| `VB_XTTS_REPETITION_PENALTY` | 2.0 | Pénalité répétitions (plus bas = tolère plus) |
| `VB_XTTS_SPEED` | 1.05 | Vitesse de parole (0.7 lent, 1.3 rapide) |
| `VB_XTTS_GPT_COND_LEN` | 30 | **Levier #1 pour l'identité** : secondes de la voix de réf utilisées pour le speaker conditioning. Plus haut = mieux capturée. Capé à la durée du WAV |
| `VB_XTTS_GPT_COND_CHUNK_LEN` | 4 | Chunk size pour le conditioning |
| `VB_XTTS_MAX_REF_LEN` | 10 | Secondes pour le décodeur diffusion |
| `VB_XTTS_PITCH_SHIFT` | 0 | Pitch shift post-process (semi-tons, négatif = plus grave, ex -1.5) |

### Caches modèles

| Variable | Chemin |
|---|---|
| `HF_HOME` / `HUGGINGFACE_HUB_CACHE` | `/var/voicebridge/data/models/hf-cache/` (modèles HF : Kyutai, NeuTTS GGUF, NeuCodec) |
| `TTS_HOME` | `/var/voicebridge/data/models/tts-cache/` (XTTS-v2, ~3 Go) |
| `NUMBA_CACHE_DIR` | `/var/voicebridge/data/cache/numba/` (JIT librosa) |
| `XDG_*_HOME` + `HOME` | `/var/voicebridge/data/cache/...` (catch-all libs Python) |

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
