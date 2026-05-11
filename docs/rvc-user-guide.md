# Guide utilisateur VoiceBridge V3
## Voice Conversion (RVC) + traduction multilingue Live

> **Version 3.0** · mai 2026 · pour utilisateurs finaux et administrateurs.
>
> Ce guide couvre l'installation, la configuration RunPod, le pipeline RVC
> de bout en bout, les 4 modes Live et le troubleshooting des problèmes
> courants observés en production.

---

## 1. Vue d'ensemble

VoiceBridge V3 est une plateforme auto-hébergée de clonage vocal et de
traduction multilingue temps réel. Elle combine :

- Un backend FastAPI sur **VPS Hostinger** (16 €/mois) qui gère l'auth,
  l'UI, l'orchestration et le mode CPU V1
- Un worker GPU **RunPod Serverless** (à la demande, ~3 €/mois usage modéré)
  pour les modes multilingues et la voice conversion
- Une **app macOS** menu bar qui injecte la voix synthétisée dans BlackHole
  pour Teams/Zoom/Meet

### Les 4 modes Live

| Mode | Voix entendue | Latence typique | Coût |
|---|---|---|---|
| **Authentique CPU FR/EN** | Ta voix clonée (NeuTTS) | 5-15 s | gratuit |
| **Multilingue – ta voix** | Ta voix dans la langue cible | ~1.5 s (court) à 2.5 s (moyen) | ~0.005 €/min GPU |
| **Voix native** | Voix générique authentique | ~1.5 s à 2.5 s | ~0.005 €/min GPU |
| **Hybride accent natif** | Ta voix + accent natif (RVC) | +500-1000 ms vs ci-dessus | ~0.006 €/min GPU |

> Optimisations en cours pour V3.1 (vrai streaming F5-TTS) : cible <1 s.

### Coûts mensuels indicatifs

| Profil d'usage | Coût mensuel total |
|---|---|
| V1 CPU seul (FR/EN) | 16 € |
| V3 modéré (8h Live/mois) | 22-23 € |
| V3 régulier (30h Live/mois) | ~30 € |
| V3 intensif (100h Live/mois) | ~57 € |

---

## 2. Pré-requis

Avant de démarrer, prépare :

1. **VPS Ubuntu 22.04 ou 24.04** avec au minimum 8 Go RAM (16 recommandé)
   et 30 Go disque libre. Hostinger KVM 4 Paris est la configuration
   testée.
