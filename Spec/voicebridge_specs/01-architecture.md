# 01 - Architecture technique

## Stack globale

```
┌─────────────────────────────────────────────────────────────┐
│                    Internet (HTTPS)                          │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  Nginx (reverse proxy + SSL Let's Encrypt + headers sécu)   │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    FastAPI (Python 3.11)                     │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │ Auth         │  │ Routes API   │  │ WebSocket Live   │  │
│  │ middleware   │  │ /api/*       │  │ /ws/stream       │  │
│  └──────────────┘  └──────────────┘  └──────────────────┘  │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              Modèles ML (chargés à la demande)       │    │
│  ├─────────────────────────────────────────────────────┤    │
│  │ NeuTTS Nano Q4 FR/EN (live, streaming)              │    │
│  │ NeuTTS Nano Q8 FR/EN (fichier, haute qualité)       │    │
│  │ NeuCodec (audio codec)                              │    │
│  │ Kyutai 1B (STT FR + EN)                             │    │
│  │ Silero VAD (détection parole/silence)               │    │
│  │ Deepfake-audio-detection-V2 (détection deepfake)                      │    │
│  │ Perth (watermark)                                   │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │ ffmpeg       │  │ yt-dlp       │  │ APScheduler      │  │
│  │ (conversion) │  │ (URL extract)│  │ (cron + V2)      │  │
│  └──────────────┘  └──────────────┘  └──────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  Système de fichiers (/var/voicebridge/)                    │
│                                                              │
│  config/        → config.json (mot de passe, token, domaine)│
│  voices/        → metadata.json + WAV + .pt encodés         │
│  audio/         → fichiers générés (rétention auto)         │
│  models/        → modèles ML téléchargés                    │
│  install/       → BlackHole + VoiceBridge.app à télécharger │
│  logs/          → app.log                                   │
└─────────────────────────────────────────────────────────────┘
```

## Composants Python

### Dépendances principales

```
fastapi>=0.110
uvicorn[standard]>=0.27
websockets>=12
python-multipart>=0.0.9
pydantic>=2
neutts>=latest          # via pip install neutts[all]
llama-cpp-python>=latest
onnxruntime>=1.17
torch>=2.2
torchaudio>=2.2
soundfile>=0.12
psutil>=5.9
slowapi>=0.1.9          # rate limiting
itsdangerous>=2.1       # sessions sécurisées
python-jose>=3.3        # token API
passlib[bcrypt]>=1.7    # hash mot de passe
yt-dlp>=2024.1
ffmpeg-python>=0.2      # wrapper ffmpeg
silero-vad>=4.0
APScheduler>=3.10       # cron + V2 sessions programmées
perth                   # watermark (inclus avec neutts)
transformers>=4.40      # pour Deepfake-audio-detection-V2 et Kyutai
```

### Dépendances système (apt)

```
nginx
certbot python3-certbot-nginx
ffmpeg
python3.11 python3.11-venv python3-pip
build-essential cmake          # pour llama-cpp-python compilation
libopenblas-dev                # accélération CPU
git
```

## Pipeline audio Live

```
Micro utilisateur (navigateur ou VoiceBridge.app)
        │
        ▼
WebSocket → /ws/stream
        │
        ▼
Silero VAD (détection silence > 400ms ou chunk > 4s)
        │
        ▼
Chunk audio (groupe de souffle) envoyé en RAM
        │
        ▼
Buffer circulaire 5s (résilience micro-coupures)
        │
        ▼
Kyutai 1B STT → texte
        │
        ▼
NeuTTS Nano Q4 (avec ref_codes pré-encodés de la voix sélectionnée)
        │
        ▼
NeuCodec → WAV streamé
        │
        ▼
Perth watermark appliqué automatiquement
        │
        ▼
WebSocket → client (navigateur ou VoiceBridge.app)
        │
        ▼
Sortie : speakers navigateur OU BlackHole (Mac) → Teams
```

## Pipeline TTS fichier

