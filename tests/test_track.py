import numpy as np
from eclipse.track import (median_filter_1d, savgol_1d, rolling_median,
                           interpolate_invalid, smooth_track,
                           planifie_trajectoire)


def test_median_filter_supprime_une_aberration_isolee():
    x = np.array([1.0, 1.0, 1.0, 50.0, 1.0, 1.0, 1.0])
    out = median_filter_1d(x, 5)
    assert out[3] == 1.0
    assert len(out) == len(x)


def test_median_filter_replique_le_bord_sans_deplacer_l_extremite():
    """Le rembourrage par replication maintient le point extreme.

    Un rembourrage par reflexion donnerait 1.0 : il decalerait la premiere
    mesure d'un echantillon vers l'interieur, donc inventerait du mouvement
    au bord de la sequence.
    """
    x = np.arange(10, dtype=np.float64)
    out = median_filter_1d(x, 5)
    assert len(out) == 10
    assert out[0] == 0.0
    assert out[-1] == 9.0


def test_savgol_preserve_une_droite():
    """Un polynome de degre <= order doit traverser le filtre intact.

    Bords compris : aucun mode de rembourrage n'y parvient (l'erreur monte
    a 0.27 en replication, 0.55 en reflexion), d'ou l'evaluation
    polynomiale aux bords.
    """
    x = np.arange(50, dtype=np.float64) * 0.7 + 3.0
    out = savgol_1d(x, window=9, order=2)
    assert np.allclose(out, x, atol=1e-6)


def test_savgol_preserve_une_parabole():
    """order=2 doit aussi laisser passer un polynome du second degre."""
    t = np.arange(50, dtype=np.float64)
    x = 0.03 * t**2 - 1.2 * t + 5.0
    out = savgol_1d(x, window=9, order=2)
    assert np.allclose(out, x, atol=1e-6)


def test_savgol_reduit_le_bruit():
    rng = np.random.default_rng(0)
    vrai = np.linspace(0.0, 10.0, 200)
    bruite = vrai + rng.normal(0.0, 1.0, 200)
    out = savgol_1d(bruite, window=9, order=2)
    assert np.abs(out - vrai).std() < np.abs(bruite - vrai).std() * 0.6


def test_rolling_median_suit_une_derive_lente():
    """La reference locale doit suivre un changement de regime."""
    x = np.concatenate([np.full(100, 10.0), np.full(100, 200.0)])
    out = rolling_median(x, 31)
    assert abs(out[10] - 10.0) < 1e-9
    assert abs(out[190] - 200.0) < 1e-9


def test_interpolate_invalid_comble_un_trou():
    x = np.array([0.0, 0.0, 0.0, 0.0, 4.0])
    valid = np.array([True, False, False, False, True])
    out = interpolate_invalid(x, valid)
    assert np.allclose(out, [0.0, 1.0, 2.0, 3.0, 4.0])


def test_interpolate_invalid_extrapole_aux_bords():
    x = np.array([0.0, 5.0, 6.0, 0.0])
    valid = np.array([False, True, True, False])
    out = interpolate_invalid(x, valid)
    assert out[0] == 5.0
    assert out[3] == 6.0


def test_smooth_track_reprend_la_mesure_telle_quelle():
    """Aucun filtrage : la sortie doit egaler l'entree sur les frames valides."""
    n = 200
    rng = np.random.default_rng(3)
    mx = np.linspace(100.0, 160.0, n) + rng.normal(0.0, 0.8, n)
    my = np.linspace(200.0, 190.0, n) + rng.normal(0.0, 0.8, n)
    sx, sy = smooth_track(mx, my, np.ones(n, dtype=bool))
    assert np.allclose(sx, mx)
    assert np.allclose(sy, my)


def test_smooth_track_preserve_une_marche():
    """Le tournage a ete repointe a la main : les marches sont du signal.

    Un filtre median ou Savitzky-Golay etalerait cette marche sur plusieurs
    frames, decentrant le disque de plusieurs centaines de pixels.
    """
    n = 100
    cx = np.full(n, 100.0)
    cx[50:] = 700.0
    cy = np.full(n, 200.0)
    sx, _ = smooth_track(cx, cy, np.ones(n, dtype=bool))
    assert sx[49] == 100.0
    assert sx[50] == 700.0


def test_smooth_track_ignore_les_mesures_invalides():
    n = 200
    vrai = np.linspace(100.0, 140.0, n)
    mesure = vrai.copy()
    valid = np.ones(n, dtype=bool)
    mesure[80:100] = 9999.0          # mesures aberrantes...
    valid[80:100] = False            # ...mais marquees invalides
    sx, _ = smooth_track(mesure, np.zeros(n), valid)
    assert np.abs(sx[80:100] - vrai[80:100]).max() < 1.0


