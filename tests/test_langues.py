"""Les garde-fous des tables de langue.

Une i18n se degrade en silence : une cle ajoutee d'un cote et oubliee de
l'autre n'est pas une erreur, c'est un trou a l'ecran. Ces tests refusent
ce silence.
"""
import json
import os
import re

import pytest

from eclipse import langues

_RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _cles(table, prefixe=""):
    """Toutes les cles d'une table, y compris les formes de pluriel."""
    trouvees = set()
    for cle, valeur in table.items():
        chemin = f"{prefixe}{cle}"
        if isinstance(valeur, dict):
            for forme in valeur:
                trouvees.add(f"{chemin}.{forme}")
        else:
            trouvees.add(chemin)
    return trouvees


def test_les_deux_tables_portent_exactement_les_memes_cles():
    fr, en = langues.charge("fr"), langues.charge("en")
    manquantes_en = _cles(fr) - _cles(en)
    manquantes_fr = _cles(en) - _cles(fr)
    assert not manquantes_en, f"absentes de en.json : {sorted(manquantes_en)}"
    assert not manquantes_fr, f"absentes de fr.json : {sorted(manquantes_fr)}"


def test_une_entree_a_formes_porte_one_et_other_dans_les_deux_langues():
    for nom in langues.NOMS:
        table = langues.charge(nom)
        for cle, valeur in table.items():
            if isinstance(valeur, dict):
                assert set(valeur) == {"one", "other"}, f"{nom}:{cle}"


def test_aucune_valeur_vide():
    for nom in langues.NOMS:
        for cle, valeur in langues.charge(nom).items():
            formes = valeur.values() if isinstance(valeur, dict) else [valeur]
            for f in formes:
                assert isinstance(f, str) and f.strip(), f"{nom}:{cle}"


def _forme_plurielle(nom, n):
    """La regle CLDR reellement appliquee par Intl.PluralRules(langue).select(n)
    dans un navigateur -- PAS le repli n === 1 de t() (viewer.html), qui ne
    joue que si Intl.PluralRules est absent. Divergence mesuree a la revue :
    Intl.PluralRules("fr").select(0) vaut "one" (0 ET 1 sont "one" en
    francais -- contre-intuitif, mais la vraie regle), alors que le repli de
    t() rendrait "other" pour n=0. Une premiere version de ce test
    reimplementait le repli, jamais la branche normale : elle aurait pu
    valider une table fausse pour n=0 en francais sans qu'aucune assertion
    n'en souffre -- exactement la classe de defaut que ce chantier essaie de
    chasser, quelque chose de faux qui ne fait rien echouer.

    Couvre seulement les deux langues de ce projet (fr, en) et seulement ce
    que comptes_sortie utilise (n entier >= 0) : pas une implementation
    generale des plafonds CLDR (fractions, categories few/many/two...)."""
    if nom == "fr":
        return "one" if n in (0, 1) else "other"
    return "one" if n == 1 else "other"


def _rend(nom, table, cle, valeurs=None):
    """Reimplementation minimale, en Python, de t() (viewer.html) : une
    forme choisie par _forme_plurielle(nom, valeurs["n"]) pour les entrees a
    pluriel, sinon la chaine telle quelle, puis substitution de {nom}. Sert
    a eprouver les VALEURS reelles des tables sans executer de JavaScript
    (node n'est pas une dependance du projet -- voir les contraintes
    globales du chantier)."""
    modele = table[cle]
    if isinstance(modele, dict):
        n = valeurs.get("n") if valeurs else None
        forme = _forme_plurielle(nom, n)
        modele = modele.get(forme, modele["other"])
    if not valeurs:
        return modele
    return re.sub(r"\{(\w+)\}",
                  lambda m: str(valeurs[m.group(1)]) if m.group(1) in valeurs else m.group(0),
                  modele)


