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
      renderModelsList(s.models, s.models_detailed);
      lastRefreshAt = Date.now();
      tickRefreshAge();
    }).catch(function () {}).finally(function () {
      if (btn) { btn.style.opacity = ''; btn.disabled = false; }
    });
  }

  function fmtRelativeTime(ts) {
    if (!ts) return '';
    var now = Date.now() / 1000;
    var diff = Math.max(0, Math.round(now - ts));
    if (diff < 60) return 'il y a ' + diff + ' s';
    if (diff < 3600) return 'il y a ' + Math.round(diff / 60) + ' min';
    if (diff < 86400) return 'il y a ' + Math.round(diff / 3600) + ' h';
    return 'il y a ' + Math.round(diff / 86400) + ' j';
  }

  function renderModelsList(models, detailed) {
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
      // Affichage du last_used si dispo (pour debug : voir si le modèle a
      // vraiment été utilisé récemment ou s'il est en veille depuis longtemps).
      var lastUsedHint = '';
      if (detailed && detailed[k] && detailed[k].last_used) {
        lastUsedHint = ' <span style="color:var(--text3);font-size:0.65rem">·  '
          + fmtRelativeTime(detailed[k].last_used) + '</span>';
      }
      left.innerHTML = '<span style="color:' + dotColor + '">●</span> ' + k + ' : ' + v + lastUsedHint;
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
    bindGroup('default-retention',    'default_retention', null);
    bindGroup('model-unload',         'model_unload_after_minutes', function (v) { return parseInt(v, 10); });
    bindGroup('default-tts-engine',   'default_tts_engine', null);
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
      selectGroup('default-tts-engine', s.default_tts_engine || 'neutts');
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

  // ════════════════════════════════════════════════════════════════
  // V3 — Cloud / Traduction / RVC
  // ════════════════════════════════════════════════════════════════

  // Helper visuel pour les résultats de test
  function setTestStatus(elId, ok, msg) {
    var el = $(elId);
    if (!el) return;
    el.style.color = ok ? 'var(--success)' : 'var(--error)';
    el.textContent = (ok ? '✅ ' : '❌ ') + msg;
  }

  function setTestLoading(elId) {
    var el = $(elId);
    if (!el) return;
    el.style.color = 'var(--text3)';
    el.textContent = '⏳ Test en cours…';
  }

  // ── Cloud panel ──

  function loadCloudPanel() {
    return VB.api.get('/api/cloud/status').then(function (s) {
      var html = '';
      html += '<div>RunPod : ' + (s.runpod_configured ? '<span style="color:var(--success)">✅ configuré</span>' : '<span style="color:var(--text3)">— non configuré</span>') + '</div>';
      if (s.runpod_configured) html += '<div>&nbsp;&nbsp;Datacenter : ' + (s.datacenter || '?') + '</div>';
      html += '<div>OpenAI : ' + (s.openai_configured ? '<span style="color:var(--success)">✅ configurée</span>' : '<span style="color:var(--text3)">— non configurée</span>') + '</div>';
      html += '<div>Mode défaut : ' + (s.default_live_mode || 'cpu-fr-en') + '</div>';
      html += '<div>Provider trad défaut : ' + (s.default_translation_provider || 'opus-mt-cpu') + '</div>';
      $('cloudStatusSummary').innerHTML = html;
    });
  }

  function saveRunpod() {
    var body = {};
    var apiKey = $('rpApiKey').value.trim();
    var endpointId = $('rpEndpointId').value.trim();
    var volumeId = $('rpVolumeId').value.trim();
    var datacenter = $('rpDatacenter').value;
    var s3a = $('rpS3Access').value.trim();
    var s3s = $('rpS3Secret').value.trim();
    if (apiKey) body.api_key = apiKey;
    if (endpointId) body.endpoint_id = endpointId;
    if (volumeId) body.volume_id = volumeId;
    if (datacenter) body.datacenter = datacenter;
    if (s3a) body.s3_access_key = s3a;
    if (s3s) body.s3_secret_key = s3s;
    if (Object.keys(body).length === 0) {
      VB.notify('warning', 'Aucun champ à enregistrer');
      return;
    }
    VB.api.post('/api/cloud/runpod/configure', body).then(function () {
      VB.notify('success', 'Configuration RunPod enregistrée');
      // Vide les champs password pour ne pas re-soumettre
      $('rpApiKey').value = '';
      $('rpS3Access').value = '';
      $('rpS3Secret').value = '';
      loadCloudPanel();
    }).catch(function (err) {
      VB.notify('error', err.message || 'Échec enregistrement');
    });
  }

  function testRunpod() {
    setTestLoading('runpodTestStatus');
    VB.api.post('/api/cloud/runpod/test', {}).then(function (r) {
      setTestStatus('runpodTestStatus', true,
        'OK · ' + (r.latency_ms || '?') + 'ms · ' + (r.datacenter || ''));
    }).catch(function (err) {
      setTestStatus('runpodTestStatus', false, err.message || 'Échec');
    });
  }

  function saveOpenai() {
    var k = $('oaiApiKey').value.trim();
    if (!k) { VB.notify('warning', 'Saisis une clé OpenAI'); return; }
    VB.api.post('/api/cloud/openai/configure', { api_key: k }).then(function () {
      VB.notify('success', 'Clé OpenAI enregistrée');
      $('oaiApiKey').value = '';
      loadCloudPanel();
    }).catch(function (err) {
      VB.notify('error', err.message || 'Échec enregistrement');
    });
  }

  function testOpenai() {
    setTestLoading('openaiTestStatus');
    VB.api.post('/api/cloud/openai/test', {}).then(function (r) {
      setTestStatus('openaiTestStatus', true,
        'OK · ' + (r.latency_ms || '?') + 'ms');
    }).catch(function (err) {
      setTestStatus('openaiTestStatus', false, err.message || 'Échec');
    });
  }

  // ── Traduction panel ──

  function loadProvidersPanel() {
    return VB.api.get('/api/cloud/providers').then(function (data) {
      var defaultId = data.default;
      var list = $('providerList');
      list.innerHTML = '';
      (data.providers || []).forEach(function (p) {
        var disabled = !p.available;
        var label = document.createElement('label');
        label.style.cssText = 'display:flex;align-items:flex-start;gap:0.6rem;padding:0.65rem 0.85rem;background:var(--surface3);border:1px solid var(--border);border-radius:6px;margin-bottom:0.5rem;cursor:' + (disabled ? 'not-allowed' : 'pointer') + ';opacity:' + (disabled ? '0.55' : '1');
        var costStr = (p.cost_per_phrase_eur && p.cost_per_phrase_eur > 0)
                       ? '~' + p.cost_per_phrase_eur.toFixed(4) + '€'
                       : 'gratuit';
        var availStr = disabled ? ' · <span style="color:var(--warning)">non configuré</span>' : '';
        label.innerHTML = '<input type="radio" name="defaultProvider" value="' + p.id + '" ' +
                          (p.id === defaultId ? 'checked' : '') +
                          (disabled ? ' disabled' : '') +
                          ' style="margin-top:0.2rem"> ' +
                          '<div style="flex:1">' +
                            '<div style="font-weight:600;font-size:0.85rem">' + p.name +
                              (p.id === defaultId ? ' <span style="font-size:0.65rem;background:var(--accent);color:white;padding:0.05rem 0.35rem;border-radius:99px;font-weight:600;margin-left:0.3rem">défaut</span>' : '') +
                            '</div>' +
                            '<div style="font-size:0.72rem;color:var(--text3);margin-top:0.15rem">' +
                              p.languages + ' · ' + (p.latency_ms || '?') + 'ms · ' + costStr + availStr +
                              (p.license_note ? ' · <span style="color:var(--warning)">licence ' + p.license_note + '</span>' : '') +
                            '</div>' +
                          '</div>';
        list.appendChild(label);
      });
      $$('input[name="defaultProvider"]').forEach(function (r) {
        r.addEventListener('change', function () {
          // POST partial config update via /api/settings (cf. routes/settings.py existant)
          if (typeof VB.api.put === 'function') {
            VB.api.put('/api/settings', { default_translation_provider: r.value })
              .then(function () { VB.notify('success', 'Provider par défaut : ' + r.value); })
              .catch(function (e) { VB.notify('error', e.message || 'Échec sauvegarde'); });
          }
        });
      });
    });
  }

  // ── Glossaire ──

  var glossaryWorking = {};

  function loadGlossary() {
    return VB.api.get('/api/settings').then(function (s) {
      glossaryWorking = (s && s.translation_glossary) || {};
      renderGlossary();
    }).catch(function () {
      glossaryWorking = {};
      renderGlossary();
    });
  }

  function renderGlossary() {
    var list = $('glossaryList');
    list.innerHTML = '';
    var keys = Object.keys(glossaryWorking);
    if (!keys.length) {
      list.innerHTML = '<div class="hint" style="padding:0.5rem">Aucune entrée. Ajoute des termes ci-dessous (ex: CODIR → Executive Committee, Limagrain → Limagrain pour le garder tel quel).</div>';
      return;
    }
    keys.forEach(function (k) {
      var row = document.createElement('div');
      row.style.cssText = 'display:flex;gap:0.4rem;align-items:center;padding:0.4rem 0.6rem;background:var(--surface3);border-radius:4px;margin-bottom:0.3rem;font-size:0.78rem;font-family:\'DM Mono\',monospace';
      row.innerHTML = '<span style="flex:1">' + escapeHtml(k) + '</span>' +
                      '<span style="color:var(--text3)">→</span>' +
                      '<span style="flex:1">' + escapeHtml(glossaryWorking[k] || '<span style="color:var(--text3)">(garder tel quel)</span>') + '</span>' +
                      '<button class="btn btn-secondary btn-glos-del" data-key="' + escapeHtml(k) + '" style="padding:0.2rem 0.5rem">🗑</button>';
      list.appendChild(row);
    });
    $$('.btn-glos-del', list).forEach(function (b) {
      b.addEventListener('click', function () {
        delete glossaryWorking[b.dataset.key];
        renderGlossary();
      });
    });
  }

  function addGlossaryEntry() {
    var k = $('glossaryNewKey').value.trim();
    var v = $('glossaryNewVal').value.trim();
    if (!k) { VB.notify('warning', 'Le terme source est requis'); return; }
    glossaryWorking[k] = v;
    $('glossaryNewKey').value = '';
    $('glossaryNewVal').value = '';
    renderGlossary();
  }

  function saveGlossary() {
    if (typeof VB.api.put !== 'function') {
      VB.notify('error', 'API put non disponible'); return;
    }
    VB.api.put('/api/settings', { translation_glossary: glossaryWorking })
      .then(function () { VB.notify('success', 'Glossaire enregistré (' + Object.keys(glossaryWorking).length + ' entrées)'); })
      .catch(function (err) { VB.notify('error', err.message || 'Échec'); });
  }

  function escapeHtml(s) {
    if (s == null) return '';
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  // ── RVC panel ──

  function loadRvcPanel() {
    return VB.api.get('/api/rvc/models').then(function (d) {
      var models = d.models || [];
      var active = models.filter(function (m) { return m.status === 'active'; });
      $('rvcModelsCount').textContent = active.length;
      var summary = $('rvcModelsSummary');
      if (!models.length) {
        summary.textContent = 'Aucun modèle pour l\'instant.';
        return;
      }
      summary.innerHTML = models.slice(0, 5).map(function (m) {
        var status = m.status === 'active' ? '✅' : (m.status === 'failed' ? '❌' : '⏳');
        return status + ' ' + escapeHtml(m.name) + ' (' + (m.size_mb || 0).toFixed(1) + ' Mo)';
      }).join('<br>');
      if (models.length > 5) {
        summary.innerHTML += '<br><span style="color:var(--text3)">+ ' + (models.length - 5) + ' autres…</span>';
      }
    }).catch(function () {
      $('rvcModelsCount').textContent = '?';
      $('rvcModelsSummary').textContent = 'Impossible de charger la liste';
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

    // V3 — Cloud panel
    var bSave = $('btnSaveRunpod'); if (bSave) bSave.addEventListener('click', saveRunpod);
    var bTest = $('btnTestRunpod'); if (bTest) bTest.addEventListener('click', testRunpod);
    var bSaveO = $('btnSaveOpenai'); if (bSaveO) bSaveO.addEventListener('click', saveOpenai);
    var bTestO = $('btnTestOpenai'); if (bTestO) bTestO.addEventListener('click', testOpenai);

    // V3 — Glossaire
    var bAddG = $('btnAddGlossary'); if (bAddG) bAddG.addEventListener('click', addGlossaryEntry);
    var bSavG = $('btnSaveGlossary'); if (bSavG) bSavG.addEventListener('click', saveGlossary);

    // Chargements V3 (silencieux, n'interrompent pas la page si endpoints absents)
    loadCloudPanel().catch(function () {});
    loadProvidersPanel().catch(function () {});
    loadGlossary().catch(function () {});
    loadRvcPanel().catch(function () {});
  });
})();
