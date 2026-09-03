import numpy as np
import pytest
from eclipse.locate import (
    sobel, lit_mask, estimate_radius, locate_center, locate_center_regime,
    scan_radius,
)
from tests.synth import make_frame, make_moon_frame, make_totality_frame


def gris(img):
    return img.astype(np.float32).mean(axis=2)


def test_sobel_detecte_un_bord_vertical():
    g = np.zeros((10, 10), np.float32)
    g[:, 5:] = 100.0
    gx, gy = sobel(g)
    assert gx[5, 4] > 0        # la luminance croit vers les x positifs
    assert abs(gy[5, 4]) < 1e-3


def test_lit_mask_isole_le_disque():
    img = make_frame(w=200, h=200, center=(100.0, 100.0), r=40.0)
    m = lit_mask(gris(img))
    aire = int(m.sum())
    assert abs(aire - np.pi * 40.0**2) / (np.pi * 40.0**2) < 0.05


def test_estimate_radius_sur_des_disques_pleins():
    grays = [gris(make_frame(w=200, h=200, center=(100.0 + i * 0.3, 100.0), r=37.0))
             for i in range(20)]
    assert abs(estimate_radius(grays, n_candidats=10) - 37.0) < 1.0


def test_estimate_radius_ignore_les_frames_eclipsees():
    """Les frames a forte phase ne doivent pas tirer le rayon vers le bas."""
    pleines = [gris(make_frame(w=200, h=200, center=(100.0, 100.0), r=37.0))
               for _ in range(10)]
    eclipsees = [gris(make_frame(w=200, h=200, center=(100.0, 100.0), r=37.0, phase=0.8))
                 for _ in range(30)]
    assert abs(estimate_radius(pleines + eclipsees, n_candidats=10) - 37.0) < 1.0


@pytest.mark.parametrize("phase", [0.0, 0.3, 0.6, 0.8, 0.95])
def test_locate_center_du_disque_plein_au_croissant_fin(phase):
    """La contrainte de rayon fixe doit tenir jusqu'au croissant a 5%."""
    img = make_frame(w=300, h=300, center=(151.4, 148.7), r=50.0,
                     phase=phase, angle=0.7)
    cx, cy, conf = locate_center(gris(img), r=50.0)
    assert abs(cx - 151.4) < 1.0
    assert abs(cy - 148.7) < 1.0
    assert conf > 0.0


def test_locate_center_ignore_le_bord_lunaire():
    """Sur un croissant fin l'arc lunaire est aussi long que l'arc solaire.

    Un vote isotrope produirait un pic concurrent au centre de la Lune ;
    le vote dirige par le gradient doit l'etaler.
    """
    r, phase, angle = 50.0, 0.9, 0.0
    img = make_frame(w=300, h=300, center=(150.0, 150.0), r=r, phase=phase, angle=angle)
    cx, cy, _ = locate_center(gris(img), r=r)
    centre_lune_x = 150.0 + 2.0 * r * (1.0 - phase)
    assert abs(cx - 150.0) < 1.0
    assert abs(cx - centre_lune_x) > 5.0


def test_locate_center_resiste_a_une_bande_nuageuse():
    img = make_frame(w=300, h=300, center=(150.0, 150.0), r=50.0, phase=0.5,
                     cloud=(120, 165, 0.25))
    cx, cy, _ = locate_center(gris(img), r=50.0)
    assert abs(cx - 150.0) < 2.0
    assert abs(cy - 150.0) < 2.0


def test_locate_center_avec_le_disque_tranche_par_l_horizon():
    """Le centre reel tombe dans la zone noircie : seul l'arc visible vote.

    C'est le cas des dernieres frames de la sequence reelle. Le vote a rayon
    fixe retrouve un centre que rien n'eclaire.
    """
    img = make_frame(w=300, h=300, center=(150.0, 200.0), r=50.0, horizon=185)
    cx, cy, _ = locate_center(gris(img), r=50.0)
    assert abs(cx - 150.0) < 2.5
    assert abs(cy - 200.0) < 4.0


def test_locate_center_sur_une_frame_noire():
    cx, cy, conf = locate_center(np.zeros((100, 100), np.float32), r=20.0)
    assert conf == 0.0
    assert np.isnan(cx) and np.isnan(cy)


def test_locate_center_precision_sous_pixel():
    for vrai_cx in (150.0, 150.25, 150.5, 150.75):
        img = make_frame(w=300, h=300, center=(vrai_cx, 150.0), r=50.0)
        cx, _, _ = locate_center(gris(img), r=50.0)
        assert abs(cx - vrai_cx) < 0.6


