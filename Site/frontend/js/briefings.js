// VoiceBridge — briefings.js
// CRUD des briefings GPT. Pattern simple : liste + editor inline.

(function () {
  function $(id) { return document.getElementById(id); }
  function $$(sel, r) { return Array.prototype.slice.call((r || document).querySelectorAll(sel)); }

  var editingId = null;   // null = nouveau ; sinon id du briefing en édition

  function load() {
    return VB.api.get('/api/briefings').then(function (d) {
      render(d.briefings || []);
    });
  }

  function render(briefings) {
    $('briefingsCount').textContent =
      briefings.length === 0 ? 'Aucun briefing sauvegardé pour l\'instant.'
                              : briefings.length + ' briefing' + (briefings.length > 1 ? 's' : '');
    var list = $('briefingsList');
    list.innerHTML = '';
    if (!briefings.length) {
      list.innerHTML = '<div class="card" style="text-align:center;color:var(--text3);padding:2rem">' +
                      '📋 Crée ton premier briefing pour aider GPT à traduire ton contexte métier.</div>';
      return;
    }
    briefings.forEach(function (b) {
      var card = document.createElement('div');
      card.className = 'card';
      card.style.marginBottom = '0.75rem';
      var preview = (b.content || '').slice(0, 200);
      if ((b.content || '').length > 200) preview += '…';
      card.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:0.75rem">' +
                       '<div style="flex:1;min-width:0">' +
                         '<div style="font-weight:700;font-size:0.95rem;margin-bottom:0.35rem">' + escapeHtml(b.name) + '</div>' +
                         '<div style="font-size:0.78rem;color:var(--text2);line-height:1.5;white-space:pre-wrap">' + escapeHtml(preview) + '</div>' +
                         '<div style="font-size:0.68rem;color:var(--text3);margin-top:0.5rem;font-family:\'DM Mono\',monospace">' +
                            '↻ ' + (b.updated_at || b.created_at || '') + '</div>' +
                       '</div>' +
                       '<div style="display:flex;gap:0.4rem;flex-shrink:0">' +
                         '<button class="btn btn-secondary btn-edit" data-id="' + b.id + '" style="padding:0.4rem 0.7rem">✏️</button>' +
                         '<button class="btn btn-secondary btn-del" data-id="' + b.id + '" style="padding:0.4rem 0.7rem">🗑</button>' +
                       '</div>' +
                       '</div>';
      list.appendChild(card);
    });

    $$('.btn-edit', list).forEach(function (btn) {
      btn.addEventListener('click', function () { openEditor(btn.dataset.id); });
    });
    $$('.btn-del', list).forEach(function (btn) {
      btn.addEventListener('click', function () {
        var id = btn.dataset.id;
        if (!confirm('Supprimer ce briefing ?')) return;
        VB.api.delete('/api/briefings/' + id).then(function () {
          VB.notify('success', 'Briefing supprimé');
          load();
        }).catch(function (err) {
          VB.notify('error', err.message || 'Échec suppression');
        });
      });
    });
  }

  function escapeHtml(s) {
    if (s == null) return '';
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function openEditor(id) {
    editingId = id || null;
    var ed = $('briefingEditor');
    ed.style.display = '';
    $('editorTitle').textContent = id ? 'Modifier briefing' : 'Nouveau briefing';

    if (id) {
      VB.api.get('/api/briefings/' + id).then(function (b) {
        $('briefingName').value = b.name || '';
        $('briefingContent').value = b.content || '';
        updateCharCount();
      });
    } else {
      $('briefingName').value = '';
      $('briefingContent').value = '';
      updateCharCount();
    }
    $('briefingName').focus();
    ed.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  function closeEditor() {
    editingId = null;
    $('briefingEditor').style.display = 'none';
  }

  function save() {
    var name = $('briefingName').value.trim();
    var content = $('briefingContent').value.trim();
    if (!name) {
      VB.notify('warning', 'Donne un nom au briefing');
      return;
    }
    var body = { name: name, content: content };
    var p = editingId
      ? VB.api.put('/api/briefings/' + editingId, body)
      : VB.api.post('/api/briefings', body);
    p.then(function () {
      VB.notify('success', editingId ? 'Briefing modifié' : 'Briefing créé');
      closeEditor();
      load();
    }).catch(function (err) {
      VB.notify('error', err.message || 'Échec enregistrement');
    });
  }

  function updateCharCount() {
    var len = ($('briefingContent').value || '').length;
    var max = 4000;
    var color = len > max * 0.9 ? 'var(--warning)' : 'var(--text3)';
    $('charCount').style.color = color;
    $('charCount').textContent = len + ' / ' + max + ' caractères';
  }

  document.addEventListener('DOMContentLoaded', function () {
    $('btnNewBriefing').addEventListener('click', function () { openEditor(null); });
    $('btnCancelEdit').addEventListener('click', closeEditor);
    $('btnSaveBriefing').addEventListener('click', save);
    $('briefingContent').addEventListener('input', updateCharCount);
    load();
  });
})();
