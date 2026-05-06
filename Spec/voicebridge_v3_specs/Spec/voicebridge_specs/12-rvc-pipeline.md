# 12 - RVC Pipeline (entraînement Kaggle + inférence RunPod)

> **Document V3 nouveau.** Pipeline complet pour le Voice Conversion (RVC) intégré à VoiceBridge.

## Vue d'ensemble

RVC = **Retrieval-based Voice Conversion**. C'est un modèle qui transforme une voix source quelconque en TA voix, tout en préservant le contenu et l'accent de la voix source.

**Cas d'usage VoiceBridge** : mode "Hybride accent natif" → F5-TTS produit une voix native (accent parfait), puis RVC convertit cette voix en TA voix (timbre reconnaissable). Résultat : tu parles anglais avec ta voix mais avec un accent britannique parfait.

## Architecture globale

```
┌─────────────────────────────────────────────────────┐
│  Phase 1 : Enregistrement (dans VoiceBridge)         │
│                                                      │
│  Page /recording-session                            │
│  Wizard 5 blocs guidés (~20 min audio)              │
│  Capture micro chunk par chunk                       │
│  Stockage temporaire sur Hostinger                  │
└────────────────────┬─────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────┐
│  Phase 2 : Retraitement (Hostinger CPU, asynchrone) │
│                                                      │
│  Pipeline noisereduce + VAD + segmentation          │
│  ~5 minutes pour 20 min d'audio brut                │
│  Score qualité calculé                              │
│  Export ZIP prêt pour Kaggle                        │
└────────────────────┬─────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────┐
│  Phase 3 : Validation (dans VoiceBridge)            │
│                                                      │
│  Page /recording-session/<id>/validate              │
│  Lecteur audio par clip                             │
│  Score qualité + suppression individuelle           │
│  Bouton "Télécharger ZIP"                           │
└────────────────────┬─────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────┐
│  Phase 4 : Entraînement Kaggle (gratuit, externe)   │
│                                                      │
│  Compte Kaggle (gratuit, 30h GPU/semaine)           │
│  Notebook RVC officiel à forker                     │
│  Upload ZIP comme dataset                            │
│  Lancement entraînement (~3-6h)                     │
│  Téléchargement .pth + .index                       │
└────────────────────┬─────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────┐
│  Phase 5 : Import dans VoiceBridge                  │
│                                                      │
│  Page /rvc-import                                   │
│  Upload .pth + .index avec validation               │
│  Push vers RunPod Network Volume                    │
│  Test rapide avec sample audio                      │
│  Modèle activé                                      │
└────────────────────┬─────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────┐
│  Phase 6 : Utilisation (mode Live "Hybride")        │
│                                                      │
│  Studio Live → Mode "Hybride accent natif"          │
│  Sélection du modèle RVC                            │
│  Pipeline cascade F5-TTS native + RVC sur GPU      │
│  Latence ~1.2s                                       │
└──────────────────────────────────────────────────────┘
```

## Phase 1 : Enregistrement guidé

### Endpoint backend

```
POST /api/recording_session/create
  body: { "name": "JC voice v1", "language": "fr" }
  response: { "session_id": "uuid", "blocks": [...] }

POST /api/recording_session/{id}/append_chunk
  body: PCM 16kHz mono int16 (binary frame)
  query: ?block=1&seq=23

POST /api/recording_session/{id}/finish_block
  body: { "block": 1 }
  response: { "duration_seconds": 312, "ok": true }

GET /api/recording_session/{id}
  response: { ... état complet de la session ... }

DELETE /api/recording_session/{id}
```

### Stockage temporaire

```
data/
  recording_sessions/
    {session_id}/
      session.json           # metadata
      block_1_raw.wav        # bloc 1 brut PCM 16kHz
      block_2_raw.wav
      block_3_raw.wav
      block_4_raw.wav
      block_5_raw.wav
      processed/              # créé par phase 2
        clip_001.wav
        clip_002.wav
        ...
        manifest.json
        quality_report.json
```

