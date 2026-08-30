# -*- mode: python ; coding: utf-8 -*-
# Gel PyInstaller de l'executable Windows, en un seul fichier (onefile).
#
# Construire depuis n'importe ou (les chemins sont ancres sur ce fichier) :
#   pyinstaller packaging/eclipse-cleaner.spec
#
# console=True est un choix, pas un oubli : la fenetre montre la progression
# et les erreurs, et sa fermeture arrete proprement le serveur du viewer.
import os
import re

# SPECPATH est fourni par PyInstaller : le dossier de ce fichier .spec.
RACINE = os.path.dirname(SPECPATH)

# La ressource VERSIONINFO de l'exe (proprietes du fichier sous Windows),
# generee ici depuis eclipse/__init__.py — une seule source pour le numero.
# Exigee par la politique de signature de code (nom de produit, version) et
# lisible par l'utilisateur ; lue par regex et non par import : le paquet n'a
# pas a etre importable dans l'environnement qui construit.
_init = open(os.path.join(RACINE, "eclipse", "__init__.py"),
             encoding="utf-8").read()
VERSION = re.search(r'__version__ = "([^"]+)"', _init).group(1)
_nums = tuple((list(map(int, VERSION.split("."))) + [0, 0, 0, 0])[:4])

_VERSIONINFO = f"""
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={_nums}, prodvers={_nums},
    mask=0x3F, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0,
    date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('ProductName', 'Eclipse Cleaner'),
      StringStruct('FileDescription',
                   'Eclipse Cleaner - clean up and stabilize a solar-eclipse timelapse'),
      StringStruct('FileVersion', '{VERSION}'),
      StringStruct('ProductVersion', '{VERSION}'),
      StringStruct('CompanyName', 'bebkill'),
      StringStruct('LegalCopyright', 'MIT License (c) bebkill'),
      StringStruct('OriginalFilename', 'eclipse-cleaner.exe')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])]),
  ])
"""
_version_file = os.path.join(workpath, "version_info.txt")
with open(_version_file, "w", encoding="utf-8") as f:
    f.write(_VERSIONINFO)

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
    version=_version_file,
)
