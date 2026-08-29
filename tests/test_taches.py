import threading
import time

import pytest

from eclipse.taches import Moteur, Occupe, TacheAnnulee


@pytest.fixture
def moteur():
    """Un moteur dont aucun fil ne survit au test, meme s'il echoue."""
    m = Moteur()
    try:
        yield m
    finally:
        m.annule()
        assert m.attend(delai=5.0), "fil encore vivant a la fin du test"


def test_arret_est_un_evenement_leve_par_annule_et_abaisse_au_lancement(moteur):
    """Le drapeau doit etre lisible par les traitements sans rappel.

    genere() delegue a ffmpeg et n'appelle aucun rappel : c'est ce drapeau
    qu'elle consulte. Il doit donc etre public, et abaisse a chaque lancement
    sans quoi la tache suivante s'annulerait aussitot.
    """
    assert isinstance(moteur.arret, threading.Event)
    assert not moteur.arret.is_set()
    moteur.annule()
    assert moteur.arret.is_set()
    moteur.lance("analyse", lambda: None)
    assert moteur.attend(delai=5.0)
    assert not moteur.arret.is_set()
    assert moteur.etat()["etat"] == "terminee"


def test_etat_avant_toute_tache_a_un_genre_nul(moteur):
    e = moteur.etat()
    assert e["genre"] is None
    assert e["etat"] is None
    assert e["fait"] == 0


def test_lance_rend_un_identifiant_et_passe_en_cours(moteur):
    demarre, libere = threading.Event(), threading.Event()

    def travail():
        demarre.set()
        libere.wait(5.0)

    ident = moteur.lance("analyse", travail, total=10)
    assert demarre.wait(5.0)
    e = moteur.etat()
    assert e["id"] == ident
    assert e["genre"] == "analyse"
    assert e["etat"] == "en_cours"
    assert e["total"] == 10
    assert e["debut"] is not None and e["fin"] is None
    libere.set()
    assert moteur.attend(delai=5.0)


def test_progression_met_a_jour_fait_et_total(moteur):
    vu, libere = threading.Event(), threading.Event()

    def travail():
        moteur.progression(3, 7)
        vu.set()
        libere.wait(5.0)

    moteur.lance("analyse", travail)
    assert vu.wait(5.0)
    e = moteur.etat()
    assert (e["fait"], e["total"]) == (3, 7)
    libere.set()
    assert moteur.attend(delai=5.0)


def test_seconde_tache_pendant_la_premiere_est_refusee(moteur):
    demarre, libere = threading.Event(), threading.Event()

    def travail():
        demarre.set()
        libere.wait(5.0)

    moteur.lance("analyse", travail)
    assert demarre.wait(5.0)
    with pytest.raises(Occupe):
        moteur.lance("rendu", lambda: None)
    libere.set()
    assert moteur.attend(delai=5.0)


def test_tache_reussie_passe_a_terminee_et_horodate_la_fin(moteur):
    moteur.lance("analyse", lambda: None)
    assert moteur.attend(delai=5.0)
    e = moteur.etat()
    assert e["etat"] == "terminee"
    assert e["fin"] is not None
    assert e["message"] is None


def test_tache_qui_leve_passe_a_echouee_avec_son_message(moteur, capsys):
    def travail():
        raise RuntimeError("le decodeur a rendu l'ame")

    moteur.lance("analyse", travail)
    assert moteur.attend(delai=5.0)
    e = moteur.etat()
    assert e["etat"] == "echouee"
    assert "le decodeur a rendu l'ame" in e["message"]
    # La trace complete part vers le terminal : le message seul ne suffit pas
    # a diagnostiquer, et l'avaler est le defaut que diagnostique() a deja
    # corrige ailleurs dans ce projet.
    assert "Traceback" in capsys.readouterr().err


def test_la_fin_d_une_tache_est_annoncee_au_terminal(moteur, capsys):
    """Le terminal ne disait RIEN de la fin d'une tache, quelle qu'elle soit.

    C'est pourtant la que l'operateur regarde tourner la passe : un rendu de
    douze minutes qui s'arrete en silence cote terminal ressemble a une
    panne. Les trois issues doivent s'y lire.
    """
    moteur.lance("rendu", lambda: None)
    assert moteur.attend(delai=5.0)
    assert "Tache rendu : terminee" in capsys.readouterr().out

    def echoue():
        raise RuntimeError("le decodeur a rendu l'ame")

    moteur.lance("analyse", echoue)
    assert moteur.attend(delai=5.0)
    sortie = capsys.readouterr().out
    assert "Tache analyse : echouee" in sortie
    # Le motif est sur la meme ligne : la trace, elle, part vers stderr et
    # une ligne "echouee" sans cause obligerait a la lire pour savoir quoi.
    assert "le decodeur a rendu l'ame" in sortie


