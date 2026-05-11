# 10 - Modèles ML et outils tiers

## Vue d'ensemble

Tous les modèles ML sont **téléchargés à l'installation** depuis HuggingFace, puis utilisés en local. Aucun appel API externe en runtime.

> **Implémentation V1 (vérifié)** : tous les modèles sont stockés dans le **cache HuggingFace standard** sous `/var/voicebridge/data/models/hf-cache/hub/`, accessible via les variables d'environnement `HF_HOME` et `HUGGINGFACE_HUB_CACHE` posées par `voicebridge.service`. Le code Python passe les **repo IDs HF** (et non des chemins filesystem) aux constructeurs : NeuTTS reconnaît les repos `neuphonic/...` comme officiels et infère langue + format GGUF, sans tentative de re-download.

## Modèles utilisés

### NeuTTS Nano (Text-to-Speech)

**Repo principal** : neuphonic/neutts (GitHub)
**Documentation** : https://github.com/neuphonic/neutts

#### Variantes téléchargées

| Modèle | HuggingFace | Taille | Usage |
|---|---|---|---|
| Q4 GGUF français | `neuphonic/neutts-nano-french-q4-gguf` | ~130 Mo | Live, fichier normale |
| Q4 GGUF anglais | `neuphonic/neutts-nano-q4-gguf` | ~130 Mo | Live, fichier normale |
| Q8 GGUF français | `neuphonic/neutts-nano-french-q8-gguf` | ~240 Mo | Fichier haute qualité |
| Q8 GGUF anglais | `neuphonic/neutts-nano-q8-gguf` | ~240 Mo | Fichier haute qualité |

#### NeuCodec

**HuggingFace** : `neuphonic/neucodec`
**Taille** : ~50 Mo
**Rôle** : Décode les tokens audio générés par NeuTTS en WAV

#### Installation Python (vérifié)

```bash
pip install neutts            # PyPI : "neutts" (et non "neuttsair")
pip install neucodec          # versions plafonnent à 0.0.5 (pas 0.1)
```

Inclut automatiquement :
- `llama-cpp-python` (pour les modèles GGUF)
- `onnxruntime` (pour le codec)
- `perth` (pour le watermark automatique)
- `phonemizer` + besoin du binaire système `espeak-ng` (`apt install espeak-ng`)

#### Configuration optionnelle llama-cpp-python avec OpenBLAS

```bash
# Prérequis apt : pkg-config + libblas-dev + liblapack-dev
CMAKE_ARGS="-DGGML_BLAS=ON -DGGML_BLAS_VENDOR=OpenBLAS" \
  pip install llama-cpp-python --force-reinstall --no-cache-dir
```

OpenBLAS peut accélérer ~10-20% l'inférence CPU. **Notre install V1
utilise le wheel pip standard** (la recompilation locale ggml-blas a
parfois des incompatibilités CMake selon Ubuntu). À ré-évaluer si la
synthèse Q8 est trop lente.

#### Usage

```python
from neutts import NeuTTS

tts = NeuTTS(
    backbone_repo="/var/voicebridge/data/models/neutts-nano-fr-q4",
    backbone_device="cpu",
    codec_repo="/var/voicebridge/data/models/neucodec",
    codec_device="cpu"
)

# Pré-encoder une voix de référence
ref_codes = tts.encode_reference("voices/jc_fr.wav")
torch.save(ref_codes, "voices/encoded/jc_fr.pt")

# Générer
ref_text = "Bonjour, je m'appelle Jean-Christophe."
output_audio = tts.infer("Mon texte à synthétiser.", ref_codes, ref_text)
```

---

### Kyutai 1B (Speech-to-Text)

**HuggingFace** : `kyutai/stt-1b-en_fr-trfs` (variante "transformers natif")
**Taille** : ~2 Go
**Langues** : Français + Anglais uniquement (auto-détection)
**Latence** : ~1 s après le premier chunk
**Sample rate attendu** : **24 kHz** (et non 16 kHz comme initialement
prévu — confirmé via le README HuggingFace officiel)

#### Pourquoi Kyutai
- Streaming natif (contrairement à Whisper qui est batch-only)
- FR + EN couvre exactement nos besoins
- Latence faible adaptée au temps réel

#### Usage (vérifié sur le README HF)

```python
from transformers import (
    KyutaiSpeechToTextProcessor,
    KyutaiSpeechToTextForConditionalGeneration,
)

model_id = "kyutai/stt-1b-en_fr-trfs"
processor = KyutaiSpeechToTextProcessor.from_pretrained(model_id)
model = KyutaiSpeechToTextForConditionalGeneration.from_pretrained(
    model_id, device_map="cpu", torch_dtype="auto",
)

# Audio en 24 kHz mono float32 (la conversion ffmpeg est faite en amont)
inputs = processor(audio_array)         # PAS de sampling_rate kwarg
inputs.to("cpu")
output_tokens = model.generate(**inputs)
text = processor.batch_decode(output_tokens, skip_special_tokens=True)[0]
```

