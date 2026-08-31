import os

import numpy as np
import pytest

from eclipse.io import FrameReader
from eclipse.locate import estimate_radius, locate_center, sobel
from eclipse.quality import (measure_quality, classify, verdicts_hors_source,
                              SEUILS_DEFAUT, masse_captee, SEUIL_LUMIERE)
from tests.synth import make_frame, make_moon_frame
from tests.test_pipeline import SOURCE_REELLE


def gris(img):
    return img.astype(np.float32).mean(axis=2)


def test_le_flou_abaisse_la_nettete_du_limbe():
    net = measure_quality(gris(make_frame(w=300, h=300, center=(150.0, 150.0),
                                          r=50.0, blur=0.0)), 150.0, 150.0, 50.0)
    flou = measure_quality(gris(make_frame(w=300, h=300, center=(150.0, 150.0),
                                           r=50.0, blur=4.0)), 150.0, 150.0, 50.0)
    assert flou["limb_sharpness"] < net["limb_sharpness"] * 0.6


def test_l_assombrissement_abaisse_disk_p90():
    clair = measure_quality(gris(make_frame(w=300, h=300, center=(150.0, 150.0),
                                            r=50.0, gain=0.8)), 150.0, 150.0, 50.0)
    sombre = measure_quality(gris(make_frame(w=300, h=300, center=(150.0, 150.0),
                                             r=50.0, gain=0.1)), 150.0, 150.0, 50.0)
    assert sombre["disk_p90"] < clair["disk_p90"] * 0.4


def test_le_flare_eleve_flare_ratio():
    propre = measure_quality(gris(make_frame(w=300, h=300, center=(150.0, 150.0),
                                             r=50.0)), 150.0, 150.0, 50.0)
    parasite = measure_quality(
        gris(make_frame(w=300, h=300, center=(150.0, 150.0), r=50.0,
                        flare=(250.0, 60.0, 40.0, 0.5))), 150.0, 150.0, 50.0)
    assert parasite["flare_ratio"] > propre["flare_ratio"] * 3.0


def test_measure_quality_sur_un_centre_nan():
    m = measure_quality(np.zeros((100, 100), np.float32),
                        float("nan"), float("nan"), 20.0)
    assert np.isnan(m["disk_p90"])


def _sequence_nominale(n=600):
    return (np.full(n, 200.0), np.full(n, 30.0), np.full(n, 0.05), np.full(n, 0.5))


def test_classify_conserve_une_sequence_nominale():
    p90, sharp, flare, conf = _sequence_nominale()
    assert all(v is None for v in classify(p90, sharp, flare, conf))


def test_classify_rejette_une_chute_de_luminance_transitoire():
    p90, sharp, flare, conf = _sequence_nominale()
    p90[300:320] = 40.0                      # 20% du niveau local
    verdicts = classify(p90, sharp, flare, conf)
    assert all(v == "too_dark" for v in verdicts[300:320])
    assert verdicts[100] is None


def test_classify_rejette_une_perte_durable_par_le_plancher_absolu():
    """La mediane glissante finit par suivre une chute durable ; le plancher non."""
    n = 600
    p90 = np.full(n, 200.0)
    p90[400:] = 15.0
    sharp, flare, conf = np.full(n, 30.0), np.full(n, 0.05), np.full(n, 0.5)
    verdicts = classify(p90, sharp, flare, conf)
    assert all(v == "too_dark" for v in verdicts[500:])


def test_classify_rejette_le_flou():
    p90, sharp, flare, conf = _sequence_nominale()
    sharp[200:210] = 5.0
    verdicts = classify(p90, sharp, flare, conf)
    assert all(v == "motion_blur" for v in verdicts[200:210])


def test_classify_rejette_le_flare():
    p90, sharp, flare, conf = _sequence_nominale()
    flare[150:160] = 0.9
    verdicts = classify(p90, sharp, flare, conf)
    assert all(v == "flare" for v in verdicts[150:160])


def test_classify_rejette_la_perte_de_lock():
    p90, sharp, flare, conf = _sequence_nominale()
    conf[50:60] = 0.0
    verdicts = classify(p90, sharp, flare, conf)
    assert all(v == "no_lock" for v in verdicts[50:60])