### Frontend : capture micro

Réutilise l'AudioWorklet existant (`Site/frontend/js/live-worklet.js`) avec adaptation pour ne pas envoyer en WebSocket mais accumuler côté browser puis POST en chunks.

```javascript
// Site/frontend/js/recording-session.js
class RecordingSessionCapture {
    constructor(sessionId, blockId) {
        this.sessionId = sessionId;
        this.blockId = blockId;
        this.chunks = [];
        this.totalDurationMs = 0;
    }

    async start() {
        const stream = await navigator.mediaDevices.getUserMedia({audio: true});
        this.audioContext = new AudioContext({sampleRate: 16000});
        await this.audioContext.audioWorklet.addModule('/js/live-worklet.js');
        const source = this.audioContext.createMediaStreamSource(stream);
        const worklet = new AudioWorkletNode(this.audioContext, 'pcm-worklet');
        worklet.port.onmessage = (e) => this.onChunk(e.data);
        source.connect(worklet);
    }

    onChunk(pcmInt16) {
        this.chunks.push(pcmInt16);
        this.totalDurationMs += (pcmInt16.length / 16000) * 1000;
        this.updateUI();

        // Upload toutes les 5 secondes
        if (this.chunks.length >= 50) {  // ~5s de chunks 100ms
            this.uploadBatch();
        }
    }

    async uploadBatch() {
        const batch = new Blob(this.chunks);
        this.chunks = [];
        await fetch(`/api/recording_session/${this.sessionId}/append_chunk?block=${this.blockId}`, {
            method: 'POST',
            body: batch,
            headers: {'Content-Type': 'application/octet-stream'},
        });
    }

    async finish() {
        if (this.chunks.length > 0) await this.uploadBatch();
        await fetch(`/api/recording_session/${this.sessionId}/finish_block`, {
            method: 'POST',
            body: JSON.stringify({block: this.blockId}),
        });
    }

    updateUI() {
        const seconds = Math.floor(this.totalDurationMs / 1000);
        document.getElementById('block-duration').textContent = 
            `${Math.floor(seconds/60)}:${(seconds%60).toString().padStart(2, '0')}`;
    }
}
```

### Les 5 blocs textes

Voir `Spec/voicebridge_specs/14-rvc-recording-guide.md` pour le contenu détaillé des 5 blocs.

## Phase 2 : Retraitement automatique

### Endpoint backend

```
POST /api/recording_session/{id}/process
  body: { "denoise_strength": 0.7, "min_clip_seconds": 5, "max_clip_seconds": 15 }
  response: { "task_id": "uuid", "status": "queued" }

GET /ws/progress/{task_id}  (WebSocket)
  emits:
    { "status": "running", "progress_percent": 0-100, "current_step": "...",
      "elapsed_seconds": int, "estimated_remaining_seconds": int }
    { "status": "done", "result": { "clips_count": 142, "score": 87, ... } }
    { "status": "error", "error": "..." }
```

### Pipeline de traitement

