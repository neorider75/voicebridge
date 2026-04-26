# 04 - Spécifications frontend

## Référence visuelle

**Le fichier `voicebridge_v8.html` est la cible UX/UI à respecter à la lettre.**

Cette spec liste les éléments à reproduire et clarifie les comportements interactifs.

## Stack frontend

- HTML5 + CSS vanilla + JavaScript vanilla (pas de framework)
- Single Page Application avec routing JS simple
- Fonts via Google Fonts CDN : **Syne** (display) + **DM Mono** (monospace)
- Pas de bundler, pas de build step, livré en 3 fichiers max :
  - `index.html` (structure)
  - `app.css` (styles)
  - `app.js` (logique)

## Palette de couleurs

### Mode clair (par défaut)
```css
--bg: #f4f4f9;
--surface: #ffffff;
--surface2: #f0f0f6;
--surface3: #e8e8f0;
--border: #dddde8;
--border2: #c8c8d8;
--accent: #5b52ee;
--accent2: #7c6ee8;
--accent3: #0ea5e9;
--success: #10b981;
--warning: #d97706;
--danger: #ef4444;
--text: #1a1a2e;
--text2: #4a4a6a;
--text3: #8a8aaa;
--glow: rgba(91,82,238,0.08);
--shadow: rgba(0,0,0,0.1);
--header-bg: rgba(244,244,249,0.92);
```

### Mode sombre
```css
--bg: #0a0a0f;
--surface: #111118;
--surface2: #1a1a24;
--surface3: #22222f;
--border: #2a2a3a;
--border2: #333345;
--accent: #6c63ff;
--accent2: #a78bfa;
--accent3: #38bdf8;
--success: #34d399;
--warning: #fbbf24;
--danger: #f87171;
--text: #f0f0f8;
--text2: #9898b8;
--text3: #5a5a7a;
--glow: rgba(108,99,255,0.15);
--shadow: rgba(0,0,0,0.5);
--header-bg: rgba(10,10,15,0.88);
```

## Typographie

- **Syne** : titres, boutons, labels, navigation
- **DM Mono** : valeurs techniques, métadonnées, breadcrumbs, codes
- Tailles : voir maquette pour les valeurs exactes (typiquement 0.65rem à 1.5rem)

## Composants réutilisables

### Boutons
- `.btn.btn-primary` : gradient violet/bleu, shadow violet
- `.btn.btn-secondary` : surface2 + border subtil
- `.btn.btn-danger` : rouge transparent
- `.btn.btn-sm` : version compacte

### Inputs
- Borders rounded 8px
- Focus : border accent + box-shadow violet
- Padding cohérent

### Étapes (steps)
- Numéro circulaire (22px)
- Titre uppercase letter-spacing 0.08em
- État `.locked` : opacity 0.3 + pointer-events: none
- État `.done` : numéro remplacé par ✓ vert

### Cards
- Background surface
- Border 1px border
- Border-radius 12px
- Padding 1.25rem

### Mic zones
- Border avec radial gradient au hover
- Animation waves pendant l'enregistrement
- 5 barres animées avec delays décalés

### Players audio
- Bouton play 30px circulaire avec gradient
- Track 3px de hauteur
- Fill avec gradient violet/cyan

### Radio groups
- Boutons rectangulaires
- État selected : background violet transparent + border accent

### Alerts
- 3 variantes : warn (orange), success (vert), info (violet)
- Padding 0.7rem, border 1px, border-radius 8px

### Badges V2/V3
- Position absolute top-right
- Background accent (violet plein)
- Texte blanc
- Font-size 0.62rem
- Padding 0.15rem 0.45rem
- Border-radius 20px
- Letter-spacing 0.04em

## Routing JavaScript

```javascript
// Routes principales
'/' → page Studio (par défaut, onglet TTS actif)
'/voices' → page Mes voix
'/voices/new' → page Ajouter une voix
'/voices/:id/edit' → page Modifier une voix
'/recordings' → page Enregistrements
'/detection' → page Détection
'/settings' → page Réglages (sous-section Serveur par défaut)
'/login' → page Login (si non authentifié)
```

Implémentation simple via History API + show/hide divs `.page`.

## Pages détaillées

### Page Login
- Plein écran, centrée
- Box 370px max
- Logo + titre + sous-titre
- Champ password + bouton Accéder
- Toggle thème en haut à droite
- Compteur tentatives en cas d'échec

### Page Studio (page d'accueil)
- Tabs (TTS / STT / Live)
- Switch entre tabs sans rechargement
- Chaque tab a ses étapes propres
- Persistance temporaire de l'état (texte saisi, voix sélectionnée) lors du switch entre tabs

