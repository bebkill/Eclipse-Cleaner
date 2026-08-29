"""Les garde-fous de tests/conftest.py, eux-memes exerces.

Une garde qui cesserait d'etre armee ne se verrait qu'a la perte suivante :
ces tests-la sont ce qui la maintient en vie. Ils portent sur la TROISIEME
garde, celle qui refuse une ecriture Python vers le depot -- les deux
premieres (repertoire courant jetable, empreinte comparee) s'exercent
d'elles-memes a chaque test de la suite.

Aucun de ces tests n'ecrit dans le depot, meme si la garde tombait : la cible
choisie est un nom qui n'existe pas et ne doit jamais exister, et surtout PAS
decisions.json, qui est precisement le fichier detruit par l'accident.
"""
import os
import pathlib

import pytest

from eclipse.decisions import enregistrer
from tests.conftest import _RACINE, _sous_le_depot

SIG = {"path": "/x/source.mp4", "size": 123, "mtime": 456}

#: Sous la racine du depot, et introuvable : si la garde ne fonctionnait pas,
#: ces tests creeraient ce fichier-la et rien d'autre -- et la deuxieme garde
#: le dirait aussitot.
_CIBLE = os.path.join(_RACINE, "ne-doit-jamais-etre-ecrit.json")


def test_la_garde_refuse_un_enregistrement_de_decisions_dans_le_depot():
    """Le geste EXACT de l'accident : enregistrer() vers un chemin du depot.

    C'est le seul chemin par lequel un fichier de decisions est ecrit, et
    c'est celui qui a detruit 228 decisions de tri. La garde doit le refuser
    avant qu'il n'aboutisse, et non le constater apres.
    """
    with pytest.raises(AssertionError, match="ECRITURE REFUSEE"):
        enregistrer(_CIBLE, SIG, {3: "ecarter"})
    assert not os.path.exists(_CIBLE)
    # Le fichier temporaire d'enregistrer() nait dans le dossier de la cible,
    # avant le remplacement : le refus ne doit pas le laisser derriere lui.
    assert not [n for n in os.listdir(_RACINE) if n.startswith(".decisions-")]


def test_la_garde_refuse_une_ouverture_en_ecriture_dans_le_depot():
    with pytest.raises(AssertionError, match="ECRITURE REFUSEE"):
        open(_CIBLE, "w")
    assert not os.path.exists(_CIBLE)


def test_la_garde_refuse_aussi_une_ecriture_par_pathlib():
    """pathlib contournait la garde, et c'est la facon la plus courante
    d'ecrire dans cette suite.

    builtins.open et io.open sont le MEME objet, mais ce sont deux noms :
    Path.open et Path.write_text appellent io.open, qui continuait de pointer
    sur la fonction d'origine quand seul builtins.open etait corrige. La
    garde promettait alors plus que ce qu'elle tenait.
    """
    cible = pathlib.Path(_CIBLE)
    with pytest.raises(AssertionError, match="ECRITURE REFUSEE"):
        cible.write_text("rien", encoding="utf-8")
    with pytest.raises(AssertionError, match="ECRITURE REFUSEE"):
        cible.open("w", encoding="utf-8")
    assert not cible.exists()


def test_le_repertoire_courant_est_jetable(tmp_path):
    """Garde 1 : monkeypatch.chdir(tmp_path) rend le repertoire courant
    jetable, pour qu'un nom relatif (--cache, --decisions, .vignettes)
    n'atteigne jamais le depot.

    Cette egalite n'est pas gratuite : sans la fixture autouse
    _repertoire_jetable (retiree, ou renommee -- l'autouse ne depend pas du
    nom mais de la presence du chdir dans son corps), le repertoire courant
    serait celui d'ou pytest a ete lance, presque toujours la racine du
    depot, et tmp_path pointe structurellement ailleurs (le dossier temporaire
    du systeme). Les deux ne peuvent coincider que si le chdir a eu lieu.

    Volontairement pas etabli par mutation du chdir lui-meme : le reviseur
    qui a signale ce trou l'a explicitement laisse intact, une ecriture
    relative pendant la verification pourrait passer par os.open,
    tempfile.mkstemp ou ffmpeg, hors de portee des gardes 2 et 3."""
    assert os.path.samefile(os.getcwd(), tmp_path)


def test_la_garde_laisse_lire_le_depot():
    """Elle ne doit gener AUCUNE lecture : la suite lit data/ et eclipse/."""
    with open(os.path.join(_RACINE, "README.md"), encoding="utf-8") as f:
        assert f.read(1) != ""


def test_la_garde_laisse_ecrire_dans_tmp_path(tmp_path):
    chemin = str(tmp_path / "d.json")
    enregistrer(chemin, SIG, {1: "conserver"})
    assert os.path.isfile(chemin)


def test_la_garde_epargne_les_dossiers_volatils():
    """__pycache__ surtout : importlib y ecrit ses .pyc avec os.replace.

    Refuser ceux-la casserait le premier import paresseux venu -- et
    eclipse.viewer importe pipeline a l'interieur de ses fonctions.
    """
    assert not _sous_le_depot(
        os.path.join(_RACINE, "eclipse", "__pycache__", "viewer.pyc"))
    assert not _sous_le_depot(os.path.join(_RACINE, ".git", "index"))
    assert _sous_le_depot(os.path.join(_RACINE, "eclipse", "viewer.py"))


def test_la_garde_ignore_ce_qui_est_hors_du_depot(tmp_path):
    assert not _sous_le_depot(str(tmp_path / "d.json"))
    # Un descripteur de fichier entier, ce qu'open() accepte : ce n'est pas
    # un chemin, et os.fspath leve dessus.
    assert not _sous_le_depot(3)
