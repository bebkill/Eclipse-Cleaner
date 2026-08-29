import numpy as np
import pytest
from eclipse.locate import sobel, lit_mask, estimate_radius, locate_center
from tests.synth import make_frame


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