def test_le_resultat_de_la_fonction_est_porte_par_l_instantane(moteur):
    """Sans cela, la page ne peut rien dire de ce qu'un traitement a produit.

    Le moteur ne l'interprete pas : il le transporte. C'est l'appelant qui
    garantit qu'il est serialisable en JSON (voir viewer._comptes_rendu).
    """
    moteur.lance("rendu", lambda: {"total": 200, "interpolees": 4})
    assert moteur.attend(delai=5.0)
    assert moteur.etat()["resultat"] == {"total": 200, "interpolees": 4}

    # Une tache qui ne rend rien n'invente pas de resultat, et une tache
    # ECHOUEE n'en porte aucun : il n'y a rien a rapporter d'un traitement
    # qui n'est pas alle au bout.
    moteur.lance("analyse", lambda: None)
    assert moteur.attend(delai=5.0)
    assert moteur.etat()["resultat"] is None

    def echoue():
        raise RuntimeError("non")

    moteur.lance("rendu", echoue)
    assert moteur.attend(delai=5.0)
    assert moteur.etat()["resultat"] is None


def test_les_options_du_lancement_sont_portees_par_l_instantane(moteur):
    """L'instantane est le seul etat partage entre les onglets du viewer.

    C'est donc le seul endroit ou un onglet ouvert PENDANT une tache peut
    retrouver ce qui a ete demande dans l'onglet qui l'a lancee.
    """
    moteur.lance("rendu", lambda: None, options={"png": True})
    assert moteur.attend(delai=5.0)
    assert moteur.etat()["options"] == {"png": True}
    # Et un lancement suivant sans options ne garde pas celles du precedent :
    # l'instantane est remis a neuf a chaque lance().
    moteur.lance("analyse", lambda: None)
    assert moteur.attend(delai=5.0)
    assert moteur.etat()["options"] is None


def test_annulation_fait_lever_le_rappel_et_marque_annulee(moteur):
    demarre, leve = threading.Event(), threading.Event()

    def travail():
        demarre.set()
        for i in range(1000):
            try:
                moteur.progression(i + 1, 1000)
            except TacheAnnulee:
                leve.set()
                raise
            time.sleep(0.005)

    moteur.lance("analyse", travail, total=1000)
    assert demarre.wait(5.0)
    moteur.annule()
    assert moteur.attend(delai=5.0)
    assert leve.is_set()
    assert moteur.etat()["etat"] == "annulee"
    assert moteur.etat()["message"] is None


def test_compteur_est_consulte_pendant_la_tache(moteur):
    demarre, libere = threading.Event(), threading.Event()
    valeurs = iter([2, 5, 5, 5, 5, 5, 5, 5])

    def travail():
        demarre.set()
        libere.wait(5.0)

    moteur.lance("vignettes", travail, total=8,
                 compteur=lambda: next(valeurs, 5))
    assert demarre.wait(5.0)
    assert moteur.etat()["fait"] == 2
    assert moteur.etat()["fait"] == 5
    libere.set()
    assert moteur.attend(delai=5.0)


def test_apres_est_appele_apres_un_succes(moteur):
    appels = []
    moteur.lance("analyse", lambda: None, apres=lambda: appels.append(1))
    assert moteur.attend(delai=5.0)
    assert appels == [1]


def test_apres_qui_leve_laisse_la_tache_terminee_avec_un_avertissement(moteur):
    def rate():
        raise OSError("cache illisible")

    moteur.lance("analyse", lambda: None, apres=rate)
    assert moteur.attend(delai=5.0)
    e = moteur.etat()
    # Le travail a reussi : marquer echouee ferait relancer un rendu de douze
    # minutes qui, lui, s'est bien passe. L'avertissement dit ce qui a rate.
    assert e["etat"] == "terminee"
    assert e["avertissement"]["code"] == "rechargement_impossible"
    assert "cache illisible" in e["avertissement"]["detail"]


