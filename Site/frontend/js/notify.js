// VoiceBridge — notify.js : toasts éphémères en haut à droite.
// API : VB.notify('success'|'warning'|'error'|'info', message, ms?)

(function () {
  function ensureStack() {
    var stack = document.getElementById('toastStack');
    if (!stack) {
      stack = document.createElement('div');
      stack.id = 'toastStack';
      stack.className = 'toast-stack';
      document.body.appendChild(stack);
    }
    return stack;
  }

  function notify(kind, message, ms) {
    var stack = ensureStack();
    var t = document.createElement('div');
    t.className = 'toast ' + (kind || 'info');
    t.textContent = message;
    stack.appendChild(t);
    setTimeout(function () {
      t.style.transition = 'opacity 0.3s';
      t.style.opacity = '0';
      setTimeout(function () { t.remove(); }, 300);
    }, ms || 3000);
  }

  window.VB = window.VB || {};
  window.VB.notify = notify;
})();
