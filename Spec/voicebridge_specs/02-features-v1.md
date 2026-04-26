# 02 - Features V1 exhaustives

## Studio - Lire un texte (TTS)

### Flux étape par étape

**Étape 1 : Texte à lire**
- Zone de texte libre, redimensionnable verticalement
- Hint sous la zone : "💡 Utilisez ? ! ... pour varier l'intonation · Les MAJUSCULES accentuent un mot"
- Étape 2 verrouillée tant que le texte est vide

**Étape 2 : Génération** (déverrouillée si texte non vide)
- Sélecteur Voix (avec drapeau langue) — liste depuis `/api/voices`
- Format : WAV (par défaut) ou MP3
- Qualité : Normale (Q4) ou Haute qualité (Q8) — Haute qualité par défaut
- Conserver le fichier : "Session uniquement (non conservé)" / "24h" / "48h"
  - Valeur par défaut héritée des réglages globaux
  - Modifiable pour cette génération uniquement
- Bouton "🎙 Générer"

**Étape 3 : Résultat** (déverrouillée après génération réussie)
- Lecteur audio inline avec barre de progression
- Si "Session uniquement" sélectionné : afficher "⚠️ Session uniquement — Téléchargez maintenant si vous souhaitez conserver ce fichier"
- Bouton "⬇ Télécharger" (dans le format choisi)

### Comportements
- Si l'utilisateur modifie le texte après génération : étape 3 reste accessible mais peut générer à nouveau pour le nouveau texte
- Texte max : 5000 caractères
- Si texte > 30s estimés : découper en chunks et concaténer côté backend

---

## Studio - Transcrire ma voix (STT)

### Flux étape par étape

**Étape 1 : Enregistrement**
- Sélecteur Langue source (FR / EN)
- Zone micro cliquable, animation niveau sonore pendant l'enregistrement
- Bouton "⏺ Démarrer" → "⏹ Arrêter" pendant l'enregistrement
- Bouton "🔄 Ré-enregistrer" toujours visible
- Étape 2 reste verrouillée tant que l'enregistrement n'est pas arrêté

**Étape 2 : Validation de la transcription** (déverrouillée après arrêt)
- Lecteur audio de l'enregistrement original
- Zone de texte avec la transcription (éditable)
- Hint : "💡 Corrigez avant de continuer si la transcription est approximative"
- Marquage ✓ vert sur l'étape 1 quand validée

**Étape 3 : Génération** (déverrouillée si texte non vide)
- Identique à TTS Étape 2 (voix, format, qualité, rétention)

**Étape 4 : Résultat** (déverrouillée après génération)
- Identique à TTS Étape 3

