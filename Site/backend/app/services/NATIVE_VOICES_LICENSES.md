# Attributions — Voix natives téléchargées par `native_voices.install_all()`

Ce fichier liste les sources et licences des samples audio utilisés par
les modes Live `gpu-native` et `gpu-hybrid`. Conformément aux licences
respectives (CC-BY-SA 3.0 en particulier), nous fournissons l'attribution
explicite ci-dessous.

| Langue | Voice ID | Source | URL | Licence |
|---|---|---|---|---|
| **EN** | `native_en` | whisper.cpp samples (US Government — JFK Rice University speech 1962) | https://github.com/ggml-org/whisper.cpp/raw/master/samples/jfk.wav | Public Domain |
| **FR** | `native_fr` | Wikipédia parlée — article *Braille* (lecteurs volontaires) | https://upload.wikimedia.org/wikipedia/commons/0/0e/Fr-braille.ogg | CC-BY-SA 3.0 |
| **ES** | `native_es` | Wikipedia hablada — article *Aaron Swartz* | https://upload.wikimedia.org/wikipedia/commons/3/31/Es-Aaron_Swartz-article.ogg | CC-BY-SA 3.0 |
| **DE** | `native_de` | Gesprochene Wikipedia — article *Albert Einstein* | https://upload.wikimedia.org/wikipedia/commons/4/44/De-Albert_Einstein.ogg | CC-BY-SA 3.0 |
| **IT** | `native_it` | Wikipedia parlata — article *Argentina* | https://upload.wikimedia.org/wikipedia/commons/2/2f/It-argentina-article.ogg | CC0 (Public Domain dedication) |
| **PT** | `native_pt` | Wikipédia falada — article *Andrelândia* | https://upload.wikimedia.org/wikipedia/commons/f/ff/Andrel%C3%A2ndia_intro.ogg | CC-BY-SA 3.0 |

## Note sur la transformation

Les fichiers d'origine sont des articles complets (3 à 38 minutes selon
la source). Le script d'installation extrait uniquement les **20 premières
secondes** et les convertit en WAV mono 24 kHz PCM 16-bit pour servir de
**référence prosodique** au modèle TTS F5-TTS.

Le contenu textuel des articles n'est ni stocké ni redistribué — seule
l'empreinte vocale (prosodie, accent, qualité tonale du locuteur) est
utilisée. Cette utilisation reste conforme aux licences CC-BY-SA dans la
mesure où nous attribuons les sources et où le rendu vocal final est une
synthèse F5-TTS, pas une rediffusion du sample d'origine.

## Pour retirer une voix

Si l'auteur d'un sample Wikimedia demande le retrait de son attribution,
ou si une licence change, modifier `app/services/native_voices.py`
(constante `NATIVE_VOICE_CATALOG`) et regénérer le set.

## Pour ajouter d'autres langues

Wikimedia Commons a des catégories `Spoken_<Language>_Wikipedia` pour ~30
langues. Trouver une URL stable au format
`https://upload.wikimedia.org/wikipedia/commons/<x>/<xx>/<filename>.ogg`,
l'ajouter au catalogue avec sa licence.