def test_classify_tolere_un_changement_de_regime_de_nettete():
    """La nettete passe de 13 a 150 a la frame 1050 dans la vraie video.

    Aucune de ces frames n'est floue : la reference locale doit absorber
    le changement de regime.
    """
    n = 2000
    sharp = np.concatenate([np.full(1050, 13.0), np.full(n - 1050, 150.0)])
    p90, flare, conf = np.full(n, 200.0), np.full(n, 0.05), np.full(n, 0.5)
    verdicts = classify(p90, sharp, flare, conf)
    rejets = sum(1 for v in verdicts if v == "motion_blur")
    assert rejets < n * 0.02


def test_classify_supprime_les_ilots_quand_le_seuil_est_actif():
    """Un ilot de 2 frames entre deux longues plages rejetees fait saccader.

    Le mecanisme est devenu opt-in : ilot_min vaut 1 par defaut depuis la
    revue humaine (l'utilisateur a repris 29 ilots sur 29), donc le test
    l'active explicitement.
    """
    n = 600
    p90 = np.full(n, 200.0)
    p90[200:260] = 10.0
    p90[230:232] = 200.0          # ilot de 2 frames au milieu du trou
    sharp, flare, conf = np.full(n, 30.0), np.full(n, 0.05), np.full(n, 0.5)
    verdicts = classify(p90, sharp, flare, conf,
                        dict(SEUILS_DEFAUT, ilot_min=5))
    assert verdicts[230] == "ilot"
    assert verdicts[231] == "ilot"


def test_classify_conserve_les_ilots_par_defaut():
    """Par defaut aucun ilot n'est supprime : la revue humaine a montre que
    les ilots observes etaient des artefacts de rejets trop stricts, et
    qu'une frame isolee entre deux coupes est visuellement bienvenue."""
    n = 600
    p90 = np.full(n, 200.0)
    p90[200:260] = 10.0
    p90[230:232] = 200.0
    sharp, flare, conf = np.full(n, 30.0), np.full(n, 0.05), np.full(n, 0.5)
    verdicts = classify(p90, sharp, flare, conf)
    assert verdicts[230] is None
    assert verdicts[231] is None


def test_les_seuils_sont_surchargeables():
    p90, sharp, flare, conf = _sequence_nominale()
    p90[300:320] = 100.0                     # 50% du niveau local
    assert classify(p90, sharp, flare, conf)[310] is None
    seuils = dict(SEUILS_DEFAUT, dark_rel=0.60)
    assert classify(p90, sharp, flare, conf, seuils)[310] == "too_dark"


def test_hors_source_accepte_un_disque_entier():
    assert verdicts_hors_source([540.], [960.], 399., 1080, 1920, 25.) == [None]


def test_hors_source_accepte_le_soleil_couchant():
    """Le centre descend a 1504 px : l'ancien critere de fenetre le rejetait."""
    assert verdicts_hors_source([540.], [1504.], 399., 1080, 1920, 25.) == [None]


def test_hors_source_rejette_un_disque_trop_ampute():
    assert verdicts_hors_source([100.], [960.], 399., 1080, 1920, 25.) == ["hors_source"]


def test_hors_source_respecte_la_tolerance():
    """A 380 px du bord, 19 px du disque manquent : accepte a 25, refuse a 10."""
    assert verdicts_hors_source([380.], [960.], 399., 1080, 1920, 25.) == [None]
    assert verdicts_hors_source([380.], [960.], 399., 1080, 1920, 10.) == ["hors_source"]


def test_hors_source_rejette_une_mesure_absente():
    v = verdicts_hors_source([float("nan")], [float("nan")], 399., 1080, 1920, 25.)
    assert v == ["hors_source"]


def test_masse_captee_disque_centre():
    """Un disque entier sous le masque : toute la lumiere est capturee."""
    img = gris(make_frame(w=300, h=300, center=(150.0, 150.0), r=50.0))
    assert masse_captee(img, 150.0, 150.0, 50.0) > 0.99


def test_masse_captee_centre_faux():
    """Masque place a deux rayons du disque : il ne capture rien."""
    img = gris(make_frame(w=300, h=300, center=(150.0, 150.0), r=50.0))
    assert masse_captee(img, 150.0 + 120.0, 150.0, 50.0) < 0.05


def test_masse_captee_lumiere_plus_large_que_le_masque():
    """Cas mesure sur la sequence reelle : la zone lumineuse est un ciel
    crepusculaire ou un nuage eclaire, plus large que le Soleil. Le masque,
    meme bien place, n'en capture qu'une part."""
    img = gris(make_frame(w=300, h=300, center=(150.0, 150.0), r=140.0))
    assert masse_captee(img, 150.0, 150.0, 50.0) < 0.5


