# 03 - Features V3 (architecture cible)

> **Ce document remplace l'ancien `03-features-v2-v3.md`.**
>
> La V2 telle qu'elle était décrite (LibreTranslate uniquement, pas de GPU, pas de RVC en live) est **abandonnée**. On passe directement à V3 avec architecture hybride Hostinger CPU + RunPod GPU.

## Vision V3 en une phrase

VoiceBridge V3 permet à un utilisateur français de **parler dans Teams en plusieurs langues avec sa propre voix**, avec un choix flexible de provider de traduction (souverain ou IA), grâce à une architecture hybride qui conserve le mode CPU FR/EN existant et ajoute un pipeline GPU à la demande pour le multilingue et le voice conversion.

## Les 4 modes Live

L'utilisateur sélectionne dans le studio Live un mode parmi 4. Chaque mode a un pipeline différent et des caractéristiques propres.

| Mode | Voix entendue | Pipeline | Latence cible | Coût |
|---|---|---|---|---|
| **Authentique CPU** (V1 existant) | Ta voix avec accent FR | NeuTTS Hostinger CPU | 5-15s ⚠️ | 0€ |
| **Multilingue ma voix** (V3 nouveau) | Ta voix dans la langue cible (accent FR perceptible) | F5-TTS RunPod GPU | ~1.0-1.2s | ~0.34€/h |
| **Voix native** (V3 nouveau) | Voix générique avec accent natif parfait | F5-TTS native RunPod GPU | ~1.0-1.2s | ~0.34€/h |
| **Hybride accent natif** (V3 nouveau) | Ta voix avec accent natif parfait | F5-TTS native + RVC RunPod GPU | ~1.2-1.5s | ~0.34€/h |

### Mode "Authentique CPU"

C'est le mode V1 existant **conservé tel quel**. Il sert de :
- Mode économique (zéro coût GPU)
- Mode souverain (rien ne sort du VPS)
- Mode fallback si RunPod down

⚠️ Mais **inutilisable en pratique pour le live** car la latence sur CPU est de 5-15s. Il reste utile pour :
- Mode fichier (TTS asynchrone)
- Test/démo en attendant que RunPod soit configuré
- Backup en cas de panne RunPod

### Mode "Multilingue ma voix"

Pipeline :
```
Capture micro → Hostinger
    ↓ HTTPS REST → RunPod EU-FR-1
    ├─ Whisper Distil-Large-V3 (STT multilingue)
    ├─ Traduction (provider choisi par l'utilisateur)
    └─ F5-TTS avec ta voix de référence (clonage zero-shot)
    ↓
WebSocket retour → Mac → BlackHole → Teams
```

**Caractéristique** : F5-TTS clone ta voix dans n'importe quelle langue, mais ta voix conserve son accent français inhérent.

**Cas d'usage** : tu veux parler à un partenaire allemand en allemand mais que ta voix reste reconnaissable.

### Mode "Voix native"

Pipeline :
```
Capture micro → Hostinger
    ↓ HTTPS REST → RunPod EU-FR-1
    ├─ Whisper Distil-Large-V3 (STT multilingue)
    ├─ Traduction (provider choisi par l'utilisateur)
    └─ F5-TTS avec voix générique native de la langue cible
    ↓
WebSocket retour → Mac → BlackHole → Teams
```

**Caractéristique** : voix d'acteur natif de la langue cible (anglais britannique parfait, allemand de Berlin, etc.). Ce n'est PAS ta voix.

**Cas d'usage** : tu veux que les interlocuteurs entendent un anglais parfait sans aucun accent, peu importe que ce ne soit pas exactement ta voix.

### Mode "Hybride accent natif"

Pipeline :
```
Capture micro → Hostinger
    ↓ HTTPS REST → RunPod EU-FR-1
    ├─ Whisper Distil-Large-V3 (STT multilingue)
    ├─ Traduction (provider choisi par l'utilisateur)
    ├─ F5-TTS avec voix générique native de la langue cible
    └─ RVC inference avec ton modèle .pth perso → ta voix avec accent natif
    ↓
WebSocket retour → Mac → BlackHole → Teams
```