def test_apres_n_est_pas_appele_apres_un_echec(moteur):
    appels = []

    def travail():
        raise RuntimeError("rate")

    moteur.lance("analyse", travail, apres=lambda: appels.append(1))
    assert moteur.attend(delai=5.0)
    assert appels == []


def test_attend_ne_lit_jamais_le_fil_perime_pendant_le_lancement(moteur, monkeypatch):
    """Ferme la fenetre de course entre l'affectation et le demarrage du fil.

    Avant correction, lance() affectait self._fil puis appelait .start() hors
    du verrou. Un attend() concurrent pouvait alors lire, dans cette fenetre,
    le fil de la tache PRECEDENTE (deja termine) et rendre True a tort, alors
    qu'une nouvelle tache venait de demarrer.

    Le fenetre reelle ne dure qu'une poignee d'instructions : une course
    naturelle serait trop etroite pour etre observee de facon fiable. On
    l'elargit donc deliberement en instrumentant threading.Thread.start pour
    la rendre observable et deterministe, dans les deux sens : avec le
    correctif, attend() doit rester bloque sur le verrou tant que lance() n'a
    pas fini d'affecter et de demarrer le nouveau fil ; sans lui, attend()
    passerait immediatement et tenterait de joindre un fil pas encore
    demarre, ce qui leve RuntimeError et fait echouer ce test.
    """
    # Premiere tache menee a terme : self._fil pointe vers un fil deja mort.
    moteur.lance("analyse", lambda: None)
    assert moteur.attend(delai=5.0)

    entre_dans_start = threading.Event()
    autorise_a_continuer = threading.Event()
    start_original = threading.Thread.start

    def start_instrumente(soi):
        # Ne retarder que le fil interne du moteur (celui cree par lance(),
        # dont la cible est _enveloppe) : sinon ce monkeypatch, pose au
        # niveau de la classe, retarderait aussi le demarrage de lanceur et
        # sondeur eux-memes, ci-dessous, qui sont des threading.Thread
        # ordinaires du harnais de test.
        if getattr(soi, "_target", None) == moteur._enveloppe:
            entre_dans_start.set()
            autorise_a_continuer.wait(5.0)
        return start_original(soi)

    monkeypatch.setattr(threading.Thread, "start", start_instrumente)

    libere = threading.Event()
    resultat = {}
    lanceur = threading.Thread(
        target=lambda: moteur.lance("rendu", lambda: libere.wait(5.0)))
    sondeur = threading.Thread(
        target=lambda: resultat.update(vu=moteur.attend(delai=0.3)))
    try:
        lanceur.start()
        assert entre_dans_start.wait(5.0)

        sondeur.start()
        sondeur.join(0.3)
        # Avec le verrou partage, attend() ne peut pas avancer pendant que
        # lance() est au milieu de son affectation+demarrage : il reste
        # bloque sur l'acquisition du verrou, quel que soit son delai.
        assert sondeur.is_alive(), (
            "attend() n'a pas ete bloque par le verrou pendant la fenetre "
            "d'affectation/demarrage du fil : il a pu lire un fil perime")

        # On debloque lance(), MAIS pas encore libere : la tache "rendu"
        # doit rester en cours pendant que sondeur constate son resultat,
        # sans quoi une liberation trop hative masquerait la difference
        # entre "suit le nouveau fil, encore actif" et "a lu l'ancien, deja
        # termine" (les deux rendraient alors le meme resultat).
        autorise_a_continuer.set()
        sondeur.join(5.0)
        lanceur.join(5.0)
        assert not sondeur.is_alive() and not lanceur.is_alive()
        # Une fois debloque, attend() doit suivre le NOUVEAU fil (encore en
        # cours, puisqu'il attend libere) et non l'ancien (deja termine) :
        # donc False, pas True.
        assert resultat.get("vu") is False
    finally:
        # Filet de securite : quel que soit le point d'echec ci-dessus, ne
        # laisser aucun fil en vie derriere ce test.
        autorise_a_continuer.set()
        libere.set()
        lanceur.join(5.0)
        sondeur.join(5.0)

    assert moteur.attend(delai=5.0)


def test_l_instantane_decrit_toujours_la_derniere_tache(moteur):
    moteur.lance("analyse", lambda: None)
    assert moteur.attend(delai=5.0)
    time.sleep(0.05)
    e = moteur.etat()
    assert e["genre"] == "analyse"
    assert e["etat"] == "terminee"