### Page Mes voix
- Header avec titre + bouton "+ Ajouter une voix"
- Liste avec items voix
- Lecteur inline qui s'ouvre en cliquant sur ▶
- Une seule voix peut être en lecture à la fois

### Page Ajouter/Modifier voix
- Breadcrumb cliquable
- Form avec champs nom + langue + source
- 3 modes source exclusifs
- Mise à jour automatique du texte de référence selon la langue

### Page Enregistrements
- Filtres en haut (badges cliquables)
- Stats à droite ("X fichiers · Y Mo")
- Liste avec items
- Lecteur inline expand/collapse
- Indicateur d'expiration en orange

### Page Détection
- 3 étapes verticales (mode → fichier → résultat)
- Drop zone avec hover effects
- Card de résultat avec verdict + détails
- Bouton copier rapport

### Page Réglages
- Sidebar gauche 160px avec items cliquables
- Panel droit avec contenu de la section active
- 4 sections : Serveur, API, Sécurité, Installation

## Header

### Structure
```html
<header>
  <div class="header-top">
    [Logo] [Theme toggle] [Status badge] [Préchauffer btn] [Nettoyer btn]
  </div>
  <nav>
    [Studio] [Mes voix] [Enregistrements] [Détection] [Réglages]
  </nav>
</header>
```

### Status badge cliquable
Au clic, affiche un panel inline (pas de classe CSS, style direct via JS) :
```
État du serveur
RAM        4.2 / 16 Go
Stockage   2.1 / 200 Go
Modèles    Chargés
Latence    ~0.8s
```

### Theme toggle
- 🌙 si mode clair actif (pour passer en sombre)
- ☀️ si mode sombre actif (pour passer en clair)
- Synchronisé entre login et header

## Animations

### Page load
- Fade-in douce des sections (animation-delay décalé)

### Mic recording
- Waves bars animation infinie 0.8s
- Delays décalés (0s, 0.1s, 0.2s, 0.3s, 0.4s)

### Status dot
- Pulse 2s infinite
- Box-shadow glow

### Hover states
- Borders changent de couleur
- Légère élévation sur boutons primary (translateY -1px)

### Step transitions
- Opacity 0.3s pour locked/unlocked

## Responsive

**V1 : desktop uniquement**
- Largeur min : 900px
- Pas de mobile/tablette en V1
- Mais ne pas bloquer le rendu sur petits écrans (scroll horizontal acceptable)

## Accessibilité

- Contraste WCAG AA minimum sur tous les textes
- Focus visible sur tous les éléments interactifs
- Labels explicites sur les inputs
- aria-label sur les boutons icônes
- Keyboard navigation fonctionnelle (Tab, Enter, Escape)

## États d'erreur

### Network errors
- Toast en haut à droite
- Message clair en français
- Bouton "Réessayer" si applicable

### Form validation
- Border rouge sur champ invalide
- Message sous le champ en rouge
- Pas d'envoi tant qu'invalide

### États de chargement
- Skeleton loaders ou spinner
- Désactivation du bouton qui a déclenché
- Message "Chargement..." si > 1s

## WebSocket

### Connexion
- Au login, ouvrir WebSocket persistant `/ws/stream`
- Reconnexion automatique en cas de coupure (backoff exponentiel)
- Heartbeat toutes les 30s

### Messages

**Reçus du serveur** :
```json
{ "type": "state_update", "active_voice": "jc_fr" }
{ "type": "audio_chunk", "data": "base64..." }
{ "type": "transcription", "text": "..." }
{ "type": "voicebridge_connected", "value": true }
```

**Envoyés au serveur** :
```json
{ "type": "audio_chunk", "data": "base64...", "mode": "live" }
{ "type": "set_voice", "voice_id": "jc_fr" }
```

## Polling REST

- `/api/system/status` toutes les 5s
- `/api/recordings` toutes les 30s (uniquement sur la page Enregistrements)
- `/api/voices` au chargement de la page Mes voix uniquement (sinon mise à jour via WebSocket events)

## Persistance localStorage

- `vb-theme` : 'light' ou 'dark'
- `vb-active-tab-studio` : 'tts' / 'stt' / 'live' (mémoire dernier onglet)
- Pas de tokens, pas de mots de passe, pas de données sensibles

## Internationalisation

- Interface en français uniquement en V1
- Pas de système i18n (textes en dur)
- Préparer un fichier `strings.js` centralisé pour faciliter une éventuelle traduction future
