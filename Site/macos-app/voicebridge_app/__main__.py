"""Entry point pour ``python -m voicebridge_app``.

Permet de lancer l'app en mode dev sans passer par le bundle PyInstaller :
    python -m voicebridge_app

main.py utilise des imports plats (``import audio``, ``import config``)
qui ne marchent que si son dossier est sur sys.path. main.py patch lui-
même sys.path au moment de son import, donc on l'importe d'abord via
le chemin de package puis on appelle main().
"""
from voicebridge_app.main import main

if __name__ == "__main__":
    main()
