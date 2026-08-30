import numpy as np
from eclipse.photometry import (measure_photometry, solve_corrections,
                                solve_couleur)
from tests.synth import make_frame


def _wb_oscillante(n, base=(180.0, 120.0, 60.0), ampleur=0.08):
    """Balance qui oscille frame a frame autour d'une teinte fixe.

    C'est la signature de la balance automatique d'un telephone : la teinte
    moyenne est stable, mais chaque frame s'en ecarte un peu, en opposition
    R/B pour garder la luminance a peu pres constante.
    """
    wb = np.tile(np.asarray(base, dtype=np.float64), (n, 1))
    signe = np.where(np.arange(n) % 2 == 0, 1.0, -1.0)
    wb[:, 0] *= 1.0 + ampleur * signe
    wb[:, 2] *= 1.0 - ampleur * signe
    return wb


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

    Les bornes larges datent de l'epoque ou le denominateur etait lisse et
    etalait la marche sur +/- 30 frames ; le gain sur niveau brut fait
    mieux, mais garder les bornes documente le minimum exige.
    """
    n = 600
    levels = np.concatenate([np.full(300, 60.0), np.full(300, 180.0)])
    gains = solve_corrections(levels, np.ones(n, dtype=bool))
    corrige = levels * gains
    avant, apres = corrige[50:250], corrige[350:550]
    assert abs(avant.mean() - apres.mean()) / avant.mean() < 0.10
    assert avant.std() / avant.mean() < 0.05
    assert apres.std() / apres.mean() < 0.05


def test_solve_corrections_corrige_le_flicker_d_exposition():
    """L'auto-exposition saute d'une frame a l'autre ; la sortie doit etre plate.

    C'est le defaut rapporte sur la video publiee (« the light keeps
    changing ») : des sauts REELS de niveau, frame a frame, que l'ancien
    lissage du denominateur rangeait dans le bruit de mesure et laissait
    donc passer tels quels dans la sortie.
    """
    n = 400
    levels = np.where(np.arange(n) % 2 == 0, 80.0, 120.0)
    gains = solve_corrections(levels, np.ones(n, dtype=bool))
    corrige = levels * gains
    assert corrige.std() / corrige.mean() < 0.01


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


def test_solve_couleur_stabilise_une_balance_oscillante():
    """L'oscillation de la balance auto disparait, la teinte moyenne reste.

    C'est le second defaut rapporte sur la video publiee (« the white
    balance keeps changing ») : la cible n'est PAS le neutre — le filtre
    solaire rouge l'interdit — mais la propre trajectoire de la sequence.
    """
    n = 400
    wb = _wb_oscillante(n)
    gains = solve_couleur(wb, np.ones(n, dtype=bool))
    corrige = wb * gains
    chroma = corrige / corrige.mean(axis=1, keepdims=True)
    # L'interieur seulement : aux bords, la reference est dominee par les
    # replicats du rembourrage et n'absorbe l'oscillation qu'a fenetre/2
    # frames du bord (voir track.reference_deflicker).
    interieur = chroma[16:n - 16]
    for canal in range(3):
        assert interieur[:, canal].std() / interieur[:, canal].mean() < 0.01
    # La teinte moyenne de la sequence n'a pas bouge : pas de neutralisation.
    avant = wb.mean(axis=0) / wb.mean()
    apres = corrige.mean(axis=0) / corrige.mean()
    assert np.abs(apres - avant).max() < 0.02


def test_solve_couleur_preserve_une_marche_franche():
    """Le retrait du filtre solaire est un VRAI changement, a conserver.

    Une reference lissee par moyenne glissante etalerait la marche sur la
    largeur de la fenetre et fabriquerait une rampe de fausse couleur ; la
    reference par mediane, elle, ne peut devier des plateaux que sur les
    deux frames qui encadrent la marche (voir track.reference_deflicker).
    """
    n = 400
    wb = np.tile(np.array([200.0, 60.0, 5.0]), (n, 1))
    wb[200:] = np.array([150.0, 140.0, 120.0])
    gains = solve_couleur(wb, np.ones(n, dtype=bool))
    hors_marche = np.concatenate([gains[:199], gains[201:]])
    assert np.abs(hors_marche - 1.0).max() < 1e-9
    # Les deux frames de la marche restent bornees par l'amplitude (et la
    # renormalisation de luminance), jamais au-dela.
    assert gains[199:201].max() < 1.25 * 1.10
    assert gains[199:201].min() > (1.0 / 1.25) / 1.10


def test_solve_couleur_borne_l_amplitude_de_correction():
    n = 200
    wb = np.tile(np.array([120.0, 120.0, 120.0]), (n, 1))
    wb[100] = np.array([360.0, 120.0, 40.0])    # aberration d'une frame
    gains = solve_couleur(wb, np.ones(n, dtype=bool), amplitude=0.25)
    # La correction de chroma est bornee a +/- 25 % ; la petite marge
    # au-dela couvre le rescale qui preserve la luminance de la frame.
    assert gains.max() <= 1.25 * 1.10
    assert gains.min() >= (1.0 / 1.25) / 1.10


def test_solve_couleur_rend_un_gain_unite_sans_mesure():
    n = 100
    wb = np.tile(np.array([100.0, 90.0, 80.0]), (n, 1))
    wb[40:60] = np.nan
    valid = np.ones(n, dtype=bool)
    valid[10:20] = False
    gains = solve_couleur(wb, valid)
    assert np.allclose(gains[40:60], 1.0)
    assert np.allclose(gains[10:20], 1.0)


def test_solve_couleur_sans_aucune_mesure_est_un_no_op():
    """Contrairement a solve_corrections, ne leve pas : la couleur est un
    raffinement, une sequence sans balance mesurable doit rester rendable."""
    n = 50
    gains = solve_couleur(np.full((n, 3), np.nan), np.ones(n, dtype=bool))
    assert gains.shape == (n, 3)
    assert np.allclose(gains, 1.0)


def test_solve_couleur_preserve_la_luminance_de_chaque_frame():
    """La correction de teinte ne doit pas reintroduire de flicker de niveau."""
    n = 200
    wb = _wb_oscillante(n, ampleur=0.15)
    gains = solve_couleur(wb, np.ones(n, dtype=bool))
    avant = wb.mean(axis=1)
    apres = (wb * gains).mean(axis=1)
    assert np.abs(apres / avant - 1.0).max() < 1e-9


def test_solve_corrections_ignore_les_frames_invalides():
    n = 400
    levels = np.full(n, 100.0)
    levels[200:220] = 1.0
    valid = np.ones(n, dtype=bool)
    valid[200:220] = False
    gains = solve_corrections(levels, valid)
    assert abs(gains[50] - 1.0) < 0.05
