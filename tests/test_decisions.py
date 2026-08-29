import json
import os

from eclipse.decisions import (SCHEMA_DECISIONS, SUFFIXE_PRECEDENT, applique,
                               charger, diagnostique, enregistrer)

SIG = {"path": "/x/source.mp4", "size": 123, "mtime": 456}


def test_aller_retour(tmp_path):
    p = str(tmp_path / "d.json")
    enregistrer(p, SIG, {12: "conserver", 40: "ecarter"})
    assert charger(p, SIG) == {12: "conserver", 40: "ecarter"}


def test_fichier_absent_donne_aucun_ecart(tmp_path):
    assert charger(str(tmp_path / "rien.json"), SIG) == {}


def test_source_differente_est_refusee(tmp_path):
    """Des decisions prises sur une autre video seraient appliquees de travers."""
    p = str(tmp_path / "d.json")
    enregistrer(p, SIG, {12: "conserver"})
    assert charger(p, dict(SIG, size=999)) == {}


def test_schema_incompatible_est_refuse(tmp_path):
    p = str(tmp_path / "d.json")
    enregistrer(p, SIG, {12: "conserver"})
    d = json.load(open(p, encoding="utf-8"))
    d["schema"] = SCHEMA_DECISIONS + 1
    json.dump(d, open(p, "w", encoding="utf-8"))
    assert charger(p, SIG) == {}


def test_fichier_illisible_ne_leve_pas(tmp_path):
    p = str(tmp_path / "d.json")
    open(p, "w", encoding="utf-8").write("{ceci n'est pas du json")
    assert charger(p, SIG) == {}


def test_applique_conserve_une_frame_rejetee():
    v = [None, "too_dark", "too_dark", None]
    assert applique(v, {1: "conserver"}) == [None, None, "too_dark", None]


def test_applique_ecarte_une_frame_conservee():
    v = [None, None, None]
    assert applique(v, {1: "ecarter"}) == [None, "manuel", None]


def test_applique_ignore_un_index_hors_bornes():
    v = [None, None]
    assert applique(v, {7: "conserver"}) == [None, None]


def test_applique_ne_modifie_pas_l_entree():
    v = [None, "too_dark"]
    applique(v, {1: "conserver"})
    assert v == [None, "too_dark"]


# -- Finding 7 : un JSON dont la racine n'est pas un objet ne doit pas lever.

