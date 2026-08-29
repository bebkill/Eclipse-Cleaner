"""Garde-fous qui s'appliquent a TOUS les tests, avant le premier d'entre eux.

Trois, et ils repondent au meme accident : une ecriture de test qui atteint le
depot. Le premier ferme les chemins RELATIFS, le deuxieme CONSTATE les
chemins absolus apres coup, le troisieme les REFUSE avant qu'ils n'aboutissent.
Voir _repertoire_jetable.
"""
import builtins
import io
import os

import pytest

#: La racine du depot, calculee depuis ce fichier et non depuis le repertoire
#: courant -- que la premiere fixture change justement.
_RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Dossiers dont le contenu bouge legitimement pendant une execution, et qui
#: ne portent aucune donnee de l'utilisateur : les exclure evite des echecs
#: qui n'apprennent rien. __pycache__ est le cas qui compte -- eclipse.viewer
#: importe pipeline PARESSEUSEMENT, a l'interieur des fonctions, donc le
#: premier test qui declenche une tache peut faire ecrire un .pyc en pleine
#: execution.
_IGNORES = {".git", "__pycache__", ".pytest_cache", ".venv", "venv",
            ".superpowers"}


def _instantane(racine):
    """{chemin: (mtime_ns, taille)} de tous les fichiers sous racine.

    os.scandir et entry.stat() plutot qu'os.walk + os.stat : sous Windows les
    metadonnees viennent avec l'entree de repertoire, sans appel systeme
    supplementaire. Mesure sur ce depot : 9 ms pour 2388 fichiers, dont les
    2096 de data/. C'est ce qui rend le controle abordable a chaque test.
    """
    instantane, pile = {}, [racine]
    while pile:
        dossier = pile.pop()
        try:
            entrees = list(os.scandir(dossier))
        except OSError:
            continue          # dossier disparu ou refuse : rien a comparer
        for entree in entrees:
            try:
                if entree.is_dir(follow_symlinks=False):
                    if entree.name not in _IGNORES:
                        pile.append(entree.path)
                else:
                    etat = entree.stat(follow_symlinks=False)
                    instantane[entree.path] = (etat.st_mtime_ns, etat.st_size)
            except OSError:
                pass          # entree volatile : ne pas faire echouer pour ca
    return instantane


#: _IGNORES en casse normalisee, pour la comparaison de composants de chemin
#: sous Windows.
_IGNORES_NORM = {os.path.normcase(nom) for nom in _IGNORES}

#: La racine, une fois normalisee : compare telle quelle a chaque cible.
_RACINE_NORM = os.path.normcase(_RACINE)


def _sous_le_depot(cible):
    """Vrai si cette cible d'ecriture designe un fichier suivi du depot.

    Les dossiers d'_IGNORES sont exclus, et ce n'est pas un detail : les .pyc
    de __pycache__ sont ecrits par importlib avec le meme os.replace que
    celui qu'on garde ici -- refuser ceux-la casserait le premier import
    paresseux venu.

    Une cible qui n'est pas un chemin (un descripteur de fichier entier, ce
    qu'open() accepte) n'est pas dans le depot : on la laisse passer.
    """
    try:
        absolu = os.path.normcase(os.path.abspath(os.fspath(cible)))
    except TypeError:
        return False
    if (absolu != _RACINE_NORM
            and not absolu.startswith(_RACINE_NORM + os.sep)):
        return False
    return not (set(absolu.split(os.sep)) & _IGNORES_NORM)


def _refus(operation, cible):
    raise AssertionError(
        f"ECRITURE REFUSEE dans le depot : {operation} -> {cible}\n\n"
        "Un test vient de fabriquer un chemin absolu sous la racine du "
        "depot. C'est exactement le geste qui a detruit 228 decisions de "
        "tri (voir _repertoire_jetable) : il est refuse AVANT d'aboutir, "
        "et non constate apres coup. Les tests n'ecrivent que dans "
        "tmp_path. Si l'ecriture vient du code teste et non du test, "
        "c'est le code qui fabrique le mauvais chemin.")


@pytest.fixture(scope="session")
def _empreinte_depot():
    """L'etat du depot, tenu a jour d'un test au suivant.

    De portee session pour ne prendre l'instantane complet qu'une fois : les
    tests suivants ne paient que la comparaison de fin. Ne touche pas au
    repertoire courant, donc ne peut pas devancer _repertoire_jetable sur ce
    terrain.
    """
    return _instantane(_RACINE)


