# 17 - Backend API V3 (extensions)

> **Document V3 nouveau.** Détail des nouveaux endpoints REST + WebSocket et des modifications des endpoints V1.
>
> Le doc `05-backend-api.md` (V1) reste valable pour les endpoints existants. Ce doc liste les ajouts/modifications V3.

## Routes V1 modifiées

### `/ws/stream` (modifié)

WebSocket existant `/ws/stream` étendu avec nouveaux champs dans le payload `configure`.

#### Nouveau payload configure (compatible V1)

```json
{
  "type": "configure",
  "voice_id": "juliette",                          // existant V1
  "language": "fr",                                 // existant V1
  "translate": true,                                // existant V1
  "translate_to": "en",                             // existant V1
  "output": "browser",                              // existant V1
  
  "mode": "gpu-clone",                              // NOUVEAU V3
  "translation_provider": "nllb",                   // NOUVEAU V3
  "rvc_model_id": "uuid-1234"                       // NOUVEAU V3 (si mode=gpu-hybrid)
}
```

#### Valeurs valides pour `mode`

| Valeur | Description | Pipeline |
|---|---|---|
| `cpu-fr-en` (défaut, rétrocompat V1) | Authentique CPU FR/EN | NeuTTS Hostinger CPU |
| `gpu-clone` | Multilingue ma voix | F5-TTS RunPod GPU |
| `gpu-native` | Voix native | F5-TTS native RunPod GPU |
| `gpu-hybrid` | Hybride accent natif | F5-TTS native + RVC RunPod GPU |

#### Valeurs valides pour `translation_provider`

| Valeur | Description |
|---|---|
| `opus-mt-cpu` (défaut V1) | OPUS-MT CPU Hostinger |
| `opus-mt-gpu` | OPUS-MT GPU RunPod |
| `nllb` | NLLB-200 GPU RunPod |
| `gpt-4o-mini` | GPT-4o-mini OpenAI |
| `gpt-4o` | GPT-4o OpenAI |
| `libretranslate` | LibreTranslate Hostinger (fallback) |

#### Validation backend

Si `mode` commence par `"gpu-"` :
- Vérifier que RunPod est configuré (`config.runpod_endpoint_id` non vide)
- Vérifier que la traduction `translation_provider` est compatible avec la paire `language`/`translate_to`

Si `mode == "gpu-hybrid"` :
- Vérifier que `rvc_model_id` est fourni
- Vérifier que le modèle RVC existe dans `data/rvc_models/metadata.json`

#### Nouveaux types de messages WS retour

En plus des messages V1 (`ready`, `transcript`, `translated`, `audio_pcm`, `audio_end`, `error`) :

```json
{
  "type": "warmup_progress",
  "step": "Loading Whisper",
  "progress_percent": 60,
  "message": "Cold start in progress..."
}
```

```json
{
  "type": "cost_update",
  "session_cost_eur": 0.034,
  "duration_seconds": 360,
  "provider_breakdown": {
    "runpod_gpu": 0.030,
    "openai": 0.004
  }
}
```

### `/api/translate/warmup` (étendu)

```
GET /api/translate/warmup?src=fr&tgt=en&provider=nllb
```

Nouveau paramètre `provider` (default = config user). Si non fourni, prend le défaut de Settings.

Retour étendu :
```json
{
  "status": "ready",
  "src": "fr",
  "tgt": "en",
  "provider": "nllb",
  "duration_ms": 1247
}
```

### `/api/settings` (étendu)

GET retour étendu :
```json
{
  "default_retention": "session",
  "model_unload_after_minutes": 15,
  "default_tts_engine": "neutts",
  "domain": "voicebridge.example.com",
  
  "default_live_mode": "gpu-clone",                    // NOUVEAU
  "default_translation_provider": "nllb",              // NOUVEAU
  "runpod_configured": true,                            // NOUVEAU (read-only)
  "openai_configured": false,                           // NOUVEAU (read-only)
  "libretranslate_url": "http://localhost:5000"        // NOUVEAU
}
```

PUT accepte tous les champs ci-dessus (sauf les `_configured` read-only).

## Nouvelles routes V3

### Cloud (RunPod + OpenAI)

#### `POST /api/cloud/runpod/configure`

Configure les credentials RunPod.

Body :
```json
{
  "api_key": "rpa_...",
  "endpoint_id": "abc123def",
  "volume_id": "vol_xyz789",
  "datacenter": "EU-FR-1"
}
```