def _texte_comptes(nom, table, r):
    """Reimplementation de texteComptes (viewer.html) : chaque compte
    s'accorde avec LUI-MEME (compte_frames_*), et comptes_sortie n'est plus
    qu'un assemblage a pluriel deja resolu par ses fragments."""
    return _rend(nom, table, "comptes_sortie", {
        "total": _rend(nom, table, "compte_frames_total", {"n": r["total"]}),
        "ecrites": _rend(nom, table, "compte_frames_rendues", {"n": r["ecrites"]}),
        "interpolees": _rend(nom, table, "compte_frames_interpolees", {"n": r["interpolees"]}),
        "ecartees": _rend(nom, table, "compte_frames_ecartees", {"n": r["ecartees"]}),
    })


def test_comptes_sortie_accorde_chaque_compte_independamment():
    """Defaut trouve a la revue de la tache 3 : le mecanisme initial
    choisissait UNE forme de pluriel sur le total puis l'appliquait aux
    quatre comptes, produisant « 1 frames ecartees » (et « 1 dropped
    frames ») des qu'un seul des quatre valait 1 sans que le total y soit.
    Chaque compte doit desormais s'accorder avec lui-meme, dans les deux
    langues, aux deux formes -- y compris le cas qui a casse la premiere
    version (un grand total, un compte a 1), ET le cas zero (0 selectionne
    "one" en francais par la vraie regle CLDR, mais "other" en anglais :
    « 0 frame ecartee » contre « 0 dropped frames », pas symetrique)."""
    cas_ordinaire = {"total": 200, "ecrites": 196, "interpolees": 4, "ecartees": 4}
    cas_qui_cassait = {"total": 200, "ecrites": 199, "interpolees": 0, "ecartees": 1}
    cas_zero = {"total": 200, "ecrites": 200, "interpolees": 0, "ecartees": 0}
    for nom in langues.NOMS:
        table = langues.charge(nom)
        ordinaire = _texte_comptes(nom, table, cas_ordinaire)
        casse = _texte_comptes(nom, table, cas_qui_cassait)
        zero = _texte_comptes(nom, table, cas_zero)
        # La precision fonctionnelle qui justifie la phrase (voir son
        # commentaire dans viewer.html, texteComptes) : les quatre nombres
        # y figurent tous, dans chacun des trois cas.
        for valeurs, texte in ((cas_ordinaire, ordinaire), (cas_qui_cassait, casse),
                               (cas_zero, zero)):
            for n in valeurs.values():
                assert str(n) in texte, f"{nom}: {texte}"
        # Le point final borne le mot : "1 dropped frame" est un prefixe de
        # "1 dropped frames", une assertion positive sans cette frontiere
        # serait donc vraie meme dans le cas fautif.
        if nom == "fr":
            assert "1 frame écartée." in casse, casse
            assert "1 frames écartées." not in casse, casse
            # Le cas contre-intuitif : 0 est singulier en francais (CLDR).
            assert "0 frame écartée." in zero, zero
            assert "0 frames écartées." not in zero, zero
        else:
            assert "1 dropped frame." in casse, casse
            assert "1 dropped frames." not in casse, casse
            # L'anglais, lui, traite 0 comme pluriel : pas symetrique avec
            # le francais, et c'est justement ce que ce cas verifie.
            assert "0 dropped frames." in zero, zero
            assert "0 dropped frame." not in zero, zero


def test_une_langue_inconnue_est_refusee():
    with pytest.raises(FileNotFoundError):
        langues.charge("de")


# rend_fr est le SEUL rendu des huit faits d'avertissement pour la ligne de
# commande (les cinq de decisions.diagnostique, tri_orphelin et
# tri_orphelin_reprise de viewer._tri_orpheline, rechargement_impossible de
# taches.Moteur._enveloppe) -- rien ne l'eprouvait directement avant les
# tests ci-dessous : seuls texteComptes et le pendant JS de rend_fr (t(),
# via _rend plus haut) avaient leur propre couverture.

def test_rend_fr_substitue_un_champ_simple():
    """Les quatre faits de diagnostique sans second champ variable :
    verifie la substitution EFFECTIVE de {chemin}, pas seulement sa
    presence dans le modele (voir fr.json)."""
    for code in ("fichier_illisible", "racine_invalide", "autre_source",
                 "ecarts_invalides"):
        texte = langues.rend_fr({"code": code, "chemin": "/tmp/d.json"})
        assert "/tmp/d.json" in texte, f"{code} : {texte}"
        assert "{chemin}" not in texte, f"{code} : {texte}"


