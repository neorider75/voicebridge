// VoiceBridge — progress.js
//
// Helper réutilisable pour s'abonner à /ws/progress/{task_id} et invoquer
// des callbacks sur chaque update + done/error.
//
// Usage typique :
//
//   VB.progress.subscribe(taskId, {
//     onProgress: (snap) => {
//       updateBar(snap.progress_percent, snap.current_step);
//     },
//     onDone: (snap) => {
//       VB.notify('success', 'Terminé !');
//       refreshList();
//     },
//     onError: (snap) => {
//       VB.notify('error', snap.error || 'Échec');
//     },
//   });
//
// Annulation côté client : appeler unsub().close()

(function () {
  if (typeof VB === 'undefined') {
    window.VB = {};
  }

  function subscribe(taskId, handlers) {
    if (!taskId) {
      console.warn('[progress] subscribe sans task_id');
      return { close: function () {} };
    }
    var proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    var url = proto + '//' + window.location.host + '/ws/progress/' + encodeURIComponent(taskId);
    var ws = new WebSocket(url);
    var done = false;

    ws.addEventListener('message', function (e) {
      var snap;
      try { snap = JSON.parse(e.data); } catch (err) { return; }
      if (snap.status === 'not_found') {
        done = true;
        if (handlers.onError) handlers.onError({ error: 'task_not_found', task_id: taskId });
        try { ws.close(); } catch (er) {}
        return;
      }
      if (handlers.onProgress) handlers.onProgress(snap);
      if (snap.status === 'done') {
        done = true;
        if (handlers.onDone) handlers.onDone(snap);
        try { ws.close(); } catch (er) {}
      } else if (snap.status === 'error') {
        done = true;
        if (handlers.onError) handlers.onError(snap);
        try { ws.close(); } catch (er) {}
      }
    });

    ws.addEventListener('error', function () {
      if (!done && handlers.onError) {
        handlers.onError({ error: 'ws_error', task_id: taskId });
      }
    });

    ws.addEventListener('close', function () {
      if (!done && handlers.onClose) handlers.onClose();
    });

    return {
      close: function () {
        try { ws.close(); } catch (e) {}
      },
    };
  }

  // ── Helper : crée une barre de progression DOM standard ──
  // Usage : VB.progress.attachBar(container, taskId, { onDone, onError });
  function attachBar(container, taskId, handlers) {
    container.innerHTML = '<div class="progress-bar-wrap" style="background:var(--surface3);height:8px;border-radius:99px;overflow:hidden">' +
                         '<div class="progress-bar-fill" style="height:100%;width:0%;background:var(--accent);transition:width 0.3s"></div>' +
                         '</div>' +
                         '<div class="progress-step" style="margin-top:0.4rem;font-size:0.78rem;color:var(--text3);font-family:\'DM Mono\',monospace"></div>';
    var fill = container.querySelector('.progress-bar-fill');
    var step = container.querySelector('.progress-step');
    return subscribe(taskId, {
      onProgress: function (snap) {
        if (fill) fill.style.width = (snap.progress_percent || 0) + '%';
        if (step) step.textContent = snap.current_step || '';
      },
      onDone: function (snap) {
        if (fill) fill.style.width = '100%';
        if (step) step.textContent = '✅ ' + (snap.current_step || 'Terminé');
        if (handlers && handlers.onDone) handlers.onDone(snap);
      },
      onError: function (snap) {
        if (step) step.textContent = '❌ ' + (snap.error || 'Erreur');
        if (handlers && handlers.onError) handlers.onError(snap);
      },
    });
  }

  VB.progress = {
    subscribe: subscribe,
    attachBar: attachBar,
  };
})();
