// VoiceBridge — rvc-import.js
// Wizard d'upload .pth + .index vers RunPod Network Volume.

(function () {
  function $(id) { return document.getElementById(id); }

  var pthFile = null;
  var indexFile = null;

  function bindDropZone(zoneId, inputId, fileNameId, accept, onPick) {
    var zone = $(zoneId);
    var input = $(inputId);
    var nameDiv = $(fileNameId);
    if (!zone || !input) return;

    zone.addEventListener('click', function () { input.click(); });
    zone.addEventListener('dragover', function (e) {
      e.preventDefault();
      zone.classList.add('dragover');
    });
    zone.addEventListener('dragleave', function () {
      zone.classList.remove('dragover');
    });
    zone.addEventListener('drop', function (e) {
      e.preventDefault();
      zone.classList.remove('dragover');
      if (e.dataTransfer.files.length > 0) handleFile(e.dataTransfer.files[0]);
    });
    input.addEventListener('change', function (e) {
      if (e.target.files.length > 0) handleFile(e.target.files[0]);
    });
    function handleFile(f) {
      if (accept && !f.name.toLowerCase().endsWith(accept)) {
        VB.notify('warning', 'Format attendu : ' + accept);
        return;
      }
      onPick(f);
      nameDiv.textContent = f.name + ' (' + (f.size / (1024 * 1024)).toFixed(1) + ' Mo)';
      nameDiv.style.display = '';
    }
  }

  function loadVoices() {
    VB.api.get('/api/voices').then(function (d) {
      var sel = $('rvcVoiceId');
      (d.voices || []).forEach(function (v) {
        var opt = document.createElement('option');
        opt.value = v.id;
        opt.textContent = (v.language === 'fr' ? '🇫🇷' : '🇬🇧') + ' ' + v.name;
        sel.appendChild(opt);
      });
    });
  }

  function checkCloud() {
    return VB.api.get('/api/cloud/status').then(function (d) {
      if (!d.runpod_configured) {
        $('cloudCheckAlert').style.display = '';
        $('btnUploadRvc').disabled = true;
      }
    }).catch(function () {});
  }

  function doUpload() {
    if (!pthFile) {
      VB.notify('warning', 'Sélectionne un fichier .pth');
      return;
    }
    var name = $('rvcName').value.trim();
    if (!name) {
      VB.notify('warning', 'Donne un nom au modèle');
      return;
    }

    var btn = $('btnUploadRvc');
    btn.disabled = true;
    btn.textContent = '⏳ Validation + Upload…';

    var fd = new FormData();
    fd.append('pth_file', pthFile);
    if (indexFile) fd.append('index_file', indexFile);
    fd.append('name', name);
    fd.append('description', $('rvcDescription').value.trim());
    var voiceId = $('rvcVoiceId').value;
    if (voiceId) fd.append('voice_id', voiceId);

    fetch('/api/rvc/models', { method: 'POST', body: fd })
      .then(function (r) {
        if (!r.ok) {
          return r.json().then(function (j) {
            throw new Error((j.detail && j.detail.message) || j.detail || ('HTTP ' + r.status));
          });
        }
        return r.json();
      })
      .then(function (resp) {
        // resp = { model_id, task_id, status: "uploading" }
        VB.notify('info', 'Upload en cours…');
        var area = $('uploadProgressArea');
        area.classList.add('visible');
        VB.progress.attachBar(area, resp.task_id, {
          onDone: function () {
            $('uploadDone').style.display = '';
            btn.textContent = '⬆ Uploader vers RunPod';
            // ne pas re-enable, l'upload est fini
          },
          onError: function (snap) {
            VB.notify('error', 'Upload échoué : ' + (snap.error || ''));
            btn.disabled = false;
            btn.textContent = '⬆ Uploader vers RunPod';
          },
        });
      })
      .catch(function (err) {
        VB.notify('error', 'Échec : ' + (err.message || err));
        btn.disabled = false;
        btn.textContent = '⬆ Uploader vers RunPod';
      });
  }

  document.addEventListener('DOMContentLoaded', function () {
    bindDropZone('dropPth', 'pthFileInput', 'pthFileName', '.pth',
                  function (f) { pthFile = f; });
    bindDropZone('dropIndex', 'indexFileInput', 'indexFileName', '.index',
                  function (f) { indexFile = f; });
    $('btnUploadRvc').addEventListener('click', doUpload);
    loadVoices();
    checkCloud();
  });
})();
