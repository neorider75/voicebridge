# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Règles de travail (non négociables)

1. **Penser avant de coder.** Posture de dev senior (15 ans). En cas de doute → poser la question à l'utilisateur. **Ne jamais choisir seul** en interprétant une consigne ambiguë.
2. **Simple d'abord.** Code minimal qui résout le problème posé. Pas d'abstractions spéculatives, pas de "flexibilité" que personne n'a demandée.
3. **Modifications chirurgicales.** Ne toucher que ce qui est demandé. Pas d'"amélioration" du code voisin, pas de refactor de ce qui n'est pas cassé, pas de suppression de commentaires non compris.
4. **Transformer le vague en cible vérifiable** avant d'écrire une ligne. Exemple : "ajoute de la validation" devient "écrire les tests pour les entrées invalides, puis les faire passer".

## État du dépôt

Ce dépôt ne contient pour l'instant **que des spécifications et une maquette UX** — aucun code applicatif n'a encore été écrit. Il n'existe donc ni commande de build, ni de lint, ni de tests à exécuter. Le travail consiste soit (a) à affiner la spec ou la maquette, soit (b) à amorcer l'implémentation décrite dans les specs.

## Organisation des fichiers

- `Spec/voicebridge_v8.html` — maquette HTML/CSS/JS en un seul fichier. C'est la **cible UX/UI canonique**. L'implémentation doit en reproduire fidèlement les interactions, animations, structures d'étapes, typographie et palette.
- `Spec/voicebridge_specs/` — jeu complet de spécifications (en français). Commencer par `README.md`, puis suivre les fichiers numérotés :
  - `01-architecture.md` — stack globale, pipelines audio (Live, TTS fichier, STT fichier, ajout de voix par URL), chargement/déchargement des modèles, cibles de performance.
  - `02-features-v1.md`, `03-features-v2-v3.md` — périmètre V1 vs roadmap V2/V3. Les entrées V2/V3 doivent être présentes dans l'UI mais grisées en V1.
  - `04-frontend-specs.md` — specs front page par page.
  - `05-backend-api.md` — arborescence du projet FastAPI (`app/main.py`, `routes/`, `models/`, `services/`, `utils/`), endpoints, payloads.
  - `06-voicebridge-app.md` — application macOS menu bar (rumps + pyaudio + websockets, packagée via PyInstaller).
  - `07-security.md` — auth, sessions, rate limiting, lockout. À implémenter dès la V1.
  - `08-installation.md` — `install.sh` interactif (point d'entrée pour le déploiement sur un VPS Ubuntu vierge).
  - `09-data-storage.md` — arborescence disque sous `/var/voicebridge/data/` (config.json, voices/, audio/, models/, logs/). **Aucune base SQL** — uniquement JSON + filesystem.
  - `10-models-and-tools.md` — modèles ML et outils tiers utilisés.
- `Site/` — **racine du code applicatif** (backend FastAPI, frontend éclaté, app macOS, install.sh). Vide jusqu'au scaffolding initial.

`Spec/voicebridge_specs/voicebridge_mockup.html` est un doublon de `voicebridge_v8.html` ; considérer `voicebridge_v8.html` comme la source de vérité.

## Architecture (vue d'ensemble)

VoiceBridge est une plateforme auto-hébergée de clonage vocal / TTS / STT. Trois composants coopèrent :

1. **Backend** — FastAPI (Python 3.11) derrière Nginx + Let's Encrypt. Expose une API REST `/api/*` et un WebSocket `/ws/stream`. Encapsule plusieurs modèles ML chargés **à la demande** (NeuTTS Nano Q4/Q8 FR+EN, NeuCodec, Kyutai 1B STT, Silero VAD, MelodyMachine/Deepfake-audio-detection-V2, watermark Perth) ainsi que `ffmpeg` et `yt-dlp`. Décharge automatique des modèles après ~15 min d'inactivité. Le backend doit broadcaster les changements d'état (ex : voix active) à tous les clients connectés via WebSocket.
2. **Front web** — single page vanilla HTML/CSS/JS (pas de framework). La maquette `voicebridge_v8.html` est la référence.
3. **App macOS** (`VoiceBridge.app`) — app menu bar Python + rumps, packagée avec PyInstaller. Capture le micro, stream vers le WebSocket du backend, joue l'audio cloné dans BlackHole pour que Teams/Zoom/Meet le récupèrent. L'URL du serveur est **intégrée au build au moment de l'installation**, jamais hardcodée dans le code source.

La persistance est **uniquement sur filesystem** sous `/var/voicebridge/data/` : `config.json` (hash bcrypt du mot de passe, sha256 du token API, domaine), `voices/` (WAV de référence + ref_codes pré-encodés `.pt` + `metadata.json`), `audio/` (fichiers générés avec politique de rétention), `models/`, `logs/`. Aucune base SQL par choix de design.

Le pipeline Live est le plus subtil : micro → WebSocket → découpage Silero VAD (silence > 400 ms ou chunk > 4 s) → buffer circulaire 5 s (invisible pour l'utilisateur, pour la résilience) → Kyutai STT → NeuTTS Q4 avec ref_codes pré-encodés → NeuCodec → watermark Perth → WebSocket retour client. Les ref_codes sont encodés **une seule fois à la création de la voix** (`tts.encode_reference()`) et conservés en RAM (~5 Mo par voix) pour minimiser la latence d'inférence.

## Règles non négociables issues des specs

- **V1 supporte uniquement le français et l'anglais.** Ne pas activer les autres langues NeuTTS même si le modèle les gère.
- **Reproduire la maquette à la lettre** — visuels, interactions, structures d'étapes, animations.
- **Toute la sécurité dès la V1** (cf. `07-security.md`) : bcrypt (coût 12) sur le mot de passe, cookie `vb_session` (`HttpOnly` + `Secure` + `SameSite=Strict`, 8 h d'inactivité, signé via `itsdangerous`, pas de JWT), rate limits slowapi, délais de login progressifs, lockout IP. Le reset password est **uniquement** en CLI via `manage.py reset-password` — pas de flow "mot de passe oublié" sur le web.
- **Ne jamais hardcoder** le mot de passe admin ni le nom de domaine — tous deux sont saisis dans `install.sh`.
- **Aucune donnée ne sort du VPS** sauf le téléchargement initial des modèles depuis HuggingFace à l'installation.
- **Le watermark Perth est automatique** sur tous les audios générés (déjà câblé dans NeuTTS — ne pas le retirer).
- **Pas de base SQL.** Métadonnées en JSON ; secrets hashés (bcrypt pour le mot de passe, SHA-256 pour le token API).
- **Modèles chargés à la demande** sauf si le mode permanent est activé ; prévoir une latence de +3 à 5 s au premier appel et la signaler dans l'UI.
- **Les features V2/V3 doivent apparaître dans l'UI mais grisées** en V1, pas supprimées.
- Le buffer de continuité Live de 5 s est **invisible pour l'utilisateur** mais nécessaire — ne pas l'exposer comme réglage.