Backend :
1. Chiffre `api_key` via Fernet
2. Stocke dans `config.json` :
   - `runpod_api_key_encrypted`
   - `runpod_endpoint_id` (en clair, non sensible)
   - `runpod_volume_id`
   - `runpod_datacenter`
3. Test de connexion automatique (appel `/health` RunPod)

Retour :
```json
{
  "ok": true,
  "test_passed": true,
  "endpoint_status": "ready",
  "datacenter": "EU-FR-1",
  "ping_ms": 12
}
```

#### `POST /api/cloud/openai/configure`

Configure la clé OpenAI.

Body :
```json
{
  "api_key": "sk-..."
}
```

Backend :
1. Chiffre via Fernet → `openai_api_key_encrypted`
2. Test de connexion (appel modèles list OpenAI)

Retour :
```json
{
  "ok": true,
  "test_passed": true,
  "models_available": ["gpt-4o-mini", "gpt-4o"]
}
```

#### `GET /api/cloud/status`

État global des intégrations cloud.

Retour :
```json
{
  "runpod": {
    "configured": true,
    "endpoint_id": "abc***def",
    "datacenter": "EU-FR-1",
    "ping_ms": 12,
    "workers_active": 0,
    "last_call_ago_seconds": 1438
  },
  "openai": {
    "configured": true,
    "api_key": "sk-...****"
  },
  "libretranslate": {
    "configured": false,
    "url": null
  }
}
```

#### `POST /api/cloud/runpod/warmup`

Pré-chauffe le worker RunPod (charge les modèles en VRAM).

Body :
```json
{
  "components": ["whisper", "f5tts", "nllb"]
}
```

Retour :
```json
{
  "task_id": "uuid-1234",
  "status": "queued"
}
```

Suivi via `/ws/progress/{task_id}` (cf. `16-progress-ux-pattern.md`).

#### `POST /api/cloud/openai/test`

Test d'une traduction simple via OpenAI pour valider la clé.

Body :
```json
{
  "model": "gpt-4o-mini"
}
```

Retour :
```json
{
  "ok": true,
  "test_translation": "Hello world",
  "tokens_used": 12,
  "cost_eur": 0.0001
}
```

### Translation router

#### `GET /api/translate/providers`

Liste tous les providers de traduction et leur disponibilité.

Retour :
```json
{
  "providers": [
    {
      "id": "opus-mt-cpu",
      "name": "OPUS-MT CPU (Hostinger)",
      "is_local": true,
      "available": true,
      "supports_pairs": [["fr", "en"], ["en", "fr"]]
    },
    {
      "id": "opus-mt-gpu",
      "name": "OPUS-MT GPU (RunPod)",
      "is_local": false,
      "available": true,
      "supports_pairs": [["fr","en"],["en","fr"],["fr","de"],["de","fr"],["fr","es"],["es","fr"],["fr","it"],["it","fr"]]
    },
    {
      "id": "nllb",
      "name": "NLLB-200 (RunPod, 200+ langues)",
      "is_local": false,
      "available": true,
      "supports_universal": true
    },
    {
      "id": "gpt-4o-mini",
      "name": "GPT-4o-mini (OpenAI)",
      "is_local": false,
      "available": true,
      "supports_universal": true,
      "cost_per_1000_translations_eur": 0.04
    }
  ]
}
```

#### `POST /api/translate/translate`

Traduit un texte avec le provider choisi.

Body :
```json
{
  "text": "Bonjour le monde",
  "src": "fr",
  "tgt": "en",
  "provider": "nllb",
  "glossary": {"CODIR": "Executive Committee"},
  "fallback": true
}
```

Retour :
```json
{
  "translated": "Hello world",
  "provider": "nllb",
  "duration_ms": 142,
  "cost_eur": 0.0
}
```

### RVC

#### `GET /api/rvc/models`

Liste les modèles RVC de l'utilisateur.

Retour :
```json
{
  "models": [
    {
      "id": "uuid-1234",
      "name": "JC voice v1",
      "description": "Entrainé sur 18 min d'audio",
      "voice_id": "jc_fr",
      "sample_rate": 40000,
      "f0": true,
      "version": "v2",
      "size_mb": 142,
      "uploaded_at": "2026-05-06T14:30:00Z",
      "trained_on_kaggle_at": "2026-04-26T10:00:00Z",
      "status": "active",
      "test_audio_url": "/api/rvc/models/uuid-1234/test.wav"
    }
  ]
}
```

#### `GET /api/rvc/models/{id}`

Métadonnées détaillées d'un modèle.

#### `POST /api/rvc/upload`

Upload .pth + .index.

