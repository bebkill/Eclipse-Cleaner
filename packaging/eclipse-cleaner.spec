# -*- mode: python ; coding: utf-8 -*-
# Gel PyInstaller de l'executable Windows, en un seul fichier (onefile).
#
# Construire depuis n'importe ou (les chemins sont ancres sur ce fichier) :
#   pyinstaller packaging/eclipse-cleaner.spec
#
# console=True est un choix, pas un oubli : la fenetre montre la progression
# et les erreurs, et sa fermeture arrete proprement le serveur du viewer.
import os

# SPECPATH est fourni par PyInstaller : le dossier de ce fichier .spec.
RACINE = os.path.dirname(SPECPATH)

a = Analysis(
    [os.path.join(SPECPATH, "lanceur.py")],
    pathex=[RACINE],
    # Les ressources lues via os.path.dirname(__file__) doivent garder la
    # meme arborescence relative dans l'archive gelee.
    datas=[
        (os.path.join(RACINE, "eclipse", "static", "viewer.html"),
         os.path.join("eclipse", "static")),
        (os.path.join(RACINE, "eclipse", "langues", "en.json"),
         os.path.join("eclipse", "langues")),
        (os.path.join(RACINE, "eclipse", "langues", "fr.json"),
         os.path.join("eclipse", "langues")),
    ],
    # Le binaire ffmpeg d'imageio_ffmpeg est ramasse par le hook communautaire
    # (pyinstaller-hooks-contrib) ; tkinter, importe paresseusement dans
    # dialogue.py, est detecte par l'analyse du bytecode. Les deux sont
    # verifies par packaging/smoke_exe.py apres construction.
    hiddenimports=[],
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="eclipse-cleaner",
    console=True,
    upx=False,
)
