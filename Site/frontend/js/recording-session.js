// VoiceBridge — recording-session.js
// Wizard d'enregistrement RVC : 5 blocs textes + retraitement async + validation.
//
// Réutilise live-worklet.js (même format PCM 16k mono int16) mais buffer
// côté client + POST batch toutes les ~3s vers /api/recording_session/{id}/append_chunk

(function () {
  function $(id) { return document.getElementById(id); }
  function $$(sel, r) { return Array.prototype.slice.call((r || document).querySelectorAll(sel)); }

  // Textes des 5 blocs (calibrés ~4 min chacun à débit normal)
  // Cf. doc 14-rvc-recording-guide.md pour les versions complètes.
  var BLOCKS_FR = [
    {
      title: 'Bloc 1 — Présentation neutre',
      text: 'Bonjour, je m\'appelle [votre prénom]. Je travaille dans le secteur des semences potagères depuis plusieurs années. Aujourd\'hui, je vais lire ce texte à voix haute pour entraîner un modèle de clonage vocal. L\'objectif est d\'obtenir un échantillon de ma voix dans différentes situations, avec des intonations variées. Je parle naturellement, sans forcer, comme si je m\'adressais à un collègue dans un couloir. Le micro est à environ vingt centimètres de ma bouche, dans une pièce calme. Je prends le temps de respirer entre les phrases, sans précipitation. Je vais maintenant énumérer quelques chiffres pour varier les sons : un, deux, trois, quatre, cinq, six, sept, huit, neuf, dix. Cent. Mille. Dix mille. Cent mille. Un million. Très bien, continuons avec une description simple. Le ciel est bleu. L\'herbe est verte. Le soleil brille à l\'horizon. Les oiseaux chantent dans les arbres. La rivière coule lentement dans la vallée. Voilà, ce premier bloc est terminé.',
    },
    {
      title: 'Bloc 2 — Conversation et nuances',
      text: 'Tu as vu le mail de la direction ? Apparemment, on doit revoir les chiffres de marge avant vendredi. C\'est embêtant parce que mon équipe est déjà sur trois projets en parallèle. Bon, on va se débrouiller, on a l\'habitude. Au fait, comment s\'est passée ta présentation hier ? Tu m\'avais dit que tu stressais un peu. Ah, super ! Je me doutais que ça allait bien se passer, tu connais ton sujet sur le bout des doigts. Tiens, à propos, tu viens déjeuner avec nous tout à l\'heure ? On va au petit restaurant du coin, celui avec la terrasse. Le plat du jour avait l\'air pas mal hier. Parfait, on se retrouve à midi trente devant l\'ascenseur. À tout à l\'heure ! Une autre chose : tu te souviens du dossier qu\'on avait commencé en mars ? Il faudrait qu\'on le reprenne, parce que le client a relancé. Pas urgent, mais on va devoir s\'y mettre dans les prochains jours.',
    },
    {
      title: 'Bloc 3 — Questions et exclamations',
      text: 'Vraiment ? Tu es sûr ? Je n\'arrive pas à y croire ! Comment c\'est possible ? Mais qui a fait ça ? Quand est-ce qu\'on l\'a su ? Pourquoi personne ne m\'a prévenu plus tôt ? Bon, d\'accord, on va gérer. Mais franchement, c\'est incroyable. Génial ! Bravo à toute l\'équipe ! Vous avez fait un travail remarquable. Excellente nouvelle, vraiment. Je suis super content pour vous. Attention ! Ne touche pas à ça, c\'est fragile ! Doucement, doucement. Voilà, comme ça. Et toi, qu\'est-ce que tu en penses ? Tu serais prêt à participer ? Tu as combien de temps disponible cette semaine ? Une heure, deux heures, plus ? Hé, qu\'est-ce que tu fais là ? Ça fait une éternité ! Comment vas-tu depuis tout ce temps ? Et la famille, tout va bien ? Les enfants ont bien grandi je suppose.',
    },
    {
      title: 'Bloc 4 — Formel et technique',
      text: 'Mesdames, messieurs, bonjour. Je vous remercie d\'être présents pour cette réunion trimestrielle. Nous allons aborder trois points principaux : le bilan financier du dernier trimestre, les perspectives pour le semestre à venir, et les ajustements stratégiques nécessaires. Concernant le bilan, les résultats sont conformes aux prévisions, avec une croissance organique de l\'ordre de quatre virgule deux pour cent. Le chiffre d\'affaires consolidé atteint cent vingt millions d\'euros. La marge brute s\'établit à dix-huit pour cent, en légère amélioration par rapport à l\'année précédente. Nous observons cependant une pression accrue sur les coûts d\'intrants, notamment l\'énergie et la logistique. Sur le plan opérationnel, nous avons finalisé l\'intégration du nouveau système de gestion des stocks, ce qui devrait nous permettre d\'optimiser les flux à compter du prochain exercice. Je passe maintenant la parole à mon collègue pour la partie commerciale.',
    },
    {
      title: 'Bloc 5 — Libre, lecture variée',
      text: 'Le brouillard du matin enveloppait la vallée. Les vaches paissaient tranquillement dans le pré, indifférentes à l\'agitation du monde. Sur la route, une voiture rouge passait lentement, puis une seconde. Le boulanger sortait son pain du four, comme chaque matin depuis trente ans. Au loin, on entendait le clocher de l\'église sonner sept heures. Les volets s\'ouvraient un à un, dans les maisons. Bientôt, le village s\'animait pour de bon. Madame Dupont arrosait ses géraniums. Monsieur Martin partait au travail, son cartable à la main. La petite fille du numéro douze attendait le bus scolaire, son cartable trop grand pour ses épaules. Et puis, soudain, le soleil perça les nuages. La lumière dorée envahit la place. Les pigeons s\'envolèrent en battant des ailes. C\'était un jour comme un autre, et pourtant, il portait en lui une promesse particulière. Quelque chose allait peut-être changer aujourd\'hui.',
    },
  ];

  var BLOCKS_EN = [
    {
      title: 'Block 1 — Neutral introduction',
      text: 'Hello, my name is [your first name]. I work in the seed industry, and I\'ve been doing so for several years now. Today, I will be reading this text aloud to train a voice cloning model. The goal is to capture a sample of my voice across different situations, with varied intonations. I speak naturally, without forcing, as if addressing a colleague in a hallway. The microphone is about twenty centimeters from my mouth, in a quiet room. I take time to breathe between sentences, without rushing. Let me now count out loud to vary the sounds: one, two, three, four, five, six, seven, eight, nine, ten. One hundred. One thousand. Ten thousand. One hundred thousand. One million. Very well, let\'s continue with a simple description. The sky is blue. The grass is green. The sun shines on the horizon. Birds sing in the trees. The river flows slowly through the valley. There, this first block is now complete.',
    },
    {
      title: 'Block 2 — Conversation and nuance',
      text: 'Did you see the email from management? Apparently we need to review the margin figures before Friday. It\'s a bit annoying because my team is already on three projects at once. Well, we\'ll figure it out, we always do. By the way, how did your presentation go yesterday? You told me you were a bit stressed. Oh, great! I had a feeling it would go well, you know your topic inside out. So, are you joining us for lunch later? We\'re going to the little restaurant on the corner, the one with the terrace. The daily special looked decent yesterday. Perfect, see you at twelve thirty by the elevator. Catch you later! One other thing: do you remember that file we started in March? We should pick it up again, because the client has been asking. Not urgent, but we\'ll need to get back to it in the coming days.',
    },
    {
      title: 'Block 3 — Questions and exclamations',
      text: 'Really? Are you sure? I can\'t believe it! How is that possible? Who did this? When did we find out? Why did no one tell me sooner? Alright, fine, we\'ll handle it. But honestly, it\'s incredible. Awesome! Well done to the whole team! You\'ve done remarkable work. Excellent news, really. I\'m so happy for you. Watch out! Don\'t touch that, it\'s fragile! Gently, gently. There, like that. And you, what do you think? Would you be willing to participate? How much time do you have available this week? An hour, two hours, more? Hey, what are you doing here? It\'s been ages! How have you been all this time? And the family, everyone doing well? The kids must have grown up by now I suppose.',
    },
    {
      title: 'Block 4 — Formal and technical',
      text: 'Ladies and gentlemen, good morning. Thank you for being present at this quarterly meeting. We will cover three main points: the financial results of the last quarter, the outlook for the coming semester, and the necessary strategic adjustments. Regarding the results, they are in line with forecasts, with organic growth of around four point two percent. Consolidated revenue reaches one hundred and twenty million euros. The gross margin stands at eighteen percent, a slight improvement over the previous year. However, we are seeing increased pressure on input costs, particularly energy and logistics. On the operational side, we have finalized the integration of the new inventory management system, which should allow us to optimize flows starting from the next fiscal year. I will now hand over to my colleague for the commercial section.',
    },
    {
      title: 'Block 5 — Free reading',
      text: 'The morning fog wrapped around the valley. Cows grazed peacefully in the meadow, indifferent to the world\'s commotion. On the road, a red car passed slowly, then a second one. The baker pulled his bread from the oven, as he had every morning for thirty years. In the distance, the church bell rang seven o\'clock. Shutters opened one by one in the houses. Soon, the village came alive in earnest. Mrs. Dupont was watering her geraniums. Mr. Martin was leaving for work, briefcase in hand. The little girl from number twelve was waiting for the school bus, her schoolbag too big for her shoulders. And then, suddenly, the sun broke through the clouds. Golden light flooded the square. Pigeons took flight, flapping their wings. It was a day like any other, and yet, it carried a special promise within it. Something might change today.',
    },
  ];

  var sessionId = null;
  var currentBlock = 1;
  var blocks = [];        // BLOCKS_FR ou BLOCKS_EN selon langue
  var blockDurations = [0, 0, 0, 0, 0, 0]; // index 0 ignoré
  var totalDurationSec = 0;

  // Capture audio
  var audioCtx = null, sourceNode = null, workletNode = null, mediaStream = null;
  var recording = false;
  var pendingChunks = [];   // Int16Array accumulés depuis le dernier batch
  var batchTimer = null;
  var blockStartTime = 0;

  function fmtTime(sec) {
    sec = Math.max(0, Math.floor(sec));
    var m = Math.floor(sec / 60), s = sec % 60;
    return m + ':' + (s < 10 ? '0' : '') + s;
  }

  function setBlock(num) {
    currentBlock = num;
    blocks = $('recLanguage').value === 'en' ? BLOCKS_EN : BLOCKS_FR;
    var b = blocks[num - 1];
    $('currentBlockNum').textContent = num;
    $('currentBlockTitle').textContent = b.title;
    $('blockText').textContent = b.text;
    $('blockProgress').textContent = num + '/5';
    $('blockDuration').textContent = fmtTime(blockDurations[num] || 0);
    $('btnPrevBlock').disabled = (num === 1);
    $('btnNextBlock').textContent = num === 5 ? 'Terminer →' : 'Bloc suivant →';
    $('recMicLabel').textContent = recording ? '⏹ Cliquez pour arrêter' : 'Cliquez pour démarrer ce bloc';
    $('recMicZone').classList.toggle('recording', recording);
  }

  // ── Capture micro via AudioWorklet (réutilise live-worklet.js) ──

  function startRecording() {
    if (recording) return;
    navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: false,    // on veut le bruit pour Silero/noisereduce post
        autoGainControl: false,     // idem, on contrôle au retraitement
      },
    }).then(function (s) {
      mediaStream = s;
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      return audioCtx.audioWorklet.addModule('/js/live-worklet.js').then(function () {
        sourceNode = audioCtx.createMediaStreamSource(mediaStream);
        workletNode = new AudioWorkletNode(audioCtx, 'pcm-capture');
        workletNode.port.onmessage = onPcmChunk;
        sourceNode.connect(workletNode);
        recording = true;
        blockStartTime = Date.now();
        $('recMicZone').classList.add('recording');
        $('recMicLabel').textContent = '⏹ Cliquez pour arrêter';
        // Flush du batch toutes les 3 s
        batchTimer = setInterval(flushBatch, 3000);
        tickDuration();
      });
    }).catch(function (err) {
      VB.notify('error', 'Accès micro refusé : ' + (err.message || err));
    });
  }

  function onPcmChunk(e) {
    if (!recording) return;
    pendingChunks.push(new Uint8Array(e.data));
  }

  function flushBatch() {
    if (!sessionId || pendingChunks.length === 0) return;
    var totalLen = pendingChunks.reduce(function (s, c) { return s + c.length; }, 0);
    var merged = new Uint8Array(totalLen);
    var off = 0;
    pendingChunks.forEach(function (c) { merged.set(c, off); off += c.length; });
    pendingChunks = [];
    fetch('/api/recording_session/' + sessionId + '/append_chunk?block=' + currentBlock, {
      method: 'POST',
      headers: { 'Content-Type': 'application/octet-stream' },
      body: merged,
    }).then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.duration_s != null) {
          blockDurations[currentBlock] = d.duration_s;
          $('blockDuration').textContent = fmtTime(d.duration_s);
          updateTotal();
        }
      })
      .catch(function (err) {
        console.warn('append_chunk failed', err);
      });
  }

  function tickDuration() {
    if (!recording) return;
    var sec = blockDurations[currentBlock] + Math.floor((Date.now() - blockStartTime) / 1000);
    $('blockDuration').textContent = fmtTime(sec);
    setTimeout(tickDuration, 250);
  }

  function updateTotal() {
    totalDurationSec = blockDurations.slice(1).reduce(function (a, b) { return a + b; }, 0);
    $('totalDuration').textContent = fmtTime(totalDurationSec);
  }

  function stopRecording() {
    if (!recording) return;
    recording = false;
    flushBatch();
    if (batchTimer) { clearInterval(batchTimer); batchTimer = null; }
    try { if (workletNode) workletNode.disconnect(); } catch (e) {}
    try { if (sourceNode) sourceNode.disconnect(); } catch (e) {}
    if (mediaStream) {
      mediaStream.getTracks().forEach(function (t) { t.stop(); });
    }
    if (audioCtx) { audioCtx.close().catch(function () {}); }
    $('recMicZone').classList.remove('recording');
    $('recMicLabel').textContent = 'Cliquez pour démarrer ce bloc';

    // Marquer le bloc comme terminé côté serveur
    if (sessionId) {
      VB.api.post('/api/recording_session/' + sessionId + '/finish_block',
                   { block: currentBlock })
        .catch(function () {});
    }
  }

  // ── Navigation ──

  function startSession() {
    var name = $('recName').value.trim();
    if (!name) { VB.notify('warning', 'Donne un nom à la session'); return; }
    var lang = $('recLanguage').value;
    VB.api.post('/api/recording_session/create', { name: name, language: lang })
      .then(function (s) {
        sessionId = s.id;
        $('stepIntro').style.display = 'none';
        $('stepBlocks').style.display = '';
        setBlock(1);
      })
      .catch(function (err) {
        VB.notify('error', err.message || 'Échec création session');
      });
  }

  function gotoNextBlock() {
    if (recording) stopRecording();
    if (currentBlock < 5) {
      setBlock(currentBlock + 1);
    } else {
      // Lancer le retraitement
      $('stepBlocks').style.display = 'none';
      $('stepProcess').style.display = '';
      VB.api.post('/api/recording_session/' + sessionId + '/process', {
        denoise_strength: 0.7, min_clip_seconds: 5, max_clip_seconds: 15,
      }).then(function (resp) {
        VB.progress.attachBar($('processProgressArea'), resp.task_id, {
          onDone: function () {
            $('processDone').style.display = '';
          },
          onError: function (snap) {
            VB.notify('error', 'Retraitement échoué : ' + (snap.error || ''));
          },
        });
      }).catch(function (err) {
        VB.notify('error', err.message || 'Échec lancement retraitement');
      });
    }
  }

  function gotoPrevBlock() {
    if (recording) stopRecording();
    if (currentBlock > 1) setBlock(currentBlock - 1);
  }

  // ── Validation ──

  function gotoValidation() {
    $('stepProcess').style.display = 'none';
    $('stepValidate').style.display = '';
    VB.api.get('/api/recording_session/' + sessionId + '/processed').then(function (data) {
      var report = data.quality_report || {};
      $('qualityScore').textContent = (report.score != null ? report.score : '—') + ' / 100';
      $('qualityDetails').textContent =
        (report.total_clips || 0) + ' clips · ' +
        Math.round(report.total_duration_s || 0) + 's total · ' +
        'SNR moy ' + (report.snr_avg_db || 0) + ' dB';
      var list = $('clipsList');
      list.innerHTML = '';
      (data.clips || []).forEach(function (c, i) {
        var idx = parseInt(c.filename.match(/clip_(\d+)/)[1], 10);
        var div = document.createElement('div');
        div.className = 'card';
        div.style.padding = '0.6rem 0.85rem';
        div.style.marginBottom = '0.4rem';
        div.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:center;gap:1rem">' +
                       '<div style="font-family:\'DM Mono\',monospace;font-size:0.78rem;flex:1">' +
                          c.filename + ' · ' + c.duration_s + 's · SNR ' + c.snr_db + ' dB' +
                       '</div>' +
                       '<audio controls preload="none" src="/api/recording_session/' + sessionId + '/clip/' + idx + '/audio" style="height:32px;max-width:240px"></audio>' +
                       '<button class="btn btn-secondary" data-idx="' + idx + '" style="padding:0.3rem 0.6rem">🗑</button>' +
                       '</div>';
        list.appendChild(div);
      });
      $$('button[data-idx]', list).forEach(function (btn) {
        btn.addEventListener('click', function () {
          var idx = btn.dataset.idx;
          if (!confirm('Supprimer le clip ' + idx + ' ?')) return;
          VB.api.delete('/api/recording_session/' + sessionId + '/clip/' + idx)
            .then(function () { gotoValidation(); /* reload */ });
        });
      });
    });
  }

  function downloadZip() {
    window.location.href = '/api/recording_session/' + sessionId + '/export';
  }

  document.addEventListener('DOMContentLoaded', function () {
    $('btnStartSession').addEventListener('click', startSession);
    $('btnNextBlock').addEventListener('click', gotoNextBlock);
    $('btnPrevBlock').addEventListener('click', gotoPrevBlock);
    $('btnGoValidate').addEventListener('click', gotoValidation);
    $('btnDownloadZip').addEventListener('click', downloadZip);
    $('recMicZone').addEventListener('click', function () {
      if (recording) stopRecording();
      else startRecording();
    });
    $('recLanguage').addEventListener('change', function () {
      // Si on a déjà commencé un bloc, on garde la langue choisie au démarrage
      if (!sessionId) blocks = $('recLanguage').value === 'en' ? BLOCKS_EN : BLOCKS_FR;
    });
  });
})();
