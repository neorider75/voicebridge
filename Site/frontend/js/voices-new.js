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

  var mediaRec = null, recChunks = [], recordedMime = 'audio/webm';
  var levelCtx = null, levelAnalyser = null, levelRaf = null;

  // ── Niveau audio temps réel pour l'anneau pulsant autour de la zone micro ──
  // Branché sur le même MediaStream que le MediaRecorder via un AnalyserNode.
  // À chaque frame : calcul du RMS, normalisation grossière (la voix parle
  // typiquement entre 0.05 et 0.15 RMS, donc x6 pour étaler vers 1) et écriture
  // dans la variable CSS --mic-level lue par le box-shadow / transform du DOM.
  function startLevelMeter(stream) {
    try {
      var Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      levelCtx = new Ctx();
      // Safari requiert un resume après création si la page n'a pas eu de
      // user-gesture audio préalable (le clic sur la zone qualifie).
      if (levelCtx.state === 'suspended') levelCtx.resume().catch(function () {});
      var source = levelCtx.createMediaStreamSource(stream);
      levelAnalyser = levelCtx.createAnalyser();
      levelAnalyser.fftSize = 1024;
      levelAnalyser.smoothingTimeConstant = 0.6;
      source.connect(levelAnalyser);
      // Pas de connexion à destination → pas de retour audio dans les enceintes.
      var data = new Float32Array(levelAnalyser.fftSize);
      var zone = $('micZone');
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
      // Web Audio indisponible — l'enregistrement marche toujours, juste sans visu
      console.warn('level meter unavailable', e);
    }
  }

  function stopLevelMeter() {
    if (levelRaf) { cancelAnimationFrame(levelRaf); levelRaf = null; }
    levelAnalyser = null;
    if (levelCtx) {
      try { levelCtx.close(); } catch (e) {}
      levelCtx = null;
    }
    var zone = $('micZone');
    if (zone) zone.style.setProperty('--mic-level', '0');
  }

  // Détermine un MIME type que le navigateur sait *vraiment* enregistrer.
  // Chrome/Firefox : audio/webm (opus) ; Safari : audio/mp4 (aac). Sans ce
  // check, Safari produit du MP4 mais on l'emballe en blob "audio/webm" →
  // <audio> n'arrive pas à décoder et affiche "Erreur".
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
    if (mime.indexOf('wav') >= 0) return 'wav';
    return 'webm';
  }

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
        var mime = pickRecorderMime();
        try {
          mediaRec = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
        } catch (e) {
          // Si le mime explicite est refusé, retombe sur les défauts du navigateur.
          mediaRec = new MediaRecorder(stream);
        }
        // Le navigateur peut renvoyer un mimeType différent de celui demandé
        // (notamment Safari quand on lui passe webm). On lit le vrai utilisé.
        recordedMime = mediaRec.mimeType || mime || 'audio/webm';
        mediaRec.ondataavailable = function (e) { if (e.data.size > 0) recChunks.push(e.data); };
        mediaRec.onstop = function () {
          stopLevelMeter();
          stream.getTracks().forEach(function (t) { t.stop(); });
          recordedBlob = new Blob(recChunks, { type: recordedMime });
          $('micPreview').src = URL.createObjectURL(recordedBlob);
          $('micPreviewWrap').style.display = 'block';
          zone.classList.remove('recording');
          $('micLabel').textContent = '🎤 Cliquez pour ré-enregistrer';
        };
        mediaRec.start();
        startLevelMeter(stream);
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
    // Auto-transcription via Kyutai pour pré-remplir le textarea ref_text.
    // L'utilisateur peut corriger avant de soumettre.
    autoTranscribeUpload(file);
  }

  // Anime une barre de progression linéairement en attendant la réponse
  // serveur. La transcription Kyutai prend ~10 s pour ~10 s d'audio sur
  // CPU (à froid +30 s pour le 1er chargement). On va à 95% en 30 s puis
  // on attend la réponse réelle.
  var uploadTranscribeTween = null;
  function startUploadTranscribeAnim() {
    var bar = $('uploadTranscribeBar');
    var step = $('uploadTranscribeStep');
    var prog = $('uploadTranscribeProgress');
    if (!prog || !bar || !step) {
      console.warn('[voices-new] uploadTranscribeProgress introuvable dans le DOM — '
        + 'HTML probablement en cache. Vide les caches Safari et Cmd+Shift+R.');
      VB.notify('warning', 'Page en cache — videz le cache Safari et rechargez (Cmd+Shift+R).');
      return;
    }
    prog.classList.add('visible');
    bar.style.width = '0%';
    step.textContent = 'Transcription Kyutai en cours… (peut prendre ~10-30 s)';
    var start = Date.now();
    var DURATION = 30000;
    if (uploadTranscribeTween) cancelAnimationFrame(uploadTranscribeTween);
    function tick() {
      var t = Math.min(0.95, (Date.now() - start) / DURATION);
      bar.style.width = (t * 100).toFixed(1) + '%';
      uploadTranscribeTween = requestAnimationFrame(tick);
    }
    tick();
  }
  function endUploadTranscribeAnim(success, label) {
    if (uploadTranscribeTween) { cancelAnimationFrame(uploadTranscribeTween); uploadTranscribeTween = null; }
    var bar = $('uploadTranscribeBar');
    var step = $('uploadTranscribeStep');
    var prog = $('uploadTranscribeProgress');
    bar.style.width = '100%';
    step.textContent = label || (success ? '✅ Transcrit — vérifiez le texte ci-dessous' : '⚠️ Transcription impossible — tapez le texte à la main');
    // Cache la barre après 2 s pour laisser respirer
    setTimeout(function () {
      prog.classList.remove('visible');
      bar.style.width = '0%';
    }, 2000);
  }

  function autoTranscribeUpload(file) {
    var lang = $('langSelect').value;
    startUploadTranscribeAnim();
    var fd = new FormData();
    fd.append('audio', file);
    fd.append('language', lang);
    fetch('/api/stt/transcribe', { method: 'POST', credentials: 'same-origin', body: fd })
      .then(function (r) {
        if (!r.ok) {
          return r.text().then(function (raw) {
            var msg = 'Erreur ' + r.status;
            try {
              var d = JSON.parse(raw);
              msg = (d && d.detail && d.detail.message) || (d && d.message) || msg;
            } catch (e) { /* nop */ }
            throw new Error(msg);
          });
        }
        return r.json();
      })
      .then(function (data) {
        $('uploadRefText').value = data.text || '';
        endUploadTranscribeAnim(true);
      })
      .catch(function (e) {
        endUploadTranscribeAnim(false, '⚠️ ' + (e.message || 'Transcription impossible'));
        VB.notify('warning', 'Auto-transcription échouée — saisissez la transcription à la main si possible');
      });
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

  // Étapes courtes côté client : POST /api/voices ne fait plus que la
  // validation + conversion ffmpeg (~1-2 s), puis renvoie 201 avec
  // status="encoding". L'encode_reference NeuTTS tourne en arrière-plan
  // côté serveur, et la liste /voices affiche un badge "Encodage…" jusqu'à
  // ce que ce soit prêt. Du coup la barre ici n'a plus à durer 30 s.
  var SUBMIT_STEPS = [
    { key: 'upload',  label: 'Téléversement…',     target: 50, duration: 600 },
    { key: 'convert', label: 'Conversion audio…',  target: 90, duration: 1500 },
  ];
  var submitTimer = null;

  function showSubmitUI(busy) {
    // Le bouton est désactivé ET caché : double-clic impossible (la garde
    // `if (btn.disabled) return` en début de click handler couvre la fenêtre
    // de quelques ms entre le click et le DOM hidden, et le hidden empêche
    // tout nouveau clic une fois la transition affichée).
    var submitBtn = $('btnSubmit');
    submitBtn.disabled = !!busy;
    $('submitRow').style.display = busy ? 'none' : 'flex';
    $('submitProgress').style.display = busy ? 'block' : 'none';
  }

  function setSubmitProgress(percent, label) {
    $('submitBar').style.width = Math.min(100, Math.round(percent)) + '%';
    if (label) $('submitStep').textContent = label;
  }

  // Fait avancer la barre de `from%` à `to%` sur `duration` ms (linéaire).
  // Retourne une fonction "abort" qui stoppe l'animation et fige la barre.
  function tweenTo(from, to, duration, onTick) {
    var start = Date.now();
    var stopped = false;
    function step() {
      if (stopped) return;
      var elapsed = Date.now() - start;
      var t = Math.min(1, elapsed / duration);
      var v = from + (to - from) * t;
      onTick(v);
      if (t < 1) submitTimer = requestAnimationFrame(step);
    }
    step();
    return function () { stopped = true; if (submitTimer) cancelAnimationFrame(submitTimer); };
  }

  // Joue les étapes upload→convert→encode en chaîne. La dernière étape
  // (encode) reste en cours tant que le serveur n'a pas répondu — si la
  // réponse arrive avant la fin de l'animation, on saute à 100%.
  function startSubmitAnimation() {
    showSubmitUI(true);
    setSubmitProgress(0, SUBMIT_STEPS[0].label);
    var current = 0;
    var lastTo = 0;
    var stopFn = null;
    function next() {
      if (current >= SUBMIT_STEPS.length) return;
      var s = SUBMIT_STEPS[current];
      $('submitStep').textContent = s.label;
      stopFn = tweenTo(lastTo, s.target, s.duration, function (v) { setSubmitProgress(v); });
      lastTo = s.target;
      current += 1;
      // Programme l'étape suivante après duration (sauf si on est sur la dernière)
      if (current < SUBMIT_STEPS.length) {
        setTimeout(function () { if (stopFn) stopFn(); next(); }, SUBMIT_STEPS[current - 1].duration);
      }
    }
    next();
    return function () { if (stopFn) stopFn(); };
  }

  function finishSubmitAnimation(success) {
    if (submitTimer) cancelAnimationFrame(submitTimer);
    setSubmitProgress(100, success ? '✅ Voix prête' : '❌ Échec');
  }

  function resetSubmitUI() {
    if (submitTimer) cancelAnimationFrame(submitTimer);
    submitTimer = null;
    setSubmitProgress(0, 'Préparation…');
    showSubmitUI(false);
  }

  function bindSubmit() {
    $('btnSubmit').addEventListener('click', function () {
      var btn = $('btnSubmit');
      if (btn.disabled) return;

      var name = $('voiceName').value.trim();
      var lang = $('langSelect').value;
      if (!name) { VB.notify('warning', 'Nom obligatoire'); return; }

      if (currentSource === 'url') {
        if (!pendingUrlVoiceId) { VB.notify('warning', 'Lancez d\'abord l\'extraction'); return; }
        startSubmitAnimation();
        VB.api.post('/api/voices/' + pendingUrlVoiceId + '/confirm')
          .then(function (v) { finishSubmitAnimation(true); done(v); })
          .catch(function (e) {
            finishSubmitAnimation(false);
            resetSubmitUI();
            VB.notify('error', e.message || 'Échec confirmation');
          });
        return;
      }

      var fd = new FormData();
      fd.append('name', name);
      fd.append('language', lang);

      if (currentSource === 'record') {
        if (!recordedBlob) { VB.notify('warning', 'Enregistrez d\'abord votre voix'); return; }
        fd.append('audio_file', recordedBlob, 'recording.' + extForMime(recordedMime));
        // L'utilisateur a lu REF_TEXT[lang] à voix haute → on l'envoie comme
        // ref_text. Sans ça, NeuTTS phonémise "" → IndexError au TTS suivant.
        fd.append('ref_text', REF_TEXT[lang] || '');
      } else if (currentSource === 'upload') {
        var input = $('fileInput');
        if (!input.files.length) { VB.notify('warning', 'Choisissez un fichier'); return; }
        var uploadRef = $('uploadRefText');
        var refText = uploadRef ? uploadRef.value.trim() : '';
        if (!refText) {
          // Sans ref_text, NeuTTS plantera ou produira de l'audio dégradé au
          // 1er TTS. On bloque ici plutôt que créer une voix inutilisable.
          VB.notify('warning',
            'La transcription est vide. Attendez l\'auto-transcription ou tapez le texte que dit l\'audio.');
          if (uploadRef) uploadRef.focus();
          return;
        }
        fd.append('audio_file', input.files[0]);
        fd.append('ref_text', refText);
      }

      startSubmitAnimation();
      fetch('/api/voices', {
        method: 'POST',
        credentials: 'same-origin',
        body: fd,
      }).then(function (r) {
        if (!r.ok) {
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
        return r.json();
      }).then(function (v) {
        finishSubmitAnimation(true);
        done(v);
      }).catch(function (e) {
        finishSubmitAnimation(false);
        resetSubmitUI();
        VB.notify('error', e.message || 'Création impossible');
      });
    });
  }

  function done(serverVoice) {
    // serverVoice (optionnel) = le payload renvoyé par POST /api/voices.
    // Si status="encoding", on l'indique dans la carte de validation pour
    // expliquer pourquoi la voix n'est pas encore utilisable dans le Studio.
    var card = $('submitDone');
    if (card) {
      if (serverVoice && serverVoice.status === 'encoding') {
        card.innerHTML =
          '<div style="font-size:1.6rem;margin-bottom:0.25rem">⏳</div>' +
          '<div><strong>Voix créée — encodage en arrière-plan</strong></div>' +
          '<div style="font-size:0.78rem;color:var(--text2);margin-top:0.25rem">' +
          'Visible dans la liste avec un badge « Encodage… ». Utilisable d\'ici ~30 s.</div>';
        card.className = 'alert alert-warning';
      }
      card.style.display = 'block';
    }
    VB.notify(
      serverVoice && serverVoice.status === 'encoding' ? 'info' : 'success',
      serverVoice && serverVoice.status === 'encoding'
        ? 'Voix créée — encodage en cours en arrière-plan'
        : 'Voix ajoutée'
    );
    setTimeout(function () { window.location.href = '/voices'; }, 1500);
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