```
Texte saisi
    ↓
Sélection voix + format (WAV/MP3) + qualité (Q4 normale / Q8 haute)
    ↓
NeuTTS charge le bon modèle (Q4 ou Q8) si pas déjà en RAM
    ↓
Synthèse complète (avec ref_codes pré-encodés)
    ↓
NeuCodec → WAV
    ↓
Perth watermark appliqué
    ↓
Si MP3 demandé : ffmpeg WAV → MP3 128kbps
    ↓
Si rétention != "session" : écriture sur disque /audio/
    ↓
Si rétention == "session" : stream direct au navigateur, jamais écrit
    ↓
Lecteur navigateur + bouton télécharger
```

## Pipeline STT fichier (avec génération)

```
Phase 1 : Enregistrement micro navigateur
    ↓
Envoi WAV au backend
    ↓
Kyutai 1B STT → texte
    ↓
Phase 2 : Affichage transcription + lecteur audio original
    ↓
Utilisateur peut corriger le texte ou ré-enregistrer
    ↓
Phase 3 : Sélection voix + format + qualité + rétention
    ↓
Génération NeuTTS (idem pipeline TTS)
    ↓
Phase 4 : Lecteur résultat + télécharger
```

## Pipeline ajout voix par URL

```
URL collée
    ↓
yt-dlp télécharge la piste audio uniquement (--extract-audio)
    ↓
ffmpeg convertit en WAV 24kHz mono
    ↓
ffmpeg silencedetect + trim → 15 premières secondes de parole
    ↓
Lecture proposée à l'utilisateur (validation)
    ↓
Si validé : tts.encode_reference() → fichier .pt
    ↓
Sauvegarde voices/<id>.wav + voices/encoded/<id>.pt
    ↓
Mise à jour voices/metadata.json
```

## Gestion mémoire et chargement des modèles

### États possibles

| État | Modèles en RAM | RAM utilisée |
|---|---|---|
| Veille | Aucun | ~200 Mo |
| Studio TTS demandé | NeuTTS Q4 ou Q8 (selon qualité) | ~2 à 3 Go |
| Studio STT demandé | Kyutai + NeuTTS | ~4 à 5 Go |
| Live actif | Kyutai + NeuTTS Q4 + Silero VAD | ~4 Go |
| Détection demandée | Deepfake-audio-detection-V2 (+ autres) | +2 Go |

### Chargement à la demande

- **Premier appel** : déclenche le chargement (latence +3 à 5s)
- **Appels suivants** : modèle déjà en RAM, latence normale
- **Inactivité > 15 min** : déchargement automatique (configurable)
- **Préchauffage manuel** : depuis Réglages → Serveur ou bouton header

### Voix de référence (.pt)

- Encodage `tts.encode_reference()` à l'**ajout** de la voix uniquement
- `.pt` chargés en RAM au démarrage (négligeable, ~5 Mo par voix)
- Permet une latence d'inférence minimale

## Communication temps réel

### WebSocket `/ws/stream`

Utilisé pour :
- Live streaming audio (front web)
- Live streaming audio (VoiceBridge.app macOS)
- État partagé voix active (synchronisation entre clients)
- État serveur (RAM, modèles chargés) push toutes les 5s

### REST API `/api/*`

Utilisé pour :
- Authentification (login/logout)
- Gestion des voix (CRUD)
- Génération TTS/STT fichier
- Détection deepfake
- Gestion enregistrements
- Réglages serveur
- Système (status, prechauffage, nettoyage)

## Multi-clients simultanés

Le backend doit gérer en parallèle :
- Le navigateur web (cookie session)
- L'application VoiceBridge.app (Bearer token)

Lorsqu'un client modifie l'état (changement de voix active), le serveur **broadcast** le changement à tous les clients connectés via WebSocket pour synchronisation temps réel.

## Performance cible

| Métrique | Cible |
|---|---|
| Latence Live (modèles chargés) | 0.6 à 1.4s |
| Latence Live (modèles à charger) | +3 à 5s premier appel |
| TTS fichier 30s de texte | 2 à 6s |
| STT 10s d'audio | 0.5 à 1s |
| Détection deepfake | 2 à 5s |
| Temps de boot serveur (modèles non chargés) | < 5s |
| Préchauffage complet (FR + EN) | ~6s |