@pytest.fixture(autouse=True)
def _repertoire_jetable(tmp_path, monkeypatch, _empreinte_depot):
    """Aucun test n'ecrit hors de tmp_path, ni par un chemin relatif ni par un
    chemin absolu.

    La regle du projet est que les tests n'ecrivent que dans tmp_path. Elle
    etait tenue en passant des chemins absolus partout -- c'est-a-dire en
    faisant confiance au CODE TESTE pour ne pas fabriquer de chemin ailleurs.
    Cette confiance a ete prise en defaut : une mutation de verification qui
    neutralisait viewer.chemins_derives (rendant « decisions.json », le
    defaut relatif au repertoire courant) a fait ecrire un test dans le
    decisions.json A LA RACINE DU DEPOT, par decisions.enregistrer, qui
    remplace atomiquement. Les 228 decisions de la sequence reelle ont ete
    perdues, sans recuperation possible.

    La lecon n'est pas « ne pas muter » : c'est qu'un test dont l'innocuite
    depend du code qu'il teste n'est pas sur. D'ou trois gardes.

    1. LE REPERTOIRE COURANT DEVIENT JETABLE. Les noms relatifs existent pour
       de bonnes raisons dans ce projet (--cache « analysis.json »,
       --decisions « decisions.json », les vignettes « .vignettes ») et
       n'importe lequel peut ressortir d'une regression ou d'une mutation.
       Une ecriture relative atterrit desormais dans un dossier temporaire.

    2. LE DEPOT EST COMPARE A LA FIN DE CHAQUE TEST. Le chdir ne peut RIEN
       contre une ecriture absolue, et cette tache en a justement ouvert la
       voie : les chemins derives sont des freres absolus de la source.
       tests/test_pipeline.py expose SOURCE_REELLE, calcule depuis __file__ et
       pointant dans data/ ; test_quality et test_render l'importent. Il n'est
       aujourd'hui que lu, mais le jour ou un test le passe a Porteur ou a
       change_source et declenche une decision, une analyse ou un rendu, la
       suite ecrit data/<video>-decisions.json, -analysis.json, -vignettes/,
       -clean.mp4 -- a cote de la source de production, et le chdir n'y voit
       rien.

    3. QUATRE APPELS D'ECRITURE SONT REFUSES QUAND ILS VISENT LE DEPOT :
       os.replace, os.rename, builtins.open et io.open en mode d'ecriture.
       La garde 2 CONSTATE, apres coup : elle nomme la perte, elle ne
       l'empeche pas. Or l'artefact irremplacable de ce projet -- le fichier
       de decisions -- est ecrit UNIQUEMENT par decisions.enregistrer, du
       Python pur, un os.replace et une copie : l'interception y est donc
       complete, et elle aurait empeche la perte au lieu de la nommer.

       io.open EN PLUS de builtins.open, et ce n'est pas une redondance : ce
       sont le meme objet, mais corriger le NOM dans builtins laisse
       pathlib.Path.open et Path.write_text appeler io.open, qui pointe
       toujours sur la fonction d'origine. Verifie sur cet interpreteur.
       Sans cette seconde ligne, la garde serait aveugle a la facon dont
       cette suite ecrit le plus souvent.

       CE QU'ELLE NE COUVRE PAS, et le titre ne doit pas promettre plus que
       le code : os.open (donc tempfile.mkstemp cree encore son
       .decisions-*.tmp dans le depot avant que le refus ne survienne au
       remplacement), les SUPPRESSIONS (os.remove, shutil.rmtree), et les
       processus ffmpeg de vignettes.genere et pipeline.render, qui ecrivent
       leurs fichiers eux-memes, hors de portee de tout monkeypatch -- mais
       ceux-la produisent un mp4 et des jpg, qui SE REGAGNENT en faisant
       tourner la machine. La garde couvre ce qui ne se regagne pas ; la
       garde 2, elle, continue de voir tout le reste, ffmpeg compris.

    L'echec est attribue au test fautif, et l'empreinte est remise a jour
    ensuite : un coupable ne fait pas tomber les trois cents tests suivants.
    """
    monkeypatch.chdir(tmp_path)

    reel_replace, reel_rename = os.replace, os.rename
    reel_open = builtins.open

    def replace_garde(src, dst, *a, **kw):
        if _sous_le_depot(dst):
            _refus("os.replace", dst)
        return reel_replace(src, dst, *a, **kw)

    def rename_garde(src, dst, *a, **kw):
        if _sous_le_depot(dst):
            _refus("os.rename", dst)
        return reel_rename(src, dst, *a, **kw)

    def open_garde(fichier, mode="r", *a, **kw):
        # Tout mode qui n'est pas une lecture pure : w, a, x, et r+ compris.
        if any(c in mode for c in "wax+") and _sous_le_depot(fichier):
            _refus(f"open(..., {mode!r})", fichier)
        return reel_open(fichier, mode, *a, **kw)

    monkeypatch.setattr(os, "replace", replace_garde)
    monkeypatch.setattr(os, "rename", rename_garde)
    monkeypatch.setattr(builtins, "open", open_garde)
    # builtins.open EST io.open, mais ce sont deux NOMS : pathlib appelle le
    # second, qui pointerait encore sur la fonction d'origine.
    monkeypatch.setattr(io, "open", open_garde)
    yield
    apres = _instantane(_RACINE)
    changes = sorted(
        chemin for chemin in set(_empreinte_depot) | set(apres)
        if _empreinte_depot.get(chemin) != apres.get(chemin))
    if changes:
        _empreinte_depot.clear()
        _empreinte_depot.update(apres)
        pytest.fail(
            "des fichiers du depot ont change pendant ce test :\n  "
            + "\n  ".join(changes)
            + "\n\nTrois explications, par ordre de gravite. (1) Le "
              "test -- ou le code qu'il exerce -- a fabrique un chemin "
              "hors de tmp_path : c'est le defaut a corriger. (2) UN "
              "TEST PRECEDENT a laisse un fil vivant qui a ecrit apres "
              "son teardown, et le nom ci-dessus est alors celui du "
              "test SUIVANT, innocent. C'est l'echec le plus "
              "deroutant de ce depot, parce que taches.Moteur fait "
              "justement tourner ses taches dans des fils non demons : "
              "chercher un test qui n'attend pas la fin de sa tache. "
              "(3) L'arbre a ete modifie de l'EXTERIEUR pendant "
              "l'execution (une edition, un script de mutation), ce "
              "qui rend de toute facon la passe douteuse puisque les "
              "modules sont deja importes.", pytrace=False)
