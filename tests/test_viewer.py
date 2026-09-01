import contextlib
import http.client
import json
import os
import re
import socket
import stat
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from eclipse import langues, viewer
from eclipse.decisions import charger, enregistrer
from eclipse.io import FrameWriter, probe
from eclipse.pipeline import _signature_source, analyze, main
from eclipse.taches import Moteur
from eclipse.verdicts import analyse_verdicts
from eclipse.viewer import (TAILLE_CORPS_MAX, Porteur, _reglages_reanalyse,
                            _tri_orpheline, chemins_derives, construit_etat,
                            fabrique_handler, nb_frames_estime, sert)
from eclipse.vignettes import _marqueur, chemin_vignette, compte, genere
from tests.synth import make_frame


def _cree_video(tmp_path, nom="src.mp4"):
    src = str(tmp_path / nom)
    with FrameWriter(src, width=120, height=200, fps=30.0) as w:
        for i in range(40):
            gain = 0.02 if 10 <= i < 15 else 0.8
            w.write(make_frame(w=120, h=200, center=(40.0 + i, 100.0),
                               r=25.0, gain=gain))
    return src


@pytest.fixture
def serveur(tmp_path):
    src = _cree_video(tmp_path)
    cache = str(tmp_path / "a.json")
    analyze(src, cache, scale=1.0)
    # construit_etat ne genere plus les vignettes : la fixture doit le faire.
    genere(src, str(tmp_path / "v"), _signature_source(src))
    porteur = Porteur(src, cache, str(tmp_path / "d.json"), str(tmp_path / "v"))
    # ThreadingHTTPServer, comme en production (voir eclipse.viewer.sert) :
    # un test qui utiliserait HTTPServer testerait un comportement different
    # de celui reellement servi.
    httpd = ThreadingHTTPServer(("127.0.0.1", 0),
                                fabrique_handler(porteur, Moteur()))
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}", porteur.etat, src
    finally:
        # Le demontage COMPLET, comme _serveur_pour : shutdown() rend la main
        # avant que le fil ne soit fini et ne ferme pas la socket d'ecoute.
        # Un fil survivant qui ecrit apres le teardown fait echouer le test
        # SUIVANT, innocent -- « l'echec le plus deroutant de ce depot »
        # (voir tests/conftest.py). Dans un try/finally, pour que ce soit
        # vrai aussi quand le test echoue en cours de route.
        httpd.shutdown()
        httpd.server_close()
        t.join(10.0)
        assert not t.is_alive()


def _get(url):
    with urllib.request.urlopen(url) as r:
        return r.status, r.read()


def _requete(methode, url, obj=None, origine=None):
    """Rend (code, corps). Un code d'erreur est une reponse, pas une panne."""
    corps = None if obj is None else json.dumps(obj).encode("utf-8")
    entetes = {"Content-Type": "application/json"}
    if origine is not None:
        entetes["Origin"] = origine
    req = urllib.request.Request(url, data=corps, method=methode,
                                 headers=entetes)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        # 400 / 409 sont des reponses attendues de ces routes : les laisser
        # remonter comme exceptions rendrait chaque test illisible.
        return e.code, e.read()


def _post(url, obj):
    return _requete("POST", url, obj)


def _get_code(url):
    """Rend (code, corps) sans lever sur un code d'erreur.

    _get laisse urlopen lever un HTTPError sur un 400 ou un 404 : les routes
    qui en rendent volontairement ont besoin de les lire comme des reponses.
    Meme role que _requete pour les POST, et le meme corps.
    """
    return _requete("GET", url)


@contextlib.contextmanager
def _serveur_pour(porteur, moteur=None):
    """Sert ce porteur le temps du bloc, sans laisser de fil derriere.

    Les tests de la tache 6 construisent leur porteur eux-memes (souvent
    SANS source, ce qu'aucune fixture ne fait) : ce gestionnaire evite d'en
    recopier le montage et surtout le demontage a chaque fois.
    """
    moteur = Moteur() if moteur is None else moteur
    httpd = ThreadingHTTPServer(("127.0.0.1", 0),
                                fabrique_handler(porteur, moteur))
    fil = threading.Thread(target=httpd.serve_forever, daemon=True)
    fil.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        moteur.annule()
        assert moteur.attend(delai=30.0)
        httpd.shutdown()
        httpd.server_close()
        fil.join(10.0)
        assert not fil.is_alive()


@pytest.fixture
def serveur_avec_moteur(tmp_path):
    src = _cree_video(tmp_path)
    cache = str(tmp_path / "a.json")
    analyze(src, cache, scale=1.0)
    genere(src, str(tmp_path / "v"), _signature_source(src))
    porteur = Porteur(src, cache, str(tmp_path / "d.json"), str(tmp_path / "v"))
    moteur = Moteur()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0),
                                fabrique_handler(porteur, moteur))
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}", moteur, src
    finally:
        # Aucun fil ne survit au test, meme s'il a echoue en cours de route.
        moteur.annule()
        assert moteur.attend(delai=30.0)
        httpd.shutdown()
        httpd.server_close()
        t.join(10.0)
        assert not t.is_alive()


def test_page_est_servie(serveur):
    base, _, _ = serveur
    statut, corps = _get(base + "/")
    assert statut == 200
    assert b"<html" in corps.lower()


# -- Tache 6 : l'interface du moteur de taches.

def test_la_page_porte_les_elements_du_moteur(serveur):
    url, _, _ = serveur
    _, corps = _get(f"{url}/")
    page = corps.decode("utf-8")
    for marqueur in ("/api/tache", "id=\"avancement\"", "id=\"barre\"",
                     "id=\"annuler\"", "id=\"lancer-rendu\""):
        assert marqueur in page, marqueur


def test_la_page_permet_de_relancer_une_etape_deja_faite(serveur):
    """Les boutons d'etape vivaient dans #manquant, cache des que tout est la.

    Une analyse une fois passee n'etait donc plus relancable depuis la page :
    il fallait tuer le serveur et repasser par la ligne de commande (constat
    d'usage). Ils vivent maintenant dans #actions, hors de #manquant, avec un
    libelle « Refaire ... » quand l'etape est faite.

    Le comportement, lui, est en JavaScript et hors de portee de pytest : ce
    test ne verifie que la structure dont il depend.
    """
    url, _, _ = serveur
    _, corps = _get(f"{url}/")
    page = corps.decode("utf-8")
    zone = page.split('<div id="actions">')[1].split("</div>")[0]
    for marqueur in ('id="lancer-vignettes"', 'id="lancer-analyse"',
                     'id="lancer-rendu"', 'id="rendu-png"'):
        assert marqueur in zone, marqueur
    # Et pas dans #manquant, qui ne porte plus que le constat : les y laisser
    # ramenerait exactement le defaut signale.
    manquant = page.split('<div id="manquant"')[1].split("</div>")[0]
    assert "lancer-" not in manquant
    # Le libelle "Refaire ..." est desormais table-driven (tache 3 du
    # chantier i18n) : il ne vit plus en clair dans la page, seulement dans
    # les tables de langue (voir tests/test_langues.py). Ce qui reste
    # verifiable ici, structurellement, est que regleEtape a bien de quoi
    # choisir ce second libelle par cle.
    assert "bouton_vignettes_refaire" in page and "bouton_analyse_refaire" in page


def test_api_frames_donne_une_entree_par_frame(serveur):
    base, etat, _ = serveur
    _, corps = _get(base + "/api/frames")
    d = json.loads(corps)
    assert d["pret"] is True
    assert len(d["frames"]) == 40
    premiere = d["frames"][0]
    for cle in ("n", "verdict", "ecart_utilisateur", "conf", "disk_p90"):
        assert cle in premiere


def test_api_frames_signale_les_rejets(serveur):
    base, _, _ = serveur
    _, corps = _get(base + "/api/frames")
    d = json.loads(corps)
    assert any(f["verdict"] is not None for f in d["frames"])


def test_vignette_est_servie(serveur):
    base, _, _ = serveur
    statut, corps = _get(base + "/thumb/0.jpg")
    assert statut == 200 and corps[:2] == b"\xff\xd8"      # entete JPEG


def test_vignette_absente_rend_404(serveur):
    base, _, _ = serveur
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(base + "/thumb/99999.jpg")
    assert e.value.code == 404


def test_decision_est_ecrite_sur_disque(serveur, tmp_path):
    base, etat, src = serveur
    corps = json.dumps({"n": 12, "statut": "conserver"}).encode()
    req = urllib.request.Request(base + "/api/decision", data=corps,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req) as r:
        assert r.status == 200
    from eclipse.decisions import charger
    assert charger(etat["decisions_path"],
                   _signature_source(src)) == {12: "conserver"}


def test_decision_rejoignant_l_algorithme_est_retiree(serveur, tmp_path):
    """Le fichier ne garde que de vrais desaccords."""
    base, etat, src = serveur
    from eclipse.decisions import charger

    def poste(n, statut):
        corps = json.dumps({"n": n, "statut": statut}).encode()
        req = urllib.request.Request(base + "/api/decision", data=corps,
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        urllib.request.urlopen(req).read()

    poste(12, "conserver")                     # 12 est rejetee : desaccord
    assert 12 in charger(etat["decisions_path"], _signature_source(src))
    poste(12, "ecarter")                       # on rejoint l'algorithme
    assert 12 not in charger(etat["decisions_path"], _signature_source(src))


def test_decision_avec_content_length_non_numerique_rend_400(serveur):
    """Un en-tete present mais non numerique doit repondre 400, pas lever."""
    base, _, _ = serveur
    hote_port = base[len("http://"):]
    hote, port = hote_port.split(":")
    conn = http.client.HTTPConnection(hote, int(port))
    try:
        corps = json.dumps({"n": 0, "statut": "conserver"}).encode()
        conn.putrequest("POST", "/api/decision")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", "abc")
        conn.endheaders()
        conn.send(corps)
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 400
    finally:
        conn.close()


def test_decision_corps_trop_grand_rend_400(serveur):
    """Cap sur la taille du corps : un client (ou un script errant) ne peut
    pas gonfler la memoire du serveur avec un POST arbitrairement grand."""
    base, _, _ = serveur
    corps = json.dumps({"n": 0, "statut": "conserver",
                        "rembourrage": "x" * (TAILLE_CORPS_MAX + 1)}).encode()
    req = urllib.request.Request(base + "/api/decision", data=corps,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code == 400


# -- Finding 1 : le viewer doit transmettre les memes parametres de tri que
# le rendu, sinon ce qu'il affiche en rouge n'est plus ce que le rendu ecarte.

def test_construit_etat_transmet_les_parametres_de_tri(tmp_path):
    src = _cree_video(tmp_path)
    cache = str(tmp_path / "a.json")
    donnees = analyze(src, cache, scale=1.0)
    from eclipse.io import probe
    info = probe(src)

    seuils = {"dark_rel": 0.9}
    # -1000.0 et non une petite valeur positive : sur cette fixture, le
    # disque n'approche jamais assez le bord de la source pour qu'une
    # tolerance de quelques pixels change quoi que ce soit (voir le
    # garde-fou ci-dessous). Une tolerance tres negative agrandit la marge
    # exigee (r = rayon_visible - tolerance) bien au-dela de toute position
    # du disque : chaque frame devient hors_source, ce qui rend la valeur
    # reellement discriminante — un construit_etat qui perdrait ce
    # parametre en silence ferait alors echouer l'egalite ci-dessous.
    tolerance_bord = -1000.0
    seuil_masque = 0.3

    attendu = analyse_verdicts(donnees, info["width"], info["height"], seuils,
                               tolerance_bord, seuil_masque)

    dossier = str(tmp_path / "v")
    # construit_etat ne genere plus les vignettes : ce test veut l'etat
    # "pret" (verdicts non vides), il doit donc les generer lui-meme.
    genere(src, dossier, _signature_source(src))
    etat = construit_etat(src, cache, str(tmp_path / "d.json"),
                          dossier, seuils=seuils,
                          tolerance_bord=tolerance_bord,
                          seuil_masque=seuil_masque)

    assert etat["verdicts"] == attendu["verdicts"]


def test_construit_etat_avec_parametres_par_defaut_differe_de_parametres_ajustes(tmp_path):
    """Garde-fou : verifie que le test ci-dessus exerce reellement des cas ou
    les parametres changent le resultat, sinon il pourrait passer meme si le
    viewer en ignorait un.

    Deux assertions, une par famille de parametres transmis :
    - conf_min plutot que dark_rel pour seuils : sur cette fixture, dark_rel
      ne change plus rien (les frames sombres sont deja rejetees par
      dark_abs quel que soit dark_rel) depuis que la butee de fenetre
      (taille, ecart_max) ne pese plus sur les verdicts. La frame 11, de
      confiance 0.23, bascule de too_dark a no_lock a partir de
      conf_min=0.3 : une difference reelle.
    - tolerance_bord=-1000.0 seul, pour la meme raison qu'expliquee dans le
      test ci-dessus : une petite tolerance ne change rien sur cette
      fixture (le disque n'approche jamais le bord), mais une tolerance tres
      negative rend toutes les frames hors_source.

    seuil_masque reste non discriminant sur cette fixture, mais pas parce que
    masse_captee y serait uniforme : elle est bimodale, ~0,989 sur 35 frames
    et 0,105 a 0,107 sur les frames 10-14 (celles a gain=0.02). L'ecart entre
    les deux modes est large : tout seuil dans ]0,107 ; 0,989] partitionne la
    fixture a l'identique, et 0.3 comme le defaut 0.80 tombent tous deux dans
    cet intervalle ; ecart residuel accepte, pas chasse plus loin."""
    src = _cree_video(tmp_path)
    cache = str(tmp_path / "a.json")
    donnees = analyze(src, cache, scale=1.0)
    from eclipse.io import probe
    info = probe(src)

    par_defaut = analyse_verdicts(donnees, info["width"], info["height"])
    ajuste = analyse_verdicts(donnees, info["width"], info["height"],
                              {"conf_min": 0.3}, 5.0, 0.3)
    assert par_defaut["verdicts"] != ajuste["verdicts"]

    ajuste_bord = analyse_verdicts(donnees, info["width"], info["height"],
                                   tolerance_bord=-1000.0)
    assert par_defaut["verdicts"] != ajuste_bord["verdicts"]


def test_construit_etat_transmet_seuil_masque_jusqu_a_analyse_verdicts(tmp_path):
    """Garde-fou dedie a seuil_masque : le test de transmission generique
    ci-dessus utilise 0.3, qui est non discriminant sur cette fixture (voir
    le garde-fou precedent) et ne prouverait donc rien si construit_etat
    perdait ce parametre en route.

    seuil_masque pese plus lourd que l'ancien seuil_conf : il decide quelles
    mesures entrent dans traj_x/traj_y, donc quelles frames sortent
    hors_source — pas seulement l'allure d'une trajectoire deja lissee. Le
    viewer et le rendu doivent calculer exactement les memes verdicts ; un
    hop qui perdrait ce parametre romprait cet invariant en silence.

    A 0.99, masse_captee (bimodale, voir plus haut) est sous le seuil sur
    TOUTES les frames : plus aucune mesure n'est valide.

    construit_etat now degrades instead of raising (see analyse_verdicts):
    mesures_valides drops to 0 and every verdict becomes no_lock. At the
    default threshold (0.80), 5 frames (10-14, masse_captee ~0.106) are
    rejected but the trajectory stays interpolable: 5 non-None verdicts,
    mesures_valides at full count."""
    src = _cree_video(tmp_path)
    cache = str(tmp_path / "a.json")
    analyze(src, cache, scale=1.0)
    signature = _signature_source(src)

    # construit_etat ne genere plus les vignettes : ce test veut l'etat
    # "pret" (verdicts non vides), il doit donc les generer lui-meme.
    dossier = str(tmp_path / "v")
    genere(src, dossier, signature)
    etat_defaut = construit_etat(src, cache, str(tmp_path / "d.json"),
                                 dossier)
    assert sum(1 for v in etat_defaut["verdicts"] if v is not None) == 5
    nb_frames = len(etat_defaut["verdicts"])
    assert etat_defaut["mesures_valides"] == nb_frames - 5

    dossier2 = str(tmp_path / "v2")
    genere(src, dossier2, signature)
    etat_degrade = construit_etat(src, cache, str(tmp_path / "d2.json"),
                                  dossier2, seuil_masque=0.99)
    assert etat_degrade["mesures_valides"] == 0
    assert all(v == "no_lock" for v in etat_degrade["verdicts"])


def test_frames_body_carries_the_valid_measure_count(tmp_path):
    src = _cree_video(tmp_path)
    cache = str(tmp_path / "a.json")
    analyze(src, cache, scale=1.0)
    genere(src, str(tmp_path / "v"), _signature_source(src))
    porteur = Porteur(src, cache, str(tmp_path / "d.json"), str(tmp_path / "v"))
    corps = viewer._corps_frames(porteur.etat)
    assert isinstance(corps["mesures_valides"], int)
    assert corps["mesures_valides"] > 0


# -- Finding 2 : le nombre de vignettes doit correspondre au cache d'analyse.

def test_construit_etat_avec_un_nombre_de_vignettes_incoherent_n_est_pas_pret(
        tmp_path):
    """Ce desaccord etait fatal ; il devient l'etat normal d'un amorcage.

    Pendant que l'analyse tourne sur une source dont les vignettes ne sont pas
    encore toutes ecrites, les deux comptes different pendant des minutes.
    """
    src = _cree_video(tmp_path)
    cache = str(tmp_path / "a.json")
    analyze(src, cache, scale=1.0)
    dossier = str(tmp_path / "v")
    genere(src, dossier, _signature_source(src))
    os.remove(chemin_vignette(dossier, 0))
    etat = construit_etat(src, cache, str(tmp_path / "d.json"), dossier)
    assert etat["pret"] is False
    assert etat["manque"] == ["vignettes"]


# -- Finding 3 : un fichier de decisions refuse doit se voir, pas disparaitre.

def test_construit_etat_signale_un_fichier_de_decisions_refuse(tmp_path, capsys):
    src = _cree_video(tmp_path)
    cache = str(tmp_path / "a.json")
    analyze(src, cache, scale=1.0)
    decisions = str(tmp_path / "d.json")
    dossier = str(tmp_path / "v")
    # Le diagnostic n'est emis que sur l'etat "pret" : ce test veut l'etat
    # pret, il doit donc generer les vignettes lui-meme (construit_etat ne
    # le fait plus).
    genere(src, dossier, _signature_source(src))
    # Enregistre pour une AUTRE source : signature differente, donc refuse.
    enregistrer(decisions, dict(_signature_source(src), size=999), {})
    construit_etat(src, cache, decisions, dossier)
    sortie = capsys.readouterr().out
    assert "ATTENTION" in sortie


def test_api_frames_signale_un_fichier_de_decisions_refuse(tmp_path):
    src = _cree_video(tmp_path)
    cache = str(tmp_path / "a.json")
    analyze(src, cache, scale=1.0)
    decisions = str(tmp_path / "d.json")
    dossier = str(tmp_path / "v")
    # Idem : ce test-ci veut la forme PRETE de la reponse, il doit donc
    # generer les vignettes lui-meme. L'avertissement porte desormais sur les
    # deux formes -- voir
    # test_revenir_a_la_source_de_la_ligne_de_commande_avertit, qui couvre la
    # forme non prete.
    genere(src, dossier, _signature_source(src))
    enregistrer(decisions, dict(_signature_source(src), size=999), {})
    etat = construit_etat(src, cache, decisions, dossier)
    from eclipse.viewer import _corps_frames
    corps = _corps_frames(etat)
    assert "avertissement" in corps


# -- Finding 4 : construit_etat observe au lieu de lever, pour permettre un
# amorcage depuis l'interface (cache absent, vignettes manquantes ou en
# desaccord de compte sont l'etat NORMAL d'un amorcage progressif).

def test_construit_etat_sans_cache_n_est_pas_pret(tmp_path):
    src = _cree_video(tmp_path)
    etat = construit_etat(src, str(tmp_path / "absent.json"),
                          str(tmp_path / "d.json"), str(tmp_path / "v"))
    assert etat["pret"] is False
    assert "analyse" in etat["manque"]
    assert "vignettes" in etat["manque"]
    assert etat["nb_frames_estime"] > 0


def test_construit_etat_sans_vignettes_n_est_pas_pret(tmp_path):
    src = _cree_video(tmp_path)
    cache = str(tmp_path / "a.json")
    analyze(src, cache, scale=1.0)
    etat = construit_etat(src, cache, str(tmp_path / "d.json"),
                          str(tmp_path / "v"))
    assert etat["pret"] is False
    assert etat["manque"] == ["vignettes"]


def test_construit_etat_ne_genere_plus_les_vignettes(tmp_path):
    """L'operation doit devenir une tache avec sa barre, pas un effet de bord.

    Tant qu'elle etait faite ici, ouvrir le viewer decodait les 2556 frames
    de la sequence reelle pendant que le navigateur attendait.
    """
    src = _cree_video(tmp_path)
    cache = str(tmp_path / "a.json")
    analyze(src, cache, scale=1.0)
    dossier = str(tmp_path / "v")
    construit_etat(src, cache, str(tmp_path / "d.json"), dossier)
    assert compte(dossier) == 0


def test_construit_etat_est_pret_quand_tout_est_la(tmp_path):
    src = _cree_video(tmp_path)
    cache = str(tmp_path / "a.json")
    analyze(src, cache, scale=1.0)
    dossier = str(tmp_path / "v")
    genere(src, dossier, _signature_source(src))
    etat = construit_etat(src, cache, str(tmp_path / "d.json"), dossier)
    assert etat["pret"] is True
    assert etat["manque"] == []


def test_api_frames_pas_prete_annonce_ce_qui_manque(tmp_path):
    src = _cree_video(tmp_path)
    porteur = Porteur(src, str(tmp_path / "absent.json"),
                      str(tmp_path / "d.json"), str(tmp_path / "v"))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0),
                                fabrique_handler(porteur, Moteur()))
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        _, corps = _get(f"http://127.0.0.1:{httpd.server_port}/api/frames")
        body = json.loads(corps)
        assert body["pret"] is False
        assert sorted(body["manque"]) == ["analyse", "vignettes"]
        assert "frames" not in body
    finally:
        httpd.shutdown()


