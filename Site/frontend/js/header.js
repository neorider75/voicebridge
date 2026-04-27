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

  var lastStatusData = null;

  function refreshStatus() {
    var badge = $('#statusBadge');
    if (!badge) return;
    VB.api.get('/api/system/status').then(function (data) {
      lastStatusData = data;
      var dot = badge.querySelector('.status-dot');
      if (dot) dot.className = 'status-dot ' + statusDotClass(data.status);
      var label = badge.querySelector('.status-label');
      if (label) label.textContent = statusLabel(data.status);
      badge.dataset.payload = JSON.stringify(data);
      // Si la popover est ouverte, on rafraîchit son contenu en place.
      var popover = document.getElementById('statusPopover');
      if (popover && popover.style.display !== 'none') renderPopover(popover, data);
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

  function ensurePopover() {
    var existing = document.getElementById('statusPopover');
    if (existing) return existing;
    var pop = document.createElement('div');
    pop.id = 'statusPopover';
    pop.style.cssText = [
      'position:absolute', 'top:100%', 'right:0', 'margin-top:0.5rem',
      'min-width:280px', 'max-width:340px',
      'background:var(--surface)', 'border:1px solid var(--border)',
      'border-radius:10px',
      'box-shadow:0 8px 30px rgba(0,0,0,0.15), 0 4px 12px rgba(0,0,0,0.08)',
      'padding:0.85rem 0.95rem', 'z-index:1000',
      'font-size:0.78rem',
      'display:none',
    ].join(';');
    return pop;
  }

  function renderPopover(pop, data) {
    if (!data) {
      pop.innerHTML = '<div style="color:var(--text3)">Chargement…</div>';
      return;
    }
    var ram = data.ram || {};
    var disk = data.storage || {};
    var models = data.models || {};
    var loaded = 0, total = 0;
    var rows = '';
    Object.keys(models).forEach(function (k) {
      total += 1;
      var v = models[k];
      var isLoaded = v === 'loaded';
      if (isLoaded) loaded += 1;
      var dotColor = isLoaded ? 'var(--success)' : 'var(--text3)';
      rows += '<div style="display:flex;align-items:center;gap:0.4rem;padding:0.15rem 0;font-family:\'DM Mono\',monospace;font-size:0.72rem">'
        + '<span style="color:' + dotColor + '">●</span>'
        + '<span>' + k + '</span>'
        + '<span style="color:var(--text3);margin-left:auto">' + v + '</span>'
        + '</div>';
    });
    var summaryColor = loaded === 0 ? 'var(--text3)'
      : loaded === total ? 'var(--success)' : 'var(--warning)';
    var summaryText = loaded === 0 ? 'Veille (' + total + ' inactifs)'
      : loaded === total ? 'Tous chargés (' + loaded + ')'
      : loaded + ' / ' + total + ' chargés';

    pop.innerHTML =
      // RAM
      '<div style="display:flex;justify-content:space-between;font-size:0.7rem;margin-bottom:0.2rem">'
      +   '<span style="color:var(--text3)">RAM</span>'
      +   '<span style="font-family:\'DM Mono\',monospace">'
      +     (ram.used_gb || 0) + ' / ' + (ram.total_gb || 0) + ' Go</span>'
      + '</div>'
      + '<div style="height:4px;background:var(--surface3);border-radius:2px;overflow:hidden;margin-bottom:0.7rem">'
      +   '<div style="height:100%;width:' + (ram.percent || 0) + '%;background:linear-gradient(90deg,var(--accent),var(--accent3))"></div>'
      + '</div>'
      // Storage
      + '<div style="display:flex;justify-content:space-between;font-size:0.7rem;margin-bottom:0.2rem">'
      +   '<span style="color:var(--text3)">Stockage</span>'
      +   '<span style="font-family:\'DM Mono\',monospace">'
      +     (disk.used_gb || 0) + ' / ' + (disk.total_gb || 0) + ' Go</span>'
      + '</div>'
      + '<div style="height:4px;background:var(--surface3);border-radius:2px;overflow:hidden;margin-bottom:0.85rem">'
      +   '<div style="height:100%;width:' + (disk.percent || 0) + '%;background:linear-gradient(90deg,var(--accent),var(--accent3))"></div>'
      + '</div>'
      // Models header
      + '<div style="display:flex;justify-content:space-between;align-items:center;font-size:0.7rem;margin-bottom:0.4rem">'
      +   '<span style="color:var(--text3)">Modèles</span>'
      +   '<span style="color:' + summaryColor + ';font-weight:600">' + summaryText + '</span>'
      + '</div>'
      + rows
      // Footer link
      + '<div style="margin-top:0.7rem;padding-top:0.6rem;border-top:1px solid var(--border)">'
      +   '<a href="/settings" style="color:var(--accent2);font-size:0.72rem;text-decoration:none">⚙ Gérer les modèles dans Réglages →</a>'
      + '</div>';
  }

  function bindStatusBadge() {
    var badge = $('#statusBadge');
    if (!badge) return;
    badge.style.cursor = 'pointer';
    badge.title = "État du serveur — clic pour voir les détails";

    // Le badge est dans un container qui doit être positionné en relative
    // pour ancrer la popover. On wrap si besoin.
    var parent = badge.parentNode;
    if (parent && getComputedStyle(parent).position === 'static') {
      parent.style.position = 'relative';
    }
    var pop = ensurePopover();
    if (!pop.parentNode) (parent || document.body).appendChild(pop);

    badge.addEventListener('click', function (e) {
      e.stopPropagation();
      var isOpen = pop.style.display !== 'none';
      if (isOpen) {
        pop.style.display = 'none';
      } else {
        renderPopover(pop, lastStatusData);
        pop.style.display = 'block';
        // Force un refresh immédiat pour avoir des données fraîches
        refreshStatus();
      }
    });
    // Fermeture au clic extérieur
    document.addEventListener('click', function (e) {
      if (pop.style.display === 'none') return;
      if (badge.contains(e.target) || pop.contains(e.target)) return;
      pop.style.display = 'none';
    });
    // Échap pour fermer
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') pop.style.display = 'none';
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    highlightNav();
    bindLogout();
    bindStatusBadge();
    refreshStatus();
    setInterval(refreshStatus, POLL_INTERVAL);
  });
})();
