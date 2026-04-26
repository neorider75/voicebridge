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
  var pendingPlay = []; // file d'attente audio renvoyé par le serveur
  var playing = false;

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

  // ── Lecture séquentielle des audio_chunk (WAV b64 → Blob → audio.play) ──

  function enqueueWav(b64) {
    try {
      var raw = atob(b64);
      var bytes = new Uint8Array(raw.length);
      for (var i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
      var blob = new Blob([bytes], { type: 'audio/wav' });
      pendingPlay.push(URL.createObjectURL(blob));
      tryPlayNext();
    } catch (e) {
      console.warn('enqueueWav failed', e);
    }
  }

  function tryPlayNext() {
    if (playing || pendingPlay.length === 0) return;
    var url = pendingPlay.shift();
    var audio = $('liveAudio');
    audio.src = url;
    playing = true;
    audio.play().then(function () {
      audio.onended = function () {
        URL.revokeObjectURL(url);
        playing = false;
        tryPlayNext();
      };
    }).catch(function () {
      URL.revokeObjectURL(url);
      playing = false;
      tryPlayNext();
    });
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
      } else if (payload.type === 'audio_chunk') {
        enqueueWav(payload.data);
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
      });
    }).catch(function (err) {
      console.warn('getUserMedia/worklet failed', err);
      VB.notify('error', 'Accès micro refusé ou AudioWorklet indisponible');
      stop();
    });
  }

  function stopCapture() {
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