def test_rend_fr_substitue_les_deux_champs_du_schema_incompatible():
    """schema_incompatible est le seul des cinq faits de diagnostique a
    porter deux champs variables en plus de chemin (trouve, attendu) -- le
    seul cas ou une confusion entre deux champs serait meme possible."""
    texte = langues.rend_fr({"code": "schema_incompatible",
                             "chemin": "/tmp/d.json",
                             "trouve": 0, "attendu": 1})
    assert "/tmp/d.json" in texte
    assert "{trouve}" not in texte and "{attendu}" not in texte
    assert "0" in texte and "1" in texte


def test_rend_fr_choisit_la_forme_plurielle_du_tri_orphelin():
    """tri_orphelin est le seul des huit faits a porter un pluriel (n) --
    rend_fr doit choisir la forme one/other exactement comme t()
    (viewer.html, voir test_comptes_sortie_accorde_chaque_compte_independamment
    plus haut pour l'equivalent cote tables de pluriel)."""
    fait = {"fichier_cli": "a.json", "fichier_derive": "b.json"}
    un = langues.rend_fr({"code": "tri_orphelin", "n": 1, **fait})
    plusieurs = langues.rend_fr({"code": "tri_orphelin", "n": 3, **fait})
    assert "1 décision que" in un, un
    assert "décisions" not in un, un
    assert "3 décisions que" in plusieurs, plusieurs


def test_rend_fr_compose_le_conseil_de_renommage():
    """tri_orphelin_reprise : le conseil de renommage lui-meme -- celui que
    _texte_avertissement (viewer.py) n'a le droit d'ajouter que si
    reprise_possible (voir C2, tests/test_viewer.py)."""
    texte = langues.rend_fr({"code": "tri_orphelin_reprise",
                             "fichier_cli": "a.json", "fichier_derive": "b.json"})
    assert texte.startswith("Renommer a.json en b.json"), texte


def test_rend_fr_porte_le_detail_d_echec_tel_quel():
    """rechargement_impossible (taches.Moteur._enveloppe) : {detail} porte
    le message d'exception BRUT, jamais traduit -- du texte de diagnostic,
    pas de la copie d'interface (meme convention que {detail} dans
    tache_<genre>_echouee, viewer.html, afficheTache)."""
    texte = langues.rend_fr({"code": "rechargement_impossible",
                             "detail": "ValueError: x"})
    assert "ValueError: x" in texte


# Liste verifiee dans le code, pas recopiee du brief : quality.py (verdicts_*,
# supprime_ilots) produit no_lock/too_dark/motion_blur/flare/hors_source/ilot,
# et decisions.MOTIF_MANUEL vaut "manuel" -- le septieme, pose quand
# l'utilisateur a contredit l'algorithme. Ces sept codes, et statut_conserver
# / statut_ecarter, ne changent JAMAIS : decisions.py:97 et :171 les comparent
# a la chaine, et un fichier de decisions ecrit avant ce test doit rester
# lisible. Seul leur AFFICHAGE passe par une cle.
_VERDICTS = ("no_lock", "too_dark", "motion_blur", "niveau_aberrant",
             "flare", "hors_source",
             "ilot", "manuel")
_STATUTS = ("conserver", "ecarter")


def test_chaque_verdict_a_un_libelle_dans_les_deux_langues():
    for nom in langues.NOMS:
        table = langues.charge(nom)
        for code in _VERDICTS:
            assert f"verdict_{code}" in table, f"{nom} : verdict_{code}"
        for code in _STATUTS:
            assert f"statut_{code}" in table, f"{nom} : statut_{code}"


