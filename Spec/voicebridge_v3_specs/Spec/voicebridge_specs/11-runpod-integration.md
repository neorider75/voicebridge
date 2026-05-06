# 11 - RunPod Integration

> **Document V3 nouveau.** Détaille tout ce qu'il faut savoir pour intégrer RunPod Serverless dans VoiceBridge.

## Pourquoi RunPod

Le live multilingue avec clonage de voix nécessite obligatoirement un GPU. Hostinger ne propose pas de GPU sur ses VPS standards, donc on délègue le compute GPU à RunPod Serverless.

Avantages :
- Datacenter EU-FR-1 (Paris, France) → latence ~5-10ms depuis Hostinger Paris
- Pay-per-second, scale to zero
- FlashBoot pour minimiser les cold starts
- Container Docker custom (full control)
- API REST simple compatible httpx

Alternatives évaluées et écartées :
- Vast.ai : moins cher mais marketplace = qualité variable, pas adapté au temps réel
- Modal : excellent dev experience mais cold starts plus longs
- Replicate : trop limité pour pipelines custom
- VPS GPU dédié 24/7 (OVH, Hetzner) : trop cher pour usage perso (150-250€/mois)

## Configuration RunPod (à faire UNE FOIS au début)

### Étape 1 : Créer un compte RunPod

1. Aller sur https://runpod.io/console/signup
2. Créer un compte (5$ de crédits offerts)
3. Ajouter une méthode de paiement (mais pas de prélèvement automatique sans seuil)

### Étape 2 : Générer une clé API

1. Console RunPod → Settings → API Keys
2. Cliquer "Create API Key"
3. Nom : `voicebridge-prod`
4. Permissions : `Full Access` (à restreindre plus tard si possible)
5. Copier la clé `rpa_...` (visible une seule fois)

Cette clé sera saisie dans VoiceBridge → Settings → Cloud → RunPod.

### Étape 3 : Créer un Network Volume EU-FR-1

1. Console RunPod → Storage → Network Volumes
2. Créer un volume :
   - **Name** : `voicebridge-models`
   - **Datacenter** : `EU-FR-1` (Paris, France)
   - **Size** : `50 GB`
   - **Cost** : ~3.5€/mois
3. Noter l'ID du volume (`vol_...`)

Ce volume contiendra :
- Cache HuggingFace (Whisper, F5-TTS, NLLB, OPUS-MT) ~30 Go
- `/runpod-volume/rvc_models/` : modèles .pth utilisateur ~5 Go par modèle

### Étape 4 : Builder et publier l'image Docker

```bash
cd runpod-worker
docker build -t voicebridge-worker:v3.0.0 .
docker tag voicebridge-worker:v3.0.0 <username>/voicebridge-worker:v3.0.0
docker push <username>/voicebridge-worker:v3.0.0
```

L'image fait ~15-20 Go (CUDA 12.1 + Python + modèles ML pré-téléchargés). Premier push long (~30 min sur connexion ADSL).

**Note** : la première fois, il faut pré-télécharger les modèles dans le Volume via un Pod éphémère :

```bash
# Sur RunPod Console : créer un Pod éphémère
# - GPU : RTX 4090
# - Datacenter : EU-FR-1
# - Network Volume : voicebridge-models (mount /runpod-volume)
# - Image : runpod/pytorch:2.4.0-py3.11-cuda12.1.0-devel-ubuntu22.04

# Sur le Pod :
export HF_HOME=/runpod-volume/hf-cache
hf download facebook/nllb-200-distilled-1.3B
hf download distil-whisper/distil-large-v3
hf download SWivid/F5-TTS
hf download Helsinki-NLP/opus-mt-fr-en
hf download Helsinki-NLP/opus-mt-en-fr
hf download Helsinki-NLP/opus-mt-fr-de
hf download Helsinki-NLP/opus-mt-de-fr
hf download Helsinki-NLP/opus-mt-fr-es
hf download Helsinki-NLP/opus-mt-es-fr
hf download Helsinki-NLP/opus-mt-fr-it
hf download Helsinki-NLP/opus-mt-it-fr

# Télécharger le base model RVC
mkdir -p /runpod-volume/rvc_assets
cd /runpod-volume/rvc_assets
wget https://huggingface.co/lj1995/VoiceConversionWebUI/resolve/main/hubert_base.pt
wget https://huggingface.co/lj1995/VoiceConversionWebUI/resolve/main/rmvpe.pt

# Détruire le Pod éphémère
```