def _disque_aplati(w, h, cx, cy, a, b, haut=1.0, bas=1.0):
    """Disque elliptique, avec un eclairement reglable par moitie.

    a, b : demi-axes horizontal et vertical. `haut` et `bas` multiplient la
    luminance au-dessus et au-dessous du centre : c'est ainsi qu'on simule le
    voile qui, sur la sequence reelle, fait basculer l'equilibre entre les
    deux arcs d'une frame a l'autre.
    """
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    d = np.sqrt(((xx - cx) / a) ** 2 + ((yy - cy) / b) ** 2)
    # Limbe NET : la transition tient en 2 px, comme sur la sequence reelle
    # (largeur 80/20 mesuree a 1,5-2,0 px). Un degrade large etalerait le
    # gradient et le vote ne tomberait plus sur le centre.
    img = np.clip((1.0 - d) * a / 2.0, 0.0, 1.0) * 200.0 + 20.0
    # Degrade DOUX du haut vers le bas : une marche franche creerait une
    # arete droite, dont les votes formeraient un artefact a eux seuls et
    # brouilleraient ce que ce test cherche a montrer.
    t = np.clip((yy - (cy - b)) / (2.0 * b), 0.0, 1.0)
    return (img * (haut + (bas - haut) * t)).astype(np.float32)


def test_un_disque_rond_n_est_pas_touche_par_l_alignement():
    """Le chemin nominal ne doit pas bouger d'un iota : sans aplatissement,
    il n'y a pas de pic concurrent et la mesure reste celle d'avant."""
    g = _disque_aplati(400, 400, 200.0, 200.0, 120.0, 120.0)
    cx, cy, conf = locate_center(g, 120.0)
    assert abs(cx - 200.0) < 2.0
    assert abs(cy - 200.0) < 2.0
    assert conf > 0.0


def _bascule(a, b, poids_concurrent):
    """Amplitude du basculement quand l'eclairement passe du haut au bas."""
    import eclipse.locate as L
    ancien = L.POIDS_CONCURRENT
    L.POIDS_CONCURRENT = poids_concurrent
    try:
        haut = _disque_aplati(400, 400, 200.0, 200.0, a, b, haut=1.0, bas=0.55)
        bas = _disque_aplati(400, 400, 200.0, 200.0, a, b, haut=0.55, bas=1.0)
        return abs(locate_center(haut, a)[1] - locate_center(bas, a)[1])
    finally:
        L.POIDS_CONCURRENT = ancien


def test_un_disque_aplati_bascule_deux_fois_moins():
    """LE DEFAUT CORRIGE. Le vote emploie un rayon FIXE ; le disque s'aplatit
    en approchant de l'horizon (refraction, 6 % mesures sur la sequence
    reelle). L'accumulateur porte alors TROIS maxima : l'arc inferieur, les
    extremites laterales -- qui donnent le vrai centre, la normale y etant
    horizontale --, et l'arc superieur. L'argmax basculait vers l'un ou
    l'autre des deux arcs selon lequel etait le mieux eclaire, et le disque
    sautait dans le cadre.

    Le mecanisme retient toujours le maximum local de plus grand cy : le vrai
    centre, ou l'arc superieur. Jamais l'arc inferieur, qui est le plus
    mauvais des trois.

    Le test compare l'amplitude du basculement avec et sans le mecanisme, en
    neutralisant ce dernier par un seuil de poids inatteignable. Un seuil
    absolu ne dirait rien : c'est l'ECART entre les deux qui est la propriete.
    """
    a, b = 120.0, 111.0                 # 7,5 % d'aplatissement
    sans = _bascule(a, b, 10.0)         # seuil inatteignable : mecanisme eteint
    avec = _bascule(a, b, 0.55)         # seuil livre
    assert sans > 15.0, f"la fixture ne reproduit pas le defaut (bascule {sans:.1f})"
    assert avec <= sans / 2.0, (
        f"bascule {avec:.1f} avec le mecanisme contre {sans:.1f} sans : "
        "le gain attendu est d'au moins la moitie")