```python
# Site/backend/app/services/audio_dataset_processor.py
"""Retraitement audio pour préparer un dataset RVC à partir d'enregistrements bruts.

Pipeline :
1. Concaténation des 5 blocs en un WAV global
2. Détection des régions de parole (Silero VAD)
3. Découpage en clips de 5-15s aux frontières de silence
4. Suppression du bruit de fond (noisereduce)
5. Normalisation loudness à -3 dB peak
6. Calcul score qualité (SNR, distribution durées, niveaux)
7. Export ZIP avec manifeste

Tourne sur Hostinger CPU (asynchrone, ~5 min pour 20 min audio).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable

import numpy as np
import soundfile as sf

log = logging.getLogger("voicebridge.dataset_processor")

PROGRESS_CALLBACK_TYPE = Callable[[int, str, dict], None]


@dataclass
class ClipMetadata:
    filename: str
    duration_s: float
    snr_db: float
    peak_db: float
    rms_db: float
    block_origin: int  # 1-5


@dataclass
class QualityReport:
    total_clips: int
    total_duration_s: float
    snr_avg_db: float
    snr_min_db: float
    peak_avg_db: float
    duration_distribution: dict  # {"<5s": N, "5-10s": N, "10-15s": N, ">15s": N}
    score: int  # 0-100


def process_session(
    session_dir: Path,
    progress_cb: PROGRESS_CALLBACK_TYPE,
    denoise_strength: float = 0.7,
    min_clip_seconds: float = 5.0,
    max_clip_seconds: float = 15.0,
) -> dict:
    """Pipeline complet. Appelle progress_cb(percent, step, details) tout du long.
    
    Returns:
        dict avec keys: clips_count, duration_s, score, output_dir
    """
    output_dir = session_dir / "processed"
    output_dir.mkdir(exist_ok=True)

    progress_cb(5, "Chargement des blocs", {})
    raw_blocks = _load_raw_blocks(session_dir)

    progress_cb(15, "Concaténation", {})
    full_audio, sr = _concatenate_blocks(raw_blocks)

    progress_cb(25, "Détection des silences (Silero VAD)", {})
    speech_regions = _detect_speech_regions(full_audio, sr)
    log.info("VAD: %d speech regions found", len(speech_regions))

    progress_cb(40, "Découpage en clips", {})
    clips = _segment_clips(full_audio, sr, speech_regions, min_clip_seconds,
                            max_clip_seconds)
    progress_cb(45, f"{len(clips)} clips identifiés", {"clips_count": len(clips)})

    progress_cb(50, "Suppression du bruit (noisereduce)", {})
    clips_denoised = _denoise_clips(clips, sr, denoise_strength,
                                      lambda i, n: progress_cb(
                                          50 + int(20 * i / n),
                                          f"Débruit clip {i+1}/{n}",
                                          {})
                                      )

    progress_cb(75, "Normalisation loudness", {})
    clips_normalized = _normalize_clips(clips_denoised)

    progress_cb(85, "Calcul du score qualité", {})
    metadata, report = _compute_quality_report(clips_normalized, sr)

    progress_cb(92, "Export des fichiers", {})
    _export_clips(output_dir, clips_normalized, sr, metadata)
    _export_manifest(output_dir, metadata, report)

    progress_cb(100, "Terminé", {"score": report.score,
                                  "clips_count": len(clips_normalized)})

    return {
        "clips_count": len(clips_normalized),
        "total_duration_s": report.total_duration_s,
        "score": report.score,
        "output_dir": str(output_dir),
    }


def _load_raw_blocks(session_dir: Path) -> list[tuple[np.ndarray, int]]:
    """Charge les 5 fichiers block_N_raw.wav."""
    blocks = []
    for i in range(1, 6):
        path = session_dir / f"block_{i}_raw.wav"
        if not path.exists():
            log.warning("Bloc %d absent : %s", i, path)
            continue
        audio, sr = sf.read(path)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)  # mono
        blocks.append((audio, sr, i))
    return blocks


def _concatenate_blocks(blocks: list) -> tuple[np.ndarray, int]:
    """Concatène en gardant l'origine du bloc pour chaque sample."""
    if not blocks:
        return np.array([]), 16000
    sr = blocks[0][1]
    audio = np.concatenate([b[0] for b in blocks])
    return audio, sr


def _detect_speech_regions(audio: np.ndarray, sr: int) -> list[tuple[int, int]]:
    """Utilise Silero VAD pour détecter les régions de parole.
    
    Returns: list of (start_sample, end_sample) tuples.
    """
    import torch
    model, utils = torch.hub.load(
        repo_or_dir='snakers4/silero-vad',
        model='silero_vad',
        trust_repo=True,
    )
    get_speech_timestamps = utils[0]
    audio_t = torch.from_numpy(audio.astype(np.float32))
    if sr != 16000:
        # Resample to 16k for Silero
        import torchaudio.functional as F
        audio_t = F.resample(audio_t, sr, 16000)
    timestamps = get_speech_timestamps(audio_t, model,
                                        sampling_rate=16000,
                                        min_speech_duration_ms=500,
                                        min_silence_duration_ms=300)
    # Convert back to original sample rate
    factor = sr / 16000
    return [(int(t["start"] * factor), int(t["end"] * factor))
            for t in timestamps]


def _segment_clips(audio, sr, regions, min_s, max_s) -> list[tuple[np.ndarray, int]]:
    """Découpe en clips de durée min_s à max_s.
    
    Returns: list of (clip_audio, block_origin) tuples.
    """
    clips = []
    for start, end in regions:
        duration = (end - start) / sr
        if duration < min_s:
            continue  # trop court, on jette
        if duration <= max_s:
            clips.append((audio[start:end], 1))  # TODO: track block origin
        else:
            # Découper en sous-clips
            n_subclips = int(np.ceil(duration / max_s))
            sub_duration = (end - start) // n_subclips
            for i in range(n_subclips):
                sub_start = start + i * sub_duration
                sub_end = min(sub_start + sub_duration, end)
                if (sub_end - sub_start) / sr >= min_s:
                    clips.append((audio[sub_start:sub_end], 1))
    return clips


def _denoise_clips(clips, sr, strength, progress_cb=None):
    """Applique noisereduce sur chaque clip."""
    import noisereduce as nr
    out = []
    n = len(clips)
    for i, (clip, origin) in enumerate(clips):
        denoised = nr.reduce_noise(y=clip, sr=sr, prop_decrease=strength)
        out.append((denoised, origin))
        if progress_cb:
            progress_cb(i, n)
    return out


def _normalize_clips(clips, target_peak_db=-3.0):
    """Normalise tous les clips au même niveau crête."""
    out = []
    for clip, origin in clips:
        peak = np.max(np.abs(clip))
        if peak > 1e-6:
            target_linear = 10 ** (target_peak_db / 20)
            gain = target_linear / peak
            clip = clip * gain
        out.append((clip, origin))
    return out


def _compute_quality_report(clips, sr) -> tuple[list[ClipMetadata], QualityReport]:
    """Calcule SNR, peak, RMS, distribution des durées, score global."""
    metadata = []
    snrs = []
    durations = []
    peaks = []

    for i, (clip, origin) in enumerate(clips):
        duration = len(clip) / sr
        peak = 20 * np.log10(np.max(np.abs(clip)) + 1e-10)
        rms = 20 * np.log10(np.sqrt(np.mean(clip**2)) + 1e-10)
        # SNR estimé par ratio puissance signal / puissance bruit (approx)
        snr = _estimate_snr(clip, sr)

        metadata.append(ClipMetadata(
            filename=f"clip_{i+1:03d}.wav",
            duration_s=round(duration, 2),
            snr_db=round(snr, 1),
            peak_db=round(peak, 1),
            rms_db=round(rms, 1),
            block_origin=origin,
        ))
        snrs.append(snr)
        durations.append(duration)
        peaks.append(peak)

    # Distribution
    dist = {"<5s": 0, "5-10s": 0, "10-15s": 0, ">15s": 0}
    for d in durations:
        if d < 5: dist["<5s"] += 1
        elif d < 10: dist["5-10s"] += 1
        elif d < 15: dist["10-15s"] += 1
        else: dist[">15s"] += 1

    # Score 0-100
    snr_avg = np.mean(snrs) if snrs else 0
    score_snr = min(40, int(snr_avg * 1.3))  # SNR 30dB → 39pts
    score_count = min(30, int(len(clips) / 5))  # 150 clips → 30pts
    score_dist = 30 if dist["5-10s"] + dist["10-15s"] >= len(clips) * 0.7 else 15
    total_score = min(100, score_snr + score_count + score_dist)

    report = QualityReport(
        total_clips=len(clips),
        total_duration_s=sum(durations),
        snr_avg_db=round(snr_avg, 1),
        snr_min_db=round(min(snrs), 1) if snrs else 0,
        peak_avg_db=round(np.mean(peaks), 1) if peaks else 0,
        duration_distribution=dist,
        score=total_score,
    )
    return metadata, report


def _estimate_snr(clip: np.ndarray, sr: int) -> float:
    """Estimation grossière du SNR : signal = 95th percentile, bruit = 5th percentile."""
    abs_clip = np.abs(clip)
    if len(abs_clip) == 0:
        return 0
    signal = np.percentile(abs_clip, 95)
    noise = np.percentile(abs_clip, 5)
    if noise < 1e-10:
        return 60  # cap
    return 20 * np.log10(signal / noise)


def _export_clips(output_dir: Path, clips, sr: int, metadata: list[ClipMetadata]):
    for i, ((clip, _), meta) in enumerate(zip(clips, metadata)):
        path = output_dir / meta.filename
        sf.write(path, clip, 44100, format="WAV", subtype="PCM_16")  # 44.1k pour Kaggle


def _export_manifest(output_dir: Path, metadata: list[ClipMetadata],
                      report: QualityReport):
    manifest = {
        "version": "1.0",
        "clips": [asdict(m) for m in metadata],
        "quality_report": asdict(report),
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
```

