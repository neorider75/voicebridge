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
  // Head buffer en début de chaque utterance : on attend N ms avant de
  // commencer à jouer pour absorber la jitter (variabilité NeuTTS Q4 +
  // transport WebSocket + base64). Trade-off : plus = moins de gaps, moins
  // de latence perçue. 150 ms est un compromis. Si on entend encore des
  // blancs : monter à 200-250. Si la latence est trop forte : descendre
  // à 100 (mais risque de gaps).
  var BUFFER_HEAD_MS = 150;
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

  function enqueuePcmChunk(b64) {
    try {
      var ctx = ensurePlaybackCtx();
      var raw = atob(b64);
      var bytes = new Uint8Array(raw.length);
      for (var i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
      // Décodage int16 little-endian
      var int16 = new Int16Array(bytes.buffer, bytes.byteOffset, bytes.byteLength / 2);
      var f32 = new Float32Array(int16.length);
      for (var j = 0; j < int16.length; j++) f32[j] = int16[j] / 32768;
      var buffer = ctx.createBuffer(1, f32.length, TTS_RATE);
      buffer.copyToChannel(f32, 0);
      var src = ctx.createBufferSource();
      src.buffer = buffer;
      src.connect(ctx.destination);

      var now = ctx.currentTime;
      var startAt;
      if (nextPlayAt <= now + 0.05) {
        // Underrun ou première chunk d'une utterance — on applique le head
        // buffer pour absorber la jitter avant d'enchaîner les chunks suivants.
        startAt = now + BUFFER_HEAD_MS / 1000;
      } else {
        // Déjà en train de jouer — on enchaîne sans gap.
        startAt = nextPlayAt;
      }
      src.start(startAt);
      nextPlayAt = startAt + buffer.duration;
    } catch (e) {
      console.warn('enqueuePcmChunk failed', e);
    }
  }

  function onAudioEnd() {
    // Pas de reset explicite de nextPlayAt : si l'utilisateur reparle, le
    // prochain chunk verra "nextPlayAt < now + 50ms" → applique le head
    // buffer naturellement (cf. enqueuePcmChunk). Plus simple et sans race.
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
