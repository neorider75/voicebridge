// VoiceBridge — theme.js
// Gestion du thème clair/sombre persisté en localStorage.
// Compatible CSP `script-src 'self'` (aucun inline JS).

(function () {
  const STORAGE_KEY = 'vb-theme';
  const root = document.documentElement;

  function applyTheme(name) {
    root.setAttribute('data-theme', name);
    localStorage.setItem(STORAGE_KEY, name);
    document.querySelectorAll('[data-theme-toggle]').forEach(function (el) {
      el.textContent = name === 'dark' ? '☀️' : '🌙';
    });
  }

  function toggle() {
    const cur = root.getAttribute('data-theme') || 'light';
    applyTheme(cur === 'dark' ? 'light' : 'dark');
  }

  // Init au chargement
  const saved = localStorage.getItem(STORAGE_KEY) || 'light';
  applyTheme(saved);

  // Bind clics
  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('[data-theme-toggle]').forEach(function (el) {
      el.addEventListener('click', toggle);
    });
  });

  window.VB = window.VB || {};
  window.VB.toggleTheme = toggle;
})();