### Étape 5 : Créer l'endpoint Serverless

1. Console RunPod → Serverless → New Endpoint
2. Configuration :
   - **Name** : `voicebridge-pipeline`
   - **Container Image** : `<username>/voicebridge-worker:v3.0.0`
   - **GPU** : `NVIDIA GeForce RTX 4090` (24 Go VRAM)
   - **Region** : `EU-FR-1` (priorité), avec fallback `EU-NL-1`, `EU-CZ-1`
   - **Network Volume** : `voicebridge-models` (mount `/runpod-volume`)
   - **Min Workers** : `0` (scale to zero)
   - **Max Workers** : `1` (usage perso, à monter pour multi-utilisateurs)
   - **Idle Timeout** : `5 minutes`
   - **FlashBoot** : `Enabled`
   - **GPU Count** : `1`
3. Noter l'Endpoint ID (`<endpoint_id>`)

L'URL d'appel sera : `https://api.runpod.ai/v2/<endpoint_id>/run` (async) ou `/runsync` (sync).

## Architecture du worker Docker

### Dockerfile (squelette)

```dockerfile
# runpod-worker/Dockerfile
FROM nvidia/cuda:12.1.0-devel-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV HF_HOME=/runpod-volume/hf-cache
ENV TRANSFORMERS_CACHE=/runpod-volume/hf-cache

# Système
RUN apt-get update && apt-get install -y \
    python3.11 python3.11-venv python3-pip \
    ffmpeg git wget curl \
    && rm -rf /var/lib/apt/lists/*

# Python deps
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Code
COPY handler.py .
COPY models/ ./models/
COPY utils/ ./utils/

# Entrypoint
CMD ["python3.11", "-u", "handler.py"]
```

### requirements.txt du worker

```
# RunPod SDK
runpod>=1.7

# Inference frameworks
torch==2.4.0+cu121
torchaudio==2.4.0+cu121
--extra-index-url https://download.pytorch.org/whl/cu121

# Models
transformers>=4.46
accelerate>=0.30
huggingface-hub>=0.26
sentencepiece>=0.2  # NLLB tokenizer
sacremoses>=0.1     # OPUS-MT tokenizer
ctranslate2>=4.4    # OPUS-MT GPU optimization

# Whisper
openai-whisper>=20240930
faster-whisper>=1.0  # alternative CTranslate2

# F5-TTS
f5-tts @ git+https://github.com/SWivid/F5-TTS.git

# RVC
fairseq>=0.12.2
faiss-cpu>=1.7.4
praat-parselmouth>=0.4
pyworld>=0.3
torchcrepe>=0.0.20

# Audio
soundfile>=0.12
librosa>=0.10
numpy<2.0  # compat torch 2.4

# Utils
pydantic>=2
```

### handler.py (squelette)