def test_recharge_prend_en_compte_un_cache_apparu_apres_coup(tmp_path):
    src = _cree_video(tmp_path)
    cache = str(tmp_path / "a.json")
    dossier = str(tmp_path / "v")
    porteur = Porteur(src, cache, str(tmp_path / "d.json"), dossier)
    assert porteur.etat["pret"] is False
    analyze(src, cache, scale=1.0)
    genere(src, dossier, _signature_source(src))
    porteur.recharge()
    assert porteur.etat["pret"] is True


def test_nb_frames_estime_sur_la_fixture(tmp_path):
    """Valeur exacte, pas seulement > 0 : fige la formule sur laquelle la
    barre d'avancement (tache 6) construira son plafonnement. La fixture
    (40 frames a 30 fps) donne une duree probee de 1.33 s ; 1.33 * 30 = 39.9,
    qui arrondit a 40 — la coincidence avec le compte reel de frames n'a
    rien de garanti en general (voir le plafonnement documente sur la
    sequence reelle dans nb_frames_estime), mais elle est stable ici."""
    src = _cree_video(tmp_path)
    from eclipse.io import probe
    info = probe(src)
    assert nb_frames_estime(info) == 40


def test_construit_etat_avec_vignettes_d_une_autre_source_n_est_pas_pret(
        tmp_path):
    """compte() seul ne regarde que le nombre de fichiers, jamais leur
    provenance : sans a_jour(), un dossier de vignettes issu d'une AUTRE
    source (avec le meme nombre de frames par coincidence) passerait pour
    pret. L'utilisateur trierait alors silencieusement sur les mauvaises
    images, sans retour possible puisque l'interface lui dirait que rien ne
    manque."""
    src = _cree_video(tmp_path)
    cache = str(tmp_path / "a.json")
    analyze(src, cache, scale=1.0)
    dossier = str(tmp_path / "v")
    genere(src, dossier, _signature_source(src))
    # Le marqueur pointe maintenant vers une AUTRE source (signature
    # differente) ; les vignettes elles-memes restent celles de src, en
    # nombre concordant avec le cache (40 == 40).
    with open(_marqueur(dossier), "w", encoding="utf-8") as f:
        json.dump(dict(_signature_source(src), size=999), f)
    etat = construit_etat(src, cache, str(tmp_path / "d.json"), dossier)
    assert etat["pret"] is False
    assert etat["manque"] == ["vignettes"]


# -- Tache 5 : les trois routes du moteur de taches.

def test_post_tache_genre_inconnu_rend_400(serveur_avec_moteur):
    url, _, _ = serveur_avec_moteur
    code, _ = _post(f"{url}/api/tache", {"genre": "danse"})
    assert code == 400


def test_post_tache_rend_202_et_un_identifiant(serveur_avec_moteur):
    url, moteur, _ = serveur_avec_moteur
    code, corps = _post(f"{url}/api/tache", {"genre": "vignettes"})
    assert code == 202
    assert json.loads(corps)["id"] >= 1
    assert moteur.attend(delai=30.0)


def test_seconde_tache_rend_409(serveur_avec_moteur):
    url, moteur, _ = serveur_avec_moteur
    libere = threading.Event()
    moteur.lance("analyse", lambda: libere.wait(10.0))
    code, _ = _post(f"{url}/api/tache", {"genre": "vignettes"})
    assert code == 409
    libere.set()
    assert moteur.attend(delai=10.0)


def test_get_tache_avant_toute_tache_rend_un_genre_nul(serveur_avec_moteur):
    url, _, _ = serveur_avec_moteur
    _, corps = _get(f"{url}/api/tache")
    assert json.loads(corps)["genre"] is None


def test_get_tache_rend_la_derniere_tache_apres_sa_fin(serveur_avec_moteur):
    url, moteur, _ = serveur_avec_moteur
    moteur.lance("analyse", lambda: None)
    assert moteur.attend(delai=10.0)
    _, corps = _get(f"{url}/api/tache")
    e = json.loads(corps)
    assert (e["genre"], e["etat"]) == ("analyse", "terminee")


def test_delete_tache_annule(serveur_avec_moteur):
    url, moteur, _ = serveur_avec_moteur
    demarre = threading.Event()

    def travail():
        demarre.set()
        for i in range(1000):
            moteur.progression(i + 1, 1000)
            time.sleep(0.005)

    moteur.lance("analyse", travail)
    assert demarre.wait(5.0)
    code, _ = _requete("DELETE", f"{url}/api/tache")
    assert code == 202
    assert moteur.attend(delai=10.0)
    assert moteur.etat()["etat"] == "annulee"


def test_annulation_depuis_la_page_est_tracee_au_terminal(
        serveur_avec_moteur, capsys):
    """DELETE /api/tache levait le drapeau en silence.

    Le terminal est la ou l'operateur regarde tourner la passe : un rendu qui
    s'y arrete sans un mot ressemble a une panne. La demande d'annulation ET
    l'issue de la tache doivent s'y lire.
    """
    url, moteur, _ = serveur_avec_moteur
    demarre = threading.Event()

    def travail():
        demarre.set()
        for i in range(1000):
            moteur.progression(i + 1, 1000)
            time.sleep(0.005)

    moteur.lance("analyse", travail)
    assert demarre.wait(5.0)
    assert _requete("DELETE", f"{url}/api/tache")[0] == 202
    assert moteur.attend(delai=10.0)
    sortie = capsys.readouterr().out
    assert "Annulation de la tache analyse demandee depuis la page" in sortie
    assert "Tache analyse : annulee" in sortie


def test_annulation_sans_tache_le_dit_plutot_que_de_mentir(
        serveur_avec_moteur, capsys):
    url, _, _ = serveur_avec_moteur
    assert _requete("DELETE", f"{url}/api/tache")[0] == 202
    assert "aucune tache en cours" in capsys.readouterr().out


def test_rendu_refuse_d_ecraser_une_sortie_existante(serveur_avec_moteur,
                                                     tmp_path):
    url, _, src = serveur_avec_moteur
    sortie = os.path.splitext(src)[0] + "-clean.mp4"
    with open(sortie, "wb") as f:
        f.write(b"deja la")
    code, _ = _post(f"{url}/api/tache", {"genre": "rendu"})
    assert code == 409
    with open(sortie, "rb") as f:
        assert f.read() == b"deja la"


def test_rendu_avec_png_exporte_la_sequence(serveur_avec_moteur):
    url, moteur, src = serveur_avec_moteur
    code, _ = _post(f"{url}/api/tache", {"genre": "rendu", "png": True})
    assert code == 202
    assert moteur.attend(delai=60.0)
    assert moteur.etat()["etat"] == "terminee"
    dossier = os.path.splitext(src)[0] + "-frames"
    assert len([n for n in os.listdir(dossier) if n.endswith(".png")]) > 0
    # Le chemin heureux de la permutation : le rendu est LIVRE sous son nom
    # definitif, pas laisse sous le nom partiel ou il a ete ecrit. Sans cette
    # ligne, supprimer la mise en place laisserait toute la suite verte.
    assert os.path.isfile(os.path.splitext(src)[0] + "-clean.mp4")


def test_reanalyse_reprend_l_echelle_et_le_rayon_du_cache(tmp_path):
    """Le rayon du cache ne se retransmet pas tel quel.

    analyze() ecrit dans le cache un "radius" deja ramene A L'ECHELLE
    D'ANALYSE, alors que son PARAMETRE radius attend une pleine resolution
    qu'elle multipliera a son tour par lw/width. Renvoyer la valeur du cache
    brute la rapetisserait une seconde fois. Ce test pince le sens de la
    conversion : c'est le detail sur lequel la reprise se serait trompee en
    silence, sans rien casser de visible.
    """
    src = _cree_video(tmp_path)
    cache = str(tmp_path / "a.json")
    analyze(src, cache, scale=0.5)
    with open(cache, encoding="utf-8") as f:
        donnees = json.load(f)

    reglages = _reglages_reanalyse(src, cache, "custom")
    assert reglages["scale"] == 0.5
    # La formule d'analyze, rejouee : radius * (lw / width) doit redonner
    # exactement le rayon que le cache portait.
    largeur = probe(src)["width"]
    assert (reglages["radius"] * (donnees["width"] / largeur)
            == pytest.approx(donnees["radius"]))
    # Et la valeur brute NE conviendrait pas : sans cette ligne, une reprise
    # qui aurait oublie la conversion passerait le test ci-dessus par
    # coincidence si l'echelle valait 1.
    assert reglages["radius"] != pytest.approx(donnees["radius"])


def test_reanalyse_sans_cache_valide_ne_reprend_rien(tmp_path):
    """Pas de cache, pas de reprise : les defauts d'analyze s'appliquent."""
    src = _cree_video(tmp_path)
    assert _reglages_reanalyse(src, str(tmp_path / "absent.json"),
                               "custom") == {}


def test_reanalysis_settings_drop_the_radius_on_preset_change(tmp_path):
    src = _cree_video(tmp_path)
    cache = str(tmp_path / "a.json")
    analyze(src, cache, scale=1.0, preset="custom")
    memes = viewer._reglages_reanalyse(src, cache, "custom")
    autres = viewer._reglages_reanalyse(src, cache, "moon")
    assert "radius" in memes
    assert "radius" not in autres and autres.get("scale") == 1.0


def test_reanalysis_settings_carry_the_cache_light_threshold(tmp_path):
    """« Refaire l'analyse » must not shift masse_captee in silence.

    A cache analyzed with an explicit --seuil-lumiere carries it in
    analysis_params. Re-analyzing from the page under the SAME preset but
    at the preset's own cut would remeasure masse_captee elsewhere, moving
    the verdicts -- the very inversion _reglages_reanalyse exists to
    prevent for the analysis scale.
    """
    src = _cree_video(tmp_path)
    cache = str(tmp_path / "a.json")
    analyze(src, cache, scale=1.0, preset="custom", seuil_lumiere=0.42)
    reglages = viewer._reglages_reanalyse(src, cache, "custom")
    assert reglages["seuil_lumiere"] == 0.42


def test_relance_de_l_analyse_conserve_la_resolution_du_cache(
        serveur_avec_moteur, tmp_path):
    """« Refaire l'analyse » pouvait INVERSER le sens des decisions prises.

    Toutes les mesures dont analyse_verdicts derive ses verdicts sont prises
    a l'echelle d'analyse. Remesurer a une autre echelle deplace les
    verdicts ; et comme POST /api/decision n'enregistre un ecart que
    lorsqu'il DESACCORDE avec le verdict automatique, chaque frame dont le
    verdict bascule voit son ecart stocke vouloir dire l'inverse de ce que
    l'utilisateur avait enregistre. Rien ne l'arretait : charger_cache
    valide le schema et la signature de la source, pas la resolution.

    La fixture analyse a scale=1.0, qui n'est pas le defaut de la
    bibliotheque (0,5) : une relance qui ignorerait le cache le trahirait.
    """
    url, moteur, _ = serveur_avec_moteur
    cache = str(tmp_path / "a.json")
    with open(cache, encoding="utf-8") as f:
        avant = json.load(f)
    assert avant["scale"] == 1.0, "la fixture doit analyser hors du defaut"

    assert _post(f"{url}/api/tache", {"genre": "analyse"})[0] == 202
    assert moteur.attend(delai=60.0)
    assert moteur.etat()["etat"] == "terminee"

    with open(cache, encoding="utf-8") as f:
        apres = json.load(f)
    assert apres["scale"] == avant["scale"]
    assert (apres["width"], apres["height"]) == (avant["width"],
                                                 avant["height"])
    # Le rayon aussi : c'est lui qui attrape une conversion a l'envers, que
    # l'egalite des echelles ne verrait pas.
    assert apres["radius"] == pytest.approx(avant["radius"])


def test_analyse_sans_cache_prealable_reprend_le_defaut(serveur_avec_moteur,
                                                        tmp_path):
    """La reprise ne doit pas se transformer en obligation d'avoir un cache."""
    url, moteur, _ = serveur_avec_moteur
    cache = str(tmp_path / "a.json")
    os.remove(cache)
    assert _post(f"{url}/api/tache", {"genre": "analyse"})[0] == 202
    assert moteur.attend(delai=60.0)
    assert moteur.etat()["etat"] == "terminee"
    with open(cache, encoding="utf-8") as f:
        assert json.load(f)["scale"] == 0.5


def test_l_option_png_est_portee_par_l_instantane_de_la_tache(
        serveur_avec_moteur):
    """Un onglet ouvert en cours de rendu doit retrouver la case d'export.

    L'instantane du moteur est le seul etat partage entre onglets : sans
    cette cle, un onglet rattache affiche une case decochee alors que le
    rendu en cours exporte bien la sequence, et la case reste cliquable pour
    un lancement qui ne peut que se faire refuser.
    """
    url, moteur, _ = serveur_avec_moteur
    code, _ = _post(f"{url}/api/tache", {"genre": "rendu", "png": True})
    assert code == 202
    # Pendant la tache, ce que verrait un onglet qui se rattache.
    assert json.loads(_get(f"{url}/api/tache")[1])["options"] == {"png": True}
    assert moteur.attend(delai=60.0)
    # Et apres : un onglet ouvert juste apres le rendu retrouve le meme choix.
    assert json.loads(_get(f"{url}/api/tache")[1])["options"] == {"png": True}


def test_une_tache_sans_option_n_en_invente_pas(serveur_avec_moteur):
    url, moteur, _ = serveur_avec_moteur
    assert _post(f"{url}/api/tache", {"genre": "analyse"})[0] == 202
    assert moteur.attend(delai=60.0)
    assert json.loads(_get(f"{url}/api/tache")[1])["options"] is None


def test_le_rendu_rapporte_ses_comptes_a_la_page(serveur_avec_moteur):
    """La page ne disait que « rendu : termine. ».

    L'utilisateur qui ecarte des frames et retrouve la sequence PNG complete
    doit lire, la ou il regarde, que les coupes courtes ont ete comblees par
    interpolation. Le terminal le disait deja ; la page, non.

    Les noms sont ceux de _comptes_rendu et non ceux de render() : le
    "gardees" de render() vaut le TOTAL ecrit, interpolees comprises, et le
    reporter tel quel sous ce nom reconduirait la confusion meme.
    """
    url, moteur, _ = serveur_avec_moteur
    assert _post(f"{url}/api/tache", {"genre": "rendu"})[0] == 202
    assert moteur.attend(delai=60.0)
    e = json.loads(_get(f"{url}/api/tache")[1])
    assert e["etat"] == "terminee", e["message"]
    r = e["resultat"]
    assert set(r) == {"total", "ecrites", "interpolees", "ecartees"}
    # Le total est bien le total : c'est l'identite que la page explique.
    assert r["total"] == r["ecrites"] + r["interpolees"]
    # La fixture ecarte ses cinq frames sombres (voir _cree_video), sur 40.
    assert r["ecartees"] == 5
    assert r["ecrites"] == 40 - 5
    assert all(isinstance(v, int) for v in r.values())


def test_rendu_refuse_un_dossier_png_non_vide(serveur_avec_moteur):
    url, _, src = serveur_avec_moteur
    dossier = os.path.splitext(src)[0] + "-frames"
    os.makedirs(dossier, exist_ok=True)
    with open(os.path.join(dossier, "deja.png"), "wb") as f:
        f.write(b"x")
    code, _ = _post(f"{url}/api/tache", {"genre": "rendu", "png": True})
    assert code == 409
    # Un dossier vide, lui, ne bloque pas : c'est le cas normal d'un premier
    # export dont le dossier a ete cree a l'avance.
    os.remove(os.path.join(dossier, "deja.png"))
    code, _ = _post(f"{url}/api/tache", {"genre": "rendu", "png": True})
    assert code == 202


