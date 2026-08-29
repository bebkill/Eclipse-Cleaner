"""La version affichee, et son accord avec l'arbre git.

Ces tests ne supposent jamais que git existe sur la machine qui les lance :
c'est precisement le cas que le module doit traverser sans bruit.
"""
import io
import os
import subprocess

import eclipse
from eclipse import version as mv

_RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_pyproject_et_le_paquet_declarent_la_meme_version():
    """Deux declarations qui divergent valent moins qu'une.

    Le numero affiche dans le viewer vient du paquet ; celui qu'un
    installateur voit vient de pyproject. S'ils se separent, le viewer
    annonce une version qui n'existe nulle part ailleurs.
    """
    with io.open(os.path.join(_RACINE, "pyproject.toml"), encoding="utf-8") as f:
        for ligne in f:
            if ligne.startswith("version ="):
                declaree = ligne.split("=", 1)[1].strip().strip('"')
                break
        else:
            raise AssertionError("pyproject.toml ne declare aucune version")
    assert declaree == eclipse.__version__


def test_sans_git_la_version_seule_est_rendue(monkeypatch):
    """git absent : la page doit s'ouvrir quand meme.

    Savoir sur quel commit on tourne est un confort ; afficher la page est
    la fonction. Un viewer qui refuserait de s'ouvrir faute de git serait un
    mauvais echange.
    """
    monkeypatch.setattr(mv, "_git", lambda *a: None)
    assert mv.version_affichee() == eclipse.__version__
    assert mv.etat_git() == (None, False)


def test_un_arbre_propre_ne_porte_pas_la_marque(monkeypatch):
    monkeypatch.setattr(mv, "_git",
                        lambda *a: "abc1234" if a[0] == "rev-parse" else "")
    assert mv.etat_git() == ("abc1234", False)
    assert mv.version_affichee() == f"{eclipse.__version__} (abc1234)"


def test_un_arbre_modifie_porte_la_marque(monkeypatch):
    """La marque est ce qui empeche le numero de mentir pendant qu'on
    developpe : deux arbres differents portent sinon la meme version."""
    monkeypatch.setattr(
        mv, "_git",
        lambda *a: "abc1234" if a[0] == "rev-parse" else " M eclipse/viewer.py")
    assert mv.etat_git() == ("abc1234", True)
    assert mv.version_affichee() == f"{eclipse.__version__} (abc1234+modifie)"


def test_un_seul_fichier_modifie_suffit(monkeypatch):
    """--porcelain rend une ligne par fichier ; on ne teste que le vide.

    Compter les lignes, ou n'agir qu'au-dela d'un seuil, laisserait une
    modification isolee passer pour un arbre propre.
    """
    monkeypatch.setattr(
        mv, "_git",
        lambda *a: "abc1234" if a[0] == "rev-parse" else " M un/seul.py")
    assert mv.etat_git()[1] is True


def test_git_qui_echoue_est_traite_comme_absent(monkeypatch):
    """Un code de retour non nul -- dossier hors depot, commande refusee --
    ne doit pas remonter en exception."""
    class Faux:
        returncode = 128
        stdout = ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Faux())
    assert mv._git("rev-parse", "--short", "HEAD") is None


def test_git_introuvable_est_traite_comme_absent(monkeypatch):
    def explose(*a, **k):
        raise FileNotFoundError("git")
    monkeypatch.setattr(subprocess, "run", explose)
    assert mv._git("rev-parse") is None


def test_git_trop_lent_est_traite_comme_absent(monkeypatch):
    """Le delai est borne : une page qui attendrait git indefiniment ne
    s'ouvrirait jamais."""
    def traine(*a, **k):
        raise subprocess.TimeoutExpired("git", mv.DELAI_GIT)
    monkeypatch.setattr(subprocess, "run", traine)
    assert mv._git("rev-parse") is None


def test_git_est_lance_dans_la_racine_du_depot(monkeypatch):
    """Le viewer se lance de n'importe ou, et git remonte l'arborescence
    depuis son cwd : sans cwd explicite il trouverait un autre depot, ou
    aucun."""
    vu = {}

    class Faux:
        returncode = 0
        stdout = "abc1234\n"

    def espion(cmd, **k):
        vu["cwd"] = k.get("cwd")
        return Faux()

    monkeypatch.setattr(subprocess, "run", espion)
    mv._git("rev-parse")
    assert vu["cwd"] == mv._RACINE
    assert os.path.isdir(os.path.join(mv._RACINE, "eclipse"))