```python
"""VoiceBridge worker — handler unifié RunPod Serverless.

3 endpoints :
- operation=live_pipeline : cascade STT + Trad + TTS [+ RVC] avec streaming
- operation=translate : traduction texte simple (OPUS-MT GPU ou NLLB)
- operation=rvc_convert : conversion RVC d'un audio (mode fichier)
"""
import runpod
import logging
import base64
from typing import Generator

from models.whisper import WhisperSTT
from models.f5tts import F5TTS
from models.nllb import NLLB
from models.opus_mt import OpusMT
from models.rvc import RVCRouter

log = logging.getLogger("voicebridge.worker")
logging.basicConfig(level="INFO")

# Modèles initialisés lazy (au premier appel) puis cached en VRAM
_whisper = None
_f5tts = None
_nllb = None
_opus_mt = None
_rvc_router = None


def get_whisper() -> WhisperSTT:
    global _whisper
    if _whisper is None:
        _whisper = WhisperSTT()
    return _whisper


def get_f5tts() -> F5TTS:
    global _f5tts
    if _f5tts is None:
        _f5tts = F5TTS()
    return _f5tts


def get_nllb() -> NLLB:
    global _nllb
    if _nllb is None:
        _nllb = NLLB()
    return _nllb


def get_opus_mt() -> OpusMT:
    global _opus_mt
    if _opus_mt is None:
        _opus_mt = OpusMT()
    return _opus_mt


def get_rvc_router() -> RVCRouter:
    global _rvc_router
    if _rvc_router is None:
        _rvc_router = RVCRouter()  # cache LRU des .pth
    return _rvc_router


def handler(job):
    inp = job["input"]
    op = inp.get("operation", "live_pipeline")
    log.info("handler op=%s", op)

    try:
        if op == "translate":
            return handle_translate(inp)
        elif op == "live_pipeline":
            yield from handle_live_pipeline(inp)
        elif op == "rvc_convert":
            return handle_rvc_convert(inp)
        elif op == "warmup":
            return handle_warmup(inp)
        else:
            return {"error": "unknown_operation", "received": op}
    except Exception as e:
        log.exception("handler error")
        return {"error": "handler_failed", "message": str(e)}


def handle_translate(inp: dict) -> dict:
    """Traduction texte simple. Pas de streaming nécessaire."""
    provider = inp.get("provider", "nllb")
    text = inp["text"]
    src = inp["src_lang"]
    tgt = inp["tgt_lang"]

    if provider == "opus-mt":
        translated = get_opus_mt().translate(text, src, tgt)
    elif provider == "nllb":
        translated = get_nllb().translate(text, src, tgt)
    else:
        return {"error": "unknown_provider", "provider": provider}

    return {"translated": translated, "provider": provider, "src": src, "tgt": tgt}


def handle_live_pipeline(inp: dict) -> Generator[dict, None, None]:
    """Pipeline cascadé : STT → Trad → TTS [→ RVC] avec streaming."""
    mode = inp["mode"]  # gpu-clone / gpu-native / gpu-hybrid
    audio_b64 = inp["audio"]
    src_lang = inp["src_lang"]
    target_lang = inp["target_lang"]
    voice_ref_b64 = inp.get("voice_ref")  # WAV ref pour clonage
    rvc_model_id = inp.get("rvc_model_id")
    translation_provider = inp.get("translation_provider", "nllb")

    # 1. STT
    text = get_whisper().transcribe(audio_b64, src_lang)
    yield {"type": "transcript", "text": text}

    # 2. Traduction (si nécessaire)
    if target_lang != src_lang:
        if translation_provider == "opus-mt":
            translated = get_opus_mt().translate(text, src_lang, target_lang)
        elif translation_provider == "nllb":
            translated = get_nllb().translate(text, src_lang, target_lang)
        else:
            # GPT-4o gere cote Hostinger pour ne pas exposer la cle au worker
            translated = inp.get("pre_translated", text)
        yield {"type": "translated", "text": translated,
               "src_lang": src_lang, "tgt_lang": target_lang}
        text_to_speak = translated
    else:
        text_to_speak = text

    # 3. TTS streaming
    if mode == "gpu-clone":
        for chunk_b64, seq in get_f5tts().synthesize_streaming(
            text_to_speak, voice_ref_b64, target_lang
        ):
            yield {"type": "audio_pcm", "data": chunk_b64, "seq": seq,
                   "sample_rate": 24000}

    elif mode == "gpu-native":
        for chunk_b64, seq in get_f5tts().synthesize_native_streaming(
            text_to_speak, target_lang
        ):
            yield {"type": "audio_pcm", "data": chunk_b64, "seq": seq,
                   "sample_rate": 24000}

    elif mode == "gpu-hybrid":
        # Cascade F5-TTS native → RVC
        if not rvc_model_id:
            yield {"type": "error", "message": "rvc_model_id required for hybrid mode"}
            return
        # Synthèse complète d'abord (RVC ne peut pas streamer chunk par chunk facilement)
        native_audio = get_f5tts().synthesize_native(text_to_speak, target_lang)
        rvc_model = get_rvc_router().load(rvc_model_id)
        for chunk_b64, seq in rvc_model.convert_streaming(native_audio):
            yield {"type": "audio_pcm", "data": chunk_b64, "seq": seq,
                   "sample_rate": 24000}

    yield {"type": "audio_end"}


def handle_rvc_convert(inp: dict) -> dict:
    """Mode fichier : conversion RVC d'un audio complet."""
    rvc_model_id = inp["rvc_model_id"]
    audio_b64 = inp["audio"]
    pitch_shift = inp.get("pitch_shift", 0)
    index_rate = inp.get("index_rate", 0.7)

    rvc_model = get_rvc_router().load(rvc_model_id)
    converted_b64 = rvc_model.convert(audio_b64, pitch_shift=pitch_shift,
                                      index_rate=index_rate)
    return {"audio": converted_b64, "sample_rate": 24000}


def handle_warmup(inp: dict) -> dict:
    """Pré-charge les modèles en VRAM."""
    components = inp.get("components", ["whisper", "f5tts", "nllb"])
    loaded = []
    for c in components:
        if c == "whisper":
            get_whisper()
            loaded.append("whisper")
        elif c == "f5tts":
            get_f5tts()
            loaded.append("f5tts")
        elif c == "nllb":
            get_nllb()
            loaded.append("nllb")
        elif c == "opus-mt":
            get_opus_mt()
            loaded.append("opus-mt")
    return {"loaded": loaded}


# Démarrage RunPod
runpod.serverless.start({
    "handler": handler,
    "return_aggregate_stream": True,  # streaming activé
})
```

