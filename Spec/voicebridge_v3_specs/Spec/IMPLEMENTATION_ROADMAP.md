# IMPLEMENTATION ROADMAP - VoiceBridge V3

> **Document destiné à Claude Code.** Suivre les phases dans l'ordre. Ne pas commencer une phase tant que la précédente n'est pas validée par les tests indiqués.

## Contexte de départ

Le repo `voicebridge` est en **V1 fonctionnelle** sur un VPS Hostinger KVM 4 (Paris, France) :

- Backend FastAPI Python 3.11 (`Site/backend/`)
- Frontend HTML/CSS/JS vanilla (`Site/frontend/`)
- App macOS PyInstaller (`Site/macos-app/`)
- Install script bash 14 phases (`Site/install/install.sh`)

Modèles ML déjà installés sur le VPS :
- NeuTTS Nano Q4/Q8 FR + EN (clonage TTS)
- XTTS-v2 (clonage TTS, 17 langues)
- Kyutai 1B STT (FR + EN)
- Silero VAD
- Détection deepfake (Deepfake-audio-detection-V2)
- Helsinki-NLP OPUS-MT (FR↔EN, dans `services/translation.py`)

L'objectif V3 est d'ajouter :
1. **Mode live multilingue** via GPU RunPod EU-FR-1
2. **Voice Conversion (RVC)** pour le mode "ma voix avec accent natif"
3. **Multi-providers de traduction** (OPUS-MT CPU/GPU, NLLB GPU, OpenAI GPT-4o-mini/GPT-4o)
4. **Wizard d'enregistrement RVC** avec retraitement audio automatique
5. **Barres de progression UX systématiques** sur toute opération > 1s

## Stratégie globale : EXTENSION, PAS REFONTE

Le code V1 fonctionne et est bien architecturé. **On l'étend, on ne le refait pas.**

Règles d'or :
- ❌ Ne pas casser les fonctionnalités V1 existantes
- ❌ Ne pas réécrire les modules qui fonctionnent
- ✅ Ajouter de nouveaux modules en parallèle
- ✅ Conserver les conventions existantes (factory functions, lazy loading via `manager.py`, services modulaires)
- ✅ Préserver les voix existantes (Juliette/Dave) et leur format
- ✅ Le mode CPU FR/EN existant reste accessible en fallback (mais PAS pour le live, qui devient GPU obligatoire)

## Architecture cible V3

```
┌────────────────────────────────────────────────────┐
│  Mac + VoiceBridge.app + BlackHole + Teams          │
└─────────────────┬──────────────────────────────────┘
                  │ wss://
                  ▼
┌────────────────────────────────────────────────────┐
│  Hostinger KVM 4 Paris (existant + extensions)      │
│                                                     │
│  V1 existant (inchangé) :                          │
│  - FastAPI + auth + voix CRUD + frontend           │
│  - NeuTTS / XTTS / Kyutai / Silero / Deepfake-V2   │
│  - OPUS-MT CPU (fallback)                          │
│                                                     │
│  V3 ajouté :                                       │
│  - services/runpod_client.py                       │
│  - services/openai_client.py                       │
│  - services/translation_router.py                  │
│  - services/rvc_models_store.py                    │
│  - services/audio_dataset_processor.py             │
│  - services/secrets.py (Fernet)                    │
│  - routes/rvc.py                                   │
│  - routes/recording_session.py                     │
│  - routes/cloud.py                                 │
│  - frontend pages : rvc, rvc-import, recording     │
│  - frontend studio.html : 4 modes Live + sélecteur │
│    provider trad                                   │
└─────────────────┬──────────────────────────────────┘
                  │ HTTPS REST + streaming
                  ▼
┌────────────────────────────────────────────────────┐
│  RunPod Serverless EU-FR-1 (NOUVEAU)                │
│  Container Docker unifié "voicebridge-worker"      │
│                                                     │
│  Endpoints :                                       │
│  - operation=live_pipeline (cascade STT+Trad+TTS+  │
│    RVC en streaming)                               │
│  - operation=translate (OPUS-MT GPU ou NLLB GPU)   │
│  - operation=rvc_convert (mode fichier RVC)        │
│                                                     │
│  Modèles :                                         │
│  - Whisper Distil-Large-V3 (STT multilingue)       │
│  - F5-TTS (clonage 100+ langues)                   │
│  - NLLB-200 distilled 1.3B                         │
│  - OPUS-MT (paires FR↔EN, FR↔DE, FR↔ES, FR↔IT)     │
│  - RVC (avec .pth utilisateur depuis Volume)       │
│                                                     │
│  Config : RTX 4090, FlashBoot, scale to zero       │
└─────────────────┬──────────────────────────────────┘
                  │
                  ▼
┌────────────────────────────────────────────────────┐
│  RunPod Network Volume EU-FR-1 (50 Go)              │
│  - HF cache (Whisper, F5-TTS, NLLB, OPUS-MT)       │
│  - rvc_models/ (.pth + .index utilisateur)         │
└────────────────────────────────────────────────────┘
                  ▲
                  │ HTTPS REST (optionnel)
┌─────────────────┴──────────────────────────────────┐
│  OpenAI API (cloud, optionnel)                      │
│  GPT-4o-mini / GPT-4o pour traduction qualité      │
└────────────────────────────────────────────────────┘

Hors-ligne (utilisateur) :
┌────────────────────────────────────────────────────┐
│  Kaggle (gratuit) : entraînement RVC               │
│  → utilisateur télécharge .pth + .index            │
│  → uploade dans VoiceBridge via /rvc-import        │
└────────────────────────────────────────────────────┘
```

