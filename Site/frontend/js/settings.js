// VoiceBridge — settings.js : Serveur / API / Sécurité / Installation.

(function () {
  function $(id) { return document.getElementById(id); }
  function $$(sel, r) { return Array.prototype.slice.call((r || document).querySelectorAll(sel)); }

  function bindTabs() {
    $$('.studio-tab').forEach(function (tab) {
      tab.addEventListener('click', function () {
        var target = tab.getAttribute('data-section');
        $$('.studio-tab').forEach(function (t) { t.classList.remove('active'); });
        tab.classList.add('active');
        $$('.studio-content').forEach(function (c) {
          c.classList.toggle('active', c.getAttribute('data-section') === target);
        });
      });
    });
  }

  // ── Server state polling ──
  function refreshServerState() {
    VB.api.get('/api/system/status').then(function (s) {
      var html = '';
      function row(k, v) { return '<div><span style="color:var(--text3)">' + k + ' :</span> <span>' + v + '</span></div>'; }
      html += row('Statut', s.status);
      html += row('Uptime', Math.round(s.uptime_seconds / 60) + ' min');
      html += row('RAM', s.ram.used_gb + ' / ' + s.ram.total_gb + ' Go (' + s.ram.percent + '%)');
      html += row('Stockage', s.storage.used_gb + ' / ' + s.storage.total_gb + ' Go (' + s.storage.percent + '%)');
      html += '<div style="margin-top:0.6rem;color:var(--text3)">Modèles :</div>';
      Object.keys(s.models).forEach(function (k) {
        var v = s.models[k];
        var color = v === 'loaded' ? 'var(--success)' : 'var(--text3)';
        html += '<div style="padding-left:0.5rem"><span style="color:' + color + '">●</span> ' + k + ' : ' + v + '</div>';
      });
      $('serverState').innerHTML = html;
    }).catch(function () {});
  }

  // ── Préchauffage ──
  function bindWarm() {
    // Charge les voix dans le sélecteur
    VB.api.get('/api/voices').then(function (d) {
      var sel = $('warmVoice');
      (d.voices || []).forEach(function (v) {
        var opt = document.createElement('option');
        opt.value = v.id;
        opt.textContent = (v.language === 'fr' ? '🇫🇷 ' : '🇬🇧 ') + v.name;
        sel.appendChild(opt);
      });
    });
    $('btnWarm').addEventListener('click', function () {
      var btn = $('btnWarm');
      btn.disabled = true; btn.textContent = '⏳ Préchauffage…';
      $('warmStatus').textContent = '';
      VB.api.post('/api/system/prechauffage', {
        language: $('warmLang').value,
        voice_id: $('warmVoice').value || undefined,
      }).then(function (r) {
        $('warmStatus').textContent = '✅ Terminé en ' + r.duration_ms + ' ms';
        VB.notify('success', 'Modèles préchauffés');
        refreshServerState();
      }).catch(function (e) {
        VB.notify('error', e.message || 'Échec');
      }).finally(function () {
        btn.disabled = false; btn.textContent = '🚀 Préchauffer';
      });
    });
  }

  // ── Settings PUT (rétention + déchargement) ──
  function bindRadioGroupsSettings() {
    function bindGroup(name, key, valueParser) {
      var group = document.querySelector('.radio-group[data-name="' + name + '"]');
      if (!group) return;
      $$('.radio-option', group).forEach(function (opt) {
        opt.addEventListener('click', function () {
          $$('.radio-option', group).forEach(function (o) { o.classList.remove('selected'); });
          opt.classList.add('selected');
          var raw = opt.getAttribute('data-value');
          var payload = {};
          payload[key] = valueParser ? valueParser(raw) : raw;
          VB.api.put('/api/settings', payload).then(function () {
            VB.notify('success', 'Réglage enregistré');
          }).catch(function (e) {
            VB.notify('error', e.message || 'Erreur');
          });
        });
      });
    }
    bindGroup('default-retention', 'default_retention', null);
    bindGroup('model-unload',      'model_unload_after_minutes', function (v) { return parseInt(v, 10); });
  }

  function syncSettingsFromServer() {
    VB.api.get('/api/settings').then(function (s) {
      function selectGroup(name, value) {
        var group = document.querySelector('.radio-group[data-name="' + name + '"]');
        if (!group) return;
        $$('.radio-option', group).forEach(function (o) {
          o.classList.toggle('selected', o.getAttribute('data-value') === String(value));
        });
      }
      selectGroup('default-retention', s.default_retention);
      selectGroup('model-unload', s.model_unload_after_minutes);
    });
  }

  // ── API key ──
  function loadApiKeyInfo() {
    VB.api.get('/api/settings/api-key').then(function (d) {
      $('apiKeyMasked').textContent = d.masked || '';
      $('apiKeyDate').textContent = d.created_at ? 'Générée le ' + d.created_at : '';
    });
  }
  function bindApiKey() {
    $('btnRegenKey').addEventListener('click', function () {
      if (!window.confirm("L'ancienne clé sera révoquée immédiatement. Continuer ?")) return;
      VB.api.post('/api/settings/api-key/generate').then(function (r) {
        $('apiKeyClear').textContent = r.key;
        $('apiKeyResult').style.display = 'block';
        VB.notify('success', 'Nouvelle clé générée');
        loadApiKeyInfo();
      }).catch(function (e) {
        VB.notify('error', e.message || 'Erreur');
      });
    });
    $('btnCopyKey').addEventListener('click', function () {
      var key = $('apiKeyClear').textContent;
      navigator.clipboard.writeText(key).then(function () { VB.notify('success', 'Clé copiée'); });
    });
  }

  // ── Mot de passe ──
  function bindPassword() {
    $('btnChangePw').addEventListener('click', function () {
      var cur = $('pwCurrent').value;
      var nw = $('pwNew').value;
      var cf = $('pwConfirm').value;
      if (!cur || !nw || !cf) { VB.notify('warning', 'Tous les champs sont requis'); return; }
      if (nw.length < 8) { VB.notify('warning', 'Min 8 caractères'); return; }
      if (nw !== cf) { VB.notify('warning', 'Les deux nouveaux mdp ne correspondent pas'); return; }
      if (nw === cur) { VB.notify('warning', 'Le nouveau doit différer'); return; }
      VB.api.post('/api/settings/password', { current_password: cur, new_password: nw })
        .then(function () {
          VB.notify('success', 'Mot de passe mis à jour');
          $('pwCurrent').value = $('pwNew').value = $('pwConfirm').value = '';
        })
        .catch(function (e) { VB.notify('error', e.message || 'Erreur'); });
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    bindTabs();
    refreshServerState();
    setInterval(refreshServerState, 5000);
    bindWarm();
    bindRadioGroupsSettings();
    syncSettingsFromServer();
    loadApiKeyInfo();
    bindApiKey();
    bindPassword();
  });
})();
