# 14 - RVC Recording Guide (wizard d'enregistrement intégré)

> **Document V3 nouveau.** Spécification du wizard d'enregistrement RVC intégré à VoiceBridge + textes calibrés des 5 blocs + génération du PDF téléchargeable.

## Objectif

Permettre à l'utilisateur de produire **un dataset audio de qualité professionnelle** pour entraîner son modèle RVC, en restant entièrement guidé dans VoiceBridge.

Critères de qualité visés :
- Durée totale : 18-20 minutes d'audio "propre"
- Diversité phonétique (tous les phonèmes français couverts)
- Diversité prosodique (questions, exclamations, énumérations, neutre)
- SNR > 30 dB (peu de bruit de fond)
- Niveau crête homogène (-6 à -3 dB)

## Workflow utilisateur (haut niveau)

```
1. /rvc → "+ Préparer un nouvel enregistrement"
2. /recording-session/new → page d'accueil du wizard
   - Conseils matériel (micro USB recommandé)
   - Conseils environnement
   - Bouton "Démarrer le bloc 1"
3. /recording-session/{id}/block/1 → enregistrement bloc 1 (~5 min)
   - Texte affiché scrollable
   - VU-meter en temps réel
   - Compteur durée 00:00 / 05:00
   - Boutons Pause/Reprendre/Recommencer
   - Bouton "Bloc suivant" (verrouillé tant que < 80% de la cible)
4. ...idem blocs 2 à 5
5. /recording-session/{id}/finalize → traitement
   - Bouton "Traiter le dataset" → lance phase backend (cf. 12-rvc-pipeline.md)
   - Barre de progression WebSocket (cf. 16-progress-ux-pattern.md)
6. /recording-session/{id}/validate → validation
   - Score qualité, lecture des clips, suppression individuelle
   - Bouton "Télécharger le dataset (ZIP)"
7. /rvc → "Comment entraîner mon modèle" → tutoriel Kaggle intégré
```

## Spécification page wizard

### Fichier `Site/frontend/recording-session.html`

```html
<!DOCTYPE html>
<html lang="fr" data-theme="light">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>VoiceBridge — Préparer un enregistrement RVC</title>
  <link href="https://fonts.googleapis.com/css2?family=Syne:wght@400;500;600;700;800&family=DM+Mono:wght@300;400;500&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="/css/base.css">
  <link rel="stylesheet" href="/css/app.css">
  <link rel="stylesheet" href="/css/recording-session.css">
</head>
<body>
<header>
  <!-- header standard, identique à /studio -->
</header>

<main>
  <div class="breadcrumb">
    <a href="/rvc">Modèles RVC</a>
    <span>›</span>
    <span class="current">Préparer un enregistrement</span>
  </div>

  <h1 class="page-title">Préparer un enregistrement RVC</h1>
  <p class="page-sub">Wizard guidé : ~20 minutes d'enregistrement → dataset prêt pour Kaggle</p>

  <!-- Indicateur de progression global -->
  <div class="wizard-progress">
    <div class="wizard-step active" data-step="prep">Préparation</div>
    <div class="wizard-step" data-step="block-1">Bloc 1</div>
    <div class="wizard-step" data-step="block-2">Bloc 2</div>
    <div class="wizard-step" data-step="block-3">Bloc 3</div>
    <div class="wizard-step" data-step="block-4">Bloc 4</div>
    <div class="wizard-step" data-step="block-5">Bloc 5</div>
    <div class="wizard-step" data-step="finalize">Finaliser</div>
  </div>

  <!-- Étape 1 : Préparation -->
  <section class="step active" data-step="prep">
    <div class="step-header">
      <div class="step-number">1</div>
      <div class="step-title">Préparation</div>
    </div>
    
    <div class="card">
      <h3>📋 Avant de commencer</h3>
      <ul>
        <li><strong>Matériel</strong> : casque-micro USB ou micro USB cardioïde recommandé. Évite les AirPods Bluetooth (compression dégrade la qualité).</li>
        <li><strong>Environnement</strong> : pièce calme, ferme la fenêtre, coupe la clim, le ventilateur, le frigo si possible.</li>
        <li><strong>Distance micro</strong> : 15-25 cm de la bouche, légèrement décalé pour éviter les "p" et "b" qui claquent.</li>
        <li><strong>Avant d'enregistrer</strong> : bois un peu d'eau, fais 2 minutes d'échauffement vocal en lisant à voix haute.</li>
        <li><strong>Posture</strong> : assis confortablement, dos droit, pas en marchant.</li>
        <li><strong>Voix</strong> : parle naturellement, comme à un collègue. Pas de "voix de présentateur radio".</li>
      </ul>
      
      <h3>📊 Tu vas enregistrer 5 blocs (~20 min total)</h3>
      <ol>
        <li><strong>Texte phonétique (5 min)</strong> — pour couvrir tous les phonèmes</li>
        <li><strong>Conversationnel (5 min)</strong> — registre professionnel naturel</li>
        <li><strong>Intonations variées (3 min)</strong> — questions, exclamations, énumérations</li>
        <li><strong>Nombres et jargon (2 min)</strong> — chiffres, dates, mots techniques</li>
        <li><strong>Lecture libre (5 min)</strong> — article, livre, ce que tu veux</li>
      </ol>
      
      <h3>📥 Guide complet à imprimer</h3>
      <p>Tu peux <a href="/api/rvc/guide.pdf" download class="btn-link">télécharger le guide PDF</a> qui regroupe tous les conseils + les 5 blocs textes.</p>
      
      <div class="field">
        <label for="sessionName">Nom de cette session</label>
        <input type="text" id="sessionName" placeholder="ex : JC voice v1" maxlength="50">
      </div>
      
      <div class="field">
        <label>Test du micro</label>
        <div class="mic-test-zone">
          <button class="btn btn-secondary" id="btnMicTest">🎤 Tester mon micro (3s)</button>
          <div class="vu-meter-mini" id="vuMeterTest">
            <div class="vu-bar"></div>
          </div>
          <div class="hint" id="micTestHint">Cliquez pour vérifier que le micro est OK</div>
        </div>
      </div>
      
      <button class="btn btn-primary" id="btnStartRecording" disabled>▶ Démarrer le bloc 1</button>
    </div>
  </section>
  
  <!-- Étapes 2-6 : Blocs (template dynamique JS) -->
  <section class="step locked" data-step="block-1" data-block="1">
    <!-- Rendu par recording-session.js à partir du template -->
  </section>
  <section class="step locked" data-step="block-2" data-block="2"></section>
  <section class="step locked" data-step="block-3" data-block="3"></section>
  <section class="step locked" data-step="block-4" data-block="4"></section>
  <section class="step locked" data-step="block-5" data-block="5"></section>
  
  <!-- Étape 7 : Finalisation -->
  <section class="step locked" data-step="finalize">
    <div class="step-header">
      <div class="step-number">7</div>
      <div class="step-title">Finaliser le dataset</div>
    </div>
    
    <div class="card">
      <h3>Récapitulatif</h3>
      <table class="recap-table">
        <thead><tr><th>Bloc</th><th>Durée</th><th>Statut</th></tr></thead>
        <tbody id="recapBody"></tbody>
        <tfoot><tr><td>Total</td><td id="recapTotal">--:--</td><td>--</td></tr></tfoot>
      </table>
      
      <div class="alert alert-info">
        Le retraitement audio prend ~5 minutes (débruitage, segmentation, normalisation). Tu peux fermer cette page, le traitement continue côté serveur.
      </div>
      
      <button class="btn btn-primary" id="btnProcessDataset">⚙ Traiter le dataset</button>
      
      <!-- Barre de progression (cachée au départ, visible quand processing) -->
      <div class="progress-container" id="processProgress" style="display:none">
        <div class="progress-header">
          <span class="progress-step" id="progressStep">Initialisation...</span>
          <span class="progress-percent" id="progressPercent">0%</span>
        </div>
        <div class="progress-bar"><div class="progress-fill" id="progressFill"></div></div>
        <div class="progress-meta">
          <span id="progressElapsed">0s écoulé</span>
          <span id="progressEta">~5 min restant</span>
        </div>
        <div class="progress-logs" id="progressLogs"></div>
      </div>
    </div>
  </section>
</main>

<script src="/js/theme.js"></script>
<script src="/js/api.js"></script>
<script src="/js/notify.js"></script>
<script src="/js/header.js"></script>
<script src="/js/live-worklet.js"></script>
<script src="/js/recording-session.js"></script>
</body>
</html>
```

