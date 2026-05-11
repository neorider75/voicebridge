# VoiceBridge - Spécifications complètes (V1 + V3)

## Contexte

VoiceBridge est une plateforme de **clonage vocal et synthèse vocale multilingue** auto-hébergée sur VPS, accessible via une interface web sécurisée et une application macOS pour l'injection dans les appels Teams/Zoom.

V1 (existant) : pipeline FR/EN CPU sur Hostinger seul, opérationnel.
V3 (à implémenter) : extension hybride **Hostinger + RunPod GPU + OpenAI** pour multilingue temps réel et Voice Conversion (RVC).

## Cible technique

| Composant | Stack |
|---|---|
| **Backend** | FastAPI Python 3.11 sur VPS Hostinger KVM 4 (Paris, FR) |
| **Frontend** | HTML/CSS/JS vanilla (pas de framework) |
| **App macOS** | Python + rumps + WebSocket, packagée en .app via PyInstaller |
| **GPU on-demand** | RunPod Serverless EU-FR-1 (RTX 4090, scale to zero) |
| **Trad cloud (optionnel)** | OpenAI GPT-4o-mini / GPT-4o |
| **Storage** | Fichiers + JSON (pas de base SQL) |

## Documents de référence

### V1 (existant, conservé)

| Fichier | Contenu | Statut V3 |
|---|---|---|
| `01-architecture.md` | Architecture globale, stack, pipeline audio | Inchangé |
| `02-features-v1.md` | Liste des features V1 | Inchangé |
| `04-frontend-specs.md` | Spécifications front web V1 | Inchangé |
| `05-backend-api.md` | API V1 | Inchangé (extensions dans 17) |
| `06-voicebridge-app.md` | App macOS V1 | Inchangé (extensions dans 19) |
| `07-security.md` | Sécurité V1 | Inchangé |
| `08-installation.md` | Script install V1 (14 phases) | Étendu en 20 |
| `09-data-storage.md` | Structure fichiers V1 | Inchangé |
| `10-models-and-tools.md` | Modèles ML V1 | Inchangé |

### V3 (nouveaux)

