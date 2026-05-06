# VoiceBridge Worker (RunPod Serverless)

Container Docker unifié déployé sur RunPod Serverless EU-FR-1, qui expose 4 endpoints :

| Operation | Description |
|---|---|
| `live_pipeline` | Cascade STT + Trad + TTS [+ RVC] avec streaming |
| `translate` | Traduction texte simple (OPUS-MT GPU ou NLLB) |
| `rvc_convert` | Conversion RVC d'un audio (mode fichier) |
| `warmup` | Pré-charge des modèles en VRAM |

## Architecture

```
runpod-worker/
├── Dockerfile                # CUDA 12.1 + Python 3.11 + deps
├── handler.py                # Handler unifié RunPod (entry point)
├── requirements.txt          # Deps Python
├── models/
│   ├── __init__.py
│   ├── whisper.py           # Whisper Distil-Large-V3 STT
│   ├── f5tts.py             # F5-TTS multilingue
│   ├── nllb.py              # NLLB-200 distilled 1.3B
│   ├── opus_mt.py           # OPUS-MT (paires FR↔EN/DE/ES/IT)
│   └── rvc.py               # RVC inférence
├── utils/
│   ├── __init__.py
│   └── audio.py             # Helpers audio (resample, bytes <-> array)
├── tests/
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

L'image fait ~15-20 Go (CUDA + PyTorch + modèles partiellement embeddés).
Premier push long (~30 min sur ADSL).

### Déploiement RunPod

1. Console RunPod → Serverless → New Endpoint
2. Image: `<username>/voicebridge-worker:v3.0.0`
3. GPU: RTX 4090 24Go
4. Region: EU-FR-1 (priorité), fallback EU-NL-1, EU-CZ-1
5. Network Volume: `voicebridge-models` (mount `/runpod-volume`)
6. Min Workers: 0 (scale to zero)
7. Max Workers: 1 (à augmenter si multi-utilisateurs)
8. Idle Timeout: 5 min
9. FlashBoot: enabled

Voir `Spec/voicebridge_specs/11-runpod-integration.md` pour détails complets.

## Tests locaux

Sans GPU local, tests limités :

```bash
pip install -r requirements.txt
pytest tests/
```

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
| `MAX_RVC_CACHE` | `3` | Nombre de .pth à garder en VRAM |

## Téléchargement des modèles (one-shot)

> **⚠️ Toujours filtrer avec `--include`** : sans filtre, HF télécharge tous
> les formats (safetensors + bin + flax + tf + fp16 + int8…) → +20 Go inutiles.

Avant le premier déploiement, pré-télécharger les modèles dans le Network Volume :

```bash
# Spawn un Pod éphémère sur EU-FR-1 avec le Volume monté
# Image : runpod/pytorch:2.4.0-py3.11-cuda12.1.0-devel-ubuntu22.04
# Network Volume monté sur /runpod-volume

# Sur le Pod :
export HF_HOME=/runpod-volume/hf-cache

# STT — Whisper Distil-Large-V3 (~3 Go)
hf download distil-whisper/distil-large-v3 \
  --include "*.safetensors" --include "*.json" --include "*.txt" \
  --include "tokenizer*" --include "preprocessor_config.json" \
  --include "generation_config.json"

# NLLB-200 distilled 1.3B (~5 Go)
hf download facebook/nllb-200-distilled-1.3B \
  --include "*.safetensors" --include "*.json" \
  --include "tokenizer*" --include "sentencepiece*"

# OPUS-MT (~300 Mo par paire)
for pair in fr-en en-fr fr-de de-fr fr-es es-fr fr-it it-fr; do
  hf download Helsinki-NLP/opus-mt-$pair \
    --include "*.safetensors" --include "*.json" --include "*.txt" \
    --include "source.spm" --include "target.spm" --include "vocab.json"
done

# F5-TTS V1 Base only (~1.5 Go)
hf download SWivid/F5-TTS \
  --include "F5TTS_v1_Base/*" --include "vocab.txt"

# RVC base models (~400 Mo)
mkdir -p /runpod-volume/rvc_assets
cd /runpod-volume/rvc_assets
wget https://huggingface.co/lj1995/VoiceConversionWebUI/resolve/main/hubert_base.pt
wget https://huggingface.co/lj1995/VoiceConversionWebUI/resolve/main/rmvpe.pt

# Détruire le Pod éphémère
```

Total ~16 Go dans le Volume avec ces filtres.

## Coûts estimés

| Composant | Coût |
|---|---|
| RTX 4090 inférence (8h/mois live) | ~2.7€ |
| Network Volume 50 Go | ~3.5€/mois |
| Build/push Docker (registry) | 0€ (Docker Hub free) |

Voir `Spec/voicebridge_specs/11-runpod-integration.md` pour estimations détaillées.
