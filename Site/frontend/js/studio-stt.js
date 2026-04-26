// VoiceBridge — studio-stt.js : 4 phases STT (enregistrement → transcription
// → génération → résultat). Chargé en plus de studio.js.

(function () {
  function $(id) { return document.getElementById(id); }
  function $$(sel, r) { return Array.prototype.slice.call((r || document).querySelectorAll(sel)); }

  var mediaRec = null, recChunks = [], recordedBlob = null, recordedMime = 'audio/webm';
  var levelCtx = null, levelAnalyser = null, levelRaf = null;

  // ── Helpers MediaRecorder cross-browser (mêmes patterns que voices-new.js) ──
  function pickRecorderMime() {
    var candidates = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4;codecs=mp4a.40.2', 'audio/mp4', 'audio/ogg;codecs=opus'];
    if (typeof MediaRecorder === 'undefined' || !MediaRecorder.isTypeSupported) return '';
    for (var i = 0; i < candidates.length; i++) {
      if (MediaRecorder.isTypeSupported(candidates[i])) return candidates[i];
    }
    return '';
  }
  function extForMime(mime) {
    if (!mime) return 'webm';
    if (mime.indexOf('mp4') >= 0) return 'm4a';
    if (mime.indexOf('ogg') >= 0) return 'ogg';
    if (mime.indexOf('webm') >= 0) return 'webm';
    return 'webm';
  }

  // ── Niveau audio temps réel pendant l'enregistrement (anneau pulsant) ──
  function startLevelMeter(stream) {
    try {
      var Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      levelCtx = new Ctx();
      if (levelCtx.state === 'suspended') levelCtx.resume().catch(function () {});
      var source = levelCtx.createMediaStreamSource(stream);
      levelAnalyser = levelCtx.createAnalyser();
      levelAnalyser.fftSize = 1024;
      levelAnalyser.smoothingTimeConstant = 0.6;
      source.connect(levelAnalyser);
      var data = new Float32Array(levelAnalyser.fftSize);
      var zone = $('sttMicZone');
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
      console.warn('level meter unavailable', e);
    }
  }
  function stopLevelMeter() {
    if (levelRaf) { cancelAnimationFrame(levelRaf); levelRaf = null; }
    levelAnalyser = null;
    if (levelCtx) { try { levelCtx.close(); } catch (e) {} levelCtx = null; }
    var zone = $('sttMicZone');
    if (zone) zone.style.setProperty('--mic-level', '0');
  }

  // ── Robust error parsing (gère 500 plain-text + JSON FastAPI) ──
  function readErrorMessage(response) {
    return response.text().then(function (raw) {
      var msg = 'Erreur ' + response.status;
      try {
        var d = JSON.parse(raw);
        msg = (d && d.detail && d.detail.message)
          || (d && d.message)
          || (d && d.detail && typeof d.detail === 'string' ? d.detail : null)
          || msg;
      } catch (e) {
        if (raw && raw.length < 200) msg = msg + ' · ' + raw;
      }
      return msg;
    });
  }

  function setLocked(num, locked) {
    var step = document.querySelector('.step[data-step="stt-' + num + '"]');
    if (step) step.classList.toggle('locked', !!locked);
  }
  function setDone(num, done) {
    var step = document.querySelector('.step[data-step="stt-' + num + '"]');
    if (step) step.classList.toggle('done', !!done);
  }

  function readRadio(name) {
    var group = document.querySelector('.radio-group[data-name="' + name + '"]');
    if (!group) return null;
    return group.dataset.value || (group.querySelector('.radio-option.selected') || {}).getAttribute?.('data-value');
  }

  // ── Step 1 : enregistrement ──
  function bindRecord() {
    var zone = $('sttMicZone');
    if (!zone) return;
    zone.addEventListener('click', function () {
      if (mediaRec && mediaRec.state === 'recording') {
        mediaRec.stop();
        return;
      }
      navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      }).then(function (stream) {
        recChunks = [];
        var mime = pickRecorderMime();
        try {
          mediaRec = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
        } catch (e) {
          mediaRec = new MediaRecorder(stream);
        }
        recordedMime = mediaRec.mimeType || mime || 'audio/webm';
        mediaRec.ondataavailable = function (e) { if (e.data.size > 0) recChunks.push(e.data); };
        mediaRec.onstop = function () {
          stopLevelMeter();
          stream.getTracks().forEach(function (t) { t.stop(); });
          recordedBlob = new Blob(recChunks, { type: recordedMime });
          // Preview local immédiat — l'utilisateur peut écouter sa propre voix
          // tout de suite, sans attendre le retour serveur.
          var audioEl = $('sttOriginalAudio');
          if (audioEl) {
            audioEl.src = URL.createObjectURL(recordedBlob);
            audioEl.load();
          }
          zone.classList.remove('recording');
          $('sttMicLabel').textContent = '🔄 Cliquez pour ré-enregistrer';
          uploadAndTranscribe();
        };
        mediaRec.start();
        startLevelMeter(stream);
        zone.classList.add('recording');
        $('sttMicLabel').textContent = '⏺ Enregistrement… cliquez pour arrêter';
      }).catch(function () {
        VB.notify('error', 'Accès au micro refusé');
      });
    });
  }

  // ── Step 2 : transcription via /api/stt/transcribe ──
  function uploadAndTranscribe() {
    if (!recordedBlob) return;
    var lang = $('sttLang').value;

    // Débloque le step 2 immédiatement pour que le textarea + l'audio
    // preview soient visibles pendant que Kyutai bosse.
    setDone(1, false);
    setLocked(2, false);
    $('sttTranscript').value = '⏳ Transcription en cours… (Kyutai charge le modèle au 1er appel : 30-60 s à froid, 2-3 s ensuite)';

    var fd = new FormData();
    // Le serveur s'appuie sur l'extension pour décoder via ffmpeg → on
    // reflète le mimeType réellement utilisé par MediaRecorder (m4a sur
    // Safari, webm sur Chrome/Firefox).
    fd.append('audio', recordedBlob, 'recording.' + extForMime(recordedMime));
    fd.append('language', lang);

    fetch('/api/stt/transcribe', {
      method: 'POST', credentials: 'same-origin', body: fd,
    }).then(function (r) {
      if (!r.ok) return readErrorMessage(r).then(function (msg) { throw new Error(msg); });
      return r.json();
    }).then(function (data) {
      $('sttTranscript').value = data.text || '';
      // Si le serveur renvoie un audio_url (WAV converti côté backend), on
      // l'utilise — sinon on garde le preview local du blob enregistré.
      if (data.audio_url) {
        var audioEl = $('sttOriginalAudio');
        audioEl.src = data.audio_url;
        audioEl.load();
      }
      setDone(1, true);
      setLocked(3, false);
      loadVoicesIntoSelect('sttVoiceSelect');
      VB.notify('success', 'Transcription terminée');
    }).catch(function (e) {
      $('sttTranscript').value = '';
      VB.notify('error', e.message || 'Transcription impossible');
    });
  }

  function loadVoicesIntoSelect(selectId) {
    VB.api.get('/api/voices').then(function (d) {
      var sel = $(selectId);
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
        opt.value = ''; opt.textContent = 'Aucune voix disponible';
        sel.appendChild(opt);
      }
    });
  }

  // ── Radio groups (factorisé) ──
  function bindRadioGroups() {
    $$('.radio-group[data-name^="stt-"]').forEach(function (group) {
      $$('.radio-option', group).forEach(function (opt) {
        opt.addEventListener('click', function () {
          $$('.radio-option', group).forEach(function (o) { o.classList.remove('selected'); });
          opt.classList.add('selected');
          group.dataset.value = opt.getAttribute('data-value');
        });
      });
    });
  }

  // ── Step 3 : génération via /api/tts/generate ──
  function bindGenerate() {
    $('sttBtnGenerate').addEventListener('click', function () {
      var text = ($('sttTranscript').value || '').trim();
      if (!text) { VB.notify('warning', 'Transcription vide'); return; }
      var voiceId = $('sttVoiceSelect').value;
      if (!voiceId) { VB.notify('warning', 'Choisissez une voix'); return; }

      var format = readRadio('stt-format') || 'wav';
      var quality = readRadio('stt-quality') || 'high';
      var retention = readRadio('stt-retention') || 'session';

      var btn = $('sttBtnGenerate');
      btn.disabled = true; btn.textContent = '⏳ Génération…';

      fetch('/api/tts/generate', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: text, voice_id: voiceId, format: format, quality: quality, retention: retention }),
      }).then(function (r) {
        var ct = r.headers.get('content-type') || '';
        if (!r.ok) return readErrorMessage(r).then(function (msg) { throw new Error(msg); });
        if (ct.indexOf('application/json') >= 0) {
          return r.json().then(function (d) { showResult({ url: d.url, format: format, retention: retention, expires_at: d.expires_at }); });
        }
        return r.blob().then(function (blob) {
          showResult({ url: URL.createObjectURL(blob), format: format, retention: 'session' });
        });
      }).catch(function (e) {
        VB.notify('error', e.message || 'Erreur de génération');
      }).finally(function () {
        btn.disabled = false; btn.textContent = '🎙 Générer';
      });
    });
  }

  function showResult(payload) {
    setDone(3, true);
    setLocked(4, false);

    var audio = $('sttResultAudio');
    audio.src = payload.url; audio.load();

    var notice = $('sttResultNotice');
    if (payload.retention === 'session') {
      notice.className = 'alert alert-warning';
      notice.textContent = '⚠️ Session uniquement — Téléchargez maintenant si vous souhaitez conserver ce fichier';
    } else {
      notice.className = 'alert alert-success';
      notice.textContent = '✅ Enregistré jusqu\'au ' + (payload.expires_at || '');
    }

    $('sttBtnDownload').onclick = function () {
      var ext = payload.format === 'mp3' ? 'mp3' : 'wav';
      var a = document.createElement('a');
      a.href = payload.url;
      a.download = 'voicebridge-stt-' + Date.now() + '.' + ext;
      document.body.appendChild(a); a.click(); a.remove();
    };

    VB.notify('success', 'Génération terminée');
  }

  // ── Sub-tab switching (TTS ↔ STT) ──
  function bindSubTabs() {
    $$('.studio-tab').forEach(function (tab) {
      tab.addEventListener('click', function () {
        if (tab.classList.contains('disabled')) return;
        var target = tab.getAttribute('data-tab');
        $$('.studio-tab').forEach(function (t) { t.classList.remove('active'); });
        tab.classList.add('active');
        $$('.studio-content').forEach(function (c) {
          c.classList.toggle('active', c.getAttribute('data-tab') === target);
        });
      });
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    bindSubTabs();
    bindRadioGroups();
    bindRecord();
    bindGenerate();
  });
})();