def test_racine_liste_ne_leve_pas(tmp_path):
    p = str(tmp_path / "d.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump([1, 2, 3], f)
    assert charger(p, SIG) == {}


def test_racine_null_ne_leve_pas(tmp_path):
    p = str(tmp_path / "d.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(None, f)
    assert charger(p, SIG) == {}


def test_champ_ecarts_invalide_ne_leve_pas(tmp_path):
    p = str(tmp_path / "d.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"schema": SCHEMA_DECISIONS, "source": SIG, "ecarts": "oups"}, f)
    assert charger(p, SIG) == {}


# -- Finding 3 : distinguer une absence normale d'un refus qui merite un
# avertissement (voir pipeline.render et viewer.construit_etat).

def test_diagnostique_absent_est_none(tmp_path):
    assert diagnostique(str(tmp_path / "rien.json"), SIG) is None


def test_diagnostique_valide_est_none(tmp_path):
    p = str(tmp_path / "d.json")
    enregistrer(p, SIG, {12: "conserver"})
    assert diagnostique(p, SIG) is None


def test_un_json_invalide_rend_le_fait_fichier_illisible(tmp_path):
    chemin = tmp_path / "decisions.json"
    chemin.write_text("{ pas du json", encoding="utf-8")
    fait = diagnostique(str(chemin), SIG)
    assert fait == {"code": "fichier_illisible", "chemin": str(chemin)}


def test_une_racine_non_objet_rend_le_fait_racine_invalide(tmp_path):
    chemin = tmp_path / "decisions.json"
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump([1, 2, 3], f)
    fait = diagnostique(str(chemin), SIG)
    assert fait == {"code": "racine_invalide", "chemin": str(chemin)}


def test_un_schema_perime_rend_le_fait_avec_les_deux_schemas(tmp_path):
    p = str(tmp_path / "d.json")
    enregistrer(p, SIG, {12: "conserver"})
    d = json.load(open(p, encoding="utf-8"))
    d["schema"] = SCHEMA_DECISIONS + 1
    json.dump(d, open(p, "w", encoding="utf-8"))
    fait = diagnostique(p, SIG)
    assert fait["code"] == "schema_incompatible"
    assert fait["trouve"] == SCHEMA_DECISIONS + 1
    assert fait["attendu"] == SCHEMA_DECISIONS


def test_une_autre_source_rend_le_fait_autre_source(tmp_path):
    p = str(tmp_path / "d.json")
    enregistrer(p, SIG, {12: "conserver"})
    fait = diagnostique(p, dict(SIG, size=999))
    assert fait == {"code": "autre_source", "chemin": p}


def test_des_ecarts_invalides_rendent_le_fait_ecarts_invalides(tmp_path):
    p = str(tmp_path / "d.json")
    enregistrer(p, SIG, {12: "conserver"})
    d = json.load(open(p, encoding="utf-8"))
    d["ecarts"] = [1, 2, 3]
    json.dump(d, open(p, "w", encoding="utf-8"))
    fait = diagnostique(p, SIG)
    assert fait == {"code": "ecarts_invalides", "chemin": p}


# -- Finding 4 : ecriture atomique (fichier temporaire puis os.replace).

def test_enregistrer_ne_laisse_pas_de_fichier_temporaire(tmp_path):
    p = str(tmp_path / "d.json")
    enregistrer(p, SIG, {12: "conserver"})
    assert os.listdir(tmp_path) == ["d.json"]


def test_enregistrer_remplace_un_fichier_existant_sans_le_vider_en_cas_d_echec(tmp_path, monkeypatch):
    """Simule un echec en cours d'ecriture : le fichier d'origine doit
    rester intact, jamais vide ni tronque."""
    p = str(tmp_path / "d.json")
    enregistrer(p, SIG, {1: "conserver"})
    avant = open(p, encoding="utf-8").read()

    reel_replace = os.replace

    def replace_qui_echoue(src, dst):
        raise OSError("panne simulee")

    monkeypatch.setattr(os, "replace", replace_qui_echoue)
    try:
        enregistrer(p, SIG, {2: "ecarter"})
    except OSError:
        pass
    monkeypatch.setattr(os, "replace", reel_replace)

    apres = open(p, encoding="utf-8").read()
    assert apres == avant                       # fichier d'origine intact
    # Aucun residu TEMPORAIRE ne doit trainer. La sauvegarde, elle, est prise
    # avant la livraison et survit a son echec : c'est son role.
    assert set(os.listdir(tmp_path)) == {"d.json", "d.json" + SUFFIXE_PRECEDENT}
    assert not [n for n in os.listdir(tmp_path) if n.startswith(".decisions-")]


# -- Revue de branche : une generation de sauvegarde (l'incident des 228
# decisions perdues).

def test_enregistrer_garde_l_avant_dernier_etat(tmp_path):
    """Apres deux enregistrements, .precedent porte l'avant-dernier.

    C'est ce qui aurait rendu les 228 decisions recuperables : l'ecriture
    fautive aurait pousse l'etat detruit sous .precedent au lieu de le
    faire disparaitre.
    """
    p = str(tmp_path / "d.json")
    enregistrer(p, SIG, {1: "conserver"})
    enregistrer(p, SIG, {2: "ecarter"})
    assert charger(p, SIG) == {2: "ecarter"}
    assert charger(p + SUFFIXE_PRECEDENT, SIG) == {1: "conserver"}


def test_la_sauvegarde_ne_retire_jamais_la_cible(tmp_path, monkeypatch):
    """La cible existe A TOUT INSTANT, y compris pendant la sauvegarde.

    Elle a d'abord ete faite par un renommage, ce qui ouvrait une fenetre ou
    `chemin` n'existait plus : si la livraison echouait la ET que la
    compensation echouait aussi -- ou si le processus mourait entre les deux,
    ou aucune compensation Python n'est possible -- il ne restait que le
    .precedent. Le viewer lisait alors {} en silence, et le deuxieme
    enregistrement suivant ecrasait la sauvegarde : le filet fabriquait la
    perte qu'il devait eviter. La copie ferme ce cas par construction.

    Le test observe l'invariant lui-meme et non le mecanisme : au moment ou
    la livraison est tentee, la cible doit etre la. Un retour au renommage
    fait echouer cette assertion-la, sans dependre d'aucune compensation.
    """
    p = str(tmp_path / "d.json")
    enregistrer(p, SIG, {1: "conserver"})
    avant = open(p, encoding="utf-8").read()

    reel_replace = os.replace
    cible_presente = []

    def replace_qui_echoue_a_la_livraison(src, dst, *a, **kw):
        # Le fichier temporaire porte ce prefixe : c'est la livraison finale.
        if os.path.basename(src).startswith(".decisions-"):
            cible_presente.append(os.path.isfile(dst))
            raise OSError("panne simulee a la livraison")
        return reel_replace(src, dst, *a, **kw)

    monkeypatch.setattr(os, "replace", replace_qui_echoue_a_la_livraison)
    try:
        enregistrer(p, SIG, {2: "ecarter"})
    except OSError:
        pass
    monkeypatch.setattr(os, "replace", reel_replace)

    assert cible_presente == [True]      # jamais retiree, meme un instant
    assert open(p, encoding="utf-8").read() == avant
    # Et la sauvegarde de l'etat courant a bien ete prise avant la panne.
    assert charger(p + SUFFIXE_PRECEDENT, SIG) == {1: "conserver"}
    assert set(os.listdir(tmp_path)) == {"d.json", "d.json" + SUFFIXE_PRECEDENT}