## Côté Hostinger : `services/runpod_client.py`

```python
"""Wrapper RunPod Serverless.

Architecture :
- Client async via httpx[http2]
- Streaming via SSE (Server-Sent Events) pour le pipeline live
- Retry automatique avec backoff exponentiel
- Métriques de coût en temps réel
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import AsyncGenerator
from urllib.parse import urljoin

import httpx

from .. import config
from . import secrets as secrets_svc

log = logging.getLogger("voicebridge.runpod")


class RunPodError(Exception):
    """Erreur d'appel RunPod (timeout, 5xx, etc.)."""


class RunPodClient:
    """Client unifié RunPod Serverless avec streaming."""

    def __init__(self):
        self.endpoint_id = config.get("runpod_endpoint_id", "")
        self.api_key = secrets_svc.decrypt(config.get("runpod_api_key_encrypted", ""))
        self.base_url = "https://api.runpod.ai/v2"
        self.client = httpx.AsyncClient(
            http2=True,
            timeout=httpx.Timeout(120.0, connect=10.0),
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20,
                                keepalive_expiry=300),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )

    async def is_configured(self) -> bool:
        return bool(self.endpoint_id and self.api_key)

    async def health(self) -> dict:
        """Test de connexion : vérifie que l'endpoint répond."""
        if not await self.is_configured():
            return {"ok": False, "error": "not_configured"}
        url = f"{self.base_url}/{self.endpoint_id}/health"
        try:
            r = await self.client.get(url)
            r.raise_for_status()
            return {"ok": True, **r.json()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def warmup(self, components: list[str] = None) -> dict:
        """Pré-charge des modèles en VRAM. Retourne dès que ready."""
        components = components or ["whisper", "f5tts", "nllb"]
        result = await self._run_sync({
            "operation": "warmup",
            "components": components,
        })
        return result

    async def translate(self, text: str, src: str, tgt: str,
                        provider: str = "nllb") -> str:
        """Traduction texte simple. Synchrone (pas de streaming)."""
        result = await self._run_sync({
            "operation": "translate",
            "provider": provider,
            "text": text,
            "src_lang": src,
            "tgt_lang": tgt,
        })
        if "error" in result:
            raise RunPodError(result["error"])
        return result["translated"]

    async def live_pipeline(
        self,
        audio_b64: str,
        mode: str,
        src_lang: str,
        target_lang: str,
        voice_ref_b64: str | None = None,
        rvc_model_id: str | None = None,
        translation_provider: str = "nllb",
        pre_translated: str | None = None,
    ) -> AsyncGenerator[dict, None]:
        """Pipeline cascadé avec streaming des chunks audio.

        Yields des messages JSON :
        - {"type": "transcript", "text": "..."}
        - {"type": "translated", "text": "...", ...}
        - {"type": "audio_pcm", "data": "<base64>", "seq": int, "sample_rate": 24000}
        - {"type": "audio_end"}
        """
        payload = {
            "operation": "live_pipeline",
            "mode": mode,
            "audio": audio_b64,
            "src_lang": src_lang,
            "target_lang": target_lang,
            "translation_provider": translation_provider,
        }
        if voice_ref_b64:
            payload["voice_ref"] = voice_ref_b64
        if rvc_model_id:
            payload["rvc_model_id"] = rvc_model_id
        if pre_translated:
            payload["pre_translated"] = pre_translated

        async for msg in self._run_stream(payload):
            yield msg

    async def rvc_convert(self, rvc_model_id: str, audio_b64: str,
                          pitch_shift: int = 0, index_rate: float = 0.7) -> str:
        """Conversion RVC d'un audio (mode fichier)."""
        result = await self._run_sync({
            "operation": "rvc_convert",
            "rvc_model_id": rvc_model_id,
            "audio": audio_b64,
            "pitch_shift": pitch_shift,
            "index_rate": index_rate,
        })
        if "error" in result:
            raise RunPodError(result["error"])
        return result["audio"]

    async def upload_rvc_model(self, rvc_model_id: str, pth_path: str,
                                index_path: str | None = None) -> dict:
        """Upload .pth + .index sur le Network Volume.

        Note: passe par un endpoint dédié RunPod ou via SSH selon l'API
        disponible. Voir RunPod docs pour la méthode actuelle.
        """
        # TODO: implémenter selon l'API RunPod du moment (peut être via
        # un Pod éphémère monté sur le Volume, ou via leur API S3-like)
        ...

    # ── Internal ───────────────────────────────────────────────────────

    async def _run_sync(self, payload: dict, timeout: int = 60) -> dict:
        """Appel synchrone /runsync (max 5 min selon RunPod)."""
        url = f"{self.base_url}/{self.endpoint_id}/runsync"
        try:
            r = await self.client.post(url, json={"input": payload},
                                       timeout=timeout)
            r.raise_for_status()
            data = r.json()
            if data.get("status") == "COMPLETED":
                return data.get("output", {})
            else:
                raise RunPodError(f"job not completed: {data}")
        except httpx.HTTPError as e:
            raise RunPodError(f"http error: {e}") from e

    async def _run_stream(self, payload: dict) -> AsyncGenerator[dict, None]:
        """Appel streaming /run-stream pour récupérer les chunks au fur et à mesure."""
        # Démarre le job
        url_run = f"{self.base_url}/{self.endpoint_id}/run"
        r = await self.client.post(url_run, json={"input": payload})
        r.raise_for_status()
        job = r.json()
        job_id = job["id"]
        log.info("runpod job started: %s", job_id)

        # Poll le stream
        url_stream = f"{self.base_url}/{self.endpoint_id}/stream/{job_id}"
        while True:
            r = await self.client.get(url_stream)
            r.raise_for_status()
            data = r.json()

            for output in data.get("stream", []):
                yield output["output"]

            if data.get("status") in ("COMPLETED", "FAILED"):
                if data["status"] == "FAILED":
                    raise RunPodError(f"job failed: {data}")
                break

            await asyncio.sleep(0.05)  # 50ms entre 2 polls

    async def close(self):
        await self.client.aclose()


# Singleton (au sens "une instance par process uvicorn workers=1")
_client: RunPodClient | None = None


def get_client() -> RunPodClient:
    global _client
    if _client is None:
        _client = RunPodClient()
    return _client
```

