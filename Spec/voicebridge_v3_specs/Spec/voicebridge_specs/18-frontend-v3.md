# 18 - Frontend V3 (extensions)

> **Document V3 nouveau.** Détail des modifications frontend pour V3.
>
> Le doc `04-frontend-specs.md` (V1) reste valable pour les pages existantes.

## Convention V1 conservée

- HTML/CSS/JS vanilla (pas de framework)
- CSS variables pour les thèmes (cf. `base.css`)
- Notification système via `/js/notify.js`
- Pas de bundler, fichiers servis tels quels

## Pages V1 modifiées

### `studio.html` (extension Live)

#### Modifications dans le tab "Live"

Section `Configuration` (step 1) modifiée :

**Suppression** : aucune (on conserve le sélecteur "Moteur TTS" pour le mode TTS fichier).

**Ajouts** :

```html
<!-- AVANT le sélecteur "Sortie audio" existant, ajouter : -->
<div class="field">
  <label>Mode Live</label>
  <div class="radio-group" data-name="live-mode">
    <button class="radio-option" data-value="cpu-fr-en">
      <strong>Authentique CPU</strong>
      <span class="hint">Ta voix · FR/EN · gratuit · 5-15s ⚠️ lent</span>
    </button>
    <button class="radio-option selected" data-value="gpu-clone">
      <strong>Multilingue ma voix</strong>
      <span class="hint">F5-TTS · 100+ langues · GPU · ~1s</span>
    </button>
    <button class="radio-option" data-value="gpu-native">
      <strong>Voix native</strong>
      <span class="hint">Voix générique · accent natif · GPU · ~1s</span>
    </button>
    <button class="radio-option" data-value="gpu-hybrid">
      <strong>Hybride accent natif</strong>
      <span class="hint">Ta voix + accent natif · F5-TTS+RVC · GPU · ~1.2s</span>
    </button>
  </div>
</div>

<!-- Sélecteur RVC visible uniquement si mode = gpu-hybrid -->
<div class="field" id="liveRvcModelField" style="display:none">
  <label for="liveRvcModelSelect">Modèle RVC</label>
  <select id="liveRvcModelSelect">
    <option value="">— Sélectionner un modèle —</option>
    <!-- Peuplé par JS depuis /api/rvc/models -->
  </select>
  <div class="hint">
    💡 Aucun modèle ? <a href="/rvc">Crée ton premier modèle RVC</a> (entraînement gratuit sur Kaggle).
  </div>
</div>

<!-- Bouton préchauffage GPU (visible si mode = gpu-*) -->
<div class="field" id="liveWarmupField" style="display:none">
  <button class="btn btn-secondary" id="btnWarmupGPU">
    🔥 Préchauffer le GPU
  </button>
  <div class="hint">
    💡 Le premier appel après inactivité prend 10-30s (cold start RunPod). Précharge maintenant pour démarrer instantanément.
  </div>
  <!-- Container pour barre de progression -->
  <div id="warmupProgress" style="display:none;margin-top:0.5rem"></div>
</div>
```

Section `Traduction Live` modifiée :

```html
<!-- Modifier liveTranslateOptions pour ajouter le sélecteur provider -->
<div id="liveTranslateOptions" style="display:none">
  <div class="grid-2">
    <div class="field">
      <label for="liveTranslateTo">Traduire vers</label>
      <select id="liveTranslateTo">
        <option value="en">🇬🇧 Anglais</option>
        <option value="de">🇩🇪 Allemand</option>
        <option value="es">🇪🇸 Espagnol</option>
        <option value="it">🇮🇹 Italien</option>
        <option value="pt">🇵🇹 Portugais</option>
        <option value="nl">🇳🇱 Néerlandais</option>
        <option value="ja">🇯🇵 Japonais</option>
        <option value="zh">🇨🇳 Chinois</option>
        <option value="fr">🇫🇷 Français</option>
      </select>
    </div>
    <div class="field">
      <label for="liveTranslateProvider">Provider de traduction</label>
      <select id="liveTranslateProvider">
        <optgroup label="Souverain (gratuit)">
          <option value="opus-mt-cpu">OPUS-MT CPU</option>
          <option value="opus-mt-gpu">OPUS-MT GPU</option>
          <option value="nllb" selected>NLLB-200 ⭐</option>
          <option value="libretranslate">LibreTranslate</option>
        </optgroup>
        <optgroup label="OpenAI (payant)">
          <option value="gpt-4o-mini">GPT-4o-mini</option>
          <option value="gpt-4o">GPT-4o</option>
        </optgroup>
      </select>
      <div class="hint" id="providerHint">
        💡 NLLB : qualité supérieure et 200+ langues, gratuit après infrastructure GPU.
      </div>
    </div>
  </div>
</div>

<!-- Indicateur de coût estimé en cours de session (visible si gpu-* ou GPT) -->
<div id="liveCostIndicator" style="display:none;margin-top:0.5rem;padding:0.5rem;background:var(--surface3);border-radius:6px;font-size:0.75rem">
  <span style="color:var(--text3)">Coût session :</span>
  <span style="font-family:'DM Mono',monospace" id="liveCostValue">0.00€</span>
  <span style="color:var(--text3)">·</span>
  <span style="font-family:'DM Mono',monospace" id="liveDurationValue">00:00</span>
</div>
```