def test_l_anglais_ne_porte_pas_d_accents():
    """La table est de la DONNEE : c'est le seul endroit ou le francais
    prend ses accents. L'anglais, lui, n'a aucune raison d'en porter -- un
    accent dans en.json est presque toujours une entree oubliee en francais.

    La typographie (apostrophe courbe, cadratin, guillemets) reste permise :
    elle appartient a la langue, pas a l'encodage."""
    en = json.dumps(langues.charge("en"), ensure_ascii=False)
    fautifs = sorted({c for c in en if ord(c) >= 128 and c not in "’—«»…"})
    assert not fautifs, f"caracteres accentues dans en.json : {fautifs}"


_PAGE = os.path.join(_RACINE, "eclipse", "static", "viewer.html")


def _page():
    with open(_PAGE, encoding="utf-8") as f:
        return f.read()


def _cles_employees(source):
    """Les cles que la page demande : data-t, data-t-title, et t("...")."""
    trouvees = set(re.findall(r'data-t(?:-title)?="([^"]+)"', source))
    trouvees |= set(re.findall(r'\bt\(\s*"([^"]+)"', source))
    return trouvees


# Cles atteintes par un mecanisme que _cles_employees ne peut pas voir : ni un
# data-t litteral, ni un t("...") dont l'argument est une chaine litterale.
# Le nom vient du brief d'origine (qui n'envisageait que des cles produites
# par le SERVEUR) ; l'usage reel, verifie en lisant viewer.html, est plus
# large : toute cle rejointe par INDIRECTION, quelle que soit son origine,
# va ici -- sans quoi test_aucune_cle_orpheline la signalerait a tort comme
# du texte mort.
#
# Les trois cles ci-dessous existent depuis la tache 1 mais ne peuvent pas
# porter de data-t litteral : regleEtape (viewer.html) choisit le libelle du
# bouton dans LIBELLES_ETAPES selon que l'etape a deja tourne ou non, et un
# data-t ecrirait textContent par-dessus a chaque changement de langue,
# effacant justement ce choix -- meme piege que pour la bascule de theme,
# qui elle EMPLOIE deja sa cle (bouton_theme) via un t("bouton_theme")
# litteral dans son propre code, donc correctement detectee sans figurer
# ici. LIBELLES_ETAPES n'est pas encore cablee sur t() : ce sera la tache 3,
# qui retirera ces trois cles d'ici en les employant par indirection (ou les
# y laissera si son indirection reste, elle aussi, invisible au regex).
# Les taches 3, 4 et 6 ajouteront leurs propres cles a cet ensemble au fil de
# leurs propres indirections (etats serveur, boite de dialogue native, etc.).
#
# Tache 3 y ajoute quinze cles, toutes atteintes par une cle COMPOSEE avant
# l'appel a t() (jamais t("prefixe_" + variable), qui ferait capturer
# "prefixe_" par _cles_employees comme une fausse cle litterale -- voir
# regleEtape, libelleEtape et afficheTache dans viewer.html) :
#   - les trois libelles "refaire" de LIBELLES_ETAPES, indexes par
#     regleEtape exactement comme les trois cles ci-dessus ;
#   - etape_vignettes / etape_analyse / etape_rendu, composees par
#     libelleEtape("etape_" + nom) ;
#   - les neuf cles d'issue de tache (tache_<genre>_<etat>), composees par
#     afficheTache.
#
# Tache 4 y ajoute quatre cles : le libelle du ::after de chaque etat de
# bouton (voir la feuille de style, #actions button.*::after), pose par
# majZoneLancement via LIBELLES_ETAT[classe] -- une indirection par
# dictionnaire, jamais un t("etat_...") litteral.
#
# Tache 5 y ajoute les cles des FAITS que le serveur envoie desormais a la
# place de phrases (decisions.diagnostique, viewer._tri_orpheline,
# taches.Moteur._enveloppe) : la page les atteint par texteDuFait, qui
# appelle t(f.code, f) -- une indirection par la valeur de f.code, jamais un
# t("...") litteral. "tri_orphelin_reprise", elle, EST appelee litteralement
# par texteDuFait (t("tri_orphelin_reprise", f)) et "boite_indisponible"
# l'est par le repli de parcourt() (t("boite_indisponible", ...)) : les deux
# sont donc deja detectees par _cles_employees et n'auraient pas besoin de
# figurer ici, mais y rester ne fausse rien (une cle a la fois employee et
# listee ici reste simplement employee).
#
# Tache 6 y ajoute neuf cles : le verdict automatique (dessine(), le motif
# de vignette dans dessineBandeau()) et l'ecart manuel (dessine()) sont des
# codes de protocole fermes -- jamais litteraux, composes en "verdict_" +
# f.verdict et "statut_" + f.ecart_utilisateur -- exactement le meme piege
# que libelleEtape et afficheTache ci-dessus.
#
# Correction (revue de la tache 8) : une des sept, verdict_manuel, n'est en
# fait JAMAIS atteinte par cette indirection. f.verdict, cote page, ne vaut
# jamais "manuel" -- voir dessine() et dessineBandeau() dans viewer.html, ou
# f.verdict vient directement du resultat brut d'analyse_verdicts ; ce code
# est ecrit par decisions.applique, appelee uniquement hors-ligne par
# pipeline.render, jamais par le viewer. verdict_manuel reste neanmoins ici,
# pour une raison differente de l'indirection : sans elle,
# test_aucune_cle_orpheline la signalerait a tort comme du texte mort, alors
# qu'elle existe pour que test_chaque_verdict_a_un_libelle_dans_les_deux_langues
# couvre les sept codes de verdict par completude du modele de donnees --
# pas parce que la page l'affiche un jour.
#
# Tache 7 y ajoute trois cles : le titre de la boite native et ses deux
# libelles de filtre (boite_titre, boite_filtre_videos, boite_filtre_tous),
# lus par dialogue.choisit_video via langues.charge(langue) et affiches dans
# une fenetre du SYSTEME, jamais dans le DOM -- _cles_employees, qui ne lit
# que viewer.html, ne peut donc jamais les y trouver, meme par indirection.
#
# Vague de correction finale (C6) y ajoute cinq cles : signaleErreur
# (viewer.html) traduit desormais ELLE-MEME son argument -- une CLE, pas
# une chaine deja traduite -- pour pouvoir la retenir (derniereErreur) et la
# rejouer dans la nouvelle langue depuis rafraichitTextesDynamiques sans
# reinterroger le serveur. Les six appelants passent donc la cle nue
# ("erreur_serveur_injoignable", ...), jamais un t("...") litteral :
# invisible a _cles_employees, meme piege que pour LIBELLES_ETAPES ci-dessus.
_CLES_SERVEUR = {
    "bouton_vignettes", "bouton_analyse", "bouton_rendu",
    "bouton_vignettes_refaire", "bouton_analyse_refaire", "bouton_rendu_refaire",
    "etape_vignettes", "etape_analyse", "etape_rendu",
    "verdict_no_lock", "verdict_too_dark", "verdict_motion_blur", "verdict_flare",
    "verdict_niveau_aberrant",
    "verdict_hors_source", "verdict_ilot", "verdict_manuel",
    "statut_conserver", "statut_ecarter",
    "tache_vignettes_terminee", "tache_vignettes_echouee", "tache_vignettes_annulee",
    "tache_analyse_terminee", "tache_analyse_echouee", "tache_analyse_annulee",
    "tache_rendu_terminee", "tache_rendu_echouee", "tache_rendu_annulee",
    "etat_deja_fait", "etat_a_refaire", "etat_indisponible", "etat_en_cours",
    "fichier_illisible", "racine_invalide", "schema_incompatible",
    "autre_source", "ecarts_invalides", "tri_orphelin", "tri_orphelin_reprise",
    "rechargement_impossible",
    "boite_titre", "boite_filtre_videos", "boite_filtre_tous",
    "erreur_serveur_injoignable", "erreur_connexion_perdue",
    "erreur_decision_refusee", "erreur_filtre_vide", "erreur_tache_en_cours",
    "erreur_requete_refusee",
}


