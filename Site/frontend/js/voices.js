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

  function render(voices) {
    var list = $('voiceList');
    list.innerHTML = '';
    if (!voices.length) {
      list.appendChild(el('div', { class: 'alert alert-info', text: 'Aucune voix pour le moment. Cliquez sur "+ Ajouter une voix".' }));
      return;
    }

    voices.forEach(function (v) {
      var info = el('div', { class: 'voice-info' }, [
        el('div', { class: 'voice-name', text: v.name }),
        el('div', { class: 'voice-meta', text: 'Ajoutée le ' + fmtDate(v.created_at) + ' · ' + (v.duration_seconds || 0) + 's · ' + (v.backbone || '') }),
      ]);

      var btnPlay = el('button', { class: 'icon-btn', title: 'Écouter', 'data-action': 'play' }, ['▶']);
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

      btnPlay.addEventListener('click', function () { togglePlay(v, item); });
      if (!v.protected) {
        btnDel.addEventListener('click', function () { confirmDelete(v); });
        btnEdit.addEventListener('click', function () { VB.notify('info', 'Édition disponible en livraison ultérieure'); });
      }
    });
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
      .then(function (d) { render(d.voices || []); })
      .catch(function (e) {
        if (e.status === 401) { window.location.href = '/login'; return; }
        VB.notify('error', 'Chargement impossible');
      });
  }

  document.addEventListener('DOMContentLoaded', refresh);
})();