#### Modifications JS dans `studio-live.js`

```javascript
// Ajouts au début de studio-live.js (état global)
const liveState = {
  mode: 'gpu-clone',                  // V3
  translationProvider: 'nllb',        // V3
  rvcModelId: null,                   // V3
  rvcModelsAvailable: [],             // V3
  isGpuWarmedUp: false,               // V3
  sessionStartedAt: null,             // V3 pour calcul coût
};

// Au chargement, fetch les modèles RVC
async function loadRvcModels() {
  try {
    const r = await fetch('/api/rvc/models');
    const data = await r.json();
    liveState.rvcModelsAvailable = data.models;
    
    const select = document.getElementById('liveRvcModelSelect');
    select.innerHTML = '<option value="">— Sélectionner un modèle —</option>';
    for (const m of data.models) {
      const opt = document.createElement('option');
      opt.value = m.id;
      opt.textContent = `${m.name} (${m.size_mb}Mo)`;
      select.appendChild(opt);
    }
  } catch (e) {
    console.error('Failed to load RVC models', e);
  }
}

// Listener sur changement de mode
function onModeChange(newMode) {
  liveState.mode = newMode;
  
  // RVC field visible uniquement si gpu-hybrid
  document.getElementById('liveRvcModelField').style.display = 
    newMode === 'gpu-hybrid' ? '' : 'none';
  
  // Warmup field visible si gpu-*
  document.getElementById('liveWarmupField').style.display = 
    newMode.startsWith('gpu-') ? '' : 'none';
  
  // Cost indicator si gpu-* ou provider OpenAI
  updateCostIndicatorVisibility();
  
  // Update latence cible affichée
  updateLatencyHint(newMode);
}

function updateLatencyHint(mode) {
  const hints = {
    'cpu-fr-en': '⏱ Latence : 5-15s (CPU, lent)',
    'gpu-clone': '⏱ Latence : ~1s',
    'gpu-native': '⏱ Latence : ~1s',
    'gpu-hybrid': '⏱ Latence : ~1.2s',
  };
  // Afficher dans un span dédié dans le HTML
  document.getElementById('liveLatencyHint').textContent = hints[mode] || '';
}

// Préchauffage GPU
async function warmupGPU() {
  const btn = document.getElementById('btnWarmupGPU');
  btn.disabled = true;
  btn.innerHTML = '⏳ Préchauffage en cours...';
  
  try {
    const r = await fetch('/api/cloud/runpod/warmup', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({components: ['whisper', 'f5tts', 'nllb']}),
    });
    const {task_id} = await r.json();
    
    const container = document.getElementById('warmupProgress');
    container.style.display = '';
    const ui = new ProgressBarUI(container, {cancelable: false});
    
    const sub = new ProgressSubscriber(task_id, {
      onUpdate: (data) => ui.update(data),
      onDone: () => {
        ui.setDone();
        liveState.isGpuWarmedUp = true;
        btn.innerHTML = '✅ GPU préchauffé';
        setTimeout(() => {
          container.style.display = 'none';
          btn.disabled = false;
          btn.innerHTML = '🔥 Préchauffer le GPU';
        }, 2000);
      },
      onError: (e) => {
        ui.setError(e);
        btn.disabled = false;
        btn.innerHTML = '🔥 Préchauffer le GPU';
        notify.error('Préchauffage échoué : ' + e);
      },
    });
    sub.subscribe();
  } catch (e) {
    btn.disabled = false;
    btn.innerHTML = '🔥 Préchauffer le GPU';
    notify.error('Erreur : ' + e.message);
  }
}

// Modification de la fonction de connexion WebSocket existante
async function connectLive() {
  // ... code V1 existant ...
  
  // Modifier le payload configure
  const configurePayload = {
    type: 'configure',
    voice_id: liveState.voiceId,
    language: liveState.language,
    output: liveState.output,
    
    // V3 ajouts
    mode: liveState.mode,
    translation_provider: liveState.translationProvider,
  };
  
  if (liveState.mode === 'gpu-hybrid') {
    if (!liveState.rvcModelId) {
      notify.error('Sélectionne un modèle RVC pour le mode hybride');
      return;
    }
    configurePayload.rvc_model_id = liveState.rvcModelId;
  }
  
  if (document.getElementById('liveTranslateToggle').checked) {
    configurePayload.translate = true;
    configurePayload.translate_to = document.getElementById('liveTranslateTo').value;
  }
  
  ws.send(JSON.stringify(configurePayload));
}

// Mise à jour du compteur de coût
function updateCostIndicator(serverCostUpdate) {
  if (!liveState.sessionStartedAt) return;
  
  const elapsed = Math.floor((Date.now() - liveState.sessionStartedAt) / 1000);
  const min = Math.floor(elapsed / 60);
  const sec = elapsed % 60;
  
  document.getElementById('liveDurationValue').textContent = 
    `${min.toString().padStart(2, '0')}:${sec.toString().padStart(2, '0')}`;
  
  if (serverCostUpdate) {
    document.getElementById('liveCostValue').textContent = 
      `${serverCostUpdate.session_cost_eur.toFixed(3)}€`;
  }
}

// Listener sur les messages WS
function handleWSMessage(msg) {
  // ... handlers V1 existants ...
  
  // V3 ajouts
  if (msg.type === 'cost_update') {
    updateCostIndicator(msg);
  } else if (msg.type === 'warmup_progress') {
    // Affichage progression cold start
    notify.info(`${msg.step} (${msg.progress_percent}%)`);
  }
}

// Init
document.addEventListener('DOMContentLoaded', () => {
  // ... init V1 existant ...
  
  loadRvcModels();
  
  document.querySelectorAll('[data-name="live-mode"] .radio-option').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('[data-name="live-mode"] .radio-option').forEach(b => b.classList.remove('selected'));
      btn.classList.add('selected');
      onModeChange(btn.dataset.value);
    });
  });
  
  document.getElementById('liveRvcModelSelect').addEventListener('change', (e) => {
    liveState.rvcModelId = e.target.value;
  });
  
  document.getElementById('liveTranslateProvider').addEventListener('change', (e) => {
    liveState.translationProvider = e.target.value;
  });
  
  document.getElementById('btnWarmupGPU').addEventListener('click', warmupGPU);
});
```

