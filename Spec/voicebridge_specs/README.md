# VoiceBridge - Spécifications complètes

## Contexte

VoiceBridge est une plateforme de **clonage vocal et synthèse vocale** auto-hébergée sur VPS, accessible via une interface web sécurisée et une application macOS pour l'injection dans les appels Teams/Zoom.

## Cible technique

- VPS Hostinger KVM 4 (4 vCPU AMD EPYC, 16 Go RAM, 200 Go NVMe SSD, Ubuntu 24.04)
- 100% self-hosted, aucune donnée ne sort du VPS
- Backend : FastAPI Python
- Frontend : HTML/CSS/JS vanilla (single page application, pas de framework)
- Application macOS : Python + rumps, packagée en .app via PyInstaller

## Documents de référence

| Fichier | Contenu |
|---|---|
| `01-architecture.md` | Architecture technique globale, stack, pipeline audio |
| `02-features-v1.md` | Liste exhaustive des features V1 à implémenter |
| `03-features-v2-v3.md` | Roadmap V2 et V3 (à griser dans l'UI V1) |
| `04-frontend-specs.md` | Spécifications détaillées du front web par page |
| `05-backend-api.md` | Endpoints API complets, payloads, comportements |
| `06-voicebridge-app.md` | Spécifications de l'application macOS |
| `07-security.md` | Sécurité, authentification, anti-bruteforce, rate limiting |
| `08-installation.md` | Script bash d'installation interactif |
| `09-data-storage.md` | Structure fichiers, config, métadonnées |
| `10-models-and-tools.md` | Modèles ML utilisés, outils tiers, téléchargements |

## Maquette de référence

Le fichier `voicebridge_v8.html` est la **cible UX/UI à respecter**. Toutes les interactions, animations, comportements visuels, structure des étapes, typographie et palette de couleurs doivent être reproduits fidèlement.

## Instructions générales pour le développement

1. **Respecter la maquette à la lettre** pour les éléments visuels et les interactions
2. **Implémenter toute la sécurité dès la V1** (cf. 07-security.md)
3. **Préparer les emplacements V2/V3** avec badges grisés dans l'UI
4. **Ne jamais stocker de mot de passe ou token en clair** (toujours hashé SHA-256)
5. **Tous les modèles ML chargés à la demande** sauf si mode permanent activé
6. **Aucune base de données SQL** — uniquement fichiers JSON pour les métadonnées
7. **Aucune donnée envoyée à des services tiers** (sauf téléchargement initial des modèles depuis HuggingFace)

## Versions

| Version | Périmètre | Statut |
|---|---|---|
| V1 | TTS/STT/Live FR+EN, gestion voix, détection deepfake, app macOS | À développer |
| V2 | Traduction FR↔EN, mode intensif programmé, app Windows | Roadmap |
| V3 | Voice Conversion (RVC) accent natif, mode permanent | Roadmap |

## Points d'attention critiques

- Le mot de passe et le nom de domaine sont demandés à l'installation et **ne peuvent pas être hardcodés**
- L'application macOS VoiceBridge.app doit être **buildée avec l'URL du serveur intégrée** (paramétrée à l'installation)
- Le watermark Perth est **automatique** sur tous les audios générés (déjà inclus dans NeuTTS)
- Le buffer de continuité Live (5s) est **invisible pour l'utilisateur** mais essentiel pour la robustesse

## Langues supportées V1

**Français et Anglais uniquement.** Pas d'allemand ni d'espagnol en V1 même si NeuTTS les supporte.
