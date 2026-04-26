// VoiceBridge — studio-live.js : WebSocket /ws/stream Live (livraison 4).
// MediaRecorder webm chunks ~1s → WebSocket → audio retour PCM 24kHz
// playé via HTMLAudioElement avec MediaSource.

(function () {
  function $(id) { return document.getElementById(id); }
  function $$(sel, r) { return Array.prototype.slice.call((r || document).querySelectorAll(sel)); }

  var ws = null;
  var mediaRec = null;
  var stream = null;
  var sessionActive = false;

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

  function playWavBase64(b64) {
    // Décode → blob → URL → Audio.play
    try {
      var raw = atob(b64);
      var bytes = new Uint8Array(raw.length);
      for (var i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
      var blob = new Blob([bytes], { type: 'audio/wav' });
      var url = URL.createObjectURL(blob);
      var audio = $('liveAudio');
      audio.src = url;
      audio.play().catch(function () {});
      // libère la précédente URL au prochain ended
      audio.onended = function () { URL.revokeObjectURL(url); };
    } catch (e) {
      console.warn('playWavBase64 failed', e);
    }
  }

  function start() {
    var voiceId = $('liveVoiceSelect').value;
    if (!voiceId) { VB.notify('warning', 'Choisissez une voix'); return; }
    var lang = $('liveLang').value;

    var proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(proto + '//' + window.location.host + '/ws/stream');

    ws.addEventListener('open', function () {
      ws.send(JSON.stringify({
        type: 'configure',
        voice_id: voiceId,
        language: lang,
        output: 'browser',
      }));
    });

    ws.addEventListener('message', function (e) {
      var payload;
      try { payload = JSON.parse(e.data); } catch (err) { return; }
      if (payload.type === 'ready') {
        setStatus('Connecté · parlez');
        startRecording();
      } else if (payload.type === 'transcript') {
        appendTranscript('🗣 ' + payload.text);
      } else if (payload.type === 'audio_chunk') {
        playWavBase64(payload.data);
      } else if (payload.type === 'error') {
        VB.notify('error', payload.message || 'Erreur');
        stop();
      } else if (payload.type === 'stopped') {
        setStatus('Arrêté');
      }
    });

    ws.addEventListener('close', function () {
      setStatus('Déconnecté');
      stopRecording();
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

  function startRecording() {
    navigator.mediaDevices.getUserMedia({ audio: true }).then(function (s) {
      stream = s;
      mediaRec = new MediaRecorder(s, { mimeType: 'audio/webm' });
      mediaRec.ondataavailable = function (e) {
        if (e.data && e.data.size > 0 && ws && ws.readyState === WebSocket.OPEN) {
          ws.send(e.data); // binary frame
        }
      };
      // Émet un chunk toutes les 1000 ms (compromis latence/CPU)
      mediaRec.start(1000);
    }).catch(function () {
      VB.notify('error', 'Accès micro refusé');
      stop();
    });
  }

  function stopRecording() {
    if (mediaRec && mediaRec.state !== 'inactive') {
      try { mediaRec.stop(); } catch (e) { /* ignore */ }
    }
    if (stream) {
      stream.getTracks().forEach(function (t) { t.stop(); });
      stream = null;
    }
    mediaRec = null;
  }

  function stop() {
    if (ws && ws.readyState === WebSocket.OPEN) {
      try { ws.send(JSON.stringify({ type: 'stop' })); } catch (e) { /* ignore */ }
    }
    stopRecording();
    if (ws) {
      try { ws.close(); } catch (e) { /* ignore */ }
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