def test_toute_cle_employee_par_la_page_existe_dans_les_tables():
    connues = set(langues.charge("fr"))
    employees = _cles_employees(_page())
    assert employees, "aucune cle detectee : l'extraction est cassee"
    manquantes = employees - connues
    assert not manquantes, f"employees mais absentes des tables : {sorted(manquantes)}"


def test_aucune_cle_orpheline():
    """Une cle que plus personne n'emploie est du texte mort qu'on
    traduirait indefiniment."""
    employees = _cles_employees(_page())
    orphelines = set(langues.charge("fr")) - employees - _CLES_SERVEUR
    assert not orphelines, f"jamais employees : {sorted(orphelines)}"


def _ids_data_t(source):
    """Ids dont la BALISE porte un data-t="..." (texte, pas data-t-title)."""
    trouves = set()
    for m in re.finditer(r'<(\w+)\b([^>]*)>', source):
        attrs = m.group(2)
        if not re.search(r'(?<!-)data-t="', attrs):
            continue
        idm = re.search(r'\bid="([\w-]+)"', attrs)
        if idm:
            trouves.add(idm.group(1))
    return trouves


def _ids_reecrits_hors_data_t(source):
    """Ids dont le textContent ou le title sont ecrits par un AUTRE chemin
    que la boucle [data-t] d'appliqueLangue -- reference nommee
    (`const elXxx = document.getElementById("xxx")` puis `elXxx.textContent
    = ...`), ou une indirection d'un cran via un dictionnaire litteral de
    references nommees (`const BOUTONS = { k: elXxx }` puis `const b =
    BOUTONS[cle]; b.textContent = ...`, le motif de regleEtape).

    Connu et ACCEPTE comme angle mort : une indirection de plus d'un cran,
    un id compose dynamiquement, ou une reference stockee autrement qu'en
    tete de variable ne sont pas suivis. Voir le rapport de tache 2."""
    var_vers_id = {}
    for var, idv in re.findall(
            r'\b(?:const|let)\s+(\w+)\s*=\s*document\.getElementById\("([\w-]+)"\)',
            source):
        if var in ("el", "c"):   # noms generiques reutilises dans des IIFE
            continue             # distinctes : intracables sans vraie portee
        var_vers_id[var] = idv

    dict_ids = {}
    for nom, corps in re.findall(r'\bconst\s+(\w+)\s*=\s*\{([^}]*)\}', source, re.S):
        ids = {var_vers_id[r] for r in re.findall(r'\b(el\w+)\b', corps)
               if r in var_vers_id}
        if ids:
            dict_ids[nom] = ids
    for var, dictname in re.findall(r'\bconst\s+(\w+)\s*=\s*(\w+)\[', source):
        if dictname in dict_ids:
            dict_ids[var] = dict_ids[dictname]

    reecrits = set()
    for attr in ("textContent", "title"):
        for var, expr in re.findall(rf'(\w+)\.{attr}\s*=\s*([^;]*);', source):
            if var == "el" and "el.dataset" in expr:
                continue   # la boucle d'appliqueLangue elle-meme
            if var in var_vers_id:
                reecrits.add(var_vers_id[var])
            if var in dict_ids:
                reecrits |= dict_ids[var]
    return reecrits


