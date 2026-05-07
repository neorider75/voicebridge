# Plan de tests V3

> **Version 3.0** · mai 2026.
>
> Ce document décrit **10 scénarios E2E** à exécuter manuellement avant
> de merger `feat/v3-live-gpu` dans `main`. Pour chaque scénario : pré-
> requis, étapes, résultat attendu, critères de succès / échec.
>
> Les **tests unitaires Python** (mockés, sans GPU/RunPod) sont dans
> `Site/backend/tests/` et `runpod-worker/tests/` — exécutables via
> `pytest` en CI.
>
> Le **script de mesure de latence** est dans
> `Site/install/scripts/measure_v3_latency.py`.

---

## Pré-requis communs (avant tous les tests)

1. ✅ Dépôt sur la branche `feat/v3-live-gpu`
2. ✅ VPS Hostinger avec `install.sh` déployé (incluant phase 14 Cloud
   config — clés RunPod et OpenAI saisies, ou flag `--skip-cloud` pour
   les scénarios CPU-only)
3. ✅ Worker Docker buildé et poussé sur Docker Hub
4. ✅ Endpoint Serverless RunPod déployé avec mount path `/runpod-volume`
5. ✅ Volume RunPod pré-rempli (~17 Go de modèles ML) — voir
   `runpod-worker/README.md`
6. ✅ Au moins une voix clonée existante (Juliette V1 par défaut)
7. ✅ Au moins un modèle RVC actif (pour les scénarios hybrides)

---

## Scénario 1 — Régression V1 : TTS fichier français NeuTTS Q8

**Objectif** : vérifier qu'aucune fonctionnalité V1 n'a été cassée par
les changements V3.

**Pré-requis** : voix Juliette présente, mode V1 fonctionnel
(installation par défaut sans config cloud).

**Étapes** :
1. Ouvrir `/studio` → onglet "🗣 Lire un texte"
2. Saisir un texte français de 30-50 mots dans la zone texte
3. Sélectionner voix : **Juliette** (FR)
4. Format : **WAV**
5. Moteur TTS : **NeuTTS Nano**
6. Qualité : **Haute qualité (Q8)**
7. Rétention : **Session uniquement**
8. Cliquer **🎙 Générer**

**Résultat attendu** :
- ✅ Génération réussie en 30-90 secondes (CPU)
- ✅ Audio WAV écouté correctement avec la voix de Juliette
- ✅ Aucune erreur dans `journalctl -u voicebridge -n 100`
- ✅ Pas de message d'erreur `cloud` ou `runpod` dans les logs

**Critère d'échec** :
- ❌ Erreur `ModuleNotFoundError` (import V3 cassé)
- ❌ Génération bloquée
- ❌ Audio inaudible ou voix différente

---

## Scénario 2 — Mode CPU FR/EN Live (V1) sans configuration cloud

**Objectif** : vérifier que le mode `cpu-fr-en` fonctionne **identiquement
à V1** même sans RunPod configuré.

**Pré-requis** :
- VPS frais avec `install.sh --skip-cloud` (RunPod intentionnellement
  non configuré)
- Voix Juliette présente

**Étapes** :
1. Ouvrir `/studio` → onglet "📡 Parler en direct"
2. **Carte Mode** : vérifier que les 3 modes GPU sont **grisés** avec
   mention `(non configuré)`
3. Carte sélectionnée par défaut : **Authentique CPU FR/EN**
4. Voix : **Juliette**
5. Langue source : **Français**
6. Cliquer la zone micro pour démarrer
7. Parler 2-3 phrases françaises
8. Cliquer la zone micro pour arrêter

**Résultat attendu** :
- ✅ Cartes GPU correctement grisées (Décision 4)
- ✅ Pipeline V1 : Kyutai STT → NeuTTS Q4 fonctionne
- ✅ Audio retour avec voix Juliette en ~5-15s
- ✅ Indicateur de coût session **caché** (mode CPU)
- ✅ Aucun appel HTTP vers RunPod dans les logs

**Critère d'échec** :
- ❌ Carte mode `cpu-fr-en` non sélectionnée par défaut
- ❌ Pipeline V1 bloque ou produit un audio cassé
- ❌ Tentative d'appel à RunPod dans les logs serveur

---

