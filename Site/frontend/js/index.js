// VoiceBridge — index.js (dashboard placeholder livraison 1).
// - Polling /api/system/status toutes les 5 s.
// - Bouton déconnexion.

(function () {
  const $ = function (id) { return document.getElementById(id); };

  function pad(n) { return n < 10 ? '0' + n : '' + n; }
  function fmtUptime(s) {
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    return pad(h) + ':' + pad(m) + ':' + pad(sec);
  }

  function renderStatus(s) {
    const el = $('status');
    el.textContent = '';
    function row(label, value) {
      const div = document.createElement('div');
      const k = document.createElement('span');
      k.style.color = 'var(--text3)';
      k.textContent = label + ' : ';
      const v = document.createElement('span');
      v.textContent = value;
      div.appendChild(k);
      div.appendChild(v);
      el.appendChild(div);
    }
    row('Version', s.version);
    row('Statut', s.status);
    row('Uptime', fmtUptime(s.uptime_seconds));
    row('RAM', s.ram.used_gb + ' / ' + s.ram.total_gb + ' Go (' + s.ram.percent + '%)');
    row('Stockage', s.storage.used_gb + ' / ' + s.storage.total_gb + ' Go (' + s.storage.percent + '%)');
  }

  function refresh() {
    VB.api.get('/api/system/status')
      .then(renderStatus)
      .catch(function (e) {
        if (e.status === 401) { window.location.href = '/login'; return; }
        $('status').textContent = 'Erreur : ' + (e.message || 'inconnue');
      });
  }

  document.addEventListener('DOMContentLoaded', function () {
    $('logoutBtn').addEventListener('click', function () {
      VB.api.post('/api/auth/logout').finally(function () {
        window.location.href = '/login';
      });
    });
    refresh();
    setInterval(refresh, 5000);
  });
})();
