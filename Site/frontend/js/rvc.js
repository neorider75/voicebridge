// VoiceBridge — rvc.js
// Page /rvc : liste les modèles RVC + onglet tutoriel + actions test/delete.

(function () {
  function $(id) { return document.getElementById(id); }
  function $$(sel, r) { return Array.prototype.slice.call((r || document).querySelectorAll(sel)); }

  function bindTabs() {
    $$('.source-tab').forEach(function (t) {
      t.addEventListener('click', function () {
        $$('.source-tab').forEach(function (x) { x.classList.remove('active'); });
        t.classList.add('active');
        $$('.source-content').forEach(function (c) { c.classList.remove('active'); });
        var target = document.querySelector('.source-content[data-tab="' + t.dataset.tab + '"]');
        if (target) target.classList.add('active');
      });
    });
  }

  function escapeHtml(s) {
    if (s == null) return '';
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function statusBadge(status) {
    var map = {
      uploading: { color: 'var(--warning)', icon: '⬆️', label: 'Upload en cours' },
      validating: { color: 'var(--text3)', icon: '🔍', label: 'Validation' },
      active: { color: 'var(--success)', icon: '✅', label: 'Actif' },
      failed: { color: 'var(--error)', icon: '❌', label: 'Échec' },
    };
    var s = map[status] || { color: 'var(--text3)', icon: '?', label: status || 'Inconnu' };
    return '<span style="display:inline-flex;align-items:center;gap:0.3rem;padding:0.15rem 0.5rem;border-radius:99px;background:' + s.color + ';opacity:0.85;color:white;font-size:0.68rem;font-weight:600">' +
            s.icon + ' ' + s.label + '</span>';
  }

  function load() {
    return VB.api.get('/api/rvc/models').then(function (d) {
      render(d.models || []);
    }).catch(function (err) {
      $('rvcModelsList').innerHTML = '<div class="alert alert-warning">' +
        '⚠️ Impossible de charger les modèles : ' + escapeHtml(err.message || err) + '</div>';
    });
  }

  function render(models) {
    $('rvcCount').textContent =
      models.length === 0 ? 'Aucun modèle pour l\'instant.'
                          : models.length + ' modèle' + (models.length > 1 ? 's' : '');
    var list = $('rvcModelsList');
    list.innerHTML = '';
    if (!models.length) {
      list.innerHTML = '<div class="card" style="text-align:center;padding:2rem;color:var(--text3)">' +
        '🎯 Importe ton premier .pth pour activer le mode <strong>Hybride accent natif</strong> en Live.<br>' +
        '<a href="/rvc-import" style="color:var(--accent2);margin-top:0.75rem;display:inline-block">+ Importer un .pth</a></div>';
      return;
    }
    models.forEach(function (m) {
      var card = document.createElement('div');
      card.className = 'card';
      card.style.marginBottom = '0.75rem';
      var sizeStr = (m.size_mb || 0).toFixed(1) + ' Mo';
      card.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:1rem">' +
                       '<div style="flex:1;min-width:0">' +
                         '<div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.4rem">' +
                           '<div style="font-weight:700;font-size:0.95rem">' + escapeHtml(m.name) + '</div>' +
                           statusBadge(m.status) +
                         '</div>' +
                         (m.description ? '<div style="font-size:0.78rem;color:var(--text2);margin-bottom:0.4rem">' + escapeHtml(m.description) + '</div>' : '') +
                         '<div style="font-size:0.7rem;color:var(--text3);font-family:\'DM Mono\',monospace">' +
                            'sr=' + (m.sample_rate || '?') + ' Hz · v' + (m.version || '?') + ' · ' + sizeStr +
                            ' · ' + (m.created_at || '') +
                         '</div>' +
                         (m.error_message ? '<div style="margin-top:0.4rem;font-size:0.72rem;color:var(--error)">⚠️ ' + escapeHtml(m.error_message) + '</div>' : '') +
                       '</div>' +
                       '<div style="display:flex;gap:0.4rem;flex-shrink:0;align-items:flex-start">' +
                         (m.status === 'active' ?
                           '<button class="btn btn-secondary btn-test" data-id="' + m.id + '" style="padding:0.4rem 0.7rem">🧪 Tester</button>' : '') +
                         '<button class="btn btn-secondary btn-del" data-id="' + m.id + '" data-name="' + escapeHtml(m.name) + '" style="padding:0.4rem 0.7rem">🗑</button>' +
                       '</div>' +
                       '</div>';
      list.appendChild(card);
    });

    $$('.btn-test', list).forEach(function (btn) {
      btn.addEventListener('click', function () { runTest(btn.dataset.id); });
    });
    $$('.btn-del', list).forEach(function (btn) {
      btn.addEventListener('click', function () {
        if (!confirm('Supprimer le modèle "' + btn.dataset.name + '" ? (le .pth sera retiré de RunPod aussi)')) return;
        VB.api.delete('/api/rvc/models/' + btn.dataset.id).then(function () {
          VB.notify('success', 'Modèle supprimé');
          load();
        }).catch(function (err) {
          VB.notify('error', err.message || 'Échec suppression');
        });
      });
    });
  }

  function runTest(modelId) {
    var area = $('rvcTestArea');
    var progressDiv = $('rvcTestProgress');
    var resultDiv = $('rvcTestResult');
    area.style.display = '';
    resultDiv.style.display = 'none';
    progressDiv.innerHTML = '⏳ Lancement du test sur RunPod GPU…';

    VB.api.post('/api/rvc/models/' + modelId + '/test', {}).then(function (d) {
      VB.progress.attachBar(progressDiv, d.task_id, {
        onDone: function () {
          // Récupère l'audio test
          fetch('/api/rvc/models/' + modelId + '/test_audio').then(function (r) {
            if (!r.ok) throw new Error('audio test indisponible');
            return r.blob();
          }).then(function (blob) {
            $('rvcTestAudio').src = URL.createObjectURL(blob);
            resultDiv.style.display = '';
            VB.notify('success', 'Test terminé — écoute le résultat');
          }).catch(function (err) {
            VB.notify('warning', 'Test OK mais audio indisponible : ' + (err.message || err));
          });
        },
        onError: function (snap) {
          VB.notify('error', 'Test échoué : ' + (snap.error || ''));
        },
      });
    }).catch(function (err) {
      progressDiv.innerHTML = '❌ ' + escapeHtml(err.message || 'Échec lancement test');
      VB.notify('error', err.message || 'Échec lancement test');
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    bindTabs();
    load();
  });
})();
