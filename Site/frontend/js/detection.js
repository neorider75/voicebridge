// VoiceBridge — detection.js : analyse deepfake (livraison 5).

(function () {
  function $(id) { return document.getElementById(id); }
  function $$(sel, r) { return Array.prototype.slice.call((r || document).querySelectorAll(sel)); }

  var lastReport = '';

  function bindRadioGroup() {
    var group = document.querySelector('.radio-group[data-name="mode"]');
    $$('.radio-option', group).forEach(function (opt) {
      opt.addEventListener('click', function () {
        $$('.radio-option', group).forEach(function (o) { o.classList.remove('selected'); });
        opt.classList.add('selected');
        group.dataset.value = opt.getAttribute('data-value');
      });
    });
  }

  function readMode() {
    var group = document.querySelector('.radio-group[data-name="mode"]');
    return group.dataset.value || (group.querySelector('.radio-option.selected') || {}).getAttribute?.('data-value') || 'both';
  }

  function bindUpload() {
    var dz = $('dropZone');
    var input = $('fileInput');
    dz.addEventListener('click', function () { input.click(); });
    dz.addEventListener('dragover', function (e) { e.preventDefault(); dz.classList.add('dragover'); });
    dz.addEventListener('dragleave', function () { dz.classList.remove('dragover'); });
    dz.addEventListener('drop', function (e) {
      e.preventDefault();
      dz.classList.remove('dragover');
      if (e.dataTransfer.files.length) {
        input.files = e.dataTransfer.files;
        showName(e.dataTransfer.files[0]);
      }
    });
    input.addEventListener('change', function () {
      if (input.files.length) showName(input.files[0]);
    });
  }
  function showName(file) {
    $('uploadName').textContent = file.name + ' · ' + Math.round(file.size / 1024) + ' Ko';
    $('uploadName').style.display = 'block';
  }

  function bindAnalyze() {
    $('btnAnalyze').addEventListener('click', function () {
      var input = $('fileInput');
      if (!input.files.length) { VB.notify('warning', 'Choisissez un fichier'); return; }
      var btn = $('btnAnalyze');
      btn.disabled = true; btn.textContent = '⏳ Analyse…';

      var fd = new FormData();
      fd.append('audio', input.files[0]);
      fd.append('mode', readMode());

      fetch('/api/detection/analyze', {
        method: 'POST', credentials: 'same-origin', body: fd,
      }).then(function (r) {
        if (!r.ok) return r.json().then(function (d) { throw new Error(d.message || 'HTTP ' + r.status); });
        return r.json();
      }).then(function (data) {
        renderResult(data);
        VB.notify('success', 'Analyse terminée');
      }).catch(function (e) {
        VB.notify('error', e.message || 'Analyse impossible');
      }).finally(function () {
        btn.disabled = false; btn.textContent = '🔍 Analyser';
      });
    });

    $('btnCopyReport').addEventListener('click', function () {
      if (!lastReport) { VB.notify('warning', 'Aucun rapport à copier'); return; }
      navigator.clipboard.writeText(lastReport).then(function () {
        VB.notify('success', 'Rapport copié');
      }).catch(function () {
        VB.notify('error', 'Copie impossible');
      });
    });
  }

  function renderResult(data) {
    document.querySelector('.step[data-step="3"]').classList.remove('locked');

    var card = $('resultCard');
    card.innerHTML = '';

    var verdictDiv = document.createElement('div');
    verdictDiv.style.fontSize = '1.4rem';
    verdictDiv.style.fontWeight = '800';
    verdictDiv.style.marginBottom = '0.75rem';
    if (data.verdict === 'ai_generated') {
      verdictDiv.style.color = 'var(--warning)';
      verdictDiv.textContent = '🤖 Généré par IA';
    } else {
      verdictDiv.style.color = 'var(--success)';
      verdictDiv.textContent = '✅ Non généré par IA';
    }
    card.appendChild(verdictDiv);

    var sub = document.createElement('div');
    sub.style.fontSize = '0.8rem';
    sub.style.color = 'var(--text3)';
    sub.style.marginBottom = '1rem';
    sub.textContent = data.summary + ' · Confiance ' + (data.confidence || 0) + ' %';
    card.appendChild(sub);

    function row(k, v) {
      var d = document.createElement('div');
      d.style.fontFamily = "'DM Mono', monospace";
      d.style.fontSize = '0.78rem';
      d.style.padding = '0.2rem 0';
      d.innerHTML = '<span style="color:var(--text3)">' + k + ' :</span> ';
      var span = document.createElement('span');
      span.textContent = v;
      d.appendChild(span);
      card.appendChild(d);
    }

    row('Watermark VoiceBridge',
      data.watermark.checked ? (data.watermark.detected ? '✅ Présent' : '❌ Absent') : '⏭ Non vérifié');
    row('Audio altéré',
      data.watermark.detected ? (data.watermark.tampered ? '⚠️ Oui' : '✅ Non') : '—');
    if (data.spectral && data.spectral.checked !== false) {
      row('Analyse spectrale',
        (data.spectral.label === 'fake' ? 'Synthétique' : 'Authentique')
        + ' (' + (data.spectral.confidence || 0) + ' %)');
    } else {
      row('Analyse spectrale', '⏭ Non vérifié');
    }
    row('Fichier', data.metadata.filename || '');
    row('Durée', (data.metadata.duration_seconds || 0) + ' s');
    row('Analysé le', data.metadata.analyzed_at || '');
    row('Mode', data.metadata.mode || '');

    // Génère le rapport texte
    var lines = [
      "Rapport d'analyse audio - VoiceBridge",
      'Date         : ' + (data.metadata.analyzed_at || ''),
      'Fichier      : ' + (data.metadata.filename || ''),
      'Durée        : ' + (data.metadata.duration_seconds || 0) + 's',
      'Mode         : ' + (data.metadata.mode || ''),
      'Watermark    : ' + (data.watermark.checked
        ? (data.watermark.detected ? 'Présent' : 'Absent') : 'Non vérifié'),
      'Audio altéré : ' + (data.watermark.detected ? (data.watermark.tampered ? 'Oui' : 'Non') : '-'),
      'Analyse IA   : ' + (data.spectral && data.spectral.checked !== false
        ? (data.spectral.label === 'fake' ? 'Synthétique' : 'Authentique')
          + ' (' + (data.spectral.confidence || 0) + '%)'
        : 'Non vérifié'),
      'Verdict      : ' + (data.verdict === 'ai_generated' ? 'Généré par IA' : 'Non généré par IA'),
    ];
    lastReport = lines.join('\n');
  }

  document.addEventListener('DOMContentLoaded', function () {
    bindRadioGroup();
    bindUpload();
    bindAnalyze();
  });
})();
