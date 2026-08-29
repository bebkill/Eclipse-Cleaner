"""Version affichee : le numero declare, et l'etat reel de l'arbre git.

Module pur : il ne connait ni HTTP ni la page. Il repond a une question
d'exploitation — « sur quel arbre suis-je en train de travailler ? » — a
laquelle un numero seul ne repond pas pendant le developpement, ou deux
arbres differents portent la meme version.

Le numero vient du paquet ; le hash et l'etat modifie viennent de git, s'il
est joignable. Toute defaillance de git est silencieuse et rend le numero
seul : savoir sur quel commit on est est un confort, afficher la page est la
fonction. Une page qui refuserait de s'ouvrir parce que git manque serait
un mauvais echange.
"""
import os
import subprocess

from . import __version__

#: Delai maximal accorde a git. Il tourne sur le depot local, donc en
#: quelques dizaines de millisecondes ; deux secondes couvrent un disque
#: reveille sans jamais retarder visiblement l'ouverture de la page.
DELAI_GIT = 2.0

_RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _git(*args):
    """Sortie de git, ou None si git ne repond pas.

    Lance git dans la racine du DEPOT et non dans le repertoire courant :
    le viewer peut etre lance de n'importe ou, et git remonte l'arborescence
    depuis son cwd -- il trouverait alors un autre depot, ou aucun.
    """
    try:
        r = subprocess.run(("git",) + args, cwd=_RACINE, timeout=DELAI_GIT,
                           capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError):
        return None                 # git absent, ou trop lent
    if r.returncode != 0:
        return None                 # pas un depot, ou commande refusee
    return r.stdout.strip()


def etat_git():
    """(hash court, arbre modifie) ; (None, False) si git ne repond pas."""
    court = _git("rev-parse", "--short", "HEAD")
    if not court:
        return None, False
    # --porcelain rend une ligne par fichier modifie, rien du tout si
    # l'arbre est propre. On ne teste donc que le vide, pas un compte : un
    # fichier suffit a rendre le numero de version menteur.
    etat = _git("status", "--porcelain")
    return court, bool(etat)


def version_affichee():
    """Le texte montre dans le rail : « 1.0.0 (19bfc48) », ou « 1.0.0 »."""
    court, modifie = etat_git()
    if not court:
        return __version__
    return f"{__version__} ({court}{'+modifie' if modifie else ''})"