def test_aucun_data_t_sur_un_noeud_reecrit_ailleurs():
    """Trois occurrences du meme piege ont ete trouvees a la main dans ce
    fichier (les boutons d'etape, la bascule de theme, #source-courante) :
    un element qui porte data-t="..." ET dont le texte est aussi ecrit par
    un AUTRE mecanisme JavaScript se fait ecraser par ce dernier a chaque
    chargement de langue, ou ecrase l'etat de ce dernier des qu'on change
    de langue -- selon lequel des deux tourne en second. Ce test rejoue la
    lecture qui les a trouves, pour qu'un quatrieme cas n'attende pas une
    nouvelle relecture manuelle.

    Angle mort assume (voir _ids_reecrits_hors_data_t) : une indirection de
    plus d'un cran ou un id compose dynamiquement echappent a l'analyse
    statique par regex menee ici. Une vraie analyse de portee JS lirait
    cela correctement ; ce test ne le fait pas, et ne pretend pas le faire."""
    source = _page()
    conflits = _ids_data_t(source) & _ids_reecrits_hors_data_t(source)
    assert not conflits, (
        f"data-t pose sur un noeud reecrit par ailleurs (voir afficheSource, "
        f"regleEtape, ou une IIFE de bascule) : {sorted(conflits)}")