## Scénario 3 — Mode GPU clone (multilingue ma voix)

**Objectif** : vérifier qu'on peut cloner sa voix dans une langue cible
via F5-TTS sur RunPod.

**Pré-requis** :
- RunPod configuré (clés saisies, endpoint déployé)
- Voix utilisateur "Moi" déjà uploadée (sample 10s parlé)
- Modèles ML pré-téléchargés dans le Volume

**Étapes** :
1. Studio Live → **Carte "Multilingue – ta voix"** (gpu-clone)
2. Voix : **Moi**
3. Langue source : **Français**
4. Cocher **🌐 Traduction Live** → Traduire vers : **Allemand**
5. Provider : **NLLB-200**
6. Cliquer **🔥 Préchauffer GPU**, attendre "✅ GPU prêt"
7. Cliquer la zone micro et parler une phrase française simple

**Résultat attendu** :
- ✅ Préchauffage retourne en 5-15s la 1ère fois (cold start), <1s ensuite
- ✅ Transcription FR affichée
- ✅ Traduction DE affichée en bleu
- ✅ Audio cloné en allemand avec **timbre proche de "Moi"** mais
  accent FR audible (attendu pour `gpu-clone`)
- ✅ Indicateur coût session affiche un cumul croissant
- ✅ Latence 1er mot audible : **1.5-2.5s** (cf. cible spec)

**Critère d'échec** :
- ❌ Préchauffage > 30s sans signal
- ❌ Audio retour avec timbre générique (pas la voix uploadée)
- ❌ Latence > 5s sur phrases courtes

---

## Scénario 4 — Mode GPU native (voix générique)

**Objectif** : vérifier la sélection d'une voix native pré-stockée et
la qualité de l'accent natif.

**Pré-requis** :
- Au moins une voix avec `kind: "native"` dans la lib (ex: EN — voix
  homme native, importée manuellement ou via patch Q1 après K)
- ⚠️ **Connu limitation V3.0** : les voix natives par défaut (EN/ES/PT/IT)
  ne sont pas auto-seedées (Patch Q1 prévu après K). Tester avec une
  voix native uploadée manuellement.

**Étapes** :
1. Pré-requis : importer manuellement un sample 10s d'une voix anglaise
   native via `/voices/new` avec un nom commençant par "EN —"
2. Studio Live → carte **"Voix native"** (gpu-native)
3. Voix : **EN — voix native** (la voix uploadée)
4. Langue source : **Français**
5. Traduction : **Anglais**, provider NLLB
6. Préchauffer + parler

**Résultat attendu** :
- ✅ Audio retour avec **timbre de la voix native sélectionnée**
- ✅ Accent anglais natif perceptible
- ✅ Pas de timbre utilisateur (différent du scénario 3)

**Critère d'échec** :
- ❌ Identique au scénario 3 (= preuve que le filtre kind n'est pas
  appliqué — patch Q1 effectivement requis)
- ❌ Aucune différence perceptible entre gpu-clone et gpu-native

---

## Scénario 5 — Mode GPU hybride (RVC accent natif)

**Objectif** : vérifier la cascade F5-TTS native → RVC = ta voix avec
accent natif parfait.

**Pré-requis** :
- Voix native EN dans la lib
- Au moins un modèle RVC actif (.pth importé via `/rvc-import` et status
  `active`)

**Étapes** :
1. Studio Live → carte **"⭐ Hybride accent natif"** (gpu-hybrid)
2. Voix : **EN — voix native**
3. **Modèle RVC** : sélectionner ton modèle entraîné (ex: `JC voice v1`)
4. Langue source : Français, traduction Anglais
5. Préchauffer GPU (composant RVC inclus automatiquement)
6. Parler une phrase française

**Résultat attendu** :
- ✅ Préchauffage charge **whisper + f5tts + nllb + rvc** (vérifier
  notif macOS si app utilisée)
- ✅ Audio retour avec **timbre utilisateur** (proche de "Moi")
- ✅ **Accent anglais natif perceptible** (pas accent français)
- ✅ Latence 1er mot : 2-3s (overhead RVC)

