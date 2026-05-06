#!/usr/bin/env python3
"""Télécharge 4 voix natives par défaut (EN, ES, PT, IT) depuis Mozilla
Common Voice et les pousse dans le store Hostinger côté backend.

Cf. Décision 2 du document 00-decisions-v3.md.

Pré-requis :
- Backend Hostinger lancé (au moins en local pour le dev)
- Clé API VoiceBridge (pour s'authentifier au backend)
- ffmpeg installé localement
- Une clé Common Voice (gratuite, https://commonvoice.mozilla.org/api/v1)
  OU des fichiers audio sourcés manuellement

Usage :
    python download_native_voices.py \\
        --backend-url https://votre-domaine.com \\
        --api-key sk-votre-cle-vb

Optionnel :
    --languages en,es,pt,it    (défaut)
    --gender male              (par défaut)
    --duration-seconds 10      (cible : 8-12s)

ATTENTION : ce script doit être lancé UNE SEULE FOIS au déploiement initial.
Il est idempotent : si une voix native par défaut existe déjà côté backend
(via is_default=true + language match), elle n'est PAS recréée.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO,
                    format="[%(asctime)s] [%(levelname)s] %(message)s")
log = logging.getLogger("seed-native-voices")


# Configuration des voix par défaut V3.0
# Format : code ISO → métadonnées affichées + critères Common Voice
DEFAULT_NATIVE_VOICES = {
    "en": {
        "name": "EN — voix homme native",
        "common_voice_locale": "en",
    },
    "es": {
        "name": "ES — voix homme native",
        "common_voice_locale": "es",
    },
    "pt": {
        "name": "PT — voix homme native",
        "common_voice_locale": "pt",
    },
    "it": {
        "name": "IT — voix homme native",
        "common_voice_locale": "it",
    },
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-url", required=True,
                        help="URL du backend VoiceBridge (https://...)")
    parser.add_argument("--api-key", required=True,
                        help="Clé API VoiceBridge (sk-...)")
    parser.add_argument("--languages", default="en,es,pt,it",
                        help="Codes ISO séparés par des virgules")
    parser.add_argument("--gender", default="male",
                        choices=["male", "female"],
                        help="Genre de la voix native (Common Voice tag)")
    parser.add_argument("--duration-seconds", type=int, default=10,
                        help="Durée cible du sample (8-12s recommandé)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Affiche ce qui serait fait sans rien envoyer")
    args = parser.parse_args()

    languages = [lang.strip() for lang in args.languages.split(",")]
    log.info("Seeding native voices for languages: %s", languages)

    # ----------------------------------------------------------------------
    # NOTE D'IMPLÉMENTATION
    # ----------------------------------------------------------------------
    # Common Voice n'expose pas encore d'API publique stable pour requêter
    # directement un sample par langue + genre + durée. Plusieurs options :
    #
    # Option 1 : utiliser le dataset HuggingFace 'mozilla-foundation/common_voice_17_0'
    #            qui est versionné, propre, et permet de filtrer par langue.
    #            (recommandé pour la reproductibilité)
    #
    # Option 2 : sourcer manuellement les 4 fichiers depuis archive.org ou
    #            un autre dataset CC0 (LibriVox pour EN, etc.) et les héberger
    #            sur un bucket public stable.
    #
    # Pour le V3.0 first ship, on documente le besoin et on laisse au
    # déployeur le soin de fournir manuellement les 4 fichiers via
    # `voicebridge-cli add-native-voice --file en_male.wav --lang en`.
    #
    # Ce script sera complété en Phase F (frontend RVC + voices) une fois
    # l'API backend `/api/voices?kind=native` disponible.
    # ----------------------------------------------------------------------

    log.warning("Ce script est un squelette. Pour V3.0 :")
    log.warning("  1. Sourcer manuellement 4 fichiers WAV (EN/ES/PT/IT)")
    log.warning("  2. Lancer : voicebridge-cli add-native-voice "
                "--file <path>.wav --lang <code> --is-default true")
    log.warning("Implémentation auto-download Common Voice : Phase F")
    return 0


if __name__ == "__main__":
    sys.exit(main())
