// VoiceBridge — header.js
// Header commun : logo, theme toggle, status badge polling, déconnexion.
// Highlight automatique de la nav selon window.location.pathname.

(function () {
  var POLL_INTERVAL = 5000;

  function $(sel) { return document.querySelector(sel); }
  function $$(sel) { return Array.prototype.slice.call(document.querySelectorAll(sel)); }

  function highlightNav() {
    var path = window.location.pathname;
    $$('.nav-item').forEach(function (el) {
      var target = el.getAttribute('data-nav');
      if (!target) return;
      // Match exact ou préfixe (ex: /voices et /voices/new → "voices")
      var matches = (path === '/' && target === '/studio')
        || path === target
        || (target !== '/' && path.indexOf(target) === 0);
      el.classList.toggle('active', !!matches);
    });
  }

  function statusLabel(s) {
    if (!s) return 'Erreur';
    if (s === 'ready') return 'Prêt';
    if (s === 'idle') return 'Veille';
    if (s === 'warming_up') return 'Préchauffage';
    return 'Erreur';
  }

  function statusDotClass(s) {
    if (s === 'ready') return '';
    if (s === 'idle') return 'idle';
    if (s === 'warming_up') return 'warm';
    return 'err';
  }

  function refreshStatus() {
    var badge = $('#statusBadge');
    if (!badge) return;
    VB.api.get('/api/system/status').then(function (data) {
      var dot = badge.querySelector('.status-dot');
      if (dot) dot.className = 'status-dot ' + statusDotClass(data.status);
      var label = badge.querySelector('.status-label');
      if (label) label.textContent = statusLabel(data.status);
      badge.dataset.payload = JSON.stringify(data);
    }).catch(function (e) {
      if (e.status === 401) { window.location.href = '/login'; return; }
    });
  }

  function bindLogout() {
    var btn = $('#logoutBtn');
    if (!btn) return;
    btn.addEventListener('click', function () {
      VB.api.post('/api/auth/logout').finally(function () {
        window.location.href = '/login';
      });
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    highlightNav();
    bindLogout();
    refreshStatus();
    setInterval(refreshStatus, POLL_INTERVAL);
  });
})();
