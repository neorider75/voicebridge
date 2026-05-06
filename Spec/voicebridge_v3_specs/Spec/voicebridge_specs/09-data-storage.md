# 09 - Stockage et données

## Pas de base de données

VoiceBridge n'utilise **aucune base de données SQL**. Toutes les données persistantes sont stockées dans des fichiers JSON ou directement sur le filesystem.

Avantages :
- Plus simple à backuper
- Pas d'injection SQL possible par construction
- Pas de processus DB supplémentaire à maintenir
- Lecture/écriture en RAM rapide pour les volumes prévus

## Structure complète

```
/var/voicebridge/
├── app/                            # Code Python (read-only en runtime)
│   └── ...
├── venv/                           # Virtualenv Python
└── data/                           # Données persistantes
    ├── config.json                 # Configuration et secrets
    ├── voices/
    │   ├── metadata.json           # Métadonnées des voix
    │   ├── jc_fr.wav               # Audio référence
    │   ├── jc_en.wav
    │   ├── juliette.wav            # Voix protégée par défaut
    │   ├── dave.wav                # Voix protégée par défaut
    │   └── encoded/
    │       ├── jc_fr.pt            # ref_codes pré-encodés
    │       ├── jc_en.pt
    │       ├── juliette.pt
    │       └── dave.pt
    ├── audio/                      # Fichiers générés (rétention)
    │   ├── metadata.json           # Métadonnées des enregistrements
    │   ├── rec_xyz123.wav
    │   ├── rec_xyz123.json         # Métadonnées du fichier
    │   └── ...
    ├── models/                     # Modèles ML (read-only après install)
    │   ├── neutts-nano-fr-q4/
    │   ├── neutts-nano-en-q4/
    │   ├── neutts-nano-fr-q8/
    │   ├── neutts-nano-en-q8/
    │   ├── neucodec/
    │   ├── kyutai-1b/
    │   ├── deepfake-detection-v2/
    │   └── silero-vad/
    ├── install/                    # Fichiers à télécharger
    │   └── VoiceBridge.app.zip
    ├── tmp/                        # Fichiers temporaires (session)
    └── logs/
        └── app.log
```

## Format config.json

```json
{
  "domain": "voicebridge.example.com",
  "password_hash": "$2b$12$....",
  "api_token_hash": "sha256_of_the_token",
  "api_token_created_at": "2026-04-26T14:30:00Z",
  "session_secret": "random_64_chars_for_signing_sessions",
  "default_retention": "session",
  "model_unload_after_minutes": 15,
  "version": "1.0.0",
  "installed_at": "2026-04-26T14:00:00Z"
}
```

### Champs

| Champ | Type | Description |
|---|---|---|
| `domain` | string | Domaine HTTPS (sans schéma) |
| `password_hash` | string | bcrypt hash du mot de passe admin |
| `api_token_hash` | string | SHA-256 hash du token API actif |
| `api_token_created_at` | ISO 8601 | Date de génération du token |
| `session_secret` | string | Clé pour signer les cookies session |
| `default_retention` | enum | "session" / "24h" / "48h" |
| `model_unload_after_minutes` | int | 15 / 30 / 60 |
| `version` | string | Version installée |
| `installed_at` | ISO 8601 | Date d'installation |

### Permissions
```bash
chmod 600 /var/voicebridge/data/config.json
chown voicebridge:voicebridge /var/voicebridge/data/config.json
```

## Format voices/metadata.json

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
      "updated_at": "2026-04-26T14:30:00Z",
      "protected": false,
      "source": "recording",
      "source_url": null
    },
    {
      "id": "voice_youtube_abc",
      "name": "Speaker YouTube",
      "language": "en",
      "backbone": "neutts-nano",
      "duration_seconds": 15,
      "created_at": "2026-04-26T16:45:00Z",
      "updated_at": "2026-04-26T16:45:00Z",
      "protected": false,
      "source": "url",
      "source_url": "https://youtube.com/watch?v=..."
    },
    {
      "id": "juliette",
      "name": "Juliette",
      "language": "fr",
      "backbone": "neutts-nano-french",
      "duration_seconds": 11,
      "created_at": "2026-04-26T00:00:00Z",
      "updated_at": "2026-04-26T00:00:00Z",
      "protected": true,
      "source": "default",
      "source_url": null
    }
  ]
}
```

### Champs

| Champ | Type | Description |
|---|---|---|
| `id` | string | UUID interne (ne contient que `[a-z0-9_]`) |
| `name` | string | Nom affiché (max 50 chars) |
| `language` | enum | "fr" ou "en" |
| `backbone` | string | Modèle NeuTTS associé |
| `duration_seconds` | int | Durée audio référence |
| `created_at` | ISO 8601 | Date de création |
| `updated_at` | ISO 8601 | Date de dernière modification |
| `protected` | bool | true = ne peut pas être supprimée |
| `source` | enum | "recording" / "import" / "url" / "default" |
| `source_url` | string\|null | URL d'origine si source=url |

### Génération des IDs

```python
import re
import uuid

def generate_voice_id(name: str) -> str:
    # Sanitize le nom
    base = re.sub(r'[^a-z0-9_]', '_', name.lower())
    # Ajoute un suffixe unique court
    suffix = uuid.uuid4().hex[:6]
    return f"{base}_{suffix}"
