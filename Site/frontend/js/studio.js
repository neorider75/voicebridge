// VoiceBridge — studio.js : Studio TTS (livraison 2). STT et Live grisés.
//
// Steps :
//   1. Texte (max 5000 chars)
//   2. Génération (voix, format, qualité, rétention) → POST /api/tts/generate
//   3. Résultat (lecteur + télécharger)

(function () {
  function $(id) { return document.getElementById(id); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  var MAX_CHARS = 5000;
  var voices = [];

  // ── Sub-tabs (TTS only en L2) ──
  function bindStudioTabs() {
    $$('.studio-tab').forEach(function (tab) {
      tab.addEventListener('click', function () {
        if (tab.classList.contains('disabled')) {
          VB.notify('info', 'Disponible dans une prochaine livraison');
          return;
        }
      });
    });
  }

  // ── Radio groups (format / qualité / rétention) ──
  function bindRadioGroups() {
    $$('.radio-group').forEach(function (group) {
      var name = group.getAttribute('data-name');
      $$('.radio-option', group).forEach(function (opt) {
        opt.addEventListener('click', function () {
          $$('.radio-option', group).forEach(function (o) { o.classList.remove('selected'); });
          opt.classList.add('selected');
          group.dataset.value = opt.getAttribute('data-value');
        });
      });
    });
  }
  function readRadio(name) {
    var group = document.querySelector('.radio-group[data-name="' + name + '"]');
    return group ? (group.dataset.value || group.querySelector('.radio-option.selected')?.getAttribute('data-value')) : null;
  }

  // ── Step locking ──
  function setStepLocked(num, locked) {
    var step = document.querySelector('.step[data-step="' + num + '"]');
    if (step) step.classList.toggle('locked', !!locked);
  }
  function setStepDone(num, done) {
    var step = document.querySelector('.step[data-step="' + num + '"]');
    if (step) step.classList.toggle('done', !!done);
  }

  // ── Charge la liste des voix ──
  function loadVoices() {
    return VB.api.get('/api/voices').then(function (d) {
      voices = d.voices || [];
      var sel = $('voiceSelect');
      sel.innerHTML = '';
      if (!voices.length) {
        var opt = document.createElement('option');
        opt.value = '';
        opt.textContent = 'Aucune voix disponible (ajoutez-en une)';
        sel.appendChild(opt);
        return;
      }
      voices.forEach(function (v) {
        var opt = document.createElement('option');
        opt.value = v.id;
        var flag = v.language === 'fr' ? '🇫🇷' : '🇬🇧';
        opt.textContent = flag + ' ' + v.name;
        sel.appendChild(opt);
      });
    }).catch(function (e) {
      if (e.status === 401) { window.location.href = '/login'; return; }
      VB.notify('error', 'Impossible de charger les voix');
    });
  }

  // ── Compteur de caractères ──
  function bindCounter() {
    var ta = $('ttsText');
    var counter = $('ttsCounter');
    function update() {
      var n = ta.value.length;
      counter.textContent = n + ' / ' + MAX_CHARS;
      counter.classList.toggle('warn', n > MAX_CHARS - 200);
      setStepLocked(2, n === 0);
    }
    ta.addEventListener('input', update);
    update();
  }

  // ── Génération ──
  function bindGenerate() {
    $('btnGenerate').addEventListener('click', function () {
      var text = $('ttsText').value.trim();
      if (!text) { VB.notify('warning', 'Saisissez un texte'); return; }

      var voiceId = $('voiceSelect').value;
      if (!voiceId) { VB.notify('warning', 'Choisissez une voix'); return; }

      var format = readRadio('format') || 'wav';
      var quality = readRadio('quality') || 'high';
      var retention = readRadio('retention') || 'session';

      var btn = $('btnGenerate');
      btn.disabled = true;
      btn.textContent = '⏳ Génération…';

      var payload = { text: text, voice_id: voiceId, format: format, quality: quality, retention: retention };

      // Pour rétention "session" : binaire direct. Pour 24h/48h : JSON.
      fetch('/api/tts/generate', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      }).then(function (r) {
        var ct = r.headers.get('content-type') || '';
        if (!r.ok) {
          return r.json().then(function (d) { throw new Error(d.message || 'Erreur ' + r.status); });
        }
        if (ct.indexOf('application/json') >= 0) {
          return r.json().then(function (d) { showResult({ url: d.url, format: format, retention: retention, expires_at: d.expires_at }); });
        }
        return r.blob().then(function (blob) {
          var url = URL.createObjectURL(blob);
          showResult({ url: url, format: format, retention: 'session', blob: blob });
        });
      }).catch(function (e) {
        VB.notify('error', e.message || 'Erreur de génération');
      }).finally(function () {
        btn.disabled = false;
        btn.textContent = '🎙 Générer';
      });
    });
  }

  function showResult(payload) {
    setStepDone(2, true);
    setStepLocked(3, false);

    var audio = $('resultAudio');
    audio.src = payload.url;
    audio.load();

    var dl = $('btnDownload');
    var ext = payload.format === 'mp3' ? 'mp3' : 'wav';
    dl.onclick = function () {
      var a = document.createElement('a');
      a.href = payload.url;
      a.download = 'voicebridge-' + Date.now() + '.' + ext;
      document.body.appendChild(a);
      a.click();
      a.remove();
    };

    var notice = $('resultNotice');
    if (payload.retention === 'session') {
      notice.className = 'alert alert-warning';
      notice.textContent = '⚠️ Session uniquement — Téléchargez maintenant si vous souhaitez conserver ce fichier';
    } else {
      notice.className = 'alert alert-success';
      notice.textContent = '✅ Enregistré jusqu\'au ' + payload.expires_at;
    }

    VB.notify('success', 'Génération terminée');
    setStepDone(2, true);
  }

  document.addEventListener('DOMContentLoaded', function () {
    bindStudioTabs();
    bindRadioGroups();
    bindCounter();
    bindGenerate();
    loadVoices();
  });
})();