**Caractéristique** : tu as **ta voix** (timbre reconnaissable) **avec un accent natif parfait**. C'est le graal.

**Pré-requis** :
- Avoir entraîné un modèle RVC sur Kaggle (gratuit, ~6h)
- Avoir importé le .pth dans VoiceBridge
- Le .pth est stocké sur RunPod Network Volume

**Cas d'usage** : présentations CODIR à l'international, négociations commerciales, conférences.

## Provider de traduction (configurable par l'utilisateur)

L'utilisateur choisit son provider de traduction **indépendamment** du mode Live. Le provider sélectionné est utilisé pour TOUTES les traductions (live, fichier, transcripts).

| Provider | Localisation | Qualité FR↔EN | Latence GPU | Coût | Multi-langues |
|---|---|---|---|---|---|
| **OPUS-MT CPU** (V1 existant) | Hostinger CPU | Bonne | N/A | 0€ | Limité |
| **OPUS-MT GPU** (V3 nouveau) | RunPod GPU | Bonne | 50-150ms | Inclus GPU | FR↔EN, FR↔DE, FR↔ES, FR↔IT |
| **NLLB-200 distilled 1.3B** (V3 nouveau) | RunPod GPU | Très bonne | 100-300ms | Inclus GPU | 200+ langues |
| **GPT-4o-mini** (V3 nouveau) | OpenAI cloud | Excellente | 300-800ms | ~0.04€/1000 trad | Universel |
| **GPT-4o** (V3 nouveau) | OpenAI cloud | Excellente++ | 600-1500ms | ~0.40€/1000 trad | Universel + contexte |
| **LibreTranslate** (V3 nouveau, fallback) | Hostinger CPU | Moyenne | N/A | 0€ | Multi-paires limitées |

### Recommandations par cas d'usage

| Scénario | Provider recommandé |
|---|---|
| Live FR↔EN avec ta voix | NLLB GPU (rapide, multi-langues, souverain) |
| Live multilingue avec accent technique | GPT-4o-mini (bon compromis qualité/coût) |
| Présentation CODIR critique | GPT-4o avec glossaire métier |
| Mode fichier (asynchrone, FR↔EN) | OPUS-MT CPU (économise le GPU) |
| Mode hors ligne / RunPod down | LibreTranslate ou OPUS-MT CPU (fallback) |

## Voice Conversion (RVC) - cycle de vie complet

### Phase 1 : Enregistrement guidé (dans VoiceBridge)

Page `/recording-session` : wizard 5 blocs avec textes calibrés.

| Bloc | Durée | Contenu |
|---|---|---|
| 1 | 5 min | Texte phonétiquement riche (couvre tous les phonèmes FR) |
| 2 | 5 min | Conversationnel naturel (registre professionnel) |
| 3 | 3 min | Variété d'intonations (questions, exclamations, énumérations) |
| 4 | 2 min | Nombres, dates, mots techniques (jargon CISO) |
| 5 | 5 min | Lecture libre (article, livre) |
| **Total** | **20 min** | |

**Capture** : AudioWorklet PCM 16kHz mono (réutilise le worklet Live existant).

**Indicateur en temps réel** :
- Durée totale enregistrée vs cible (15-20 min)
- Niveau audio (VU-meter)
- Détection silences trop longs (warning)

### Phase 2 : Retraitement automatique (asynchrone)

Backend Hostinger CPU lance un pipeline de traitement (~5 min pour 20 min audio).

```
Audio brut des 5 blocs (~20 min)
    ↓
1. Détection des silences (Silero VAD - déjà installé)
    ↓
2. Découpage en clips de 5-15s
    ↓
3. Suppression du bruit de fond (noisereduce, FFT-based)
    ↓
4. Normalisation loudness (-3 dB peak via pydub)
    ↓
5. Calcul score qualité (SNR, distribution durées, niveaux)
    ↓
6. Export ZIP prêt pour Kaggle
```