## Phases d'implémentation

### Phase A — Worker RunPod Docker (semaine 1)

**Objectif :** créer le container Docker unifié déployable sur RunPod Serverless.

**Livrables :**
- `runpod-worker/` : nouveau dossier racine du repo
- `runpod-worker/Dockerfile` : image GPU CUDA 12.1 + Python 3.11
- `runpod-worker/handler.py` : handler unifié RunPod (3 endpoints)
- `runpod-worker/models/whisper.py` : wrapper Whisper Distil-Large-V3
- `runpod-worker/models/f5tts.py` : wrapper F5-TTS multilingue
- `runpod-worker/models/nllb.py` : wrapper NLLB-200 distilled 1.3B
- `runpod-worker/models/opus_mt.py` : wrapper OPUS-MT (paires FR↔EN, FR↔DE, FR↔ES, FR↔IT)
- `runpod-worker/models/rvc.py` : wrapper RVC inférence
- `runpod-worker/requirements.txt` : torch 2.4 + cu121, transformers, f5-tts, etc.
- `runpod-worker/README.md` : instructions de build et déploiement
- `runpod-worker/test_local.py` : tests unitaires sur GPU local (si dispo)

**Tests de validation phase A :**
1. `docker build .` produit une image fonctionnelle
2. `docker run --gpus all` démarre le worker en local (ou échoue proprement si pas de GPU)
3. Test handler avec input fictif retourne un output structuré
4. Image poussée sur Docker Hub (`<username>/voicebridge-worker:v3.0.0`)

Voir détail dans `Spec/voicebridge_specs/11-runpod-integration.md`.

### Phase B — Backend Hostinger : services Cloud (3 jours)

**Objectif :** créer les wrappers d'appel vers RunPod et OpenAI, et le routeur multi-providers de traduction.

**Livrables :**
- `Site/backend/app/services/secrets.py` : chiffrement Fernet des clés API
- `Site/backend/app/services/runpod_client.py` : wrapper REST API RunPod (avec retry et streaming)
- `Site/backend/app/services/openai_client.py` : wrapper OpenAI traduction
- `Site/backend/app/services/translation_router.py` : sélection provider selon config user
- `Site/backend/app/routes/cloud.py` : endpoints test connexion + warmup
- Modifications de `requirements.txt` : ajouter `httpx[http2]`, `openai`, `cryptography`
- Modifications de `Site/backend/app/services/translation.py` : refacto pour exposer une API uniforme

**Tests de validation phase B :**
1. `pytest tests/test_runpod_client.py` : tests mockés
2. `pytest tests/test_translation_router.py` : tests provider switching
3. Endpoint `POST /api/cloud/test` répond OK avec une vraie clé RunPod
4. Endpoint `POST /api/cloud/openai/test` répond OK avec une vraie clé OpenAI

Voir détail dans `Spec/voicebridge_specs/13-translation-providers.md` et `11-runpod-integration.md`.

### Phase C — Backend Hostinger : extension `/ws/stream` (3 jours)

**Objectif :** étendre le WebSocket Live pour supporter le mode GPU avec routing intelligent.

**Livrables :**
- Modification de `Site/backend/app/routes/live.py` :
  - Champ `mode` dans le payload `configure` : `"cpu-fr-en"` (existant) | `"gpu-clone"` | `"gpu-native"` | `"gpu-hybrid"`
  - Champ `translation_provider` : `"opus-mt-cpu"` | `"opus-mt-gpu"` | `"nllb"` | `"gpt-4o-mini"` | `"gpt-4o"`
  - Champ `rvc_model_id` (requis si mode = `"gpu-hybrid"`)
  - Champ `target_lang` (requis si traduction activée)
  - Nouvelle fonction `flush_speech_gpu()` qui appelle RunPod en cascade et stream les chunks audio