### Mise à jour barre de progression

Le service publie via WebSocket `/ws/progress/{task_id}` toutes les ~500ms :

```python
# routes/recording_session.py
import asyncio
from fastapi import WebSocket
from .. import config

# Registre des tâches en cours
_tasks: dict[str, dict] = {}


@router.websocket("/ws/progress/{task_id}")
async def progress_ws(ws: WebSocket, task_id: str):
    if not _ws_authenticated(ws):
        await ws.close(code=4401)
        return
    await ws.accept()

    while True:
        task = _tasks.get(task_id)
        if not task:
            await ws.send_json({"status": "not_found"})
            break
        await ws.send_json({
            "status": task["status"],
            "progress_percent": task.get("progress", 0),
            "current_step": task.get("step", ""),
            "elapsed_seconds": int(time.time() - task["started_at"]),
            "estimated_remaining_seconds": task.get("eta", 0),
            "details": task.get("details", {}),
        })
        if task["status"] in ("done", "error"):
            break
        await asyncio.sleep(0.5)

    await ws.close()
```

## Phase 3 : Validation

### Endpoint backend

```
GET /api/recording_session/{id}/processed
  response: { 
    "clips": [{ "filename": "clip_001.wav", "duration_s": 8.4, "snr_db": 28.5, ... }],
    "quality_report": { "score": 87, ... }
  }

GET /api/recording_session/{id}/clip/{clip_id}/audio
  response: WAV binary

DELETE /api/recording_session/{id}/clip/{clip_id}
  response: { "ok": true }

GET /api/recording_session/{id}/export
  response: ZIP binary (avec progression via Content-Length)
```