### `settings.html` (panneaux étendus)

#### Nouveau panel "Cloud"

```html
<!-- Ajouter dans settings.html, après le panel "Sécurité" -->
<button class="settings-nav-item" data-panel="cloud">Cloud</button>

<!-- Et le panel correspondant -->
<div class="settings-panel" data-panel="cloud">
  
  <div class="card">
    <div class="card-title">RunPod Serverless</div>
    <div class="hint">
      Pour activer le mode Live multilingue (F5-TTS, NLLB, RVC), tu dois configurer un compte RunPod. 
      <a href="https://runpod.io" target="_blank">Créer un compte (5$ offerts)</a>.
    </div>
    
    <div class="field">
      <label for="runpodApiKey">Clé API RunPod</label>
      <input type="password" id="runpodApiKey" placeholder="rpa_..." autocomplete="off">
    </div>
    <div class="field">
      <label for="runpodEndpointId">Endpoint ID</label>
      <input type="text" id="runpodEndpointId" placeholder="abc123def">
    </div>
    <div class="field">
      <label for="runpodVolumeId">Network Volume ID (EU-FR-1)</label>
      <input type="text" id="runpodVolumeId" placeholder="vol_xyz789">
    </div>
    
    <button class="btn btn-primary" id="btnSaveRunpod">💾 Enregistrer et tester</button>
    <div id="runpodStatus" style="margin-top:0.5rem"></div>
    <div id="runpodTestProgress" style="margin-top:0.5rem;display:none"></div>
  </div>
  
  <div class="card">
    <div class="card-title">OpenAI API (optionnel)</div>
    <div class="hint">
      Pour la traduction GPT-4o et GPT-4o-mini. Coûts variables.
    </div>
    
    <div class="field">
      <label for="openaiApiKey">Clé API OpenAI</label>
      <input type="password" id="openaiApiKey" placeholder="sk-..." autocomplete="off">
    </div>
    
    <button class="btn btn-primary" id="btnSaveOpenai">💾 Enregistrer et tester</button>
    <div id="openaiStatus" style="margin-top:0.5rem"></div>
  </div>
  
  <div class="card">
    <div class="card-title">Statut global cloud</div>
    <div id="cloudStatusInfo">
      <div class="status-row">
        <span>RunPod</span>
        <span class="status-pill" id="runpodPill">⚪ Non configuré</span>
      </div>
      <div class="status-row">
        <span>OpenAI</span>
        <span class="status-pill" id="openaiPill">⚪ Non configuré</span>
      </div>
      <div class="status-row">
        <span>LibreTranslate</span>
        <span class="status-pill" id="libreTranslatePill">⚪ Non configuré</span>
      </div>
    </div>
  </div>
  
  <div class="card">
    <div class="card-title">Usage du mois en cours</div>
    <div id="usageBreakdown">
      <div class="usage-row">
        <span>Sessions live</span>
        <span style="font-family:'DM Mono',monospace" id="usageLiveSessions">0</span>
      </div>
      <div class="usage-row">
        <span>GPU RunPod</span>
        <span style="font-family:'DM Mono',monospace" id="usageRunpod">0.00€</span>
      </div>
      <div class="usage-row">
        <span>OpenAI</span>
        <span style="font-family:'DM Mono',monospace" id="usageOpenai">0.00€</span>
      </div>
      <div class="usage-row">
        <span>Volume RunPod (fixe)</span>
        <span style="font-family:'DM Mono',monospace">3.50€</span>
      </div>
      <hr>
      <div class="usage-row" style="font-weight:600">
        <span>Total estimé</span>
        <span style="font-family:'DM Mono',monospace" id="usageTotal">3.50€</span>
      </div>
    </div>
  </div>
  
</div>
```

