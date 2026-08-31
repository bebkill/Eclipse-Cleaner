import numpy as np
from tests.synth import make_frame, make_moon_frame, make_totality_frame


def test_disque_plein_a_l_aire_attendue():
    """phase=0 doit produire un disque solaire complet de rayon r."""
    img = make_frame(w=200, h=200, center=(100.0, 100.0), r=40.0, phase=0.0)
    gray = img.astype(np.float32).mean(axis=2)
    aire = int((gray > gray.max() * 0.5).sum())
    attendu = np.pi * 40.0**2
    assert abs(aire - attendu) / attendu < 0.02


def test_centroide_du_disque_plein_est_le_centre_demande():
    img = make_frame(w=200, h=200, center=(87.0, 113.0), r=35.0, phase=0.0)
    gray = img.astype(np.float32).mean(axis=2)
    ys, xs = np.nonzero(gray > gray.max() * 0.5)
    assert abs(xs.mean() - 87.0) < 0.5
    assert abs(ys.mean() - 113.0) < 0.5


def test_la_phase_reduit_la_surface_eclairee():
    """Plus la phase monte, moins il reste de surface eclairee."""
    aires = []
    for phase in (0.0, 0.3, 0.6, 0.9):
        img = make_frame(w=200, h=200, center=(100.0, 100.0), r=40.0, phase=phase)
        gray = img.astype(np.float32).mean(axis=2)
        aires.append(int((gray > gray.max() * 0.5).sum()))
    assert aires[0] > aires[1] > aires[2] > aires[3]
    assert aires[3] < aires[0] * 0.25


def test_le_gain_eleve_la_luminance():
    sombre = make_frame(w=100, h=100, center=(50.0, 50.0), r=20.0, gain=0.4)
    clair = make_frame(w=100, h=100, center=(50.0, 50.0), r=20.0, gain=0.9)
    assert clair.astype(np.float32).mean() > sombre.astype(np.float32).mean() * 1.5


def test_la_balance_teinte_les_canaux():
    img = make_frame(w=100, h=100, center=(50.0, 50.0), r=20.0, wb=(1.0, 0.6, 0.3))
    f = img.astype(np.float32)
    disque = f.reshape(-1, 3)[f.mean(axis=2).reshape(-1) > 40]
    assert disque[:, 0].mean() > disque[:, 1].mean() > disque[:, 2].mean()


def test_le_flou_adoucit_le_limbe():
    """Le gradient maximal chute quand la frame est floue."""
    def grad_max(img):
        g = img.astype(np.float32).mean(axis=2)
        return float(np.abs(np.diff(g, axis=1)).max())

    net = make_frame(w=200, h=200, center=(100.0, 100.0), r=40.0, blur=0.0)
    flou = make_frame(w=200, h=200, center=(100.0, 100.0), r=40.0, blur=4.0)
    assert grad_max(flou) < grad_max(net) * 0.6


def test_le_bord_est_antialiase():
    """Un bord dur casserait la precision sous-pixel des tests de locate."""
    img = make_frame(w=200, h=200, center=(100.0, 100.0), r=40.0)
    g = img.astype(np.float32).mean(axis=2)
    ligne = g[100]
    intermediaires = ((ligne > g.max() * 0.15) & (ligne < g.max() * 0.85)).sum()
    assert intermediaires >= 2


def test_la_sortie_est_uint8_rgb():
    img = make_frame(w=64, h=48, center=(32.0, 24.0), r=10.0)
    assert img.shape == (48, 64, 3)
    assert img.dtype == np.uint8


def test_moon_frame_has_a_lit_and_an_umbral_part():
    img = make_moon_frame(w=200, h=200, center=(100.0, 100.0), r=50.0,
                          umbra=0.5, umbra_level=0.15)
    g = img.astype(np.float32).mean(axis=2)
    # Lit half bright, umbral half dim but NOT black (it must stay above
    # the sky so a max-relative threshold can still see the full disc).
    assert g[100, 130] > 100.0            # lit side
    assert 5.0 < g[100, 70] < 60.0        # umbral side: dim, present
    assert g[100, 5] < 3.0                # sky stays black


def test_moon_frame_umbra_is_reddened():
    img = make_moon_frame(w=200, h=200, center=(100.0, 100.0), r=50.0,
                          umbra=0.6, umbra_level=0.2)
    r_, b_ = float(img[100, 70, 0]), float(img[100, 70, 2])
    assert r_ > 1.5 * max(b_, 1.0)


def test_totality_frame_is_dark_inside_bright_ring():
    img = make_totality_frame(w=200, h=200, center=(100.0, 100.0), r=50.0,
                              corona=0.5)
    g = img.astype(np.float32).mean(axis=2)
    assert g[100, 100] < 8.0                       # dark disc
    assert g[100, 100 + 55] > 40.0                 # corona just outside
    assert g[5, 5] < 2.0                           # far sky ~ black
