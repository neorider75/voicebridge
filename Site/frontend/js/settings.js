// VoiceBridge — settings.js : Serveur / API / Sécurité / Installation.
// Layout sidebar gauche + panneaux droite (cf. maquette voicebridge_v8.html).

(function () {
  function $(id) { return document.getElementById(id); }
  function $$(sel, r) { return Array.prototype.slice.call((r || document).querySelectorAll(sel)); }

  function bindNav() {
    $$('.settings-nav-item').forEach(function (item) {
      item.addEventListener('click', function () {
        var target = item.getAttribute('data-panel');
        $$('.settings-nav-item').forEach(function (i) { i.classList.remove('active'); });
        item.classList.add('active');
        $$('.settings-panel').forEach(function (p) {
          p.classList.toggle('active', p.getAttribute('data-panel') === target);
        });
      });
    });
  }

  // ── Server state polling ──
  var lastRefreshAt = 0;

  function tickRefreshAge() {
    var el = $('serverRefreshAge');
    if (!el || !lastRefreshAt) return;
    var ago = Math.max(0, Math.round((Date.now() - lastRefreshAt) / 1000));
    el.textContent = ago === 0 ? 'à l\'instant' : 'il y a ' + ago + ' s';
  }

  function refreshServerState() {
    var btn = $('btnRefreshServer');
    if (btn) { btn.style.opacity = '0.5'; btn.disabled = true; }
    VB.api.get('/api/system/status').then(function (s) {
      // RAM
      $('ramText').textContent = s.ram.used_gb + ' / ' + s.ram.total_gb + ' Go';
      $('ramBar').style.width = (s.ram.percent || 0) + '%';
      // Stockage
      $('diskText').textContent = s.storage.used_gb + ' / ' + s.storage.total_gb + ' Go';
      $('diskBar').style.width = (s.storage.percent || 0) + '%';
      // Modèles : nombre chargés / total + détail (avec bouton ⏏ par ligne loaded)
      renderModelsList(s.models);
      lastRefreshAt = Date.now();
      tickRefreshAge();
    }).catch(function () {}).finally(function () {
      if (btn) { btn.style.opacity = ''; btn.disabled = false; }
    });
  }

  function renderModelsList(models) {
    var loaded = 0, total = 0;
    var list = $('modelsList');
    list.innerHTML = '';
    Object.keys(models).forEach(function (k) {
      total += 1;
      var v = models[k];
      var isLoaded = v === 'loaded';
      if (isLoaded) loaded += 1;

      var row = document.createElement('div');
      row.style.display = 'flex';
      row.style.alignItems = 'center';
      row.style.justifyContent = 'space-between';
      row.style.gap = '0.5rem';

      var left = document.createElement('span');
      var dotColor = isLoaded ? 'var(--success)' : 'var(--text3)';
      left.innerHTML = '<span style="color:' + dotColor + '">●</span> ' + k + ' : ' + v;
      row.appendChild(left);

      if (isLoaded) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn-icon';
        btn.title = 'Décharger ce modèle';
        btn.textContent = '⏏';
        btn.style.cssText = 'background:transparent;border:1px solid var(--border);color:var(--text2);' +
          'border-radius:4px;padding:0 0.4rem;font-size:0.75rem;line-height:1.4;cursor:pointer;font-family:inherit';
        btn.addEventListener('click', function () { unloadModel(k, btn); });
        row.appendChild(btn);
      }

      list.appendChild(row);
    });

    var summary = $('modelsSummary');
    if (loaded === 0) {
      summary.textContent = 'Veille (' + total + ' inactifs)';
      summary.style.color = 'var(--text3)';
    } else if (loaded === total) {
      summary.textContent = 'Tous chargés (' + loaded + ')';
      summary.style.color = 'var(--success)';
    } else {
      summary.textContent = loaded + ' / ' + total + ' chargés';
      summary.style.color = 'var(--warning)';
    }
  }

  function unloadModel(key, btn) {
    btn.disabled = true;
    btn.textContent = '…';
    VB.api.post('/api/system/unload', { key: key }).then(function (r) {
      if (r.was_loaded) {
        VB.notify('success', key + ' déchargé');
      } else {
        VB.notify('info', key + ' n\'était pas chargé');
      }
      refreshServerState();
    }).catch(function (e) {
      VB.notify('error', e.message || 'Échec du déchargement');
      btn.disabled = false;
      btn.textContent = '⏏';
    });
  }

  // ── Préchauffage ──
  function bindWarm() {
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
      var profiles = [];
      if ($('warmProfileTts').checked) profiles.push('tts');
      if ($('warmProfileLive').checked) profiles.push('live');
      if (!profiles.length) {
        VB.notify('warning', 'Cochez au moins un profil (TTS ou Live)');
        return;
      }
      btn.disabled = true; btn.textContent = '⏳ Préchauffage…';
      $('warmStatus').textContent = '';
      VB.api.post('/api/system/prechauffage', {
        language: $('warmLang').value,
        profiles: profiles,
        voice_id: $('warmVoice').value || undefined,
      }).then(function (r) {
        var newCount = (r.newly_loaded || []).length;
        var totalLoaded = r.loaded_count || 0;
        var summary = newCount === 0
          ? 'Déjà chargé (' + totalLoaded + '/' + r.total_count + ')'
          : '+' + newCount + ' chargé(s) · ' + totalLoaded + '/' + r.total_count + ' au total';
        $('warmStatus').textContent = '✅ ' + summary + ' en ' + r.duration_ms + ' ms';
        VB.notify('success', summary);
        refreshServerState();
      }).catch(function (e) {
        VB.notify('error', e.message || 'Échec');
      }).finally(function () {
        btn.disabled = false; btn.textContent = '🚀 Préchauffer';
      });
    });
  }

  // ── Rétention + déchargement (PUT /api/settings) ──
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
    bindNav();
    refreshServerState();
    setInterval(refreshServerState, 5000);
    setInterval(tickRefreshAge, 1000);  // met à jour le compteur "il y a X s"
    var btnR = $('btnRefreshServer');
    if (btnR) btnR.addEventListener('click', refreshServerState);
    bindWarm();
    bindRadioGroupsSettings();
    syncSettingsFromServer();
    loadApiKeyInfo();
    bindApiKey();
    bindPassword();
  });
})();