def _ralentit_progression(moteur, monkeypatch, pas=0.05):
    """Rend l'annulation en cours de rendu deterministe, toute plateforme.

    Sur une machine rapide (les executeurs Linux de la CI), le rendu de la
    petite video de fixture se terminait AVANT l'arrivee du DELETE : le test
    constatait 'terminee' au lieu d''annulee'. Le rappel de progression est
    le point d'annulation du moteur ; le ralentir garantit que la tache est
    encore en vol au moment de l'annulation, sans dependre de la vitesse de
    la machine. A poser AVANT le POST : _prepare capture moteur.progression
    au lancement de la tache.
    """
    reel = moteur.progression

    def lente(fait, total=None):
        time.sleep(pas)
        return reel(fait, total)

    monkeypatch.setattr(moteur, "progression", lente)


def test_rendu_annule_ne_laisse_pas_de_fichier_de_sortie(
        serveur_avec_moteur, monkeypatch):
    """Un mp4 tronque laisse en place serait pris pour un rendu valide."""
    url, moteur, src = serveur_avec_moteur
    sortie = os.path.splitext(src)[0] + "-clean.mp4"
    _ralentit_progression(moteur, monkeypatch)
    code, _ = _post(f"{url}/api/tache", {"genre": "rendu"})
    assert code == 202
    # Laisser le rendu demarrer pour de vrai avant d'annuler, sinon le test
    # verifierait qu'un rendu jamais commence ne laisse rien.
    for _ in range(200):
        if moteur.etat()["fait"] >= 1:
            break
        time.sleep(0.05)
    assert moteur.etat()["fait"] >= 1
    _requete("DELETE", f"{url}/api/tache")
    assert moteur.attend(delai=30.0)
    assert moteur.etat()["etat"] == "annulee"
    assert not os.path.isfile(sortie)


# -- Arbitrage de la collision T3/T5 : genere() leve Interrompue, que le
# moteur classerait "echouee" alors que l'utilisateur vient d'annuler. La
# traduction se fait dans _prepare ; sans ce test, elle est a une suppression
# de silencieusement regresser.

@pytest.mark.skipif(os.name != "nt",
                    reason="remplace ffmpeg par un script .bat, propre a Windows")