| Fichier | Contenu |
|---|---|
| `03-features-v3.md` | Liste des features V3 (remplace l'ancien 03-features-v2-v3) |
| `11-runpod-integration.md` | Architecture RunPod détaillée, Dockerfile, services/runpod_client.py |
| `12-rvc-pipeline.md` | Pipeline RVC complet : enregistrement → Kaggle → import → live |
| `13-translation-providers.md` | 6 providers de traduction, router, glossaire GPT, compteur coûts |
| `14-rvc-recording-guide.md` | Wizard d'enregistrement intégré + 5 blocs textes calibrés |
| `15-latency-optimization.md` | Catalogue des optimisations de latence par phases A/B/C |
| `16-progress-ux-pattern.md` | Pattern systématique des barres de progression (toute opération > 1s) |
| `17-backend-api-v3.md` | Extensions backend API (routes cloud/rvc/recording/progress + modifs /ws/stream) |
| `18-frontend-v3.md` | Extensions frontend (4 modes, settings panels, pages RVC) |
| `19-app-macos-v3.md` | Extensions app macOS (menu mode/RVC/translate, indicateur session) |
| `20-installation-v3.md` | Phase 14 nouvelle "Cloud config" + commandes manage.py |

### Compagnons

- `RVC_recording_guide.pdf` (parent dossier) : guide PDF 10 pages téléchargeable
- `IMPLEMENTATION_ROADMAP.md` (parent dossier) : roadmap 11 phases A-K pour Claude Code
- `voicebridge_mockup.html` : maquette HTML de référence (UX/UI cible)
- `runpod-worker/` (parent dossier) : container Docker complet (Dockerfile, handler.py, 5 wrappers de modèles, tests)

## Stratégie d'implémentation V3

**EXTENSION, PAS REFONTE.** Le code V1 fonctionne, on construit autour :
- Conserver tous les modules existants (NeuTTS, XTTS-v2, Kyutai STT, OPUS-MT CPU, etc.)
- Ajouter `services/runpod_client.py`, `services/openai_client.py`, `services/translation_router.py`, `services/rvc_*.py`
- Ajouter routes `routes/cloud.py`, `routes/rvc.py`, `routes/recording_session.py`, `routes/progress.py`
- Étendre `routes/live.py` (WebSocket) avec nouveaux modes
- Préserver compatibilité ascendante : mode V1 (`cpu-fr-en`) reste accessible et est le défaut si RunPod n'est pas configuré

## Architecture des 4 modes Live V3

| Mode | Pipeline | Latence cible | Coût |
|---|---|---|---|
| Authentique CPU FR/EN (V1, fallback) | NeuTTS Hostinger CPU | 5-15s | 0€ |
| Multilingue ma voix | F5-TTS RunPod GPU | ~1s | ~0.005€/min GPU |
| Voix native | F5-TTS native RunPod GPU | ~1s | ~0.005€/min GPU |
| Hybride accent natif | F5-TTS native + RVC RunPod GPU | ~1.2s | ~0.006€/min GPU |

## Coûts mensuels indicatifs

| Profil | Total |
|---|---|
| V1 CPU seul | 16€ |
| V3 modéré (8h Live/mois) | ~22€ |
| V3 régulier (30h Live/mois) | ~30€ |
| V3 intensif (100h Live/mois) | ~57€ |

## Pattern UX systématique : barres de progression

**Règle non négociable V3** : toute opération backend > 1 seconde DOIT afficher une barre de progression côté frontend.

Voir `16-progress-ux-pattern.md` pour le pattern complet (composant `ProgressBarUI` réutilisable, helper `ProgressSubscriber` WebSocket, format JSON standard).

Concerne notamment :
- Préchauffage GPU RunPod (cold start ~10-30s)
- Retraitement audio dataset RVC (~5 min)
- Upload .pth vers RunPod Volume (30s-3 min)
- Téléchargement modèles HF (premier appel)
- Génération TTS fichier (texte long)
- STT fichier
- Détection deepfake
- Encodage voix
- Test rapide d'un modèle RVC
- Génération PDF guide

## Maquette de référence

Le fichier `voicebridge_mockup.html` est la cible UX/UI à respecter pour les pages V1 existantes. Les pages V3 (rvc.html, recording-session.html, etc.) suivent le même langage visuel (palette rouge `#A8243C`, typographie Syne + DM Mono).

## Instructions générales pour Claude Code

1. Lire `IMPLEMENTATION_ROADMAP.md` en premier : ordre des 11 phases A-K
2. Respecter les conventions V1 : factory functions, lazy loading via manager.py, services modulaires, vanilla JS
3. Toutes les nouvelles routes V3 derrière `Depends(require_auth)` et rate limiting `slowapi`
4. Clés API chiffrées via Fernet dans config.json (services/secrets.py)
5. Préserver la rétrocompatibilité des voix existantes (Juliette/Dave) et du mode `cpu-fr-en`
6. Pattern barres de progression OBLIGATOIRE pour toute opération > 1s
7. Aucune nouvelle dépendance lourde sur Hostinger (le GPU reste sur RunPod)

## Points d'attention critiques

- Mot de passe et domaine sont demandés à l'installation, jamais hardcodés
- L'application macOS V3 reste rétrocompatible avec un serveur V1
- Le watermark Perth reste automatique sur les audios générés
- Le buffer de continuité Live (250ms en V3, vs 400ms en V1) reste invisible pour l'utilisateur
- Toutes les données transitant via OpenAI/RunPod doivent être considérées comme externalisées (à prendre en compte si l'utilisateur a des politiques de confidentialité internes)

## Langues supportées V3

| Mode | Langues |
|---|---|
| `cpu-fr-en` (V1) | FR, EN |
| `gpu-clone` (F5-TTS) | 100+ langues |
| `gpu-native` (F5-TTS native) | 9 voix natives par défaut, extensible |
| Traduction NLLB | 200+ langues |
| Traduction OPUS-MT GPU | FR↔EN/DE/ES/IT |
| Traduction GPT-4o(/-mini) | universel |
