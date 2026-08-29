import json

from eclipse.descripteur import (NOM_SCHEMA, chemin_descripteur, compare,
                                 ecrit, perime)

REGLAGES = {"seuils": {"conf_min": 0.02}, "tolerance_bord": 25.0}
SIG = {"path": "/x.mp4", "size": 1, "mtime": 2}


def test_l_ordre_d_insertion_ne_perime_rien(tmp_path):
    """Ce que le hachage du schema 1 obtenait par un tri de cles, le schema 2
    l'obtient de l'egalite de deux dict, qui ignore l'ordre d'insertion.

    Sans cette propriete, deux tris identiques construits dans un ordre
    different perimeraient le rendu et le bandeau annoncerait une peremption
    imaginaire."""
    sortie = str(tmp_path / "o.mp4")
    ecrit(sortie, {1: "ecarter", 5: "conserver"}, SIG, REGLAGES)
    assert perime(sortie, {5: "conserver", 1: "ecarter"}, SIG, REGLAGES) is False


def test_le_descripteur_enregistre_les_ecarts_et_non_leur_empreinte(tmp_path):
    """Le point du schema 2. Une empreinte sait dire QUE le tri a change,
    jamais QUOI : c'est de ce fichier que /api/frames tire les frames
    divergentes, et un hachage ne s'y prete pas."""
    sortie = str(tmp_path / "o.mp4")
    ecrit(sortie, {1: "ecarter", 5: "conserver"}, SIG, REGLAGES)
    with open(chemin_descripteur(sortie), encoding="utf-8") as f:
        lu = json.load(f)
    assert lu["schema"] == NOM_SCHEMA
    # Cles en TEXTE : c'est ce que json rend a la relecture, et c'est donc la
    # forme que perime() doit comparer.
    assert lu["decisions"] == {"1": "ecarter", "5": "conserver"}


def test_un_descripteur_de_schema_1_est_perime(tmp_path):
    """La montee de schema doit emprunter le chemin « a refaire », et non un
    autre : perime() traite un schema inconnu comme une defaillance, ce qui
    se repare tout seul au rendu suivant."""
    sortie = str(tmp_path / "o.mp4")
    with open(chemin_descripteur(sortie), "w", encoding="utf-8") as f:
        json.dump({"schema": 1, "decisions": "un-hachage",
                   "cache": SIG, "reglages": REGLAGES}, f)
    perime_, divergentes = compare(sortie, {}, SIG, REGLAGES)
    assert perime_ is True
    # Et AUCUNE marque : le tri du schema 1 n'est pas relisible frame par
    # frame, dire lesquelles divergent serait l'inventer.
    assert divergentes == ()


def test_un_schema_inconnu_a_decisions_bien_formees_ne_les_relit_pas(tmp_path):
    """La garde de schema de compare() doit fermer la porte AVANT toute
    relecture des decisions, meme quand ces decisions sont bien formees.

    Un descripteur {"schema": 999} sans cle "decisions" exploitable (ou avec
    une chaine) passe par _ecarts_relus, qui rend None, et divergentes est
    () de toute facon -- ce cas-la ne prouve rien sur la garde de schema
    elle-meme. Ici les decisions sont un dict a cles convertibles en entiers,
    exactement la forme qu'ecrirait un schema futur : sans le controle sur
    lu.get("schema"), _ecarts_relus les relirait avec succes et
    _divergences peindrait un lisere depuis un format qu'on ne connait pas.
    perime doit rester vrai (invariant du module), et divergentes doit
    rester vide : le schema est inconnu, on ne sait pas lire ce format."""
    sortie = str(tmp_path / "o.mp4")
    with open(chemin_descripteur(sortie), "w", encoding="utf-8") as f:
        json.dump({"schema": 3, "decisions": {"7": "ecarter"},
                   "cache": SIG, "reglages": REGLAGES}, f)
    assert compare(sortie, {}, SIG, REGLAGES) == (True, ())


def test_un_descripteur_absent_rend_perime(tmp_path):
    sortie = str(tmp_path / "o.mp4")
    assert perime(sortie, {}, SIG, REGLAGES) is True


def test_un_descripteur_ecrit_puis_relu_n_est_pas_perime(tmp_path):
    sortie = str(tmp_path / "o.mp4")
    ecrit(sortie, {1: "ecarter"}, SIG, REGLAGES)
    assert perime(sortie, {1: "ecarter"}, SIG, REGLAGES) is False


def test_un_changement_de_decision_perime(tmp_path):
    sortie = str(tmp_path / "o.mp4")
    ecrit(sortie, {1: "ecarter"}, SIG, REGLAGES)
    assert perime(sortie, {1: "ecarter", 2: "ecarter"}, SIG, REGLAGES) is True


def test_un_changement_de_cache_perime(tmp_path):
    sortie = str(tmp_path / "o.mp4")
    ecrit(sortie, {}, SIG, REGLAGES)
    autre = dict(SIG, mtime=3)
    assert perime(sortie, {}, autre, REGLAGES) is True


def test_un_changement_de_reglages_perime(tmp_path):
    sortie = str(tmp_path / "o.mp4")
    ecrit(sortie, {}, SIG, REGLAGES)
    autres = {"seuils": {"conf_min": 0.5}, "tolerance_bord": 25.0}
    assert perime(sortie, {}, SIG, autres) is True