### Template d'un bloc (rendu dynamique en JS)

```html
<!-- Injecté par recording-session.js dans <section data-step="block-N"> -->
<div class="step-header">
  <div class="step-number">${blockIndex + 1}</div>
  <div class="step-title">Bloc ${blockIndex} : ${blockTitle}</div>
  <div class="block-target-duration">~${targetDurationMin} min</div>
</div>

<div class="card">
  <div class="block-instructions">
    <p><strong>💡 Conseil :</strong> ${blockTip}</p>
  </div>
  
  <div class="block-text-display" id="blockText-${blockIndex}">
    <!-- Le texte du bloc est rendu ici (HTML formatté) -->
    ${blockTextHtml}
  </div>
  
  <div class="recording-controls">
    <div class="vu-meter">
      <div class="vu-bar" id="vuBar-${blockIndex}"></div>
      <div class="vu-label" id="vuLabel-${blockIndex}">-∞ dB</div>
    </div>
    
    <div class="duration-display">
      <span class="duration-current" id="durationCurrent-${blockIndex}">00:00</span>
      <span class="duration-target"> / 0${targetDurationMin}:00</span>
      <div class="duration-bar"><div class="duration-fill" id="durationFill-${blockIndex}"></div></div>
    </div>
    
    <div class="recording-buttons">
      <button class="btn btn-primary" data-action="start">⏺ Enregistrer</button>
      <button class="btn btn-secondary" data-action="pause" style="display:none">⏸ Pause</button>
      <button class="btn btn-secondary" data-action="resume" style="display:none">▶ Reprendre</button>
      <button class="btn btn-secondary" data-action="restart" style="display:none">🔄 Recommencer</button>
    </div>
    
    <div class="recording-warnings">
      <div class="warning hidden" data-warn="too-quiet">⚠️ Niveau audio trop faible — rapproche le micro</div>
      <div class="warning hidden" data-warn="too-loud">⚠️ Saturation détectée — éloigne le micro ou parle moins fort</div>
      <div class="warning hidden" data-warn="long-silence">⚠️ Silence > 5s — reprends ta lecture</div>
    </div>
  </div>
  
  <button class="btn btn-primary" data-action="next" disabled>
    Bloc suivant ➤
  </button>
</div>
```

## Les 5 blocs textes (canoniques)

Ces textes sont stockés dans une constante JS partagée entre frontend et la génération du PDF. Source unique : `Site/frontend/js/recording-session-content.js`.

### Fichier `Site/frontend/js/recording-session-content.js`

```javascript
/**
 * Contenu canonique des 5 blocs d'enregistrement RVC.
 * Source unique utilisée par le wizard frontend ET la génération du PDF.
 */
window.RECORDING_BLOCKS = [
  {
    index: 1,
    title: "Texte phonétiquement riche",
    targetDurationSec: 300,  // 5 min
    minDurationSec: 240,     // 4 min minimum
    tip: "Lis posément. Ce texte est calibré pour couvrir tous les phonèmes du français. Articule clairement.",
    parts: [
      {
        title: "Partie 1 — La bise et le soleil (étalon ISO phonétique)",
        text: `La bise et le soleil se disputaient, chacun assurant qu'il était le plus fort, quand ils ont vu un voyageur qui s'avançait, enveloppé dans son manteau. Ils sont tombés d'accord que celui qui arriverait le premier à faire ôter son manteau au voyageur serait regardé comme le plus fort.

Alors, la bise s'est mise à souffler de toute sa force, mais plus elle soufflait, plus le voyageur serrait son manteau autour de lui. À la fin, la bise a renoncé à le lui faire ôter. Alors le soleil a commencé à briller, et au bout d'un moment, le voyageur, réchauffé, a ôté son manteau. Ainsi, la bise a dû reconnaître que le soleil était le plus fort des deux.`
      },
      {
        title: "Partie 2 — Phonèmes étendus",
        text: `Voici quelques mots difficiles à prononcer : excellence, anticonstitutionnellement, prestidigitateur, désoxyribonucléique, hémorragie, paroxysme, stéréotypé, juxtaposition, métamorphose, philharmonique.

La voiture rouge roule rapidement sur la route. Pierre prend trois pommes pourpres au panier. Le chasseur sachant chasser sans son chien est un excellent chasseur. Six saucissons secs et six saucisses sèches.