#### Nouveau panel "Traduction"

```html
<button class="settings-nav-item" data-panel="translation">Traduction</button>

<div class="settings-panel" data-panel="translation">
  
  <div class="card">
    <div class="card-title">Provider par défaut</div>
    <div class="hint">
      Provider utilisé par défaut quand aucun n'est explicitement sélectionné en session.
    </div>
    <select id="defaultTranslationProvider" class="select-block">
      <option value="opus-mt-cpu">OPUS-MT CPU (gratuit, FR↔EN)</option>
      <option value="opus-mt-gpu">OPUS-MT GPU (gratuit, multi-paires)</option>
      <option value="nllb" selected>NLLB-200 (gratuit, 200+ langues)</option>
      <option value="gpt-4o-mini">GPT-4o-mini (payant)</option>
      <option value="gpt-4o">GPT-4o (payant)</option>
      <option value="libretranslate">LibreTranslate (fallback)</option>
    </select>
    <button class="btn btn-primary" id="btnSaveDefaultTrad">💾 Enregistrer</button>
  </div>
  
  <div class="card">
    <div class="card-title">Glossaire métier (GPT uniquement)</div>
    <div class="hint">
      Termes à traduire de manière précise. Format : un terme par ligne, "FR | EN".
    </div>
    <textarea id="glossaryEditor" rows="8" placeholder="CODIR | Executive Committee
DSI | IT Department  
NIS2 | NIS2 (untranslated)
PSSI | Information System Security Policy"></textarea>
    <button class="btn btn-primary" id="btnSaveGlossary">💾 Enregistrer</button>
  </div>
  
  <div class="card">
    <div class="card-title">Test de traduction</div>
    <div class="grid-2">
      <div class="field">
        <label>Texte source</label>
        <textarea id="testTranslateSrc" rows="3"
          placeholder="Le projet a démarré la semaine dernière."></textarea>
      </div>
      <div class="field">
        <label>De → Vers</label>
        <select id="testTranslateLangs">
          <option value="fr-en">FR → EN</option>
          <option value="fr-de">FR → DE</option>
          <option value="fr-es">FR → ES</option>
          <option value="fr-it">FR → IT</option>
          <option value="fr-ja">FR → JA</option>
          <option value="en-fr">EN → FR</option>
        </select>
      </div>
    </div>
    <button class="btn btn-secondary" id="btnTestAllProviders">🧪 Tester tous les providers</button>
    <div id="testResults" style="display:none;margin-top:0.5rem"></div>
  </div>
  
</div>
```