def test_vignettes_annulees_finissent_annulee_et_non_echouee(
        serveur_avec_moteur, tmp_path, monkeypatch):
    """Annuler des vignettes est une annulation, pas un echec.

    Le vrai ffmpeg est remplace par un script temoin qui boucle : sur cette
    video minuscule, un vrai ffmpeg finirait avant l'annulation et le test
    passerait pour la mauvaise raison. Meme technique que
    test_arret_leve_en_cours_interrompt_un_processus_vivant
    (tests/test_vignettes.py).
    """
    from eclipse import vignettes
    url, moteur, src = serveur_avec_moteur
    dossier = str(tmp_path / "v")
    # La fixture a deja genere les vignettes : sans retirer le marqueur,
    # genere() rendrait la main immediatement, sans rien a interrompre.
    os.remove(_marqueur(dossier))

    temoin = tmp_path / "demarre.txt"
    faux_ffmpeg = tmp_path / "faux_ffmpeg.bat"
    faux_ffmpeg.write_text(
        "@echo off\r\n"
        f'echo demarre> "{temoin}"\r\n'
        ":boucle\r\n"
        "goto boucle\r\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(vignettes, "ffmpeg_exe", lambda: str(faux_ffmpeg))

    code, _ = _post(f"{url}/api/tache", {"genre": "vignettes"})
    assert code == 202
    for _ in range(100):                       # jusqu'a 5 s, par pas de 50 ms
        if temoin.exists():
            break
        time.sleep(0.05)
    assert temoin.exists()                     # le faux ffmpeg tourne encore
    assert _requete("DELETE", f"{url}/api/tache")[0] == 202
    assert moteur.attend(delai=30.0)
    etat = moteur.etat()
    assert etat["etat"] == "annulee"
    assert etat["message"] is None


# -- Le rendu lance depuis le viewer doit trier comme le viewer affiche.
# Voir "Finding 1" plus haut : c'est le meme invariant, applique cette fois
# au rendu declenche par la page et non aux verdicts affiches.

# -- Stabilisation de la couleur : les reglages vivent dans le Porteur,
# entrent au descripteur via les reglages, sont exposes a la page par
# /api/frames et modifiables par POST /api/couleur.

def test_construit_etat_porte_la_couleur_par_defaut(tmp_path):
    """Les reglages de couleur entrent au descripteur du rendu : sans eux,
    un rendu fait avec la stabilisation ne se distinguerait pas d'un rendu
    fait sans, et le bandeau dirait « deja fait » d'un rendu a refaire."""
    src = _cree_video(tmp_path)
    cache = str(tmp_path / "a.json")
    analyze(src, cache, scale=1.0)
    etat = construit_etat(src, cache, str(tmp_path / "d.json"),
                          str(tmp_path / "v"))
    assert etat["reglages"]["couleur"] == {
        "actif": True, "fenetre": 31, "amplitude": 0.25}


def test_la_page_porte_les_controles_de_couleur(serveur):
    """La case, la section depliable et ses deux champs, et la route :
    l'oubli de l'un d'eux laisserait un reglage serveur sans aucun moyen
    d'etre vu ou change depuis la page."""
    url, _, _ = serveur
    _, corps = _get(f"{url}/")
    page = corps.decode("utf-8")
    for marqueur in ("/api/couleur", "id=\"couleur-actif\"",
                     "id=\"couleur-reglages\"", "id=\"couleur-fenetre\"",
                     "id=\"couleur-amplitude\""):
        assert marqueur in page, marqueur


def test_le_rendu_reprend_la_couleur_du_porteur(tmp_path, monkeypatch):
    """Miroir de test_le_rendu_reprend_les_reglages_du_porteur pour la
    couleur : sans transmission, render() reprendrait ses propres defauts
    et la page afficherait des reglages que le rendu n'applique pas."""
    from eclipse import pipeline
    from eclipse.viewer import _prepare

    src = _cree_video(tmp_path)
    cache = str(tmp_path / "a.json")
    analyze(src, cache, scale=1.0)
    dossier = str(tmp_path / "v")
    genere(src, dossier, _signature_source(src))
    porteur = Porteur(src, cache, str(tmp_path / "d.json"), dossier,
                      couleur=False, couleur_fenetre=15,
                      couleur_amplitude=0.1)

    recu = {}

    def faux_render(*a, **k):
        recu.update(a=a, k=k)
        with open(a[1], "wb") as f:
            f.write(b"rendu")

    monkeypatch.setattr(pipeline, "render", faux_render)
    travail, _, _ = _prepare(porteur, Moteur(), "rendu", False)
    travail()

    assert recu["k"]["couleur"] is False
    assert recu["k"]["couleur_fenetre"] == 15
    assert recu["k"]["couleur_amplitude"] == 0.1


def test_api_frames_expose_les_reglages_de_couleur(serveur):
    """La page seme ses controles depuis cette reponse : sans elle, la case
    et les champs afficheraient des valeurs inventees cote client."""
    url, _, _ = serveur
    _, corps = _get(url + "/api/frames")
    d = json.loads(corps)
    assert d["couleur"] == {"actif": True, "fenetre": 31, "amplitude": 0.25}


def test_post_couleur_change_les_reglages(serveur):
    url, _, _ = serveur
    code, corps = _post(url + "/api/couleur",
                        {"actif": False, "fenetre": 15, "amplitude": 0.1})
    assert code == 200
    assert json.loads(corps) == {"actif": False, "fenetre": 15,
                                 "amplitude": 0.1}
    _, corps = _get(url + "/api/frames")
    d = json.loads(corps)
    assert d["couleur"] == {"actif": False, "fenetre": 15, "amplitude": 0.1}


def test_post_couleur_entre_dans_les_reglages_du_descripteur(tmp_path):
    """Le changement doit atteindre etat['reglages'], pas seulement la
    reponse de l'API : c'est cette copie-la que le descripteur enregistre et
    que la comparaison « a refaire » lit."""
    src = _cree_video(tmp_path)
    cache = str(tmp_path / "a.json")
    analyze(src, cache, scale=1.0)
    genere(src, str(tmp_path / "v"), _signature_source(src))
    porteur = Porteur(src, cache, str(tmp_path / "d.json"),
                      str(tmp_path / "v"))
    with _serveur_pour(porteur) as url:
        code, _ = _post(url + "/api/couleur",
                        {"actif": True, "fenetre": 61, "amplitude": 0.5})
        assert code == 200
        assert porteur.etat["reglages"]["couleur"] == {
            "actif": True, "fenetre": 61, "amplitude": 0.5}


@pytest.mark.parametrize("corps", [
    {},                                                   # tout manque
    {"actif": True, "fenetre": 0, "amplitude": 0.1},      # fenetre nulle
    {"actif": True, "fenetre": -3, "amplitude": 0.1},     # fenetre negative
    {"actif": True, "fenetre": 2.5, "amplitude": 0.1},    # fenetre non entiere
    {"actif": True, "fenetre": 31, "amplitude": -0.1},    # amplitude negative
    {"actif": True, "fenetre": 31, "amplitude": 9.0},     # amplitude demesuree
    {"actif": "oui", "fenetre": 31, "amplitude": 0.1},    # actif non booleen
    {"actif": True, "fenetre": "31", "amplitude": 0.1},   # types JSON stricts
])
def test_post_couleur_invalide_rend_400(serveur, corps):
    url, _, _ = serveur
    code, _ = _post(url + "/api/couleur", corps)
    assert code == 400
    # Et rien n'a bouge : la page relirait sinon des reglages a moitie pris.
    _, reponse = _get(url + "/api/frames")
    assert json.loads(reponse)["couleur"] == {
        "actif": True, "fenetre": 31, "amplitude": 0.25}


def test_le_rendu_reprend_les_reglages_du_porteur(tmp_path, monkeypatch):
    """Sans transmission, render() reprend ses propres defauts.

    Deux consequences, toutes deux silencieuses : des seuils differents de
    ceux affiches en rouge, et surtout le fichier de decisions par defaut du
    repertoire courant (decisions.DECISIONS_DEFAUT_NOM) au lieu de celui que
    le viewer vient d'ecrire — la revue humaine serait purement ignoree.
    """
    from eclipse import pipeline
    from eclipse.viewer import _prepare

    src = _cree_video(tmp_path)
    cache = str(tmp_path / "a.json")
    analyze(src, cache, scale=1.0)
    dossier = str(tmp_path / "v")
    genere(src, dossier, _signature_source(src))
    decisions = str(tmp_path / "d.json")
    porteur = Porteur(src, cache, decisions, dossier,
                      seuils={"dark_rel": 0.9}, tolerance_bord=7.5,
                      seuil_masque=0.3)

    recu = {}

    def faux_render(*a, **k):
        recu.update(a=a, k=k)
        # Le vrai render() ecrit le fichier qu'on lui donne ; le faux doit en
        # faire autant, sinon le remplacement final n'a rien a remplacer.
        with open(a[1], "wb") as f:
            f.write(b"rendu")

    monkeypatch.setattr(pipeline, "render", faux_render)
    travail, _, _ = _prepare(porteur, Moteur(), "rendu", False)
    travail()

    assert recu["k"]["decisions_path"] == decisions
    assert recu["k"]["seuils"] == {"dark_rel": 0.9}
    assert recu["k"]["tolerance_bord"] == 7.5
    assert recu["k"]["seuil_masque"] == 0.3


# -- Revue de la tache 5, constatation Important 1 : le nettoyage d'echec ne
# doit detruire que ce que CE rendu a ecrit.

def test_rendu_qui_echoue_avant_d_ecrire_ne_detruit_pas_le_precedent(
        serveur_avec_moteur, tmp_path):
    """Scenario en plein dans le flux de la fonctionnalite.

    La page existe pour marquer des frames "ecarter". Toutes les marquer,
    puis relancer un rendu avec ecrasement, fait lever render() AVANT que le
    moindre ecrivain soit ouvert (« Toutes les frames ont ete rejetees »,
    pipeline.render). Un nettoyage en bloc supprimerait alors le rendu
    precedent, complet, sans le moindre avertissement.
    """
    url, moteur, src = serveur_avec_moteur
    sortie = os.path.splitext(src)[0] + "-clean.mp4"
    with open(sortie, "wb") as f:
        f.write(b"le rendu precedent, complet")
    enregistrer(str(tmp_path / "d.json"), _signature_source(src),
                {n: "ecarter" for n in range(40)})

    code, _ = _post(f"{url}/api/tache", {"genre": "rendu", "ecraser": True})
    assert code == 202
    assert moteur.attend(delai=60.0)
    assert moteur.etat()["etat"] == "echouee"
    # Le fichier n'a pas ete touche : ni supprime, ni tronque.
    with open(sortie, "rb") as f:
        assert f.read() == b"le rendu precedent, complet"


# -- Revue de la tache 5, constatation Important 2 : un reexport plus court
# ne doit pas laisser survivre les PNG de numero superieur du precedent.

def test_reexport_png_plus_court_ne_laisse_pas_de_png_du_precedent(
        serveur_avec_moteur, tmp_path):
    """Sinon un consommateur qui globe *.png melange deux executions.

    ffmpeg reecrit frame-00001.png et suivants avec -y, mais ne supprime
    rien : les numeros au-dela du nouveau compte survivent. Un second export
    plus court est le cas NORMAL, puisque la revue sert a ecarter davantage
    de frames.

    Les comptes sont deterministes sur cette fixture : au premier export,
    les frames 10 a 14 sont rejetees (too_dark) et la coupe de 5 depasse
    INTERP_MAX_DEFAUT=3, donc aucune frame interpolee ne s'ajoute. Au
    second, les frames 0 a 19 sont ecartees a la main : les gardees, 20 a
    39, sont contigues, donc la encore aucune interpolee.
    """
    url, moteur, src = serveur_avec_moteur
    dossier = os.path.splitext(src)[0] + "-frames"

    def png():
        return sorted(n for n in os.listdir(dossier) if n.endswith(".png"))

    code, _ = _post(f"{url}/api/tache", {"genre": "rendu", "png": True})
    assert code == 202
    assert moteur.attend(delai=60.0)
    assert moteur.etat()["etat"] == "terminee"
    premier = png()
    assert len(premier) == 35

    enregistrer(str(tmp_path / "d.json"), _signature_source(src),
                {n: "ecarter" for n in range(20)})
    code, _ = _post(f"{url}/api/tache",
                    {"genre": "rendu", "png": True, "ecraser": True})
    assert code == 202
    assert moteur.attend(delai=60.0)
    assert moteur.etat()["etat"] == "terminee"

    second = png()
    assert len(second) < len(premier)          # la revue a ecarte davantage
    # Exactement la nouvelle sequence, sans aucun survivant numerote plus
    # haut : c'est la seule facon de le verifier sans faire confiance au
    # compte.
    assert second == [f"frame-{i:05d}.png" for i in range(1, len(second) + 1)]


# -- Revue de la tache 5, ronde 2. Le rendu ecrit a cote puis remplace : le
# fichier precedent n'est touche qu'une fois le nouveau complet.

def test_rendu_annule_avec_ecraser_laisse_le_precedent_intact(
        serveur_avec_moteur, monkeypatch):
    """La propriete que ni le nettoyage en bloc ni l'empreinte ne donnaient.

    ffmpeg ouvre sa sortie avec -y et la tronque des le demarrage. Tant que
    le rendu ecrivait directement dans <source>-clean.mp4, une annulation en
    cours de route perdait le bon rendu de l'utilisateur : l'empreinte
    decidait seulement s'il fallait en plus supprimer la carcasse. En
    ecrivant a cote puis en remplacant, le fichier precedent survit par
    construction.
    """
    url, moteur, src = serveur_avec_moteur
    dossier = os.path.dirname(src)
    sortie = os.path.splitext(src)[0] + "-clean.mp4"
    contenu = b"le rendu precedent, complet"
    with open(sortie, "wb") as f:
        f.write(contenu)

    _ralentit_progression(moteur, monkeypatch)
    code, _ = _post(f"{url}/api/tache", {"genre": "rendu", "ecraser": True})
    assert code == 202
    # Attendre que le rendu ecrive pour de vrai : sinon le test verifierait
    # qu'un rendu jamais commence ne casse rien.
    for _ in range(200):
        if moteur.etat()["fait"] >= 1:
            break
        time.sleep(0.05)
    assert moteur.etat()["fait"] >= 1
    _requete("DELETE", f"{url}/api/tache")
    assert moteur.attend(delai=30.0)
    assert moteur.etat()["etat"] == "annulee"

    with open(sortie, "rb") as f:
        assert f.read() == contenu
    # Et aucune carcasse laissee a cote, quel que soit son nom.
    assert sorted(n for n in os.listdir(dossier) if n.endswith(".mp4")) == [
        "src-clean.mp4", "src.mp4"]


def test_rendu_refuse_pour_occupation_ne_touche_pas_l_export_png(
        serveur_avec_moteur):
    """Un 409 ne doit rien detruire : il n'a rien rendu en echange.

    Le vidage du dossier PNG se fait dans le fil de la tache. Fait au moment
    du _prepare, il tournait dans le fil HTTP, avant meme que moteur.lance()
    ait pu refuser la tache pour occupation.
    """
    url, moteur, src = serveur_avec_moteur
    dossier = os.path.splitext(src)[0] + "-frames"
    os.makedirs(dossier)
    temoin = os.path.join(dossier, "frame-00001.png")
    with open(temoin, "wb") as f:
        f.write(b"export precedent")

    libere = threading.Event()
    moteur.lance("analyse", lambda: libere.wait(10.0))
    code, _ = _post(f"{url}/api/tache",
                    {"genre": "rendu", "png": True, "ecraser": True})
    assert code == 409
    with open(temoin, "rb") as f:
        assert f.read() == b"export precedent"
    libere.set()
    assert moteur.attend(delai=10.0)


def test_reexport_png_annule_laisse_l_export_precedent_intact(
        serveur_avec_moteur, monkeypatch):
    """Symetrique du test ci-dessus, cote sequence PNG.

    ffmpeg ecrit les PNG au fur et a mesure dans le dossier qu'on lui donne.
    Tant que c'etait le dossier livre, il fallait le vider avant de
    commencer, et une annulation laissait l'utilisateur sans l'export
    precedent ni le nouveau. L'export se fait donc dans un dossier partiel,
    permute a la fin.
    """
    url, moteur, src = serveur_avec_moteur
    dossier = os.path.splitext(src)[0] + "-frames"
    os.makedirs(dossier)
    temoin = os.path.join(dossier, "frame-00001.png")
    with open(temoin, "wb") as f:
        f.write(b"export precedent")

    _ralentit_progression(moteur, monkeypatch)
    code, _ = _post(f"{url}/api/tache",
                    {"genre": "rendu", "png": True, "ecraser": True})
    assert code == 202
    for _ in range(200):
        if moteur.etat()["fait"] >= 1:
            break
        time.sleep(0.05)
    assert moteur.etat()["fait"] >= 1      # l'export a vraiment commence
    _requete("DELETE", f"{url}/api/tache")
    assert moteur.attend(delai=30.0)
    assert moteur.etat()["etat"] == "annulee"

    # L'export precedent est intact : ni vide, ni panache de nouvelles
    # frames.
    assert sorted(os.listdir(dossier)) == ["frame-00001.png"]
    with open(temoin, "rb") as f:
        assert f.read() == b"export precedent"
    # Et le doublon partiel ne reste pas la : sur la sequence reelle, ce sont
    # 8 a 13 Go qui restaient sans etre nommes nulle part, jusqu'au prochain
    # lancement. Le doublement de l'export etait accepte PENDANT le rendu,
    # pas apres.
    assert not os.path.isdir(dossier + "-partiel")


@pytest.mark.skipif(os.name != "nt",
                    reason="compte sur chmod S_IREAD pour faire echouer "
                           "os.replace, ce que POSIX ne fait pas")
def test_remplacement_impossible_ne_detruit_pas_le_rendu_termine(
        serveur_avec_moteur):
    """Un rendu COMPLET ne doit jamais partir avec un echec de livraison.

    La mise en place peut echouer pour de vraies raisons sous Windows : ici
    une cible en lecture seule, ailleurs un lecteur qui tient le fichier
    ouvert, ou une poignee d'antivirus. Le fichier fraichement encode
    represente des minutes a des heures de travail : la tache doit finir
    "echouee" en le laissant sur le disque, recuperable a la main, et sans
    toucher au rendu precedent non plus.
    """
    url, moteur, src = serveur_avec_moteur
    dossier = os.path.dirname(src)
    sortie = os.path.splitext(src)[0] + "-clean.mp4"
    with open(sortie, "wb") as f:
        f.write(b"le rendu precedent, complet")
    os.chmod(sortie, stat.S_IREAD)
    try:
        code, _ = _post(f"{url}/api/tache",
                        {"genre": "rendu", "ecraser": True})
        assert code == 202
        assert moteur.attend(delai=60.0)
        assert moteur.etat()["etat"] == "echouee"

        # Le rendu termine est toujours la, sous le nom ou il a ete ecrit.
        restes = [n for n in os.listdir(dossier) if n.endswith(".mp4")
                  and n not in ("src.mp4", "src-clean.mp4")]
        assert len(restes) == 1
        assert os.path.getsize(os.path.join(dossier, restes[0])) > 0
        # Et le precedent n'a pas ete touche.
        with open(sortie, "rb") as f:
            assert f.read() == b"le rendu precedent, complet"
    finally:
        # Sans quoi tmp_path n'est pas nettoyable.
        os.chmod(sortie, stat.S_IWRITE)


# -- Revue finale, point A : la tache « vignettes » ne doit pas etre un
# cul-de-sac. construit_etat compte les .jpg ; genere() consulte le marqueur.
# Quand les deux se contredisent, seul le forcage sort de l'impasse.

def test_vignettes_perimees_mais_marquees_sont_regenerees(serveur_avec_moteur,
                                                          tmp_path):
    """Des .jpg manquants sous un marqueur a jour : l'etat exact du test
    test_construit_etat_avec_un_nombre_de_vignettes_incoherent_n_est_pas_pret,
    dont personne ne verifiait qu'on pouvait en sortir.

    construit_etat met "vignettes" dans manque des que leur nombre differe de
    celui du cache, marqueur ou non ; genere() rendait la main immediatement
    parce que le marqueur, lui, etait a jour. La tache finissait "terminee"
    sans rien avoir fait, le rechargement reproduisait le meme etat, et le
    meme bouton reapparaissait : l'utilisateur pouvait cliquer indefiniment
    sans aucun diagnostic nulle part.
    """
    url, moteur, src = serveur_avec_moteur
    dossier = str(tmp_path / "v")
    os.remove(chemin_vignette(dossier, 0))
    assert compte(dossier) == 39
    # Le marqueur est intact : c'est toute la difficulte du cas.
    assert os.path.isfile(_marqueur(dossier))

    code, _ = _post(f"{url}/api/tache", {"genre": "vignettes"})
    assert code == 202
    assert moteur.attend(delai=60.0)
    assert moteur.etat()["etat"] == "terminee"

    # Regenerees pour de vrai, et l'etat recharge est PRET : sans cela, la
    # page reafficherait le meme panneau avec le meme bouton.
    assert compte(dossier) == 40
    _, corps = _get(f"{url}/api/frames")
    assert json.loads(corps)["pret"] is True


# -- Revue finale, point F : recuperer une permutation interrompue AVANT tout
# controle de vacuite, sans quoi le lancement suivant detruit l'export neuf.

def test_permutation_png_interrompue_est_recuperee_et_non_perdue(
        serveur_avec_moteur):
    """L'etat que laisse un arret brutal entre les deux renommages.

    _permute_dossier ecarte <cible> en <cible>-ancien, puis renomme le neuf en
    <cible>. Tue entre les deux, le disque garde l'export complet sous -ancien
    et plus rien a la cible. Le controle de vacuite ne voyait alors rien,
    acceptait donc la requete sans demander de consentement, et le rendu qui
    suivait detruisait cet export : _vide_les_png d'abord, le rmtree du reste
    -ancien ensuite. Garder seulement ce rmtree aurait sauve la plus ancienne
    des deux copies alors que la plus recente etait deja perdue.
    """
    url, moteur, src = serveur_avec_moteur
    frames = os.path.splitext(src)[0] + "-frames"
    ancien = frames + "-ancien"
    os.makedirs(ancien)
    contenu = b"l'export complet, laisse a mi-permutation"
    with open(os.path.join(ancien, "frame-00001.png"), "wb") as f:
        f.write(contenu)

    code, _ = _post(f"{url}/api/tache", {"genre": "rendu", "png": True})
    # Remis a sa place, il redevient une sortie livree : c'est ce refus-la
    # qu'on attend, pas une acceptation silencieuse.
    assert code == 409
    assert moteur.etat()["genre"] is None      # aucune tache n'a demarre
    assert not os.path.isdir(ancien)
    with open(os.path.join(frames, "frame-00001.png"), "rb") as f:
        assert f.read() == contenu


# -- Revue finale, point E : le sous-parseur viewer accepte sept options de
# cadrage que le rendu lance depuis la page ignorait.

def test_le_cadrage_du_viewer_atteint_render(tmp_path, monkeypatch):
    """« viewer src.mp4 --taille 900x1600 » puis Lancer le rendu.

    Avant, la page ne faisait qu'afficher et ces options etaient sans
    consequence ; le bouton de rendu en a fait un defaut de la meme classe que
    celui deja protege pour les seuils — un rendu qui ne correspond pas a ce
    que l'utilisateur a demande, sans un mot. Le test suit la chaine entiere,
    sert() -> Porteur -> _travail_rendu -> render, parce que c'est un maillon
    manquant au milieu qui produisait le silence.
    """
    from eclipse import pipeline, viewer
    from eclipse.viewer import _prepare

    src = _cree_video(tmp_path)
    cache = str(tmp_path / "a.json")
    analyze(src, cache, scale=1.0)
    dossier = str(tmp_path / "v")
    genere(src, dossier, _signature_source(src))

    captures = {}

    class PorteurEspion(viewer.Porteur):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            captures["porteur"] = self

    class FauxServeur:
        """Aucune socket ouverte, et serve_forever rend la main aussitot."""

        server_port = 0

        def __init__(self, adresse, handler):
            pass

        def serve_forever(self):
            raise KeyboardInterrupt

    monkeypatch.setattr(viewer, "Porteur", PorteurEspion)
    monkeypatch.setattr(viewer, "ThreadingHTTPServer", FauxServeur)
    viewer.sert(src, cache, str(tmp_path / "d.json"), dossier, ouvrir=False,
                moteur=Moteur(), taille=(100, 160), taille_sortie=(100, 160),
                interp_max=7, interp_deplacement_max=12.5,
                depassement_butee=42.0)
    porteur = captures["porteur"]

    recu = {}

    def faux_render(*a, **k):
        recu.clear()
        recu.update(k)
        # Le vrai render() ecrit ce qu'on lui donne ; le faux doit en faire
        # autant, sinon la livraison n'a rien a livrer.
        with open(a[1], "wb") as f:
            f.write(b"rendu")
        if k.get("frames_dir"):
            os.makedirs(k["frames_dir"], exist_ok=True)
            with open(os.path.join(k["frames_dir"], "frame-00001.png"),
                      "wb") as f:
                f.write(b"png")

    monkeypatch.setattr(pipeline, "render", faux_render)
    travail, _, _ = _prepare(porteur, Moteur(), "rendu", False)
    travail()

    assert recu["taille"] == (100, 160)
    assert recu["taille_sortie"] == (100, 160)
    assert recu["interp_max"] == 7
    assert recu["interp_deplacement_max"] == 12.5
    assert recu["depassement_butee"] == 42.0

    # Le septieme, --frames-dir, est justement celui que le viewer REFUSE :
    # la livraison permute le dossier en entier, donc le diriger vers un
    # dossier de l'utilisateur en detruirait tout le contenu. L'export part
    # donc toujours a cote de la source.
    travail, _, _ = _prepare(porteur, Moteur(), "rendu", True, png=True)
    travail()
    attendu = os.path.splitext(src)[0] + "-frames"
    assert recu["frames_dir"] == attendu + "-partiel"
    assert os.path.isfile(os.path.join(attendu, "frame-00001.png"))


def test_dossier_png_contenant_la_source_est_refuse(tmp_path):
    """La contrainte permanente du projet, sur le chemin que --frames-dir ouvre.

    Le rendu lance depuis la page PERMUTE le dossier d'export : il l'ecarte,
    met le neuf a sa place, puis supprime l'ancien. Vise sur le dossier qui
    contient la video, cette sequence detruirait la source. En ligne de
    commande le danger n'existe pas : render() se contente d'y ecrire.
    """
    from eclipse.viewer import _prepare

    src = _cree_video(tmp_path)
    cache = str(tmp_path / "a.json")
    analyze(src, cache, scale=1.0)
    dossier = str(tmp_path / "v")
    genere(src, dossier, _signature_source(src))
    # Le dossier n'est plus atteignable depuis la ligne de commande (le
    # viewer refuse --frames-dir), mais la garde reste l'invariant qui autorise
    # _permute_dossier a remplacer un dossier en entier : on l'exerce
    # directement.
    from eclipse.viewer import _verifie_dossier_png

    Porteur(src, cache, str(tmp_path / "d.json"), dossier)
    with pytest.raises(ValueError, match="contient la source"):
        _verifie_dossier_png(str(tmp_path), src)
    assert os.path.isfile(src)


# -- Revue finale, point B : os.replace livrait sans verifier que le rendu
# partiel etait valide. FrameWriter.close() ne lit jamais le code de retour de
# ffmpeg (voir io.py) : un encodage rate rend la main sans bruit.

def test_rendu_partiel_vide_n_est_pas_livre(tmp_path, monkeypatch):
    """Un fichier vide ne doit pas prendre la place du bon rendu.

    Declencheur etroit, perte totale : la tache rapportait "terminee" apres
    avoir deplace un fichier de zero octet par-dessus des minutes d'encodage.
    """
    from eclipse import pipeline
    from eclipse.viewer import _prepare

    src = _cree_video(tmp_path)
    cache = str(tmp_path / "a.json")
    analyze(src, cache, scale=1.0)
    dossier = str(tmp_path / "v")
    genere(src, dossier, _signature_source(src))
    sortie = os.path.splitext(src)[0] + "-clean.mp4"
    contenu = b"le rendu precedent, complet"
    with open(sortie, "wb") as f:
        f.write(contenu)

    def faux_render(*a, **k):
        # Exactement ce que fait ffmpeg quand il sort en erreur a la
        # finalisation : le fichier existe et ne contient rien.
        with open(a[1], "wb"):
            pass

    monkeypatch.setattr(pipeline, "render", faux_render)
    porteur = Porteur(src, cache, str(tmp_path / "d.json"), dossier)
    travail, _, _ = _prepare(porteur, Moteur(), "rendu", True)
    with pytest.raises(RuntimeError, match="absent ou vide"):
        travail()

    with open(sortie, "rb") as f:
        assert f.read() == contenu


# -- Revue finale, point D : une permutation ratee laissait un rendu complet
# sans dire ou. Le message du moteur ne nommait que l'exception.

@pytest.mark.skipif(os.name != "nt",
                    reason="compte sur chmod S_IREAD pour faire echouer "
                           "os.replace, ce que POSIX ne fait pas")
def test_echec_de_permutation_nomme_les_artefacts_de_recuperation(
        tmp_path, monkeypatch):
    """Sans le nom du -partiel, le lancement suivant le detruit en silence.

    C'est ce qui rendait le report inconfortable : l'utilisateur n'avait aucun
    moyen d'apprendre que son rendu complet existait. La sortie en lecture
    seule fait echouer os.replace pour de vrai, comme dans
    test_remplacement_impossible_ne_detruit_pas_le_rendu_termine.
    """
    from eclipse import pipeline
    from eclipse.viewer import _prepare

    src = _cree_video(tmp_path)
    cache = str(tmp_path / "a.json")
    analyze(src, cache, scale=1.0)
    dossier = str(tmp_path / "v")
    genere(src, dossier, _signature_source(src))
    partiel = os.path.splitext(src)[0] + "-clean-partiel.mp4"
    frames_partiel = os.path.splitext(src)[0] + "-frames-partiel"
    sortie = os.path.splitext(src)[0] + "-clean.mp4"
    with open(sortie, "wb") as f:
        f.write(b"le rendu precedent, complet")
    os.chmod(sortie, stat.S_IREAD)

    def faux_render(*a, **k):
        with open(a[1], "wb") as f:
            f.write(b"un rendu complet, douze minutes d'encodage")
        os.makedirs(k["frames_dir"], exist_ok=True)
        with open(os.path.join(k["frames_dir"], "frame-00001.png"), "wb") as f:
            f.write(b"png")

    monkeypatch.setattr(pipeline, "render", faux_render)
    porteur = Porteur(src, cache, str(tmp_path / "d.json"), dossier)
    travail, _, _ = _prepare(porteur, Moteur(), "rendu", True, png=True)
    try:
        with pytest.raises(RuntimeError) as capte:
            travail()

        message = str(capte.value)
        assert partiel in message
        assert frames_partiel in message
        assert "Error" in message              # la cause n'est pas perdue
        # Et les artefacts nommes sont bien la, sur le disque.
        assert os.path.getsize(partiel) > 0
        assert os.path.isfile(os.path.join(frames_partiel, "frame-00001.png"))
    finally:
        # Sans quoi tmp_path n'est pas nettoyable.
        os.chmod(sortie, stat.S_IWRITE)


def test_viewer_refuse_frames_dir(tmp_path, capsys):
    """--frames-dir dirigerait la permutation vers un dossier de l'utilisateur.

    La livraison de la sequence PNG remplace le dossier cible EN ENTIER, y
    compris son contenu non-PNG : accepter l'option ferait de
    `viewer src.mp4 --frames-dir C:/Photos/export` une commande destructrice
    sous un simple « ecraser ? ». Le rendu lance depuis la page exporte donc
    toujours a cote de la source, et qui veut un autre dossier passe par la
    commande render.
    """
    src = _cree_video(tmp_path)
    cible = tmp_path / "mes-photos"
    cible.mkdir()
    temoin = cible / "sans-rapport.txt"
    temoin.write_bytes(b"a moi")

    code = main(["viewer", src, "--cache", str(tmp_path / "a.json"),
                 "--frames-dir", str(cible)])
    assert code != 0

    # Le refus est un refus : rien n'a ete touche, et le serveur n'a pas
    # demarre — un refus qui aurait quand meme servi la page laisserait
    # l'utilisateur cliquer sur un bouton de rendu deja condamne.
    assert temoin.read_bytes() == b"a moi"
    assert list(cible.iterdir()) == [temoin]

    # Le message porte les trois informations dont l'utilisateur a besoin, et
    # rien d'autre : ce qui est refuse, ou l'export atterrit a la place, et
    # quoi faire pour choisir un autre dossier. L'explication du danger
    # (permutation du dossier en entier) vit dans le commentaire du code, pas
    # sous les yeux de l'utilisateur — d'ou la derniere assertion.
    err = capsys.readouterr().err
    assert "--frames-dir" in err
    assert "<source>-frames" in err
    assert "render" in err
    assert "detruirait" not in err


# -- Tache 4 : la garde d'origine sur les routes qui agissent.

def test_post_avec_une_origine_etrangere_rend_403(serveur_avec_moteur):
    url, _, _ = serveur_avec_moteur
    code, _ = _requete("POST", f"{url}/api/tache", {"genre": "vignettes"},
                       origine="http://exemple.invalide")
    assert code == 403


def test_delete_avec_une_origine_etrangere_rend_403(serveur_avec_moteur):
    url, _, _ = serveur_avec_moteur
    code, _ = _requete("DELETE", f"{url}/api/tache",
                       origine="http://exemple.invalide")
    assert code == 403


def test_post_avec_l_origine_du_viewer_est_accepte(serveur_avec_moteur):
    url, moteur, _ = serveur_avec_moteur
    code, _ = _requete("POST", f"{url}/api/tache", {"genre": "vignettes"},
                       origine=url)
    assert code == 202
    assert moteur.attend(delai=30.0)


def test_post_sans_origine_est_accepte(serveur_avec_moteur):
    """Un client sans navigateur — curl, un test — n'en envoie pas.

    Refuser l'absence casserait ces usages sans rien proteger : le danger
    vient d'une page tierce, et une page tierce en envoie toujours un.
    """
    url, moteur, _ = serveur_avec_moteur
    code, _ = _post(f"{url}/api/tache", {"genre": "vignettes"})
    assert code == 202
    assert moteur.attend(delai=30.0)


def test_get_ne_controle_pas_l_origine(serveur_avec_moteur):
    """Les GET ne font que LIRE : la page, l'etat, les vignettes.

    Plus d'exception depuis que l'exploration du systeme de fichiers est
    partie avec l'explorateur web : la route qui la remplace, /api/parcourir,
    est un POST et herite de la garde par sa methode (voir
    test_parcourir_avec_une_origine_etrangere_rend_403)."""
    url, _, _ = serveur_avec_moteur
    code, _ = _requete("GET", f"{url}/api/tache",
                       origine="http://exemple.invalide")
    assert code == 200


# -- Tache 5 du bandeau d'etapes : le descripteur livre avec le rendu, et les
# trois etats exposes par /api/frames.

def test_le_rendu_ecrit_son_descripteur(serveur_avec_moteur):
    from eclipse.descripteur import chemin_descripteur
    url, moteur, src = serveur_avec_moteur
    code, _ = _post(f"{url}/api/tache", {"genre": "rendu"})
    assert code == 202
    assert moteur.attend(delai=90.0)
    assert moteur.etat()["etat"] == "terminee"
    sortie = os.path.splitext(src)[0] + "-clean.mp4"
    assert os.path.isfile(chemin_descripteur(sortie))


def test_un_rendu_frais_n_est_pas_a_refaire(serveur_avec_moteur):
    url, moteur, _ = serveur_avec_moteur
    _post(f"{url}/api/tache", {"genre": "rendu"})
    assert moteur.attend(delai=90.0)
    _, corps = _get(f"{url}/api/frames")
    assert json.loads(corps)["etapes"]["rendu"] == "faite"


def test_une_decision_prise_apres_le_rendu_le_rend_a_refaire(
        serveur_avec_moteur):
    """Le coeur du chantier : la peremption est EXACTE, pas datee."""
    url, moteur, _ = serveur_avec_moteur
    _post(f"{url}/api/tache", {"genre": "rendu"})
    assert moteur.attend(delai=90.0)
    _, corps = _get(f"{url}/api/frames")
    assert json.loads(corps)["etapes"]["rendu"] == "faite"

    code, _ = _post(f"{url}/api/decision", {"n": 0, "statut": "ecarter"})
    assert code == 200
    _, corps = _get(f"{url}/api/frames")
    assert json.loads(corps)["etapes"]["rendu"] == "a_refaire"


def test_un_rendu_sans_descripteur_est_a_refaire(serveur_avec_moteur):
    """Un rendu produit en ligne de commande n'en a pas : provenance
    inconnue vaut « a refaire », pas « a jour »."""
    url, _, src = serveur_avec_moteur
    with open(os.path.splitext(src)[0] + "-clean.mp4", "wb") as f:
        f.write(b"venu d'ailleurs")
    _, corps = _get(f"{url}/api/frames")
    assert json.loads(corps)["etapes"]["rendu"] == "a_refaire"


def test_une_reanalyse_a_une_autre_echelle_rend_le_rendu_a_refaire(tmp_path):
    """Le descripteur doit dire de quelle ANALYSE le rendu est issu.

    Il portait donnees["source"], c'est-a-dire la signature de la SOURCE --
    un champ qui ne peut jamais differer quand l'etape de rendu est
    atteignable, puisque charger_cache rend None des qu'il differe. Le
    descripteur ne disait donc RIEN de l'analyse.

    Le chemin, atteignable depuis la page sans aucun drapeau : rendre contre
    un cache fait a une echelle, perdre ce cache, cliquer « Refaire
    l'analyse » -- qui reprend le defaut de analyze faute de cache valide
    dont heriter (voir _reglages_reanalyse) -- et retrouver un descripteur
    identique. Or toutes les mesures que lit analyse_verdicts sont prises A
    L'ECHELLE D'ANALYSE : les verdicts ont bouge, le rendu sur disque ne leur
    correspond plus, et le bandeau disait « deja fait ». Un faux negatif, la
    seule direction que ce chantier interdit.

    Ce test le reproduit sans passer par la perte du cache : c'est le meme
    etat final, et il ne depend pas des defauts de analyze.
    """
    from eclipse.viewer import _corps_frames, _prepare

    src = _cree_video(tmp_path)
    cache = str(tmp_path / "a.json")
    analyze(src, cache, scale=1.0)
    dossier = str(tmp_path / "v")
    genere(src, dossier, _signature_source(src))
    porteur = Porteur(src, cache, str(tmp_path / "d.json"), dossier)
    travail, _, _ = _prepare(porteur, Moteur(), "rendu", False)
    travail()
    porteur.recharge()
    assert _corps_frames(porteur.etat)["etapes"]["rendu"] == "faite"

    # Meme source, meme fichier de decisions, memes reglages de tri : SEULE
    # l'echelle d'analyse change. Avant la correction, les trois champs du
    # descripteur restaient identiques et l'etape disait « deja fait ».
    analyze(src, cache, scale=0.5)
    porteur.recharge()
    assert _corps_frames(porteur.etat)["etapes"]["rendu"] == "a_refaire"


def test_une_source_reencodee_en_place_rend_le_rendu_a_refaire(tmp_path):
    """L'autre moitie du descripteur : SUR QUELLE VIDEO l'analyse a porte.

    La signature de source avait failli disparaitre du descripteur au motif
    qu'elle « ne peut jamais differer » : charger_cache rend bien None des
    qu'elle ne correspond plus a la source, donc la valeur COURANTE est
    toujours celle de la source du jour. Mais perime() ne compare pas deux
    valeurs courantes -- il compare celle qui a ete ECRITE AU MOMENT DU RENDU
    a celle d'aujourd'hui. C'est un champ d'histoire.

    Le chemin : reencoder la video en place (meme chemin, autre contenu)
    invalide le cache, donc l'analyse redevient a faire ; la relancer a la
    meme echelle redonne EXACTEMENT la meme resolution, et le mp4 de la video
    precedente, toujours sur le disque, passerait pour « deja fait ».
    etapes.py nomme justement le reencodage de la source comme le cas ou un
    etat stocke ment.

    Le rayon est passe explicitement aux deux analyses : sans cela, une
    estimation qui differerait d'un cheveu ferait passer ce test pour une
    raison sans rapport avec ce qu'il garde. L'assertion sur les quatre
    champs de resolution le verifie noir sur blanc.
    """
    from eclipse.viewer import _corps_frames, _prepare

    src = _cree_video(tmp_path)
    cache = str(tmp_path / "a.json")
    analyze(src, cache, scale=1.0, radius=25.0)
    dossier = str(tmp_path / "v")
    genere(src, dossier, _signature_source(src))
    porteur = Porteur(src, cache, str(tmp_path / "d.json"), dossier)
    travail, _, _ = _prepare(porteur, Moteur(), "rendu", False)
    travail()
    porteur.recharge()
    assert _corps_frames(porteur.etat)["etapes"]["rendu"] == "faite"
    # Le CACHE lui-meme, et non _signature_analyse : les assertions de
    # diagnostic ne doivent pas dependre de la fonction qu'elles encadrent,
    # sinon retirer un champ ferait echouer le test avant l'assertion qui
    # compte -- celle sur l'etat de l'etape.
    avant = dict(porteur.etat["cache"])

    # La source est reencodee EN PLACE : meme chemin, autre contenu. Le cache
    # est invalide (charger_cache compare la signature), d'ou la reanalyse --
    # a la meme echelle, comme le ferait « Refaire l'analyse ».
    with FrameWriter(src, width=120, height=200, fps=30.0) as w:
        for i in range(25):
            w.write(make_frame(w=120, h=200, center=(40.0 + i, 100.0),
                               r=25.0, gain=0.8))
    analyze(src, cache, scale=1.0, radius=25.0)
    porteur.recharge()
    apres = porteur.etat["cache"]

    # Seule la signature de source distingue les deux analyses : la
    # resolution, elle, est identique au bit. C'est donc bien ce champ-la, et
    # lui seul, que ce test garde.
    resolution = ("scale", "radius", "width", "height")
    assert {c: apres[c] for c in resolution} == {c: avant[c] for c in resolution}
    assert apres["source"] != avant["source"]
    # Le mp4 de la video precedente est toujours la, et l'analyse est refaite.
    assert _corps_frames(porteur.etat)["etapes"]["rendu"] == "a_refaire"


def test_une_reanalyse_a_la_meme_echelle_ne_perime_pas_le_rendu(tmp_path):
    """Et le revers : une reanalyse EQUIVALENTE ne doit rien perimer.

    C'est ce qui a fait preferer la resolution d'analyse a une signature
    (taille, mtime) du fichier de cache, plus exacte au sens strict : le
    fichier change a chaque reanalyse, la resolution non. Sans ce test, une
    signature de fichier passerait le test precedent tout en faisant refaire
    douze minutes d'encodage a chaque clic sur « Refaire l'analyse ».
    """
    from eclipse.viewer import _corps_frames, _prepare

    src = _cree_video(tmp_path)
    cache = str(tmp_path / "a.json")
    analyze(src, cache, scale=1.0)
    dossier = str(tmp_path / "v")
    genere(src, dossier, _signature_source(src))
    porteur = Porteur(src, cache, str(tmp_path / "d.json"), dossier)
    travail, _, _ = _prepare(porteur, Moteur(), "rendu", False)
    travail()
    analyze(src, cache, scale=1.0)
    porteur.recharge()
    assert _corps_frames(porteur.etat)["etapes"]["rendu"] == "faite"


def test_les_etapes_sont_exposees_meme_quand_rien_n_est_pret(tmp_path):
    src = _cree_video(tmp_path)
    porteur = Porteur(src, str(tmp_path / "absent.json"),
                      str(tmp_path / "d.json"), str(tmp_path / "v"))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0),
                                fabrique_handler(porteur, Moteur()))
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        _, corps = _get(f"http://127.0.0.1:{httpd.server_port}/api/frames")
        recu = json.loads(corps)
        assert recu["pret"] is False       # bien la forme NON prete
        e = recu["etapes"]
        assert e["vignettes"] == "disponible"
        assert e["analyse"] == "disponible"
        assert e["rendu"] == "indisponible"
    finally:
        httpd.shutdown()
        httpd.server_close()
        t.join(10.0)
        assert not t.is_alive()