Body : `multipart/form-data`
- `pth_file` : fichier .pth (max 500 Mo)
- `index_file` : fichier .index (optionnel, max 200 Mo)
- `name` : str (max 50 char)
- `description` : str (max 500 char)
- `voice_id` : str (optionnel, lien vers voix existante)
- `sample_rate` : int (40000 par défaut)

Retour immédiat (avant fin upload sur RunPod Volume) :
```json
{
  "model_id": "uuid-new",
  "task_id": "uuid-task",
  "status": "uploading"
}
```

Le `task_id` permet de suivre la progression via `/ws/progress/{task_id}`.

#### `DELETE /api/rvc/models/{id}`

Supprime un modèle (local + RunPod Volume).

Retour : `{"ok": true}`

#### `POST /api/rvc/models/{id}/test`

Teste un modèle RVC avec un audio sample.

Body :
```json
{
  "sample_text": "Hello world, this is a test",
  "language": "en",
  "voice_ref_id": "uuid-ref"
}
```

Retour :
```json
{
  "task_id": "uuid-task",
  "audio_url": "/api/rvc/models/{id}/test_results/{task_id}.wav"
}
```

#### `GET /api/rvc/models/{id}/test_results/{task_id}.wav`

Récupère l'audio test (disponible une fois la tâche terminée).

#### `GET /api/rvc/guide.pdf`

Sert le guide PDF téléchargeable (généré à la volée + cache 24h).

### Recording session

#### `POST /api/recording_session/create`

Crée une nouvelle session d'enregistrement.

Body :
```json
{
  "name": "JC voice v1",
  "language": "fr"
}
```

Retour :
```json
{
  "session_id": "uuid-session",
  "blocks": [
    {"index": 1, "title": "Texte phonétique", "target_duration_sec": 300},
    {"index": 2, "title": "Conversationnel", "target_duration_sec": 300},
    {"index": 3, "title": "Intonations", "target_duration_sec": 180},
    {"index": 4, "title": "Nombres et jargon", "target_duration_sec": 120},
    {"index": 5, "title": "Lecture libre", "target_duration_sec": 300}
  ]
}
```

#### `POST /api/recording_session/{id}/append_chunk?block=N`

Ajoute un chunk audio à un bloc.

Headers : `Content-Type: application/octet-stream`
Body : raw bytes (PCM 16-bit mono 16kHz int16)

Retour : `{"ok": true, "received_samples": 80000}`

#### `POST /api/recording_session/{id}/finish_block`

Marque un bloc comme terminé.

Body : `{"block": 1}`

Retour :
```json
{
  "ok": true,
  "block": 1,
  "duration_seconds": 312
}
```

#### `POST /api/recording_session/{id}/clear_block`

Vide un bloc (pour permettre de recommencer).

Body : `{"block": 1}`

#### `POST /api/recording_session/{id}/process`

Lance le retraitement asynchrone.

Body :
```json
{
  "denoise_strength": 0.7,
  "min_clip_seconds": 5,
  "max_clip_seconds": 15
}
```

Retour :
```json
{
  "task_id": "uuid-task",
  "status": "queued"
}
```

Progression via `/ws/progress/{task_id}`.

#### `GET /api/recording_session/{id}/processed`

Liste les clips après traitement.

Retour :
```json
{
  "quality_report": {
    "score": 87,
    "total_clips": 142,
    "total_duration_s": 1086,
    "snr_avg_db": 32.4,
    "snr_min_db": 24.1,
    "duration_distribution": {
      "<5s": 0,
      "5-10s": 87,
      "10-15s": 55,
      ">15s": 0
    }
  },
  "clips": [
    {
      "filename": "clip_001.wav",
      "duration_s": 8.4,
      "snr_db": 28.5,
      "peak_db": -3.2,
      "rms_db": -18.1,
      "block_origin": 1,
      "url": "/api/recording_session/{id}/clip/clip_001/audio"
    }
  ]
}
```

#### `GET /api/recording_session/{id}/clip/{clip_id}/audio`

Audio d'un clip (WAV).

#### `DELETE /api/recording_session/{id}/clip/{clip_id}`

Supprime un clip.

#### `GET /api/recording_session/{id}/export`

Export ZIP du dataset prêt pour Kaggle.

Headers retour :
- `Content-Type: application/zip`
- `Content-Disposition: attachment; filename="voicebridge-rvc-{name}.zip"`

Le ZIP contient :
- `clips/clip_001.wav` ... `clips/clip_NNN.wav`
- `manifest.json`

#### `GET /api/recording_session/{id}`