#### Nouveau panel "RVC"

```html
<button class="settings-nav-item" data-panel="rvc-settings">Modèles RVC</button>

<div class="settings-panel" data-panel="rvc-settings">
  
  <div class="card">
    <div class="card-title">Tes modèles RVC</div>
    <div class="rvc-summary" id="rvcSummary">
      <div class="metric-big">
        <span id="rvcCount">0</span>
        <span class="metric-label">modèle(s)</span>
      </div>
      <div class="metric-big">
        <span id="rvcStorage">0</span>
        <span class="metric-label">Mo utilisés</span>
      </div>
    </div>
    <div style="display:flex;gap:0.5rem;margin-top:1rem">
      <a href="/rvc" class="btn btn-primary">📋 Gérer mes modèles</a>
      <a href="/recording-session" class="btn btn-secondary">+ Nouveau modèle</a>
    </div>
  </div>
  
  <div class="card">
    <div class="card-title">Guide RVC PDF</div>
    <div class="hint">
      Guide complet 12 pages : préparation matériel, 5 blocs textes calibrés, tutoriel Kaggle, FAQ.
    </div>
    <a href="/api/rvc/guide.pdf" download class="btn btn-secondary">
      📥 Télécharger le guide PDF
    </a>
  </div>
  
</div>
```

## Pages V3 nouvelles

### `rvc.html` - Liste des modèles RVC

```html
<!DOCTYPE html>
<html lang="fr" data-theme="light">
<head>
  <meta charset="UTF-8">
  <title>VoiceBridge — Modèles RVC</title>
  <link rel="stylesheet" href="/css/base.css">
  <link rel="stylesheet" href="/css/app.css">
  <link rel="stylesheet" href="/css/rvc.css">
</head>
<body>

<header><!-- header standard --></header>

<main>
  <h1 class="page-title">Modèles RVC</h1>
  <p class="page-sub">Voice Conversion : applique ta voix sur n'importe quel audio source</p>
  
  <div class="rvc-tabs" role="tablist">
    <button class="rvc-tab active" data-tab="my-models">Mes modèles</button>
    <button class="rvc-tab" data-tab="tutorial">Tutoriel Kaggle</button>
  </div>
  
  <div class="rvc-content active" data-tab="my-models">
    <div class="actions">
      <a href="/recording-session" class="btn btn-primary">+ Préparer un nouvel enregistrement</a>
      <a href="/rvc/import" class="btn btn-secondary">📥 Importer un modèle existant</a>
    </div>
    
    <div class="rvc-models-grid" id="rvcModelsGrid">
      <!-- Cards générées par JS -->
    </div>
    
    <div class="rvc-empty" id="rvcEmpty" style="display:none">
      <h2>Aucun modèle RVC</h2>
      <p>Pour démarrer, prépare un enregistrement (~25 min) et entraîne ton premier modèle gratuitement sur Kaggle.</p>
      <a href="/recording-session" class="btn btn-primary">+ Démarrer maintenant</a>
    </div>
  </div>
  
  <div class="rvc-content" data-tab="tutorial">
    <div id="kaggleTutorial">
      <!-- Markdown rendered : tutoriel intégré -->
    </div>
    <a href="/api/rvc/guide.pdf" download class="btn btn-secondary">
      📥 Télécharger le guide PDF complet
    </a>
  </div>
</main>

<script src="/js/theme.js"></script>
<script src="/js/api.js"></script>
<script src="/js/notify.js"></script>
<script src="/js/header.js"></script>
<script src="/js/rvc.js"></script>
</body>
</html>
```