def test_masse_captee_centre_non_fini():
    img = gris(make_frame(w=100, h=100, center=(50.0, 50.0), r=20.0))
    assert np.isnan(masse_captee(img, float("nan"), 50.0, 20.0))


def test_masse_captee_image_noire_ne_leve_pas():
    assert np.isnan(masse_captee(np.zeros((80, 80), np.float32), 40.0, 40.0, 20.0))


def test_masse_captee_light_threshold_is_a_parameter():
    """At the default 0.35 x max, the umbral part of a half-shadowed moon
    does not count as light; at 0.10 it does, and a correct center then
    captures almost everything."""
    img = make_moon_frame(w=200, h=200, center=(100.0, 100.0), r=50.0,
                          umbra=0.5, umbra_level=0.15)
    g = img.astype(np.float32).mean(axis=2)
    assert masse_captee(g, 100.0, 100.0, 50.0, seuil_lumiere=0.10) > 0.95


def test_masse_captee_est_bornee():
    """Quel que soit le placement, la fraction reste dans [0, 1]."""
    img = gris(make_frame(w=200, h=200, center=(100.0, 100.0), r=40.0))
    for dx in (0.0, 30.0, 90.0, 200.0):
        v = masse_captee(img, 100.0 + dx, 100.0, 40.0)
        assert np.isnan(v) or 0.0 <= v <= 1.0


# --- Oracles pre-optimisation, figes ici pour la comparaison de non-regression
# numerique (tache perf/calcul-par-frame). Ne pas les faire evoluer avec le
# code de production : ils doivent rester ce que measure_quality et
# masse_captee faisaient AVANT le passage a la grille diffusee, aux distances
# au carre et a la racine unique sur le percentile.

def _measure_quality_oracle(gray, cx, cy, r):
    if not (np.isfinite(cx) and np.isfinite(cy)):
        return {"disk_p90": float("nan"), "limb_sharpness": float("nan"),
                "flare_ratio": float("nan")}
    g = gray.astype(np.float32)
    h, w = g.shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    interieur = dist <= r
    disk_p90 = float(np.percentile(g[interieur], 90.0)) if interieur.any() else 0.0
    anneau = (dist >= 0.85 * r) & (dist <= 1.15 * r)
    if anneau.any() and disk_p90 > 1e-6:
        gx, gy = sobel(g)
        mag = np.sqrt(gx * gx + gy * gy)
        # 98 et non 90 : voir quality.measure_quality. L'oracle prouve
        # l'equivalence de la forme optimisee a la forme naive, il doit donc
        # suivre le percentile employe -- il ne fige pas ce choix, qui est
        # garde par test_le_percentile_atteint_le_limbe.
        limb_sharpness = float(np.percentile(mag[anneau], 98.0)) / disk_p90 * 100.0
    else:
        limb_sharpness = 0.0
    dehors = dist > 1.4 * r
    if dehors.any() and disk_p90 > 1e-6:
        flare_ratio = float(g[dehors].mean()) / disk_p90
    else:
        flare_ratio = 0.0
    return {"disk_p90": disk_p90, "limb_sharpness": limb_sharpness,
            "flare_ratio": flare_ratio}


def _masse_captee_oracle(gray, cx, cy, r):
    if not (np.isfinite(cx) and np.isfinite(cy)):
        return float("nan")
    g = np.asarray(gray, dtype=np.float32)
    pic = float(g.max())
    if pic <= 1e-6:
        return float("nan")
    lumiere = g > SEUIL_LUMIERE * pic
    masse = float(g[lumiere].sum())
    if masse <= 0.0:
        return float("nan")
    h, w = g.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    dedans = ((xx - float(cx)) ** 2 + (yy - float(cy)) ** 2) <= float(r) ** 2
    return float(g[lumiere & dedans].sum()) / masse


# Une frame reelle sur PAS_ECHANTILLONNAGE, sur toute la sequence (~2556
# frames), au lieu des 40 premieres : la revue a montre que le debut de la
# video est sa portion la plus favorable (disque le plus grand, anneau le
# mieux peuple) et masquait des ecarts mesures ailleurs (jusqu'a 1,07e-05 sur
# limb_sharpness vers la frame 940, avant correction). 60 donne ~43 frames
# pour un temps de decodage comparable (ffmpeg doit neanmoins parcourir
# sequentiellement toute la sequence, faute d'API de seek exact dans
# eclipse.io ; mesure sur cette machine : ~45 s pour les 2556 frames).
PAS_ECHANTILLONNAGE = 60