_COMMENTAIRE_BLOC = re.compile(r"/\*.*?\*/", re.S)
_COMMENTAIRE_LIGNE = re.compile(r"//[^\n]*")


def _script_sans_commentaires(source):
    """Le texte des <script> de la page, commentaires JS retires.

    Necessaire pour chercher un IDENTIFIANT (t utilise comme variable) sans
    se faire piquer par un commentaire qui en PARLE : cette meme tache s'est
    fait prendre deux fois par exactement ce piege en ecrivant ses propres
    commentaires -- _cles_employees, elle, n'a pas ce probleme parce
    qu'elle ne cherche que des motifs qui SONT du code fonctionnel
    (data-t="...", t("...")), jamais un mot cle JS nu comme `let` ou
    `function`, bien plus probable dans une phrase en francais.

    Angle mort assume : un "//" a l'interieur d'une chaine ou d'un litteral
    d'expression reguliere serait pris pour un debut de commentaire, coupant
    la ligne trop tot. Verifie par lecture qu'aucun des deux ne se produit
    dans ce fichier a ce jour (aucune URL, et les deux seuls litteraux
    d'expression reguliere du fichier ne contiennent pas "//"). Une vraie
    tokenisation JS n'aurait pas ce defaut ; celle-ci n'y pretend pas."""
    scripts = re.findall(r"<script>(.*?)</script>", source, re.S)
    texte = "\n".join(scripts)
    texte = _COMMENTAIRE_BLOC.sub("", texte)
    texte = _COMMENTAIRE_LIGNE.sub("", texte)
    return texte


_PARAM_LISTS = re.compile(
    r"function\s+\w*\s*\(([^()]*)\)"    # function foo(...) / function(...)
    r"|\(([^()]*)\)\s*=>")              # (...) => ...
_PARAM_ARROW_NU = re.compile(r"(?<![\w$.])t\s*=>")   # t => ... (sans parentheses)
_VARIABLE_T = re.compile(r"\b(?:let|const|var)\s+t\b")


def _portees_qui_masquent_t(texte):
    """Parametres ou variables locales nommes exactement `t`, qui masquent
    la fonction de traduction pour toute la portee ou ils sont declares.

    Angle mort assume : une deconstruction (`{t}` ou `[t]`), un parametre
    renomme puis reaffecte a une variable `t` plus loin, ou un id compose
    echappent a cette recherche par regex -- meme classe de limite que
    _ids_reecrits_hors_data_t ci-dessus. Elle attrape la forme reelle,
    simple, qui s'est deja produite trois fois dans ce fichier : un
    parametre ou un `let`/`const`/`var` nomme `t` tout court."""
    fautifs = []
    for m in _PARAM_LISTS.finditer(texte):
        liste = m.group(1) if m.group(1) is not None else m.group(2)
        for brut in liste.split(","):
            nom = brut.strip().split("=")[0].strip()
            if nom == "t":
                fautifs.append(m.group(0).strip())
    if _PARAM_ARROW_NU.search(texte):
        fautifs.append("t => ...")
    fautifs.extend(m.group(0) for m in _VARIABLE_T.finditer(texte))
    return fautifs


def test_aucune_portee_ne_masque_la_fonction_de_traduction():
    """`t` designe partout la fonction de traduction declaree en tete du
    premier bloc <script>. Trois portees l'ont deja masquee avec un
    parametre ou une variable locale du meme nom -- afficheTache(t),
    suitLaTache (`let t` dans son sondage), demarre (`let t`) -- toutes les
    trois trouvees a la relecture plutot que par un garde-fou, un appel a
    t(...) dans une telle portee levant "t is not a function" au lieu de
    traduire. Ce test rejoue cette lecture pour qu'une quatrieme occurrence
    n'attende pas, elle non plus, une relecture manuelle.

    Angle mort assume : voir _portees_qui_masquent_t. Une garde par analyse
    de portee JS complete lirait cela correctement ; celle-ci, par regex sur
    le texte sans commentaires, ne le fait pas et ne pretend pas le faire."""
    texte = _script_sans_commentaires(_page())
    fautifs = _portees_qui_masquent_t(texte)
    assert not fautifs, f"portee(s) qui masquent la fonction t() : {fautifs}"