Quand un cordier cordant veut accorder sa corde, pour sa corde accorder, six cordons il accorde. Si l'un des cordons de la corde décorde, le cordon décordant fait décorder la corde.`
      },
      {
        title: "Partie 3 — Voyelles nasales et liaisons",
        text: `Un bon vin blanc dans un grand bain plein. Cinq cents enfants chantent ensemble. Les amandes amères et les oranges orangées. Mon ami est venu hier de Lyon en train avec son frère et sa sœur.

Il faisait un temps merveilleux ce matin-là dans le petit village paisible où mes grands-parents habitaient depuis toujours. Les oiseaux chantaient dans les arbres centenaires et le boulanger préparait déjà ses fameux croissants au beurre.`
      },
      {
        title: "Partie 4 — Lettres et apostrophes",
        text: `L'avocat de l'accusé a interjeté appel. L'enfant s'est étonné. L'éléphant emporte l'arbre. Aujourd'hui, j'ai aperçu un aigle au-dessus de l'horizon. Quelqu'un m'a appelé, j'ai répondu, mais personne n'était là.

Si tu finis avant la fin du temps imparti, lis simplement à nouveau l'une des parties précédentes pour atteindre les 5 minutes.`
      }
    ]
  },
  {
    index: 2,
    title: "Conversationnel naturel",
    targetDurationSec: 300,
    minDurationSec: 240,
    tip: "Parle comme en réunion. Ce sont des phrases que tu pourrais réellement dire au boulot. Reste naturel.",
    parts: [
      {
        title: "Partie 1 — Présentation professionnelle",
        text: `Bonjour à tous, j'espère que vous allez bien. Aujourd'hui, je voudrais aborder avec vous un sujet important pour notre organisation : la sécurité de nos systèmes d'information. Comme vous le savez, les menaces évoluent rapidement, et il est essentiel que nous restions vigilants ensemble.

Je vais vous présenter trois axes principaux que nous devons traiter cette année. Premièrement, la sensibilisation des équipes — c'est la première ligne de défense. Deuxièmement, le renforcement de notre infrastructure technique. Et troisièmement, la mise en conformité avec les nouvelles réglementations européennes.

J'ai préparé une analyse détaillée que je vais maintenant partager avec vous. N'hésitez pas à me poser des questions à tout moment, j'y répondrai volontiers.`
      },
      {
        title: "Partie 2 — Échange conversationnel",
        text: `Tu as raison, c'est exactement ce que je pensais aussi. Mais je crois qu'il faut qu'on prenne un peu de recul pour bien comprendre ce qui se joue. Si on regarde les chiffres de l'année dernière, on voit clairement une tendance qui se confirme.

Bon, écoute, je propose qu'on en reparle demain matin si tu veux, ça te va ? Comme ça on aura le temps d'y réfléchir tranquillement chacun de notre côté. J'aimerais qu'on prépare quelque chose de solide à présenter au comité exécutif la semaine prochaine.

D'accord, parfait. Alors je note qu'on se voit demain à neuf heures dans la salle de réunion du troisième étage. Si tu peux préparer un petit document de synthèse, ce serait formidable. Bon, je te laisse, je dois filer à mon prochain rendez-vous. À demain !`
      },
      {
        title: "Partie 3 — Quotidien professionnel",
        text: `Ce matin, j'ai eu une réunion très intéressante avec les équipes de la production. On a discuté pendant deux heures des enjeux de cybersécurité dans les usines. C'est un domaine qui me passionne particulièrement parce qu'il combine technique et humain.

L'après-midi, j'ai pris le temps de répondre à mes mails et d'avancer sur le dossier que je dois présenter vendredi. C'est toujours un peu sportif les fins de semaine, mais c'est aussi ce qui rend le métier intéressant.

Ce week-end, j'aimerais bien qu'on aille faire un tour en famille. On pourrait peut-être aller voir nos amis à la campagne. Les enfants seraient ravis et ça nous ferait du bien à tous de prendre l'air.`
      }
    ]
  },
  {
    index: 3,
    title: "Variété d'intonations",
    targetDurationSec: 180,
    minDurationSec: 150,
    tip: "Pour chaque type d'intonation, sois expressif. Joue le jeu, comme si tu étais vraiment dans la situation.",
    parts: [
      {
        title: "Partie 1 — Questions (intonation montante)",
        text: `Avez-vous vraiment réfléchi à cette proposition ? Pensez-vous que c'est la bonne approche ? Comment êtes-vous arrivé à cette conclusion ? Pourriez-vous m'expliquer votre raisonnement ?

Est-ce qu'on s'est bien compris ? Tu es sûr que c'est ce qu'il faut faire ? On peut vraiment se permettre de prendre ce risque maintenant ? Mais qu'est-ce que vous voulez que je fasse ?`
      },
      {
        title: "Partie 2 — Exclamations (intonation expressive)",
        text: `Quelle excellente idée ! C'est absolument fantastique ! Je suis vraiment bluffé par votre travail ! Bravo, c'est du grand art ! Magnifique !

Mais c'est incroyable ce que vous avez réussi à faire ! Quelle journée ! Quelle aventure ! C'est exactement ce qu'on cherchait depuis des semaines !`
      },
      {
        title: "Partie 3 — Affirmations calmes",
        text: `Je suis tout à fait d'accord avec vous. C'est exactement ce que je pense. Cela me semble la voie à suivre. Nous devons prendre cette décision ensemble. Le rapport sera prêt demain matin.

J'ai bien réfléchi à la question, et je crois sincèrement que c'est la meilleure option dont nous disposons. Cela demandera un effort collectif, mais nous y arriverons.`
      },
      {
        title: "Partie 4 — Négations fermes",
        text: `Non, je ne peux pas valider cela. Ce n'est absolument pas envisageable. Je suis désolé mais ma réponse est non. Cette proposition ne respecte pas nos engagements.

Il n'est pas question de céder sur ce point. Nous ne pouvons pas accepter de telles conditions. C'est une ligne rouge à ne pas franchir.`
      },
      {
        title: "Partie 5 — Énumérations",
        text: `Premièrement, deuxièmement, troisièmement, et enfin quatrièmement. Lundi, mardi, mercredi, jeudi, vendredi, samedi, dimanche. Janvier, février, mars, avril, mai, juin, juillet, août, septembre, octobre, novembre, décembre.

Trois choses à retenir : la sécurité, l'efficacité, et la satisfaction client. Quatre piliers de notre stratégie : innovation, qualité, agilité, durabilité.`
      },
      {
        title: "Partie 6 — Hésitations naturelles",
        text: `Eh bien... je crois que... oui, peut-être. C'est une question difficile, je ne suis pas sûr de... attendez, laissez-moi réfléchir un instant. Hmm... c'est compliqué.

Bon, écoutez, je ne sais pas trop quoi vous dire. Il faudrait que j'y réfléchisse encore un peu, mais a priori, je pense qu'on peut envisager... enfin, on verra.`
      },
      {
        title: "Partie 7 — Insistance",
        text: `C'est vraiment, vraiment important pour moi. Je ne peux pas insister assez sur ce point. C'est crucial. Crucial. Vous m'entendez ? C'est absolument essentiel.

Cette décision va impacter tout notre projet pour les six prochains mois. Tout le monde doit en avoir conscience. C'est non négociable.`
      }
    ]
  },
  {
    index: 4,
    title: "Nombres, dates, jargon technique",
    targetDurationSec: 120,
    minDurationSec: 90,
    tip: "Articule bien les chiffres. Pour les acronymes, dis-les comme tu les dis vraiment au quotidien.",
    parts: [
      {
        title: "Partie 1 — Nombres et statistiques",
        text: `Le 15 mars 2026, nous avons enregistré 1247 incidents de sécurité, soit une augmentation de 23,5% par rapport à l'année précédente. Notre budget alloué de 2,8 millions d'euros sera réparti sur 3 axes principaux. Le rapport sera disponible avant le 31 décembre.

Il y a 8 537 utilisateurs actifs sur notre plateforme. La latence moyenne est de 142 millisecondes. Nous traitons environ 50 000 requêtes par jour. Le temps de réponse cible est de 200 millisecondes au 99e percentile.`
      },
      {
        title: "Partie 2 — Dates et horaires",
        text: `Lundi 26 avril 2026, à 14h30, dans la salle de réunion numéro trois. Du 1er janvier au 31 décembre 2025, nous avons déployé 47 nouvelles fonctionnalités. La conférence aura lieu le mardi 7 mai à 9h15 précises.

Né le 12 octobre 1980, embauché le 3 septembre 2007, promu directeur le 1er juillet 2018. La prochaine échéance est fixée au 30 juin 2026.`
      },
      {
        title: "Partie 3 — Jargon cybersécurité",
        text: `Quelques termes techniques : firewall, endpoint detection, cybersécurité, authentification multi-facteurs, EDR, SOC, SIEM, ransomware, phishing, zero trust, DevSecOps, SAST, DAST, IAM, PAM.

Notre EDR détecte 99,7% des menaces. Le SIEM Splunk corrèle les logs de tout le SOC. Nous sommes en conformité NIS2 et alignés sur ISO 27001. Le PSSI prévoit un niveau "Confidentiel Sécurisé" pour le patrimoine génétique.`
      },
      {
        title: "Partie 4 — Acronymes et codes",
        text: `CODIR, COMEX, RSE, DRH, DAF, DSI, RSSI, DPO, CISO, CTO, CEO, CFO. Référence projet : PRJ-2026-0142. Ticket support : INC-12345.

Standards ISO 9001, ISO 14001, ISO 50001, ISO 27001, SOC 2 Type II, RGPD, HIPAA, PCI-DSS.`
      }
    ]
  },
  {
    index: 5,
    title: "Lecture libre",
    targetDurationSec: 300,
    minDurationSec: 240,
    tip: "Choisis un texte que tu aimes : un article, un extrait de livre, un essai. L'idée, c'est que tu lises naturellement, comme à voix haute pour quelqu'un.",
    parts: [
      {
        title: "Instructions",
        text: `Pour ce dernier bloc, deux options :

OPTION A — Texte fourni (recommandé si tu n'as rien sous la main)
Lis le texte fourni ci-dessous (extrait du Petit Prince).

OPTION B — Ton propre texte
Lis n'importe quel texte de ton choix : un livre, un article récent, un essai, une page Wikipédia. Privilégie quelque chose qui t'intéresse vraiment.

Dans tous les cas, lis pendant environ 5 minutes en parlant naturellement.`
      },
      {
        title: "Texte fourni — Le Petit Prince, chapitre XXI (extrait)",
        text: `C'est alors qu'apparut le renard. "Bonjour", dit le renard. "Bonjour", répondit poliment le petit prince, qui se retourna mais ne vit rien. "Je suis là, dit la voix, sous le pommier."

"Qui es-tu ?" dit le petit prince. "Tu es bien joli." "Je suis un renard", dit le renard. "Viens jouer avec moi, lui proposa le petit prince. Je suis tellement triste." "Je ne puis pas jouer avec toi, dit le renard. Je ne suis pas apprivoisé."

"Ah ! pardon", fit le petit prince. Mais, après réflexion, il ajouta : "Qu'est-ce que signifie apprivoiser ?" "Tu n'es pas d'ici, dit le renard. Que cherches-tu ?" "Je cherche les hommes, dit le petit prince. Qu'est-ce que signifie apprivoiser ?"

"Les hommes, dit le renard, ils ont des fusils et ils chassent. C'est bien gênant ! Ils élèvent aussi des poules. C'est leur seul intérêt. Tu cherches des poules ?" "Non, dit le petit prince. Je cherche des amis. Qu'est-ce que signifie apprivoiser ?"

"C'est une chose trop oubliée, dit le renard. Ça signifie créer des liens." "Créer des liens ?" "Bien sûr, dit le renard. Tu n'es encore pour moi qu'un petit garçon tout semblable à cent mille petits garçons. Et je n'ai pas besoin de toi. Et tu n'as pas besoin de moi non plus. Je ne suis pour toi qu'un renard semblable à cent mille renards. Mais, si tu m'apprivoises, nous aurons besoin l'un de l'autre. Tu seras pour moi unique au monde. Je serai pour toi unique au monde."

"Je commence à comprendre", dit le petit prince. "Il y a une fleur... je crois qu'elle m'a apprivoisé..." "C'est possible", dit le renard. "On voit sur la Terre toutes sortes de choses..."

"Oh ! ce n'est pas sur la Terre", dit le petit prince. Le renard parut très intrigué : "Sur une autre planète ?" "Oui." "Il y a des chasseurs sur cette planète-là ?" "Non." "Ça, c'est intéressant ! Et des poules ?" "Non." "Rien n'est parfait", soupira le renard.`
      }
    ]
  }
];
```

## Spécification du JS frontend

### Fichier `Site/frontend/js/recording-session.js`

```javascript
/**
 * Wizard d'enregistrement RVC.
 * 
 * Architecture :
 * - Une session = ID unique stocké côté backend dans data/recording_sessions/{id}/
 * - Capture audio via AudioWorklet (PCM 16kHz mono int16)
 * - Upload des chunks en streaming vers le backend (POST par batch de 5s)
 * - Persistance backend des 5 blocs en .wav bruts
 * - Phase de retraitement asynchrone à la fin
 */