Métadonnées de la session.

#### `DELETE /api/recording_session/{id}`

Supprime la session (local files).

### Progression de tâches longues

#### `WS /ws/progress/{task_id}`

Stream des updates en temps réel.

Format émis :
```json
{
  "task_id": "uuid",
  "status": "running",
  "progress_percent": 42,
  "current_step": "Découpage en clips (3/6)",
  "elapsed_seconds": 23,
  "estimated_remaining_seconds": 120,
  "logs": ["[09:14:23] VAD detection terminée"],
  "result": null
}
```

À la fin (status = `done`) :
```json
{
  "status": "done",
  "progress_percent": 100,
  "result": {...},
  ...
}
```

En cas d'erreur :
```json
{
  "status": "error",
  "error": "denoise_failed: ...",
  ...
}
```

#### `GET /api/tasks/{task_id}/status`

Polling fallback pour récupérer l'état d'une tâche.

#### `POST /api/tasks/{task_id}/cancel`

Demande l'annulation (best-effort).

## Modifications de `Site/backend/app/main.py`

Inclure les nouveaux routers :

```python
# main.py - lignes à ajouter dans la section "Routers"
from .routes import cloud as r_cloud
from .routes import rvc as r_rvc
from .routes import recording_session as r_rec_session
from .routes import progress as r_progress

app.include_router(r_cloud.router)
app.include_router(r_rvc.router)
app.include_router(r_rec_session.router)
app.include_router(r_progress.router)
```

## Pages frontend nouvelles à enregistrer

```python
# main.py - section _HTML_PAGES
_HTML_PAGES = {
    "/studio": "studio.html",
    "/voices": "voices.html",
    "/voices/new": "voices-new.html",
    "/recordings": "recordings.html",
    "/detection": "detection.html",
    "/settings": "settings.html",
    
    # NOUVEAU V3
    "/rvc": "rvc.html",
    "/rvc/import": "rvc-import.html",
    "/recording-session": "recording-session.html",
    "/recording-session/{session_id}/validate": "recording-session-validate.html",
}
```

## Sécurité

### Routes V3 derrière `Depends(require_auth)`

Toutes les routes V3 (`/api/cloud/*`, `/api/rvc/*`, `/api/recording_session/*`, `/api/translate/*`) sont protégées par le middleware d'auth global de `main.py` ET par `Depends(require_auth)` au niveau du router.

### Rate limiting

```python
# routes/cloud.py
from ..limiter import limiter

@router.post("/runpod/warmup")
@limiter.limit("10/hour")  # éviter les abus
async def warmup(request: Request, payload: WarmupRequest):
    ...

@router.post("/openai/test")
@limiter.limit("20/hour")
async def test_openai(request: Request, payload: OpenAITestRequest):
    ...
```

```python
# routes/translate.py
@router.post("/translate")
@limiter.limit("60/minute")
async def translate(request: Request, payload: TranslateRequest):
    ...
```

### Path traversal

Tous les `model_id`, `session_id`, `clip_id`, `task_id` doivent être validés via `utils/files.safe_id()` (regex `^[A-Za-z0-9_-]+$`).

### Magic bytes uploads

Les uploads .pth doivent être validés via `python-magic` ET vérification de la signature PyTorch :
```python
def validate_pth_magic(path: Path) -> bool:
    with path.open('rb') as f:
        head = f.read(4)
    # PyTorch saved file = ZIP archive (PK\x03\x04) ou pickle (\x80\x02 ou \x80\x05)
    return head[:2] == b'PK' or head[:1] == b'\x80'
```

## Codes d'erreur

| HTTP | error code | Description |
|---|---|---|
| 400 | `invalid_payload` | Payload mal formé |
| 400 | `unsupported_pair` | Paire de langues non supportée par le provider |
| 401 | `unauthorized` | Auth requise |
| 403 | `runpod_not_configured` | RunPod pas configuré pour route GPU |
| 404 | `model_not_found` | Modèle RVC ou session introuvable |
| 422 | `invalid_mode` | Mode Live invalide |
| 429 | `rate_limited` | Trop de requêtes |
| 500 | `internal_error` | Erreur interne |
| 502 | `runpod_error` | Erreur côté RunPod |
| 502 | `openai_error` | Erreur côté OpenAI |
| 503 | `service_unavailable` | RunPod/OpenAI down |

## Format des erreurs

```json
{
  "detail": {
    "error": "runpod_not_configured",
    "message": "RunPod n'est pas configuré. Allez dans Réglages > Cloud."
  }
}
```