def test_l_arc_inferieur_n_est_jamais_retenu():
    """Le pire des trois maxima est celui de l'arc inferieur : il vote dR
    au-DESSUS du vrai centre. Quel que soit l'eclairement, la mesure ne doit
    jamais s'y poser."""
    a, b = 120.0, 105.0                 # 12,5 % : dR = 15 px
    for haut, bas in ((1.0, 0.45), (0.45, 1.0)):
        g = _disque_aplati(400, 400, 200.0, 200.0, a, b, haut=haut, bas=bas)
        cy = locate_center(g, a)[1]
        assert cy > 200.0 - 5.0, (
            f"cy={cy:.1f} : la mesure est tombee sur l'arc inferieur "
            f"(attendu a {200.0 - (a - b):.0f})")


def test_le_concurrent_doit_etre_assez_fort():
    """Un pic parasite faible ne doit pas detourner la mesure : le seuil de
    poids existe pour que le mecanisme ne se declenche que sur le vrai
    double pic, et non sur le premier accident de l'accumulateur."""
    from eclipse.locate import _concurrent_vertical
    acc = np.zeros((300, 300), dtype=np.float32)
    acc[100, 150] = 100.0
    acc[130, 150] = 20.0                # 20 % du principal : trop faible
    assert _concurrent_vertical(acc, 100, 150, 120.0) is None
    acc[130, 150] = 80.0                # 80 % : retenu
    assert _concurrent_vertical(acc, 100, 150, 120.0) == (130, 150)


def test_le_concurrent_doit_etre_dans_la_bande_verticale():
    """Hors de la bande, ce n'est pas le double pic du defaut de rayon mais
    autre chose — un second astre, un reflet — et il ne faut pas s'y fier."""
    from eclipse.locate import _concurrent_vertical
    acc = np.zeros((400, 300), dtype=np.float32)
    acc[100, 150] = 100.0
    acc[105, 150] = 90.0                # 5 px : sous la bande (0,06 x 120 = 7)
    assert _concurrent_vertical(acc, 100, 150, 120.0) is None
    acc[105, 150] = 0.0
    acc[250, 150] = 90.0                # 150 px : au-dela (0,25 x 120 = 30)
    assert _concurrent_vertical(acc, 100, 150, 120.0) is None


def test_un_petit_rayon_desactive_le_mecanisme():
    """Sous un certain rayon, la bande de recherche tombe DANS le pic
    principal et son propre epaulement passe pour un maximum local.

    Attrape sur la sequence de test a r=20 px : 0,06 r n'y fait qu'un pixel,
    alors qu'un pic de l'accumulateur en fait plusieurs apres les deux
    lissages 3x3. La mesure se deplacait de quelques pixels et le cadrage
    revelait des bords noirs -- test_render_recadre_et_supprime_les_bords_noirs
    est tombe dessus.

    L'accumulateur ci-dessous porte un epaulement a 4 px du pic, entoure de
    creux : sans le plancher de bande il passe le controle de maximum local
    et detourne la mesure. Avec le plancher, il est hors bande.
    """
    from eclipse.locate import _concurrent_vertical
    acc = np.zeros((200, 200), dtype=np.float32)
    acc[100, 100] = 100.0
    acc[101, 100] = 50.0
    acc[104, 100] = 90.0             # l'epaulement : maximum local a 4 px
    acc[107, 100] = 50.0
    assert _concurrent_vertical(acc, 100, 100, 20.0) is None, (
        "a r=20 la bande [1,5] tombe dans le pic : rien ne doit etre retenu")
    # Le meme accumulateur avec un rayon assez grand : la bande commence
    # au-dela du pic, et un vrai concurrent y redevient cherchable.
    acc[130, 100] = 90.0
    assert _concurrent_vertical(acc, 100, 100, 200.0) == (130, 100)


def test_lit_mask_max_mode_sees_the_umbral_part():
    """Percentile mode only sees the lit sliver of a half-shadowed moon;
    max mode must cover the whole disc (measured failure: area-radius 73
    to 132 px for a constant 195 px disc on the user's videos).
    umbra_level 0.25: the umbral gray sits at ~14 % of the frame max
    (umbra_wb dims the gray), comfortably above LIT_MAX_FRACTION."""
    img = make_moon_frame(w=200, h=200, center=(100.0, 100.0), r=50.0,
                          umbra=0.5, umbra_level=0.25)
    g = gris(img)
    aire_max = int(lit_mask(g, mode="max").sum())
    attendu = np.pi * 50.0 ** 2
    assert abs(aire_max - attendu) / attendu < 0.12


def test_lit_mask_max_mode_on_a_black_frame_is_empty():
    assert not lit_mask(np.zeros((60, 60), np.float32), mode="max").any()