- Conservation totale du chemin CPU existant pour `mode="cpu-fr-en"`
- Modifications de `Site/backend/app/routes/translate.py` : warmup multi-providers
- Modifications de `Site/backend/app/config.py` : nouvelles clés `runpod`, `openai`

**Tests de validation phase C :**
1. Mode `cpu-fr-en` continue de fonctionner exactement comme avant (régression)
2. Mode `gpu-clone` produit de l'audio cloné multilingue via RunPod
3. Mode `gpu-hybrid` applique RVC après F5-TTS native
4. Switching provider de traduction fonctionne en cours de session

Voir détail dans `Spec/voicebridge_specs/03-features-v3.md` et `05-backend-api.md` (section V3).

### Phase D — Backend Hostinger : RVC + Recording session (4 jours)

**Objectif :** routes pour gérer les modèles RVC et le wizard d'enregistrement.

**Livrables :**
- `Site/backend/app/services/rvc_models_store.py` : CRUD .pth + .index uploadés
- `Site/backend/app/services/audio_dataset_processor.py` : retraitement audio (VAD + denoise + normalize + segment)
- `Site/backend/app/routes/rvc.py` : endpoints RVC (list, upload, delete, test, push to RunPod Volume)
- `Site/backend/app/routes/recording_session.py` : endpoints wizard (create, append_chunk, process, validate, export_zip, status)
- Modifications de `requirements.txt` : ajouter `noisereduce`, `pydub`, `librosa`
- Système de tâches asynchrones avec **barre de progression** via WebSocket `/ws/progress/{task_id}`

**Tests de validation phase D :**
1. Upload d'un .pth de test (~150 Mo) → stockage local + upload RunPod Volume
2. Retraitement audio sur 5 min de test → produit un dataset propre
3. Score qualité calculé correctement (SNR, distribution, niveau)
4. Export ZIP avec manifeste JSON valide
5. WebSocket `/ws/progress/{task_id}` envoie des updates toutes les 500ms

Voir détail dans `Spec/voicebridge_specs/12-rvc-pipeline.md` et `14-rvc-recording-guide.md`.

### Phase E — Frontend : Studio Live nouveau (3 jours)

**Objectif :** ajouter dans le studio Live le sélecteur 4 modes + sélecteur provider trad.

**Livrables :**
- Modification de `Site/frontend/studio.html` :
  - Nouveau radio-group `live-mode` (4 options)
  - Nouveau radio-group `translate-provider` (5 options)
  - Sélecteur `rvc-model` qui apparaît si mode = "gpu-hybrid"
  - Bouton "🔥 Préchauffer GPU" si mode commence par "gpu-"
  - Indicateur de latence cible affiché selon le mode
- Modification de `Site/frontend/js/studio-live.js` :
  - Logique de routing selon le mode
  - Préchauffage RunPod via REST avant ouverture WebSocket
  - Affichage barre de progression cold start
  - Compteur de coût estimé en temps réel (mode GPU + OpenAI)

**Tests de validation phase E :**
1. UI propre dans les 4 modes
2. Préchauffage GPU affiche barre de progression jusqu'au "Prêt"
3. Switch de mode pendant une session ferme et rouvre proprement le WebSocket
4. Indicateur de coût se met à jour à chaque chunk traité

Voir détail dans `Spec/voicebridge_specs/04-frontend-specs.md` (section V3).

### Phase F — Frontend : RVC + Recording session (5 jours)

**Objectif :** créer les pages RVC, le wizard d'import et le wizard d'enregistrement.

**Livrables :**
- `Site/frontend/rvc.html` : page liste des modèles RVC
- `Site/frontend/rvc-import.html` : wizard upload .pth + .index
- `Site/frontend/recording-session.html` : wizard enregistrement guidé
- `Site/frontend/js/rvc.js` : logique CRUD modèles
- `Site/frontend/js/rvc-import.js` : logique upload + validation
- `Site/frontend/js/recording-session.js` : logique enregistrement chunk par chunk + progression
- `Site/frontend/css/rvc.css` : styles dédiés
- Ajout d'un item de menu "Modèles RVC" dans la navbar
- Tutoriel Kaggle intégré dans `rvc.html`

**Tests de validation phase F :**
1. Upload d'un .pth fonctionne avec barre de progression
2. Wizard d'enregistrement : 5 blocs avec compteur durée
3. Indicateur durée cumulée mis à jour en temps réel
4. Bouton "Traiter le dataset" lance la phase D backend avec progression
5. Page validation affiche tous les clips avec lecteur + score qualité
6. Export ZIP démarre le téléchargement

