import numpy as np
from eclipse.photometry import measure_photometry, solve_corrections
from tests.synth import make_frame


def test_le_niveau_suit_le_gain():
    bas = measure_photometry(make_frame(w=300, h=300, center=(150.0, 150.0),
                                        r=50.0, gain=0.3), 150.0, 150.0, 50.0)
    haut = measure_photometry(make_frame(w=300, h=300, center=(150.0, 150.0),
                                         r=50.0, gain=0.6), 150.0, 150.0, 50.0)
    assert 1.7 < haut["level"] / bas["level"] < 2.3


def test_le_niveau_ne_depend_pas_de_la_phase():
    """La brillance de surface est constante ; seule l'aire eclairee change.

    Mesurer le flux total ferait chuter le niveau a mesure que le croissant
    s'affine, et la normalisation surexposerait la fin de sequence.
    """
    niveaux = []
    for phase in (0.0, 0.4, 0.8):
        m = measure_photometry(make_frame(w=300, h=300, center=(150.0, 150.0),
                                          r=50.0, phase=phase, gain=0.6),
                               150.0, 150.0, 50.0)
        niveaux.append(m["level"])
    assert max(niveaux) / min(niveaux) < 1.15


def test_la_balance_est_mesuree_sur_les_pixels_non_ecretes():
    m = measure_photometry(make_frame(w=300, h=300, center=(150.0, 150.0),
                                      r=50.0, gain=0.5, wb=(1.0, 0.5, 0.25)),
                           150.0, 150.0, 50.0)
    wb = np.array(m["wb"])
    ratios = wb / wb.mean()
    attendu = np.array([1.0, 0.5, 0.25])
    attendu = attendu / attendu.mean()
    assert np.abs(ratios - attendu).max() < 0.08


def test_la_balance_est_nan_si_tout_est_ecrete():
    m = measure_photometry(make_frame(w=300, h=300, center=(150.0, 150.0),
                                      r=50.0, gain=3.0, wb=(1.0, 1.0, 1.0)),
                           150.0, 150.0, 50.0)
    assert np.isnan(m["wb"][0])


def test_solve_corrections_aplatit_une_rupture_d_exposition():
    """Reproduit la rupture de la frame 1050 : le niveau triple d'un coup.

    On compare les deux plateaux entre eux, en dehors de la zone de
    transition : le lissage sur 61 frames etale forcement la marche sur
    +/- 30 frames, et exiger la continuite au point de rupture meme
    reviendrait a tester l'impossible.
    """
    n = 600
    levels = np.concatenate([np.full(300, 60.0), np.full(300, 180.0)])
    gains = solve_corrections(levels, np.ones(n, dtype=bool))
    corrige = levels * gains
    avant, apres = corrige[50:250], corrige[350:550]
    assert abs(avant.mean() - apres.mean()) / avant.mean() < 0.10
    assert avant.std() / avant.mean() < 0.05
    assert apres.std() / apres.mean() < 0.05


def test_solve_corrections_borne_le_gain():
    """Une plage trop longue pour que le filtre median l'absorbe."""
    n = 300
    levels = np.full(n, 100.0)
    levels[100:130] = 1.0
    gains = solve_corrections(levels, np.ones(n, dtype=bool))
    assert gains.max() <= 4.0 + 1e-9
    assert gains.min() >= 0.25 - 1e-9
    assert gains[115] == 4.0                # aurait valu ~100 sans la borne


def test_solve_corrections_gain_unite_sur_un_niveau_constant():
    """Un niveau constant doit produire un gain de 1.0 partout.

    solve_corrections ne recoit plus de canaux, seulement une luminance en
    1D : elle ne peut rien savoir d'un canal eteint. Le garde-fou contre la
    reintroduction d'une correction de couleur est
    test_render.test_apply_frame_ne_corrige_pas_la_couleur, pas ce test-ci.
    """
    n = 300
    levels = np.full(n, 100.0)
    gains = solve_corrections(levels, np.ones(n, dtype=bool))
    assert np.isfinite(gains).all()
    assert np.allclose(gains, 1.0, atol=1e-6)


def test_solve_corrections_ignore_les_frames_invalides():
    n = 400
    levels = np.full(n, 100.0)
    levels[200:220] = 1.0
    valid = np.ones(n, dtype=bool)
    valid[200:220] = False
    gains = solve_corrections(levels, valid)
    assert abs(gains[50] - 1.0) < 0.05