def test_un_rendu_avec_cadrage_explicite_n_est_pas_a_refaire(tmp_path):
    """Le piege des structures non JSON, arme par le chemin par defaut ABSENT.

    _parse_taille rend un COUPLE d'entiers, que --taille et --sortie-taille
    portent jusqu'au Porteur. Ecrit tel quel dans le descripteur, un couple
    revient en LISTE a la relecture : perime() comparerait (100, 160) a
    [100, 160] et repondrait « perime » pour toujours, le bandeau affichant
    « rendu a refaire » a la seconde meme ou le rendu vient d'etre livre.

    Aucun autre test ne le verrait : les fixtures laissent le cadrage a None,
    ou le couple n'existe pas, et l'invariant du descripteur fait pencher
    toute defaillance du meme cote — la fonctionnalite serait inerte tout en
    ayant l'air correcte. D'ou le cadrage EXPLICITE ici.
    """
    from eclipse.viewer import _corps_frames, _prepare

    src = _cree_video(tmp_path)
    cache = str(tmp_path / "a.json")
    analyze(src, cache, scale=1.0)
    dossier = str(tmp_path / "v")
    genere(src, dossier, _signature_source(src))
    porteur = Porteur(src, cache, str(tmp_path / "d.json"), dossier,
                      taille=(100, 160), taille_sortie=(100, 160))
    # Le piege est bien arme : sans conversion, c'est ce couple-la qui
    # partirait au descripteur.
    assert isinstance(porteur.cadrage["taille"], tuple)

    travail, _, _ = _prepare(porteur, Moteur(), "rendu", False)
    travail()
    porteur.recharge()
    assert _corps_frames(porteur.etat)["etapes"]["rendu"] == "faite"


def test_une_decision_prise_pendant_le_rendu_le_rend_a_refaire(tmp_path,
                                                               monkeypatch):
    """Le cas qui justifie a lui seul le descripteur, et que les dates ratent.

    L'encodage dure douze minutes et la revue reste utilisable pendant ce
    temps. Une decision prise a la minute 3 laisse un fichier de decisions
    PLUS ANCIEN que le rendu livre a la minute 12, alors que le rendu ne l'a
    pas prise en compte : une comparaison de mtime repondrait « a jour », a
    tort. Verifie : avec os.path.getmtime a la place de perime(), ce test
    echoue (« faite » au lieu de « a_refaire ») tandis que son jumeau
    test_une_decision_prise_APRES_le_rendu passe quand meme — c'est celui-ci
    seul qui discrimine.

    Deterministe, sans sondage ni attente calibree : le faux render s'arrete
    sur un verrou jusqu'a ce que la decision soit enregistree. Le fichier de
    decisions est ensuite REDATE en arriere plutot qu'endormi une seconde,
    pour armer le piege des dates sans allonger la suite.
    """
    from eclipse import pipeline
    from eclipse.viewer import _sortie_rendu

    src = _cree_video(tmp_path)
    cache = str(tmp_path / "a.json")
    analyze(src, cache, scale=1.0)
    dossier = str(tmp_path / "v")
    genere(src, dossier, _signature_source(src))
    decisions = str(tmp_path / "d.json")
    porteur = Porteur(src, cache, decisions, dossier)
    moteur = Moteur()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0),
                                fabrique_handler(porteur, moteur))
    fil = threading.Thread(target=httpd.serve_forever, daemon=True)
    fil.start()
    url = f"http://127.0.0.1:{httpd.server_port}"

    demarre = threading.Event()
    continuer = threading.Event()

    def faux_render(*a, **k):
        # Un vrai encodage ferait le meme travail en douze minutes ; ce qui
        # compte ici est l'ordre des evenements, pas la duree.
        demarre.set()
        assert continuer.wait(30.0)
        with open(a[1], "wb") as f:
            f.write(b"rendu")
        return {"gardees": 1, "interpolees": 0, "rejetees": 0}

    monkeypatch.setattr(pipeline, "render", faux_render)
    try:
        assert _post(f"{url}/api/tache", {"genre": "rendu"})[0] == 202
        assert demarre.wait(30.0)
        # Le rendu a deja lu le tri qu'il applique : cette decision-ci n'y
        # sera pas.
        assert _post(f"{url}/api/decision",
                     {"n": 0, "statut": "ecarter"})[0] == 200
        avant = os.path.getmtime(decisions)
        os.utime(decisions, (avant - 60.0, avant - 60.0))
        continuer.set()
        assert moteur.attend(delai=60.0)
        assert moteur.etat()["etat"] == "terminee", moteur.etat()

        # Le piege des dates est bien arme : le fichier de decisions est plus
        # ancien que le rendu, donc une comparaison de mtime le croirait pris
        # en compte.
        assert (os.path.getmtime(decisions)
                < os.path.getmtime(_sortie_rendu(src)))
        _, corps = _get(f"{url}/api/frames")
        assert json.loads(corps)["etapes"]["rendu"] == "a_refaire"
    finally:
        moteur.annule()
        assert moteur.attend(delai=30.0)
        httpd.shutdown()
        httpd.server_close()
        fil.join(10.0)
        assert not fil.is_alive()


# -- Tache 6 : choisir la source depuis la page.

def test_les_chemins_derives_sont_propres_a_la_source(tmp_path):
    a = chemins_derives(str(tmp_path / "a.mp4"))
    b = chemins_derives(str(tmp_path / "b.mp4"))
    assert a["cache_path"] != b["cache_path"]
    assert a["decisions_path"] != b["decisions_path"]
    assert a["dossier_vignettes"] != b["dossier_vignettes"]
    assert a["cache_path"].endswith("a.mp4-analysis.json")


def test_deux_sources_ne_partagent_pas_leur_tri(tmp_path):
    """Le defaut que la selection de source rendrait atteignable.

    --decisions vaut « decisions.json », un nom RELATIF au repertoire
    courant : deux sources choisies dans la page l'auraient partage, et les
    decisions prises sur l'eclipse de 2024 auraient trie celle de 2026.
    """
    a = chemins_derives(str(tmp_path / "eclipse-2024.mp4"))
    b = chemins_derives(str(tmp_path / "eclipse-2026.mp4"))
    assert a["decisions_path"] != b["decisions_path"]


def test_post_source_change_la_source_et_derive_les_chemins(tmp_path):
    src = _cree_video(tmp_path, nom="autre.mp4")
    porteur = Porteur(None, None, None, None)
    with _serveur_pour(porteur) as url:
        code, _ = _post(f"{url}/api/source", {"chemin": src})
        assert code == 200
    assert porteur.etat["source"] == src
    # Les TROIS chemins derivent de la source, et non des defauts relatifs
    # au repertoire courant : c'est ce qui empeche deux sources de partager
    # un cache et un tri.
    assert porteur.cache_path.endswith("autre.mp4-analysis.json")
    assert porteur.etat["decisions_path"].endswith("autre.mp4-decisions.json")
    assert porteur.etat["dossier_vignettes"].endswith("autre.mp4-vignettes")


def test_post_source_sur_un_fichier_illisible_rend_400(tmp_path):
    faux = tmp_path / "faux.mp4"
    faux.write_bytes(b"ceci n'est pas une video")
    porteur = Porteur(None, None, None, None)
    with _serveur_pour(porteur) as url:
        code, _ = _post(f"{url}/api/source", {"chemin": str(faux)})
        assert code == 400
    assert porteur.etat["source"] is None
    # Et rien n'a ete pose a moitie : un cache_path neuf sur une source
    # ancienne serait pire que le refus lui-meme.
    assert porteur.cache_path is None


def test_post_source_sans_chemin_rend_400(tmp_path):
    porteur = Porteur(None, None, None, None)
    with _serveur_pour(porteur) as url:
        assert _post(f"{url}/api/source", {})[0] == 400
        assert _post(f"{url}/api/source", {"chemin": 12})[0] == 400
    assert porteur.etat["source"] is None


def test_changer_de_source_pendant_une_tache_rend_409(serveur_avec_moteur,
                                                      tmp_path):
    url, moteur, _ = serveur_avec_moteur
    # La video est fabriquee AVANT le lancement : son encodage ne doit pas
    # entamer le delai que la tache factice tient ouvert.
    autre = _cree_video(tmp_path, nom="autre.mp4")
    libere = threading.Event()
    moteur.lance("analyse", lambda: libere.wait(30.0))
    try:
        code, _ = _post(f"{url}/api/source", {"chemin": autre})
        assert code == 409
    finally:
        libere.set()
    assert moteur.attend(delai=30.0)


def test_changer_de_source_n_efface_pas_les_decisions_precedentes(tmp_path):
    """La lacune que le tableau de couverture du plan admet.

    Aucune tache de ce chantier n'ecrit ni n'efface un fichier de
    decisions : « les decisions ne sont jamais effacees » n'avait donc
    aucun test, et une tache future aurait pu les supprimer sans que rien
    ne le dise. Changer de source doit laisser intact le fichier de la
    source precedente -- et y revenir doit le RELIRE, ce qui prouve aussi
    que la derivation est stable d'un passage a l'autre.
    """
    premiere = _cree_video(tmp_path, nom="premiere.mp4")
    seconde = _cree_video(tmp_path, nom="seconde.mp4")
    porteur = Porteur(None, None, None, None)
    porteur.change_source(premiere)
    decisions = porteur.decisions_path
    enregistrer(decisions, porteur.etat["signature"], {3: "ecarter"})

    porteur.change_source(seconde)
    assert porteur.decisions_path != decisions
    assert os.path.isfile(decisions)

    porteur.change_source(premiere)
    assert porteur.decisions_path == decisions
    assert charger(porteur.etat["decisions_path"],
                   porteur.etat["signature"]) == {3: "ecarter"}


def test_un_etat_sans_source_ne_leve_pas():
    """Le viewer doit pouvoir s'ouvrir sur rien du tout."""
    porteur = Porteur(None, None, None, None)
    assert porteur.etat["source"] is None
    assert porteur.etat["pret"] is False
    # recharge() aussi : c'est le rappel de fin de tache, et il ne doit pas
    # avoir besoin d'une source pour ne rien faire.
    porteur.recharge()
    assert porteur.etat["source"] is None