```

## Format audio/metadata.json (enregistrements)

```json
{
  "recordings": [
    {
      "id": "rec_abc123",
      "mode": "tts",
      "voice_id": "jc_fr",
      "voice_name": "JC - Français",
      "voice_language": "fr",
      "format": "wav",
      "quality": "high",
      "duration_seconds": 12,
      "size_bytes": 412800,
      "created_at": "2026-04-26T14:32:00Z",
      "expires_at": "2026-04-27T14:32:00Z",
      "retention": "24h"
    }
  ]
}
```

### Champs

| Champ | Type | Description |
|---|---|---|
| `id` | string | UUID interne |
| `mode` | enum | "tts" / "stt" / "live" |
| `voice_id` | string | Référence à la voix utilisée |
| `voice_name` | string | Cache du nom (au cas où la voix est supprimée) |
| `voice_language` | enum | "fr" ou "en" |
| `format` | enum | "wav" / "mp3" |
| `quality` | enum | "normal" (Q4) / "high" (Q8) |
| `duration_seconds` | int | Durée audio |
| `size_bytes` | int | Taille fichier |
| `created_at` | ISO 8601 | Date de génération |
| `expires_at` | ISO 8601 | Date d'expiration |
| `retention` | enum | "24h" / "48h" |

⚠️ Les enregistrements en mode "session" **n'apparaissent pas ici** car jamais écrits sur disque.

## Format des fichiers individuels

Chaque enregistrement a son fichier audio + un fichier .json compagnon optionnel pour les métadonnées détaillées :

```
audio/
├── rec_abc123.wav              # Audio
└── rec_abc123.json             # Métadonnées détaillées (optionnel)
```

`rec_abc123.json` :
```json
{
  "source_text": "Bonjour, je m'appelle...",  // Pour TTS uniquement
  "transcription": "Bonjour, je m'appelle...",  // Pour STT uniquement
  "settings_used": {
    "voice_id": "jc_fr",
    "format": "wav",
    "quality": "high"
  }
}
```

⚠️ Privacy : les fichiers `.json` ne contiennent JAMAIS le texte source. Seules les métadonnées techniques. Cf. principe privacy by design.

## Concurrence et accès aux fichiers JSON

### Lecture
- Les fichiers JSON sont lus à chaque requête (pas de cache)
- Volumes prévus négligeables (< 100 voix typiquement, < 1000 enregistrements)
- Lecture < 1ms

### Écriture
- Toutes les écritures passent par un lock (asyncio.Lock par fichier)
- Pattern : read → modify → write avec lock
- Écriture atomique : write to .tmp puis rename

```python
async def update_voices_metadata(updater_func):
    async with voices_lock:
        with open(VOICES_META) as f:
            data = json.load(f)
        data = updater_func(data)
        with open(VOICES_META + '.tmp', 'w') as f:
            json.dump(data, f, indent=2)
        os.rename(VOICES_META + '.tmp', VOICES_META)
```

## Cron de nettoyage

Toutes les heures, le cron exécute `python manage.py cleanup-expired` qui :

1. Lit `audio/metadata.json`
2. Identifie les enregistrements avec `expires_at < now()`
3. Supprime les fichiers audio + .json compagnons
4. Met à jour `audio/metadata.json` (retire les entrées)

```python
async def cleanup_expired():
    async with audio_lock:
        with open(AUDIO_META) as f:
            data = json.load(f)
        
        now = datetime.utcnow()
        kept = []
        for rec in data["recordings"]:
            expires = datetime.fromisoformat(rec["expires_at"].replace("Z", ""))
            if expires < now:
                # Supprimer fichiers
                wav_path = AUDIO_DIR / f"{rec['id']}.wav"
                mp3_path = AUDIO_DIR / f"{rec['id']}.mp3"
                meta_path = AUDIO_DIR / f"{rec['id']}.json"
                for p in [wav_path, mp3_path, meta_path]:
                    if p.exists():
                        p.unlink()
            else:
                kept.append(rec)
        
        data["recordings"] = kept
        atomic_write_json(AUDIO_META, data)
```

## Backup

### Quoi sauvegarder régulièrement
- `config.json` (config + secrets)
- `voices/` (toute la bibliothèque vocale)

### Quoi NE PAS sauvegarder
- `audio/` : généré, peut être supprimé
- `models/` : retéléchargeable
- `logs/`
- `tmp/`

### Recommandation utilisateur
- Backup hebdomadaire via SSH/scp ou rsync
- Inclure dans le README :
  ```bash
  scp -r user@vps:/var/voicebridge/data/voices ./backup/
  scp user@vps:/var/voicebridge/data/config.json ./backup/
  ```

## Restauration

Pour restaurer après réinstallation :
1. Lancer `install.sh` (réinstalle tout)
2. Stop le service : `systemctl stop voicebridge`
3. Restaurer les fichiers : `scp -r ./backup/voices user@vps:/var/voicebridge/data/`
4. Restaurer config : `scp ./backup/config.json user@vps:/var/voicebridge/data/`
5. Permissions : `chown -R voicebridge:voicebridge /var/voicebridge/data`
6. Redémarrer : `systemctl start voicebridge`

## Volumes attendus

| Donnée | Taille typique |
|---|---|
| config.json | < 1 Ko |
| voices/metadata.json | < 100 Ko (pour 1000 voix) |
| Fichier WAV référence par voix | ~400 Ko |
| Fichier .pt encodé par voix | ~100 Ko |
| audio/metadata.json | < 500 Ko (pour 5000 enregistrements) |
| Fichier WAV généré moyen | 0.5 à 5 Mo |
| Modèles ML totaux | ~5 Go |

Dimensionnement KVM 4 (200 Go) largement suffisant.