(function() {
  'use strict';
  
  // État global de la session
  const state = {
    sessionId: null,
    sessionName: '',
    currentBlockIndex: 0,  // 0 = prep, 1-5 = blocs, 6 = finalize
    blocks: [
      { index: 1, durationSec: 0, status: 'pending' },  // pending, recording, paused, done
      { index: 2, durationSec: 0, status: 'pending' },
      { index: 3, durationSec: 0, status: 'pending' },
      { index: 4, durationSec: 0, status: 'pending' },
      { index: 5, durationSec: 0, status: 'pending' }
    ],
    capture: null,  // RecordingCapture instance
  };
  
  // ===========================================================================
  // RecordingCapture : encapsule AudioWorklet + upload backend
  // ===========================================================================
  
  class RecordingCapture {
    constructor(sessionId, blockIndex, callbacks) {
      this.sessionId = sessionId;
      this.blockIndex = blockIndex;
      this.callbacks = callbacks; // { onLevelUpdate, onDurationUpdate, onWarning, onError }
      this.chunks = [];
      this.totalSamples = 0;
      this.isRecording = false;
      this.isPaused = false;
      this.lastUploadTime = 0;
      this.silenceCounter = 0;  // pour détecter long silence
    }
    
    async start() {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          sampleRate: 16000,
          echoCancellation: false,  // garde le signal brut
          noiseSuppression: false,
          autoGainControl: false
        }
      });
      
      this.audioContext = new AudioContext({sampleRate: 16000});
      await this.audioContext.audioWorklet.addModule('/js/live-worklet.js');
      const source = this.audioContext.createMediaStreamSource(stream);
      this.worklet = new AudioWorkletNode(this.audioContext, 'pcm-worklet');
      this.worklet.port.onmessage = (e) => this._onPCMChunk(e.data);
      source.connect(this.worklet);
      
      this.isRecording = true;
      this.startTimestamp = performance.now();
    }
    
    pause() {
      this.isPaused = true;
    }
    
    resume() {
      this.isPaused = false;
    }
    
    async stop() {
      this.isRecording = false;
      if (this.worklet) {
        this.worklet.disconnect();
      }
      if (this.audioContext) {
        await this.audioContext.close();
      }
      // Flush final
      await this._uploadBatch(true);
      // Notifier le backend que le bloc est fini
      await fetch(`/api/recording_session/${this.sessionId}/finish_block`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({block: this.blockIndex})
      });
    }
    
    async restart() {
      // Vide les chunks et réinitialise sur le serveur
      this.chunks = [];
      this.totalSamples = 0;
      await fetch(`/api/recording_session/${this.sessionId}/clear_block`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({block: this.blockIndex})
      });
    }
    
    _onPCMChunk(int16Array) {
      if (!this.isRecording || this.isPaused) return;
      
      this.chunks.push(int16Array);
      this.totalSamples += int16Array.length;
      
      // Calcul du niveau RMS pour le VU-meter
      const rms = this._computeRMS(int16Array);
      const dB = rms > 0 ? 20 * Math.log10(rms / 32768) : -Infinity;
      this.callbacks.onLevelUpdate(dB);
      
      // Détection avertissements
      if (dB < -50) {
        this.silenceCounter++;
        if (this.silenceCounter > 50) {  // ~5s de silence (chunks ~100ms)
          this.callbacks.onWarning('long-silence');
        }
      } else {
        this.silenceCounter = 0;
      }
      if (dB > -1) this.callbacks.onWarning('too-loud');
      if (dB < -40 && rms > 0) this.callbacks.onWarning('too-quiet');
      
      // Mise à jour durée
      const durationSec = this.totalSamples / 16000;
      this.callbacks.onDurationUpdate(durationSec);
      
      // Upload périodique (toutes les 5s)
      const now = performance.now();
      if (now - this.lastUploadTime > 5000) {
        this._uploadBatch(false);
        this.lastUploadTime = now;
      }
    }
    
    _computeRMS(int16Array) {
      let sum = 0;
      for (let i = 0; i < int16Array.length; i++) {
        sum += int16Array[i] * int16Array[i];
      }
      return Math.sqrt(sum / int16Array.length);
    }
    
    async _uploadBatch(isFinal) {
      if (this.chunks.length === 0 && !isFinal) return;
      const batch = this.chunks.splice(0);  // vide la queue
      if (batch.length === 0) return;
      
      // Concaténer
      let totalLength = 0;
      for (const c of batch) totalLength += c.length;
      const combined = new Int16Array(totalLength);
      let offset = 0;
      for (const c of batch) {
        combined.set(c, offset);
        offset += c.length;
      }
      
      try {
        await fetch(
          `/api/recording_session/${this.sessionId}/append_chunk?block=${this.blockIndex}`,
          {
            method: 'POST',
            body: combined.buffer,
            headers: {'Content-Type': 'application/octet-stream'}
          }
        );
      } catch (e) {
        console.error('Upload chunk failed:', e);
        this.callbacks.onError(e);
      }
    }
  }
  
  // ===========================================================================
  // UI rendering
  // ===========================================================================
  
  function renderBlock(index) {
    const block = window.RECORDING_BLOCKS[index - 1];
    const section = document.querySelector(`[data-step="block-${index}"]`);
    
    let textHtml = '';
    for (const part of block.parts) {
      textHtml += `<h4>${part.title}</h4>`;
      textHtml += `<p>${part.text.replace(/\n\n/g, '</p><p>').replace(/\n/g, '<br>')}</p>`;
    }
    
    const targetMin = Math.floor(block.targetDurationSec / 60);
    
    section.innerHTML = `
      <div class="step-header">
        <div class="step-number">${index + 1}</div>
        <div class="step-title">Bloc ${index} : ${block.title}</div>
        <div class="block-target-duration">~${targetMin} min</div>
      </div>
      <div class="card">
        <div class="block-instructions">
          <p><strong>💡 Conseil :</strong> ${block.tip}</p>
        </div>
        <div class="block-text-display">${textHtml}</div>
        <div class="recording-controls">
          <div class="vu-meter">
            <div class="vu-bar" id="vuBar-${index}"></div>
            <div class="vu-label" id="vuLabel-${index}">-∞ dB</div>
          </div>
          <div class="duration-display">
            <span class="duration-current" id="durationCurrent-${index}">00:00</span>
            <span class="duration-target"> / 0${targetMin}:00</span>
            <div class="duration-bar"><div class="duration-fill" id="durationFill-${index}"></div></div>
          </div>
          <div class="recording-buttons">
            <button class="btn btn-primary" data-action="start">⏺ Enregistrer</button>
            <button class="btn btn-secondary" data-action="pause" style="display:none">⏸ Pause</button>
            <button class="btn btn-secondary" data-action="resume" style="display:none">▶ Reprendre</button>
            <button class="btn btn-secondary" data-action="restart" style="display:none">🔄 Recommencer</button>
          </div>
          <div class="recording-warnings">
            <div class="warning hidden" data-warn="too-quiet">⚠️ Niveau audio trop faible</div>
            <div class="warning hidden" data-warn="too-loud">⚠️ Saturation détectée</div>
            <div class="warning hidden" data-warn="long-silence">⚠️ Silence > 5s</div>
          </div>
        </div>
        <button class="btn btn-primary" data-action="next" disabled>Bloc suivant ➤</button>
      </div>
    `;
    
    // Bind events
    bindBlockEvents(section, index);
  }
  
  function bindBlockEvents(section, index) {
    section.querySelector('[data-action="start"]').addEventListener('click', () => startBlock(index));
    section.querySelector('[data-action="pause"]').addEventListener('click', () => pauseBlock(index));
    section.querySelector('[data-action="resume"]').addEventListener('click', () => resumeBlock(index));
    section.querySelector('[data-action="restart"]').addEventListener('click', () => restartBlock(index));
    section.querySelector('[data-action="next"]').addEventListener('click', () => nextBlock(index));
  }
  
  // ===========================================================================
  // Block lifecycle
  // ===========================================================================
  
  async function startBlock(index) {
    const block = window.RECORDING_BLOCKS[index - 1];
    state.capture = new RecordingCapture(state.sessionId, index, {
      onLevelUpdate: (dB) => updateVUMeter(index, dB),
      onDurationUpdate: (sec) => updateDuration(index, sec, block),
      onWarning: (type) => showWarning(index, type),
      onError: (e) => showError(e)
    });
    
    await state.capture.start();
    
    document.querySelector(`[data-step="block-${index}"] [data-action="start"]`).style.display = 'none';
    document.querySelector(`[data-step="block-${index}"] [data-action="pause"]`).style.display = '';
    document.querySelector(`[data-step="block-${index}"] [data-action="restart"]`).style.display = '';
    
    state.blocks[index - 1].status = 'recording';
  }
  
  async function pauseBlock(index) {
    state.capture.pause();
    state.blocks[index - 1].status = 'paused';
    document.querySelector(`[data-step="block-${index}"] [data-action="pause"]`).style.display = 'none';
    document.querySelector(`[data-step="block-${index}"] [data-action="resume"]`).style.display = '';
  }
  
  async function resumeBlock(index) {
    state.capture.resume();
    state.blocks[index - 1].status = 'recording';
    document.querySelector(`[data-step="block-${index}"] [data-action="pause"]`).style.display = '';
    document.querySelector(`[data-step="block-${index}"] [data-action="resume"]`).style.display = 'none';
  }
  
  async function restartBlock(index) {
    if (!confirm('Tu vas perdre l\'enregistrement de ce bloc. Continuer ?')) return;
    if (state.capture) {
      await state.capture.stop();
      await state.capture.restart();
    }
    state.blocks[index - 1].durationSec = 0;
    state.blocks[index - 1].status = 'pending';
    renderBlock(index);
  }
  
  async function nextBlock(index) {
    if (state.capture) {
      await state.capture.stop();
      state.capture = null;
    }
    state.blocks[index - 1].status = 'done';
    
    if (index < 5) {
      // Démarrer le bloc suivant
      goToStep(`block-${index + 1}`);
      renderBlock(index + 1);
    } else {
      // Tous les blocs terminés
      goToStep('finalize');
      renderFinalize();
    }
  }
  
  // ===========================================================================
  // UI updates
  // ===========================================================================
  
  function updateVUMeter(index, dB) {
    const bar = document.getElementById(`vuBar-${index}`);
    const label = document.getElementById(`vuLabel-${index}`);
    if (!bar) return;
    // Map dB [-60, 0] to width [0, 100]%
    const width = Math.max(0, Math.min(100, ((dB + 60) / 60) * 100));
    bar.style.width = `${width}%`;
    if (dB > -3) bar.style.background = '#dc2626';  // red (saturation)
    else if (dB > -12) bar.style.background = '#16a34a';  // green
    else bar.style.background = '#f59e0b';  // orange (faible)
    label.textContent = isFinite(dB) ? `${dB.toFixed(0)} dB` : '-∞ dB';
  }
  
  function updateDuration(index, sec, block) {
    const current = document.getElementById(`durationCurrent-${index}`);
    const fill = document.getElementById(`durationFill-${index}`);
    const min = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    current.textContent = `${min.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
    
    const pct = Math.min(100, (sec / block.targetDurationSec) * 100);
    fill.style.width = `${pct}%`;
    
    state.blocks[index - 1].durationSec = sec;
    
    // Activer "Bloc suivant" si durée minimum atteinte
    if (sec >= block.minDurationSec) {
      document.querySelector(`[data-step="block-${index}"] [data-action="next"]`).disabled = false;
    }
  }
  
  function showWarning(index, type) {
    const warn = document.querySelector(`[data-step="block-${index}"] [data-warn="${type}"]`);
    if (!warn) return;
    warn.classList.remove('hidden');
    setTimeout(() => warn.classList.add('hidden'), 3000);
  }
  
  function showError(e) {
    notify.error('Erreur enregistrement : ' + e.message);
  }
  
  // ===========================================================================
  // Finalize : déclenche le retraitement avec barre de progression
  // ===========================================================================
  
  function renderFinalize() {
    const recap = document.getElementById('recapBody');
    let totalSec = 0;
    let html = '';
    for (const b of state.blocks) {
      const block = window.RECORDING_BLOCKS[b.index - 1];
      const min = Math.floor(b.durationSec / 60);
      const s = Math.floor(b.durationSec % 60);
      const ok = b.status === 'done';
      html += `
        <tr>
          <td>${b.index} : ${block.title}</td>
          <td>${min}:${s.toString().padStart(2, '0')}</td>
          <td>${ok ? '✅ OK' : '❌ Manquant'}</td>
        </tr>
      `;
      totalSec += b.durationSec;
    }
    recap.innerHTML = html;
    const totalMin = Math.floor(totalSec / 60);
    const totalSecRem = Math.floor(totalSec % 60);
    document.getElementById('recapTotal').textContent =
      `${totalMin}:${totalSecRem.toString().padStart(2, '0')}`;
    
    document.getElementById('btnProcessDataset').addEventListener('click', processDataset);
  }
  
  async function processDataset() {
    const btn = document.getElementById('btnProcessDataset');
    btn.disabled = true;
    btn.textContent = 'Traitement en cours...';
    
    document.getElementById('processProgress').style.display = '';
    
    const res = await fetch(`/api/recording_session/${state.sessionId}/process`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        denoise_strength: 0.7,
        min_clip_seconds: 5,
        max_clip_seconds: 15
      })
    });
    const {task_id} = await res.json();
    
    // Connexion WebSocket pour la progression
    subscribeProgress(task_id);
  }
  
  function subscribeProgress(taskId) {
    const ws = new WebSocket(`wss://${location.host}/ws/progress/${taskId}`);
    
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      
      document.getElementById('progressPercent').textContent = `${data.progress_percent}%`;
      document.getElementById('progressStep').textContent = data.current_step;
      document.getElementById('progressFill').style.width = `${data.progress_percent}%`;
      document.getElementById('progressElapsed').textContent =
        `${data.elapsed_seconds}s écoulé`;
      document.getElementById('progressEta').textContent =
        data.estimated_remaining_seconds > 0
          ? `~${Math.ceil(data.estimated_remaining_seconds / 60)} min restant`
          : '';
      
      if (data.status === 'done') {
        ws.close();
        location.href = `/recording-session/${state.sessionId}/validate`;
      } else if (data.status === 'error') {
        ws.close();
        notify.error(`Erreur : ${data.error}`);
      }
    };
  }
  
  function goToStep(stepName) {
    document.querySelectorAll('.step').forEach(s => {
      s.classList.remove('active');
      s.classList.add('locked');
    });
    document.querySelectorAll('.wizard-step').forEach(s => {
      s.classList.remove('active');
    });
    
    const target = document.querySelector(`[data-step="${stepName}"]`);
    target.classList.remove('locked');
    target.classList.add('active');
    target.scrollIntoView({behavior: 'smooth'});
    
    document.querySelector(`.wizard-step[data-step="${stepName}"]`).classList.add('active');
  }
  
  // ===========================================================================
  // Init
  // ===========================================================================
  
  async function init() {
    // Test micro
    document.getElementById('btnMicTest').addEventListener('click', testMicrophone);
    
    // Lancement
    document.getElementById('btnStartRecording').addEventListener('click', async () => {
      const name = document.getElementById('sessionName').value.trim();
      if (!name) {
        notify.error('Saisis un nom pour cette session');
        return;
      }
      
      const res = await fetch('/api/recording_session/create', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name, language: 'fr'})
      });
      const data = await res.json();
      state.sessionId = data.session_id;
      state.sessionName = name;
      
      goToStep('block-1');
      renderBlock(1);
    });
  }
  
  async function testMicrophone() {
    // ...
    // Test 3s qui mesure le niveau et active le bouton "Démarrer" si OK
  }
  
  document.addEventListener('DOMContentLoaded', init);
})();
```

## Spécification du PDF téléchargeable

Le PDF est généré via reportlab côté backend, route `GET /api/rvc/guide.pdf`.

### Structure du PDF (12 pages)

| Page | Contenu |
|---|---|
| 1 | Page de titre + intro |
| 2 | Pré-requis matériel + environnement |
| 3 | Workflow général en 6 phases |
| 4-5 | Bloc 1 : Texte phonétique |
| 6-7 | Bloc 2 : Conversationnel |
| 8 | Bloc 3 : Intonations |
| 9 | Bloc 4 : Nombres et jargon |
| 10 | Bloc 5 : Lecture libre |
| 11 | Tutoriel Kaggle (étapes 1 à 5) |
| 12 | FAQ + contacts |

### Code de génération du PDF

```python
# Site/backend/app/services/rvc_guide_pdf.py
"""Génération du PDF "Guide RVC VoiceBridge".

Utilise reportlab. Le contenu des blocs est lu depuis un fichier de
référence partagé avec le frontend (voir docs/rvc_blocks_content.json).
"""
from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.colors import HexColor
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle,
)