## Coûts estimés et facturation

### Pricing RunPod Serverless (à vérifier au moment de l'implémentation)

| GPU | Active worker | Idle worker | Tarif (€/h en GPU actif) |
|---|---|---|---|
| RTX 4090 (24Go) | Yes | Scale to zero | ~0.34 € |
| RTX 4090 always-on | Yes | Always | ~0.24 € (-30% via "active worker") |
| L40 (48Go) | Yes | Scale to zero | ~0.69 € |
| H100 (80Go) | Yes | Scale to zero | ~1.50 € |

Pour VoiceBridge, RTX 4090 suffit largement.

### Network Volume

50 Go × ~0.07 €/Go/mois = **3.5 €/mois**

### Estimations mensuelles

| Profil usage | Coût RunPod estimé |
|---|---|
| Mode fichier seulement | 0 € (Volume payé même sans usage GPU) |
| Live ponctuel (8h/mois) | ~3 € + Volume = ~6.5 € |
| Live régulier (30h/mois) | ~10 € + Volume = ~13.5 € |
| Live intensif (100h/mois) | ~34 € + Volume = ~37.5 € |
| Active worker 9h-18h en semaine | ~80 € + Volume = ~83.5 € |

### Compteur de coûts dans VoiceBridge

UI dans Settings → Cloud → RunPod :

