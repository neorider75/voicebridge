# 05 - API Backend complète

## Stack backend

- FastAPI (Python 3.11)
- Uvicorn (ASGI server)
- WebSockets natifs FastAPI
- SQLAlchemy ❌ NON utilisé (pas de BDD)
- Données : fichiers JSON + filesystem

## Structure du projet

```
/var/voicebridge/
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI app + middleware
│   ├── config.py               # Lecture config.json
│   ├── auth.py                 # Auth + sessions + tokens
│   ├── models/
│   │   ├── tts.py              # Wrapper NeuTTS
│   │   ├── stt.py              # Wrapper Kyutai
│   │   ├── vad.py              # Wrapper Silero
│   │   ├── detection.py        # Wrapper Deepfake-audio-detection-V2 + Perth
│   │   └── manager.py          # Chargement/déchargement modèles
│   ├── routes/
│   │   ├── auth.py
│   │   ├── voices.py
│   │   ├── tts.py
│   │   ├── stt.py
│   │   ├── live.py             # WebSocket
│   │   ├── recordings.py
│   │   ├── detection.py
│   │   ├── settings.py
│   │   └── system.py
│   ├── services/
│   │   ├── audio.py            # ffmpeg, conversion
│   │   ├── url_extract.py      # yt-dlp
│   │   ├── retention.py        # Cron nettoyage
│   │   └── prechauffage.py
│   └── utils/
│       ├── security.py         # Hash, rate limit
│       └── files.py            # Helpers filesystem
├── data/
│   ├── config.json
│   ├── voices/
│   │   ├── metadata.json
│   │   ├── jc_fr.wav
│   │   ├── encoded/
│   │   │   └── jc_fr.pt
│   │   └── ...
│   ├── audio/                  # générés
│   ├── models/                 # ML téléchargés
│   ├── install/
│   │   └── VoiceBridge.app.zip
│   └── logs/
│       └── app.log
├── manage.py                   # CLI (reset password, etc.)
├── requirements.txt
└── voicebridge.service         # systemd unit file
```

## Endpoints REST

### Authentification

#### POST /api/auth/login
**Body** :
```json
{ "password": "user_password" }
```
**Réponse 200** :
```json
{ "success": true }
```
+ Set-Cookie session sécurisé

**Réponse 401** :
```json
{ "error": "invalid_password", "remaining_attempts": 3 }
```

**Rate limit** : 5/15min par IP

#### POST /api/auth/logout
Invalide la session.

#### GET /api/auth/check
Vérifie si la session est valide.
**Réponse 200** : `{ "authenticated": true }`

---

### Système

#### GET /api/system/status
Pas d'authentification requise (utilisé pour le polling header).

**Réponse** :
```json
{
  "ram": {
    "used_gb": 4.2,
    "total_gb": 16.0,
    "percent": 26
  },
  "storage": {
    "used_gb": 2.1,
    "total_gb": 200.0,
    "percent": 1
  },
  "models": {
    "neutts_fr_q4": "loaded",
    "neutts_en_q4": "loaded",
    "neutts_fr_q8": "unloaded",
    "neutts_en_q8": "unloaded",
    "kyutai": "loaded",
    "detect2b": "unloaded",
    "silero_vad": "loaded"
  },
  "latency_ms": 820,
  "voicebridge_connected": true,
  "uptime_seconds": 86400,
  "status": "ready"
}
```

`status` peut être : `ready`, `idle`, `warming_up`, `error`

#### POST /api/system/prechauffage
**Body** :
```json
{ "language": "fr", "voice_id": "jc_fr" }
```
**Réponse** :
```json
{ "success": true, "duration_ms": 5800 }
```

#### POST /api/system/clean
Décharge tous les modèles et supprime les fichiers audio temporaires.
**Réponse** :
```json
{ "success": true, "freed_ram_gb": 3.5, "freed_storage_mb": 48 }
```

---

### Voix

#### GET /api/voices
**Réponse** :
```json
{
  "voices": [
    {
      "id": "jc_fr",
      "name": "JC - Français",
      "language": "fr",
      "backbone": "neutts-nano-french",
      "duration_seconds": 12,
      "created_at": "2026-04-26T14:30:00Z",
      "protected": false
    },
    {
      "id": "juliette",
      "name": "Juliette",
      "language": "fr",
      "backbone": "neutts-nano-french",
      "duration_seconds": 11,
      "created_at": "2026-04-26T00:00:00Z",
      "protected": true
    }
  ]
}
```

#### GET /api/voices/{voice_id}/audio
Sert le fichier WAV de la voix de référence (pour écoute dans Mes voix).