**Critère d'échec** :
- ❌ Mode bloque sans `rvc_model_id` (vérifier message d'erreur clair)
- ❌ Audio retour identique à gpu-clone (RVC non appliqué)
- ❌ Latence > 5s même phrases courtes

---

## Scénario 6 — Switch de provider de traduction en cours de session

**Objectif** : vérifier que le switch de provider sans déconnexion
WebSocket fonctionne.

**Pré-requis** : RunPod + OpenAI configurés.

**Étapes** :
1. Studio Live, mode **gpu-clone**, provider **NLLB-200**
2. Démarrer la session, parler 2 phrases (vérifier traduction NLLB OK)
3. **Sans arrêter la session**, changer le provider à **GPT-4o-mini**
   dans le dropdown
4. Constat attendu : la session reste connectée (pas de re-handshake)
5. Parler 2 nouvelles phrases

**Résultat attendu** :
- ✅ Session WebSocket reste connectée (pas de "Déconnecté" dans liveStatus)
- ✅ Les nouvelles phrases sont traduites avec **GPT** (qualité visible
  sur le contexte conversationnel et les pronoms)
- ✅ Coût session **incrémente** après changement (ligne `openai` dans
  `provider_breakdown` cost_update)

**Critère d'échec** :
- ❌ Session se déconnecte au changement
- ❌ Provider semble ne pas changer (logs Python doivent montrer le
  switch)

---

## Scénario 7 — Wizard RVC complet (enregistrement → ZIP)

**Objectif** : valider tout le pipeline d'enregistrement guidé.

**Étapes** :
1. Aller sur `/recording-session`
2. **Step 0** : nom = "Test RVC v1", langue = Français, **Démarrer**
3. **Steps 1-5** : pour chaque bloc, lire le texte affiché à voix
   haute (~30s minimum, max 4 min)
4. Vérifier le compteur de durée du bloc + total session
5. Au bloc 5, cliquer **Terminer →**
6. Phase retraitement : suivre la barre de progression (~2-5 min)
7. À la fin, cliquer **Valider & télécharger**
8. Vérifier le score qualité, écouter quelques clips
9. Cliquer **Télécharger ZIP**

**Résultat attendu** :
- ✅ Capture micro fonctionne (AudioWorklet)
- ✅ Upload chunks tous les 3s sans erreur réseau
- ✅ Retraitement async émet des messages WebSocket lisibles ("Découpage
  en clips", "Débruit clip 5/12", etc.)
- ✅ Score qualité affiché (>= 60 pour audio normal)
- ✅ ZIP téléchargé contient les clips WAV 44.1kHz + manifest.json

**Critère d'échec** :
- ❌ Capture micro échoue (permissions browser)
- ❌ Upload chunks bloque (404, 500, ou rate limit)
- ❌ Retraitement plante (vérifier `journalctl` pour stack trace)
- ❌ ZIP corrompu ou incomplet

---

## Scénario 8 — Upload .pth RVC

**Objectif** : valider le pipeline d'upload S3 vers RunPod Volume.

**Pré-requis** :
- Un .pth RVC entraîné sur Kaggle (~150 Mo)
- Le .index FAISS associé (~50 Mo)
- RunPod configuré avec S3 credentials

**Étapes** :
1. Aller sur `/rvc-import`
2. Drag-drop le .pth dans la zone "Modèle"
3. Drag-drop le .index dans la zone "Index FAISS"
4. Nom : "Test upload v1"
5. Description : "Test scénario 8"
6. Voix associée : (laisser vide)
7. Cliquer **⬆ Uploader vers RunPod**

**Résultat attendu** :
- ✅ Validation .pth synchrone OK (pas d'erreur "magic bytes")
- ✅ Barre de progression affiche les Mo uploadés byte par byte
- ✅ Upload se termine en 30-90s (selon connexion)
- ✅ Card "✅ Modèle importé" apparaît
- ✅ Aller sur `/rvc` : le modèle apparaît avec status **Actif**
- ✅ Côté RunPod : `ls /runpod-volume/rvc_models/<model_id>/` montre
  `model.pth` + `added.index` (vérifiable via SSH sur Pod éphémère)

**Critère d'échec** :
- ❌ Validation .pth refuse un fichier valide (vérifier les magic bytes)
- ❌ Upload S3 plante (vérifier credentials + datacenter)
- ❌ Status reste "uploading" indéfiniment

---

## Scénario 9 — Cold start RunPod (1ère session)

**Objectif** : vérifier le comportement de la 1ère utilisation après
worker scale-to-zero.

**Pré-requis** :
- Endpoint RunPod en état "0 workers running" depuis > 5 min

**Étapes** :
1. Studio Live → mode **gpu-clone**, voix Moi, langue FR
2. **Sans préchauffer**, cliquer la zone micro et parler une phrase
3. Observer le temps avant le premier audio retour

**Résultat attendu** :
- ✅ Cold start visible : 5-30s avant le 1er chunk audio (FlashBoot
  divise normalement par 5-10×)
- ✅ Transcription apparaît dès que Whisper a fini (~5s après cold start)
- ✅ Audio retour arrive après synthèse F5-TTS complète

**Critère d'échec** :
- ❌ Cold start > 60s (problème worker startup ou modèles non pré-cache)
- ❌ Erreur "endpoint timeout"

---

## Scénario 10 — Régression voix existantes (Juliette / Dave)

**Objectif** : vérifier que les voix V1 (Juliette FR, Dave EN) restent
utilisables en mode V3.

**Étapes** :
1. Studio Live → mode **cpu-fr-en**, voix **Juliette**, langue FR
2. Parler une phrase → vérifier audio Juliette OK
3. Studio TTS → voix **Dave**, langue EN, texte EN simple → générer
4. Vérifier audio Dave correct
5. Studio Live → mode **gpu-clone**, voix **Juliette**, traduction EN
6. Parler une phrase FR → vérifier que Juliette est utilisée comme
   référence par F5-TTS

**Résultat attendu** :
- ✅ Juliette/Dave fonctionnent en CPU V1 sans changement
- ✅ Juliette utilisable en gpu-clone (le WAV est lu depuis voices/
  et envoyé à F5-TTS)
- ✅ Audio retour conserve le timbre de Juliette

**Critère d'échec** :
- ❌ Juliette/Dave non listées dans le sélecteur
- ❌ Mode gpu-clone refuse les voix V1

---

## Mesure de latence (script automatisé)

Lancer `Site/install/scripts/measure_v3_latency.py` qui mesure les
latences réelles dans chaque mode et produit un tableau Markdown
exploitable. Voir le script lui-même pour les options.

```bash
cd /var/voicebridge/app
sudo -u voicebridge ./venv/bin/python \
    Site/install/scripts/measure_v3_latency.py \
    --mode gpu-clone --voice-id moi --runs 5
```

Comparer les chiffres mesurés à ceux annoncés dans la doc utilisateur :
- gpu-clone court : ~1.5s
- gpu-clone moyen : ~2.5s
- gpu-hybrid : +500-1000ms

Mettre à jour `docs/rvc-user-guide.md` § 1 si écart > 30%.

---

## Tests unitaires Python (mockés, CI)

Exécuter dans le venv backend :

```bash
cd Site/backend
./venv/bin/pip install pytest pytest-asyncio
./venv/bin/pytest tests/ -v
```

Couverture cible :
- `test_secrets.py` — Fernet round-trip + master key auto-bootstrap
- `test_briefings_store.py` — CRUD + validation taille
- `test_rvc_models_store.py` — paths Volume + key generation
- `test_progress_tasks.py` — registry CRUD + GC
- `test_translation_router.py` — dispatch + fallback

Worker Docker (mockés, sans GPU) :

```bash
cd runpod-worker
pip install pytest numpy soundfile
pytest tests/ -v
```

---

## Critères globaux de bascule sur main

Avant de merger `feat/v3-live-gpu` dans `main` :

- [ ] Scénarios 1, 2, 10 (régression V1) → **tous verts**
- [ ] Scénarios 3, 5, 7, 8 (parcours V3 critiques) → **tous verts**
- [ ] Scénario 9 (cold start) → comportement acceptable
- [ ] Tests unitaires Python (backend + worker) → **tous verts**
- [ ] Mesure latence → écart < 30% vs doc utilisateur
- [ ] Tous les patches après K appliqués (cf. liste de patches)
- [ ] Aucun TODO `# TODO Phase X` non résolu dans le code merged

---

*Plan généré pour `feat/v3-live-gpu` · à archiver après le merge.*