**Barre de progression** via WebSocket `/ws/progress/{task_id}` :
```
Étape 1/6 : Détection des silences        [████████░░] 80%
3 min 12s écoulé · ~1 min restant
```

**Output** : ZIP contenant ~150 clips WAV mono 44.1kHz + manifeste JSON.

### Phase 3 : Validation (dans VoiceBridge)

Page `/recording-session/<id>/validate` :

```
Score qualité dataset : 87/100 ✅

✅ Niveau audio : Excellent (-4 dB crête)
✅ Bruit de fond : Faible (32 dB SNR)
⚠️ 3 clips trop courts (à supprimer ?)
✅ Diversité : 142 clips, 18 min total

[Liste des clips avec lecteur audio individuel]
[Bouton "Supprimer les clips problématiques"]
[Bouton "Télécharger le dataset (ZIP)"]
```

### Phase 4 : Entraînement Kaggle (gratuit, hors VoiceBridge)

L'utilisateur :
1. Crée un compte Kaggle (gratuit, 30h GPU/semaine)
2. Forke le notebook RVC officiel (lien fourni dans VoiceBridge)
3. Uploade le ZIP comme dataset Kaggle
4. Lance le notebook (3-6h selon paramètres)
5. Télécharge le `.pth` (~150 Mo) + `.index` (~50 Mo)

Tutoriel complet dans `/rvc` (page tutoriel intégré) + PDF téléchargeable.

### Phase 5 : Import dans VoiceBridge

Page `/rvc-import` : wizard upload.

```
Étape 1 : Fichiers
- Drop zone .pth (validation magic bytes : signature PyTorch)
- Drop zone .index (optionnel mais recommandé)

Étape 2 : Métadonnées
- Nom du modèle (ex : "JC voice v1")
- Description
- Voix associée (sélecteur des voix existantes - optionnel)
- Sample rate (40000 Hz par défaut)
- Pitch type (auto / manuel)

Étape 3 : Upload vers RunPod Volume
- Barre de progression upload (XHR native progress)
- ETA basé sur la vitesse moyenne

Étape 4 : Test rapide
- Synthétise un audio sample avec F5-TTS native
- Applique RVC avec le .pth uploadé
- Lecteur audio : "Voici comment vous sonnerez"

Étape 5 : Validation finale
- Bouton "Activer le modèle" → status = "active"
- Modèle disponible dans le sélecteur du Studio Live
```

### Phase 6 : Utilisation en mode Live

Mode "Hybride accent natif" :
- Sélection du modèle RVC dans le studio
- Préchauffage RunPod (charge le .pth en VRAM)
- Pipeline live cascadé (F5-TTS native + RVC)

## Préchauffage GPU et latence

### Cold start

Premier appel après inactivité : 10-30s pour démarrer le worker RunPod (FlashBoot accélère mais pas infini).

**UX** : barre de progression "Préchauffage GPU en cours" :
```
🔥 Préchauffage GPU EU-FR-1 ...
[████████████████░░░░░░] 75%
12s écoulé · ~5s restant
```

**Bouton "Préchauffer GPU"** disponible dans :
- Studio Live (avant de démarrer une session)
- VoiceBridge.app menu macOS

**Auto-préchauffage** : 30s avant une réunion Teams détectée (V3.5, plus tard).

### Latence cible perçue (premier son)

Avec config optimale (Hostinger Paris + RunPod EU-FR-1 + endpoint unifié + streaming) :

| Mode | Latence sans cold start |
|---|---|
| Authentique CPU FR/EN | 5-15s ⚠️ |
| Multilingue ma voix | ~1.0s |
| Voix native | ~1.0s |
| Hybride accent natif | ~1.2s |

Voir détail dans `Spec/voicebridge_specs/15-latency-optimization.md`.

## Modifications du sélecteur Engine TTS (Studio TTS fichier)

