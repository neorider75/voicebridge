# 00 — Décisions de cadrage V3 (avant build)

> **Document de référence** rédigé le 6 mai 2026 après une revue critique des specs V3 par Claude (Opus 4.7) et l'utilisateur (JC).
> Les décisions ci-dessous **prévalent sur les autres documents de spec** en cas de contradiction.
> Pointer vers ce document avant toute discussion d'implémentation V3.

## Contexte

Avant de démarrer la branche `feat/v3-live-gpu`, une revue des specs a identifié 7 points qui devaient être tranchés pour éviter des choix arbitraires pendant le build. Ce fichier formalise ces décisions et leur justification.

---

## Décision 1 — Upload `.pth` vers RunPod Volume : API S3

**Problème :** la spec `12-rvc-pipeline.md` listait 3 approches sans en choisir (Pod éphémère, API RunPod, S3).

**Décision :** **Approche B — API S3 RunPod** (`boto3` côté Hostinger).

**Justification :**
- RunPod expose une API S3-compatible sur les Network Volumes depuis fin 2024 (`https://s3api-{datacenter}.runpod.io`).
- Coût : 0€ (vs ~0.05€ par upload pour un Pod éphémère).
- Latence : ~30s pour 200 Mo (vs 3-5 min pour l'approche Pod).
- Code minimal : `boto3.client('s3').put_object()` + callback de progression.
- Aucune dépendance fragile (pas de SSH, pas de spawn/kill de Pod).

**Conséquences techniques :**
- Nouvelle dépendance dans `Site/backend/requirements.txt` : `boto3`.
- Nouvelles clés dans `config.json` (chiffrées via Fernet) : `runpod_s3_access_key`, `runpod_s3_secret_key`.
- L'utilisateur doit générer ces credentials dans la console RunPod (Storage → son Volume → S3 Credentials).

**Fichiers impactés :** `services/runpod_client.py`, `routes/rvc.py` (upload), `routes/cloud.py` (configure + test).

---

## Décision 2 — Voix natives : 4 langues sourcées + UI d'ajout

**Problème :** `runpod-worker/models/f5tts.py` référence 9 voix natives WAV qui n'étaient nulle part téléchargées par la spec.

**Décision :**
- **Pré-fournir 4 voix natives par défaut** : EN, ES, PT, IT.
- **Sourcer depuis Mozilla Common Voice** (CC0).
- **L'utilisateur peut en ajouter d'autres** via une UI unifiée dans `/voices`.

**Justification :**
- Pas de FR (utilisateur francophone, redondant) ni DE/JA/ZH/NL (V3.1 si besoin).
- Common Voice est le standard pour ce cas d'usage : licence CC0, qualité validée.
- Le concept de "voix native" est juste une voix avec un attribut `kind: "native"` — pas de mécanique séparée.

**Modèle de données dans `voices/metadata.json` :**
```json
{
  "id": "v_xxx",
  "name": "EN — voix homme native",
  "kind": "native",          // "clone" (default) | "native"
  "language": "en",
  "is_default": true         // les 4 fournies par défaut
}
```

**UI :** la page `/voices` affichera deux sections séparées (Clonées / Natives) avec un bouton "+ Ajouter" qui ouvre un wizard demandant le `kind`.

**Studio Live :** le sélecteur de voix filtre par mode :
- `gpu-clone` → voix `kind="clone"`
- `gpu-native` → voix `kind="native"` filtrées par langue cible
- `gpu-hybrid` → **double sélecteur** : "Voix native source" (accent) + "Modèle RVC" (timbre cible)

**Fichiers impactés :** `voices_store.py`, `routes/voices.py`, `voices.html`, `voices-new.html`, `studio.html`, `studio-live.js`. Worker RunPod : suppression du dict hardcodé `NATIVE_VOICES`, le path WAV est passé en input live.

**Phase A nouveau livrable :** `runpod-worker/scripts/download_native_voices.py` qui télécharge les 4 voix par défaut depuis Common Voice, convertit en WAV 24kHz mono via ffmpeg, et les pousse dans le Volume RunPod via S3.

---

## Décision 3 — Master key Fernet : fichier dédié

**Problème :** la spec proposait de dériver la master key du hash bcrypt du password. Bug : un changement de password rend tous les secrets indéchiffrables.

**Décision :** **fichier dédié `/var/voicebridge/data/.master_key`** (chmod 400, propriétaire `voicebridge`).

**Justification :**
- Pattern standard Linux pour ce cas (cf. `/etc/shadow`, `ssh_host_*_key`, `.pgpass`).
- Auto-bootstrap : généré au premier boot du backend.
- Indépendant du password : changement de password sans impact.
- Si le filesystem est compromis, l'attaquant a déjà `config.json` aussi → le chiffrement protège uniquement contre les exfiltrations partielles (backup mal configuré).
- Backup propre : 1 fichier de 44 bytes à inclure dans la sauvegarde.

**Implémentation (`services/secrets.py`) :**
```python
from pathlib import Path
from cryptography.fernet import Fernet
import os, stat

MASTER_KEY_PATH = Path(os.environ.get("VB_MASTER_KEY_PATH",
                      "/var/voicebridge/data/.master_key"))

def get_master_key() -> bytes:
    if MASTER_KEY_PATH.exists():
        return MASTER_KEY_PATH.read_bytes().strip()
    MASTER_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    MASTER_KEY_PATH.write_bytes(key)
    os.chmod(MASTER_KEY_PATH, stat.S_IRUSR)  # 400
    return key
```

**Fichiers impactés :** `services/secrets.py` (nouveau), `.gitignore` (ajouter `.master_key`), `08-installation.md` (doc backup).

---

## Décision 4 — Mode par défaut de l'app macOS : combinaison B+C+D

**Problème :** spec `19-app-macos-v3.md` ligne 115 disait `default_mode = "gpu-clone"` ce qui contredit la promesse de rétrocompat V1 du README.

**Décision :** algorithme intelligent :

```
1. Premier lancement → toujours cpu-fr-en (sûr, garanti V1)
2. Settings UI a un champ "Mode Live par défaut" → l'honorer si défini
3. Sinon, dernier mode utilisé (mémorisé localement)
4. Validation systématique : si mode demandé est gpu-* mais runpod_configured == false
   → fallback cpu-fr-en + notification utilisateur (pas un crash silencieux)
```

**Justification :**
- Premier lancement = cpu-fr-en : zéro friction si RunPod pas configuré.
- Settings prioritaire : utilisateur GPU intensif définit son défaut une fois.
- Last mode : "tu utilisais hier `gpu-hybrid`, on te le repropose".
- Validation : ne jamais crasher silencieusement, toujours tomber sur un mode qui marche.

**UI menu macOS :** modes GPU **grisés et `[non configuré]`** si RunPod absent, avec lien vers Settings.

**Fichiers impactés :** `Site/macos-app/voicebridge_app/main.py`, `ws_client.py`, `routes/cloud.py` (endpoint `/status` qui expose `runpod_configured` + `default_live_mode`).

---

## Décision 5 — Latence F5-TTS : pré-warming agressif (V3.0) + vrai streaming (V3.1)

**Problème :** `runpod-worker/models/f5tts.py` ligne 108 fait du chunking **post-synthèse**, pas du vrai streaming. La spec promet ~1s pour `gpu-clone` et ~1.2s pour `gpu-hybrid`, la réalité est plutôt 1.5s et 2-2.5s.

**Décision V3.0 :** garder le chunking post-synthèse + ajouter un **pré-warming agressif** côté Hostinger.

**Décision V3.1 (ticket à créer) :** implémenter le vrai streaming F5-TTS (hooker la boucle de génération mel→audio).

**Justification :**
- Patcher F5-TTS = risque technique (1 à 5 jours de dev imprévisibles, casse à chaque release F5-TTS).
- Bénéfice V3 reste massif : 5-15s (V1 CPU) → 1.5-2s (V3 GPU) sans le vrai streaming.
- Pré-warming agressif rattrape ~100-200ms gratuitement, code propre.

**Implémentation pré-warming :**
- Dès que l'utilisateur sélectionne un mode GPU + une voix dans le Studio Live, déclencher en background :
  - Warmup F5-TTS sur RunPod (load en VRAM)
  - Pré-pousse du sample de voix au worker
- Au moment du clic "Démarrer", tout est déjà chargé.
- UI : "🔥 GPU prêt en 8s..." → "✅ GPU prêt".

**Latences honnêtes affichées dans l'UI :**
| Mode | Affichage |
|---|---|
| Authentique CPU FR/EN | `~5-15s · gratuit` |
| Multilingue ma voix | `~1.2s après préchauffage · GPU` |
| Voix native | `~1.2s après préchauffage · GPU` |
| Hybride accent natif | `~2s après préchauffage · GPU + RVC` |

Tooltip : *"Première phrase = +5-10s le temps de charger les modèles GPU. Phrases suivantes = latence indiquée."*

**Fichiers impactés :** `services/runpod_client.py` (méthode `warmup`), `routes/cloud.py` (endpoint `/warmup`), `studio-live.js` (auto-warmup), `studio.html` (chiffres ajustés).

---

## Décision 6 — NLLB : garder tel que prévu

**Problème :** licence CC-BY-NC limite l'usage commercial.

**Décision :** **garder NLLB**, risque licence connu et accepté. **Aucun disclaimer dans la doc.**

**Justification :**
- L'utilisateur cible (JC) accepte le risque.
- M2M-100 (alternative MIT) n'est pas significativement supérieur sur les paires courantes.
- L'écosystème provider sera de toute façon multi-options (OPUS-MT, NLLB, GPT-4o-mini, GPT-4o).

**Fichiers impactés :** aucun ajustement par rapport à la spec V3 actuelle.

---

## Décision 7 — Contexte GPT : briefings sauvegardés + édition par session

**Problème :** la spec V3 mentionne le glossaire métier mais pas comment fournir un contexte ponctuel à GPT (ex: "réunion CODIR de Limagrain, sujet marges").

**Décision :** **Option C combinée** — briefings sauvegardés réutilisables **+** édition libre par session.

**Trois niveaux de contexte distincts :**

| Type | Où | Persistance |
|---|---|---|
| Glossaire métier | `Settings → Traduction → Glossaire` | Permanent, partagé toutes sessions |
| Mémoire conversationnelle | Automatique côté backend | RAM, durée de la session |
| Briefing de session | Dropdown studio + textarea | Modèles sauvegardés réutilisables, override par session |

**Justification :**
- Cas réels : la plupart des réunions sont récurrentes (CODIR mensuel, point client, brainstorming) → templates réutilisables.
- Mais chaque session a sa spécificité (date, sujet précis) → édition libre nécessaire.
- Cohérent avec le pattern UX d'autres outils (Notion, Otter, Loom AI).

**UI Studio Live (mode GPT) :**
```
Briefing : [CODIR mensuel ▾]   [+ Nouveau]
┌─────────────────────────────────────────┐
│ Réunion CODIR Limagrain, agenda mensuel,│
│ présents : DG, DAF, DRH, DOP. Ton formel│
│ Session du 6 mai : sujet marges Q1.     │← édition libre
└─────────────────────────────────────────┘
[ Démarrer la session ]
```

**Stockage :** `data/briefings/metadata.json` (CRUD JSON simple, pattern voices_store).

**Fichiers à créer :**
- Backend : `services/briefings_store.py`, `routes/briefings.py`.
- Frontend : page `/briefings` (CRUD), section dans `studio.html` (dropdown + textarea).
- `services/openai_client.py` : injection du briefing comme `system` prompt GPT, ignoré pour OPUS-MT/NLLB.

---

## Dépendances externes à préparer (par l'utilisateur, en parallèle du build)

Avant la fin de la Phase A, l'utilisateur doit avoir :

1. **Compte RunPod actif** : https://runpod.io
2. **Network Volume EU-FR-1, 50 Go** créé dans la console RunPod (~3.5€/mois)
3. **API key globale RunPod** : Settings → API Keys → Create
4. **Credentials S3 du Volume** : Storage → Volume → S3 Credentials → Create (2 strings : access key + secret)
5. **Compte Docker Hub** (ou autre registry) pour pousser l'image worker
6. **Optionnel : compte OpenAI Platform** + clé `sk-...` si test GPT-4o-mini en traduction

Le compte RunPod et le Volume peuvent être créés dès maintenant. Les credentials S3 doivent être générés une fois mais ne servent qu'au moment de la Phase A.

---

## Ordre d'attaque des phases (rappel)

| Phase | Sujet | Estimation |
|---|---|---|
| A | Worker RunPod Docker | 1 semaine |
| B | Services backend Cloud (runpod_client, openai_client, secrets, translation_router) | 3 jours |
| C | Extension WebSocket `/ws/stream` (4 modes) | 3 jours |
| D | RVC + Recording session + Briefings | 4 jours |
| E | Studio Live nouveau (4 modes + UI claire) | 3 jours |
| F | Pages RVC + Recording session + Briefings | 5 jours |
| G | Settings extended (panels Cloud / Trad / RVC) | 2 jours |
| H | App macOS (mode + RVC menu + indicateur) | 2 jours |
| I | Install : phase 15 Cloud config | 1 jour |
| J | Doc : guide RVC PDF + tutoriel Kaggle | 2 jours |
| K | Tests E2E | 3 jours |

**Total estimé : ~6 semaines** de dev (plus tests utilisateur en parallèle).

---

## Référence des spécifications

Les autres documents de spec V3 restent valables, **sauf en cas de contradiction avec le présent document** :
- `README.md`, `IMPLEMENTATION_ROADMAP.md` (parent)
- `01-architecture.md`, `02-features-v1.md` (existants V1)
- `03-features-v3.md` à `20-installation-v3.md` (V3)
- `runpod-worker/` (handler.py + models/* + tests)