BLOCKS_FILE = Path(__file__).parent.parent.parent.parent / "frontend" / "js" / "recording-session-content.js"


def _load_blocks_from_js() -> list[dict]:
    """Parse le fichier JS pour extraire RECORDING_BLOCKS.
    
    Approche : extrait le JSON entre window.RECORDING_BLOCKS = et le ;
    final, puis json.loads. Évite de dupliquer le contenu.
    """
    text = BLOCKS_FILE.read_text(encoding='utf-8')
    start = text.find('window.RECORDING_BLOCKS = ') + len('window.RECORDING_BLOCKS = ')
    end = text.rfind('];') + 1
    json_str = text[start:end]
    # JSON5 → JSON normal n'est pas trivial, donc on parse comme JS
    # Solution simple : require que le fichier .js soit en JSON-compatible strict
    return json.loads(json_str)


def generate_guide_pdf(output_path: Path | None = None) -> bytes:
    """Génère le guide PDF. Retourne les bytes ou écrit dans output_path si fourni."""
    blocks = _load_blocks_from_js()
    
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2*cm, bottomMargin=2*cm,
        title="Guide RVC VoiceBridge",
        author="VoiceBridge",
    )
    
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle('H1', parent=styles['Heading1'],
                         fontSize=20, textColor=HexColor('#A8243C'),
                         spaceAfter=12)
    h2 = ParagraphStyle('H2', parent=styles['Heading2'],
                         fontSize=14, textColor=HexColor('#A8243C'),
                         spaceAfter=8)
    h3 = ParagraphStyle('H3', parent=styles['Heading3'],
                         fontSize=12, textColor=HexColor('#333'),
                         spaceAfter=6)
    body = ParagraphStyle('Body', parent=styles['Normal'],
                           fontSize=10, leading=14, spaceAfter=6)
    tip = ParagraphStyle('Tip', parent=styles['Normal'],
                          fontSize=10, leading=14,
                          backColor=HexColor('#FEF3C7'),
                          borderPadding=8,
                          spaceAfter=10)
    
    story = []
    
    # Page 1 : Titre
    story.append(Paragraph("🎤 Guide RVC VoiceBridge", h1))
    story.append(Spacer(1, 12))
    story.append(Paragraph(
        "Comment enregistrer un dataset de qualité pour entraîner ton modèle RVC.",
        body
    ))
    story.append(Spacer(1, 24))
    story.append(Paragraph("Vue d'ensemble", h2))
    story.append(Paragraph(
        "RVC (Retrieval-based Voice Conversion) permet de transformer une voix "
        "source quelconque en TA voix. Pour l'entraîner, il faut un dataset audio "
        "propre et diversifié de ~18-20 minutes de ta voix.",
        body
    ))
    story.append(Paragraph(
        "Ce guide te accompagne pas-à-pas : préparation du matériel, lecture des "
        "5 blocs textes, traitement automatique du dataset, entraînement gratuit "
        "sur Kaggle, et import du modèle dans VoiceBridge.",
        body
    ))
    story.append(Spacer(1, 24))
    story.append(Paragraph("Workflow (~6h dont 5h d'attente Kaggle)", h2))
    workflow_data = [
        ['Phase', 'Durée', 'Où ?'],
        ['1. Enregistrement guidé', '~25 min', 'VoiceBridge'],
        ['2. Retraitement audio', '~5 min', 'VoiceBridge (auto)'],
        ['3. Validation', '~5 min', 'VoiceBridge'],
        ['4. Entraînement', '3-6h', 'Kaggle (gratuit)'],
        ['5. Import du .pth', '~5 min', 'VoiceBridge'],
        ['6. Utilisation Live', '∞', 'VoiceBridge'],
    ]
    t = Table(workflow_data, colWidths=[8*cm, 4*cm, 5*cm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), HexColor('#A8243C')),
        ('TEXTCOLOR', (0, 0), (-1, 0), HexColor('#FFFFFF')),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 0.5, HexColor('#CCCCCC')),
        ('PADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(t)
    story.append(PageBreak())
    
    # Page 2 : Pré-requis matériel
    story.append(Paragraph("Avant de commencer", h1))
    story.append(Paragraph("Matériel recommandé", h2))
    story.append(Paragraph(
        "<b>Casque-micro USB</b> ou <b>micro USB cardioïde</b>. Le micro "
        "intégré du Mac fonctionne si nécessaire mais la qualité sera moindre.",
        body
    ))
    story.append(Paragraph(
        "<b>Évite</b> les AirPods Bluetooth : la compression HFP en mode micro "
        "dégrade fortement la qualité audio (16kHz mono compressé).",
        body
    ))
    story.append(Spacer(1, 12))
    story.append(Paragraph("Environnement", h2))
    story.append(Paragraph(
        "<b>Pièce calme</b> : ferme la fenêtre, coupe la clim, le ventilateur, "
        "le frigo si possible.",
        body
    ))
    story.append(Paragraph(
        "<b>Distance micro</b> : 15-25 cm de la bouche, légèrement décalé pour "
        "éviter les pops sur les 'p' et 'b'.",
        body
    ))
    story.append(Paragraph(
        "<b>Posture</b> : assis confortablement, dos droit. Pas en marchant "
        "(souffle inégal).",
        body
    ))
    story.append(Spacer(1, 12))
    story.append(Paragraph("Préparation vocale", h2))
    story.append(Paragraph(
        "Bois un peu d'eau avant. Échauffe-toi 2 minutes en lisant à voix haute. "
        "Parle naturellement, comme à un collègue, pas avec une voix artificielle "
        "de présentateur radio.",
        body
    ))
    story.append(PageBreak())
    
    # Pages 4-10 : Les 5 blocs
    for block in blocks:
        story.append(Paragraph(f"Bloc {block['index']} : {block['title']}", h1))
        story.append(Spacer(1, 6))
        target_min = block['targetDurationSec'] // 60
        story.append(Paragraph(
            f"<b>Durée cible</b> : ~{target_min} minutes",
            body
        ))
        story.append(Paragraph(f"<b>💡 Conseil</b> : {block['tip']}", tip))
        story.append(Spacer(1, 6))
        for part in block['parts']:
            story.append(Paragraph(part['title'], h3))
            # Remplacer les sauts de ligne par <br/> pour reportlab
            text_html = part['text'].replace('\n\n', '<br/><br/>').replace('\n', '<br/>')
            story.append(Paragraph(text_html, body))
            story.append(Spacer(1, 6))
        story.append(PageBreak())
    
    # Page 11 : Tutoriel Kaggle
    story.append(Paragraph("Entraîner ton modèle sur Kaggle (gratuit)", h1))
    story.append(Paragraph("Étape 1 : Créer un compte Kaggle", h2))
    story.append(Paragraph(
        "Va sur <b>kaggle.com</b> et crée un compte. Vérifie ton numéro de "
        "téléphone, c'est obligatoire pour activer l'accès GPU gratuit "
        "(30h GPU/semaine).",
        body
    ))
    story.append(Paragraph("Étape 2 : Forker le notebook RVC", h2))
    story.append(Paragraph(
        "Dans VoiceBridge → page <b>Modèles RVC</b> → onglet "
        "<b>Tutoriel Kaggle</b>, clique sur le lien du notebook recommandé "
        "(ex : Applio RVC Trainer). Sur Kaggle, clique sur <b>Copy & Edit</b> "
        "pour le forker dans ton compte.",
        body
    ))
    story.append(Paragraph("Étape 3 : Uploader ton dataset", h2))
    story.append(Paragraph(
        "Sur Kaggle → <b>+ New Dataset</b> → drag & drop ton ZIP exporté "
        "depuis VoiceBridge. Visibilité : Privé. Nom recommandé : "
        "<code>voicebridge-rvc-{ton_nom}-v1</code>.",
        body
    ))
    story.append(Paragraph("Étape 4 : Lancer le training", h2))
    story.append(Paragraph(
        "Dans le notebook forké : Settings → Accelerator : <b>GPU T4 x2</b>. "
        "Add data → ton dataset uploadé. Puis <b>Run All</b>. Le training "
        "prend 3-6h, tu peux fermer ton ordi, ça tourne sur Kaggle.",
        body
    ))
    story.append(Paragraph("Étape 5 : Récupérer le modèle", h2))
    story.append(Paragraph(
        "À la fin du training, dans <code>/kaggle/working/</code> tu trouves : "
        "<br/>• <code>model.pth</code> (~150 Mo) : le modèle RVC "
        "<br/>• <code>added_*.index</code> (~50 Mo) : index FAISS "
        "<br/><br/>Télécharge les deux fichiers.",
        body
    ))
    story.append(PageBreak())
    
    # Page 12 : FAQ
    story.append(Paragraph("FAQ", h1))
    faq = [
        ("Combien de temps faut-il enregistrer ?",
         "5-10 min pour un résultat correct. 15-20 min pour une qualité "
         "professionnelle. Au-delà de 30 min, le gain est marginal."),
        ("Mon modèle ne ressemble pas assez à ma voix.",
         "Cause probable : pas assez de variété dans tes intonations OU "
         "trop de bruit de fond. Réenregistre dans un environnement plus calme."),
        ("Le test rapide est OK mais en live ça sonne bizarre.",
         "Vérifie que le pitch shift est à 0. Si tu utilises une voix native "
         "F5-TTS différente, RVC peut produire un résultat étrange. Essaie "
         "avec une voix native plus proche de ton timbre."),
        ("Combien de modèles RVC puis-je entraîner ?",
         "Illimité. Le stockage RunPod Volume gère ~150 Mo par modèle, "
         "soit ~330 modèles dans 50 Go."),
        ("Puis-je entraîner sur RunPod GPU au lieu de Kaggle ?",
         "Oui mais c'est payant (~5€ par entraînement). Kaggle gratuit "
         "suffit largement pour un usage perso."),
    ]
    for q, a in faq:
        story.append(Paragraph(f"<b>{q}</b>", h3))
        story.append(Paragraph(a, body))
        story.append(Spacer(1, 6))
    
    doc.build(story)
    pdf_bytes = buf.getvalue()
    buf.close()
    
    if output_path:
        output_path.write_bytes(pdf_bytes)
    
    return pdf_bytes
```

### Endpoint backend pour servir le PDF

```python
# Site/backend/app/routes/rvc.py
@router.get("/guide.pdf")
async def download_guide(_: bool = Depends(require_auth)):
    """Sert le guide PDF (généré à la volée ou caché sur disque)."""
    cache_path = config.DATA_DIR / "rvc_guide_cache.pdf"
    
    if not cache_path.exists() or _is_stale(cache_path):
        from ..services.rvc_guide_pdf import generate_guide_pdf
        generate_guide_pdf(cache_path)
    
    return FileResponse(
        cache_path,
        media_type="application/pdf",
        filename="VoiceBridge_RVC_Guide.pdf",
    )


def _is_stale(path: Path, max_age_seconds: int = 86400) -> bool:
    """True si le fichier est plus vieux que max_age_seconds."""
    import time
    return time.time() - path.stat().st_mtime > max_age_seconds
```

## CSS dédié

### `Site/frontend/css/recording-session.css`

```css
/* Wizard progress bar */
.wizard-progress {
  display: flex;
  gap: 4px;
  margin-bottom: 2rem;
  flex-wrap: wrap;
}
.wizard-step {
  flex: 1;
  padding: 0.5rem;
  background: var(--surface2);
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 0.75rem;
  text-align: center;
  color: var(--text3);
}
.wizard-step.active {
  background: var(--accent);
  color: white;
  border-color: var(--accent);
}
.wizard-step.done {
  background: var(--success);
  color: white;
}

/* VU-meter */
.vu-meter {
  height: 24px;
  background: var(--surface3);
  border-radius: 4px;
  overflow: hidden;
  position: relative;
  margin-bottom: 0.5rem;
}
.vu-bar {
  height: 100%;
  background: #16a34a;
  transition: width 50ms linear, background 200ms;
  width: 0%;
}
.vu-label {
  position: absolute;
  top: 50%;
  right: 8px;
  transform: translateY(-50%);
  font-family: 'DM Mono', monospace;
  font-size: 0.7rem;
  color: white;
  text-shadow: 0 0 2px black;
}

/* Duration display */
.duration-display {
  font-family: 'DM Mono', monospace;
  font-size: 1.5rem;
  margin: 1rem 0;
}
.duration-target {
  color: var(--text3);
}
.duration-bar {
  height: 8px;
  background: var(--surface3);
  border-radius: 4px;
  overflow: hidden;
  margin-top: 4px;
}
.duration-fill {
  height: 100%;
  background: var(--accent);
  transition: width 200ms;
  width: 0%;
}

/* Block text display */
.block-text-display {
  background: var(--surface2);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 1rem;
  max-height: 400px;
  overflow-y: auto;
  font-size: 1rem;
  line-height: 1.7;
  margin-bottom: 1rem;
}
.block-text-display h4 {
  color: var(--accent);
  font-size: 0.9rem;
  margin: 1rem 0 0.5rem;
}
.block-text-display h4:first-child {
  margin-top: 0;
}

/* Recording warnings */
.recording-warnings {
  margin-top: 0.5rem;
}
.warning {
  background: #FEF3C7;
  color: #92400E;
  padding: 0.5rem 0.75rem;
  border-radius: 4px;
  font-size: 0.8rem;
  margin-bottom: 0.25rem;
}
.warning.hidden {
  display: none;
}

/* Progress container (during dataset processing) */
.progress-container {
  background: var(--surface2);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 1rem;
  margin-top: 1rem;
}
.progress-header {
  display: flex;
  justify-content: space-between;
  margin-bottom: 0.5rem;
}
.progress-bar {
  height: 12px;
  background: var(--surface3);
  border-radius: 6px;
  overflow: hidden;
}
.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--accent), var(--accent2));
  transition: width 300ms;
  width: 0%;
}
.progress-meta {
  display: flex;
  justify-content: space-between;
  font-size: 0.75rem;
  color: var(--text3);
  margin-top: 0.5rem;
  font-family: 'DM Mono', monospace;
}
.progress-logs {
  background: var(--surface3);
  border-radius: 4px;
  padding: 0.5rem;
  margin-top: 0.5rem;
  font-family: 'DM Mono', monospace;
  font-size: 0.7rem;
  max-height: 100px;
  overflow-y: auto;
  display: none;  /* mode debug seulement */
}
```

## Tests à implémenter

```python
# tests/test_rvc_guide_pdf.py
def test_generate_pdf():
    from app.services.rvc_guide_pdf import generate_guide_pdf
    pdf_bytes = generate_guide_pdf()
    assert pdf_bytes.startswith(b'%PDF')
    assert len(pdf_bytes) > 10000  # taille raisonnable