def _echantillon_video_reelle(pas=PAS_ECHANTILLONNAGE, width=540, height=960):
    grays = []
    with FrameReader(SOURCE_REELLE, width=width, height=height) as reader:
        for i, frame in enumerate(reader):
            if i % pas == 0:
                grays.append(frame.astype(np.float32).mean(axis=2))
    return grays


@pytest.mark.skipif(not os.path.isfile(SOURCE_REELLE),
                    reason="video reelle absente (data/ est gitignore)")
def test_measure_quality_et_masse_captee_equivalent_a_l_oracle_sur_video_reelle():
    """Preuve d'equivalence numerique de l'optimisation contre
    l'implementation pre-optimisation (oracle ci-dessus), sur des frames
    reellement decodees, echantillonnees sur TOUTE la sequence plutot que sur
    son seul debut (voir PAS_ECHANTILLONNAGE ci-dessus).

    540x960 : memes dimensions que le profilage cite dans les docstrings de
    measure_quality/masse_captee (65,1 ms/frame -> 27,3 ms au total avant la
    revue ; voir rapport-perf.md pour le chiffre re-mesure apres correction).
    """
    grays = _echantillon_video_reelle()
    assert len(grays) >= 30, "echantillon trop petit pour etre representatif"

    r = estimate_radius(iter(grays))

    ecarts = {"disk_p90": [], "limb_sharpness": [], "flare_ratio": [], "masse_captee": []}
    for g in grays:
        cx, cy, _conf = locate_center(g, r)

        attendu = _measure_quality_oracle(g, cx, cy, r)
        obtenu = measure_quality(g, cx, cy, r)
        for cle in ("disk_p90", "limb_sharpness", "flare_ratio"):
            a, o = attendu[cle], obtenu[cle]
            if np.isnan(a) or np.isnan(o):
                assert np.isnan(a) and np.isnan(o)
                continue
            ecarts[cle].append(abs(o - a) / max(abs(a), 1e-9))

        m_attendu = _masse_captee_oracle(g, cx, cy, r)
        m_obtenu = masse_captee(g, cx, cy, r)
        if np.isnan(m_attendu) or np.isnan(m_obtenu):
            assert np.isnan(m_attendu) and np.isnan(m_obtenu)
        else:
            ecarts["masse_captee"].append(
                abs(m_obtenu - m_attendu) / max(abs(m_attendu), 1e-9))

    # Depuis la correction de la revue (racine appliquee au sous-ensemble
    # mag2[anneau] AVANT le percentile, masques reconstruits sur
    # dist = sqrt(d2) et non d2 <= r*r), l'operation est bit-exacte : egalite
    # stricte, pas de tolerance. Si une valeur non nulle apparaissait ici, ce
    # serait un signal reel a rapporter, pas une bornre a elargir.
    for nom, valeurs in ecarts.items():
        if valeurs:
            assert max(valeurs) == 0.0, f"{nom}: ecart relatif {max(valeurs)}"


def test_measure_quality_et_masse_captee_equivalent_a_l_oracle_sur_fixture_synthetique():
    """Meme preuve d'equivalence que le test video reelle ci-dessus, mais sur
    une fixture synthetique qui tourne partout (data/ est gitignore, donc le
    test reel se saute sur toute machine sans la video source).

    Reprend le pouvoir discriminant verifie par la revue sur le test reel
    (mutation testing : rayon non mis au carre -> 2,1e-02 d'ecart, racine
    oubliee -> 15,5) en construisant un disque avec un anneau de taille et de
    texture comparables (bruit + gradient radial, pour peupler l'anneau de
    gradients non triviaux plutot que d'un plateau constant).
    """
    w, h = 300, 300
    cx, cy, r = 150.0, 150.0, 60.0
    rng = np.random.default_rng(20260821)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    # Degrade radial + bruit : un plateau uniforme donnerait un gradient nul
    # partout et rendrait le test aveugle a une erreur de racine ou de seuil.
    base = np.clip(220.0 - dist * 1.5, 0.0, 255.0).astype(np.float32)
    bruit = rng.normal(0.0, 8.0, size=(h, w)).astype(np.float32)
    gray = np.clip(base + bruit, 0.0, 255.0)

    attendu = _measure_quality_oracle(gray, cx, cy, r)
    obtenu = measure_quality(gray, cx, cy, r)
    for cle in ("disk_p90", "limb_sharpness", "flare_ratio"):
        assert not np.isnan(attendu[cle]) and not np.isnan(obtenu[cle]), cle
        ecart = abs(obtenu[cle] - attendu[cle]) / max(abs(attendu[cle]), 1e-9)
        assert ecart == 0.0, f"{cle}: ecart relatif {ecart}"

    m_attendu = _masse_captee_oracle(gray, cx, cy, r)
    m_obtenu = masse_captee(gray, cx, cy, r)
    assert not np.isnan(m_attendu) and not np.isnan(m_obtenu)
    assert abs(m_obtenu - m_attendu) == 0.0