def test_smooth_track_reprend_toujours_la_mesure_brute():
    """La sortie doit egaler l'entree sur les frames valides."""
    n = 50
    cx = np.linspace(100.0, 200.0, n)
    cy = np.full(n, 50.0)
    sx, sy = smooth_track(cx, cy, np.ones(n, dtype=bool))
    assert np.allclose(sx, cx)


BORNES = (50.0, 150.0)



def test_la_fenetre_se_pose_exactement_sur_le_centre():
    """Dans le corridor, la fenetre EGALE la trajectoire : le disque est
    exactement centre, sans residu. C'est tout l'objet du placement
    geometrique -- l'ancienne poursuite a vitesse bornee laissait le disque
    decentre sur 10,8 % des frames rendues de la sequence reelle."""
    s = np.array([500.0, 503.0, 497.0, 600.0, 450.0])
    W = planifie_trajectoire(s, (420.0, 660.0), 200.0)
    assert np.array_equal(W, s)


def test_un_echelon_est_suivi_immediatement():
    """Une re-acquisition du tracking est un ECHELON, pas une derive : la
    fenetre doit le suivre d'un coup.

    L'ancienne poursuite bornee a 2 px/frame mettait 150 frames a rattraper
    un echelon de 300 px, en produisant un rampement uniforme parfaitement
    visible. Mesure sur la sequence reelle : sur les 300 dernieres frames
    rendues la fenetre panotait a sa vitesse maximale 68,3 % du temps.
    """
    s = np.concatenate([np.full(10, 500.0), np.full(10, 800.0)])
    W = planifie_trajectoire(s, (420.0, 900.0), 200.0)
    assert W[10] - W[9] == 300.0, "l'echelon doit passer en une frame"
    assert np.array_equal(W, s)


def test_chaque_frame_est_independante_de_ses_voisines():
    """Le placement d'une frame ne doit rien devoir a son passe ni a son
    avenir : c'est ce qui permet de conserver une frame dont la position
    s'ecarte de ses voisines, au lieu de l'ecarter pour ne pas imposer un
    panoramique. La poursuite, elle, propageait l'etat de proche en proche.
    """
    a = np.array([500.0, 500.0, 500.0, 700.0, 500.0])
    b = np.array([500.0, 900.0, 500.0, 700.0, 500.0])   # une voisine changee
    Wa = planifie_trajectoire(a, (420.0, 900.0), 200.0)
    Wb = planifie_trajectoire(b, (420.0, 900.0), 200.0)
    assert Wa[3] == Wb[3], "la frame 3 ne doit pas dependre de la frame 1"


def test_hors_du_corridor_la_fenetre_se_borne():
    """Au-dela du corridor la fenetre s'arrete, et le disque se decentre
    d'autant : c'est le seul residu du placement geometrique, et il est
    entierement gouverne par le depassement."""
    s = np.array([200.0, 1500.0])
    W = planifie_trajectoire(s, (420.0, 660.0), 200.0)
    assert W[0] == 220.0                      # 420 - 200
    assert W[1] == 860.0                      # 660 + 200


def test_le_depassement_par_defaut_evite_le_rognage():
    """Quand le disque sort de la course du centre de fenetre, seul le
    depassement permet a la fenetre de le suivre ; sinon le disque deborde
    du cadre et se fait rogner.

    Geometrie reelle : source de 1080 px, fenetre de 840 px, donc un centre
    borne a [420, 660] ; le disque fait 798 px de large, d'ou 21 px de marge
    seulement avant que le cadre ne le coupe.
    """
    from eclipse.pipeline import DEPASSEMENT_BUTEE_DEFAUT
    s = np.full(60, 800.0)              # bien au-dela de la borne 660
    W = planifie_trajectoire(s, (420.0, 660.0), DEPASSEMENT_BUTEE_DEFAUT)
    assert np.abs(s - W).max() <= 21.0   # disque entier dans le cadre


def test_un_depassement_trop_court_laisse_le_disque_deborder():
    """Le compagnon du test ci-dessus : il documente pourquoi le defaut vaut
    ce qu'il vaut. A 40 px la fenetre s'arrete a 700 et le disque, centre en
    800, deborde de 100 px — dont 79 px sont coupes par le cadre."""
    s = np.full(60, 800.0)
    W = planifie_trajectoire(s, (420.0, 660.0), 40.0)
    assert np.abs(s - W).max() >= 100.0