def test_le_viewer_sans_source_sert_ses_routes_sans_planter():
    """Aucune route ne doit lever tant qu'aucune video n'est choisie.

    L'etat vide porte des chemins None, et os.path.isfile(None) comme
    os.path.splitext(None) sont des TypeError : sans court-circuit,
    /api/frames rendait une trace de pile au lieu d'une reponse -- sur la
    toute premiere requete que fait la page, celle du chargement.
    """
    porteur = Porteur(None, None, None, None)
    with _serveur_pour(porteur) as url:
        assert _get_code(f"{url}/")[0] == 200
        code, corps = _get_code(f"{url}/api/frames")
        assert code == 200
        recu = json.loads(corps)
        assert recu["pret"] is False
        # La page doit pouvoir distinguer « pas de source du tout » de « pas
        # encore prete » : les deux ont pret=False, seule cette cle les
        # separe (la tache 7 en depend).
        assert recu["source"] is None
        assert recu["etapes"] == {"vignettes": "indisponible",
                                  "analyse": "indisponible",
                                  "rendu": "indisponible"}
        # Et la vignette d'une source inexistante est une absence, pas une
        # panne : chemin_vignette leverait sur un dossier None.
        assert _get_code(f"{url}/thumb/0.jpg")[0] == 404


def test_api_langues_rend_les_deux_tables():
    porteur = Porteur(None, None, None, None)
    with _serveur_pour(porteur) as url:
        code, corps = _get_code(f"{url}/api/langues")
        assert code == 200
        donnees = json.loads(corps)
        assert set(donnees) == {"fr", "en"}
        # titre_page itself is now "Eclipse Cleaner" in both tables (task
        # 10): the two-language check picks another key instead.
        assert donnees["fr"]["bouton_parcourir"]
        assert donnees["en"]["bouton_parcourir"] != donnees["fr"]["bouton_parcourir"]


def test_api_langues_ne_demande_pas_d_origine():
    """C'est une LECTURE. La garde d'origine est sur do_POST et do_DELETE,
    et une table de libelles d'interface n'est pas un secret."""
    porteur = Porteur(None, None, None, None)
    with _serveur_pour(porteur) as url:
        code, _ = _requete("GET", f"{url}/api/langues", origine="http://ailleurs")
        assert code == 200


def test_lancer_une_tache_sans_source_est_refuse():
    """Les trois genres, refuses avant meme d'etre confies au moteur.

    Sans cette garde, deux d'entre eux levaient dans le fil HTTP (trace de
    pile, aucun statut rendu au client) et « vignettes » partait pour
    echouer DANS son fil sur un os.path.join(None, ...) -- un echec de
    tache la ou la requete elle-meme n'avait aucun sens.
    """
    porteur = Porteur(None, None, None, None)
    moteur = Moteur()
    with _serveur_pour(porteur, moteur) as url:
        for genre in ("vignettes", "analyse", "rendu"):
            code, _ = _post(f"{url}/api/tache", {"genre": genre})
            assert code == 400, genre
        assert moteur.etat()["genre"] is None


# -- Revue de la tache 6, les quatre constatations Important.

def test_deux_extensions_du_meme_radical_ne_partagent_rien(tmp_path):
    """Important 1 : la collision de radicaux, par un chemin destructeur.

    En retirant l'extension, eclipse.mov et eclipse.mp4 -- tous deux offerts
    par dialogue.EXTENSIONS_VIDEO, et une source avec son transcodage
    dans un meme dossier est ordinaire ici -- donnaient des chemins
    identiques au bit. Ouvrir le .mov, voir l'avertissement « autre
    source », cliquer une frame, et enregistrer() (os.replace) ecrasait TOUT
    le tri du .mp4.

    Les tests d'origine ne pouvaient pas le voir : ils comparaient a.mp4 a
    b.mp4 et eclipse-2024 a eclipse-2026, radicaux differents des deux
    cotes. C'est le radical COMMUN qui discrimine.
    """
    mov = chemins_derives(str(tmp_path / "eclipse.mov"))
    mp4 = chemins_derives(str(tmp_path / "eclipse.mp4"))
    for cle in ("cache_path", "decisions_path", "dossier_vignettes"):
        assert mov[cle] != mp4[cle], cle
    # Et aucun derive ne peut designer la source : il est strictement plus
    # long qu'elle.
    assert mp4["cache_path"] != str(tmp_path / "eclipse.mp4")


def test_revenir_a_la_source_de_la_ligne_de_commande_avertit(tmp_path, capsys):
    """Important 4 : le tri de la ligne de commande, orpheline en silence.

    « viewer A.mp4 » ecrit les revues dans le fichier de la LIGNE DE
    COMMANDE. Basculer vers B.mp4 puis revenir a A.mp4 depuis la page fait
    lire A.mp4-decisions.json, qui n'existe pas : charger() rend {} et, le
    fichier etant ABSENT, diagnostique() rend None. Aucun avertissement
    n'atteignait la page, rien n'etait imprime, et la revue de A
    disparaissait de l'interface sans un mot.

    Le test verifie deux canaux -- le terminal et le CORPS de /api/frames --
    et surtout le NEGATIF : basculer vers B ne doit rien dire, le tri de A
    n'ayant jamais concerne B. C'est ce negatif qui distingue un
    avertissement utile d'un avertissement permanent.

    Il ne dit RIEN de ce que la page affiche : un corps JSON correct que le
    script jette n'est pas un avertissement rendu. C'est precisement ce que
    cette docstring affirmait a tort, et le canal ecran a desormais son test
    a lui (voir test_la_page_affiche_l_avertissement_hors_de_l_etat_pret).
    """
    a = _cree_video(tmp_path, nom="A.mp4")
    b = _cree_video(tmp_path, nom="B.mp4")
    decisions_cli = str(tmp_path / "decisions-ligne-de-commande.json")
    porteur = Porteur(a, str(tmp_path / "cache-cli.json"), decisions_cli,
                      str(tmp_path / "vignettes-cli"))
    # La revue faite sous la ligne de commande, dans SON fichier.
    enregistrer(decisions_cli, porteur.etat["signature"], {3: "ecarter"})
    assert _tri_orpheline(porteur.etat) is None

    # Vers B : rien a dire, ce tri n'a jamais concerne B.
    porteur.change_source(b)
    assert _tri_orpheline(porteur.etat) is None
    assert "ATTENTION" not in capsys.readouterr().out

    # Retour a A : le tri de la ligne de commande cesse d'etre lu.
    porteur.change_source(a)
    avertissement = _tri_orpheline(porteur.etat)
    assert avertissement is not None
    # UN FAIT, pas une phrase : voir viewer._tri_orpheline.
    assert avertissement["code"] == "tri_orphelin"
    assert avertissement["fichier_cli"] == decisions_cli
    assert avertissement["fichier_derive"] == porteur.decisions_path
    assert "ATTENTION" in capsys.readouterr().out

    # Et il atteint la page, sur la forme NON PRETE de la reponse : une
    # source qu'on vient de choisir n'a ni cache ni vignettes.
    with _serveur_pour(porteur) as url:
        _, corps = _get(f"{url}/api/frames")
    recu = json.loads(corps)
    assert recu["pret"] is False
    assert recu["source"] == a
    # UNE LISTE DE FAITS : voir viewer._corps_frames. Le fichier de decisions
    # DERIVE n'existe pas encore ici, diagnostique() ne parle donc pas.
    assert recu["avertissement"] == [avertissement]


def test_l_avertissement_de_tri_orphelin_survit_au_premier_clic(tmp_path):
    """Il s'eteint sur « ce tri est repris », pas sur « le fichier existe ».

    Le predicat precedent etait os.path.isfile(derive) : le premier appui
    sur k creait le fichier derive et l'avertissement disparaissait pour de
    bon, alors que les decisions de la ligne de commande n'etaient toujours
    pas reprises. Le seul indice de leur existence s'eteignait au premier
    clic -- et cliquer avant de lire est l'ordre normal des choses.

    Il ne devient pas pour autant un bandeau fige, ce qui serait l'exces
    inverse : la seconde moitie du test reprend la decision manquante et
    verifie qu'il se tait.

    Le chemin exerce est celui de l'utilisateur, et non porteur.recharge()
    appele a la main : POST /api/decision n'appelle JAMAIS recharge(), et
    c'est exactement pour cela que l'avertissement doit se recalculer a
    chaque requete. Une version precedente de ce test appelait recharge()
    lui-meme et passait donc a cote du seul chemin qui compte.

    D'ou une source PRETE : POST /api/decision refuse (400) sur une source
    qui n'a pas de verdicts.
    """
    a = _cree_video(tmp_path, nom="A.mp4")
    derives = chemins_derives(a)
    analyze(a, derives["cache_path"], scale=1.0)
    genere(a, derives["dossier_vignettes"], _signature_source(a))
    decisions_cli = str(tmp_path / "decisions-ligne-de-commande.json")
    porteur = Porteur(a, str(tmp_path / "cache-cli.json"), decisions_cli,
                      str(tmp_path / "vignettes-cli"))
    enregistrer(decisions_cli, porteur.etat["signature"], {3: "ecarter"})
    porteur.change_source(a)
    assert porteur.etat["pret"] is True
    signature = porteur.etat["signature"]

    with _serveur_pour(porteur) as url:
        _, corps = _get(f"{url}/api/frames")
        avant = json.loads(corps)["avertissement"]
        # Tant que rien n'existe a cote, le renommage est la reprise la plus
        # simple et il ne peut rien detruire : le conseil est donne --
        # reprise_possible porte exactement cette condition (voir
        # viewer._tri_orpheline), la page decidant d'afficher ou non le
        # second fait.
        assert avant[0]["code"] == "tri_orphelin"
        assert avant[0]["reprise_possible"] is True

        # Une decision sur une AUTRE frame que celles du tri de la ligne de
        # commande : le fichier derive nait, et c'est tout ce que l'ancien
        # predicat regardait.
        assert _post(f"{url}/api/decision",
                     {"n": 0, "statut": "ecarter"})[0] == 200
        assert os.path.isfile(derives["decisions_path"])
        _, corps = _get(f"{url}/api/frames")
        pendant = json.loads(corps)["avertissement"]
        assert pendant[0]["n"] == 1
        # Et le conseil de renommage a disparu : il ecraserait desormais la
        # decision qu'on vient de prendre.
        assert pendant[0]["reprise_possible"] is False

        # La decision manquante reprise : plus rien a signaler.
        assert _post(f"{url}/api/decision",
                     {"n": 3, "statut": "ecarter"})[0] == 200
        # Le POST n'enregistre un ecart que s'il CONTREDIT le verdict
        # automatique : sans ce controle, une frame que l'algorithme ecarte
        # deja ferait passer ce test pour une raison sans rapport.
        assert charger(derives["decisions_path"], signature)[3] == "ecarter"
        _, corps = _get(f"{url}/api/frames")
    assert "avertissement" not in json.loads(corps)


def test_texte_avertissement_ne_conseille_le_renommage_que_si_reprise_possible():
    """La CONSOMMATION de reprise_possible, pas seulement la condition qui la
    calcule (voir _tri_orpheline, deja testee ci-dessus par
    test_l_avertissement_de_tri_orphelin_survit_au_premier_clic sur la forme
    du fait).  _texte_avertissement (viewer.py) est le seul rendu du conseil
    de renommage pour la ligne de commande, miroir de texteDuFait
    (viewer.html) -- voir le test JS ci-dessous.

    Mutation controlee, faite puis defaite par copie de sauvegarde (jamais
    git checkout) : reduire la condition de viewer.py:471 a
    `if fait["code"] == "tri_orphelin":` (retrait de
    `and fait.get("reprise_possible")`) laisse ce test-ci echouer alors que
    8 tests existants passent encore (`pytest tests/test_viewer.py -k
    "orphelin or avertit or tri"`) : le terminal conseille alors le
    renommage destructeur meme quand le fichier derive existe deja et porte
    des decisions prises depuis -- exactement la perte de 228 decisions que
    ce projet a deja subie.
    """
    fait = {"code": "tri_orphelin", "n": 3,
            "fichier_cli": "a.json", "fichier_derive": "b.json",
            "reprise_possible": True}
    avec = viewer._texte_avertissement(fait)
    sans = viewer._texte_avertissement({**fait, "reprise_possible": False})
    assert "Renommer" in avec
    assert "Renommer" not in sans


def test_texte_du_fait_js_ne_conseille_le_renommage_que_si_reprise_possible():
    """Le pendant JavaScript du test ci-dessus : texteDuFait (viewer.html),
    seul rendu du conseil de renommage pour la page. Aucun test ne le
    couvrait avant celui-ci -- grep sur tests/ ne le trouvait que dans des
    commentaires (test_langues.py:241,244).

    pytest ne peut pas executer ce script (pas de moteur JS dans les
    dependances du projet) : on verifie donc la CONDITION reellement ecrite
    dans la fonction, et non une reimplementation qui la redirait
    correctement d'elle-meme -- une reimplementation ne casserait jamais si
    viewer.html changeait. Mutation controlee, faite puis defaite par copie
    de sauvegarde : retirer `&& f.reprise_possible` de cette ligne fait
    echouer ce test (la sous-chaine n'est plus dans le bloc), pendant que le
    test Python ci-dessus continuerait de passer -- les deux langages ont
    chacun leur propre garde.
    """
    page = _page()
    corps = _bloc_accolades(page, "function texteDuFait")
    assert 'f.code === "tri_orphelin" && f.reprise_possible' in corps, corps
    assert 'tri_orphelin_reprise' in corps, corps


def _page():
    """Le texte de la page livree, lu sur le disque.

    Sans serveur : ces tests-la ne portent que sur le fichier, et monter une
    fixture complete (analyse + vignettes) pour lire un fichier couterait
    quelques secondes pour rien.
    """
    from eclipse.viewer import _PAGE

    with open(_PAGE, encoding="utf-8") as f:
        return f.read()


def test_la_case_png_disparait_bien_quand_elle_porte_hidden():
    """Une regle display d'AUTEUR bat [hidden], quelle que soit sa specificite.

    Pour des declarations normales, la cascade compare l'ORIGINE avant la
    specificite : `label.filtre { display: inline-flex }` gagnait donc contre
    la regle [hidden] { display: none } du navigateur, et le libelle
    « exporter aussi la sequence PNG » flottait seul a chaque chargement,
    avant meme que les trois boutons ne se montrent. Le commentaire du
    fichier raisonnait sur la seule specificite et concluait a l'envers,
    alors que le meme fichier enonce la regle juste pour #avancement et
    pour #actions.

    Cosmetique -- elRenduPng.disabled tenait toujours -- mais a CHAQUE
    chargement.
    """
    page = _page()
    assert "label.filtre[hidden] { display: none; }" in page
    # La case PNG est bien de celles qui portent [hidden] : sans cela, la
    # regle ci-dessus ne garderait rien.
    assert '<label class="filtre" hidden>' in page


def _bloc_accolades(source, marqueur, jusqu_a=None):
    """Le bloc { ... } qui suit `marqueur`, accolades comprises.

    Comptage naif : une accolade seule dans une chaine ou dans un commentaire
    fermerait le bloc trop tot, et le bloc rendu serait TRONQUE. Une assertion
    « X n'est pas dans ce bloc » passerait alors a vide -- l'echec silencieux,
    la direction qui ne se remarque pas.

    Compter les accolades du bloc rendu ne le dirait pas : le comptage
    s'arrete justement quand elles s'equilibrent, donc elles s'equilibrent
    TOUJOURS, tronque ou non. La verification qui mord est un fragment que le
    bloc entier contient forcement et qu'une troncature perdrait : c'est
    `jusqu_a`. A donner par tout appelant qui assert une ABSENCE.
    """
    depart = source.index("{", source.index(marqueur))
    profondeur = 0
    for i in range(depart, len(source)):
        if source[i] == "{":
            profondeur += 1
        elif source[i] == "}":
            profondeur -= 1
            if profondeur == 0:
                bloc = source[depart:i + 1]
                assert jusqu_a is None or jusqu_a in bloc, (
                    f"bloc tronque apres {marqueur!r} : {jusqu_a!r} manque. "
                    f"Une accolade dans une chaine ou un commentaire l'a "
                    f"ferme trop tot, et toute assertion d'absence portant "
                    f"sur ce bloc serait sans valeur.\n{bloc}")
                return bloc
    raise AssertionError(f"bloc non ferme apres {marqueur!r}")


def test_la_page_affiche_l_avertissement_hors_de_l_etat_pret():
    """Le canal ECRAN, que l'assertion sur le corps JSON ne couvre pas.

    `if (d.avertissement) signaleErreur(...)` vivait A L'INTERIEUR de
    `if (d.pret) { ... }`. Or l'avertissement qui compte le plus arrive sur
    la forme NON prete : une source qu'on vient de choisir dans la page n'a
    ni cache ni vignettes. Le script jetait donc exactement le message que
    le serveur venait d'ajouter -- l'operateur revenant a sa source voyait
    « Extraire les images » et pas un mot sur ses decisions devenues
    invisibles. Le seul canal qui fonctionnait etait le print au terminal.

    Un test du CORPS passait pendant que la page n'affichait rien : c'est
    la raison d'etre de celui-ci. Il verifie la structure, la seule chose
    que pytest puisse voir d'un script ; le comportement a ete verifie a
    part en executant chargeFrames sous node contre un DOM bouchonne (voir
    le rapport de tache).
    """
    from eclipse.viewer import _PAGE

    with open(_PAGE, encoding="utf-8") as f:
        page = f.read()
    corps = _bloc_accolades(page, "async function chargeFrames",
                            jusqu_a="majZoneLancement();")
    assert "d.avertissement" not in _bloc_accolades(corps, "if (d.pret)",
                                                    jusqu_a="vaVers(0);")
    # Deplace, et non supprime.
    assert "if (d.avertissement) signaleErreur(d.avertissement);" in corps


# -- Tache 7 : le bandeau d'etapes dans la page, et le selecteur de source.

