// VoiceBridge — recordings.js : liste filtrable + lecteur + suppression.

(function () {
  function $(id) { return document.getElementById(id); }
  function $$(sel, r) { return Array.prototype.slice.call((r || document).querySelectorAll(sel)); }

  var currentMode = 'all';

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
      return;
    }
    data.recordings.forEach(function (r) {
      var info = el('div', { class: 'voice-info' }, [
        el('div', { class: 'voice-name', style: 'display:flex;align-items:center;gap:0.5rem' }, [
          badge(r.mode || 'tts'),
          (r.voice_language === 'fr' ? '🇫🇷 ' : '🇬🇧 ') + (r.voice_name || ''),
        ]),
        el('div', { class: 'voice-meta', text:
          fmtDate(r.created_at) + ' · ' + (r.duration_seconds || 0) + 's · '
          + (r.format || 'wav').toUpperCase() + ' · '
          + (r.quality === 'high' ? 'Haute qualité' : 'Normale') + ' · '
          + (r.size_mb || 0) + ' Mo · ' + fmtExpires(r.expires_at),
        }),
      ]);

      var btnPlay = el('button', { class: 'icon-btn', title: 'Écouter' }, ['▶']);
      var btnDl = el('a', { class: 'icon-btn', title: 'Télécharger', href: '/api/recordings/' + r.id + '/audio', download: '' }, ['⬇']);
      var btnDel = el('button', { class: 'icon-btn danger', title: 'Supprimer' }, ['🗑']);
      var actions = el('div', { class: 'voice-actions' }, [btnPlay, btnDl, btnDel]);
      var player = el('div', { class: 'voice-player', id: 'rec-player-' + r.id }, [
        el('audio', { controls: 'controls', preload: 'none' }),
      ]);
      var top = el('div', { class: 'voice-row' }, [info, actions]);
      var item = el('div', {}, [top, player]);
      list.appendChild(item);

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
          VB.notify('success', 'Supprimé'); refresh();
        }).catch(function (e) { VB.notify('error', e.message || 'Erreur'); });
      });
    });
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
    refresh();
    setInterval(refresh, 30000);
  });
})();
