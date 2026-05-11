// VoiceBridge — studio-live.js : Live WebSocket avec AudioWorklet PCM raw.
//
// Pipeline :
//   getUserMedia → AudioContext → MediaStreamSource → AudioWorkletNode (pcm-capture)
//     → port.onmessage (Int16Array PCM 16 kHz mono, chunks 100 ms)
//     → ws.send(buffer) (binary frame)
//
// Réception : audio_chunk JSON base64 (WAV 24 kHz mono) → Blob → AudioElement.play().
//
// Latence cible : 0,6 – 1,4 s (cf. spec). On évite la latence d'1 s du
// timeslice MediaRecorder + ffmpeg subprocess côté serveur.

(function () {
  function $(id) { return document.getElementById(id); }
  function $$(sel, r) { return Array.prototype.slice.call((r || document).querySelectorAll(sel)); }

  var ws = null;
  var audioCtx = null;
  var workletNode = null;
  var sourceNode = null;
  var stream = null;
  var sessionActive = false;
  // Lecture streaming : on schedule chaque chunk PCM 24 kHz directement sur
  // l'AudioContext (au lieu de file d'attente Blob WAV qui ajoute du delay).
  var playbackCtx = null;
  var nextPlayAt = 0;
  var TTS_RATE = 24000;

  // Jitter buffer : on n'enchaîne pas chaque chunk au fil de l'eau (qui mène
  // à des gaps si NeuTTS Q4 génère plus lentement que le temps réel sur CPU
  // modeste). À la place, on accumule jusqu'à JITTER_BUFFER_MS de signal
  // *avant* de commencer à jouer. Pendant la lecture, les chunks suivants
  // sont scheduled bout-en-bout depuis nextPlayAt → zéro gap tant qu'ils
  // arrivent avant la fin du buffer accumulé.
  //
  // Compromis : augmenter ce buffer = plus de latence perçue mais moins de
  // hachage. Sur un CPU plus lent que real-time pour NeuTTS Q4, il faut
  // beaucoup de buffer pour ne pas underrun :
  //   500 ms : OK courtes phrases, hache sur les longues
  //   1000 ms : OK la plupart du temps, latence visible
  //   1500 ms : très tolérant, latence forte
  // À ajuster selon hardware. Sur un GPU on pourrait redescendre à 100-200 ms.
  // Compromis : augmenter ce buffer = plus de latence perçue mais moins de
  // hachage. Sur un CPU plus lent que real-time pour NeuTTS Q4, il faut
  // beaucoup de buffer pour ne pas underrun. En mode GPU les chunks arrivent
  // en rafale après synthèse → on peut être plus tolérant côté buffer.
  //   500 ms : OK courtes phrases, hache sur les longues
  //   1000 ms : OK la plupart du temps, latence visible
  //   1500 ms : très tolérant, latence forte mais zéro hachage
  // (override via localStorage.vbJitterMs)
  var JITTER_BUFFER_MS = parseInt(
    localStorage.getItem('vbJitterMs') || '1500', 10);

  // Marge de sécurité quand on schedule un chunk : si nextPlayAt <=
  // currentTime + SAFETY, on reset à currentTime + SAFETY pour éviter de
  // démarrer dans le passé. Doit être > la jitter du main thread browser
  // (typique 30-80ms quand UI charge).
  // (override via localStorage.vbScheduleSafetyMs)
  var SCHEDULE_SAFETY_MS = parseInt(
    localStorage.getItem('vbScheduleSafetyMs') || '100', 10);

  // Mode "wait_full" : attendre audio_end avant de démarrer la lecture →
  // zéro coupure garantie mais latence +durée_phrase. Activable via
  // localStorage.vbWaitFull = "1"
  var WAIT_FULL_PHRASE = (localStorage.getItem('vbWaitFull') === '1');

  // Fade in/out aux frontières de chunks (ms) pour masquer les clicks
  // de jonction si le buffer n'est pas exactement aligné en sample.
  // Default 0 = désactivé (les clicks 24 kHz speech sont peu audibles
  // et le fade introduit un risque de coupure sur les chunks courts).
  // Activable via localStorage.vbChunkFadeMs = "3".
  var CHUNK_FADE_MS = parseInt(
    localStorage.getItem('vbChunkFadeMs') || '0', 10);

  var warmupPending = false;     // true pendant le chargement du modèle de traduction
  var gpuWarmupPending = false;  // true pendant /api/cloud/runpod/warmup
  var cloudStatus = {            // alimenté par /api/cloud/status
    runpod_configured: false,
    openai_configured: false,
  };
  var currentMode = 'cpu-fr-en';
  var pendingChunks = [];          // AudioBuffers en attente de scheduling
  var pendingDurationMs = 0;       // total des durées accumulées
  var hasStartedUtterance = false; // true dès qu'on a commencé à scheduler la phrase courante
  // Level meter (anneau pulsant rouge autour de liveMicZone)
  var levelAnalyser = null;
  var levelRaf = null;

  function loadVoices() {
    VB.api.get('/api/voices').then(function (d) {
      var sel = $('liveVoiceSelect');
      sel.innerHTML = '';
      (d.voices || []).forEach(function (v) {
        var opt = document.createElement('option');
        opt.value = v.id;
        var flag = v.language === 'fr' ? '🇫🇷' : '🇬🇧';
        opt.textContent = flag + ' ' + v.name;
        sel.appendChild(opt);
      });
      if (!sel.options.length) {
        var opt = document.createElement('option');
        opt.value = '';
        opt.textContent = 'Aucune voix disponible';
        sel.appendChild(opt);
      }
    });
  }

  function setStatus(text) { $('liveStatus').textContent = text; }

  function appendTranscript(text) {
    var div = document.createElement('div');
    div.style.padding = '0.25rem 0';
    div.textContent = text;
    var box = $('liveTranscript');
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
  }

  // ── V3 : chargement état cloud + RVC + briefings + providers ──

  function loadCloudStatus() {
    return VB.api.get('/api/cloud/status').then(function (d) {
      cloudStatus = {
        runpod_configured: !!d.runpod_configured,
        openai_configured: !!d.openai_configured,
        default_live_mode: d.default_live_mode || 'cpu-fr-en',
        default_translation_provider: d.default_translation_provider || 'opus-mt-cpu',
      };
      applyCloudGating();
    }).catch(function () {
      // Cloud endpoints pas dispo → on reste sur cpu-fr-en seulement
      cloudStatus = { runpod_configured: false, openai_configured: false };
      applyCloudGating();
    });
  }

  function applyCloudGating() {
    // Grise les cartes-modes qui dépendent de RunPod si non configuré
    var hasRunpod = cloudStatus.runpod_configured;
    $$('.mode-card[data-needs-runpod]').forEach(function (card) {
      card.classList.toggle('disabled', !hasRunpod);
    });
    $('liveCloudHint').style.display = hasRunpod ? 'none' : '';

    // Grise les options du provider qui dépendent d'OpenAI / RunPod
    var providerSel = $('liveProviderSelect');
    if (providerSel) {
      Array.prototype.forEach.call(providerSel.options, function (opt) {
        if (opt.dataset.needsOpenai !== undefined) {
          opt.disabled = !cloudStatus.openai_configured;
        }
        if (opt.dataset.modeGpu !== undefined) {
          opt.disabled = !hasRunpod;
        }
      });
    }
  }

  function loadRvcModels() {
    var sel = $('liveRvcSelect');
    if (!sel) return;
    VB.api.get('/api/rvc/models').then(function (d) {
      sel.innerHTML = '';
      var models = (d.models || []).filter(function (m) { return m.status === 'active'; });
      if (!models.length) {
        var opt = document.createElement('option');
        opt.value = ''; opt.textContent = '— Aucun modèle disponible —';
        sel.appendChild(opt);
        return;
      }
      models.forEach(function (m) {
        var opt = document.createElement('option');
        opt.value = m.id;
        opt.textContent = m.name + ' (' + m.size_mb + ' Mo)';
        sel.appendChild(opt);
      });
    }).catch(function () { /* RVC endpoint pas dispo, on ignore */ });
  }

  function loadBriefings() {
    var sel = $('liveBriefingSelect');
    if (!sel) return;
    VB.api.get('/api/briefings').then(function (d) {
      sel.innerHTML = '<option value="">— Aucun —</option>';
      (d.briefings || []).forEach(function (b) {
        var opt = document.createElement('option');
        opt.value = b.id; opt.textContent = b.name;
        opt.dataset.content = b.content || '';
        sel.appendChild(opt);
      });
    }).catch(function () { /* briefings pas dispo */ });
  }

  // ── Mode cards ──

  function setMode(mode) {
    currentMode = mode;
    $$('.mode-card', $('liveModeCards')).forEach(function (card) {
      card.classList.toggle('selected', card.dataset.value === mode);
    });
    var isGpu = mode !== 'cpu-fr-en';
    var isHybrid = mode === 'gpu-hybrid';

    // RVC selector visible only in hybrid
    if ($('liveRvcField')) $('liveRvcField').style.display = isHybrid ? '' : 'none';
    // GPU pre-warmup + cost indicator visible only in GPU modes
    if ($('liveGpuField')) $('liveGpuField').style.display = isGpu ? '' : 'none';
    if ($('liveCostIndicator')) $('liveCostIndicator').style.display = isGpu ? '' : 'none';

    // Restreint langues source si CPU V1 (fr/en seulement)
    var langSel = $('liveLang');
    if (langSel) {
      Array.prototype.forEach.call(langSel.options, function (opt) {
        if (opt.dataset.modeGpu !== undefined) {
          opt.hidden = !isGpu;
        }
      });
      if (langSel.options[langSel.selectedIndex] &&
          langSel.options[langSel.selectedIndex].hidden) {
        langSel.value = 'fr';
      }
    }
    var transToSel = $('liveTranslateTo');
    if (transToSel) {
      Array.prototype.forEach.call(transToSel.options, function (opt) {
        if (opt.dataset.modeGpu !== undefined) opt.hidden = !isGpu;
      });
    }
    // Provider : par défaut adapté au mode
    var providerSel = $('liveProviderSelect');
    if (providerSel && isGpu && providerSel.value === 'opus-mt-cpu') {
      providerSel.value = 'nllb';
    } else if (providerSel && !isGpu) {
      providerSel.value = 'opus-mt-cpu';
    }
    onProviderChange();
  }

  function bindModeCards() {
    var cards = $$('.mode-card', $('liveModeCards'));
    cards.forEach(function (card) {
      card.addEventListener('click', function () {
        if (card.classList.contains('disabled')) {
          VB.notify('warning', 'Ce mode nécessite RunPod — Réglages → Cloud');
          return;
        }
        setMode(card.dataset.value);
      });
    });
  }

  // ── Provider GPT → afficher briefing ──

  function onProviderChange() {
    var p = ($('liveProviderSelect') && $('liveProviderSelect').value) || 'opus-mt-cpu';
    var isGpt = p === 'gpt-4o-mini' || p === 'gpt-4o';
    if ($('liveBriefingField')) $('liveBriefingField').style.display = isGpt ? '' : 'none';
  }

  function onBriefingSelect() {
    var sel = $('liveBriefingSelect');
    var ta = $('liveBriefingContent');
    if (!sel || !ta) return;
    var opt = sel.options[sel.selectedIndex];
    if (opt && opt.dataset.content !== undefined) ta.value = opt.dataset.content;
  }

  // ── Pré-warmup GPU manuel (Décision 5) ──

  function doGpuWarmup() {
    if (!cloudStatus.runpod_configured) {
      VB.notify('warning', 'RunPod non configuré');
      return;
    }
    var components = ['whisper', 'f5tts', 'nllb'];
    if (currentMode === 'gpu-hybrid') components.push('rvc');
    gpuWarmupPending = true;
    var btn = $('btnLiveWarmup');
    var status = $('liveWarmupStatus');
    if (btn) btn.disabled = true;
    if (status) status.textContent = '⏳ Chargement modèles GPU sur RunPod… (3-15s au premier appel)';

    VB.api.post('/api/cloud/runpod/warmup', { components: components })
      .then(function (d) {
        gpuWarmupPending = false;
        if (btn) btn.disabled = false;
        if (status) status.textContent = '✅ GPU prêt (' + (d.loaded || []).join(', ') + ')';
        setTimeout(function () {
          if (status) status.textContent = '✅ GPU chaud · 1ère phrase rapide';
        }, 2500);
      })
      .catch(function (err) {
        gpuWarmupPending = false;
        if (btn) btn.disabled = false;
        if (status) status.textContent = '⚠️ Échec : ' + (err.message || err);
      });
  }

  // ── Cost update ──

  function applyCostUpdate(payload) {
    var v = $('liveCostValue');
    var d = $('liveCostDuration');
    var ind = $('liveCostIndicator');
    if (!v || !d || !ind) return;
    ind.style.display = '';
    var cost = (payload.session_cost_eur || 0).toFixed(4);
    v.textContent = cost + '€';
    var dur = payload.duration_seconds || 0;
    var mins = Math.floor(dur / 60), secs = dur % 60;
    d.textContent = (mins > 0 ? mins + 'm ' : '') + secs + 's';
  }

  function appendTranslated(text) {
    var div = document.createElement('div');
    div.style.padding = '0.25rem 0';
    div.textContent = text;
    var box = $('liveTranslated');
    if (!box) return;
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
  }

  // ── Lecture streaming des chunks PCM 24 kHz via Web Audio API ──
  //
  // Chaque audio_pcm reçu est :
  //   1. décodé base64 → Int16Array → Float32Array normalisé [-1, 1]
  //   2. emballé dans un AudioBuffer mono à 24 kHz
  //   3. scheduled sur l'AudioContext de lecture, à la suite du précédent
  //
  // ``nextPlayAt`` accumule le moment de fin du dernier chunk → permet de
  // schedule sans gap audible. Reset à 0 à la fin de la phrase (audio_end).

  function ensurePlaybackCtx() {
    if (!playbackCtx) {
      // Idem start() : pas de sampleRate forcé pour ne pas casser Safari.
      // Resampling auto à la lecture (le buffer indique 24 kHz, le contexte
      // tourne au rate device).
      var Ctx = window.AudioContext || window.webkitAudioContext;
      playbackCtx = new Ctx({ latencyHint: 'interactive' });
      nextPlayAt = playbackCtx.currentTime;
      console.log('[live] AudioContext (lazy) — rate=' + playbackCtx.sampleRate
                + ' state=' + playbackCtx.state);
    }
    if (playbackCtx.state === 'suspended') {
      playbackCtx.resume().catch(function () {});
    }
    return playbackCtx;
  }

  function decodePcmChunk(b64, ctx, sampleRate) {
    var raw = atob(b64);
    var bytes = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
    var int16 = new Int16Array(bytes.buffer, bytes.byteOffset, bytes.byteLength / 2);
    var f32 = new Float32Array(int16.length);
    for (var j = 0; j < int16.length; j++) f32[j] = int16[j] / 32768;
    var sr = sampleRate || TTS_RATE;

    // Fade in/out IN-PLACE sur les samples (pas via GainNode WebAudio,
    // qui posait un bug subtil quand bufRate=24000 ≠ ctxRate=48000 : la
    // séquence setValueAtTime + linearRamp était mal interprétée et le
    // gain restait à 0 → silence total). Ici on multiplie les premiers
    // et derniers samples par une rampe linéaire, c'est 100% prédictible
    // et indépendant du resampling AudioContext.
    if (CHUNK_FADE_MS > 0 && f32.length > 0) {
      var fadeSamples = Math.min(
        Math.floor(CHUNK_FADE_MS * sr / 1000),
        Math.floor(f32.length / 2)
      );
      for (var k = 0; k < fadeSamples; k++) {
        var coef = k / fadeSamples;     // 0 → 1 linéaire
        f32[k] *= coef;                  // fade-in
        f32[f32.length - 1 - k] *= coef; // fade-out (symétrique)
      }
    }

    var buffer = ctx.createBuffer(1, f32.length, sr);
    buffer.copyToChannel(f32, 0);
    return buffer;
  }

  function scheduleBuffer(ctx, audioBuffer) {
    // Le fade est appliqué in-place dans decodePcmChunk(), donc ici on
    // connecte la source directement à la destination — pas de GainNode
    // ni de ramp scheduling.
    var src = ctx.createBufferSource();
    src.buffer = audioBuffer;
    src.connect(ctx.destination);

    var safety = SCHEDULE_SAFETY_MS / 1000;
    var dur = audioBuffer.duration;
    var now = ctx.currentTime;
    var startAt = (nextPlayAt <= now + safety) ? (now + safety) : nextPlayAt;

    // 1-shot log pour vérifier que scheduleBuffer s'exécute bien
    if (!window.__vbScheduleLogged) {
      window.__vbScheduleLogged = true;
      console.log('[live] FIRST scheduleBuffer call:'
                + ' startAt=' + startAt.toFixed(3)
                + ' now=' + now.toFixed(3)
                + ' dur=' + (dur * 1000).toFixed(0) + 'ms'
                + ' ctxState=' + ctx.state
                + ' ctxRate=' + ctx.sampleRate
                + ' bufRate=' + audioBuffer.sampleRate
                + ' bufLen=' + audioBuffer.length
                + ' destination=' + (ctx.destination ? 'OK' : 'MISSING'));
    }

    try {
      src.start(startAt);
    } catch (e) {
      console.warn('[live] src.start failed', e, 'startAt=', startAt,
                   'now=', now, 'dur=', dur);
      return;
    }
    nextPlayAt = startAt + dur;

    // Détection underrun : si on a dû reset à `now + safety`, c'est qu'on
    // a perdu le slot précédent → log pour diagnostic.
    if (startAt === now + safety && hasStartedUtterance) {
      console.warn('[live] underrun: reset start to now+' + SCHEDULE_SAFETY_MS + 'ms');
    }
  }

  function enqueuePcmChunk(b64, sampleRate) {
    try {
      var ctx = ensurePlaybackCtx();
      if (ctx.state === 'suspended') {
        ctx.resume().catch(function (err) {
          console.warn('[live] resume during chunk failed', err);
        });
      }
      var audioBuffer = decodePcmChunk(b64, ctx, sampleRate);

      // Mode "wait_full" : on bufferise TOUS les chunks de la phrase
      // jusqu'à audio_end avant de commencer la lecture. Latence +durée
      // mais zéro underrun garanti. Activable via localStorage.vbWaitFull.
      if (WAIT_FULL_PHRASE) {
        pendingChunks.push(audioBuffer);
        pendingDurationMs += audioBuffer.duration * 1000;
        return;
      }

      if (hasStartedUtterance) {
        scheduleBuffer(ctx, audioBuffer);
        return;
      }

      pendingChunks.push(audioBuffer);
      pendingDurationMs += audioBuffer.duration * 1000;
      if (pendingDurationMs >= JITTER_BUFFER_MS) {
        flushPending(ctx);
      }
    } catch (e) {
      console.warn('[live] enqueuePcmChunk failed', e);
    }
  }

  function flushPending(ctx) {
    // Démarre la lecture en chaînant tous les chunks accumulés. Les chunks
    // qui arriveront ensuite sont scheduled directement (cf. enqueuePcmChunk).
    while (pendingChunks.length > 0) {
      scheduleBuffer(ctx, pendingChunks.shift());
    }
    pendingDurationMs = 0;
    hasStartedUtterance = true;
  }

  function onAudioEnd() {
    // Le serveur signale la fin de la phrase.
    // - Mode normal : si on a accumulé des chunks sans atteindre
    //   JITTER_BUFFER_MS (utterance courte), on flush.
    // - Mode wait_full : on flush TOUT maintenant (c'est le moment où
    //   on a la phrase complète bufferisée).
    if ((WAIT_FULL_PHRASE || !hasStartedUtterance)
        && pendingChunks.length > 0 && playbackCtx) {
      console.log('[live] audio_end → flush ' + pendingChunks.length
                + ' chunks (' + pendingDurationMs.toFixed(0) + 'ms)'
                + (WAIT_FULL_PHRASE ? ' [wait_full mode]' : ''));
      flushPending(playbackCtx);
    }
    // Reset pour la prochaine phrase : on ré-accumulera JITTER_BUFFER_MS.
    setTimeout(function () {
      hasStartedUtterance = false;
      pendingChunks = [];
      pendingDurationMs = 0;
    }, 100);
  }

  // ── WebSocket ──

  function start() {
    if (warmupPending) {
      VB.notify('warning', 'Patientez, le modèle de traduction est en cours de chargement…');
      return;
    }
    var voiceId = $('liveVoiceSelect').value;
    if (!voiceId) { VB.notify('warning', 'Choisissez une voix'); return; }
    var lang = $('liveLang').value;

    // IMPORTANT : créer + resume le playbackCtx ICI dans la chaîne de user-
    // gesture (clic sur la zone micro). Si on attend la première chunk
    // WebSocket pour le créer, Safari le marque "suspended" et resume()
    // échoue silencieusement → aucun audio ne sort.
    //
    // Pas de sampleRate forcé : Safari peut échouer silencieusement à créer
    // un AudioContext à 24 kHz si le hardware ne le supporte pas. On laisse
    // le contexte tourner au rate device (44.1 ou 48 kHz typiquement) et on
    // signale juste à chaque AudioBuffer qu'il est en 24 kHz — le navigateur
    // resamplera pour la sortie hardware.
    if (!playbackCtx) {
      try {
        var Ctx = window.AudioContext || window.webkitAudioContext;
        playbackCtx = new Ctx({ latencyHint: 'interactive' });
        console.log('[live] AudioContext created — rate=' + playbackCtx.sampleRate
                  + ' state=' + playbackCtx.state);
      } catch (e) {
        console.warn('[live] AudioContext creation failed', e);
      }
    }
    if (playbackCtx && playbackCtx.state === 'suspended') {
      playbackCtx.resume().then(function () {
        console.log('[live] playbackCtx resumed — state=' + playbackCtx.state);
      }).catch(function (err) {
        console.warn('[live] playbackCtx.resume failed', err);
      });
    }
    nextPlayAt = playbackCtx ? playbackCtx.currentTime : 0;

    var proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(proto + '//' + window.location.host + '/ws/stream');
    ws.binaryType = 'arraybuffer';

    var translateEnabled = $('liveTranslateToggle') && $('liveTranslateToggle').checked;
    var translateTo = ($('liveTranslateTo') && $('liveTranslateTo').value) || 'en';
    var provider = ($('liveProviderSelect') && $('liveProviderSelect').value) || 'opus-mt-cpu';
    var rvcModelId = ($('liveRvcSelect') && $('liveRvcSelect').value) || null;
    var briefing = ($('liveBriefingContent') && $('liveBriefingContent').value) || '';

    // Validation côté UI : mode hybride exige un modèle RVC
    if (currentMode === 'gpu-hybrid' && !rvcModelId) {
      VB.notify('error', 'Le mode hybride exige un modèle RVC — importe-en un sur /rvc-import');
      try { ws.close(); } catch (e) {}
      return;
    }

    ws.addEventListener('open', function () {
      ws.send(JSON.stringify({
        type: 'configure',
        // V1 (rétrocompat) :
        voice_id: voiceId,
        language: lang,
        output: 'browser',
        format: 'pcm16',
        translate: translateEnabled,
        translate_to: translateTo,
        // V3 :
        mode: currentMode,
        translation_provider: provider,
        target_lang: translateEnabled ? translateTo : lang,
        rvc_model_id: rvcModelId,
        briefing: (provider === 'gpt-4o-mini' || provider === 'gpt-4o') ? briefing : '',
      }));
    });

    ws.addEventListener('message', function (e) {
      // Le serveur envoie du JSON — pas de binaire pour l'instant
      var payload;
      try { payload = JSON.parse(e.data); } catch (err) { return; }
      if (payload.type === 'ready') {
        setStatus('Connecté · parlez');
        startCapture();
      } else if (payload.type === 'transcript') {
        appendTranscript('🗣 ' + payload.text);
      } else if (payload.type === 'translated') {
        var flagTo = payload.tgt_lang === 'en' ? '🇬🇧' : '🇫🇷';
        appendTranslated(flagTo + ' ' + payload.text);
      } else if (payload.type === 'translation_error') {
        VB.notify('warning', payload.message || 'Traduction échouée');
      } else if (payload.type === 'audio_pcm') {
        enqueuePcmChunk(payload.data, payload.sample_rate);
      } else if (payload.type === 'audio_end') {
        onAudioEnd();
      } else if (payload.type === 'cost_update') {
        applyCostUpdate(payload);
      } else if (payload.type === 'error') {
        VB.notify('error', payload.message || 'Erreur');
        stop();
      } else if (payload.type === 'stopped') {
        setStatus('Arrêté');
      }
    });

    ws.addEventListener('close', function () {
      setStatus('Déconnecté');
      stopCapture();
      sessionActive = false;
      $('liveMicZone').classList.remove('recording');
      $('liveMicLabel').textContent = 'Cliquez pour démarrer';
    });

    ws.addEventListener('error', function () {
      VB.notify('error', 'Erreur WebSocket');
    });

    sessionActive = true;
    $('liveMicZone').classList.add('recording');
    $('liveMicLabel').textContent = '⏹ Cliquez pour arrêter';
    setStatus('Connexion…');
  }

  // ── Capture micro via AudioWorklet PCM 16 kHz ──

  function startCapture() {
    navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    }).then(function (s) {
      stream = s;
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      return audioCtx.audioWorklet.addModule('/js/live-worklet.js').then(function () {
        sourceNode = audioCtx.createMediaStreamSource(stream);
        workletNode = new AudioWorkletNode(audioCtx, 'pcm-capture');
        workletNode.port.onmessage = function (event) {
          // ``event.data`` est l'ArrayBuffer du Int16Array (transferred)
          if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(event.data);
          }
        };
        sourceNode.connect(workletNode);
        // Pas besoin de connecter à destination (sinon l'utilisateur s'entend)
        // Level meter : tap parallèle sur le sourceNode → AnalyserNode → RMS
        // → CSS variable --mic-level sur liveMicZone (cohérent avec voices/new
        // et studio-stt). Pas de retour audio dans les enceintes (on ne
        // connecte pas l'AnalyserNode à destination).
        startLevelMeter(sourceNode, audioCtx);
      });
    }).catch(function (err) {
      console.warn('getUserMedia/worklet failed', err);
      VB.notify('error', 'Accès micro refusé ou AudioWorklet indisponible');
      stop();
    });
  }

  function startLevelMeter(srcNode, ctx) {
    try {
      levelAnalyser = ctx.createAnalyser();
      levelAnalyser.fftSize = 1024;
      levelAnalyser.smoothingTimeConstant = 0.6;
      srcNode.connect(levelAnalyser);
      var data = new Float32Array(levelAnalyser.fftSize);
      var zone = $('liveMicZone');
      function tick() {
        if (!levelAnalyser) return;
        levelAnalyser.getFloatTimeDomainData(data);
        var sum = 0;
        for (var i = 0; i < data.length; i++) sum += data[i] * data[i];
        var rms = Math.sqrt(sum / data.length);
        var level = Math.min(1, rms * 6);
        if (zone) zone.style.setProperty('--mic-level', level.toFixed(3));
        levelRaf = requestAnimationFrame(tick);
      }
      tick();
    } catch (e) {
      console.warn('live level meter unavailable', e);
    }
  }

  function stopLevelMeter() {
    if (levelRaf) { cancelAnimationFrame(levelRaf); levelRaf = null; }
    levelAnalyser = null;
    var zone = $('liveMicZone');
    if (zone) zone.style.setProperty('--mic-level', '0');
  }

  function stopCapture() {
    stopLevelMeter();
    try { if (workletNode) { workletNode.disconnect(); workletNode = null; } } catch (e) {}
    try { if (sourceNode) { sourceNode.disconnect(); sourceNode = null; } } catch (e) {}
    if (stream) {
      stream.getTracks().forEach(function (t) { t.stop(); });
      stream = null;
    }
    if (audioCtx) {
      audioCtx.close().catch(function () {});
      audioCtx = null;
    }
  }

  function stop() {
    if (ws && ws.readyState === WebSocket.OPEN) {
      try { ws.send(JSON.stringify({ type: 'stop' })); } catch (e) {}
    }
    stopCapture();
    if (ws) {
      try { ws.close(); } catch (e) {}
      ws = null;
    }
    sessionActive = false;
    $('liveMicZone').classList.remove('recording');
    $('liveMicLabel').textContent = 'Cliquez pour démarrer';
    setStatus('Arrêté');
  }

  function bindMicZone() {
    var zone = $('liveMicZone');
    if (!zone) return;
    zone.addEventListener('click', function () {
      if (sessionActive) stop();
      else start();
    });
  }

  function bindRadioGroups() {
    $$('.radio-group[data-name="live-output"]').forEach(function (group) {
      $$('.radio-option', group).forEach(function (opt) {
        opt.addEventListener('click', function () {
          if (opt.classList.contains('disabled')) return;
          $$('.radio-option', group).forEach(function (o) { o.classList.remove('selected'); });
          opt.classList.add('selected');
          group.dataset.value = opt.getAttribute('data-value');
        });
      });
    });
  }

  function bindTranslateToggle() {
    var toggle = $('liveTranslateToggle');
    var opts = $('liveTranslateOptions');
    var wrap = $('liveTranslatedWrap');
    var translateTo = $('liveTranslateTo');
    var sourceLang = $('liveLang');
    if (!toggle) return;

    function syncTranslateTo() {
      // Quand la langue source change, s'assurer que la cible est différente.
      if (!translateTo || !sourceLang) return;
      var src = sourceLang.value;
      // Filtrer les options de la liste cible : on masque la langue identique à la source
      Array.prototype.forEach.call(translateTo.options, function (opt) {
        opt.hidden = (opt.value === src);
      });
      // Si la valeur sélectionnée est devenue cachée, choisir la première visible
      if (translateTo.value === src) {
        var first = Array.prototype.find.call(translateTo.options, function (o) { return !o.hidden; });
        if (first) translateTo.value = first.value;
      }
    }

    // Affiche/masque un message de statut sous les options de traduction.
    function setWarmupStatus(msg, isError) {
      var el = $('liveTranslateStatus');
      if (!el) return;
      el.style.display = msg ? '' : 'none';
      el.textContent = msg || '';
      el.style.color = isError ? 'var(--error, #e55)' : 'var(--text3)';
    }

    // Pre-warm : charge le modèle OPUS-MT avant le démarrage de la session.
    function doWarmup() {
      if (!translateTo) return;
      var src = (sourceLang && sourceLang.value) || 'fr';
      var tgt = translateTo.value || 'en';
      if (src === tgt) return;

      warmupPending = true;
      var zone = $('liveMicZone');
      if (zone) { zone.style.opacity = '0.45'; zone.style.pointerEvents = 'none'; }
      setWarmupStatus('⏳ Chargement modèle de traduction…');

      VB.api.get('/api/translate/warmup?src=' + src + '&tgt=' + tgt)
        .then(function (d) {
          warmupPending = false;
          if (zone) { zone.style.opacity = ''; zone.style.pointerEvents = ''; }
          setWarmupStatus('✅ Modèle prêt');
          setTimeout(function () {
            if ($('liveTranslateToggle') && $('liveTranslateToggle').checked) {
              setWarmupStatus('');
            }
          }, 2000);
        })
        .catch(function (err) {
          warmupPending = false;
          if (zone) { zone.style.opacity = ''; zone.style.pointerEvents = ''; }
          setWarmupStatus('⚠️ Échec chargement modèle : ' + (err.message || err), true);
          console.warn('[live] translate warmup failed', err);
        });
    }

    toggle.addEventListener('change', function () {
      if (opts) opts.style.display = toggle.checked ? '' : 'none';
      if (wrap) wrap.style.display = toggle.checked ? '' : 'none';
      if (toggle.checked) {
        syncTranslateTo();
        doWarmup();
      } else {
        // Toggle off : annuler l'état de warmup et réactiver la zone micro.
        warmupPending = false;
        var zone = $('liveMicZone');
        if (zone) { zone.style.opacity = ''; zone.style.pointerEvents = ''; }
        setWarmupStatus('');
      }
    });

    if (sourceLang) {
      sourceLang.addEventListener('change', function () {
        syncTranslateTo();
        // Si la traduction est active, re-warm avec la nouvelle paire de langues.
        if (toggle.checked) doWarmup();
      });
    }

    // Re-warm si la langue cible change manuellement.
    if (translateTo) {
      translateTo.addEventListener('change', function () {
        if (toggle.checked) doWarmup();
      });
    }
  }

  document.addEventListener('DOMContentLoaded', function () {
    bindMicZone();
    bindRadioGroups();
    bindTranslateToggle();
    bindModeCards();

    // V3 : provider + briefing handlers
    var providerSel = $('liveProviderSelect');
    if (providerSel) providerSel.addEventListener('change', onProviderChange);
    var briefingSel = $('liveBriefingSelect');
    if (briefingSel) briefingSel.addEventListener('change', onBriefingSelect);
    var btnWarmup = $('btnLiveWarmup');
    if (btnWarmup) btnWarmup.addEventListener('click', doGpuWarmup);

    // Loaders
    loadVoices();
    loadCloudStatus().then(function () {
      // Une fois cloud status connu, charger RVC + briefings + appliquer mode défaut
      loadRvcModels();
      loadBriefings();
      // Mode par défaut : si RunPod pas configuré, force cpu-fr-en
      var defaultMode = cloudStatus.runpod_configured
        ? (cloudStatus.default_live_mode || 'cpu-fr-en')
        : 'cpu-fr-en';
      setMode(defaultMode);
      onProviderChange();
    });
  });
})();