def test_la_page_porte_le_bandeau_et_le_selecteur_de_source(serveur):
    """La structure dont depend le bandeau, la seule chose que pytest voie.

    Le comportement est en JavaScript et hors de portee de la suite : il a
    ete verifie a part en executant les fonctions livrees sous node contre un
    DOM bouchonne (voir le rapport de tache). Ce test garde les points
    d'ancrage : les routes appelees, les identifiants du selecteur de source,
    et la classe d'etat « a-refaire » sans laquelle la demonstration du
    chantier -- ecarter une frame apres un rendu -- n'a rien pour s'afficher.
    """
    url, _, _ = serveur
    _, corps = _get(f"{url}/")
    page = corps.decode("utf-8")
    for marqueur in ("/api/parcourir", "/api/source",
                     "id=\"choisir-source\"", "id=\"source-courante\"",
                     "id=\"source-message\"", "a-refaire"):
        assert marqueur in page, marqueur
    # L'explorateur web est parti EN ENTIER : ni sa route, ni son panneau, ni
    # son champ de chemin. Un reste inerte ferait croire a un repli qui
    # n'existe plus.
    for disparu in ("/api/dossier", 'id="explorateur"',
                    'id="explorateur-saisie"', "listeDossier"):
        assert disparu not in page, disparu
    # Les deux grisages sont DEUX mecanismes distincts qui se composent : la
    # tache en cours, et l'indisponibilite de l'etape. Les confondre ferait
    # paraitre disponible, a la seconde ou une tache se termine, une etape
    # qui ne l'est pas. La composition doit donc se lire dans le calcul de
    # `disabled`, et non dans deux affectations qui s'ecrasent.
    zone = _bloc_accolades(page, "function majZoneLancement")
    assert "tacheEnCours ||" in zone, zone
    assert "indisponible" in zone, zone
    # C'est CETTE ligne qui pose l'attribut que les regles CSS ci-dessous
    # lisent par attr(data-libelle-etat) : supprimer bouton.dataset.libelleEtat
    # = ... ne fait echouer AUCUN autre test (mutation controlee, faite puis
    # defaite par copie de sauvegarde -- jamais git checkout) alors que les
    # quatre ::after se vident, dont « ! a refaire : sortie perimee », l'un
    # des trois signaux non chromatiques que le commentaire CSS
    # (voir plus haut, #actions button.*::after) presente comme suffisant
    # seul pour un daltonien. La ligne est NEUVE sur cette branche (56b63cf) :
    # ni une regression pre-existante, ni un trou herite.
    assert ('bouton.dataset.libelleEtat = LIBELLES_ETAT[classe] ? '
            't(LIBELLES_ETAT[classe]) : "";') in zone, zone
    # « a refaire » se distingue de « disponible » sans reposer sur la seule
    # couleur : un mot ecrit en toutes lettres. Le libelle n'est plus un
    # content: litteral (tache 4 -- voir tests/test_langues.py,
    # test_aucun_texte_visible_ne_subsiste_dans_la_css) : il vient de
    # l'attribut data-libelle-etat, pose par majZoneLancement et traduit par
    # t(). La regle CSS elle-meme doit donc employer attr(...), et non la
    # simple presence de la chaine quelque part dans la page : les
    # commentaires de la feuille de style contiennent « a refaire » plusieurs
    # fois, et un test qui s'en contentait survivait a la suppression pure et
    # simple de la declaration.
    #
    # Normalise les fins de ligne : la page servie porte des \r\n, et les
    # motifs ci-dessous tiennent sur plusieurs lignes.
    #
    # Une PREMIERE version de ce test ne verifiait que le bloc .a-refaire, et
    # seulement la PRESENCE d'attr(...) quelque part dedans -- deux angles
    # morts trouves par MUTATION CONTROLEE a la revue, aucun des deux
    # detecte par lecture :
    #   1) un nom d'attribut invente (attr(nexistepas)) sur .indisponible,
    #      .faite ou .en-cours rendrait un texte vide, silencieusement, sans
    #      faire echouer ni l'ancienne assertion (qui ne regardait que
    #      .a-refaire) ni le test d'epuisement CSS (qui ne cherche que des
    #      content: "..." litteraux, jamais un attr() qui pointe dans le
    #      vide) -- d'ou la boucle sur les QUATRE regles ci-dessous ;
    #   2) permuter faite et a-refaire dans LIBELLES_ETAT ferait afficher
    #      « ! a refaire : sortie perimee » sur une etape DEJA FAITE, et
    #      « deja fait » sur une etape a refaire -- faux et silencieux,
    #      puisque aucune garde existante ne verifie la CORRESPONDANCE
    #      classe -> cle (test_aucune_cle_orpheline, par exemple, ne verifie
    #      que l'ENSEMBLE des cles employees, jamais leur affectation) --
    #      d'ou l'egalite terme a terme sur LIBELLES_ETAT plus bas.
    page_lf = page.replace("\r\n", "\n")
    selecteurs_etat = {
        "faite": r'#actions button\.faite::after, #actions button\.a-refaire::after',
        # lookbehind negatif : exclut l'occurrence du MEME texte a la fin du
        # selecteur combine ci-dessus (", #actions button.a-refaire::after"),
        # pour n'isoler que la regle SPECIFIQUE a .a-refaire (poids 700).
        "a-refaire": r'(?<!, )#actions button\.a-refaire::after',
        "indisponible": r'#actions button\.indisponible::after',
        "en-cours": r'#actions\.occupe button\.en-cours::after, #actions button\.en-cours::after',
    }
    for nom, selecteur in selecteurs_etat.items():
        m = re.search(selecteur + r'\s*\{([^}]*)\}', page_lf)
        assert m, f"regle CSS introuvable pour l'etat {nom}"
        assert "content: attr(data-libelle-etat)" in m.group(1), (
            f"etat {nom} : {m.group(1)!r}")
    m = re.search(r'const\s+LIBELLES_ETAT\s*=\s*\{([^}]*)\}', page_lf)
    assert m, "LIBELLES_ETAT introuvable"
    libelles_etat = dict(re.findall(r'["\']?([\w-]+)["\']?\s*:\s*"(\w+)"', m.group(1)))
    assert libelles_etat == {
        "faite": "etat_deja_fait",
        "a-refaire": "etat_a_refaire",
        "indisponible": "etat_indisponible",
        "en-cours": "etat_en_cours",
    }
    assert "etat_a_refaire" in langues.charge("fr")
    assert "etat_a_refaire" in langues.charge("en")
    # La demonstration du chantier : apres un rendu, ecarter une frame doit
    # faire basculer l'etape 3 en « a refaire ». Le serveur le dit des la
    # requete suivante (test_une_decision_prise_apres_le_rendu_le_rend_a_refaire),
    # mais bascule() ne recharge pas la pellicule : sans ce rappel, l'ecran
    # ne le montrerait qu'au rechargement de la page.
    assert "rafraichitEtapes" in _bloc_accolades(page, "async function bascule")
    # L'etape 3 et son option PNG suivent `etapes.rendu`, et non `pret` :
    # `pret` exige aussi les VIGNETTES, que le rendu ne consomme pas (il part
    # de la source et du cache d'analyse, voir _travail_rendu). Un retour a
    # `elLancerRendu.hidden = !d.pret` cachait le bouton d'une etape que le
    # serveur declare disponible, et passait la suite entiere en silence.
    charge = _bloc_accolades(page, "async function chargeFrames",
                             jusqu_a="majZoneLancement();")
    assert "elLancerRendu.hidden" not in charge, charge
    assert ('elRenduPng.parentElement.hidden = etatsEtapes.rendu === '
            '"indisponible"') in charge, charge


def test_libelles_etapes_associe_chaque_etape_a_ses_deux_bons_boutons():
    """Meme classe de defaut que LIBELLES_ETAT ci-dessus (voir
    test_la_page_porte_le_bandeau_et_le_selecteur_de_source), sur la table
    SOEUR que la tache 4 n'a jamais couverte : `grep LIBELLES_ETAPES tests/`
    ne remontait avant ce test que des commentaires.

    Mutation par lecture (deduite de la mecanique identique a LIBELLES_ETAT,
    non reproduite a l'ecran par la revue) : permuter les entrees
    `vignettes` et `rendu` ferait porter "3. Produire la video finale" au
    bouton d'extraction et "1. Extraire les images" a celui du rendu. Ce
    test le detecterait -- l'egalite ci-dessous est terme a terme, pas
    seulement un controle de presence des six cles."""
    page = _page().replace("\r\n", "\n")
    m = re.search(r'const\s+LIBELLES_ETAPES\s*=\s*\{([^}]*)\}', page)
    assert m, "LIBELLES_ETAPES introuvable"
    associations = {nom: (a, b) for nom, a, b in re.findall(
        r'(\w+):\s*\["(\w+)",\s*"(\w+)"\]', m.group(1))}
    assert associations == {
        "vignettes": ("bouton_vignettes", "bouton_vignettes_refaire"),
        "analyse": ("bouton_analyse", "bouton_analyse_refaire"),
        "rendu": ("bouton_rendu", "bouton_rendu_refaire"),
    }
    for _, (a, b) in associations.items():
        assert a in langues.charge("fr") and b in langues.charge("fr")


def test_texte_comptes_associe_chaque_role_a_la_bonne_cle():
    """Le controle terme a terme ajoute par la tache 4 (LIBELLES_ETAT, voir
    ci-dessus) ne couvrait qu'une seule table. texteComptes (viewer.html) en
    a une seconde, sous une autre forme -- un objet passe a t("comptes_sortie",
    ...) plutot qu'une const nommee -- et rien ne la couvrait.

    Mutation controlee, faite puis defaite par copie de sauvegarde (jamais
    git checkout) : permuter compte_frames_rendues et compte_frames_ecartees
    entre les champs `ecrites` et `ecartees` fait echouer ce test, alors que
    `pytest tests/test_langues.py` continue de passer (13 passed) --
    test_comptes_sortie_accorde_chaque_compte_independamment (test_langues.py)
    REIMPLEMENTE texteComptes en Python avec la bonne affectation deja
    codee en dur, et n'eprouve donc que les TABLES, jamais cette
    AFFECTATION-ci. Sans le correctif, le rendu apres permutation devient
    « 196 frames ecartees depuis la source ... pour 4 rendues » -- l'inverse
    de la verite -- sans qu'aucun test existant ne le remarque."""
    page = _page()
    corps = _bloc_accolades(page, "function texteComptes")
    associations = dict(re.findall(r'(\w+):\s*t\("(\w+)"', corps))
    assert associations == {
        "total": "compte_frames_total",
        "ecrites": "compte_frames_rendues",
        "interpolees": "compte_frames_interpolees",
        "ecartees": "compte_frames_ecartees",
    }


# -- La boite de dialogue du systeme, a la place de l'explorateur web.
#
# choisit_video est REMPLACEE dans ces tests-la, et c'est la frontiere juste :
# ce que la route fait, c'est traduire trois issues en statuts HTTP et
# n'ouvrir qu'une boite a la fois. La boite elle-meme attendrait un humain, et
# ce qui peut s'en verifier sans humain l'est dans tests/test_dialogue.py, qui
# dit aussi ce qui reste non couvert.

def _parcourt(url, origine=None, corps=None):
    return _requete("POST", f"{url}/api/parcourir", obj=corps, origine=origine)


def test_parcourir_rend_le_chemin_choisi(serveur_avec_moteur, monkeypatch):
    url, _, _ = serveur_avec_moteur
    monkeypatch.setattr(viewer, "choisit_video",
                        lambda depart=None, langue="fr": "D:/films/eclipse.mp4")
    code, corps = _parcourt(url)
    assert code == 200
    assert json.loads(corps) == {"chemin": "D:/films/eclipse.mp4"}


def test_parcourir_rend_un_chemin_nul_a_l_annulation(serveur_avec_moteur,
                                                     monkeypatch):
    """Une annulation est une REPONSE, pas une panne : 200 avec chemin nul.

    Le module convertit deja la chaine vide de askopenfilename en None (voir
    tests/test_dialogue.py) ; ici on garde que la route la transporte telle
    quelle, au lieu de rendre 400 et de faire clignoter un message d'erreur a
    quelqu'un qui a simplement change d'avis.
    """
    url, _, _ = serveur_avec_moteur
    monkeypatch.setattr(viewer, "choisit_video",
                        lambda depart=None, langue="fr": None)
    code, corps = _parcourt(url)
    assert code == 200
    assert json.loads(corps) == {"chemin": None}


def test_parcourir_ouvre_la_boite_a_cote_de_la_source_courante(
        serveur_avec_moteur, monkeypatch):
    """Sinon la boite s'ouvre n'importe ou et il faut renaviguer a chaque
    changement de source."""
    url, _, src = serveur_avec_moteur
    vus = []
    monkeypatch.setattr(viewer, "choisit_video",
                        lambda depart=None, langue="fr": vus.append(depart))
    _parcourt(url)
    assert vus == [os.path.dirname(src)]


def test_parcourir_sans_source_n_impose_aucun_dossier(monkeypatch):
    """Viewer ouvert sur rien : os.path.dirname(None) leverait un TypeError,
    et la route rendrait une trace au lieu d'un statut."""
    vus = []
    monkeypatch.setattr(viewer, "choisit_video",
                        lambda depart=None, langue="fr": vus.append(depart))
    with _serveur_pour(Porteur(None, None, None, None)) as url:
        code, _ = _parcourt(url)
    assert code == 200
    assert vus == [None]


def test_parcourir_indisponible_rend_503_et_dit_quoi_faire(
        serveur_avec_moteur, monkeypatch):
    """LE CAS DE REPLI. Sans explorateur web, une boite qui ne s'ouvre pas
    laisse la page sans AUCUN moyen de designer une source : le message doit
    donc porter la sortie de secours, et non le seul constat de la panne.
    """
    from eclipse.dialogue import Indisponible

    def boum(depart=None, langue="fr"):
        raise Indisponible("La boite n'a pas pu s'ouvrir (simule).")

    url, _, _ = serveur_avec_moteur
    monkeypatch.setattr(viewer, "choisit_video", boum)
    code, corps = _parcourt(url)
    assert code == 503
    fait = json.loads(corps)
    # UN FAIT, pas une phrase : le detail est le texte de diagnostic libre
    # de l'exception (voir dialogue.py), et la cle "boite_indisponible" -- que
    # la page compose -- porte le QUE FAIRE (relancer avec la source en
    # argument).
    assert fait["code"] == "boite_indisponible"
    assert "n'a pas pu s'ouvrir" in fait["detail"]


# -- Tache 7 : la langue de la page traverse POST /api/parcourir.
#
# choisit_video reste remplace, exactement comme au-dessus : ce qui est
# verifie ici, c'est que la route EXTRAIT la langue du corps et la transmet,
# pas que la boite l'affiche (voir tests/test_dialogue.py pour cela).

def test_parcourir_transmet_la_langue_recue(serveur_avec_moteur, monkeypatch):
    vus = []
    monkeypatch.setattr(viewer, "choisit_video",
                        lambda depart=None, langue="fr": vus.append(langue))
    url, _, _ = serveur_avec_moteur
    _parcourt(url, corps={"langue": "en"})
    assert vus == ["en"]


def test_parcourir_sans_corps_utilise_le_francais(serveur_avec_moteur,
                                                   monkeypatch):
    """Le corps est FACULTATIF (viewer.py disait encore, avant cette tache,
    que /api/parcourir n'en a pas) : son absence ne doit pas empecher de
    choisir un fichier, seulement priver la boite de la langue de la page."""
    vus = []
    monkeypatch.setattr(viewer, "choisit_video",
                        lambda depart=None, langue="fr": vus.append(langue))
    url, _, _ = serveur_avec_moteur
    code, _ = _parcourt(url)
    assert code == 200
    assert vus == ["fr"]


def test_parcourir_sans_cle_langue_utilise_le_francais(serveur_avec_moteur,
                                                        monkeypatch):
    """Un corps JSON valide mais sans la cle "langue" -- par exemple un
    client plus ancien -- ne doit pas non plus lever."""
    vus = []
    monkeypatch.setattr(viewer, "choisit_video",
                        lambda depart=None, langue="fr": vus.append(langue))
    url, _, _ = serveur_avec_moteur
    code, _ = _parcourt(url, corps={})
    assert code == 200
    assert vus == ["fr"]


def test_parcourir_avec_un_corps_json_invalide_utilise_le_francais(
        serveur_avec_moteur, monkeypatch):
    """Le meme repli qu'un corps absent, sur un corps qui n'est PAS du JSON
    exploitable : _lit_corps_json rend deja None dans ce cas pour les autres
    routes (qui, elles, rendent alors 400) -- /api/parcourir, dont le corps
    ne porte qu'un confort, ne doit pas lever pour autant."""
    vus = []
    monkeypatch.setattr(viewer, "choisit_video",
                        lambda depart=None, langue="fr": vus.append(langue))
    url, _, _ = serveur_avec_moteur
    hote_port = url[len("http://"):]
    hote, port = hote_port.split(":")
    conn = http.client.HTTPConnection(hote, int(port))
    try:
        corps = b"{ceci n'est pas du JSON"
        conn.putrequest("POST", "/api/parcourir")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", str(len(corps)))
        conn.endheaders()
        conn.send(corps)
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 200
    finally:
        conn.close()
    assert vus == ["fr"]


def test_une_seule_boite_a_la_fois(serveur_avec_moteur, monkeypatch):
    """Deux onglets peuvent cliquer, et deux fenetres modales sur le meme
    bureau seraient deroutantes : la seconde repart en 409.

    La premiere requete est retenue DANS choisit_video par un evenement, ce
    qui reproduit exactement l'etat « une boite est ouverte ». Le serveur est
    threade (voir la fixture), la seconde est donc servie pendant ce temps.
    """
    url, _, _ = serveur_avec_moteur
    dedans, relache = threading.Event(), threading.Event()

    def bloque(depart=None, langue="fr"):
        dedans.set()
        relache.wait(30.0)
        return None

    monkeypatch.setattr(viewer, "choisit_video", bloque)
    resultat = {}
    fil = threading.Thread(target=lambda: resultat.update(
        code=_parcourt(url)[0]), daemon=True)
    fil.start()
    try:
        assert dedans.wait(30.0), "la premiere requete n'est jamais entree"
        code, _ = _parcourt(url)
        assert code == 409
    finally:
        # Dans un finally : un echec au milieu ne doit pas laisser un fil
        # bloque trente secondes, ni le serveur avec son verrou pris.
        relache.set()
        fil.join(30.0)
    assert not fil.is_alive()
    assert resultat.get("code") == 200


def test_le_verrou_de_la_boite_est_rendu_apres_une_indisponibilite(
        serveur_avec_moteur, monkeypatch):
    """Sans le finally autour du release, un premier echec fermerait la route
    pour de bon : tous les clics suivants repartiraient en 409, y compris
    apres que l'affichage est revenu."""
    from eclipse.dialogue import Indisponible

    url, _, _ = serveur_avec_moteur

    def boum(depart=None, langue="fr"):
        raise Indisponible("simule")

    monkeypatch.setattr(viewer, "choisit_video", boum)
    assert _parcourt(url)[0] == 503
    monkeypatch.setattr(viewer, "choisit_video",
                        lambda depart=None, langue="fr": None)
    assert _parcourt(url)[0] == 200


def test_parcourir_avec_une_origine_etrangere_rend_403(serveur_avec_moteur,
                                                       monkeypatch):
    """La route AGIT -- elle ouvre une fenetre sur le bureau. La garde lui
    vient de la methode POST, sans etre reimplementee : une page tierce ne
    doit pas pouvoir faire surgir une boite de dialogue.
    """
    url, _, _ = serveur_avec_moteur
    appels = []
    monkeypatch.setattr(viewer, "choisit_video",
                        lambda depart=None, langue="fr": appels.append(depart))
    code, _ = _parcourt(url, origine="http://exemple.invalide")
    assert code == 403
    # Refusee AVANT d'ouvrir quoi que ce soit, et non apres.
    assert appels == []


