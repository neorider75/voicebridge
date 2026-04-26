// VoiceBridge — studio-stt.js : 4 phases STT (enregistrement → transcription
// → génération → résultat). Chargé en plus de studio.js.

(function () {
  function $(id) { return document.getElementById(id); }
  function $$(sel, r) { return Array.prototype.slice.call((r || document).querySelectorAll(sel)); }

  var mediaRec = null, recChunks = [], recordedBlob = null;

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
      navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
        recChunks = [];
        mediaRec = new MediaRecorder(stream);
        mediaRec.ondataavailable = function (e) { if (e.data.size > 0) recChunks.push(e.data); };
        mediaRec.onstop = function () {
          stream.getTracks().forEach(function (t) { t.stop(); });
          recordedBlob = new Blob(recChunks, { type: 'audio/webm' });
          zone.classList.remove('recording');
          $('sttMicLabel').textContent = '🔄 Cliquez pour ré-enregistrer';
          uploadAndTranscribe();
        };
        mediaRec.start();
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

    setDone(1, false);
    setLocked(2, false);
    $('sttTranscript').value = 'Transcription en cours…';

    var fd = new FormData();
    fd.append('audio', recordedBlob, 'recording.webm');
    fd.append('language', lang);

    fetch('/api/stt/transcribe', {
      method: 'POST', credentials: 'same-origin', body: fd,
    }).then(function (r) {
      if (!r.ok) return r.json().then(function (d) { throw new Error(d.message || ('HTTP ' + r.status)); });
      return r.json();
    }).then(function (data) {
      $('sttTranscript').value = data.text || '';
      $('sttOriginalAudio').src = data.audio_url || '';
      $('sttOriginalAudio').load();
      setDone(1, true);
      setLocked(3, false);
      // Charge la liste des voix dans le sélecteur STT
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
        if (!r.ok) return r.json().then(function (d) { throw new Error(d.message || ('HTTP ' + r.status)); });
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