def test_aucun_texte_visible_ne_subsiste_dans_la_css():
    """Un content: litteral est hors de portee de toute traduction : la page
    peut changer de langue entierement, il resterait fige dans l'autre.
    Seul content: attr(...) (le libelle vient alors de l'attribut pose par
    majZoneLancement) et le decoratif content: "" (la puce carree de la case
    PNG, qui ne porte aucun mot) sont toleres."""
    restants = [m for m in re.findall(r'content:\s*"([^"]*)"', _page()) if m]
    assert not restants, f"texte litteral en CSS : {restants}"


_MOTS_OUTILS = (" le ", " la ", " les ", " des ", " une ", " est ", " pas ",
                " sur ", " dans ", " pour ", " aucun")


def test_aucune_phrase_francaise_ne_subsiste_dans_le_script():
    """Heuristique, et assumee comme telle : cherche les mots outils du
    francais dans les chaines litterales du JAVASCRIPT (les <script>...
    </script> seulement -- pas le reste de la page). Elle ne prouve pas
    l'absence, elle attrape l'oubli ordinaire -- qui est le cas frequent.

    Portee volontairement etroite, verifiee a la main plutot que devinee :
    - Restreinte au(x) <script> : le brief d'origine cherchait sur la page
      entiere, ce qui remonte des faux positifs a la pelle -- des
      commentaires CSS pleins de prose francaise ("/* ... */", cf. la
      legende .cle-ecart plus haut dans ce fichier), et les attributs
      title="..." des boutons/labels, qui sont le TEXTE DE REPLI HTML avant
      que appliqueLangue() ne l'ecrase au chargement (chaque title="..."
      litteral de ce fichier a son data-t-title jumeau ; voir bouton
      Parcourir, les trois filtres, le bouton theme, la legende d'ecart) --
      le meme motif que le texte des boutons eux-memes (ex. "Parcourir..."
      dans <button data-t="...">Parcourir...</button>), deja toleres par
      construction ailleurs dans ce fichier de tests. Une liste
      d'exceptions pour ces cas grandirait sans fin ; retrecir la fenetre
      de recherche au <script> les evite structurellement.
    - Commentaires JS retires via _script_sans_commentaires (meme fonction
      que test_aucune_portee_ne_masque_la_fonction_de_traduction) : sans
      cela, un commentaire narratif comme celui-ci se ferait attraper par
      son propre test, et pire, un "//" a l'interieur d'une chaine plus
      loin romprait la paire de guillemets et avalerait tout ce qui suit
      jusqu'au prochain guillemet, y compris a travers des commentaires.

    Ce que ce test n'attrape PAS, par construction : un template litteral
    interpole (`${...}`, exclu par le [^`$\\] de la regex des backticks --
    tous ceux de ce fichier le sont deja, ex. `/thumb/${n}.jpg`), une
    phrase francaise assemblee par concatenation plutot qu'ecrite d'un
    bloc, ou construite sans les mots outils de cette liste (ex. un verbe a
    l'imperatif seul). Une garde par vraie tokenisation JS n'aurait pas ces
    angles morts ; celle-ci, par regex, ne pretend pas les couvrir."""
    texte = _script_sans_commentaires(_page())
    chaines = re.findall(r'(?<![\w.])"([^"\\]{4,})"', texte)
    chaines += re.findall(r'`([^`$\\]{4,})`', texte)
    fautives = [c for c in chaines
                if any(m in f" {c.lower()} " for m in _MOTS_OUTILS)]
    assert not fautives, f"phrases francaises hors des tables : {fautives}"