Le sélecteur "Moteur TTS" dans le Studio TTS reste inchangé (NeuTTS / XTTS-v2). Le mode fichier garde son fonctionnement V1.

**Ajout V3** : option "F5-TTS GPU" dans le sélecteur (synthèse multilingue + clonage haute qualité). Optionnel, désactivé par défaut.

## Tableau récapitulatif features V3

| Feature | V1 | V3 |
|---|---|---|
| TTS fichier WAV/MP3 | ✅ | ✅ + F5-TTS option |
| TTS Qualité Q4/Q8 | ✅ | ✅ |
| Live FR/EN ta voix CPU | ✅ (lent) | ✅ (lent, fallback) |
| **Live multilingue ta voix GPU** | ❌ | ✅ NOUVEAU |
| **Live voix native multilingue GPU** | ❌ | ✅ NOUVEAU |
| **Live hybride RVC (ta voix + accent natif)** | ❌ | ✅ NOUVEAU |
| STT FR/EN Kyutai | ✅ | ✅ |
| **STT multilingue Whisper (GPU)** | ❌ | ✅ NOUVEAU |
| Mes voix CRUD | ✅ | ✅ |
| Ajout voix par enregistrement / fichier / URL | ✅ | ✅ |
| **Mes modèles RVC CRUD** | ❌ | ✅ NOUVEAU |
| **Wizard d'enregistrement RVC** | ❌ | ✅ NOUVEAU |
| **Retraitement audio dataset** | ❌ | ✅ NOUVEAU |
| **Tutoriel Kaggle intégré** | ❌ | ✅ NOUVEAU |
| **Guide RVC PDF téléchargeable** | ❌ | ✅ NOUVEAU |
| Détection deepfake | ✅ | ✅ |
| Page Enregistrements | ✅ | ✅ |
| Rétention 24h/48h/Session | ✅ | ✅ |
| Buffer continuité Live 5s | ✅ | ✅ |
| Silero VAD | ✅ | ✅ |
| Dark/Light mode | ✅ | ✅ |
| Login + sécurité | ✅ | ✅ |
| Clé API + token VoiceBridge | ✅ | ✅ |
| **Clés API RunPod + OpenAI chiffrées** | ❌ | ✅ NOUVEAU |
| Traduction OPUS-MT FR/EN CPU | ✅ | ✅ |
| **Traduction OPUS-MT GPU multi-pairs** | ❌ | ✅ NOUVEAU |
| **Traduction NLLB GPU 200+ langues** | ❌ | ✅ NOUVEAU |
| **Traduction GPT-4o-mini / GPT-4o** | ❌ | ✅ NOUVEAU |
| **LibreTranslate fallback** | ❌ | ✅ NOUVEAU (optionnel) |
| **Sélecteur provider trad par session** | ❌ | ✅ NOUVEAU |
| **Glossaire métier traduction** | ❌ | ✅ NOUVEAU (GPT only) |
| App macOS VoiceBridge | ✅ | ✅ |
| **Préchauffage GPU depuis macOS** | ❌ | ✅ NOUVEAU |
| Injection BlackHole (Teams) | ✅ | ✅ |
| **Barre de progression UX systématique** | ❌ | ✅ NOUVEAU |
| **Compteur coûts RunPod + OpenAI** | ❌ | ✅ NOUVEAU |
| Polling status 5s | ✅ | ✅ |
| Préchauffage manuel | ✅ | ✅ + GPU |
| Cron nettoyage rétention | ✅ | ✅ |

## Hors scope V3

Reportés à V3.5 ou V4 :
- Mode guidé intonation (4 intentions)
- Mode intensif programmé (sessions calendrier)
- App Windows VoiceBridge
- Enregistrement de session live à la volée
- Mode permanent (modèles always-loaded)
- Multi-utilisateurs
- Mobile app
- API publique externe
- Marketplace de voix/modèles RVC
- Traduction de fichiers vidéo (.mp4 → .mp4 doublé)
- Analytics d'usage avancées