### `rvc-import.html` - Wizard upload

```html
<main>
  <div class="breadcrumb">
    <a href="/rvc">Modèles RVC</a> › <span>Importer</span>
  </div>
  
  <h1 class="page-title">Importer un modèle RVC</h1>
  
  <div class="wizard-progress">
    <div class="wizard-step active" data-step="files">Fichiers</div>
    <div class="wizard-step" data-step="metadata">Métadonnées</div>
    <div class="wizard-step" data-step="upload">Upload</div>
    <div class="wizard-step" data-step="test">Test</div>
    <div class="wizard-step" data-step="done">Terminé</div>
  </div>
  
  <!-- Étape 1 : Drop zones -->
  <section class="step active" data-step="files">
    <div class="card">
      <h3>Fichiers du modèle</h3>
      
      <div class="drop-zone" data-target="pth">
        <div class="drop-zone-icon">📁</div>
        <div class="drop-zone-label">Glissez votre <strong>model.pth</strong> ici</div>
        <input type="file" accept=".pth" id="pthFile" hidden>
      </div>
      
      <div class="drop-zone" data-target="index">
        <div class="drop-zone-icon">📁</div>
        <div class="drop-zone-label">Glissez votre <strong>added_*.index</strong> (optionnel)</div>
        <input type="file" accept=".index" id="indexFile" hidden>
      </div>
      
      <button class="btn btn-primary" id="btnFilesContinue" disabled>Continuer</button>
    </div>
  </section>
  
  <!-- Étape 2 : Métadonnées -->
  <section class="step locked" data-step="metadata">
    <div class="card">
      <h3>Métadonnées</h3>
      <div class="field">
        <label for="modelName">Nom du modèle</label>
        <input type="text" id="modelName" placeholder="ex : JC voice v1" maxlength="50">
      </div>
      <div class="field">
        <label for="modelDescription">Description (optionnel)</label>
        <textarea id="modelDescription" rows="3" maxlength="500"></textarea>
      </div>
      <div class="field">
        <label for="modelVoiceLink">Voix associée (optionnel)</label>
        <select id="modelVoiceLink">
          <option value="">— Aucune liaison —</option>
          <!-- Peuplé par JS depuis /api/voices -->
        </select>
      </div>
      <div class="field">
        <label for="modelSampleRate">Sample rate</label>
        <select id="modelSampleRate">
          <option value="40000" selected>40 000 Hz (défaut)</option>
          <option value="32000">32 000 Hz</option>
          <option value="48000">48 000 Hz</option>
        </select>
      </div>
      <button class="btn btn-primary" id="btnMetadataContinue">Continuer</button>
    </div>
  </section>
  
  <!-- Étape 3 : Upload (avec barre de progression) -->
  <section class="step locked" data-step="upload">
    <div class="card">
      <h3>Upload en cours</h3>
      <div id="uploadProgress"></div>
    </div>
  </section>
  
  <!-- Étape 4 : Test rapide -->
  <section class="step locked" data-step="test">
    <div class="card">
      <h3>Test rapide</h3>
      <div class="hint">Voici comment tu sonneras avec ce modèle :</div>
      <div id="testProgress"></div>
      <audio id="testAudio" controls style="width:100%;display:none"></audio>
    </div>
  </section>
  
  <!-- Étape 5 : Terminé -->
  <section class="step locked" data-step="done">
    <div class="card">
      <h3>✅ Modèle importé avec succès</h3>
      <p>Ton modèle est prêt à être utilisé en mode Live "Hybride accent natif".</p>
      <a href="/rvc" class="btn btn-primary">Retour à mes modèles</a>
      <a href="/studio" class="btn btn-secondary">Tester en Live maintenant</a>
    </div>
  </section>
</main>
```

### `recording-session.html`

Voir détail dans `Spec/voicebridge_specs/14-rvc-recording-guide.md`.

### `recording-session-validate.html`

Page de validation après retraitement.