def test_lit_mask_refuses_an_unknown_mode():
    with pytest.raises(ValueError, match="inconnu"):
        lit_mask(np.zeros((10, 10), np.float32), mode="mediane")


def test_lit_mask_default_mode_is_unchanged():
    img = make_frame(w=200, h=200, center=(100.0, 100.0), r=40.0)
    assert (lit_mask(gris(img)) == lit_mask(gris(img), mode="percentile")).all()


def test_estimate_radius_max_mode_on_small_dim_moons():
    """A small moon (1.7 % of the pixels, like Moon-Eclipse.mp4): the p99
    falls in the sky and percentile mode underestimates; max mode holds."""
    grays = [gris(make_moon_frame(w=360, h=640, center=(180.0, 320.0),
                                  r=35.0, umbra=0.3, umbra_level=0.25))
             for _ in range(10)]
    assert abs(estimate_radius(grays, n_candidats=5, lit_mode="max") - 35.0) < 2.0


def test_l_alignement_ne_deplace_pas_la_mesure_horizontale():
    """L'alignement ne reprend que le Y du pic concurrent.

    Le defaut du rayon fixe dedouble le pic VERTICALEMENT ; en x les deux
    pics ne different que par le bruit de l'accumulateur. Reprendre aussi le
    x transportait ce bruit dans la mesure horizontale, et surtout le faisait
    APPARAITRE ET DISPARAITRE avec le mecanisme -- donc sauter d'une frame a
    l'autre, ce qui est exactement ce qu'on cherche a supprimer.

    Ici le meme disque aplati est rendu deux fois, une fois l'arc du haut
    favorise (le mecanisme reste inactif), une fois l'arc du bas (il tire).
    L'ecart de cx entre les deux mesure ce que la bascule coute en
    horizontal : 3,97 px en reprenant les deux coordonnees, 1,52 px avec le
    y seul. Mesure equivalente sur la sequence reelle, contre une verite de
    terrain independante : erreur horizontale maximale de 20,6 a 10,3 px.
    """
    a, b = 120.0, 105.0
    haut = _disque_aplati(400, 400, 200.0, 200.0, a, b, haut=1.0, bas=0.45)
    bas = _disque_aplati(400, 400, 200.0, 200.0, a, b, haut=0.45, bas=1.0)
    cx_haut = locate_center(haut, a)[0]
    cx_bas = locate_center(bas, a)[0]
    assert abs(cx_haut - cx_bas) < 2.5, (
        f"la bascule deplace cx de {abs(cx_haut - cx_bas):.2f} px : le x du "
        f"concurrent est-il repris ?")


def test_dark_vote_locks_a_totality_disc():
    img = make_totality_frame(w=240, h=240, center=(120.0, 118.0), r=55.0,
                              corona=0.5)
    g = gris(img)
    cx, cy, conf = locate_center(g, 55.0, vote="dark")
    assert abs(cx - 120.0) < 1.5 and abs(cy - 118.0) < 1.5
    assert conf > 0.05


def test_bright_vote_does_not_lock_a_totality_disc():
    """The regression the dual regime exists for: on a dark disc the
    bright-vote normals point away from the center."""
    img = make_totality_frame(w=240, h=240, center=(120.0, 118.0), r=55.0,
                              corona=0.5)
    cx, cy, conf = locate_center(gris(img), 55.0)     # default bright
    _, _, conf_dark = locate_center(gris(img), 55.0, vote="dark")
    assert conf_dark > 2.0 * conf


def test_dual_vote_picks_the_right_regime_on_both_sides():
    tot = gris(make_totality_frame(w=240, h=240, center=(120.0, 120.0),
                                   r=55.0, corona=0.5))
    croissant = gris(make_frame(w=240, h=240, center=(120.0, 120.0),
                                r=55.0, phase=0.9))
    (cx, cy, _), regime = locate_center_regime(tot, 55.0)
    assert regime == "dark" and abs(cx - 120.0) < 1.5
    (cx, cy, _), regime = locate_center_regime(croissant, 55.0)
    assert regime == "bright" and abs(cx - 120.0) < 1.5


def test_dual_vote_equals_the_winning_single_regime():
    tot = gris(make_totality_frame(w=240, h=240, center=(120.0, 120.0),
                                   r=55.0, corona=0.5))
    assert locate_center(tot, 55.0, vote="dual") == \
        locate_center(tot, 55.0, vote="dark")


