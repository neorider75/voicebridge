// VoiceBridge — recording-session.js
// Wizard d'enregistrement RVC : 5 blocs textes + retraitement async + validation.
//
// Réutilise live-worklet.js (même format PCM 16k mono int16) mais buffer
// côté client + POST batch toutes les ~3s vers /api/recording_session/{id}/append_chunk

(function () {
  function $(id) { return document.getElementById(id); }
  function $$(sel, r) { return Array.prototype.slice.call((r || document).querySelectorAll(sel)); }

  // Textes des 5 blocs (calibrés ~5 min chacun à débit normal, ~700-800 mots)
  // Cf. doc 14-rvc-recording-guide.md pour les versions complètes.
  var BLOCKS_FR = [
    {
      title: 'Bloc 1 — Présentation neutre',
      text: 'Bonjour, je m\'appelle [votre prénom]. Aujourd\'hui, je vais lire ce texte à voix haute pour entraîner un modèle de clonage vocal. L\'objectif est d\'obtenir un échantillon de ma voix dans différentes situations, avec des intonations variées. Je parle naturellement, sans forcer, comme si je m\'adressais à un collègue dans un couloir. Le micro est à environ vingt centimètres de ma bouche, dans une pièce calme. Je prends le temps de respirer entre les phrases, sans précipitation. Je vais maintenant énumérer quelques chiffres pour varier les sons : un, deux, trois, quatre, cinq, six, sept, huit, neuf, dix. Onze, douze, treize, quatorze, quinze, seize, dix-sept, dix-huit, dix-neuf, vingt. Trente, quarante, cinquante, soixante, soixante-dix, quatre-vingts, quatre-vingt-dix, cent. Mille. Dix mille. Cent mille. Un million. Un milliard. Continuons avec les jours de la semaine : lundi, mardi, mercredi, jeudi, vendredi, samedi, dimanche. Et les mois de l\'année : janvier, février, mars, avril, mai, juin, juillet, août, septembre, octobre, novembre, décembre. Très bien, continuons avec une description simple. Le ciel est bleu. L\'herbe est verte. Le soleil brille à l\'horizon. Les oiseaux chantent dans les arbres. La rivière coule lentement dans la vallée. Le vent souffle doucement à travers les feuilles. Les nuages dérivent paresseusement vers l\'est. Une abeille bourdonne autour d\'une fleur de lavande. Dans le jardin, les rosiers commencent à fleurir. Les tomates rougissent sur leurs tiges. Le chat de la voisine se prélasse au soleil sur un muret de pierre. Plus loin, un tracteur traverse un champ de blé jaune. Le boulanger ferme sa boutique pour la pause déjeuner. La place du village est presque déserte à cette heure. Quelques pigeons picorent des miettes près de la fontaine. Voilà pour la description. Pour finir, voici quelques mots à articuler distinctement : anticonstitutionnellement, anniversaire, philosophie, mathématiques, encyclopédie, kinésithérapeute, ornithologue, archéologue. Et quelques phrases plus longues pour varier la prosodie : il était une fois, dans un petit village au bord de la mer, un vieux marin qui passait ses journées à raconter ses voyages aux enfants curieux. Voilà, ce premier bloc est terminé.',
    },
    {
      title: 'Bloc 2 — Conversation et nuances',
      text: 'Tu as vu le mail de la direction ? Apparemment, on doit revoir les chiffres de marge avant vendredi. C\'est embêtant parce que mon équipe est déjà sur trois projets en parallèle. Bon, on va se débrouiller, on a l\'habitude. Au fait, comment s\'est passée ta présentation hier ? Tu m\'avais dit que tu stressais un peu. Ah, super ! Je me doutais que ça allait bien se passer, tu connais ton sujet sur le bout des doigts. Tiens, à propos, tu viens déjeuner avec nous tout à l\'heure ? On va au petit restaurant du coin, celui avec la terrasse. Le plat du jour avait l\'air pas mal hier. Parfait, on se retrouve à midi trente devant l\'ascenseur. À tout à l\'heure ! Une autre chose : tu te souviens du dossier qu\'on avait commencé en mars ? Il faudrait qu\'on le reprenne, parce que le client a relancé. Pas urgent, mais on va devoir s\'y mettre dans les prochains jours. Au passage, est-ce que tu as eu le temps de relire la note que je t\'ai envoyée mardi ? J\'aimerais avoir ton avis avant de la transmettre plus haut. Il y a deux ou trois passages où je ne suis pas totalement sûr de la formulation. Tu sais, j\'hésite toujours entre rester précis sur les chiffres ou laisser une marge d\'interprétation pour la suite des discussions. Bon, c\'est pas si grave, on en reparle. Sinon, ce week-end tu fais quoi ? Moi je pars dans le Sud avec ma famille, on a loué une petite maison près de la mer. J\'espère qu\'il fera beau, parce que les prévisions ne sont pas formidables. La dernière fois qu\'on y est allés, il a plu pendant trois jours d\'affilée. Les enfants étaient déçus, et moi aussi pour être honnête. Mais bon, on s\'est rabattus sur le cinéma et les jeux de société, et finalement on a passé un bon moment. Ah, j\'allais oublier : tu pourras me transférer les coordonnées du prestataire dont tu m\'avais parlé ? Celui qui fait de la traduction automatique. Je voudrais lui demander un devis pour notre prochaine campagne internationale. Merci d\'avance, c\'est sympa. Bon allez, je file, j\'ai une réunion dans cinq minutes. On se voit ce midi !',
    },
    {
      title: 'Bloc 3 — Questions et exclamations',
      text: 'Vraiment ? Tu es sûr ? Je n\'arrive pas à y croire ! Comment c\'est possible ? Mais qui a fait ça ? Quand est-ce qu\'on l\'a su ? Pourquoi personne ne m\'a prévenu plus tôt ? Bon, d\'accord, on va gérer. Mais franchement, c\'est incroyable. Génial ! Bravo à toute l\'équipe ! Vous avez fait un travail remarquable. Excellente nouvelle, vraiment. Je suis super content pour vous. Attention ! Ne touche pas à ça, c\'est fragile ! Doucement, doucement. Voilà, comme ça. Et toi, qu\'est-ce que tu en penses ? Tu serais prêt à participer ? Tu as combien de temps disponible cette semaine ? Une heure, deux heures, plus ? Hé, qu\'est-ce que tu fais là ? Ça fait une éternité ! Comment vas-tu depuis tout ce temps ? Et la famille, tout va bien ? Les enfants ont bien grandi je suppose. Mais dis-moi, tu n\'aurais pas un peu changé ? Tu as quelque chose de différent, je ne saurais pas dire quoi exactement. Une nouvelle coupe de cheveux peut-être ? Non ? Ah, je dois rêver alors. Bon, et sinon, qu\'est-ce que tu deviens professionnellement ? Toujours dans la même boîte ? Ah bon, tu as changé ! Et ça te plaît ? C\'est dans quelle ville exactement ? Loin de chez toi ? Tu fais comment pour le trajet le matin ? En voiture, en train, à vélo ? D\'accord, je vois. Et au niveau salaire, c\'est mieux ? Bon, je ne devrais pas demander ça, désolé, c\'est indiscret. Allez, raconte-moi plutôt tes dernières vacances ! Tu es parti où cette année ? L\'Italie ? Sérieusement ? Tu as adoré, j\'imagine ! Quelle région exactement ? La Toscane, la Sicile, les Pouilles ? La Toscane, j\'aurais parié ! Tu as visité Florence, Sienne, Pise ? Tout ça ? C\'est magnifique cette région. Et qu\'est-ce qui t\'a le plus marqué ? Les paysages, la nourriture, l\'architecture, les gens ? La nourriture, évidemment ! Qui pourrait résister aux pâtes fraîches et aux glaces italiennes ? Personne ! Bon allez, il faut vraiment que j\'y aille maintenant. On se reparle bientôt, promis. Prends soin de toi, et embrasse les enfants de ma part. À très vite !',
    },
    {
      title: 'Bloc 4 — Formel et technique',
      text: 'Mesdames, messieurs, bonjour. Je vous remercie d\'être présents pour cette réunion trimestrielle. Nous allons aborder trois points principaux : le bilan financier du dernier trimestre, les perspectives pour le semestre à venir, et les ajustements stratégiques nécessaires. Concernant le bilan, les résultats sont conformes aux prévisions, avec une croissance organique de l\'ordre de quatre virgule deux pour cent. Le chiffre d\'affaires consolidé atteint cent vingt millions d\'euros. La marge brute s\'établit à dix-huit pour cent, en légère amélioration par rapport à l\'année précédente. Nous observons cependant une pression accrue sur les coûts d\'intrants, notamment l\'énergie et la logistique. Sur le plan opérationnel, nous avons finalisé l\'intégration du nouveau système de gestion des stocks, ce qui devrait nous permettre d\'optimiser les flux à compter du prochain exercice. Je passe maintenant la parole à mon collègue pour la partie commerciale. Mais avant cela, permettez-moi de revenir brièvement sur quelques indicateurs clés. Le délai moyen de traitement des commandes est passé de douze à neuf jours, ce qui représente une amélioration significative. Le taux de satisfaction client, mesuré par notre enquête annuelle, ressort à quatre-vingt-sept pour cent, en hausse de trois points. Le taux d\'absentéisme reste stable à quatre virgule un pour cent, légèrement en dessous de la moyenne du secteur. En matière de cybersécurité, nous avons déployé une nouvelle solution d\'authentification multi-facteurs sur l\'ensemble des postes de travail. Les premiers retours sont positifs, malgré quelques résistances initiales liées au changement d\'habitudes. La formation continue, avec déjà soixante-douze pour cent des collaborateurs formés au nouveau dispositif. S\'agissant des projets stratégiques, le programme de transformation digitale avance conformément au calendrier. Les trois chantiers prioritaires — refonte du système d\'information, automatisation des processus administratifs, et déploiement de l\'intelligence artificielle générative — devraient livrer leurs premiers bénéfices avant la fin du semestre. Nous restons toutefois vigilants sur les enjeux d\'adoption par les équipes, qui constituent à mon sens le principal facteur de succès. En conclusion, malgré un contexte économique incertain et des tensions géopolitiques persistantes, notre entreprise poursuit sa trajectoire de croissance maîtrisée. Je vous remercie de votre attention, et je suis maintenant prêt à répondre à vos questions.',
    },
    {
      title: 'Bloc 5 — Libre, lecture variée',
      text: 'Le brouillard du matin enveloppait la vallée. Les vaches paissaient tranquillement dans le pré, indifférentes à l\'agitation du monde. Sur la route, une voiture rouge passait lentement, puis une seconde. Le boulanger sortait son pain du four, comme chaque matin depuis trente ans. Au loin, on entendait le clocher de l\'église sonner sept heures. Les volets s\'ouvraient un à un, dans les maisons. Bientôt, le village s\'animait pour de bon. Madame Dupont arrosait ses géraniums. Monsieur Martin partait au travail, son cartable à la main. La petite fille du numéro douze attendait le bus scolaire, son cartable trop grand pour ses épaules. Et puis, soudain, le soleil perça les nuages. La lumière dorée envahit la place. Les pigeons s\'envolèrent en battant des ailes. C\'était un jour comme un autre, et pourtant, il portait en lui une promesse particulière. Quelque chose allait peut-être changer aujourd\'hui. Personne ne savait quoi exactement, mais l\'air avait ce parfum de printemps, ce mélange subtil de terre humide et de bourgeons à peine éclos, qui annonce souvent les bonnes nouvelles. Sur le marché, les étals se garnissaient peu à peu. Le poissonnier disposait ses sardines argentées sur un lit de glace pilée. La marchande de fruits empilait des oranges sanguines en pyramide, attentive à ne pas faire dégringoler l\'édifice. Le fromager taillait des morceaux de comté pour les habitués qui passeraient avant l\'heure du déjeuner. Une odeur de café flottait depuis le bistrot d\'en face, où trois retraités refaisaient le monde devant leurs tasses à demi vides. Plus loin, deux adolescents discutaient avec animation, sac à dos sur l\'épaule, en route vers le lycée. L\'un riait fort, l\'autre semblait préoccupé par un contrôle de mathématiques qui l\'attendait à neuf heures. Au-dessus, dans le ciel maintenant dégagé, une montgolfière apparut. Elle dérivait silencieusement, ronde et colorée, projetant une ombre furtive sur les toits d\'ardoise. Des enfants la pointèrent du doigt en sortant de chez le coiffeur. Un vieil homme, assis sur un banc, leva les yeux et sourit. Il pensa à sa femme, disparue dix ans plus tôt, qui aurait adoré ce spectacle. Le temps semblait suspendu pendant ces quelques minutes. Puis la montgolfière disparut derrière la colline, et la vie reprit son cours habituel, avec ses petits riens et ses grandes promesses du quotidien.',
    },
  ];

  var BLOCKS_EN = [
    {
      title: 'Block 1 — Neutral introduction',
      text: 'Hello, my name is [your first name]. Today, I will be reading this text aloud to train a voice cloning model. The goal is to capture a sample of my voice across different situations, with varied intonations. I speak naturally, without forcing, as if addressing a colleague in a hallway. The microphone is about twenty centimeters from my mouth, in a quiet room. I take time to breathe between sentences, without rushing. Let me now count out loud to vary the sounds: one, two, three, four, five, six, seven, eight, nine, ten. Eleven, twelve, thirteen, fourteen, fifteen, sixteen, seventeen, eighteen, nineteen, twenty. Thirty, forty, fifty, sixty, seventy, eighty, ninety, one hundred. One thousand. Ten thousand. One hundred thousand. One million. One billion. Let me continue with the days of the week: Monday, Tuesday, Wednesday, Thursday, Friday, Saturday, Sunday. And the months of the year: January, February, March, April, May, June, July, August, September, October, November, December. Very well, let\'s continue with a simple description. The sky is blue. The grass is green. The sun shines on the horizon. Birds sing in the trees. The river flows slowly through the valley. The wind blows gently through the leaves. Clouds drift lazily towards the east. A bee buzzes around a lavender flower. In the garden, the rose bushes are starting to bloom. Tomatoes are ripening on their stems. The neighbor\'s cat is sunbathing on a stone wall. Further away, a tractor crosses a field of golden wheat. The baker closes his shop for the lunch break. The village square is almost deserted at this hour. A few pigeons peck at crumbs near the fountain. So much for the description. Now, here are a few words to articulate clearly: encyclopedia, philosophy, mathematics, ornithologist, archaeologist, kinesiotherapist, anticonstitutional, refrigerator. And some longer sentences to vary the prosody: once upon a time, in a small village by the sea, there lived an old sailor who spent his days telling stories of his voyages to curious children. There, this first block is now complete.',
    },
    {
      title: 'Block 2 — Conversation and nuance',
      text: 'Did you see the email from management? Apparently we need to review the margin figures before Friday. It\'s a bit annoying because my team is already on three projects at once. Well, we\'ll figure it out, we always do. By the way, how did your presentation go yesterday? You told me you were a bit stressed. Oh, great! I had a feeling it would go well, you know your topic inside out. So, are you joining us for lunch later? We\'re going to the little restaurant on the corner, the one with the terrace. The daily special looked decent yesterday. Perfect, see you at twelve thirty by the elevator. Catch you later! One other thing: do you remember that file we started in March? We should pick it up again, because the client has been asking. Not urgent, but we\'ll need to get back to it in the coming days. By the way, did you have time to review the note I sent you on Tuesday? I\'d like your opinion before passing it up the chain. There are two or three passages where I\'m not entirely sure about the wording. You know, I always hesitate between staying precise on the numbers or leaving some room for interpretation in the subsequent discussions. Well, it\'s not that critical, we can talk about it later. So, what are you up to this weekend? I\'m heading south with my family, we rented a small house near the sea. I hope the weather will be nice, because the forecast isn\'t great. Last time we went, it rained for three days straight. The kids were disappointed, and honestly so was I. But we fell back on the cinema and board games, and ended up having a good time anyway. Oh, I almost forgot: could you forward me the contact details of that provider you mentioned? The one who does machine translation. I want to ask them for a quote for our next international campaign. Thanks in advance, that\'s nice of you. Alright, I have to run, I\'ve got a meeting in five minutes. See you at lunch!',
    },
    {
      title: 'Block 3 — Questions and exclamations',
      text: 'Really? Are you sure? I can\'t believe it! How is that possible? Who did this? When did we find out? Why did no one tell me sooner? Alright, fine, we\'ll handle it. But honestly, it\'s incredible. Awesome! Well done to the whole team! You\'ve done remarkable work. Excellent news, really. I\'m so happy for you. Watch out! Don\'t touch that, it\'s fragile! Gently, gently. There, like that. And you, what do you think? Would you be willing to participate? How much time do you have available this week? An hour, two hours, more? Hey, what are you doing here? It\'s been ages! How have you been all this time? And the family, everyone doing well? The kids must have grown up by now I suppose. But tell me, haven\'t you changed a bit? There\'s something different about you, I can\'t quite put my finger on it. A new haircut maybe? No? Oh, I must be imagining things then. Anyway, what about work, what are you up to professionally? Still at the same company? Oh really, you switched! And do you like it? Which city is it in exactly? Far from home? How do you handle the commute in the morning? By car, train, bike? Got it. And salary-wise, is it better? Well, I shouldn\'t ask that, sorry, that\'s rude. Tell me about your last vacation instead! Where did you go this year? Italy? Seriously? You must have loved it! Which region exactly? Tuscany, Sicily, Puglia? Tuscany, I would have bet on it! Did you visit Florence, Siena, Pisa? All of them? It\'s a magnificent region. And what struck you the most? The landscapes, the food, the architecture, the people? The food, obviously! Who could resist fresh pasta and Italian ice cream? Nobody! Alright, I really have to go now. We\'ll talk again soon, I promise. Take care of yourself, and give the kids a hug from me. See you very soon!',
    },
    {
      title: 'Block 4 — Formal and technical',
      text: 'Ladies and gentlemen, good morning. Thank you for being present at this quarterly meeting. We will cover three main points: the financial results of the last quarter, the outlook for the coming semester, and the necessary strategic adjustments. Regarding the results, they are in line with forecasts, with organic growth of around four point two percent. Consolidated revenue reaches one hundred and twenty million euros. The gross margin stands at eighteen percent, a slight improvement over the previous year. However, we are seeing increased pressure on input costs, particularly energy and logistics. On the operational side, we have finalized the integration of the new inventory management system, which should allow us to optimize flows starting from the next fiscal year. I will now hand over to my colleague for the commercial section. But before that, allow me to briefly revisit a few key indicators. The average order processing time has dropped from twelve to nine days, which represents a significant improvement. The customer satisfaction rate, measured by our annual survey, stands at eighty-seven percent, up three points. The absenteeism rate remains stable at four point one percent, slightly below the industry average. In terms of cybersecurity, we have deployed a new multi-factor authentication solution across all workstations. Initial feedback is positive, despite some initial resistance linked to changes in habits. Training is ongoing, with seventy-two percent of employees already trained on the new system. Regarding strategic projects, the digital transformation program is progressing on schedule. The three priority initiatives — overhaul of the information system, automation of administrative processes, and deployment of generative artificial intelligence — should deliver their first benefits before the end of the semester. However, we remain vigilant about adoption challenges within the teams, which I believe are the main success factor. In conclusion, despite an uncertain economic context and persistent geopolitical tensions, our company continues its trajectory of controlled growth. Thank you for your attention, and I am now ready to answer your questions.',
    },
    {
      title: 'Block 5 — Free reading',
      text: 'The morning fog wrapped around the valley. Cows grazed peacefully in the meadow, indifferent to the world\'s commotion. On the road, a red car passed slowly, then a second one. The baker pulled his bread from the oven, as he had every morning for thirty years. In the distance, the church bell rang seven o\'clock. Shutters opened one by one in the houses. Soon, the village came alive in earnest. Mrs. Dupont was watering her geraniums. Mr. Martin was leaving for work, briefcase in hand. The little girl from number twelve was waiting for the school bus, her schoolbag too big for her shoulders. And then, suddenly, the sun broke through the clouds. Golden light flooded the square. Pigeons took flight, flapping their wings. It was a day like any other, and yet, it carried a special promise within it. Something might change today. Nobody knew exactly what, but the air had that scent of spring, that subtle mix of damp earth and freshly opened buds that often heralds good news. At the market, stalls were gradually being set up. The fishmonger arranged silvery sardines on a bed of crushed ice. The fruit vendor stacked blood oranges into a pyramid, careful not to topple the structure. The cheesemonger cut pieces of Comté for regular customers who would drop by before lunch. The smell of coffee floated over from the bistro across the street, where three retirees were putting the world to rights over their half-empty cups. Further along, two teenagers were chatting animatedly, backpacks slung over their shoulders, heading to school. One was laughing loudly, the other seemed worried about a math test waiting for him at nine. Above, in the now clear sky, a hot air balloon appeared. It drifted silently, round and colorful, casting a fleeting shadow over the slate rooftops. Children pointed at it as they came out of the barber shop. An old man sitting on a bench looked up and smiled. He thought of his wife, who had passed away ten years earlier, and who would have loved this sight. Time seemed suspended during those few minutes. Then the balloon disappeared behind the hill, and life resumed its usual course, with its small nothings and its great everyday promises.',
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
        $('stepMicTest').style.display = '';
        resetMicTestUI();
      })
      .catch(function (err) {
        VB.notify('error', err.message || 'Échec création session');
      });
  }

  // ── Test micro (silence + parole + score qualité) ──

  var micTestCtx = null;
  var micTestStream = null;
  var micTestSource = null;
  var micTestWorklet = null;
  var micTestSampleBuffer = [];   // tampon des chunks Int16 (Uint8Array) en cours de phase

  function resetMicTestUI() {
    $('micTestIcon').textContent = '🎙';
    $('micTestLabel').textContent = 'Prêt ?';
    $('micTestSubLabel').textContent = 'Clique pour démarrer le test du micro.';
    $('micTestPhrase').style.display = 'none';
    $('micTestProgress').style.display = 'none';
    $('micTestMeter').style.display = 'none';
    $('micTestResult').style.display = 'none';
    $('micTestStartBtnWrap').style.display = '';
    $('micTestProgressBar').style.width = '0%';
    $('micTestMeterBar').style.width = '0%';
  }

  function stopMicTestStream() {
    try { if (micTestWorklet) micTestWorklet.disconnect(); } catch (e) {}
    try { if (micTestSource) micTestSource.disconnect(); } catch (e) {}
    if (micTestStream) {
      micTestStream.getTracks().forEach(function (t) { t.stop(); });
    }
    if (micTestCtx) { micTestCtx.close().catch(function () {}); }
    micTestCtx = null; micTestStream = null;
    micTestSource = null; micTestWorklet = null;
  }

  // Calcule RMS et peak (en [0..1]) d'un buffer Int16 (Uint8Array)
  function analyzeChunks(chunks) {
    var totalLen = chunks.reduce(function (s, c) { return s + c.length; }, 0);
    if (totalLen === 0) return { rms: 0, peak: 0, samples: 0 };
    var sumSq = 0;
    var peak = 0;
    var nSamples = 0;
    chunks.forEach(function (c) {
      // c est Uint8Array, on lit en Int16 little-endian
      var dv = new DataView(c.buffer, c.byteOffset, c.byteLength);
      for (var i = 0; i + 1 < c.length; i += 2) {
        var s = dv.getInt16(i, true) / 32768; // normalisé [-1..1]
        sumSq += s * s;
        var a = Math.abs(s);
        if (a > peak) peak = a;
        nSamples++;
      }
    });
    return {
      rms: nSamples > 0 ? Math.sqrt(sumSq / nSamples) : 0,
      peak: peak,
      samples: nSamples,
    };
  }

  function startMicTest() {
    $('micTestStartBtnWrap').style.display = 'none';
    navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: false,
        autoGainControl: false,
      },
    }).then(function (s) {
      micTestStream = s;
      micTestCtx = new (window.AudioContext || window.webkitAudioContext)();
      return micTestCtx.audioWorklet.addModule('/js/live-worklet.js').then(function () {
        micTestSource = micTestCtx.createMediaStreamSource(micTestStream);
        micTestWorklet = new AudioWorkletNode(micTestCtx, 'pcm-capture');
        micTestWorklet.port.onmessage = function (e) {
          micTestSampleBuffer.push(new Uint8Array(e.data));
        };
        micTestSource.connect(micTestWorklet);
        runMicTestSequence();
      });
    }).catch(function (err) {
      VB.notify('error', 'Accès micro refusé : ' + (err.message || err));
      resetMicTestUI();
    });
  }

  function runPhase(opts, done) {
    // opts: {label, subLabel, icon, durationMs, phraseText, showMeter}
    $('micTestIcon').textContent = opts.icon;
    $('micTestLabel').textContent = opts.label;
    $('micTestSubLabel').textContent = opts.subLabel;
    if (opts.phraseText) {
      $('micTestPhrase').textContent = opts.phraseText;
      $('micTestPhrase').style.display = '';
    } else {
      $('micTestPhrase').style.display = 'none';
    }
    $('micTestProgress').style.display = '';
    $('micTestMeter').style.display = opts.showMeter ? '' : 'none';
    micTestSampleBuffer = [];
    var t0 = Date.now();
    var lastMeterUpdate = 0;
    function tick() {
      var elapsed = Date.now() - t0;
      var pct = Math.min(100, (elapsed / opts.durationMs) * 100);
      $('micTestProgressBar').style.width = pct + '%';
      var remaining = Math.max(0, opts.durationMs - elapsed);
      $('micTestCountdown').textContent = (remaining / 1000).toFixed(1) + ' s';
      // VU-mètre live
      if (opts.showMeter && Date.now() - lastMeterUpdate > 80) {
        lastMeterUpdate = Date.now();
        if (micTestSampleBuffer.length > 0) {
          var last = micTestSampleBuffer.slice(-3);
          var a = analyzeChunks(last);
          var meterPct = Math.min(100, (a.rms / 0.3) * 100);
          $('micTestMeterBar').style.width = meterPct + '%';
        }
      }
      if (elapsed >= opts.durationMs) {
        done(micTestSampleBuffer.slice());
      } else {
        requestAnimationFrame(tick);
      }
    }
    requestAnimationFrame(tick);
  }

  function runMicTestSequence() {
    // Petit délai pour laisser le user "se préparer"
    $('micTestIcon').textContent = '⏳';
    $('micTestLabel').textContent = 'Préparation…';
    $('micTestSubLabel').textContent = 'Test démarre dans 3 s.';
    setTimeout(function () {
      // Phase 1 : silence (3 s)
      runPhase({
        icon: '🤫',
        label: 'Reste silencieux',
        subLabel: 'On mesure le bruit ambiant. Ne parle pas.',
        durationMs: 3000,
        showMeter: false,
      }, function (silenceChunks) {
        var silenceStats = analyzeChunks(silenceChunks);
        // Phase 2 : parole (8 s) — phrase plus courte pour ne pas
        // précipiter la lecture, parle à ton rythme naturel.
        runPhase({
          icon: '🎤',
          label: 'Lis cette phrase à voix haute',
          subLabel: 'Parle à ton rythme naturel, comme pour les blocs. Tu as 8 secondes.',
          durationMs: 8000,
          showMeter: true,
          phraseText: 'Bonjour, je teste mon microphone. Je parle naturellement, ' +
                      'sans forcer, dans une pièce calme.',
        }, function (speechChunks) {
          var speechStats = analyzeChunks(speechChunks);
          stopMicTestStream();
          // Stocke les chunks pour la lecture
          lastMicTestSilenceChunks = silenceChunks;
          lastMicTestSpeechChunks = speechChunks;
          showMicTestResult(silenceStats, speechStats);
        });
      });
    }, 3000);
  }

  // ── Conversion chunks Int16 PCM 16 kHz → WAV blob lisible ──

  var lastMicTestSilenceChunks = null;
  var lastMicTestSpeechChunks = null;

  function chunksToWavBlob(chunks, sampleRate) {
    sampleRate = sampleRate || 16000;
    var totalLen = chunks.reduce(function (s, c) { return s + c.length; }, 0);
    var merged = new Uint8Array(totalLen);
    var off = 0;
    chunks.forEach(function (c) { merged.set(c, off); off += c.length; });

    var dataSize = merged.length;          // bytes
    var byteRate = sampleRate * 2;          // mono 16-bit
    var blockAlign = 2;
    var wavSize = 44 + dataSize;
    var buf = new ArrayBuffer(wavSize);
    var dv = new DataView(buf);

    function writeStr(off, s) {
      for (var i = 0; i < s.length; i++) dv.setUint8(off + i, s.charCodeAt(i));
    }
    writeStr(0, 'RIFF');
    dv.setUint32(4, 36 + dataSize, true);
    writeStr(8, 'WAVE');
    writeStr(12, 'fmt ');
    dv.setUint32(16, 16, true);            // PCM fmt chunk size
    dv.setUint16(20, 1, true);              // PCM format
    dv.setUint16(22, 1, true);              // mono
    dv.setUint32(24, sampleRate, true);
    dv.setUint32(28, byteRate, true);
    dv.setUint16(32, blockAlign, true);
    dv.setUint16(34, 16, true);             // bits per sample
    writeStr(36, 'data');
    dv.setUint32(40, dataSize, true);
    // Copie des bytes PCM
    new Uint8Array(buf, 44).set(merged);

    return new Blob([buf], { type: 'audio/wav' });
  }

  function setAudioPlayback(elemId, chunks) {
    var el = $(elemId);
    if (!el) return;
    if (!chunks || chunks.length === 0) {
      el.src = '';
      return;
    }
    // Révoque l'ancien blob URL s'il existe (évite les fuites)
    var prev = el.dataset.blobUrl;
    if (prev) { try { URL.revokeObjectURL(prev); } catch (e) {} }
    var blob = chunksToWavBlob(chunks, 16000);
    var url = URL.createObjectURL(blob);
    el.src = url;
    el.dataset.blobUrl = url;
  }

  function toDb(linear) {
    if (linear <= 0) return -120;
    return 20 * Math.log10(linear);
  }

  function showMicTestResult(silence, speech) {
    var noiseDb = toDb(silence.rms);
    var speechDb = toDb(speech.rms);
    var peakDb = toDb(speech.peak);
    var snrDb = speechDb - noiseDb;

    // Scoring (chaque métrique 0..25)
    var noiseScore = noiseDb < -55 ? 25 : noiseDb < -50 ? 22 : noiseDb < -45 ? 18
                    : noiseDb < -40 ? 12 : noiseDb < -35 ? 6 : 0;
    var speechScore;
    if (speechDb > -8) speechScore = 8;          // trop fort
    else if (speechDb > -12) speechScore = 20;
    else if (speechDb >= -22) speechScore = 25;  // sweet spot
    else if (speechDb >= -28) speechScore = 18;
    else if (speechDb >= -34) speechScore = 10;
    else speechScore = 3;                         // trop faible
    var peakScore = peakDb < -3 ? 25 : peakDb < -1.5 ? 18 : peakDb < -0.5 ? 8 : 0;
    var snrScore = snrDb > 35 ? 25 : snrDb > 28 ? 22 : snrDb > 22 ? 16
                  : snrDb > 15 ? 10 : 4;

    var total = noiseScore + speechScore + peakScore + snrScore;
    var verdict;
    if (total >= 85) verdict = '✅ Excellent — tu peux y aller';
    else if (total >= 65) verdict = '👍 Correct — quelques améliorations possibles';
    else if (total >= 40) verdict = '⚠️ Moyen — ajuste avant de continuer';
    else verdict = '❌ Insuffisant — corrige absolument';

    function row(label, value, score, status) {
      var color = score >= 20 ? '#22c55e' : score >= 10 ? '#eab308' : '#ef4444';
      return '<div style="display:flex;justify-content:space-between;align-items:center;padding:0.5rem 0;border-bottom:1px solid var(--border)">'
        + '<div><div style="font-weight:600">' + label + '</div>'
        + '<div style="font-size:0.78rem;color:var(--text3);font-family:DM Mono,monospace">' + value + '</div></div>'
        + '<div style="color:' + color + ';font-weight:600">' + status + '</div></div>';
    }

    var details = '';
    details += row('Bruit ambiant',
                   noiseDb.toFixed(1) + ' dBFS',
                   noiseScore,
                   noiseScore >= 20 ? 'Très calme' : noiseScore >= 12 ? 'Acceptable' : 'Trop bruyant');
    details += row('Niveau de voix',
                   speechDb.toFixed(1) + ' dBFS',
                   speechScore,
                   speechScore >= 20 ? 'Optimal' : speechScore >= 10 ? 'Limite' : speechDb > -12 ? 'Trop fort' : 'Trop faible');
    details += row('Pic max',
                   peakDb.toFixed(1) + ' dBFS',
                   peakScore,
                   peakScore >= 20 ? 'Pas de saturation' : peakScore >= 8 ? 'Proche saturation' : 'Saturation détectée');
    details += row('Rapport signal/bruit',
                   snrDb.toFixed(1) + ' dB',
                   snrScore,
                   snrScore >= 20 ? 'Excellent' : snrScore >= 10 ? 'Acceptable' : 'Insuffisant');

    var recos = [];
    if (noiseScore < 15) recos.push('🌬️ Réduis le bruit ambiant : ferme la fenêtre, éloigne ventilateur/clim, désactive les notifications.');
    if (speechDb < -28) recos.push('🔉 Rapproche-toi du micro ou monte le gain d\'entrée (System Settings → Sound → Input).');
    if (speechDb > -10) recos.push('🔉 Éloigne-toi du micro ou baisse le gain — risque de saturation et de plosives.');
    if (peakScore < 15) recos.push('⚠️ Saturation détectée : baisse le gain d\'entrée de quelques dB pour garder de la marge.');
    if (snrScore < 15) recos.push('📉 Le signal est trop proche du bruit ambiant : améliore le niveau de voix ou réduis le bruit.');
    if (recos.length === 0) recos.push('🎉 Setup nickel, lance les blocs.');

    $('micTestScore').textContent = total + ' / 100';
    $('micTestVerdict').textContent = verdict;
    $('micTestDetails').innerHTML = details;
    $('micTestRecommendations').innerHTML = recos.map(function (r) {
      return '<div style="margin:0.3rem 0">' + r + '</div>';
    }).join('');

    $('micTestIcon').textContent = total >= 85 ? '✅' : total >= 65 ? '👍' : total >= 40 ? '⚠️' : '❌';
    $('micTestLabel').textContent = 'Résultat';
    $('micTestSubLabel').textContent = '';
    $('micTestProgress').style.display = 'none';
    $('micTestMeter').style.display = 'none';
    $('micTestPhrase').style.display = 'none';
    $('micTestResult').style.display = '';

    // Branche les lecteurs audio (WAV générés à la volée depuis les chunks)
    setAudioPlayback('micTestPlaybackSilence', lastMicTestSilenceChunks);
    setAudioPlayback('micTestPlaybackSpeech', lastMicTestSpeechChunks);
  }

  function proceedToBlocks() {
    $('stepMicTest').style.display = 'none';
    $('stepBlocks').style.display = '';
    setBlock(1);
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
    $('btnMicTestStart').addEventListener('click', startMicTest);
    $('btnMicTestRetry').addEventListener('click', function () {
      stopMicTestStream();
      resetMicTestUI();
    });
    $('btnMicTestProceed').addEventListener('click', proceedToBlocks);
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