Headers : `Content-Type: audio/wav`

#### POST /api/voices
Création par enregistrement ou import.

**Multipart/form-data** :
- `name` : str
- `language` : "fr" ou "en"
- `audio_file` : fichier (WAV/MP3/M4A/OGG, max 10 Mo)

**Réponse 201** :
```json
{ "id": "voice_abc123", "name": "...", "..." }
```

#### POST /api/voices/from-url
Création par extraction URL.

**Body** :
```json
{
  "name": "JC depuis YouTube",
  "language": "fr",
  "url": "https://youtube.com/watch?v=..."
}
```

**Réponse** : streaming SSE avec progression
```
event: progress
data: {"step": "download", "percent": 25}

event: progress
data: {"step": "extract", "percent": 50}

event: progress
data: {"step": "convert", "percent": 75}

event: progress
data: {"step": "trim", "percent": 100}

event: result
data: {"id": "voice_abc123", "preview_url": "/api/voices/voice_abc123/preview"}
```

#### GET /api/voices/{voice_id}/preview
Sert le fichier WAV extrait avant validation finale.

#### POST /api/voices/{voice_id}/confirm
Valide l'ajout d'une voix créée par URL (encode en .pt et l'ajoute officiellement).

#### PUT /api/voices/{voice_id}
Modifier une voix.

**Multipart/form-data** :
- `name` : str (optionnel)
- `language` : "fr" ou "en" (optionnel)
- `audio_file` : fichier (optionnel, déclenche re-encodage)

#### DELETE /api/voices/{voice_id}
Supprimer une voix. Refuse si `protected: true`.

---

### TTS

#### POST /api/tts/generate
**Body** :
```json
{
  "text": "Bonjour, je m'appelle...",
  "voice_id": "jc_fr",
  "format": "wav",
  "quality": "high",
  "retention": "session"
}
```

`format` : "wav" ou "mp3"
`quality` : "normal" (Q4) ou "high" (Q8)
`retention` : "session" ou "24h" ou "48h"

**Si `retention == "session"`** :
- Réponse streaming : audio binaire direct (Content-Type: audio/wav ou audio/mpeg)
- Pas de fichier écrit sur disque

**Si `retention == "24h"` ou `"48h"`** :
- Fichier écrit dans `/data/audio/`
- Réponse JSON :
```json
{
  "id": "rec_xyz",
  "url": "/api/recordings/rec_xyz/audio",
  "expires_at": "2026-04-27T14:32:00Z"
}
```

---

### STT

#### POST /api/stt/transcribe
**Multipart/form-data** :
- `audio` : fichier WAV
- `language` : "fr" ou "en"

**Réponse** :
```json
{
  "text": "Bonjour, je m'appelle...",
  "duration_seconds": 8.3,
  "audio_url": "/tmp/stt_xyz.wav"  // pour rejouer dans le studio
}
```

Le fichier audio temporaire est conservé en session uniquement (pour permettre la lecture côté client).

#### POST /api/stt/generate
Synthèse à partir d'un texte transcrit (étape 3 du STT).

Identique à `/api/tts/generate`.

---

### Live (WebSocket)

#### WS /ws/stream
Authentification : Bearer token (header `Authorization: Bearer sk-...`) OU cookie session.

**Messages client → serveur** :

```json
{ "type": "configure", "voice_id": "jc_fr", "language": "fr", "output": "browser" }
{ "type": "audio_chunk", "data": "base64encoded..." }
{ "type": "stop" }
```

**Messages serveur → client** :

```json
{ "type": "ready" }
{ "type": "audio_chunk", "data": "base64encoded..." }
{ "type": "buffer_status", "level": "ok" | "warning" | "critical" }
{ "type": "reconnecting" }
{ "type": "reconnected" }
{ "type": "state_update", "active_voice": "jc_fr" }
```

**Comportement** :
- Reçoit chunks audio toutes les ~100ms
- Silero VAD analyse en continu
- Quand silence > 400ms ou chunk > 4s : envoie au pipeline STT → TTS
- Buffer circulaire 5s en deque(maxlen=25)
- En cas de coupure : envoie "reconnecting" puis "reconnected"

---

### Enregistrements

#### GET /api/recordings
**Query params** :
- `mode` : "all" | "tts" | "stt" | "live" (default: "all")
- `sort` : "date_desc" | "date_asc" (default: "date_desc")

**Réponse** :
```json
{
  "recordings": [
    {
      "id": "rec_xyz",
      "mode": "tts",
      "voice_name": "JC - Français",
      "voice_language": "fr",
      "created_at": "2026-04-26T14:32:00Z",
      "expires_at": "2026-04-27T14:32:00Z",
      "duration_seconds": 12,
      "format": "wav",
      "quality": "high",
      "size_mb": 0.4
    }
  ],
  "total_count": 12,
  "total_size_mb": 48
}
```

#### GET /api/recordings/{id}/audio
Sert le fichier audio.

#### DELETE /api/recordings/{id}
Supprime un enregistrement.

---

### Détection

#### POST /api/detection/analyze
**Multipart/form-data** :
- `audio` : fichier (WAV/MP3/M4A/OGG, max 50 Mo)
- `mode` : "watermark" | "spectral" | "both"

**Réponse** :
```json
{
  "verdict": "ai_generated",
  "confidence": 94.1,
  "watermark": {
    "checked": true,
    "detected": true,
    "tampered": false
  },
  "spectral": {
    "checked": true,
    "result": "synthetic",
    "confidence": 94.1,
    "model": "Deepfake-audio-detection-V2"
  },
  "metadata": {
    "filename": "enregistrement.wav",
    "duration_seconds": 12.3,
    "analyzed_at": "2026-04-26T14:32:00Z"
  }
}
```

`verdict` : "ai_generated" ou "human"

---

### Réglages

#### GET /api/settings
**Réponse** :
```json
{
  "default_retention": "session",
  "model_unload_after_minutes": 15,
  "domain": "voicebridge.example.com"
}
```

#### PUT /api/settings
**Body** : champs à mettre à jour (partiel).

#### POST /api/settings/password
**Body** :
```json
{ "current_password": "...", "new_password": "..." }
```

#### GET /api/settings/api-key
**Réponse** :
```json
{
  "masked": "sk-••••••••••••••••••••4f2a",
  "created_at": "2026-04-26T14:30:00Z"
}
```

#### POST /api/settings/api-key/generate
Génère une nouvelle clé. Révoque l'ancienne immédiatement.

**Réponse** :
```json
{
  "key": "sk-a8f3bc9d2e1f4a7b6c5d8e9f0a1b2c3d",
  "created_at": "2026-04-26T15:00:00Z"
}
```

⚠️ La clé en clair n'est retournée **qu'une seule fois**, jamais re-récupérable.

---

## WebSocket events broadcast

Quand un client modifie l'état (changement de voix active depuis VoiceBridge.app), le serveur broadcast à tous les clients connectés :

```json
{ "type": "state_update", "active_voice": "jc_fr" }
{ "type": "voicebridge_connected", "value": true }
{ "type": "voicebridge_connected", "value": false }
```

## Codes d'erreur

| Code | Cas |
|---|---|
| 400 | Body invalide, format non supporté |
| 401 | Non authentifié |
| 403 | Action interdite (ex: supprimer voix protégée) |
| 404 | Ressource introuvable |
| 413 | Fichier trop gros |
| 422 | Validation échouée |
| 429 | Rate limit dépassé |
| 500 | Erreur serveur |
| 503 | Modèle en cours de chargement |

Format des erreurs :
```json
{ "error": "code_court", "message": "Message lisible en français" }
```

## Middleware

### Auth middleware
- Toutes les routes `/api/*` nécessitent authentification SAUF :
  - `/api/auth/login`
  - `/api/system/status` (pour le polling)
- Vérifie cookie session OU Bearer token
- Si non authentifié : 401

### Rate limiting (slowapi)
- `/api/auth/login` : 5/15min par IP
- `/api/voices` POST : 10/min par session
- `/api/tts/generate` : 60/min par session
- `/api/detection/analyze` : 20/min par session
- WebSocket : pas de rate limit (déjà limité par bandwidth)

### CORS
- Origines autorisées : domaine configuré uniquement
- Pas de wildcard

### Headers de sécurité
- Content-Security-Policy: default-src 'self'; style-src 'self' fonts.googleapis.com; font-src 'self' fonts.gstatic.com
- X-Frame-Options: DENY
- X-Content-Type-Options: nosniff
- Strict-Transport-Security: max-age=31536000; includeSubDomains

## Logging

- Tous les logs dans `/var/voicebridge/data/logs/app.log`
- Rotation hebdomadaire via logrotate
- Format : `[ISO8601] [LEVEL] [request_id] message`
- Niveaux : DEBUG (dev) / INFO (prod) / WARNING / ERROR
- Évènements logués obligatoirement :
  - Tentatives login (succès et échecs avec IP)
  - Génération nouvelle clé API
  - Modifications config
  - Erreurs serveur
  - Suppressions (voix, enregistrements)
- Évènements **NON** logués (privacy) :
  - Contenu des textes synthétisés
  - Contenu audio
  - Mots de passe (même partiels)