```html
<main>
  <h1 class="page-title">Validation du dataset</h1>
  
  <div class="quality-card">
    <div class="quality-score-big" id="qualityScore">87 / 100 ✅</div>
    <div class="quality-details" id="qualityDetails">
      <!-- Listes d'indicateurs -->
    </div>
  </div>
  
  <div class="filter-bar">
    <input type="text" id="clipFilter" placeholder="🔍 Filtrer les clips...">
    <select id="clipSort">
      <option value="filename">Trier par nom</option>
      <option value="duration">Trier par durée</option>
      <option value="snr">Trier par SNR (qualité)</option>
    </select>
    <button class="btn btn-secondary" id="btnDeleteProblematic">
      🗑 Supprimer les clips problématiques (SNR &lt; 20dB)
    </button>
  </div>
  
  <div class="clips-list" id="clipsList">
    <!-- Cards générées JS -->
  </div>
  
  <div class="actions-bottom">
    <a href="/rvc" class="btn btn-secondary">← Retour</a>
    <button class="btn btn-primary" id="btnDownloadZip">
      📥 Télécharger le dataset (ZIP)
    </button>
    <a href="/rvc?tab=tutorial" class="btn btn-primary">
      Suivre le tutoriel Kaggle →
    </a>
  </div>
</main>
```

## Nouveaux fichiers JS

| Fichier | Rôle |
|---|---|
| `Site/frontend/js/recording-session.js` | Wizard d'enregistrement (cf. doc 14) |
| `Site/frontend/js/recording-session-content.js` | Contenu canonique des 5 blocs |
| `Site/frontend/js/recording-session-validate.js` | Page de validation des clips |
| `Site/frontend/js/rvc.js` | Page liste des modèles |
| `Site/frontend/js/rvc-import.js` | Wizard import .pth |
| `Site/frontend/js/progress.js` | Helper ProgressSubscriber (cf. doc 16) |
| `Site/frontend/js/progress-ui.js` | Composant ProgressBarUI (cf. doc 16) |
| `Site/frontend/js/translation-test.js` | Test de traduction multi-providers (settings) |

## Nouveaux fichiers CSS

| Fichier | Rôle |
|---|---|
| `Site/frontend/css/recording-session.css` | Styles wizard enregistrement |
| `Site/frontend/css/rvc.css` | Styles pages RVC |
| `Site/frontend/css/progress.css` | Styles barres de progression standard |

## Modifications de la nav

Ajouter "Modèles RVC" dans la navbar de toutes les pages :

```html
<nav class="app-nav">
  <a href="/studio"     class="nav-item" data-nav="/studio">Studio</a>
  <a href="/voices"     class="nav-item" data-nav="/voices">Mes voix</a>
  <a href="/rvc"        class="nav-item" data-nav="/rvc">Modèles RVC</a>  <!-- NOUVEAU -->
  <a href="/recordings" class="nav-item" data-nav="/recordings">Enregistrements</a>
  <a href="/detection"  class="nav-item" data-nav="/detection">Détection</a>
  <a href="/settings"   class="nav-item" data-nav="/settings">Réglages</a>
</nav>
```

## Variables CSS à ajouter

```css
/* base.css : extensions V3 */
:root {
  /* Existant V1 */
  --accent: #A8243C;
  --accent2: #6B7280;  /* À définir si pas existant */
  
  /* NOUVEAU V3 - couleurs spécifiques */
  --warning: #F59E0B;
  --success: #16A34A;
  --error: #DC2626;
  --gpu-color: #8B5CF6;     /* Pour les éléments mode GPU */
  --rvc-color: #EC4899;     /* Pour les éléments RVC */
}
```

## Tests manuels à valider

| Scénario | Expected |
|---|---|
| Mode `cpu-fr-en` (défaut V1) | UI inchangée, fonctionne comme V1 |
| Mode `gpu-clone` sans RunPod configuré | Erreur claire "Configurez RunPod dans Réglages" |
| Mode `gpu-hybrid` sans modèle RVC | UI affiche message "Pas de modèle, créer un" |
| Bouton préchauffage GPU | Barre progression pendant cold start, ✅ à la fin |
| Switch provider trad en cours session | Prochain chunk utilise le nouveau provider |
| Compteur de coût en session | Mis à jour en temps réel |
| Wizard recording session complet | 5 blocs → traitement → validation → ZIP download |
| Upload .pth (300 Mo) | Barre progression XHR + push RunPod |
| Test rapide d'un modèle RVC | Audio audible dans le navigateur |
| Drop dans les drop zones | Validation magic bytes côté backend |
