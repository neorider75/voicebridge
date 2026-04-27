// VoiceBridge — recordings.js : liste filtrable + lecteur + suppression.

(function () {
  function $(id) { return document.getElementById(id); }
  function $$(sel, r) { return Array.prototype.slice.call((r || document).querySelectorAll(sel)); }

  var currentMode = 'all';
  // Set des IDs sélectionnés (entre les refreshs auto, on tente de
  // préserver la sélection si les recordings existent encore).
  var selectedIds = new Set();

  function fmtDate(iso) {
    if (!iso) return '';
    try { return new Date(iso).toLocaleString('fr-FR'); }
    catch (e) { return iso; }
  }

  function fmtExpires(iso) {
    if (!iso) return '';
    var d = new Date(iso);
    var now = new Date();
    var hours = Math.round((d - now) / 36e5);
    if (hours < 0) return 'Expiré';
    if (hours < 24) return 'Expire dans ' + hours + ' h';
    return 'Expire ' + d.toLocaleDateString('fr-FR');
  }

  function el(tag, attrs, kids) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === 'class') e.className = attrs[k];
      else if (k === 'text') e.textContent = attrs[k];
      else e.setAttribute(k, attrs[k]);
    });
    (kids || []).forEach(function (c) { if (c) e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); });
    return e;
  }

  function badge(mode) {
    var colors = { tts: 'var(--accent)', stt: 'var(--accent3)', live: 'var(--success)' };
    var b = el('span', {}, [mode.toUpperCase()]);
    b.style.background = colors[mode] || 'var(--text3)';
    b.style.color = '#fff';
    b.style.fontSize = '0.65rem';
    b.style.padding = '0.15rem 0.45rem';
    b.style.borderRadius = '8px';
    b.style.fontWeight = '700';
    return b;
  }

  function render(data) {
    $('recStats').textContent = data.total_count + ' fichier(s) · ' + data.total_size_mb + ' Mo';
    var list = $('recList');
    list.innerHTML = '';
    if (!data.recordings.length) {
      list.appendChild(el('div', { class: 'alert alert-info' }, ['Aucun enregistrement disponible. Les fichiers générés (rétention 24 h / 48 h) apparaîtront ici.']));
      // Au cas où la sélection contenait des IDs disparus, on nettoie
      selectedIds.clear();
      updateBulkBar(data.recordings);
      return;
    }
    // Élague la sélection des IDs qui ne sont plus dans la liste (ex :
    // refresh auto après une suppression côté serveur)
    var presentIds = new Set(data.recordings.map(function (r) { return r.id; }));
    Array.from(selectedIds).forEach(function (id) {
      if (!presentIds.has(id)) selectedIds.delete(id);
    });

    data.recordings.forEach(function (r) {
      // Checkbox de sélection
      var cb = el('input', { type: 'checkbox', 'data-rec-id': r.id });
      cb.style.margin = '0 0.5rem 0 0';
      cb.style.flexShrink = '0';
      cb.style.cursor = 'pointer';
      if (selectedIds.has(r.id)) cb.checked = true;

      var nameLine = el('div', { class: 'voice-name', style: 'display:flex;align-items:center;gap:0.5rem;flex-wrap:wrap' }, [
        badge(r.mode || 'tts'),
        (r.voice_language === 'fr' ? '🇫🇷 ' : '🇬🇧 ') + (r.voice_name || ''),
      ]);
      // Badge engine si dispo (TTS uniquement)
      if (r.engine) {
        var engBadge = el('span', { text: r.engine === 'xtts' ? 'XTTS-v2' : 'NeuTTS' });
        engBadge.style.fontSize = '0.62rem';
        engBadge.style.padding = '0.1rem 0.4rem';
        engBadge.style.borderRadius = '6px';
        engBadge.style.background = 'var(--surface3)';
        engBadge.style.color = 'var(--text3)';
        nameLine.appendChild(engBadge);
      }

      var children = [nameLine];

      // Texte du prompt sous le nom (si présent)
      if (r.text_preview) {
        var textBlock = el('div', { text: '« ' + r.text_preview + ' »' });
        textBlock.style.fontSize = '0.78rem';
        textBlock.style.color = 'var(--text2)';
        textBlock.style.fontStyle = 'italic';
        textBlock.style.lineHeight = '1.45';
        textBlock.style.margin = '0.35rem 0';
        textBlock.style.padding = '0.35rem 0.6rem';
        textBlock.style.borderLeft = '2px solid var(--border)';
        children.push(textBlock);
      }

      children.push(el('div', { class: 'voice-meta', text:
        fmtDate(r.created_at) + ' · ' + (r.duration_seconds || 0) + 's · '
        + (r.format || 'wav').toUpperCase() + ' · '
        + (r.quality === 'high' ? 'Haute qualité' : 'Normale') + ' · '
        + (r.size_mb || 0) + ' Mo · ' + fmtExpires(r.expires_at),
      }));

      var info = el('div', { class: 'voice-info' }, children);

      var btnPlay = el('button', { class: 'icon-btn', title: 'Écouter' }, ['▶']);
      var btnDl = el('a', { class: 'icon-btn', title: 'Télécharger', href: '/api/recordings/' + r.id + '/audio', download: '' }, ['⬇']);
      var btnDel = el('button', { class: 'icon-btn danger', title: 'Supprimer' }, ['🗑']);
      var actions = el('div', { class: 'voice-actions' }, [btnPlay, btnDl, btnDel]);
      var player = el('div', { class: 'voice-player', id: 'rec-player-' + r.id }, [
        el('audio', { controls: 'controls', preload: 'none' }),
      ]);
      // Top row = checkbox + info + actions (alignés)
      var top = el('div', { class: 'voice-row', style: 'align-items:flex-start' }, [cb, info, actions]);
      var item = el('div', {}, [top, player]);
      list.appendChild(item);

      cb.addEventListener('change', function () {
        if (cb.checked) selectedIds.add(r.id);
        else selectedIds.delete(r.id);
        updateBulkBar(data.recordings);
      });
      btnPlay.addEventListener('click', function () {
        var audio = player.querySelector('audio');
        var visible = player.classList.contains('visible');
        document.querySelectorAll('.voice-player.visible').forEach(function (p) {
          p.classList.remove('visible');
          var a = p.querySelector('audio'); if (a) a.pause();
        });
        if (!visible) {
          audio.src = '/api/recordings/' + r.id + '/audio';
          player.classList.add('visible');
          audio.play().catch(function () {});
        }
      });
      btnDel.addEventListener('click', function () {
        if (!window.confirm('Supprimer ce fichier ?')) return;
        VB.api.del('/api/recordings/' + r.id).then(function () {
          selectedIds.delete(r.id);
          VB.notify('success', 'Supprimé'); refresh();
        }).catch(function (e) { VB.notify('error', e.message || 'Erreur'); });
      });
    });

    updateBulkBar(data.recordings);
  }

  function updateBulkBar(recordings) {
    var bar = $('recBulkBar');
    var count = $('recSelectedCount');
    var selectAll = $('recSelectAll');
    if (!bar) return;
    var n = selectedIds.size;
    bar.style.display = n > 0 ? 'flex' : 'none';
    count.textContent = n + ' sélectionné(s)';
    // Coche maître = vrai si TOUTES les recordings visibles sont sélectionnées
    if (selectAll && recordings && recordings.length) {
      selectAll.checked = recordings.every(function (r) { return selectedIds.has(r.id); });
    }
  }

  function bindBulkBar() {
    var selectAll = $('recSelectAll');
    var btn = $('recBulkDelete');
    if (selectAll) {
      selectAll.addEventListener('change', function () {
        // Sélectionne / désélectionne TOUS les checkboxes affichés
        var boxes = $$('input[type="checkbox"][data-rec-id]', $('recList'));
        boxes.forEach(function (cb) {
          cb.checked = selectAll.checked;
          var id = cb.getAttribute('data-rec-id');
          if (selectAll.checked) selectedIds.add(id);
          else selectedIds.delete(id);
        });
        updateBulkBar(null);
      });
    }
    if (btn) {
      btn.addEventListener('click', function () {
        var ids = Array.from(selectedIds);
        if (!ids.length) return;
        if (!window.confirm('Supprimer ' + ids.length + ' enregistrement(s) ?')) return;
        btn.disabled = true;
        VB.api.post('/api/recordings/bulk-delete', { ids: ids })
          .then(function (r) {
            selectedIds.clear();
            VB.notify('success', (r.deleted || 0) + ' supprimé(s)');
            refresh();
          })
          .catch(function (e) { VB.notify('error', e.message || 'Erreur'); })
          .finally(function () { btn.disabled = false; });
      });
    }
  }

  function refresh() {
    VB.api.get('/api/recordings?mode=' + currentMode).then(render).catch(function (e) {
      if (e.status === 401) { window.location.href = '/login'; return; }
      VB.notify('error', 'Chargement impossible');
    });
  }

  function bindTabs() {
    $$('.studio-tab').forEach(function (tab) {
      tab.addEventListener('click', function () {
        currentMode = tab.getAttribute('data-mode');
        $$('.studio-tab').forEach(function (t) { t.classList.remove('active'); });
        tab.classList.add('active');
        refresh();
      });
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    bindTabs();
    bindBulkBar();
    refresh();
    setInterval(refresh, 30000);
  });
})();
