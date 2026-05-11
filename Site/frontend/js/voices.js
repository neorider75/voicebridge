// VoiceBridge — voices.js : liste Mes voix + lecteur inline + suppression.

(function () {
  function $(id) { return document.getElementById(id); }
  function el(tag, attrs, kids) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === 'class') e.className = attrs[k];
      else if (k === 'text') e.textContent = attrs[k];
      else e.setAttribute(k, attrs[k]);
    });
    (kids || []).forEach(function (child) {
      if (typeof child === 'string') e.appendChild(document.createTextNode(child));
      else if (child) e.appendChild(child);
    });
    return e;
  }

  function fmtDate(iso) {
    if (!iso) return '';
    try {
      return new Date(iso).toLocaleDateString('fr-FR', { day: '2-digit', month: 'short', year: 'numeric' });
    } catch (e) { return iso; }
  }

  function flagFor(language) { return language === 'fr' ? '🇫🇷' : '🇬🇧'; }

  function statusBadge(v) {
    // status par défaut "ready" pour les voix créées avant l'introduction du
    // champ. encoding/failed sont des états transitoires côté création.
    var status = v.status || 'ready';
    if (status === 'encoding') {
      return el('span', {
        class: 'voice-status encoding',
        title: 'Encodage NeuTTS en cours, ré-essayez dans quelques secondes',
        style: 'background:rgba(245,158,11,0.15);color:var(--warning);border:1px solid var(--warning);padding:0.15rem 0.55rem;border-radius:12px;font-size:0.7rem;font-weight:600',
      }, ['⏳ Encodage…']);
    }
    if (status === 'failed') {
      return el('span', {
        class: 'voice-status failed',
        title: v.error_message || 'Encodage échoué',
        style: 'background:rgba(239,68,68,0.12);color:var(--danger);border:1px solid var(--danger);padding:0.15rem 0.55rem;border-radius:12px;font-size:0.7rem;font-weight:600',
      }, ['❌ Échec']);
    }
    return null;
  }

  function render(voices) {
    var list = $('voiceList');
    list.innerHTML = '';
    if (!voices.length) {
      list.appendChild(el('div', { class: 'alert alert-info', text: 'Aucune voix pour le moment. Cliquez sur "+ Ajouter une voix".' }));
      return;
    }

    voices.forEach(function (v) {
      var status = v.status || 'ready';
      var ready = status === 'ready';

      var nameNode = el('div', { class: 'voice-name', style: 'display:flex;align-items:center;gap:0.5rem;flex-wrap:wrap' }, [
        document.createTextNode(v.name),
      ]);
      var badge = statusBadge(v);
      if (badge) nameNode.appendChild(badge);

      var metaText = ready
        ? 'Ajoutée le ' + fmtDate(v.created_at) + ' · ' + (v.duration_seconds || 0) + 's · ' + (v.backbone || '')
        : (status === 'encoding'
            ? 'Pré-encodage NeuTTS en arrière-plan (~30 s à froid). Vous pouvez attendre ou revenir plus tard.'
            : 'Échec de l\'encodage : ' + (v.error_message || 'raison inconnue') + '. Supprimez et recréez la voix.');

      var info = el('div', { class: 'voice-info' }, [
        nameNode,
        el('div', { class: 'voice-meta', text: metaText }),
      ]);

      var btnPlay = el('button', {
        class: 'icon-btn' + (ready ? '' : ' disabled'),
        title: ready ? 'Écouter' : 'Indisponible — voix non prête',
        'data-action': 'play',
        'aria-disabled': ready ? 'false' : 'true',
        style: ready ? '' : 'opacity:0.4;cursor:not-allowed',
      }, ['▶']);
      var btnEdit = v.protected
        ? el('button', { class: 'icon-btn lock', title: 'Voix protégée' }, ['🔒'])
        : el('button', { class: 'icon-btn', title: 'Modifier', 'data-action': 'edit' }, ['✏️']);
      var btnDel = v.protected
        ? null
        : el('button', { class: 'icon-btn danger', title: 'Supprimer', 'data-action': 'delete' }, ['🗑']);

      var actions = el('div', { class: 'voice-actions' }, [btnPlay, btnEdit].concat(btnDel ? [btnDel] : []));
      var player = el('div', { class: 'voice-player', id: 'player-' + v.id }, [
        el('audio', { controls: 'controls', preload: 'none' }),
      ]);

      var topRow = el('div', { class: 'voice-row' }, [
        el('div', { class: 'voice-flag', text: flagFor(v.language) }),
        info, actions,
      ]);

      var item = el('div', {}, [topRow, player]);
      list.appendChild(item);

      btnPlay.addEventListener('click', function () {
        if (!ready) {
          VB.notify(status === 'encoding' ? 'info' : 'error',
            status === 'encoding'
              ? 'Voix en cours d\'encodage — patientez quelques secondes'
              : 'Encodage échoué pour cette voix');
          return;
        }
        togglePlay(v, item);
      });
      if (!v.protected) {
        btnDel.addEventListener('click', function () { confirmDelete(v); });
        btnEdit.addEventListener('click', function () { VB.notify('info', 'Édition disponible en livraison ultérieure'); });
      }
    });
  }

  // Si au moins une voix est en "encoding", on poll toutes les 3 s pour mettre
  // à jour automatiquement la liste quand le background task finit.
  var pollTimer = null;
  function maybeStartPolling(voices) {
    var hasEncoding = (voices || []).some(function (v) { return v.status === 'encoding'; });
    if (hasEncoding && !pollTimer) {
      pollTimer = setInterval(refresh, 3000);
    } else if (!hasEncoding && pollTimer) {
      clearInterval(pollTimer); pollTimer = null;
    }
  }

  function togglePlay(voice, container) {
    var player = container.querySelector('.voice-player');
    var audio = player.querySelector('audio');
    var visible = player.classList.contains('visible');

    // Ferme tous les autres lecteurs
    document.querySelectorAll('.voice-player.visible').forEach(function (p) {
      p.classList.remove('visible');
      var a = p.querySelector('audio'); if (a) a.pause();
    });

    if (!visible) {
      audio.src = '/api/voices/' + voice.id + '/audio';
      audio.load();
      player.classList.add('visible');
      audio.play().catch(function () { /* autoplay peut être bloqué */ });
    }
  }

  function confirmDelete(voice) {
    if (!window.confirm('Supprimer la voix "' + voice.name + '" ?')) return;
    VB.api.del('/api/voices/' + voice.id)
      .then(function () { VB.notify('success', 'Voix supprimée'); refresh(); })
      .catch(function (e) { VB.notify('error', e.message || 'Suppression impossible'); });
  }

  function refresh() {
    VB.api.get('/api/voices')
      .then(function (d) {
        render(d.voices || []);
        maybeStartPolling(d.voices || []);
      })
      .catch(function (e) {
        if (e.status === 401) { window.location.href = '/login'; return; }
        VB.notify('error', 'Chargement impossible');
      });
  }

  function installNativeVoices() {
    var btn = document.getElementById('btnInstallNative');
    if (!btn) return;
    if (!window.confirm(
        'Télécharger et installer les voix natives par défaut (FR, EN, ES, DE, IT, PT) ?\n\n'
        + 'Source : Wikimedia Commons (Wikipédia parlée, licence CC-BY-SA).\n'
        + 'Téléchargement : 5-10 Mo, 30 s à 1 min selon réseau.\n\n'
        + 'Continuer ?')) return;
    var orig = btn.textContent;
    btn.disabled = true;
    btn.textContent = '⏳ Installation…';
    VB.api.post('/api/voices/native/install')
      .then(function (r) {
        var msg = '✅ ' + (r.installed || []).length + ' voix installée(s)';
        if ((r.skipped || []).length) msg += ' · ' + r.skipped.length + ' déjà présente(s)';
        if ((r.failed || []).length) msg += ' · ⚠️ ' + r.failed.length + ' en échec';
        VB.notify('success', msg);
        if ((r.failed || []).length) {
          console.warn('[voices] native install failures:', r.failed);
        }
        refresh();
      })
      .catch(function (e) {
        VB.notify('error', 'Échec installation : ' + (e.message || e));
      })
      .finally(function () {
        btn.disabled = false;
        btn.textContent = orig;
      });
  }

  // ── Upload manuel voix native ──

  function openNativeUploadModal() {
    var m = document.getElementById('nativeUploadModal');
    if (m) m.style.display = 'flex';
  }

  function closeNativeUploadModal() {
    var m = document.getElementById('nativeUploadModal');
    if (m) m.style.display = 'none';
    // Reset champs
    var file = document.getElementById('nativeUploadFile');
    if (file) file.value = '';
    var nameInput = document.getElementById('nativeUploadName');
    if (nameInput) nameInput.value = '';
  }

  function submitNativeUpload() {
    var lang = document.getElementById('nativeUploadLang').value;
    var name = (document.getElementById('nativeUploadName').value || '').trim();
    var fileInput = document.getElementById('nativeUploadFile');
    var file = fileInput && fileInput.files && fileInput.files[0];

    if (!name) { VB.notify('warning', 'Donne un nom à la voix'); return; }
    if (!file) { VB.notify('warning', 'Choisis un fichier audio'); return; }

    var fd = new FormData();
    fd.append('lang', lang);
    fd.append('name', name);
    fd.append('audio', file);

    var btn = document.getElementById('btnNativeUploadSubmit');
    var orig = btn ? btn.textContent : '';
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Upload…'; }

    fetch('/api/voices/native/upload', {
      method: 'POST', body: fd, credentials: 'same-origin',
    }).then(function (r) {
      return r.json().then(function (j) { return { ok: r.ok, body: j }; });
    }).then(function (res) {
      if (btn) { btn.disabled = false; btn.textContent = orig; }
      if (!res.ok) {
        var msg = (res.body && res.body.detail && res.body.detail.message)
                || (res.body && res.body.detail)
                || 'Upload échoué';
        VB.notify('error', String(msg));
        return;
      }
      VB.notify('success',
        '✅ Voix native importée (' + res.body.duration + 's, '
        + Math.round(res.body.size_bytes / 1024) + ' Ko)');
      closeNativeUploadModal();
      refresh();
    }).catch(function (e) {
      if (btn) { btn.disabled = false; btn.textContent = orig; }
      VB.notify('error', 'Upload échoué : ' + (e.message || e));
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    refresh();
    var btn = document.getElementById('btnInstallNative');
    if (btn) btn.addEventListener('click', installNativeVoices);
    var btnUp = document.getElementById('btnUploadNative');
    if (btnUp) btnUp.addEventListener('click', openNativeUploadModal);
    var btnCancel = document.getElementById('btnNativeUploadCancel');
    if (btnCancel) btnCancel.addEventListener('click', closeNativeUploadModal);
    var btnSub = document.getElementById('btnNativeUploadSubmit');
    if (btnSub) btnSub.addEventListener('click', submitNativeUpload);
    // Clic en dehors du modal → ferme
    var modal = document.getElementById('nativeUploadModal');
    if (modal) modal.addEventListener('click', function (e) {
      if (e.target === modal) closeNativeUploadModal();
    });
  });
})();
