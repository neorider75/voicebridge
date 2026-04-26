// VoiceBridge — voices-new.js
// Ajout d'une voix : 3 sources (enregistrement, upload, URL).

(function () {
  function $(id) { return document.getElementById(id); }
  function $$(sel, r) { return Array.prototype.slice.call((r || document).querySelectorAll(sel)); }

  var REF_TEXT = {
    fr: "Ce matin, le ciel était particulièrement clair. J'ai décidé de sortir marcher un peu, histoire de prendre l'air et de réfléchir tranquillement. Quelle belle journée pour se promener ! Tu viens avec moi la prochaine fois ?",
    en: "I never thought a simple walk could change my whole morning. The air was fresh, the light was soft, and everything felt strangely calm. What an incredible thing nature can be ! Do you ever get that feeling where time just stops for a moment ?",
  };

  var currentSource = 'record'; // record | upload | url
  var pendingUrlVoiceId = null;
  var recordedBlob = null;

  function selectSource(src) {
    currentSource = src;
    $$('.source-tab').forEach(function (t) {
      t.classList.toggle('active', t.getAttribute('data-source') === src);
    });
    $$('.source-content').forEach(function (c) {
      c.classList.toggle('active', c.getAttribute('data-source') === src);
    });
  }

  function updateRefText() {
    var lang = $('langSelect').value;
    $('refText').textContent = REF_TEXT[lang] || REF_TEXT.fr;
  }

  // ── Source : enregistrement micro (MediaRecorder) ──

  var mediaRec = null, recChunks = [];
  function bindRecord() {
    var zone = $('micZone');
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
          $('micPreview').src = URL.createObjectURL(recordedBlob);
          $('micPreviewWrap').style.display = 'block';
          zone.classList.remove('recording');
          $('micLabel').textContent = '🎤 Cliquez pour ré-enregistrer';
        };
        mediaRec.start();
        zone.classList.add('recording');
        $('micLabel').textContent = '⏺ Enregistrement… cliquez pour arrêter';
      }).catch(function () {
        VB.notify('error', 'Accès au micro refusé');
      });
    });
  }

  // ── Source : upload fichier ──

  function bindUpload() {
    var dz = $('dropZone');
    var input = $('fileInput');
    if (!dz || !input) return;

    dz.addEventListener('click', function () { input.click(); });
    dz.addEventListener('dragover', function (e) { e.preventDefault(); dz.classList.add('dragover'); });
    dz.addEventListener('dragleave', function () { dz.classList.remove('dragover'); });
    dz.addEventListener('drop', function (e) {
      e.preventDefault();
      dz.classList.remove('dragover');
      if (e.dataTransfer.files.length) {
        input.files = e.dataTransfer.files;
        showUploadPreview(e.dataTransfer.files[0]);
      }
    });
    input.addEventListener('change', function () {
      if (input.files.length) showUploadPreview(input.files[0]);
    });
  }
  function showUploadPreview(file) {
    $('uploadName').textContent = file.name + ' · ' + Math.round(file.size / 1024) + ' Ko';
    $('uploadName').style.display = 'block';
  }

  // ── Source : URL (SSE) ──

  function bindUrl() {
    var btn = $('btnExtract');
    if (!btn) return;
    btn.addEventListener('click', extractFromUrl);
  }

  function extractFromUrl() {
    var url = $('urlInput').value.trim();
    var name = $('voiceName').value.trim();
    var lang = $('langSelect').value;
    if (!url) { VB.notify('warning', 'Saisissez une URL'); return; }
    if (!name) { VB.notify('warning', 'Saisissez d\'abord un nom'); return; }

    var prog = $('extractProgress');
    var bar = $('extractBar');
    var label = $('extractStep');
    prog.classList.add('visible');
    bar.style.width = '0%';
    label.textContent = 'Démarrage…';

    fetch('/api/voices/from-url', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name, language: lang, url: url }),
    }).then(function (r) {
      if (!r.ok || !r.body) { throw new Error('Extraction impossible (HTTP ' + r.status + ')'); }
      var reader = r.body.getReader();
      var decoder = new TextDecoder();
      var buf = '';
      function pump() {
        return reader.read().then(function (chunk) {
          if (chunk.done) return;
          buf += decoder.decode(chunk.value, { stream: true });
          // Parse les events SSE séparés par \n\n
          var parts = buf.split('\n\n');
          buf = parts.pop();
          parts.forEach(handleSseBlock);
          return pump();
        });
      }
      return pump();
    }).catch(function (e) {
      VB.notify('error', e.message || 'Extraction impossible');
    });
  }

  function handleSseBlock(block) {
    var lines = block.split('\n');
    var ev = 'message', data = '';
    lines.forEach(function (l) {
      if (l.indexOf('event:') === 0) ev = l.slice(6).trim();
      else if (l.indexOf('data:') === 0) data = l.slice(5).trim();
    });
    if (!data) return;
    var payload;
    try { payload = JSON.parse(data); } catch (e) { return; }
    if (ev === 'progress') {
      $('extractBar').style.width = (payload.percent || 0) + '%';
      var labels = { download: 'Téléchargement', extract: 'Extraction', convert: 'Conversion', trim: 'Sélection' };
      $('extractStep').textContent = labels[payload.step] || payload.step;
    } else if (ev === 'result') {
      pendingUrlVoiceId = payload.id;
      $('extractStep').textContent = '✅ Extrait — écoutez puis confirmez ci-dessous';
      $('extractPreview').src = payload.preview_url;
      $('extractPreviewWrap').style.display = 'block';
    } else if (ev === 'error') {
      VB.notify('error', payload.message || 'Erreur extraction');
    }
  }

  // ── Soumission finale ──

  function bindSubmit() {
    $('btnSubmit').addEventListener('click', function () {
      var name = $('voiceName').value.trim();
      var lang = $('langSelect').value;
      if (!name) { VB.notify('warning', 'Nom obligatoire'); return; }

      if (currentSource === 'url') {
        if (!pendingUrlVoiceId) { VB.notify('warning', 'Lancez d\'abord l\'extraction'); return; }
        VB.api.post('/api/voices/' + pendingUrlVoiceId + '/confirm')
          .then(function () { done(); })
          .catch(function (e) { VB.notify('error', e.message || 'Échec confirmation'); });
        return;
      }

      var fd = new FormData();
      fd.append('name', name);
      fd.append('language', lang);

      if (currentSource === 'record') {
        if (!recordedBlob) { VB.notify('warning', 'Enregistrez d\'abord votre voix'); return; }
        fd.append('audio_file', recordedBlob, 'recording.webm');
      } else if (currentSource === 'upload') {
        var input = $('fileInput');
        if (!input.files.length) { VB.notify('warning', 'Choisissez un fichier'); return; }
        fd.append('audio_file', input.files[0]);
      }

      fetch('/api/voices', {
        method: 'POST',
        credentials: 'same-origin',
        body: fd,
      }).then(function (r) {
        if (!r.ok) return r.json().then(function (d) { throw new Error(d.message || 'Erreur ' + r.status); });
        return r.json();
      }).then(function () { done(); })
        .catch(function (e) { VB.notify('error', e.message || 'Création impossible'); });
    });
  }

  function done() {
    VB.notify('success', 'Voix ajoutée');
    setTimeout(function () { window.location.href = '/voices'; }, 600);
  }

  document.addEventListener('DOMContentLoaded', function () {
    $$('.source-tab').forEach(function (t) {
      t.addEventListener('click', function () { selectSource(t.getAttribute('data-source')); });
    });
    $('langSelect').addEventListener('change', updateRefText);
    updateRefText();
    bindRecord();
    bindUpload();
    bindUrl();
    bindSubmit();
    $('btnCancel').addEventListener('click', function () { window.location.href = '/voices'; });
  });
})();
