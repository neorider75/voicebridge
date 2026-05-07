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

  // Bouton-barre-de-progression : transforme un .btn en barre qui se remplit
  // de 0 à 95 % linéairement sur `durationMs` puis stagne. Le caller appelle
  // la fonction stop() retournée à la fin (succès ou erreur) pour finaliser
  // (saute à 100% puis nettoie après 300ms). Utilise la classe CSS
  // `.btn-progress` + variable `--progress` (cf. base.css).
  function startBtnProgress(btn, durationMs) {
    btn.classList.add('btn-progress');
    btn.style.setProperty('--progress', '0%');
    var start = Date.now();
    var raf = null;
    var stopped = false;
    function tick() {
      if (stopped) return;
      var t = Math.min(0.95, (Date.now() - start) / durationMs);
      btn.style.setProperty('--progress', (t * 100).toFixed(1) + '%');
      raf = requestAnimationFrame(tick);
    }
    tick();
    return function stop() {
      stopped = true;
      if (raf) cancelAnimationFrame(raf);
      btn.style.setProperty('--progress', '100%');
      setTimeout(function () {
        btn.classList.remove('btn-progress');
        btn.style.removeProperty('--progress');
      }, 300);
    };
  }

  // ── Sub-tabs (le switch TTS/STT est géré par studio-stt.js) ──
  function bindStudioTabs() {
    // Notification "disponible plus tard" pour les onglets disabled (Live)
    $$('.studio-tab.disabled').forEach(function (tab) {
      tab.addEventListener('click', function () {
        VB.notify('info', 'Disponible dans une prochaine livraison');
      });
    });
  }

  // ── Radio groups (format / qualité / rétention / engine) ──
  function bindRadioGroups() {
    $$('.radio-group').forEach(function (group) {
      var name = group.getAttribute('data-name');
      $$('.radio-option', group).forEach(function (opt) {
        opt.addEventListener('click', function () {
          $$('.radio-option', group).forEach(function (o) { o.classList.remove('selected'); });
          opt.classList.add('selected');
          group.dataset.value = opt.getAttribute('data-value');
          // Quand l'engine change, on (dé)grise le groupe Qualité — XTTS-v2
          // n'a qu'un seul niveau, le radio Qualité ne fait rien si engine=xtts.
          if (name === 'engine') updateQualityVisibility();
        });
      });
    });
    updateQualityVisibility();  // initial state
  }

  function updateQualityVisibility() {
    var engine = readRadio('engine') || 'neutts';
    var qualityField = document.querySelector('.radio-group[data-name="quality"]');
    if (!qualityField) return;
    var fieldWrap = qualityField.closest('.field') || qualityField;
    var isXtts = engine === 'xtts';
    fieldWrap.style.opacity = isXtts ? '0.4' : '';
    fieldWrap.style.pointerEvents = isXtts ? 'none' : '';
    var label = fieldWrap.querySelector('label');
    if (label) {
      label.textContent = isXtts
        ? 'Qualité (NeuTTS uniquement — ignoré pour XTTS)'
        : 'Qualité (NeuTTS uniquement)';
    }
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
      var engine = readRadio('engine') || 'neutts';

      var btn = $('btnGenerate');
      btn.disabled = true;
      btn.textContent = engine === 'xtts' ? '⏳ Génération XTTS… (~30-60s)' : '⏳ Génération…';
      // Barre de progression dans le bouton (linear tween 0→95% sur la durée
      // attendue, puis saut à 100% quand la réponse arrive).
      var stopProgress = startBtnProgress(btn, engine === 'xtts' ? 45000 : 12000);

      var payload = {
        text: text, voice_id: voiceId, format: format,
        quality: quality, retention: retention, engine: engine,
      };

      // ── V3 : params traduction optionnels ──
      var translateOn = $('ttsTranslateToggle') && $('ttsTranslateToggle').checked;
      if (translateOn) {
        payload.translate = true;
        payload.source_lang = $('ttsSourceLang') ? $('ttsSourceLang').value : 'fr';
        payload.target_lang = $('ttsTargetLang') ? $('ttsTargetLang').value : 'en';
        payload.translation_provider = $('ttsProvider') ? $('ttsProvider').value : 'opus-mt-cpu';
        var briefing = $('ttsBriefing') ? $('ttsBriefing').value.trim() : '';
        var prov = payload.translation_provider;
        if ((prov === 'gpt-4o-mini' || prov === 'gpt-4o') && briefing) {
          payload.briefing = briefing;
        }
      }

      // Pour rétention "session" : binaire direct. Pour 24h/48h : JSON.
      fetch('/api/tts/generate', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      }).then(function (r) {
        var ct = r.headers.get('content-type') || '';
        if (!r.ok) {
          // r.json() sur un body plain-text "Internal Server Error" lève un
          // SyntaxError dont Safari formate le message en "The string did not
          // match the expected pattern." → cryptique. On lit en text() puis
          // best-effort JSON parse pour exposer le vrai message si dispo.
          return r.text().then(function (raw) {
            var msg = 'Erreur ' + r.status;
            try {
              var d = JSON.parse(raw);
              msg = (d && d.detail && d.detail.message)
                || (d && d.message)
                || (d && d.detail && typeof d.detail === 'string' ? d.detail : null)
                || msg;
            } catch (e) {
              if (raw && raw.length < 200) msg = msg + ' · ' + raw;
            }
            throw new Error(msg);
          });
        }
        // Si traduction utilisée, on la récupère soit du JSON (rétention),
        // soit du header X-Translation-Meta (mode session/streaming).
        var translationMeta = null;
        try {
          var hdr = r.headers.get('X-Translation-Meta');
          if (hdr) translationMeta = JSON.parse(hdr);
        } catch (e) { /* ignore */ }

        if (ct.indexOf('application/json') >= 0) {
          return r.json().then(function (d) {
            if (d.translation) translationMeta = d.translation;
            showResult({
              url: d.url, format: format, retention: retention,
              expires_at: d.expires_at, translation: translationMeta,
            });
          });
        }
        return r.blob().then(function (blob) {
          var url = URL.createObjectURL(blob);
          showResult({
            url: url, format: format, retention: 'session', blob: blob,
            translation: translationMeta,
          });
        });
      }).catch(function (e) {
        VB.notify('error', e.message || 'Erreur de génération');
      }).finally(function () {
        if (stopProgress) stopProgress();
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

    // Affichage de la traduction si utilisée
    var wrap = $('ttsTranslatedWrap');
    if (payload.translation && payload.translation.translated_text) {
      var t = payload.translation;
      $('ttsTranslatedText').textContent = t.translated_text;
      $('ttsTranslatedMeta').textContent =
        '🌐 ' + (t.src_lang || '?') + ' → ' + (t.tgt_lang || '?')
        + ' · ' + (t.provider || '?')
        + (t.latency_ms ? ' · ' + t.latency_ms + 'ms' : '')
        + (t.cost_eur ? ' · ' + t.cost_eur.toFixed(4) + '€' : '');
      wrap.style.display = '';
    } else if (wrap) {
      wrap.style.display = 'none';
    }

    VB.notify('success', 'Génération terminée');
    setStepDone(2, true);
  }

  // ── V3 : binding du toggle traduction TTS ──
  function bindTtsTranslateToggle() {
    var toggle = $('ttsTranslateToggle');
    var opts = $('ttsTranslateOptions');
    var providerSel = $('ttsProvider');
    var briefingField = $('ttsBriefingField');
    if (!toggle) return;

    toggle.addEventListener('change', function () {
      if (opts) opts.style.display = toggle.checked ? '' : 'none';
    });

    if (providerSel) {
      providerSel.addEventListener('change', function () {
        var p = providerSel.value;
        var isGpt = p === 'gpt-4o-mini' || p === 'gpt-4o';
        if (briefingField) briefingField.style.display = isGpt ? '' : 'none';
      });
    }
  }

  document.addEventListener('DOMContentLoaded', function () {
    bindStudioTabs();
    bindRadioGroups();
    bindCounter();
    bindGenerate();
    bindTtsTranslateToggle();
    loadVoices();
  });
})();