def test_scan_radius_recovers_a_half_shadowed_moon():
    """The decisive measurement of the spec, in synthetic form: the lit
    AREA lies (radius 73-132 px for a 195 px disc on the user's videos),
    the vote-peak scan does not."""
    grays = [gris(make_moon_frame(w=270, h=480, center=(135.0, 240.0),
                                  r=97.0, umbra=u, umbra_level=0.25))
             for u in (0.2, 0.5, 0.7)]
    assert abs(scan_radius(grays) - 97.0) < 2.0


def test_scan_radius_recovers_a_crescent_sun_in_a_halo():
    grays = [gris(make_frame(w=270, h=480, center=(135.0, 240.0), r=65.0,
                             phase=0.9, halo=0.4))
             for _ in range(3)]
    assert abs(scan_radius(grays, vote="dual") - 65.0) < 2.0


def test_scan_radius_ignores_empty_frames():
    vide = np.zeros((480, 270), np.float32)
    grays = [vide, gris(make_moon_frame(w=270, h=480,
                                        center=(135.0, 240.0), r=80.0,
                                        umbra=0.4, umbra_level=0.25)), vide]
    assert abs(scan_radius(grays) - 80.0) < 2.0


def test_scan_radius_with_nothing_usable_raises():
    with pytest.raises(ValueError):
        scan_radius([np.zeros((60, 60), np.float32)])


def test_dual_vote_takes_a_dark_radius_of_its_own():
    """The diagnosed defect, in synthetic form. A dual-vote sequence has TWO
    radii -- the bright solar limb and the larger dark lunar disc -- and the
    dark vote must use its own. On m2-res_852p the cached radius fitted the
    solar limb (86.9 px) while the dark disc measured 93.8: every dark vote
    then landed on a CIRCLE of radius 6.9 px around the true centre instead
    of on a peak, and the argmax alternated between two wrong modes 6 px
    either side of truth (58 horizontal jumps of 24 source px).
    """
    tot = gris(make_totality_frame(w=200, h=200, center=(100.0, 100.0),
                                   r=63.0, corona=0.5))
    # At the BRIGHT radius the dark vote is degenerate: the ring accumulator
    # puts the argmax off the true centre.
    (faux_x, _, faux_conf), _ = locate_center_regime(tot, 55.0, vote="dual")
    # At the dark radius it lands, and with a far stronger peak.
    (cx, cy, conf), regime = locate_center_regime(tot, 55.0, vote="dual",
                                                  r_dark=63.0)
    assert regime == "dark"
    assert abs(cx - 100.0) < 1.5 and abs(cy - 100.0) < 1.5
    assert conf > faux_conf
    assert abs(faux_x - 100.0) > abs(cx - 100.0)


def test_r_dark_none_keeps_the_single_radius_form():
    """Byte-identity of the historic call: no r_dark means one radius."""
    tot = gris(make_totality_frame(w=200, h=200, center=(100.0, 100.0),
                                   r=63.0, corona=0.5))
    croissant = gris(make_frame(w=200, h=200, center=(100.0, 100.0),
                                r=55.0, phase=0.9))
    for g in (tot, croissant):
        for vote in ("bright", "dark", "dual"):
            assert (locate_center_regime(g, 55.0, vote=vote)
                    == locate_center_regime(g, 55.0, vote=vote, r_dark=None))


def test_a_dark_single_vote_honours_r_dark():
    """vote="dark" is the regime the second radius scan runs under: it must
    read r_dark too, not the bright radius it is given alongside."""
    tot = gris(make_totality_frame(w=200, h=200, center=(100.0, 100.0),
                                   r=63.0, corona=0.5))
    (cx, _, _), regime = locate_center_regime(tot, 55.0, vote="dark",
                                              r_dark=63.0)
    assert regime == "dark" and abs(cx - 100.0) < 1.5
    assert locate_center_regime(tot, 55.0, vote="dark", r_dark=63.0) \
        == locate_center_regime(tot, 63.0, vote="dark")


def test_a_bright_vote_ignores_r_dark():
    """The bright regime has nothing to do with the dark radius: passing one
    must not move a bright measure, or non-dual profiles would shift."""
    croissant = gris(make_frame(w=200, h=200, center=(100.0, 100.0),
                                r=55.0, phase=0.5))
    assert locate_center_regime(croissant, 55.0, vote="bright", r_dark=63.0) \
        == locate_center_regime(croissant, 55.0, vote="bright")