def test_pdf_endpoint(client):
    r = client.get("/api/rvc/guide.pdf")
    assert r.status_code == 200
    assert r.headers['content-type'] == 'application/pdf'
```

```javascript
// tests/test_recording_session_capture.js
describe('RecordingCapture', () => {
  it('compute RMS from int16 array', () => {
    const cap = new RecordingCapture('test', 1, {});
    const arr = new Int16Array([1000, -1000, 2000, -2000]);
    const rms = cap._computeRMS(arr);
    expect(rms).toBeCloseTo(1581, 0);  // sqrt((1M+1M+4M+4M)/4) = 1581
  });
});
```

## Récap des routes backend

| Route | Méthode | Description |
|---|---|---|
| `/api/recording_session/create` | POST | Crée une nouvelle session |
| `/api/recording_session/{id}` | GET | Métadonnées d'une session |
| `/api/recording_session/{id}` | DELETE | Supprime une session |
| `/api/recording_session/{id}/append_chunk?block=N` | POST (binary) | Ajoute un chunk audio à un bloc |
| `/api/recording_session/{id}/finish_block` | POST | Marque un bloc comme terminé |
| `/api/recording_session/{id}/clear_block` | POST | Vide un bloc (pour recommencer) |
| `/api/recording_session/{id}/process` | POST | Lance le retraitement asynchrone |
| `/api/recording_session/{id}/processed` | GET | Liste des clips après traitement |
| `/api/recording_session/{id}/clip/{clip_id}/audio` | GET | Audio d'un clip (WAV) |
| `/api/recording_session/{id}/clip/{clip_id}` | DELETE | Supprime un clip |
| `/api/recording_session/{id}/export` | GET | ZIP du dataset prêt pour Kaggle |
| `/ws/progress/{task_id}` | WebSocket | Progression d'une tâche backend |
| `/api/rvc/guide.pdf` | GET | Guide PDF téléchargeable |

Détails dans `Spec/voicebridge_specs/05-backend-api.md` (section V3).