```
Usage du mois en cours :
- Sessions live : 12 (~ 8h 45min)
- Coût estimé   : 3.18 €
- Volume        : 3.50 € (fixe)
- Total         : 6.68 €
```

Calcul côté Hostinger basé sur les durées de session enregistrées (pas d'appel à l'API billing RunPod, juste estimation).

## Datacenters et fallback

| Datacenter | Code | Latence depuis Paris | Disponibilité GPU |
|---|---|---|---|
| France | EU-FR-1 | ~5-10ms ⚡ | Variable |
| Pays-Bas | EU-NL-1 | ~15-25ms | Bonne |
| Tchéquie | EU-CZ-1 | ~25-40ms | Excellente |
| Suède | EU-SE-1 | ~30-45ms | Bonne |
| Roumanie | EU-RO-1 | ~40-60ms | Excellente |
| Islande | EUR-IS-2 | ~40-60ms | Excellente |

**Configuration recommandée endpoint** : `EU-FR-1,EU-NL-1,EU-CZ-1` (priorité France, fallback Pays-Bas, deuxième fallback Tchéquie).

⚠️ **Limitation Network Volume** : un Volume est attaché à UN datacenter. Si EU-FR-1 down, le worker doit accéder au Volume via un autre DC, ce qui n'est pas possible directement. Solutions :
- Backup quotidien des .pth sur Hostinger (via cron)
- Recréation manuelle du Volume sur EU-NL-1 si EU-FR-1 down longtemps

## Tests

### Tests locaux (sans RunPod)

```python
# Site/backend/tests/test_runpod_client.py
import pytest
from unittest.mock import AsyncMock, patch

from app.services.runpod_client import RunPodClient, RunPodError


@pytest.mark.asyncio
async def test_translate_success():
    with patch.object(RunPodClient, "_run_sync",
                      new_callable=AsyncMock) as mock:
        mock.return_value = {"translated": "Hello world"}
        client = RunPodClient()
        result = await client.translate("Bonjour le monde", "fr", "en")
        assert result == "Hello world"


@pytest.mark.asyncio
async def test_translate_error():
    with patch.object(RunPodClient, "_run_sync",
                      new_callable=AsyncMock) as mock:
        mock.return_value = {"error": "model_not_loaded"}
        client = RunPodClient()
        with pytest.raises(RunPodError):
            await client.translate("test", "fr", "en")


@pytest.mark.asyncio
async def test_live_pipeline_streaming():
    async def mock_stream(payload):
        yield {"type": "transcript", "text": "Bonjour"}
        yield {"type": "translated", "text": "Hello"}
        yield {"type": "audio_pcm", "data": "...", "seq": 0,
               "sample_rate": 24000}
        yield {"type": "audio_end"}

    with patch.object(RunPodClient, "_run_stream",
                      side_effect=mock_stream):
        client = RunPodClient()
        chunks = []
        async for msg in client.live_pipeline(
            "audio_b64", "gpu-clone", "fr", "en"
        ):
            chunks.append(msg)
        assert len(chunks) == 4
        assert chunks[0]["type"] == "transcript"
```