def test_un_descripteur_illisible_rend_perime(tmp_path):
    """Toute defaillance penche vers « a refaire », jamais vers « a jour »."""
    sortie = str(tmp_path / "o.mp4")
    with open(chemin_descripteur(sortie), "w", encoding="utf-8") as f:
        f.write("{ceci n'est pas du json")
    assert perime(sortie, {}, SIG, REGLAGES) is True


def test_un_schema_inconnu_rend_perime(tmp_path):
    sortie = str(tmp_path / "o.mp4")
    with open(chemin_descripteur(sortie), "w", encoding="utf-8") as f:
        json.dump({"schema": 999}, f)
    assert perime(sortie, {}, SIG, REGLAGES) is True


def test_le_descripteur_ne_prend_pas_le_nom_du_rendu(tmp_path):
    """<sortie>.json et non <sortie>.mp4.json : une extension inconnue a
    deja fait ecrire zero octet en silence sur ce projet."""
    assert chemin_descripteur(str(tmp_path / "o.mp4")).endswith("o.json")


def test_un_descripteur_a_l_encodage_invalide_rend_perime(tmp_path):
    """Un octet non-UTF8 externe doit rendre perime, jamais faire lever
    perime() : l'exception remonterait a un appelant qui ne l'attend pas,
    ce qui est pire qu'une fausse peremption."""
    sortie = str(tmp_path / "o.mp4")
    with open(chemin_descripteur(sortie), "wb") as f:
        f.write(b"\xff\xfe\x00\x01")
    assert perime(sortie, {}, SIG, REGLAGES) is True


# -- Les divergences : ce que le schema 2 rend possible, et que le schema 1
# ne pouvait pas dire.

def test_aucune_divergence_sur_un_descripteur_a_jour(tmp_path):
    sortie = str(tmp_path / "o.mp4")
    ecrit(sortie, {1: "ecarter"}, SIG, REGLAGES)
    assert compare(sortie, {1: "ecarter"}, SIG, REGLAGES) == (False, ())


def test_un_ecart_ajoute_diverge(tmp_path):
    sortie = str(tmp_path / "o.mp4")
    ecrit(sortie, {}, SIG, REGLAGES)
    assert compare(sortie, {7: "ecarter"}, SIG, REGLAGES)[1] == (7,)


def test_un_ecart_retire_diverge(tmp_path):
    """L'autre sens, et il compte autant : le rendu a ecarte la frame 7, on
    a change d'avis, elle revient au montage. Une comparaison qui ne
    parcourrait que les ecarts COURANTS ne verrait rien."""
    sortie = str(tmp_path / "o.mp4")
    ecrit(sortie, {7: "ecarter"}, SIG, REGLAGES)
    assert compare(sortie, {}, SIG, REGLAGES)[1] == (7,)


def test_un_ecart_retourne_diverge(tmp_path):
    sortie = str(tmp_path / "o.mp4")
    ecrit(sortie, {7: "ecarter"}, SIG, REGLAGES)
    assert compare(sortie, {7: "conserver"}, SIG, REGLAGES)[1] == (7,)


def test_les_divergences_sont_triees_et_sans_doublon(tmp_path):
    sortie = str(tmp_path / "o.mp4")
    ecrit(sortie, {5: "ecarter", 9: "ecarter"}, SIG, REGLAGES)
    assert compare(sortie, {1: "ecarter", 5: "conserver", 9: "ecarter"},
                   SIG, REGLAGES)[1] == (1, 5)


def test_un_changement_de_cache_ne_fabrique_pas_de_divergence(tmp_path):
    """Le rendu est perime, mais AUCUNE frame n'a change de statut : la
    timeline ne doit rien peindre. Sans cette separation, une reanalyse
    couvrirait la pellicule entiere de liseres qui ne veulent rien dire."""
    sortie = str(tmp_path / "o.mp4")
    ecrit(sortie, {1: "ecarter"}, SIG, REGLAGES)
    perime_, divergentes = compare(sortie, {1: "ecarter"},
                                   dict(SIG, mtime=3), REGLAGES)
    assert perime_ is True
    assert divergentes == ()


def test_un_descripteur_absent_ne_diverge_de_rien(tmp_path):
    """Vide veut dire « rien a montrer », jamais « rien n'a change » : c'est
    perime qui porte cette reponse-la."""
    sortie = str(tmp_path / "o.mp4")
    assert compare(sortie, {1: "ecarter"}, SIG, REGLAGES) == (True, ())


def test_des_decisions_a_cles_non_entieres_ne_divergent_de_rien(tmp_path):
    """Un descripteur trafique a la main. On ne sait plus a quelle frame
    rattacher quoi : se taire, et rester perime."""
    sortie = str(tmp_path / "o.mp4")
    with open(chemin_descripteur(sortie), "w", encoding="utf-8") as f:
        json.dump({"schema": NOM_SCHEMA, "decisions": {"abc": "ecarter"},
                   "cache": SIG, "reglages": REGLAGES}, f)
    perime_, divergentes = compare(sortie, {1: "ecarter"}, SIG, REGLAGES)
    assert perime_ is True
    assert divergentes == ()


def test_des_decisions_qui_ne_sont_pas_un_dict_ne_divergent_de_rien(tmp_path):
    sortie = str(tmp_path / "o.mp4")
    with open(chemin_descripteur(sortie), "w", encoding="utf-8") as f:
        json.dump({"schema": NOM_SCHEMA, "decisions": ["ecarter"],
                   "cache": SIG, "reglages": REGLAGES}, f)
    assert compare(sortie, {}, SIG, REGLAGES) == (True, ())