### Frontend page validation

```html
<!-- Site/frontend/recording-session-validate.html -->
<main>
  <h1>Validation du dataset</h1>
  
  <div class="quality-card">
    <div class="score-big" id="qualityScore">87 / 100 ✅</div>
    <div class="score-details">
      <div>✅ Niveau audio : Excellent (-4 dB crête)</div>
      <div>✅ Bruit de fond : Faible (32 dB SNR)</div>
      <div>⚠️ 3 clips trop courts (à supprimer ?)</div>
      <div>✅ Diversité : 142 clips, 18 min total</div>
    </div>
  </div>

  <div class="clips-list">
    <!-- Boucle JS sur les clips -->
    <div class="clip-item">
      <span>clip_001.wav · 8.4s · SNR 28.5dB</span>
      <audio controls src="..."></audio>
      <button class="btn-delete">🗑</button>
    </div>
  </div>

  <div class="actions">
    <button class="btn btn-secondary">Supprimer les clips problématiques</button>
    <button class="btn btn-primary" id="btnDownload">📥 Télécharger le dataset (ZIP)</button>
  </div>
</main>
```

## Phase 4 : Entraînement Kaggle

### Tutoriel intégré

Page `/rvc` contient un onglet "Comment entraîner mon modèle" avec :

```markdown
## Étape 1 : Créer un compte Kaggle (gratuit)

1. Aller sur https://kaggle.com et créer un compte
2. Vérifier ton numéro de téléphone (requis pour l'accès GPU)

## Étape 2 : Forker le notebook RVC

[Lien direct vers notebook fourni : "Applio RVC Trainer"]

Le notebook est pré-configuré pour :
- 300 epochs (qualité standard, ~3h)
- 500 epochs (haute qualité, ~6h)

## Étape 3 : Uploader ton dataset

1. Sur Kaggle → "+ New Dataset"
2. Drag & drop ton ZIP VoiceBridge
3. Nom : `voicebridge-rvc-jc-v1`
4. Visibilité : Privé

## Étape 4 : Lancer le notebook

1. Ouvrir le notebook forké
2. Settings → GPU : `T4 x2` (gratuit)
3. Add data → ton dataset
4. Run All

## Étape 5 : Récupérer le modèle

À la fin du training :
- `model.pth` (~150 Mo) : le modèle RVC
- `added_*.index` (~50 Mo) : index FAISS

Télécharger les deux dans `/kaggle/working/`.

## Étape 6 : Importer dans VoiceBridge

[Bouton] → /rvc-import
```

