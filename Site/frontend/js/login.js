// VoiceBridge — login.js
// POST /api/auth/login → si ok, redirection vers /.
// Affiche les tentatives restantes et le retry-after en cas de lockout.

(function () {
  document.addEventListener('DOMContentLoaded', function () {
    const input = document.getElementById('pwInput');
    const btn = document.getElementById('loginBtn');
    const err = document.getElementById('loginError');

    function showError(text) {
      err.textContent = text;
      err.classList.add('visible');
    }
    function clearError() {
      err.textContent = '';
      err.classList.remove('visible');
    }

    function submit() {
      clearError();
      const password = input.value;
      if (!password) {
        showError('Veuillez saisir votre mot de passe.');
        return;
      }
      btn.disabled = true;
      btn.textContent = '…';
      VB.api.post('/api/auth/login', { password: password })
        .then(function () {
          window.location.href = '/';
        })
        .catch(function (e) {
          btn.disabled = false;
          btn.textContent = 'Accéder →';
          if (e.status === 429) {
            const sec = (e.payload && e.payload.retry_after) || 60;
            showError('Trop de tentatives. Réessayez dans ' + sec + ' s.');
            return;
          }
          if (e.status === 401 && e.payload && typeof e.payload.remaining_attempts === 'number') {
            showError('Mot de passe incorrect · Tentatives restantes : ' + e.payload.remaining_attempts);
            return;
          }
          showError(e.message || 'Erreur de connexion');
        });
    }

    btn.addEventListener('click', submit);
    input.addEventListener('keydown', function (event) {
      if (event.key === 'Enter') submit();
    });
    input.focus();
  });
})();