Voir détail dans `Spec/voicebridge_specs/14-rvc-recording-guide.md`.

### Phase G — Frontend : Settings extended (2 jours)

**Objectif :** ajouter les panneaux Cloud (RunPod + OpenAI) et RVC.

**Livrables :**
- Modification de `Site/frontend/settings.html` :
  - Nouveau panel "Cloud" : config RunPod + OpenAI
  - Nouveau panel "Traduction" : provider par défaut + glossaire
  - Nouveau panel "RVC" : compteur modèles + lien vers /rvc
- Modification de `Site/frontend/js/settings.js` : logique des nouveaux panneaux
- Tests de connexion intégrés (boutons "Tester RunPod" et "Tester OpenAI")

**Tests de validation phase G :**
1. Saisie clé RunPod + clic "Tester" → ✅ verte si OK
2. Saisie clé OpenAI + clic "Tester" → ✅ verte si OK
3. Sélection provider trad par défaut persistée dans config

Voir détail dans `Spec/voicebridge_specs/04-frontend-specs.md` (section V3).

### Phase H — App macOS : préchauffage GPU (2 jours)

**Objectif :** ajouter dans le menu bar le bouton de préchauffage GPU et l'indicateur de latence.

**Livrables :**
- Modification de `Site/macos-app/voicebridge_app/main.py` :
  - Item de menu "🔥 Préchauffer GPU"
  - Item de menu "Mode" : sous-menu avec 4 modes
  - Item de menu "Modèle RVC" : sous-menu (visible si mode = "gpu-hybrid")
- Modification de `Site/macos-app/voicebridge_app/ws_client.py` :
  - Envoi du mode et du provider trad dans le payload `configure`
  - Affichage du statut de préchauffage dans le titre du menu
- Build .app.zip via PyInstaller
- Mise à jour de `Site/macos-app/release/VoiceBridge.app.zip`

**Tests de validation phase H :**
1. Préchauffage GPU depuis le menu macOS fonctionne
2. Mode et modèle RVC sont synchronisés avec le serveur
3. Indicateur 🔴/🟡/🟢 reflète l'état (déconnecté / chauffe / prêt)

Voir détail dans `Spec/voicebridge_specs/06-voicebridge-app.md` (section V3).

### Phase I — Install : phase 15 Cloud config (1 jour)

**Objectif :** ajouter une phase optionnelle dans `install.sh` pour configurer RunPod et OpenAI.

**Livrables :**
- Modification de `Site/install/install.sh` :
  - Nouvelle phase 15 (renommée 14 → 15, et 14 actuel devient récap final)
  - Demande optionnelle clés RunPod + OpenAI
  - Test de connexion automatique
  - Stockage chiffré dans config.json
- Documentation de la nouvelle phase

**Tests de validation phase I :**
1. `./install.sh --skip-cloud` saute la phase 15
2. `./install.sh` propose la phase 15 et accepte les clés
3. Reprise après échec phase 15 fonctionne

Voir détail dans `Spec/voicebridge_specs/08-installation.md` (section V3).

### Phase J — Documentation : guide RVC PDF + tutoriel Kaggle (2 jours)

**Objectif :** produire le PDF téléchargeable et le tutoriel intégré.

**Livrables :**
- `Spec/RVC_recording_guide.pdf` : guide complet 12 pages
- Tutoriel Kaggle intégré dans `rvc.html` (markdown rendered)
- Lien de téléchargement du PDF dans la page `/rvc`

**Tests de validation phase J :**
1. PDF s'ouvre correctement
2. Tutoriel s'affiche dans la page sans bug

### Phase K — Tests + intégration end-to-end (3 jours)

**Objectif :** tests d'intégration complets pour valider la V3.

**Scénarios de test E2E :**
1. **Mode V1 (régression)** : génération TTS fichier en français avec NeuTTS Q8
2. **Mode CPU FR/EN (live)** : pipeline complet sans GPU
3. **Mode GPU clone** : ta voix clonée en allemand via F5-TTS
4. **Mode GPU native** : voix générique anglaise
5. **Mode GPU hybride** : ta voix clonée en anglais avec accent natif via RVC
6. **Switch provider trad en cours de session** : NLLB → GPT-4o-mini
7. **Wizard RVC complet** : enregistrement → retraitement → export ZIP
8. **Upload .pth** : import + test avec sample
9. **Cold start RunPod** : première session = barre de progression + connexion finale
10. **Régression voix existantes** : Juliette/Dave fonctionnent toujours