### Comportements
- Capture micro via Web Audio API (`getUserMedia`)
- Format de capture : WAV 24kHz mono pour Kyutai (le modèle `kyutai/stt-1b-en_fr-trfs` exige 24 kHz, cf. README HuggingFace)
- Durée max : 5 minutes (au-delà : message d'erreur)
- Si l'utilisateur ré-enregistre, les étapes 3 et 4 sont re-verrouillées

---

## Studio - Parler en direct (Live)

### Flux étape par étape

**Étape 1 : Configuration**
- Alerte si modèles non chargés : "⚠️ Modèles non chargés — latence +3 à 5s au premier appel" avec bouton "🚀 Préchauffer"
- Sélecteur Voix (drapeau langue) — étape 2 verrouillée si non choisi
- Sélecteur Langue source (FR / EN)
- **Sections V2 grisées** :
  - Traduction (sélecteurs langue source/cible)
  - Intention vocale (Neutre/Enthousiaste/Urgent/Posé)
- Sortie audio :
  - Navigateur (par défaut)
  - BlackHole (Teams/Zoom)
- Si BlackHole sélectionné :
  - Si VoiceBridge.app détecté via WebSocket : "✅ VoiceBridge connecté · BlackHole 2ch détecté"
  - Sinon : "⚠️ VoiceBridge non détecté · [Télécharger VoiceBridge]"
- Hint : "💡 Le mode Live ne génère aucun fichier · Un buffer de 5s assure la continuité en cas de micro-coupure réseau"

**Étape 2 : Session live** (déverrouillée si voix choisie ET modèles chargés)
- Zone micro avec animation
- Indicateur de latence estimée
- Bouton "⏺ Démarrer" → "⏹ Arrêter"
- Pendant la session : indicateur visuel de buffer (vert/jaune/rouge)
- Reconnexion automatique en cas de coupure WebSocket

### Comportements
- Découpe via Silero VAD (silence > 400ms ou chunk > 4s)
- Buffer 5s en RAM (deque maxlen=25)
- Pas d'écriture disque (jamais)
- Latence cible : 0.6 à 1.4s

---

## Mes voix

### Liste des voix
- Affichage en liste avec :
  - Drapeau langue (🇫🇷 ou 🇬🇧)
  - Nom de la voix
  - Métadonnées (date d'ajout, backbone, durée audio)
- Boutons par voix :
  - ▶ Écouter (toggle lecteur inline)
  - ✏️ Modifier
  - 🗑 Supprimer (avec confirmation)
- Voix protégées (samples NeuTTS) : icône 🔒 au lieu de modifier/supprimer, mais ▶ Écouter disponible

### Voix protégées par défaut (livrées avec l'installation)
- 🇫🇷 Juliette (sample NeuTTS - juliette.wav)
- 🇬🇧 Dave (sample NeuTTS - dave.wav)

### Bouton "+ Ajouter une voix"
Redirige vers `/voices/new`

### Lecteur inline
- Une seule voix peut être en lecture à la fois
- Lecteur fait apparaître une barre de progression sous l'item

---

## Ajouter une voix (`/voices/new`)

### Champs
- **Nom** (obligatoire, max 50 caractères)
- **Langue** (FR ou EN) — change automatiquement le texte de référence
- **Source** (3 choix exclusifs) :

#### Source : Enregistrer maintenant
- Affichage du texte de référence selon la langue (encadré décoratif gauche violet)
- Hint : "💡 Parlez naturellement · Durée recommandée : 10 à 15 secondes"
- Zone micro cliquable
- Animation pendant l'enregistrement
- Validation automatique en silence > 1s (auto-stop) ou clic manuel

#### Source : Importer un fichier
- Zone de drop avec drag & drop ou clic
- Formats acceptés : WAV, MP3, M4A, OGG (max 10 Mo)
- Conversion automatique en WAV 24kHz mono via ffmpeg
- Validation type MIME côté serveur

#### Source : Extraire depuis une URL
- Champ input URL + bouton "🔗 Extraire"
- yt-dlp télécharge la piste audio uniquement
- ffmpeg convertit en WAV 24kHz mono
- ffmpeg trim les 15 premières secondes de parole nette (silencedetect)
- Affichage progression pendant extraction (étapes : Téléchargement → Extraction → Conversion → Sélection)
- Une fois extrait : alerte succès + lecteur audio + hint "💡 Écoutez l'extrait avant de valider"
- Plateformes supportées : YouTube, Vimeo, Dailymotion, Twitter/X, LinkedIn et 1000+ via yt-dlp

### Boutons
- "Annuler" → retour à `/voices`
- "✅ Ajouter la voix"
  - Encode automatiquement en `.pt` via `tts.encode_reference()`
  - Sauvegarde dans `voices/<id>.wav` + `voices/encoded/<id>.pt`
  - Met à jour `voices/metadata.json`
  - Redirection vers `/voices`

### Textes de référence par langue

**Français** :
> "Ce matin, le ciel était particulièrement clair. J'ai décidé de sortir marcher un peu, histoire de prendre l'air et de réfléchir tranquillement. Quelle belle journée pour se promener ! Tu viens avec moi la prochaine fois ?"

**Anglais** :
> "I never thought a simple walk could change my whole morning. The air was fresh, the light was soft, and everything felt strangely calm. What an incredible thing nature can be ! Do you ever get that feeling where time just stops for a moment ?"

---

## Modifier une voix (`/voices/:id/edit`)

- Renommer (input texte)
- Changer la langue (avertissement si changement : re-encodage nécessaire)
- Audio de référence :
  - "Conserver l'audio actuel" (par défaut)
  - "Nouvel enregistrement"
  - "Importer un nouveau fichier"
  - "Extraire depuis une URL"
- Si nouvel audio : re-encode automatiquement le `.pt`
- Boutons "Annuler" / "✅ Enregistrer"

---

## Enregistrements

### Filtres en haut
- Tous (par défaut)
- TTS
- STT
- Live (V1 : ne contiendra rien car Live ne stocke pas)

### Affichage stats
- "X fichiers · Y Mo"

### Liste des enregistrements
Pour chaque fichier :
- Badge mode (TTS violet / STT bleu / Live vert)
- Drapeau langue + nom voix utilisée
- Date et heure de génération
- Durée audio
- Format (WAV ou MP3)
- Qualité (Normale / Haute qualité)
- Date d'expiration ("Expire dans 3h" en orange)
- Boutons :
  - ▶ Écouter (lecteur inline qui s'ouvre/ferme)
  - ⬇ Télécharger
  - 🗑 Supprimer (avec confirmation)

### Tri
- Par date décroissante (plus récent en haut)

### État vide
- "Aucun enregistrement disponible"
- "Les fichiers générés apparaîtront ici"

### Comportements
- Polling `/api/recordings` toutes les 30s pour détecter les expirations
- Suppression automatique côté serveur via cron job toutes les heures
- Les fichiers générés en mode "Session uniquement" n'apparaissent **jamais** ici (jamais écrits sur disque)

---

## Détection

### Étape 1 : Type d'analyse (3 choix exclusifs)

1. **Vérifier un audio VoiceBridge**
   - Sous-titre : "Détecte le watermark Perth · Instantané"
   - Utilise uniquement Perth

2. **Analyser un audio inconnu**
   - Sous-titre : "Analyse spectrale Deepfake-audio-detection-V2 · 2 à 5s"
   - Utilise uniquement Deepfake-audio-detection-V2

3. **Les deux** (par défaut, recommandé)
   - Sous-titre : "Watermark + analyse spectrale · Recommandé"
   - Utilise les deux

### Étape 2 : Fichier audio
- Zone drop avec drag & drop
- Formats : WAV, MP3, M4A, OGG (max 50 Mo)
- Bouton alternatif "🎤 Enregistrer maintenant"
- Bouton "🔍 Analyser"

### Étape 3 : Résultat (déverrouillée après analyse)
- Carte avec :
  - Verdict principal :
    - 🤖 "Généré par IA" (warning color) ou
    - ✅ "Non généré par IA" (success color)
  - Confiance globale en %
- Détails :
  - Watermark VoiceBridge : ✅ Présent / ❌ Absent
  - Audio altéré : ✅ Non / ⚠️ Oui (si watermark détecté mais incomplet)
  - Analyse spectrale : verdict + %
  - Fichier
  - Durée
  - Analysé le (date)
  - Mode utilisé
- Bouton "📋 Copier le rapport" (texte formaté pour le presse-papier)

### Logique de verdict combiné

| Watermark | Deepfake-audio-detection-V2 | Verdict |
|---|---|---|
| Présent | Synthétique | 🤖 Généré par IA (VoiceBridge) |
| Présent | Authentique | 🤖 Généré par IA (VoiceBridge, audio préservé) |
| Absent | Synthétique | 🤖 Généré par IA (origine inconnue) |
| Absent | Authentique | ✅ Non généré par IA |

### Format rapport copié
```
Rapport d'analyse audio - VoiceBridge
Date         : 26/04/2026 à 14:32
Fichier      : enregistrement.wav
Durée        : 12.3s
Mode         : Les deux
Watermark    : Présent
Audio altéré : Non
Analyse IA   : Synthétique (94.1%)
Verdict      : Généré par IA
```

---

## Réglages

### Sidebar 4 sections
1. Serveur
2. API
3. Sécurité
4. Installation

### Section Serveur

**État du serveur** (cards avec barres de progression)
- RAM utilisée / totale
- Stockage utilisé / total
- Modèles : Chargés / Veille / Préchauffage en cours

**Préchauffage**
- Sélecteur Langue (FR / EN)
- Sélecteur Voix
- Bouton "🚀 Préchauffer"
- Durant le préchauffage : indicateur de progression

**Rétention des fichiers audio (TTS et STT uniquement)**
- Description : "Valeur par défaut appliquée à toutes les générations. Modifiable pour chaque génération depuis le Studio."
- Choix : Session uniquement (non conservé) / 24h / 48h
- Hint : "Le mode Live ne génère jamais de fichier · Un buffer de 5s en RAM gère les micro-coupures réseau"

**Déchargement des modèles**
- "Après inactivité de" : 15 min / 30 min / 1h
- Note V1 : pas l'option 2h (réservée V2 mode intensif)

**Carte Mode Intensif (V2 grisée)**
- Badge violet "V2"
- Description : "Programmez des sessions avec préchauffage automatique et déchargement étendu. Disponible en version 2."

### Section API

**Clé API**
- Description : "Utilisée par l'application VoiceBridge pour s'authentifier au serveur."
- Affichage masqué : "sk-••••••••••••••••••••4f2a"
- Date de génération
- Bouton "🔄 Générer une nouvelle clé" (avec confirmation)
- Après génération :
  - Alerte succès
  - Affichage en clair de la nouvelle clé
  - Bouton "📋 Copier"
  - Hint orange : "⚠️ Copiez maintenant · Ne sera plus affichée · Ancienne clé révoquée"

### Section Sécurité

**Mot de passe**
- Champ "Mot de passe actuel"
- Champ "Nouveau mot de passe"
- Champ "Confirmer le nouveau mot de passe"
- Bouton "✅ Mettre à jour"
- Validation : nouveau ≠ actuel, confirmation correspond, longueur ≥ 8

### Section Installation

**Étapes guidées**

1. **Périphérique virtuel**
   - Bouton "📥 Télécharger BlackHole (Mac)" → lien externe officiel

2. **Application VoiceBridge**
   - Bouton "📥 Télécharger VoiceBridge.app" → fichier généré pendant l'installation, accessible via `/install/VoiceBridge.app`

3. **Configuration Teams**
   - Code block : `Teams → Paramètres → Périphériques → Microphone → BlackHole 2ch`

4. **Clé API**
   - "Copiez votre clé depuis Réglages → API et collez-la dans VoiceBridge → Préférences"

---

## Header (présent sur toutes les pages sauf login)

### Éléments
1. Logo "🎤 VoiceBridge"
2. Bouton thème (🌙 / ☀️) — synchronisé avec login
3. Badge statut serveur :
   - 🟢 Prêt (modèles chargés)
   - 🟡 Veille (modèles non chargés)
   - 🔵 Préchauffage en cours
   - 🔴 Erreur
   - Cliquable → ouvre un panel avec :
     - RAM utilisée / totale
     - Stockage utilisé / total
     - Modèles : Chargés / etc.
     - Latence estimée
4. Bouton "🚀 Préchauffer" → redirige vers Réglages → Serveur
5. Bouton "🧹 Nettoyer" → confirmation puis appel `/api/system/clean`

### Polling
- `/api/system/status` toutes les 5 secondes
- Mise à jour du badge statut + panel si ouvert

---

## Login

### Champs
- Mot de passe (input password avec toggle afficher/masquer)
- Bouton "Accéder →"

### Comportements
- Tentatives max : 5 par tranche de 15 minutes par IP
- Compteur "Tentatives restantes : X" affiché après le premier échec
- Au 5e échec : "Trop de tentatives, réessayez dans 15 min"
- Délai progressif entre tentatives : 0s, 0s, 2s, 5s, 10s
- Lockout IP après 10 tentatives sur 1h
- Toggle thème en haut à droite (synchronisé)

### Reset mot de passe
- Pas de "mot de passe oublié" en interface
- Reset via CLI SSH : `python /var/voicebridge/manage.py reset-password`

---

## Thème (Dark/Light)

### Comportement
- Light par défaut (premier chargement)
- Persistance via `localStorage.vb-theme`
- Toggle disponible :
  - Page login (haut droit)
  - Header de l'app (à côté du badge statut)
- Changement instantané (transition CSS 0.3s)
- Synchronisation entre login et app

### Variables CSS
Voir `voicebridge_v8.html` pour les valeurs exactes (palette violet/bleu cyan).

---

## Comportements transverses

### Étapes verrouillées/déverrouillées
- Opacité 0.3 + pointer-events: none quand verrouillées
- Transition douce à l'activation
- Marquage ✓ vert sur les étapes complétées (numéro remplacé par ✓)

### Confirmations destructives
- Suppression voix : modal "Supprimer cette voix ?"
- Suppression enregistrement : modal "Supprimer ce fichier ?"
- Génération nouvelle clé API : modal "L'ancienne clé sera révoquée immédiatement. Continuer ?"
- Nettoyage serveur : modal "Décharger les modèles et vider les fichiers audio temporaires ?"

### Notifications
- Toast en haut à droite, auto-dismiss 3s
- Couleurs : success (vert), warning (orange), error (rouge), info (violet)
- Exemples :
  - "Voix ajoutée"
  - "Clé copiée"
  - "Génération terminée"
  - "Erreur de connexion"
