import os
import time

import numpy as np
import pytest

from eclipse.parallele import (applique, mesure_frame, nombre_processus,
                               rend_frame)
from tests.synth import make_frame


def test_nombre_processus_deduit_du_materiel():
    n = nombre_processus(None)
    assert n >= 1


def test_nombre_processus_respecte_la_demande():
    assert nombre_processus(3) == 3
    assert nombre_processus(1) == 1


def test_nombre_processus_refuse_une_valeur_absurde():
    for mauvais in (0, -1, -4):
        with pytest.raises(ValueError, match="processus"):
            nombre_processus(mauvais)


def test_nombre_processus_refuse_un_non_entier():
    """int(2.9) rendrait 2 en silence : un appelant programmatique croirait
    avoir demande 3 travailleurs. La ligne de commande est en type=int, donc
    seul un appel de bibliotheque peut y tomber, et c'est bien pour lui que le
    refus existe."""
    for mauvais in (2.9, 1.5, 3.0, "3"):
        with pytest.raises(ValueError, match="processus"):
            nombre_processus(mauvais)


def _double(x):
    return x * 2


def test_applique_sequentiel_preserve_l_ordre():
    assert list(applique(_double, range(10), processus=1)) == [i * 2 for i in range(10)]


def _double_lent_a_l_envers(x):
    """Double x, d'autant plus lentement que x est petit.

    L'attente decroissante est ce qui rend le test discriminant : chaque
    travail s'acheve APRES celui qui le suit, donc une collecte non ordonnee
    rend les resultats permutes, et non ordonnes par chance. Verifie hors suite
    en substituant imap_unordered a Pool.map : permutation observee aux trois
    essais, la ou _double instantane laissait l'ordre intact.
    """
    # Pas de 10 ms, et non de 2 : la granularite du sleep sous Windows est
    # trop grossiere pour que 2 ms separent deux achevements de facon
    # fiable, et le test cesserait d'etre discriminant.
    time.sleep((16 - x % 16) * 0.01)
    return x * 2


def test_applique_parallele_preserve_l_ordre():
    """L'ordre des resultats doit suivre celui des travaux, pas celui des
    achevements : c'est ce qui rend l'identite de la sortie atteignable.

    Le cout de la fonction de travail decroit avec son entree (voir
    _double_lent_a_l_envers) : avec un _double instantane, les resultats
    arriveraient dans l'ordre meme sous imap_unordered et le test ne prouverait
    rien de ce que ce docstring annonce.
    """
    obtenu = list(applique(_double_lent_a_l_envers, range(16), processus=2))
    assert obtenu == [i * 2 for i in range(16)]


def test_applique_borne_la_memoire_en_vol():
    """Le fil d'alimentation d'imap consomme son iterable avidement, ce qui
    decoderait toute la video en memoire. La distribution par blocs ne doit
    tirer de la source que ce qu'un bloc contient.

    On compte ce que le generateur d'entree a produit apres avoir consomme un
    seul resultat : cela doit rester borne, pas avoir tout avale.
    """
    tire = []

    def source():
        for i in range(1000):
            tire.append(i)
            yield i

    g = applique(_double, source(), processus=2, bloc=8)
    next(g)
    assert len(tire) <= 16, f"{len(tire)} elements tires pour un seul resultat"


def _pid(x):
    # Une attente courte force le pool a repartir les morceaux : sans elle un
    # travailleur peut avaler tout le paquet avant que les autres ne soient
    # prets, et l'assertion sur le nombre de pids deviendrait intermittente.
    time.sleep(0.02)
    return os.getpid()


def test_applique_parallele_fait_bien_sortir_le_travail_du_parent():
    """Preuve directe que le travail est bien execute hors du parent.

    Sans ce test, remplacer le nombre de travailleurs par 1 laisserait toute la
    suite verte ; et la passe 2 ne gagnant rien de mesurable, aucune mesure de
    bout en bout ne detecterait non plus un tel no-op.

    Aucune duree n'est mesuree ici : la premiere assertion est vraie ou fausse
    quel que soit le temps que prend le travail.
    """
    pids = set(applique(_pid, range(24), processus=3))
    assert os.getpid() not in pids, (
        f"le travail est reste dans le parent : {pids}")
    assert len(pids) > 1, f"un seul travailleur a travaille : {pids}"


def test_applique_sans_travail_ne_leve_pas():
    assert list(applique(_double, [], processus=2)) == []


def test_mesure_frame_rend_les_memes_mesures_que_le_calcul_direct():
    """La fonction de travail doit reproduire exactement ce que la boucle
    sequentielle calculait, sans quoi le cache changerait."""
    from eclipse.locate import locate_center
    from eclipse.photometry import measure_photometry
    from eclipse.quality import masse_captee, measure_quality

    rgb = make_frame(w=200, h=300, center=(100.0, 150.0), r=40.0)
    r = 40.0
    gray = rgb.astype(np.float32).mean(axis=2)
    cx, cy, conf = locate_center(gray, r)
    attendu_q = measure_quality(gray, cx, cy, r)
    attendu_m = masse_captee(gray, cx, cy, r)
    attendu_p = measure_photometry(rgb, cx, cy, r)

    obtenu = mesure_frame((rgb, r))
    assert obtenu["cx"] == cx and obtenu["cy"] == cy and obtenu["conf"] == conf
    assert obtenu["q"] == attendu_q
    assert obtenu["m"] == attendu_m or (np.isnan(obtenu["m"]) and np.isnan(attendu_m))
    assert obtenu["p"] == attendu_p


def test_rend_frame_rend_la_meme_image_que_apply_frame():
    from eclipse.render import apply_frame
    rgb = make_frame(w=200, h=300, center=(100.0, 150.0), r=40.0)
    attendu = apply_frame(rgb, 100.0, 150.0, 1.2, taille=(120, 200),
                          remplissage="bord")
    obtenu = rend_frame((rgb, 100.0, 150.0, 1.2, (120, 200), "bord"))
    assert np.array_equal(obtenu, attendu)