## Coûts de fonctionnement V3

| Profil | Coût mensuel |
|---|---|
| Hostinger Paris | 16 € |
| RunPod Network Volume EU-FR-1 (50 Go) | 3.5 € |
| RunPod RTX 4090 inférence (8h/mois live) | ~2.7 € |
| OpenAI GPT-4o-mini (~10 000 trad) | ~0.4 € |
| **Total V3 usage modéré** | **~22-23 €/mois** |

Variabilité selon usage (voir détail dans `Spec/voicebridge_specs/11-runpod-integration.md`).

## Pattern UX systématique : barres de progression

**Toute opération backend > 1 seconde DOIT afficher une barre de progression.**

| Opération | Méthode |
|---|---|
| Préchauffage GPU RunPod | WebSocket `/ws/progress/{task_id}` |
| Retraitement audio dataset | WebSocket `/ws/progress/{task_id}` |
| Upload .pth (gros fichiers) | Native progress event XHR/fetch |
| Génération TTS fichier | Polling `/api/tts/status/{task_id}` toutes les 500ms |
| STT fichier | Polling |
| Cold start RunPod | Indicateur en console + barre de progression |
| Téléchargement modèles HF (premier appel) | Polling logs serveur |

**Format standard :**
```javascript
{
  "task_id": "uuid",
  "status": "running" | "done" | "error",
  "progress_percent": 0-100,
  "current_step": "Découpage en clips (3/6)",
  "elapsed_seconds": 23,
  "estimated_remaining_seconds": 120,
  "logs": ["..."],  // pour mode debug
  "result": {} | null  // si status=done
}
```

UI :
- Barre HTML5 progressive
- Texte "X/Y" pour les étapes discrètes
- Spinner pour les étapes indéterminées
- Logs repliables (mode debug uniquement)
- Bouton "Annuler" si applicable

Voir détail dans `Spec/voicebridge_specs/04-frontend-specs.md` (section "Progress UX").

## Conventions à respecter

### Code Python (backend)
- Suivre les conventions existantes : `factory functions`, `lazy loading via manager.py`
- Imports paresseux pour les deps ML (cf. `tts.py`, `stt.py`)
- Logging structuré avec `logging.getLogger("voicebridge.<module>")`
- Pas de SQL (toujours JSON sur disque pour la persistance)
- Tests unitaires dans `tests/` (à créer si absent)

### Code JS (frontend)
- Vanilla JS uniquement (pas de framework, c'est la convention V1)
- CSS variables pour le thème (cf. `base.css`)
- Pas de bundler, fichiers servis tels quels
- Notification système via `/js/notify.js` (existant)

### Sécurité (V1 + V3)
- Toutes les routes V3 derrière `Depends(require_auth)`
- Path traversal : whitelist `^[A-Za-z0-9_-]+$` sur tous les IDs (cf. `utils/files.py`)
- Magic bytes via `python-magic` pour les uploads
- Clés API chiffrées via Fernet dans `config.json`
- HTTPS forcé via Nginx
- Rate limiting `slowapi` étendu aux nouvelles routes

### Resource limits
- Upload .pth : max 500 Mo
- Upload .index : max 200 Mo
- Audio enregistrement par bloc : max 10 minutes
- Total dataset RVC : max 30 minutes

## Lecture obligatoire avant de commencer

Lire dans l'ordre :
1. `Spec/voicebridge_specs/README.md` (existant)
2. `Spec/voicebridge_specs/01-architecture.md` (existant + section V3)
3. `Spec/voicebridge_specs/03-features-v3.md` (mis à jour)
4. `Spec/voicebridge_specs/11-runpod-integration.md` (nouveau)
5. `Spec/voicebridge_specs/12-rvc-pipeline.md` (nouveau)
6. `Spec/voicebridge_specs/13-translation-providers.md` (nouveau)
7. `Spec/voicebridge_specs/14-rvc-recording-guide.md` (nouveau)
8. `Spec/voicebridge_specs/15-latency-optimization.md` (nouveau)
9. `Spec/voicebridge_specs/05-backend-api.md` (existant + section V3)
10. `Spec/voicebridge_specs/04-frontend-specs.md` (existant + section V3)

Le scope est volontairement contraint pour éviter le scope creep. Ne **pas** ajouter :
- Multi-utilisateurs (V1 reste mono-utilisateur)
- Mobile app (Android/iOS)
- API publique externe
- Marketplace de voix
- Modes V2 non-V3 (intonation guidée, sessions programmées, app Windows, enregistrement session live)

Tout ça reste pour plus tard.
