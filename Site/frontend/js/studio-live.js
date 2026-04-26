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

  // Stratégie "full-phrase buffer" : on accumule TOUS les chunks d'une phrase
  // sans rien jouer, et on les flush d'un coup quand le serveur envoie
  // `audio_end`. Garantit zéro gap, au prix d'une latence = durée de
  // génération complète côté serveur. Choix imposé par les CPU plus lents
  // que real-time pour NeuTTS Q4 (ex : 7-9s de génération pour 2-3s d'audio
  // sur EPYC 4 cores) → impossible de streamer fluide sans risque de gap.
  //
  // Un cap MAX_BUFFER_MS coupe court si l'on dépasse 6s d'audio bufferisé
  // (cas pathologique : audio_end jamais reçu). À ce stade on flush ce
  // qu'on a et tant pis si la suite est hachée.
  var MAX_BUFFER_MS = 6000;
  var IDLE_FLUSH_MS = 4000;        // si plus aucun chunk depuis 4s mais audio_end pas reçu → flush quand même
  var pendingChunks = [];          // AudioBuffers en attente de scheduling
  var pendingDurationMs = 0;       // total des durées accumulées
  var hasStartedUtterance = false; // true dès qu'on a commencé à scheduler la phrase courante
  var lastChunkAt = 0;             // timestamp ms du dernier chunk reçu (watchdog idle flush)
  var idleFlushTimer = null;
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
      playbackCtx = new (window.AudioContext || window.webkitAudioContext)({
        sampleRate: TTS_RATE,
        latencyHint: 'interactive',
      });
      nextPlayAt = playbackCtx.currentTime;
    }
    if (playbackCtx.state === 'suspended') {
      playbackCtx.resume().catch(function () {});
    }
    return playbackCtx;
  }

  function decodePcmChunk(b64, ctx) {
    var raw = atob(b64);
    var bytes = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
    var int16 = new Int16Array(bytes.buffer, bytes.byteOffset, bytes.byteLength / 2);
    var f32 = new Float32Array(int16.length);
    for (var j = 0; j < int16.length; j++) f32[j] = int16[j] / 32768;
    var buffer = ctx.createBuffer(1, f32.length, TTS_RATE);
    buffer.copyToChannel(f32, 0);
    return buffer;
  }

  function scheduleBuffer(ctx, audioBuffer) {
    var src = ctx.createBufferSource();
    src.buffer = audioBuffer;
    src.connect(ctx.destination);
    var now = ctx.currentTime;
    var startAt = (nextPlayAt <= now + 0.02) ? now + 0.02 : nextPlayAt;
    src.start(startAt);
    nextPlayAt = startAt + audioBuffer.duration;
  }

  function enqueuePcmChunk(b64) {
    try {
      var ctx = ensurePlaybackCtx();
      var audioBuffer = decodePcmChunk(b64, ctx);
      lastChunkAt = Date.now();

      if (hasStartedUtterance) {
        // Phrase déjà en cours de lecture (flush déclenché par audio_end ou
        // par MAX_BUFFER_MS) — on enchaîne au fil de l'eau. Risque de gap
        // si la génération devient plus lente que la lecture.
        scheduleBuffer(ctx, audioBuffer);
        return;
      }

      // Accumule sans jouer. On flush sur audio_end (cas normal) ou via le
      // watchdog idle/MAX_BUFFER (sécurité si audio_end n'arrive pas).
      pendingChunks.push(audioBuffer);
      pendingDurationMs += audioBuffer.duration * 1000;
      console.log('[live] +chunk dur=' + (audioBuffer.duration * 1000).toFixed(0) + 'ms total=' + pendingDurationMs.toFixed(0) + 'ms n=' + pendingChunks.length);

      if (pendingDurationMs >= MAX_BUFFER_MS) {
        console.warn('[live] MAX_BUFFER_MS atteint avant audio_end — flush forcé');
        flushPending(ctx);
        return;
      }

      // Watchdog : si plus aucun chunk pendant IDLE_FLUSH_MS, on flush
      // par défaut (audio_end manquant ou serveur déconnecté).
      if (idleFlushTimer) clearTimeout(idleFlushTimer);
      idleFlushTimer = setTimeout(function () {
        if (pendingChunks.length > 0 && playbackCtx) {
          console.warn('[live] idle flush (' + IDLE_FLUSH_MS + 'ms sans chunk + audio_end manquant)');
          flushPending(playbackCtx);
        }
      }, IDLE_FLUSH_MS);
    } catch (e) {
      console.warn('[live] enqueuePcmChunk failed', e);
    }
  }

  function flushPending(ctx) {
    var n = pendingChunks.length;
    var totalMs = pendingDurationMs.toFixed(0);
    while (pendingChunks.length > 0) {
      scheduleBuffer(ctx, pendingChunks.shift());
    }
    pendingDurationMs = 0;
    hasStartedUtterance = true;
    if (idleFlushTimer) { clearTimeout(idleFlushTimer); idleFlushTimer = null; }
    console.log('[live] flushed ' + n + ' chunks (' + totalMs + 'ms total)');
  }

  function onAudioEnd() {
    console.log('[live] audio_end reçu, pending=' + pendingChunks.length);
    if (pendingChunks.length > 0 && playbackCtx) {
      flushPending(playbackCtx);
    }
    if (idleFlushTimer) { clearTimeout(idleFlushTimer); idleFlushTimer = null; }
    setTimeout(function () {
      hasStartedUtterance = false;
      pendingChunks = [];
      pendingDurationMs = 0;
    }, 100);
  }

  // ── WebSocket ──

  function start() {
    var voiceId = $('liveVoiceSelect').value;
    if (!voiceId) { VB.notify('warning', 'Choisissez une voix'); return; }
    var lang = $('liveLang').value;

    var proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(proto + '//' + window.location.host + '/ws/stream');
    ws.binaryType = 'arraybuffer';

    ws.addEventListener('open', function () {
      ws.send(JSON.stringify({
        type: 'configure',
        voice_id: voiceId,
        language: lang,
        output: 'browser',
        format: 'pcm16',  // indique au serveur qu'on envoie du PCM 16k int16 mono
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
      } else if (payload.type === 'audio_pcm') {
        enqueuePcmChunk(payload.data);
      } else if (payload.type === 'audio_end') {
        onAudioEnd();
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

  document.addEventListener('DOMContentLoaded', function () {
    bindMicZone();
    bindRadioGroups();
    loadVoices();
  });
})();