## Phase 5 : Import dans VoiceBridge

### Endpoint backend

```
POST /api/rvc/upload
  multipart/form-data:
    pth_file: .pth (max 500 Mo)
    index_file: .index (optionnel, max 200 Mo)
    name: "JC voice v1"
    description: "..."
    voice_id: "uuid" (optional, lien vers voix existante)
    sample_rate: 40000
  response: { 
    "model_id": "uuid", 
    "task_id": "uuid",  // pour suivre le upload sur RunPod Volume
    "status": "validating" 
  }

GET /ws/progress/{task_id}  (upload progress + push to RunPod)

GET /api/rvc/models
  response: [{ "id": "...", "name": "...", "uploaded_at": "...", "status": "...", "size_mb": ... }]

GET /api/rvc/models/{id}
  response: { ... métadonnées détaillées ... }

DELETE /api/rvc/models/{id}
  
POST /api/rvc/models/{id}/test
  body: { "sample_text": "Hello world", "language": "en", "voice_ref_id": "..." }
  response: { "task_id": "uuid" }  // génère un audio test via RunPod
```

### Validation .pth côté Hostinger

```python
# Site/backend/app/services/rvc_models_store.py
def validate_pth_file(path: Path) -> dict:
    """Vérifie qu'un fichier est bien un .pth RVC valide.
    
    Checks :
    - Magic bytes PyTorch (0x80 0x02 ... 'PK' pour zip-based, ou autre)
    - Présence des keys attendus dans le checkpoint
    - Sample rate plausible (32k, 40k, 48k)
    """
    import torch
    try:
        ckpt = torch.load(path, map_location='cpu', weights_only=False)
    except Exception as e:
        raise ValueError(f"Fichier .pth corrompu : {e}")
    
    required_keys = ["weight", "config", "info"]
    for k in required_keys:
        if k not in ckpt:
            raise ValueError(f"Clé manquante dans le .pth : {k}")
    
    sample_rate = ckpt.get("config", [None]*16)[15] or 40000
    if sample_rate not in (32000, 40000, 48000):
        log.warning("sample_rate inhabituel : %d", sample_rate)
    
    return {
        "valid": True,
        "sample_rate": sample_rate,
        "version": ckpt.get("version", "unknown"),
        "f0": ckpt.get("f0", True),
    }
```