2. **Nom de domaine** pointant vers ton VPS (pour HTTPS Let's Encrypt).
3. **Compte RunPod** (https://runpod.io) — gratuit à l'inscription, tu
   paies uniquement le compute consommé.
4. **Compte OpenAI** (optionnel, https://platform.openai.com) si tu veux
   la traduction GPT-4o-mini avec briefings métier.
5. **Compte Docker Hub** (optionnel, https://hub.docker.com) pour
   pousser ton image worker. Tu peux aussi utiliser ghcr.io ou GitLab
   Registry.

---

## 3. Installation côté serveur Hostinger

### 3.1 Lancement du script

```bash
# Sur ton VPS, en root
wget https://raw.githubusercontent.com/<TON_FORK>/voicebridge/main/Site/install/install.sh
chmod +x install.sh
sudo ./install.sh
```

Le script enchaîne 15 phases automatiques (~30 minutes la 1ère fois)
incluant : paquets système, code, modèles ML V1 (NeuTTS, Kyutai),
voix par défaut, config, app macOS, Nginx + SSL, systemd, fail2ban,
cron jobs, **configuration cloud V3** et récap.

### 3.2 Phase 14 — Configuration cloud (V3)

À cette phase, le script te demande si tu veux configurer RunPod et
OpenAI maintenant. Tu peux **dire non** et configurer plus tard depuis
l'UI Réglages → Cloud.

Pour passer cette phase en mode CI/auto :

```bash
sudo ./install.sh --skip-cloud
```

### 3.3 Reprise après échec

L'installateur est **idempotent et reprend** là où il s'est arrêté :

```bash
sudo ./install.sh           # reprend après une coupure
sudo ./install.sh --fresh   # repart de zéro (rare)
```

---

## 4. Configuration RunPod (étape par étape)

### 4.1 Création du compte

1. Va sur https://runpod.io et crée un compte
2. **Vérifie ton email** + ajoute une carte bancaire (RunPod fonctionne
   en pré-paiement, tu peux charger 10 € pour démarrer)

### 4.2 Création du Network Volume

1. Console RunPod → **Storage** → **+ New Network Volume**
2. **Taille : 30 Go** suffit largement (occupation réelle ~14 Go avec
   les filtres `--include` et Whisper CTranslate2, le reste pour tes
   modèles RVC à ~200 Mo chacun)
3. **Datacenter** :
   - **EU-FR-1 (Paris)** — recommandé pour latence depuis la France
   - **EU-RO-1 (Roumanie)** — alternative testée et stable
   - **EU-NL-1 (Amsterdam)** ou **EU-CZ-1 (Prague)** — si EU-FR-1
     est temporairement "out of stock" (cas réel observé sur RTX 4090)
   - **EUR-NO-1 (Norvège)** — alternative la plus récente
4. Note le **Volume ID** (ex: `abc123xyz`) — tu le saisiras côté VoiceBridge

> **⚠️ Ne crée PAS le Volume après l'endpoint Serverless.** L'ordre
> compte : Volume → Endpoint, pas l'inverse.

### 4.3 Génération des credentials

#### API key globale (pour appels REST)

1. **Settings → API Keys → + Create API Key**
2. Donne-lui un nom (ex: `voicebridge-prod`)
3. Copie la clé `rpa_...` immédiatement (elle ne sera plus affichée)

#### S3 credentials du Volume (pour upload des .pth RVC)

1. Console RunPod → **Storage** → ton Volume → **S3 Credentials**
2. Clique **Create**
3. Note les deux strings : `Access Key` + `Secret Key`

---

## 5. Pré-téléchargement des modèles ML dans le Volume

Avant le premier déploiement de l'endpoint Serverless, tu dois pré-charger
les modèles dans le Network Volume pour que les workers ne les
retéléchargent pas à chaque cold start (~14 Go au total).

### 5.1 Spawn d'un Pod éphémère

1. Console RunPod → ouvre la page de détails de **ton Volume**
2. Clique sur le bouton **"Configure Pod with volume"** (PAS le bouton
   générique "Deploy a Pod" qui ne pré-attache pas le Volume)
3. **GPU** : RTX 4090 (le moins cher disponible, on télécharge juste)
4. **Image** : `runpod/pytorch:2.4.0-py3.11-cuda12.1.0-devel-ubuntu22.04`
   (recommandé — `hf` est déjà installé)
   - Ou `runpod/ubuntu:22.04` (plus léger, mais il faudra installer `hf`
     manuellement, voir ci-dessous)
5. **Mount Path** : ⚠️ saisis explicitement `/runpod-volume`
   (et NON le défaut UI `/workspace` — sinon le worker plantera car
   il cherche les fichiers à `/runpod-volume/...`)
6. Clique Deploy

### 5.2 Connexion au Pod

Une fois le Pod en état "Running", utilise le bouton **"Connect"** →
**"Web Terminal"** ou ssh selon ta préférence.

### 5.3 Installation de `hf` (si template Ubuntu)

> **Templates PyTorch officiels** : `hf` est déjà installé, saute cette étape.
>
> **Templates Ubuntu nus** : installe-le.

```bash
pip3 install --break-system-packages "huggingface_hub[cli]>=0.34"
```

> Le flag `--break-system-packages` est nécessaire sur Ubuntu 22.04+ qui
> bloque par défaut les installs pip system-wide (PEP 668). C'est sans
> danger dans un container éphémère.

Note : depuis fin 2024, l'ancienne CLI `huggingface-cli` est dépréciée.
Le binaire moderne s'appelle simplement `hf`. La syntaxe est identique.

### 5.4 Téléchargement des modèles

> **Toujours utiliser `--include`** pour ne télécharger que le format
> utile. Sans filtre, certains repos téléchargent plusieurs formats
> redondants (PyTorch + TF + Flax + Marian original) et saturent le
> Volume — F5-TTS dépasse 20 Go, distil-whisper 12 Go, etc.

```bash
# Sur le Pod éphémère
export HF_HOME=/runpod-volume/hf-cache
mkdir -p /runpod-volume/hf-cache /runpod-volume/rvc_assets

# ── STT — Whisper Large-V3 CTranslate2 multilingue (~3 Go) ──
# /!\ utiliser le repo Systran (pré-converti CTranslate2 model.bin), PAS
# openai/whisper-large-v3 qui est en safetensors PyTorch et fait planter
# faster-whisper avec "Unable to open file 'model.bin'".
# /!\ NE PAS prendre la version "distil" (faster-distil-whisper-large-v3) :
# c'est un modèle English-only, il transcrit du français en pseudo-anglais.
hf download Systran/faster-whisper-large-v3

# ── Traduction — NLLB-200 distilled 1.3B (~5 Go) ──
hf download facebook/nllb-200-distilled-1.3B \
  --include "*.safetensors" --include "*.json" \
  --include "tokenizer*" --include "sentencepiece*"

# ── Traduction — OPUS-MT (~300 Mo par paire, 6 paires V3.0) ──
# Note : les paires fr-it / it-fr ne sont PAS publiées sur HuggingFace
# (404). Pour FR↔IT et autres langues non listées, NLLB-200 prend le
# relais automatiquement côté translation_router Hostinger.
for pair in fr-en en-fr fr-de de-fr fr-es es-fr; do
  hf download Helsinki-NLP/opus-mt-$pair \
    --include "*.safetensors" --include "*.json" --include "*.txt" \
    --include "source.spm" --include "target.spm" --include "vocab.json"
done

# ── TTS — F5-TTS V1 Base only (~1.5 Go) ──
hf download SWivid/F5-TTS \
  --include "F5TTS_v1_Base/*" --include "vocab.txt"

# ── RVC base models (~400 Mo) ──
cd /runpod-volume/rvc_assets
# Hubert via transformers (remplace hubert_base.pt + fairseq retiré)
hf download facebook/hubert-base-ls960 \
  --include "*.safetensors" --include "*.json" --include "*.txt"
# rmvpe pour la détection F0
wget https://huggingface.co/lj1995/VoiceConversionWebUI/resolve/main/rmvpe.pt
```

Total Volume après ces commandes : **~14 Go** (Whisper CT2 750 Mo +
NLLB 5 Go + 6 OPUS-MT 1.8 Go + F5-TTS 1.5 Go + Hubert 360 Mo + rmvpe
200 Mo + cache ct2 généré au 1er run ~5 Go).
Reste ~16 Go pour ~80 modèles RVC utilisateur (à 200 Mo chacun).

### 5.5 Stop du Pod éphémère

Une fois les modèles téléchargés :

1. Vérifie : `ls -lh /runpod-volume/hf-cache/hub/` (tu dois voir les
   `models--*` correspondants à chaque repo HF)
2. Stop le Pod via la console RunPod

> **⚠️ Bug d'UI RunPod** : le bouton Stop affiche systématiquement le
> warning "You do not have a volume configured. ALL DATA will be lost!"
> même quand un Volume est correctement attaché. Ce warning est trompeur.
>
> **Vraie source de vérité** : la section **"Network volume"** sur la
> page de détails du Pod. Si elle indique le nom de ton Volume + le
> mount path `/runpod-volume`, le stop est sans risque — toutes les
> données du Volume sont préservées.

---

## 6. Build et déploiement du worker Docker

### 6.1 Build local de l'image

Sur ta machine de dev (Mac ou Linux avec Docker installé) :

```bash
cd voicebridge/runpod-worker
docker build -t voicebridge-worker:v3.0.0 .
```

Le build prend ~10 minutes (pull base CUDA + install torch + transformers
+ f5-tts depuis git + RVC deps). L'image fait **~15-20 Go**.

### 6.2 Push sur registry

```bash
docker tag voicebridge-worker:v3.0.0 <TON_USER>/voicebridge-worker:v3.0.0
docker push <TON_USER>/voicebridge-worker:v3.0.0
```

Premier push long (~30 min sur ADSL) — pousses bien le Wi-Fi rapide.

### 6.3 Création de l'endpoint Serverless

1. Console RunPod → **Serverless** → **+ New Endpoint**
2. **Image** : `<TON_USER>/voicebridge-worker:v3.0.0`
3. **GPU** : RTX 4090 (24 Go)
4. **Datacenter** : EU-FR-1 (ou alternative — voir 4.2)
5. **Network Volume** : sélectionne ton Volume (créé en 4.2)
6. **Mount Path** : ⚠️ saisis explicitement `/runpod-volume` (encore
   une fois — ne pas laisser le défaut `/workspace`)
7. **Min Workers** : 0 (scale to zero pour minimiser le coût)
8. **Max Workers** : 1 (à augmenter si multi-utilisateurs)
9. **Idle Timeout** : 5 min (worker se dé-spawn après 5 min d'inactivité)
10. **FlashBoot** : enabled (cold start divisé par 5-10×)
11. Clique **Deploy**

Note l'**Endpoint ID** — tu le saisiras côté VoiceBridge.

---

## 7. Configuration côté VoiceBridge (panel Réglages → Cloud)

Ouvre `https://<ton-domaine>` → connexion → **Réglages → Cloud**.

### 7.1 Section RunPod

Saisis :
- **API key** : `rpa_...` (de 4.3)
- **Endpoint ID** : valeur de 6.3
- **Volume ID** : valeur de 4.2
- **Datacenter** : ton choix de 4.2
- **S3 access key** + **S3 secret key** : valeurs de 4.3

Clique **💾 Enregistrer** puis **🧪 Tester la connexion**. Tu dois voir
**✅ OK · 50-300ms · EU-FR-1** (la latence dépend de la distance).

### 7.2 Section OpenAI (optionnel)

Si tu veux GPT-4o(-mini) pour la traduction qualité, saisis ta clé
`sk-...` puis **🧪 Tester**. Sans cette clé, NLLB-200 (gratuit) et
OPUS-MT (gratuit) restent disponibles pour traduire.

---

## 8. Workflow RVC complet

### 8.1 Vue d'ensemble du pipeline

```
Tu enregistres 5 blocs guidés (~20 min audio brut)
    ↓ retraitement Hostinger CPU (~5 min)
    ↓ ZIP de clips propres (44.1 kHz, 5-15s chacun)
    ↓ entraînement Kaggle GPU (~3-6h, gratuit)
    ↓ téléchargement .pth + .index
    ↓ import dans VoiceBridge → upload S3 RunPod
    ↓ disponible en mode Live "Hybride accent natif"
```

### 8.2 Phase 1 — Enregistrement guidé

Va sur **Modèles RVC → 🎤 Enregistrer un dataset** (ou directement
`/recording-session`).

1. Donne un nom à la session (ex: "JC voice v1")
2. Choisis la langue (FR ou EN — détermine les textes des 5 blocs)
3. Pour chaque bloc :
   - Lis le texte affiché à voix haute (~4 minutes par bloc)
   - Pièce calme, micro à 15-20 cm de la bouche
   - Tu peux mettre en pause et reprendre — les silences seront
     coupés au retraitement
4. Au 5e bloc, clique **Terminer →** : le retraitement async démarre
   automatiquement (barre de progression)

### 8.3 Phase 2 — Validation et export ZIP

Une fois le retraitement fini, tu vois :
- Un **score qualité 0-100** (basé sur SNR, distribution durées, niveau)
- La liste des clips (~140-180 clips de 5-15s) avec lecteur audio
- Un bouton **🗑** par clip pour retirer ceux qui sonnent mal
- Bouton **📥 Télécharger ZIP** quand tu es satisfait

### 8.4 Phase 3 — Entraînement Kaggle

Pourquoi Kaggle ? Parce qu'ils offrent **30h GPU/semaine gratuit** —
suffisant pour 5-10 modèles RVC par semaine.

1. Crée un compte sur https://kaggle.com
2. **Vérifie ton numéro de téléphone** (Settings → Account → Phone
   verification) — obligatoire pour l'accès GPU
3. Forke le notebook **"Applio RVC Trainer"** :
   https://www.kaggle.com/code/lemonpepper/applio-rvc-trainer
4. Sur Kaggle → **+ New Dataset** → drag & drop ton ZIP VoiceBridge
   → nom : `voicebridge-rvc-{ton-prenom}` → visibilité Privé
5. Ouvre le notebook forké
6. **Settings** → Accelerator : **GPU T4 x2** (gratuit)
7. **Add data** → ton dataset privé
8. Modifie la 1ère cellule : nom du modèle = `{ton-prenom}_v1`
9. Run All → reviens dans 3-6h

> Kaggle déconnecte après ~1h d'inactivité. Reviens régulièrement
> cliquer sur la fenêtre.

### 8.5 Phase 4 — Téléchargement du modèle

Quand le training est fini, dans `/kaggle/working/` :
- `model.pth` (~150 Mo) — le modèle RVC
- `added_*.index` (~50 Mo) — l'index FAISS (qualité +)

Télécharge les deux.

### 8.6 Phase 5 — Import dans VoiceBridge

VoiceBridge → **Modèles RVC → + Importer un .pth** (ou
`/rvc-import`).

1. Drag-drop ton `.pth` dans la zone "Modèle"
2. Drag-drop ton `.index` dans la zone "Index FAISS" (optionnel mais
   recommandé)
3. Donne un nom au modèle (ex: "JC voice v1")
4. Optionnel : associe-le à une voix existante de ta lib
5. Clique **⬆ Uploader vers RunPod**

L'upload S3 vers le Volume RunPod prend 30-60 secondes (200 Mo
typiques). Une barre de progression suit le transfert byte par byte.

À la fin, le modèle apparaît dans la liste avec status **✅ Actif**.
Tu peux cliquer **🧪 Tester** pour générer un sample audio rapide.

---

## 9. Modes Live — choix et utilisation

### 9.1 Sélection du mode

Va sur **Studio → 📡 Parler en direct** → carte du mode souhaité.

Les modes GPU (gpu-clone, gpu-native, gpu-hybrid) sont **automatiquement
grisés** si RunPod n'est pas configuré. Le mode hybride est aussi
indisponible si tu n'as aucun modèle RVC actif.

### 9.2 Pré-warming

**Avant ta 1ère phrase**, clique **🔥 Préchauffer GPU** dans la
section "Configuration". Ça charge les modèles ML en VRAM côté worker
(~5-10s la première fois de la journée).

Sans préchauffage, ta 1ère phrase aura un cold start visible (+5-15s
pour le démarrage du worker).

### 9.3 Mode hybride : sélection RVC + voix native

En mode **gpu-hybrid**, deux sélecteurs apparaissent :

- **Voix** : sélectionne une voix `kind="native"` de ta lib (ex:
  EN — voix homme native). C'est la voix qui fournit l'accent.
- **Modèle RVC** : sélectionne ton `.pth`. C'est ce qui transforme
  le timbre vers ta voix.

Résultat : "Hello" dit avec **ta voix** mais **accent natif anglais
parfait**.

### 9.4 Provider de traduction et briefings

Dans la section "🌐 Traduction Live" :

- **OPUS-MT (CPU local)** : gratuit, FR↔EN/DE/ES, 80ms — défaut V1
- **OPUS-MT (GPU)** : gratuit, mêmes paires, 80ms — pour économiser le CPU
- **NLLB-200** : gratuit, 200+ langues, 150ms — pour les langues exotiques
- **GPT-4o-mini** : ~0.0004€/phrase, 600ms — qualité + contexte
- **GPT-4o** : ~0.005€/phrase, 1100ms — top qualité

Les providers GPT supportent les **briefings** : un contexte de
session injecté dans le prompt système qui aide GPT à résoudre les
ambiguïtés de pronoms, à utiliser le bon vocabulaire métier, etc.

Tu peux gérer tes briefings sauvegardés depuis la page **/briefings**
ou éditer un briefing ad-hoc dans le Studio Live.

---

## 10. Troubleshooting

### 10.1 Mode GPU ne démarre pas — "RunPod non configuré"

→ Vérifie **Réglages → Cloud → État cloud actuel**. Si "RunPod : —
non configuré", saisis tes clés. Si déjà configuré mais l'erreur
persiste, clique **🧪 Tester** — tu auras un message précis.

### 10.2 Endpoint répond "FAILED" / cold start très long

Possibilités :
- **Première utilisation de la journée** : c'est normal, FlashBoot
  doit retélécharger le snapshot (~30-60s).
- **Mauvais mount path** : vérifie sur l'endpoint que tu as bien
  saisi `/runpod-volume` (et non `/workspace`).
- **Modèles non pré-téléchargés** : tu peux le voir dans les logs
  RunPod (le worker tente de télécharger Whisper depuis HuggingFace).

### 10.3 RTX 4090 "out of stock"

Cas réel observé en prod. Solutions :
- Switche le datacenter de l'endpoint vers une alternative
  (EU-RO-1, EU-NL-1, EU-CZ-1, EUR-NO-1)
- Ou attends quelques minutes (la disponibilité tourne)
- Ou augmente vers RTX A6000 (plus cher mais plus dispo)

### 10.4 Warning "ALL DATA will be lost" au Stop d'un Pod

**À ignorer** — c'est un bug d'affichage de RunPod. Vérifie la
section "Network volume" sur la page du Pod :
- Si le nom de ton Volume + mount path apparaissent → stop sans risque
- Si rien n'apparaît → le Pod n'avait pas le Volume attaché, là tes
  données seront effectivement perdues

Pour éviter ce piège : utilise systématiquement le bouton **"Configure
Pod with volume"** depuis la page du Volume (pas "Deploy a Pod"
générique).

### 10.5 Upload .pth échoué

Causes fréquentes :
- **S3 credentials non saisis** (Réglages → Cloud → la section S3
  est vide)
- **Volume ID ne correspond pas** au datacenter de l'endpoint
- **Mauvaise région** : l'API S3 RunPod est régionale, tes credentials
  doivent être ceux du Volume où tu pousses

### 10.6 Recovery master key (perte de `data/.master_key`)

Si le fichier `/var/voicebridge/data/.master_key` est perdu (corruption
disque, mauvais backup, suppression accidentelle), toutes les clés
API tierces (RunPod, OpenAI, S3) deviennent **indéchiffrables**.

L'app ne plante pas, mais les modes GPU sont indisponibles tant que
les clés ne sont pas re-saisies.

**Procédure de recovery** :

```bash
# Sur ton VPS
cd /var/voicebridge/app/Site/backend
sudo -u voicebridge ./venv/bin/python manage.py reset-cloud-secrets

# Le script :
#  1. Régénère un nouveau /var/voicebridge/data/.master_key
#  2. Supprime de config.json toutes les clés *_encrypted
#     (RunPod API key, S3 access/secret, OpenAI)
#  3. Redémarre voicebridge.service

# Puis va sur https://ton-domaine/settings#cloud et re-saisis les clés.
```

> **Pour éviter ce cas** : sauvegarde régulièrement
> `/var/voicebridge/data/` avec `.master_key` inclus.

### 10.7 "Cache HF saturé" / Volume plein

Sur le Volume RunPod, vérifie l'occupation :

```bash
# Spawn un Pod éphémère + exécute
du -sh /runpod-volume/* | sort -h
```

Suspects courants :
- Modèles téléchargés sans `--include` (formats redondants TF/Flax/Marian)
- Vieux modèles RVC orphelins (utilisateurs supprimés)
- Cache CTranslate2 dupliqué

Solution : suppr manuel + re-téléchargement avec les bons filtres.

---

## 11. Annexe — Coût détaillé

| Composant | Coût mensuel |
|---|---|
| Hostinger KVM 4 (Paris) | 16 € |
| RunPod Network Volume 30 Go | 3.5 € |
| RunPod RTX 4090 inférence (8h Live/mois) | ~2.7 € |
| OpenAI GPT-4o-mini (~10 000 trad) | ~0.4 € |
| **Total V3 usage modéré** | **~22-23 €/mois** |

Avec usage intensif (30h Live/mois) : ~30 €/mois.

---

## 12. Ressources externes

- **Documentation RunPod** : https://docs.runpod.io
- **Notebook Kaggle Applio** :
  https://www.kaggle.com/code/lemonpepper/applio-rvc-trainer
- **F5-TTS** : https://github.com/SWivid/F5-TTS
- **HuggingFace CLI** :
  https://huggingface.co/docs/huggingface_hub/main/en/guides/cli
- **NLLB-200** :
  https://huggingface.co/facebook/nllb-200-distilled-1.3B
- **Mozilla Common Voice** : https://commonvoice.mozilla.org

---

*VoiceBridge V3 · Document généré automatiquement depuis
`docs/rvc-user-guide.md` · pour signaler une erreur dans ce guide,
ouvre une issue sur le dépôt GitHub.*
