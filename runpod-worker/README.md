# VoiceBridge Worker — RunPod Serverless

Container Docker unifié déployé sur RunPod Serverless EU-FR-1, qui expose 4 endpoints :

| Operation | Description |
|---|---|
| `live_pipeline` | Cascade STT + Trad + TTS [+ RVC] avec streaming |
| `translate` | Traduction texte simple (OPUS-MT GPU ou NLLB) |
| `rvc_convert` | Conversion RVC d'un audio (mode fichier) |
| `warmup` | Pré-charge des modèles en VRAM |

> **Décisions de cadrage V3 :** voir
> [`Spec/voicebridge_v3_specs/Spec/voicebridge_specs/00-decisions-v3.md`](../Spec/voicebridge_v3_specs/Spec/voicebridge_specs/00-decisions-v3.md).
> Notamment **Décision 2** (suppression du dict `NATIVE_VOICES` hardcodé)
> et **Décision 5** (chunking post-synthèse en V3.0, vrai streaming F5-TTS
> reporté en V3.1).

## Architecture

```
runpod-worker/
├── Dockerfile                # CUDA 12.1 + Python 3.11 + deps GPU
├── handler.py                # Handler unifié RunPod (entry point)
├── requirements.txt          # Deps Python (torch 2.4 + cu121, transformers, f5-tts...)
├── models/
│   ├── __init__.py
│   ├── whisper.py           # Whisper Distil-Large-V3 STT
│   ├── f5tts.py             # F5-TTS multilingue (clone OU native)
│   ├── nllb.py              # NLLB-200 distilled 1.3B
│   ├── opus_mt.py           # OPUS-MT (paires FR↔EN/DE/ES/IT)
│   └── rvc.py               # RVC inférence (.pth utilisateur)
├── utils/
│   ├── __init__.py
│   └── audio.py             # Helpers audio (resample, encode, chunk)
├── scripts/
│   └── download_native_voices.py  # Seed 4 voix natives par défaut côté Hostinger
├── tests/
│   ├── __init__.py
│   ├── test_handler.py
│   └── test_audio.py
├── README.md                 # Ce fichier
└── .dockerignore
```

## Build et déploiement

### Prérequis local

- Docker 24+
- Compte Docker Hub (ou autre registry)
- Compte RunPod avec API key

### Build

```bash
cd runpod-worker
docker build -t voicebridge-worker:v3.0.0 .
docker tag voicebridge-worker:v3.0.0 <username>/voicebridge-worker:v3.0.0
docker push <username>/voicebridge-worker:v3.0.0
```

L'image fait ~15-20 Go (CUDA + PyTorch + deps). Premier push long
(~30 min sur ADSL).

### Déploiement RunPod Serverless

1. Console RunPod → Serverless → New Endpoint
2. Image: `<username>/voicebridge-worker:v3.0.0`
3. GPU: RTX 4090 24Go
4. Region: EU-FR-1 (priorité), fallback EU-NL-1, EU-CZ-1
5. Network Volume: `voicebridge-models` (mount `/runpod-volume`)
6. Min Workers: 0 (scale to zero)
7. Max Workers: 1 (à augmenter si multi-utilisateurs)
8. Idle Timeout: 5 min
9. FlashBoot: enabled

Voir `Spec/voicebridge_v3_specs/Spec/voicebridge_specs/11-runpod-integration.md`
pour les détails complets.

### Network Volume + S3 Credentials

Décision 1 : l'upload des `.pth` RVC depuis Hostinger se fait **via l'API
S3 RunPod**, pas via Pod éphémère.

Avant la fin de Phase B :
- Console RunPod → Storage → ton Volume → S3 Credentials → Create
- Récupérer `access_key` et `secret_key`
- Les saisir dans Settings → Cloud côté Hostinger (chiffrés Fernet)

## Tests locaux

Sans GPU local, tests mockés (rapides) :

```bash
cd runpod-worker
pip install -r requirements.txt   # ou juste pytest + numpy + soundfile pour les tests audio
pytest tests/
```

Tests attendus : tous verts (mocks pour les modèles, pas de chargement réel).

Avec GPU local :

```bash
docker run --gpus all -p 8000:8000 voicebridge-worker:v3.0.0
# Test handler via curl
curl -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{"input": {"operation": "warmup", "components": ["whisper"]}}'
```

## Variables d'environnement

| Variable | Défaut | Description |
|---|---|---|
| `HF_HOME` | `/runpod-volume/hf-cache` | Cache HuggingFace |
| `RVC_VOLUME_PATH` | `/runpod-volume/rvc_models` | Path des .pth utilisateur |
| `LOG_LEVEL` | `INFO` | DEBUG / INFO / WARNING |

## Téléchargement des modèles ML (one-shot, avant le premier deployment)

> **CLI HuggingFace** : depuis `huggingface-hub>=0.34`, le binaire s'appelle
> `hf` (l'ancien `huggingface-cli` est déprécié, simple wrapper qui affiche
> un warning). On utilise `hf` partout. Si jamais tu vois un environnement
> où seul `huggingface-cli` existe, fais : `pip install -U 'huggingface-hub>=0.34'`.

Les modèles sont téléchargés une fois dans le Network Volume pour ne pas
les retélécharger à chaque cold start. Spawner un Pod éphémère sur EU-FR-1
avec le Volume monté :

```bash
# Pod : runpod/pytorch:2.4.0-py3.11-cuda12.1.0-devel-ubuntu22.04
# Network Volume monté sur /runpod-volume

export HF_HOME=/runpod-volume/hf-cache

# STT
hf download distil-whisper/distil-large-v3

# Traduction (NLLB + OPUS-MT)
hf download facebook/nllb-200-distilled-1.3B
hf download Helsinki-NLP/opus-mt-fr-en
hf download Helsinki-NLP/opus-mt-en-fr
hf download Helsinki-NLP/opus-mt-fr-de
hf download Helsinki-NLP/opus-mt-de-fr
hf download Helsinki-NLP/opus-mt-fr-es
hf download Helsinki-NLP/opus-mt-es-fr
hf download Helsinki-NLP/opus-mt-fr-it
hf download Helsinki-NLP/opus-mt-it-fr

# TTS
hf download SWivid/F5-TTS

# RVC base models
mkdir -p /runpod-volume/rvc_assets
cd /runpod-volume/rvc_assets
wget https://huggingface.co/lj1995/VoiceConversionWebUI/resolve/main/hubert_base.pt
wget https://huggingface.co/lj1995/VoiceConversionWebUI/resolve/main/rmvpe.pt

# (Détruire le Pod éphémère)
```

Total dans le Volume : **~30 Go**.

## Voix natives (Décision 2)

**Plus de dict `NATIVE_VOICES` hardcodé dans le worker.** Les voix natives
sont gérées dans la bibliothèque `/voices` côté Hostinger (avec le champ
`kind: "native"`). Quand l'utilisateur démarre une session `gpu-native` ou
`gpu-hybrid`, Hostinger envoie le WAV de la voix native sélectionnée comme
`voice_ref` au worker (exactement comme pour `gpu-clone`).

→ Le script `scripts/download_native_voices.py` (à compléter en Phase F)
seedera 4 voix par défaut (EN, ES, PT, IT) dans la lib Hostinger.

## Coûts estimés

| Composant | Coût |
|---|---|
| RTX 4090 inférence (8h/mois live) | ~2.7€ |
| Network Volume 50 Go | ~3.5€/mois |
| Build/push Docker (registry) | 0€ (Docker Hub free) |

Voir `Spec/voicebridge_v3_specs/Spec/voicebridge_specs/11-runpod-integration.md`
pour les estimations détaillées.
