import numpy as np
from tests.synth import make_frame


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