### Push vers RunPod Volume

```python
async def push_to_runpod_volume(model_id: str, pth_path: Path,
                                 index_path: Path | None,
                                 progress_cb) -> dict:
    """Upload .pth + .index sur le RunPod Network Volume.
    
    Utilise un Pod éphémère temporaire OU l'API RunPod si disponible.
    """
    # Approach A: Pod éphémère
    # 1. Spawn un Pod sur EU-FR-1 avec le Volume monté
    # 2. SCP les fichiers vers /runpod-volume/rvc_models/{model_id}/
    # 3. Détruire le Pod
    
    # Approach B: API RunPod (si dispo)
    # 1. POST /v2/network-volumes/{volume_id}/upload
    # 2. Stream le fichier
    
    # Approach C: pré-signed S3 URL (si RunPod expose S3)
    # 1. GET /v2/network-volumes/{volume_id}/upload-url
    # 2. PUT direct en HTTP
    
    # À implémenter selon la documentation RunPod actuelle.
    ...
```

### Stockage local

```
data/
  rvc_models/
    metadata.json           # liste des modèles
    {model_id}/
      info.json             # nom, description, dates, etc.
      pth_size               # juste taille pour réf (le fichier est sur RunPod Volume)
      sample_test.wav        # audio de test généré à l'import
      backup/                # optionnel : backup local du .pth si activé
        model.pth
        added_*.index
```

### Format metadata.json

```json
{
  "version": "1.0",
  "models": [
    {
      "id": "uuid-1234",
      "name": "JC voice v1",
      "description": "Entrainé sur 18 min d'audio le 26/04/2026",
      "voice_id": "jc_fr",
      "sample_rate": 40000,
      "f0": true,
      "version": "v2",
      "size_mb": 142,
      "uploaded_at": "2026-05-06T14:30:00Z",
      "trained_on_kaggle_at": "2026-04-26T10:00:00Z",
      "status": "active",
      "runpod_volume_path": "/runpod-volume/rvc_models/uuid-1234/",
      "test_audio_path": "data/rvc_models/uuid-1234/sample_test.wav"
    }
  ]
}
```

## Phase 6 : Utilisation en Live

### Sélection du modèle

Studio Live → Mode "Hybride accent natif" → sélecteur RVC qui s'affiche :

```html
<div class="field" id="rvcModelField" style="display:none">
  <label for="rvcModelSelect">Modèle RVC</label>
  <select id="rvcModelSelect">
    <option value="">— Sélectionner —</option>
    <option value="uuid-1234">JC voice v1 (FR)</option>
  </select>
</div>
```

### Pipeline live

```
Hostinger reçoit chunk audio
    ↓ flush_speech_gpu()
    ↓ POST /v2/<endpoint>/run
    ↓ {
        "input": {
          "operation": "live_pipeline",
          "mode": "gpu-hybrid",
          "audio": "<base64>",
          "src_lang": "fr",
          "target_lang": "en",
          "rvc_model_id": "uuid-1234",
          "translation_provider": "nllb"
        }
      }
    ↓ Stream retour :
        {"type": "transcript", "text": "Bonjour"}
        {"type": "translated", "text": "Hello"}
        {"type": "audio_pcm", "data": "...", "seq": 0}  ← F5-TTS native
        {"type": "audio_pcm", "data": "...", "seq": 1}  ← appliqué RVC
        ...
        {"type": "audio_end"}
```

### Cache .pth côté worker

Le worker RunPod garde en VRAM les derniers `.pth` utilisés (LRU cache, max 3 modèles en RAM = ~6 Go VRAM) :