def _disque(w, h, cx, cy, r, doux):
    """Disque clair sur fond sombre, limbe plus ou moins doux.

    doux = largeur de la transition en px. Le fond porte un bruit faible :
    sans lui l'anneau serait un plateau de gradient nul et le test ne
    distinguerait plus le limbe de son voisinage, ce qui est precisement ce
    qu'il doit mesurer.
    """
    rng = np.random.default_rng(20260827)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    profil = np.clip(0.5 - (dist - r) / max(doux, 1e-6), 0.0, 1.0)
    return np.clip(20.0 + 200.0 * profil
                   + rng.normal(0.0, 2.0, size=(h, w)), 0.0, 255.0)


def test_le_percentile_atteint_le_limbe():
    """limb_sharpness doit voir le LIMBE, pas le fond de l'anneau.

    L'anneau [0,85r ; 1,15r] est large de 0,3 r ; une transition de limbe
    fait quelques pixels. Le limbe n'y pese donc que quelques pour cent --
    mesure sur la sequence reelle : 1,7 a 2,2 %, soit le 98e percentile. Un
    percentile trop bas ne l'atteint jamais et mesure le fond : il rendait 7
    a 17 la ou la mediane des pixels de limbe vaut 140 a 256, et rejetait
    pour « flou » les neuf frames les plus nettes de la sequence.

    Le test compare un limbe net a un limbe flou. Si le percentile rate le
    limbe, les deux rendent la meme chose et le rapport tombe a 1.
    """
    w = h = 400
    cx = cy = 200.0
    r = 120.0
    net = measure_quality(_disque(w, h, cx, cy, r, 1.0), cx, cy, r)
    flou = measure_quality(_disque(w, h, cx, cy, r, 12.0), cx, cy, r)
    assert net["limb_sharpness"] > 2.0 * flou["limb_sharpness"], (
        f"net {net['limb_sharpness']:.2f} contre flou "
        f"{flou['limb_sharpness']:.2f} : le percentile n'atteint pas le limbe")


def test_un_niveau_aberrant_est_ecarte():
    """Une pointe de luminance d'une frame doit etre rejetee.

    La correction de gain (photometry.solve_corrections) lisse la courbe de
    niveau avant de l'inverser, volontairement : elle laisse donc passer une
    pointe isolee. Mesure sur la sequence reelle : 189 % de saut entre deux
    frames conservees avant correction, 188,7 % apres. C'est au tri de
    l'ecarter.
    """
    n = 101
    level = np.full(n, 100.0)
    level[50] = 36.0                       # la frame 184 de la sequence reelle
    neutre = dict(disk_p90=np.full(n, 100.0),
                  limb_sharpness=np.full(n, 50.0),
                  flare_ratio=np.zeros(n),
                  confiance=np.full(n, 0.5))
    sans = classify(**neutre)
    avec = classify(**neutre, level=level)
    assert sans[50] is None, "sans level, rien ne doit changer"
    assert avec[50] == "niveau_aberrant"
    assert all(v is None for i, v in enumerate(avec) if i != 50), (
        "seule la frame aberrante doit etre ecartee")


def test_un_palier_d_exposition_n_est_pas_une_aberration():
    """L'exposition automatique du smart-telescope change par paliers francs
    -- 99,7 puis 156 puis 209 autour de la frame 1085 de la sequence de
    reference. Un palier n'est pas un defaut : la reference courte (31) doit
    le suivre."""
    n = 201
    level = np.concatenate([np.full(100, 100.0), np.full(101, 205.0)])
    v = classify(disk_p90=np.full(n, 100.0), limb_sharpness=np.full(n, 50.0),
                 flare_ratio=np.zeros(n), confiance=np.full(n, 0.5),
                 level=level)
    assert all(x is None for x in v), (
        f"un palier a ete pris pour une aberration : {set(x for x in v if x)}")