**Requiert** `transformers >= 4.53` (les classes `KyutaiSpeechToText*`
ne sont disponibles qu'à partir de cette version).

---

### Silero VAD (Voice Activity Detection)

**Repo** : `snakers4/silero-vad` (GitHub + torch.hub)
**Taille** : ~1 Mo
**Licence** : MIT

#### Rôle
Détecter les silences pour découper les chunks audio par groupes de souffle (au lieu de chunks à durée fixe). Évite l'effet robotique en préservant les intonations.

#### Configuration

| Paramètre | Valeur |
|---|---|
| Silence déclencheur | 400ms |
| Chunk minimum | 500ms |
| Chunk maximum | 4s |
| Sampling rate | 16kHz |

#### Usage

```python
import torch

model, utils = torch.hub.load(
    repo_or_dir='snakers4/silero-vad',
    model='silero_vad',
    trust_repo=True
)

(get_speech_timestamps, _, read_audio, _, _) = utils

# Détecter les segments de parole
speech_timestamps = get_speech_timestamps(
    audio,
    model,
    sampling_rate=16000,
    min_silence_duration_ms=400,
    min_speech_duration_ms=500
)
```

---

### Deepfake-audio-detection-V2 (Détection deepfake)

**HuggingFace** : `MelodyMachine/Deepfake-audio-detection-V2`
**Taille** : ~1.5 Go
**Précision** : 94 à 98%
**Langues** : 30+ langues (incluant FR + EN)

#### Architecture
Basé sur Mamba-SSM, distingue audio synthétique vs authentique avec haute précision même sur audio compressé.

#### Usage

```python
from transformers import AutoModel, AutoFeatureExtractor

extractor = AutoFeatureExtractor.from_pretrained("MelodyMachine/Deepfake-audio-detection-V2")
model = AutoModel.from_pretrained("MelodyMachine/Deepfake-audio-detection-V2")

inputs = extractor(audio, sampling_rate=16000, return_tensors="pt")
outputs = model(**inputs)
# Probabilité audio synthétique
prob_synthetic = torch.softmax(outputs.logits, dim=-1)[0, 1].item()
```

---

### Perth (Watermark)

**Package PyPI** : `perth` (inclus avec `neutts[all]`)
**Repo** : `resemble-ai/Perth`

#### Application automatique
NeuTTS applique Perth automatiquement à toute synthèse. Pas de code additionnel requis.

#### Vérification d'un audio

```python
from perth.perth_net.perth_net_implicit.perth_watermarker import PerthImplicitWatermarker

watermarker = PerthImplicitWatermarker(device="cpu")

# Détecter le watermark
detected = watermarker.detect(audio_array, sample_rate=24000)
# Retourne True si watermark VoiceBridge détecté
```

---

## Outils tiers

### ffmpeg

**Installation** : `apt install ffmpeg`
**Rôle** :
- Conversion MP3/M4A/OGG → WAV
- Conversion WAV → MP3 (sortie)
- Découpe audio (trim 15 premières secondes)
- Détection silences (`silencedetect`)

#### Commandes utilisées

**Conversion en WAV 24kHz mono** :
```bash
ffmpeg -i input.mp3 -ac 1 -ar 24000 output.wav
```

**Conversion WAV → MP3 128kbps** :
```bash
ffmpeg -i input.wav -b:a 128k output.mp3
```

**Trim premières 15s en ignorant silence initial** :
```bash
ffmpeg -i input.wav -af "silenceremove=start_periods=1:start_silence=0.5:start_threshold=-30dB" \
  -t 15 output.wav
```

#### Wrapper Python

```python
import ffmpeg

(
    ffmpeg
    .input('input.mp3')
    .output('output.wav', ac=1, ar=24000)
    .overwrite_output()
    .run(quiet=True)
)
```

---

### yt-dlp

**Installation** : `pip install yt-dlp`
**Rôle** : Téléchargement audio depuis n'importe quelle URL vidéo (YouTube, Vimeo, Dailymotion, Twitter/X, LinkedIn, etc.)

#### Usage

```python
import yt_dlp

ydl_opts = {
    'format': 'bestaudio/best',
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'wav',
        'preferredquality': '192',
    }],
    'outtmpl': '/tmp/extracted_%(id)s.%(ext)s',
    'quiet': True,
}

with yt_dlp.YoutubeDL(ydl_opts) as ydl:
    info = ydl.extract_info(url, download=True)
    audio_path = ydl.prepare_filename(info).replace('.webm', '.wav').replace('.m4a', '.wav')
```

#### Plateformes supportées
yt-dlp supporte plus de 1000 sites. Les principaux :
- YouTube
- Vimeo
- Dailymotion
- Twitter/X
- LinkedIn
- Twitch
- SoundCloud
- TikTok
- Et beaucoup d'autres

---

## Dépendances Python complètes

### requirements.txt

```
# Core
fastapi>=0.110
uvicorn[standard]>=0.27
python-multipart>=0.0.9
pydantic>=2.6

# WebSocket
websockets>=12

# Auth & Security
passlib[bcrypt]>=1.7.4
itsdangerous>=2.1.2
slowapi>=0.1.9
fastapi-csrf-protect>=0.3.4

# ML Models
neutts[all]                      # TTS + Perth + llama-cpp-python
torch>=2.2
torchaudio>=2.2
transformers>=4.40
onnxruntime>=1.17
huggingface-hub>=0.20

# Audio processing
soundfile>=0.12
ffmpeg-python>=0.2
silero-vad                       # via torch.hub
yt-dlp>=2024.1
python-magic>=0.4.27

# System
psutil>=5.9
APScheduler>=3.10

# Utilities
pyaudio>=0.2.14                  # Pour test local et VoiceBridge.app
```

### requirements-dev.txt (build VoiceBridge.app sur Mac)

```
pyinstaller>=6.0
rumps>=0.4.0
pyaudio>=0.2.14
websockets>=12
keyring>=24.0
pip-audit>=2.7
```

## Téléchargement à l'installation

Le script bash exécute :

```bash
# Création du venv
python3.11 -m venv /var/voicebridge/venv
source /var/voicebridge/venv/bin/activate

# Installation packages
pip install --upgrade pip
pip install -r requirements.txt

# Recompilation llama-cpp-python avec OpenBLAS
CMAKE_ARGS="-DGGML_BLAS=ON -DGGML_BLAS_VENDOR=OpenBLAS" \
  pip install llama-cpp-python --force-reinstall --no-cache-dir

# Téléchargement des modèles
hf download neuphonic/neutts-nano-french-q4-gguf \
  --local-dir /var/voicebridge/data/models/neutts-nano-fr-q4
hf download neuphonic/neutts-nano-q4-gguf \
  --local-dir /var/voicebridge/data/models/neutts-nano-en-q4
hf download neuphonic/neutts-nano-french-q8-gguf \
  --local-dir /var/voicebridge/data/models/neutts-nano-fr-q8
hf download neuphonic/neutts-nano-q8-gguf \
  --local-dir /var/voicebridge/data/models/neutts-nano-en-q8
hf download neuphonic/neucodec \
  --local-dir /var/voicebridge/data/models/neucodec
hf download kyutai/stt-1b-en_fr \
  --local-dir /var/voicebridge/data/models/kyutai-1b
hf download MelodyMachine/Deepfake-audio-detection-V2 \
  --local-dir /var/voicebridge/data/models/deepfake-detection-v2

# Silero VAD via torch hub
python -c "import torch; torch.hub.load('snakers4/silero-vad', 'silero_vad', trust_repo=True)"
```

## Tailles totales

| Catégorie | Taille |
|---|---|
| Code applicatif | ~5 Mo |
| Virtualenv Python | ~3 Go |
| Modèles ML | ~5 Go |
| Voix par défaut | ~10 Mo |
| **Total après installation** | **~8 Go** |

Sur KVM 4 (200 Go) : largement suffisant, reste 192 Go pour les enregistrements et données utilisateur.

## Mises à jour

### Modèles
- Pas de mise à jour automatique
- L'utilisateur peut relancer `install.sh` qui détecte et met à jour si version plus récente disponible

### Code applicatif
- Mise à jour via `git pull` dans `/var/voicebridge/app`
- Puis `systemctl restart voicebridge`

### Dépendances Python
- À l'installation initiale uniquement
- Recommander à l'utilisateur de relancer `pip install --upgrade -r requirements.txt` tous les 3 mois

## Licences

| Composant | Licence |
|---|---|
| NeuTTS Nano | NeuTTS Open License 1.0 |
| NeuCodec | NeuTTS Open License 1.0 |
| Kyutai 1B | CC-BY-4.0 |
| Silero VAD | MIT |
| Deepfake-audio-detection-V2 | Open (MelodyMachine) |
| Perth | Open source |
| ffmpeg | LGPL/GPL |
| yt-dlp | Unlicense |
| FastAPI | MIT |
| llama-cpp-python | MIT |

⚠️ **Action utilisateur** : vérifier la NeuTTS Open License 1.0 pour usage interne entreprise. Acceptable pour usage personnel et probablement OK pour usage interne, mais à confirmer juridiquement.