```python
# runpod-worker/models/rvc.py
class RVCRouter:
    def __init__(self, cache_size=3):
        self.cache = {}  # model_id -> (model, last_used_ts)
        self.cache_size = cache_size

    def load(self, model_id: str):
        if model_id in self.cache:
            self.cache[model_id] = (self.cache[model_id][0], time.time())
            return self.cache[model_id][0]

        if len(self.cache) >= self.cache_size:
            # Evict LRU
            oldest = min(self.cache.items(), key=lambda kv: kv[1][1])
            del self.cache[oldest[0]]
            torch.cuda.empty_cache()

        log.info("loading rvc model from volume: %s", model_id)
        pth_path = f"/runpod-volume/rvc_models/{model_id}/model.pth"
        index_path = f"/runpod-volume/rvc_models/{model_id}/added.index"
        model = RVCModel.from_files(pth_path, index_path)
        self.cache[model_id] = (model, time.time())
        return model
```

## Pattern barre de progression UX

Toutes les opérations RVC longues utilisent ce pattern :

| Opération | Durée typique | Méthode |
|---|---|---|
| Recording session capture | Temps réel | UI native (compteur) |
| Phase 2 retraitement (5 blocs) | ~5 min | WebSocket /ws/progress |
| Upload .pth (150 Mo) | 30s-3min | XHR upload progress + WebSocket pour push RunPod |
| Test rapide RVC (15s audio) | ~5s | Polling /api/rvc/test/status |
| Live first cold start | 10-30s | Indicateur animation côté frontend |

## Tests

### Tests phase 2 retraitement

```python
# tests/test_audio_dataset_processor.py
import pytest
from pathlib import Path
from app.services.audio_dataset_processor import process_session

@pytest.fixture
def fake_session(tmp_path):
    """Crée un fake session_dir avec 5 blocs WAV de bruit blanc."""
    import numpy as np
    import soundfile as sf
    for i in range(1, 6):
        audio = np.random.randn(16000 * 60).astype(np.float32) * 0.3  # 1 min
        sf.write(tmp_path / f"block_{i}_raw.wav", audio, 16000)
    return tmp_path


def test_process_session_progress(fake_session):
    progress_calls = []
    def cb(percent, step, details):
        progress_calls.append((percent, step))
    
    result = process_session(fake_session, cb)
    
    assert result["clips_count"] > 0
    assert any(p[0] == 100 for p in progress_calls)  # finit bien à 100%
    assert progress_calls[0][0] < 50  # commence bas


def test_quality_report_low_score_for_noise(fake_session):
    result = process_session(fake_session, lambda *a: None)
    # Bruit blanc → mauvais SNR → score bas
    assert result["score"] < 50
```

### Tests phase 5 upload

```python
# tests/test_rvc_upload.py
def test_validate_pth_invalid():
    with pytest.raises(ValueError):
        validate_pth_file(Path("/tmp/random_data.bin"))


def test_validate_pth_valid(real_pth_path):
    info = validate_pth_file(real_pth_path)
    assert info["valid"] is True
    assert info["sample_rate"] in (32000, 40000, 48000)
```

## Coûts RVC

| Phase | Coût |
|---|---|
| Phase 1 enregistrement | 0€ (Hostinger CPU) |
| Phase 2 retraitement | 0€ (Hostinger CPU) |
| Phase 3 validation | 0€ |
| **Phase 4 entraînement Kaggle** | **0€** (gratuit) |
| Phase 5 upload + push RunPod | ~0.05€ (Pod éphémère 5 min) |
| Phase 6 utilisation live (8h/mois) | ~3€ (inclus dans le live multilingue) |
| Stockage RunPod Volume (par modèle ~200 Mo) | inclus dans 50 Go = 3.5€/mois |

**Total marginal pour ajouter le RVC : ~0.05€ à 3€/mois selon usage.**