def test_parcourir_n_est_pas_une_route_get(serveur_avec_moteur):
    """GET la rendrait atteignable par un <img> ou un <script src>, qui
    n'envoient aucun Origin : la garde ne l'attraperait pas."""
    url, _, _ = serveur_avec_moteur
    code, _ = _get_code(f"{url}/api/parcourir")
    assert code == 404


# -- Les ecarts avec le rendu, exposes frame par frame.

def _porteur_rendu(tmp_path):
    """Un porteur dont le rendu est FAIT et le descripteur a jour."""
    from eclipse.viewer import _prepare

    src = _cree_video(tmp_path)
    cache = str(tmp_path / "a.json")
    analyze(src, cache, scale=1.0)
    dossier = str(tmp_path / "v")
    genere(src, dossier, _signature_source(src))
    porteur = Porteur(src, cache, str(tmp_path / "d.json"), dossier)
    travail, _, _ = _prepare(porteur, Moteur(), "rendu", False)
    travail()
    porteur.recharge()
    return porteur


def test_un_rendu_frais_n_a_aucun_ecart(tmp_path):
    from eclipse.viewer import _corps_frames

    corps = _corps_frames(_porteur_rendu(tmp_path).etat)
    assert corps["etapes"]["rendu"] == "faite"
    assert corps["divergentes"] == []


def test_une_decision_prise_apres_le_rendu_marque_SA_frame(tmp_path):
    """Le chantier entier, en une assertion : le bandeau disait « a refaire »
    sans jamais dire OU. La frame decidee est marquee, et elle seule.
    """
    from eclipse.viewer import _corps_frames

    porteur = _porteur_rendu(tmp_path)
    etat = porteur.etat
    # Une frame que l'algorithme GARDE, qu'on ecarte a la main : c'est le
    # geste de l'utilisateur (touche k), et il cree bien un ecart.
    n = next(i for i, v in enumerate(etat["verdicts"]) if v is None)
    enregistrer(etat["decisions_path"], etat["signature"], {n: "ecarter"})

    corps = _corps_frames(porteur.etat)
    assert corps["etapes"]["rendu"] == "a_refaire"
    assert corps["divergentes"] == [n]


def test_une_decision_annulee_apres_le_rendu_efface_sa_marque(tmp_path):
    """Revenir sur sa decision doit RETIRER la marque et rendre le rendu a
    jour : une marque qui resterait ferait relancer douze minutes pour rien.
    """
    from eclipse.viewer import _corps_frames

    porteur = _porteur_rendu(tmp_path)
    etat = porteur.etat
    n = next(i for i, v in enumerate(etat["verdicts"]) if v is None)
    enregistrer(etat["decisions_path"], etat["signature"], {n: "ecarter"})
    assert _corps_frames(porteur.etat)["divergentes"] == [n]

    enregistrer(etat["decisions_path"], etat["signature"], {})
    corps = _corps_frames(porteur.etat)
    assert corps["divergentes"] == []
    assert corps["etapes"]["rendu"] == "faite"


def test_un_descripteur_orphelin_ne_marque_rien(tmp_path):
    """Le rendu efface a la main, son .json reste. L'etape 3 redevient
    « disponible » (etapes.etats ne transforme pas une absence en peremption)
    et la timeline ne doit rien peindre : marquer des ecarts avec un rendu qui
    n'existe plus n'a pas de sens.
    """
    from eclipse.viewer import _corps_frames, _sortie_rendu

    porteur = _porteur_rendu(tmp_path)
    etat = porteur.etat
    n = next(i for i, v in enumerate(etat["verdicts"]) if v is None)
    enregistrer(etat["decisions_path"], etat["signature"], {n: "ecarter"})
    assert _corps_frames(porteur.etat)["divergentes"] == [n]

    os.remove(_sortie_rendu(etat["source"]))
    porteur.recharge()
    corps = _corps_frames(porteur.etat)
    assert corps["etapes"]["rendu"] == "disponible"
    assert corps["divergentes"] == []


def test_les_ecarts_voyagent_par_api_frames(serveur_avec_moteur):
    """La cle est bien SUR LA REPONSE, et pas seulement dans _corps_frames :
    c'est par HTTP que la page la lit.
    """
    url, _, _ = serveur_avec_moteur
    _, corps = _get(f"{url}/api/frames")
    assert json.loads(corps)["divergentes"] == []


def test_les_ecarts_sont_annonces_meme_sans_source(tmp_path):
    """La page remplace son ensemble a CHAQUE chargement des frames : une
    forme de reponse amputee y laisserait les ecarts de la source
    precedente, peints sur la timeline de la nouvelle.
    """
    from eclipse.viewer import _corps_frames

    vide = _corps_frames(Porteur(None, None, None, None).etat)
    assert vide["divergentes"] == []
    src = _cree_video(tmp_path)
    porteur = Porteur(src, str(tmp_path / "pas-de-cache.json"),
                      str(tmp_path / "d.json"), str(tmp_path / "v"))
    corps = _corps_frames(porteur.etat)
    assert corps["pret"] is False
    assert corps["divergentes"] == []


def test_la_page_peint_le_lisere_des_ecarts():
    """Les points d'ancrage du lisere. Le comportement est en JavaScript et
    hors de portee de pytest ; il a ete exerce a part sous node contre un
    bouchon de canvas.

    Une asterisque etait la demande d'origine ; a 2556 frames dans 1900 px
    une frame fait 0,7 px et aucun glyphe ne s'y dessine. D'ou le lisere, et
    d'ou un QUATRIEME jeton de couleur : reutiliser l'un des trois aurait
    rendu la marque indistinguable de l'etat qu'elle surcharge.
    """
    page = _page()
    assert "--ecart: #ffcc00;" in page          # theme clair
    assert "--ecart: #ffff80;" in page          # theme sombre
    # Peint depuis l'ensemble, et non depuis un drapeau par frame : c'est ce
    # qui permet a rafraichitEtapes de le remplacer sans toucher a
    # etat.frames ni a la position courante.
    zone = _bloc_accolades(page, "function dessineTimeline")
    assert "divergentes.has(n)" in zone, zone
    assert "HAUTEUR_LISERE" in zone, zone
    # Et il se met a jour sous la touche k, pas au prochain rechargement.
    rafraichit = _bloc_accolades(page, "async function rafraichitEtapes")
    assert "divergentes = new Set(d.divergentes" in rafraichit, rafraichit
    assert "dessineTimeline();" in rafraichit, rafraichit
    # La legende nomme la quatrieme couleur : sans elle, le lisere est un
    # ornement inexplique.
    assert 'class="cle cle-ecart"' in page


def test_la_page_dit_qu_elle_attend_pendant_que_la_boite_est_ouverte():
    """La requete reste en attente tant que la boite est ouverte -- c'est le
    fil du gestionnaire HTTP qui la tient. Une page muette paraitrait figee.
    """
    page = _page()
    zone = _bloc_accolades(page, "async function parcourt")
    assert "messageSource(" in zone, zone
    # Le texte d'attente est desormais table-driven (tache 3 du chantier
    # i18n) : "Cette page attend" ne vit plus en clair dans la page, mais
    # dans les tables de langue sous message_boite_ouverte (voir
    # tests/test_langues.py). Ce qui reste verifiable ici, structurellement,
    # est que parcourt() demande bien ce libelle-la.
    assert 't("message_boite_ouverte")' in zone, zone
    # Le 503 est traite a part : c'est le seul cas ou la page n'a plus aucun
    # moyen de designer une source, et le message du serveur porte la sortie
    # de secours.
    assert "r.status === 503" in zone, zone


# --- Un client qui raccroche ne doit rien ecrire dans le terminal ----------
#
# Le terminal est l'endroit ou l'operateur voit ses rendus et ses
# annulations. Le navigateur, lui, annule ses requetes de vignettes des que
# l'utilisateur traverse la pellicule plus vite qu'elles ne chargent :
# socketserver imprime alors une trace complete par requete annulee, et ces
# traces cachent les messages qui comptent. Ce n'est pas une panne, c'est le
# fonctionnement normal d'un navigateur.


class _SocketQuiRaccroche:
    """Une socket acceptee dont l'ecriture echoue comme sur un client parti.

    POURQUOI PAS UNE VRAIE SOCKET REFERMEE. Le raccrochage reel est une
    course : le client envoie un RST, et le serveur ne le decouvre qu'en
    ecrivant apres. Selon l'ordonnancement, une reponse courte part avant le
    RST et le test serait vert sans rien prouver. L'echec est donc pose a
    l'endroit EXACT ou le systeme le pose -- sendall de la socket acceptee --
    et tout le reste du chemin est le vrai : vrai ThreadingHTTPServer, vrai
    aiguillage socketserver, vrai gestionnaire construit par fabrique_handler.
    """

    def __init__(self, sock, exc):
        self._sock = sock
        self._exc = exc

    def sendall(self, *a, **kw):
        raise self._exc

    def __getattr__(self, nom):
        # makefile, settimeout, shutdown, close, fileno... : tout le reste
        # est la vraie socket, sinon le client ne verrait jamais la fin de
        # la connexion et le test attendrait pour rien.
        #
        # CE QUI FAIT PASSER L'ECRITURE PAR ICI : socketserver n'ouvre un
        # ecrivain bufferise (par makefile, donc sur la VRAIE socket, hors
        # de portee de cet enveloppeur) que si wbufsize > 0. Le gestionnaire
        # HTTP est non bufferise, et ecrit donc par _SocketWriter, qui
        # appelle sendall sur l'objet rendu par get_request -- celui-ci. Si
        # cela changeait un jour, l'exception ne serait plus levee du tout et
        # le test deviendrait vert sans rien prouver : d'ou le corps vide
        # verifie par chaque test qui s'en sert.
        return getattr(self._sock, nom)


@contextlib.contextmanager
def _serveur_dont_l_ecriture_echoue(exc):
    """Sert un porteur nu, mais toute ecriture vers le client leve `exc`."""
    porteur = Porteur(None, None, None, None)

    class Serveur(ThreadingHTTPServer):
        def get_request(self):
            sock, adresse = super().get_request()
            return _SocketQuiRaccroche(sock, exc), adresse

    httpd = Serveur(("127.0.0.1", 0), fabrique_handler(porteur, Moteur()))
    fil = threading.Thread(target=httpd.serve_forever, daemon=True)
    fil.start()
    try:
        yield httpd.server_port
    finally:
        httpd.shutdown()
        httpd.server_close()
        fil.join(10.0)
        assert not fil.is_alive()


def _requete_brute(port, chemin):
    """Envoie une requete et lit jusqu'a la fermeture, sans rien exiger.

    urlopen leverait sur une reponse absente ou tronquee, ce qui est
    justement le cas ici. Et lire jusqu'a EOF SYNCHRONISE le test : dans
    socketserver, handle_error() s'execute avant shutdown_request(), donc la
    fermeture vue par le client prouve que la trace, si elle devait etre
    ecrite, l'a deja ete.
    """
    with socket.create_connection(("127.0.0.1", port), timeout=10.0) as s:
        s.sendall(f"GET {chemin} HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                  "Connection: close\r\n\r\n".encode("ascii"))
        morceaux = []
        while True:
            bloc = s.recv(4096)
            if not bloc:
                return b"".join(morceaux)
            morceaux.append(bloc)


@pytest.mark.parametrize("exc", [
    # WinError 10053, celle que l'utilisateur a vue dans son terminal.
    ConnectionAbortedError(10053, "connexion abandonnee (simulee)"),
    # WinError 10054 cote Windows, ECONNRESET cote POSIX.
    ConnectionResetError(104, "connexion reinitialisee (simulee)"),
    # EPIPE : la forme POSIX du meme evenement.
    BrokenPipeError(32, "tuyau rompu (simule)"),
])
def test_un_client_qui_raccroche_n_ecrit_rien_dans_le_terminal(exc, capsys):
    """LE DEFAUT. Les trois formes du raccrochage, sur les deux systemes."""
    with _serveur_dont_l_ecriture_echoue(exc) as port:
        recu = _requete_brute(port, "/")
    # D'ABORD : l'echec a bien eu lieu. Sans cette ligne, un enveloppeur
    # contourne (voir _SocketQuiRaccroche.__getattr__) rendrait la page
    # entiere, aucune exception ne serait levee, et le silence ci-dessous
    # serait vert sans rien prouver.
    assert recu == b"", recu[:200]
    err = capsys.readouterr().err
    assert err == "", err


def test_une_autre_erreur_d_ecriture_reste_visible(capsys):
    """L'AUTRE MOITIE, et la raison de ne pas attraper OSError en bloc : une
    erreur d'ecriture qui n'est pas un raccrochage est un defaut, et doit
    garder sa trace."""
    with _serveur_dont_l_ecriture_echoue(OSError(9, "descripteur invalide")) as port:
        recu = _requete_brute(port, "/")
    assert recu == b"", recu[:200]
    err = capsys.readouterr().err
    assert "Exception occurred during processing of request" in err, err
    assert "descripteur invalide" in err, err


def test_un_defaut_dans_une_route_reste_visible(capsys, monkeypatch):
    """Et un defaut de NOTRE code, sur une socket parfaitement saine : le
    silence ne doit couvrir que le client parti, jamais le gestionnaire."""
    def boum(_etat):
        raise RuntimeError("defaut simule dans _corps_frames")

    monkeypatch.setattr(viewer, "_corps_frames", boum)
    porteur = Porteur(None, None, None, None)
    with _serveur_pour(porteur) as base:
        port = int(base.rsplit(":", 1)[1])
        _requete_brute(port, "/api/frames")
    err = capsys.readouterr().err
    assert "defaut simule dans _corps_frames" in err, err


def test_api_version_rend_la_version_du_paquet():
    """La route sert a savoir sur quel arbre on travaille."""
    import eclipse
    porteur = Porteur(None, None, None, None)
    with _serveur_pour(porteur) as url:
        code, corps = _get_code(f"{url}/api/version")
        assert code == 200
        donnees = json.loads(corps)
        assert donnees["version"].startswith(eclipse.__version__)


def test_api_version_ne_demande_pas_d_origine():
    """C'est une LECTURE, comme /api/langues : la garde d'origine est sur
    do_POST et do_DELETE, et un numero de version n'est pas un secret."""
    porteur = Porteur(None, None, None, None)
    with _serveur_pour(porteur) as url:
        code, _ = _requete("GET", f"{url}/api/version",
                           origine="http://ailleurs")
        assert code == 200


def test_api_version_est_recalculee_a_chaque_appel(monkeypatch):
    """Le viewer tourne pendant qu'on modifie le depot : une version figee
    au demarrage dirait « propre » sur un arbre devenu sale."""
    import eclipse.viewer as ev
    valeurs = iter(["0.0.1 (aaa1111)", "0.0.1 (bbb2222+modifie)"])
    monkeypatch.setattr(ev, "version_affichee", lambda: next(valeurs))
    porteur = Porteur(None, None, None, None)
    with _serveur_pour(porteur) as url:
        _, un = _get_code(f"{url}/api/version")
        _, deux = _get_code(f"{url}/api/version")
    assert json.loads(un)["version"] != json.loads(deux)["version"]


def test_la_page_porte_le_noeud_de_version_sans_data_t():
    """Le texte de ce noeud est ecrit par chargeVersion : un data-t serait
    efface a chaque changement de langue -- meme piege que les boutons
    theme et langue. Le titre, lui, est statique."""
    page = _page()
    assert 'id="version"' in page
    assert 'data-t-title="version_infobulle"' in page
    debut = page.index('id="version"')
    balise = page[page.rindex("<", 0, debut):page.index(">", debut) + 1]
    assert "data-t=" not in balise, f"data-t interdit sur ce noeud : {balise}"


def test_state_carries_the_preset_triplet(tmp_path):
    src = _cree_video(tmp_path)
    cache = str(tmp_path / "a.json")
    analyze(src, cache, scale=1.0, preset="custom")
    genere(src, str(tmp_path / "v"), _signature_source(src))
    porteur = Porteur(src, cache, str(tmp_path / "d.json"),
                      str(tmp_path / "v"))
    p = porteur.etat["preset"]
    assert p["effectif"] == "custom" and p["cache"] == "custom"
    corps = viewer._corps_frames(porteur.etat)
    assert corps["preset"]["effectif"] == "custom"


def test_choosing_another_preset_makes_the_analysis_stale(tmp_path):
    src = _cree_video(tmp_path)
    cache = str(tmp_path / "a.json")
    analyze(src, cache, scale=1.0, preset="custom")
    genere(src, str(tmp_path / "v"), _signature_source(src))
    porteur = Porteur(src, cache, str(tmp_path / "d.json"),
                      str(tmp_path / "v"))
    assert porteur.etat["pret"]
    porteur.regle_preset("moon")
    assert "analyse" in porteur.etat["manque"]
    assert porteur.etat["preset"]["effectif"] == "moon"
    # Back to the cache's preset: ready again, nothing was destroyed.
    porteur.regle_preset("auto")
    assert porteur.etat["pret"]


def test_a_page_preset_choice_does_not_follow_the_next_source(tmp_path):
    """A preset chosen in the page belongs to the video it was chosen for.

    Carried over, "moon" picked for A would force moon on B AND suppress
    B's own detection -- the profile picks the pass-1 strategies, so this is
    a wrong analysis, not a cosmetic default.
    """
    a = _cree_video(tmp_path, "a.mp4")
    b = _cree_video(tmp_path, "b.mp4")
    cache_a = str(tmp_path / "a.json")
    analyze(a, cache_a, scale=1.0, preset="custom")
    porteur = Porteur(a, cache_a, str(tmp_path / "d.json"),
                      str(tmp_path / "v"))
    porteur.regle_preset("moon")
    assert porteur.etat["preset"]["effectif"] == "moon"

    porteur.change_source(b)
    # B has no cache: its profile comes from ITS OWN detection, falling back
    # to "custom" -- never from the choice made for A. Written against the
    # triplet rather than a hard-coded name, so the test says "B decides for
    # itself" and does not pin what detection makes of a synthetic video.
    p = porteur.etat["preset"]
    assert p["cache"] is None
    assert p["effectif"] == (p["suggere"] or "custom")
    assert porteur.preset_choisi is None


def test_api_preset_route(serveur):
    url, _, _ = serveur
    code, _ = _requete("POST", url + "/api/preset", {"preset": "moon"})
    assert code == 200
    _, corps = _get(url + "/api/frames")
    assert json.loads(corps)["preset"]["effectif"] == "moon"
    code, _ = _requete("POST", url + "/api/preset", {"preset": "pluton"})
    assert code == 400


def test_the_page_and_state_agree_on_the_preset(serveur):
    """The page reads d.preset and posts /api/preset: check the server
    side of that contract end to end over HTTP."""
    url, _, _ = serveur
    _, corps = _get(url + "/api/frames")
    avant = json.loads(corps)["preset"]["effectif"]
    code, _ = _requete("POST", url + "/api/preset", {"preset": "planetary"})
    assert code == 200
    _, corps = _get(url + "/api/frames")
    assert json.loads(corps)["preset"]["effectif"] == "planetary"
    _requete("POST", url + "/api/preset", {"preset": avant})
