/**
 * /sessions — historique paginé des sessions Live + récap 30 j.
 *
 * Backend : /api/sessions, /api/sessions/summary, /api/sessions/{id}.
 */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };

  var PAGE_SIZE = 30;
  var currentOffset = 0;
  var currentTotal = 0;

  var MODE_LABELS = {
    'cpu-fr-en':   { label: 'Authentique CPU', emoji: '🔵' },
    'gpu-clone':   { label: 'Multilingue clone', emoji: '🟣' },
    'gpu-native':  { label: 'Voix native', emoji: '🟢' },
    'gpu-hybrid':  { label: 'Hybride RVC', emoji: '⭐' },
  };

  var LANG_FLAGS = {
    fr: '🇫🇷', en: '🇬🇧', es: '🇪🇸', de: '🇩🇪', it: '🇮🇹',
    pt: '🇵🇹', nl: '🇳🇱', ja: '🇯🇵', zh: '🇨🇳', ko: '🇰🇷',
  };

  function fmtDuration(seconds) {
    if (!seconds || seconds < 1) return '—';
    var s = Math.round(seconds);
    if (s < 60) return s + 's';
    var m = Math.floor(s / 60);
    var r = s % 60;
    if (m < 60) return m + 'm' + (r ? (' ' + r + 's') : '');
    var h = Math.floor(m / 60);
    var mr = m % 60;
    return h + 'h' + (mr ? (' ' + mr + 'm') : '');
  }

  function fmtCost(eur) {
    if (eur === undefined || eur === null) return '—';
    if (eur < 0.0001) return '~0€';
    return eur.toFixed(4) + '€';
  }

  function fmtDate(iso) {
    if (!iso) return '—';
    try {
      var d = new Date(iso);
      return d.toLocaleString('fr-FR', {
        day: '2-digit', month: '2-digit', year: 'numeric',
        hour: '2-digit', minute: '2-digit',
      });
    } catch (e) { return iso; }
  }

  function modeBadge(mode) {
    var m = MODE_LABELS[mode] || { label: mode, emoji: '·' };
    return '<span class="session-mode-badge">'
         + m.emoji + ' ' + m.label + '</span>';
  }

  function translationLabel(t) {
    if (!t || !t.enabled) return '<span style="color:var(--text3)">—</span>';
    var src = LANG_FLAGS[t.source_lang] || t.source_lang;
    var tgt = LANG_FLAGS[t.target_lang] || t.target_lang;
    return src + ' → ' + tgt
         + ' <span style="color:var(--text3);font-size:0.75em">('
         + (t.provider || '') + ')</span>';
  }

  // ── Récap 30 jours ────────────────────────────────────────────────

  function loadSummary() {
    VB.api.get('/api/sessions/summary?days=30')
      .then(function (s) {
        $('sumNSessions').textContent = s.n_sessions || 0;
        $('sumDuration').textContent = fmtDuration(s.total_duration_s);
        $('sumCost').textContent = fmtCost(s.total_cost_eur);
        $('sumCostRunpod').textContent = fmtCost(s.cost_runpod_eur);
        $('sumCostOpenai').textContent = fmtCost(s.cost_openai_eur);
        // Répartition par mode
        var by = s.by_mode || {};
        var parts = [];
        Object.keys(by).forEach(function (m) {
          var lbl = (MODE_LABELS[m] || { label: m }).label;
          parts.push(lbl + ' : ' + by[m]);
        });
        $('sumByMode').textContent = parts.length
          ? 'Répartition : ' + parts.join(' · ')
          : '';
      })
      .catch(function (err) {
        console.warn('[sessions] summary failed', err);
      });
  }

  // ── Liste paginée ─────────────────────────────────────────────────

  function escapeHtml(s) {
    return String(s || '').replace(/[&<>"']/g, function (c) {
      return { '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c];
    });
  }

  function rowHtml(s) {
    var t = s.translation || {};
    return '<div class="session-row">'
      + '<div>' + fmtDate(s.ended_at) + '</div>'
      + '<div>' + modeBadge(s.mode) + '</div>'
      + '<div>🎤 ' + escapeHtml(s.voice_name || s.voice_id) + '</div>'
      + '<div>' + translationLabel(t) + '</div>'
      + '<div>' + fmtDuration(s.duration_s) + '</div>'
      + '<div class="session-cost">' + fmtCost((s.cost_eur || {}).total) + '</div>'
      + '<div style="text-align:right">'
        + '<button class="btn-danger-ghost" data-del="' + escapeHtml(s.id)
        + '" title="Supprimer">🗑</button>'
      + '</div>'
      + '</div>';
  }

  function renderList(payload) {
    var container = $('sessionsList');
    var sessions = payload.sessions || [];
    currentTotal = payload.total || 0;
    if (sessions.length === 0) {
      container.innerHTML = '<div class="session-empty">'
        + 'Aucune session enregistrée pour le moment. Lance une session '
        + '<a href="/studio">depuis le Studio</a> pour commencer.'
        + '</div>';
      $('sessionsCount').textContent = '';
      $('btnPurgeAll').style.display = 'none';
      $('sessionsPagination').innerHTML = '';
      return;
    }
    var header = '<div class="session-row head">'
      + '<div>Fin</div><div>Mode</div><div>Voix</div>'
      + '<div>Traduction</div><div>Durée</div><div>Coût</div><div></div>'
      + '</div>';
    container.innerHTML = header + sessions.map(rowHtml).join('');
    $('sessionsCount').textContent = currentTotal + ' session'
      + (currentTotal > 1 ? 's' : '') + ' au total';
    $('btnPurgeAll').style.display = '';
    bindRowActions();
    renderPagination();
  }

  function bindRowActions() {
    document.querySelectorAll('button[data-del]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var id = btn.getAttribute('data-del');
        if (!confirm('Supprimer cette session de l\'historique ?')) return;
        VB.api.del('/api/sessions/' + encodeURIComponent(id))
          .then(function () {
            VB.notify('success', 'Session supprimée');
            loadPage(currentOffset);
            loadSummary();
          })
          .catch(function (err) {
            VB.notify('error', err.message || 'Suppression échouée');
          });
      });
    });
  }

  function renderPagination() {
    var pg = $('sessionsPagination');
    var nPages = Math.ceil(currentTotal / PAGE_SIZE);
    if (nPages <= 1) {
      pg.innerHTML = '';
      return;
    }
    var current = Math.floor(currentOffset / PAGE_SIZE);
    var html = '';
    html += '<button class="btn-danger-ghost" '
        + (current === 0 ? 'disabled' : '')
        + ' data-page="' + (current - 1) + '">← Précédent</button>';
    html += '<span style="padding:0.4rem 0.8rem;color:var(--text3);'
         + 'font-family:DM Mono,monospace;font-size:0.78rem">'
         + (current + 1) + ' / ' + nPages + '</span>';
    html += '<button class="btn-danger-ghost" '
        + (current >= nPages - 1 ? 'disabled' : '')
        + ' data-page="' + (current + 1) + '">Suivant →</button>';
    pg.innerHTML = html;
    pg.querySelectorAll('button[data-page]').forEach(function (b) {
      b.addEventListener('click', function () {
        var p = parseInt(b.getAttribute('data-page'), 10);
        if (!isNaN(p) && p >= 0) {
          currentOffset = p * PAGE_SIZE;
          loadPage(currentOffset);
        }
      });
    });
  }

  function loadPage(offset) {
    VB.api.get('/api/sessions?limit=' + PAGE_SIZE + '&offset=' + offset)
      .then(renderList)
      .catch(function (err) {
        $('sessionsList').innerHTML =
          '<div class="session-empty">Erreur de chargement : '
          + escapeHtml(err.message || err) + '</div>';
      });
  }

  // ── Purge ─────────────────────────────────────────────────────────

  function bindPurge() {
    $('btnPurgeAll').addEventListener('click', function () {
      if (!confirm('Supprimer DÉFINITIVEMENT tout l\'historique des sessions ? '
                 + 'Cette action est irréversible.')) return;
      VB.api.del('/api/sessions')
        .then(function (r) {
          VB.notify('success', (r.deleted || 0) + ' session(s) supprimée(s)');
          currentOffset = 0;
          loadPage(0);
          loadSummary();
        })
        .catch(function (err) {
          VB.notify('error', err.message || 'Purge échouée');
        });
    });
  }

  // ── Init ──────────────────────────────────────────────────────────

  document.addEventListener('DOMContentLoaded', function () {
    bindPurge();
    loadSummary();
    loadPage(0);
  });
})();