### Tests d'intégration (avec RunPod réel)

À faire en environnement de dev avec une vraie clé RunPod :

```bash
# .env.test
RUNPOD_API_KEY=rpa_test_...
RUNPOD_ENDPOINT_ID=test_endpoint
```

```python
# Site/backend/tests/integration/test_runpod_live.py
@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("RUNPOD_API_KEY"),
                    reason="No RunPod key in env")
async def test_real_translation():
    client = RunPodClient()
    result = await client.translate("Bonjour", "fr", "en", provider="nllb")
    assert "hello" in result.lower()
```

## Monitoring et debug

### Logs côté Hostinger

```python
# Tous les appels RunPod loggés avec durée
log.info("runpod call op=%s duration=%.2fs", op, duration)
log.warning("runpod retry %d/3 after error: %s", attempt, error)
```

### Dashboard RunPod

Console RunPod → Serverless → ton endpoint :
- Charts d'usage (requests/sec, GPU utilization, queue length)
- Logs des workers en temps réel
- Erreurs récentes

### Indicateur d'état dans VoiceBridge

Settings → Cloud → RunPod → "Statut" :
```
🟢 Connecté · EU-FR-1 · Latence ping ~12ms
Workers actifs : 0 (scale to zero)
Dernier appel : il y a 23 min
```

## Sécurité

### Stockage des clés API

Les clés RunPod et OpenAI sont chiffrées via Fernet (cryptography lib) avant stockage dans `config.json` :

```python
# services/secrets.py
from cryptography.fernet import Fernet
from .. import config

def _get_master_key() -> bytes:
    """Master key dérivée du password_hash (pas idéal mais simple V3)."""
    # En V3.5 : utiliser un keyring système ou env var
    return config.get("master_key", "").encode()[:32].ljust(32, b"\0")

def encrypt(plaintext: str) -> str:
    f = Fernet(base64.urlsafe_b64encode(_get_master_key()))
    return f.encrypt(plaintext.encode()).decode()

def decrypt(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    f = Fernet(base64.urlsafe_b64encode(_get_master_key()))
    return f.decrypt(ciphertext.encode()).decode()
```

### Rate limiting

Les routes V3 appelant RunPod ont un rate limit séparé pour éviter les explosions de coûts :

```python
# routes/cloud.py
from ..limiter import limiter

@router.post("/translate")
@limiter.limit("60/minute")
async def translate(payload: ..., request: Request):
    ...
```

### Audit des coûts

Logger chaque appel RunPod avec coût estimé pour audit :

```python
# Après chaque appel RunPod
estimated_cost_eur = duration_seconds * 0.34 / 3600
log.info("runpod_billing op=%s duration=%.2fs cost=%.4f€",
         op, duration_seconds, estimated_cost_eur)
```

Stocker dans `data/billing/runpod_usage.jsonl` pour l'agréger côté Settings.

## Migration depuis V1

Pas de migration nécessaire pour l'utilisateur final :
- Voix existantes préservées
- Mode CPU FR/EN reste fonctionnel
- Le mode V3 est entièrement opt-in via Settings → Cloud

L'utilisateur doit juste :
1. Créer un compte RunPod
2. Saisir sa clé API + endpoint ID + volume ID dans Settings → Cloud
3. Cliquer "Tester la connexion"
4. (Optionnel) Saisir sa clé OpenAI dans Settings → Cloud → OpenAI
5. Choisir son mode et provider de traduction préférés
