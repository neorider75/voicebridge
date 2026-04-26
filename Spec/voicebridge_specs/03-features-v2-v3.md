# 03 - Features V2 et V3 (roadmap)

Ce document liste les features futures à **prévoir dans l'architecture V1** pour faciliter l'évolution, mais à griser dans l'UI.

## V2

### Traduction temps réel FR ↔ EN

**Périmètre**
- LibreTranslate self-hosted sur le VPS
- Paires : FR → EN et EN → FR uniquement
- Traduction en streaming (phrase par phrase via détection de ponctuation)

**Pipeline V2 Live avec traduction**
```
Micro FR → Kyutai STT → texte FR → LibreTranslate → texte EN → NeuTTS EN (voix) → audio EN
```

**UI à griser dans le Live V1**
- Champs "Traduction" avec sélecteurs source/cible
- Badge "V2" violet en haut à droite de la card

**Latence cible V2** : 1.0 à 1.3s

### Mode guidé intonation

**Périmètre**
- 4 intentions : Neutre / Enthousiaste / Urgent / Posé
- Adapte automatiquement la ponctuation du texte avant synthèse
- Aide les utilisateurs non-experts à obtenir une intonation naturelle

**UI à griser dans le Live V1**
- Radio group avec les 4 intentions
- Badge "V2"

### Mode intensif programmé

**Périmètre**
- Programmation de sessions (jour, heure début, heure fin)
- Récurrence : une fois / hebdomadaire / jours ouvrés
- Préchauffage automatique X minutes avant
- Déchargement étendu (2h d'inactivité)
- APScheduler intégré au backend

**UI à griser dans Réglages V1**
- Carte "Mode Intensif" avec badge "V2"
- Description : "Programmez des sessions avec préchauffage automatique et déchargement étendu."

### Application Windows VoiceBridge

**Périmètre**
- Équivalent macOS pour Windows
- Compatible VB-Cable au lieu de BlackHole
- Tray system Windows
- Build via PyInstaller pour Windows

### Enregistrement de session Live

**Périmètre**
- Bouton "🔴 Enregistrer la session" dans le Live
- Écriture sur disque uniquement quand activé
- Apparaît dans Enregistrements après la session

---

## V3

### Voice Conversion (RVC) - accent natif

**Périmètre**
- Modèle RVC (Retrieval Voice Conversion) via Applio
- Entraînement sur GPU cloud (Kaggle gratuit ou RunPod)
- Inférence sur le VPS CPU
- Permet de parler une langue avec l'accent natif de la voix cible

**Pipeline V3 complet**
```
Micro FR → Kyutai STT → texte FR → LibreTranslate → texte EN
       → NeuTTS EN (voix neutre) → audio EN
       → RVC (modèle entraîné sur la voix cible) → ta voix avec accent EN natif
```

**Bibliothèque de modèles RVC**
- Stockage `.pth` dans `/var/voicebridge/rvc_models/`
- UI de gestion (ajout, suppression, association voix TTS + modèle RVC)
- Import de modèles communautaires

**Enregistrement guidé pour entraînement RVC**
- Session guidée avec textes phonétiquement riches
- 5 à 10 minutes minimum pour entraînement de qualité
- Indicateur de progression phonétique
- Export pour entraînement Kaggle/RunPod

**Latence V3**
- CPU : ~3.5s (mode fichier uniquement)
- GPU : ~0.8s (live possible avec upgrade)

### Mode permanent

**Périmètre**
- Modèles chargés en permanence au démarrage
- Jamais déchargés automatiquement
- Déchargement manuel uniquement
- Sélection des modèles à garder en permanence

**UI à prévoir V3**
- Option supplémentaire dans Réglages → Serveur → Déchargement
- Liste avec checkboxes des modèles à garder en permanence

### Mode live avec accent natif

**Périmètre**
- Pipeline V3 complet en temps réel
- Nécessite un GPU dédié (upgrade VPS ou RunPod à la demande)
- Indicateur dans l'UI si GPU disponible ou non

---

## Préparations V1 pour V2/V3

### Architecture à prévoir

**Backend** :
- Système de plugins/modules pour ajouter LibreTranslate et RVC sans refonte
- Pipeline audio modulaire (chaque étape encapsulée)
- API extensible (nouveaux endpoints sans casser les anciens)

**Frontend** :
- Cards/sections grisées avec badge "V2" ou "V3"
- Code commenté pour les futures features
- Variables CSS pour faciliter l'ajout de nouveaux thèmes ou couleurs

**Base de données fichiers** :
- `voices/metadata.json` extensible avec champs futurs (rvc_model_id, etc.)
- `config.json` extensible avec nouveaux paramètres

### Points de vigilance

- Ne pas hardcoder les langues dans le code (utiliser des constantes)
- Ne pas hardcoder le modèle TTS (passer par configuration)
- Préparer les hooks pour la traduction (passe-plat en V1, actif en V2)
- Préparer les hooks pour la voice conversion (passe-plat en V1, actif en V3)

---

## Tableau récapitulatif features

| Feature | V1 | V2 | V3 |
|---|---|---|---|
| TTS fichier WAV/MP3 | ✅ | ✅ | ✅ |
| Qualité Q4/Q8 | ✅ | ✅ | ✅ |
| TTS stream live | ✅ | ✅ | ✅ |
| STT FR/EN Kyutai | ✅ | ✅ | ✅ |
| STT + TTS live | ✅ | ✅ | ✅ |
| Mes voix CRUD | ✅ | ✅ | ✅ |
| Ajout voix par enregistrement | ✅ | ✅ | ✅ |
| Ajout voix par fichier | ✅ | ✅ | ✅ |
| Ajout voix par URL (yt-dlp) | ✅ | ✅ | ✅ |
| Détection deepfake (Perth + Deepfake-audio-detection-V2) | ✅ | ✅ | ✅ |
| Page Enregistrements | ✅ | ✅ | ✅ |
| Rétention 24h/48h/Session | ✅ | ✅ | ✅ |
| Buffer continuité Live 5s | ✅ | ✅ | ✅ |
| Silero VAD | ✅ | ✅ | ✅ |
| Dark/Light mode | ✅ | ✅ | ✅ |
| Login + sécurité | ✅ | ✅ | ✅ |
| Clé API + token VoiceBridge | ✅ | ✅ | ✅ |
| App macOS VoiceBridge | ✅ | ✅ | ✅ |
| Injection BlackHole (Teams) | ✅ | ✅ | ✅ |
| Polling status 5s | ✅ | ✅ | ✅ |
| Préchauffage manuel | ✅ | ✅ | ✅ |
| Nettoyage manuel | ✅ | ✅ | ✅ |
| Cron nettoyage rétention | ✅ | ✅ | ✅ |
| Traduction FR↔EN | ❌ | ✅ | ✅ |
| Mode guidé intonation | ❌ | ✅ | ✅ |
| Mode intensif programmé | ❌ | ✅ | ✅ |
| App Windows | ❌ | ✅ | ✅ |
| Enregistrement session Live | ❌ | ✅ | ✅ |
| RVC accent natif | ❌ | ❌ | ✅ |
| Mode permanent | ❌ | ❌ | ✅ |
| Live accent natif (GPU) | ❌ | ❌ | ✅ |
