import json
import os
import numpy as np
import pytest

from eclipse.io import FrameWriter, FrameReader, probe
from eclipse.pipeline import (SCHEMA_VERSION, analyze, render, charger_cache, main,
                              SEUIL_MASQUE_DEFAUT, TOLERANCE_BORD_DEFAUT,
                              tailles_defaut)
from eclipse.presets import analysis_params
from tests.synth import make_frame, make_moon_frame, make_totality_frame

# Ancre sur la racine du depot, et non sur le repertoire courant : sinon le
# test de fumee se sauterait silencieusement selon l'endroit d'ou pytest est
# lance.
_RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_REELLE = os.path.join(_RACINE, "data",
                             "2026-08-12-192459-Solar-timelapse.mp4")


@pytest.fixture
def video_synthetique(tmp_path):
    """40 frames : derive du disque, 5 frames noires, 5 frames floues."""
    chemin = tmp_path / "src.mp4"
    with FrameWriter(str(chemin), width=120, height=200, fps=30.0) as w:
        for i in range(40):
            cx = 40.0 + i * 1.0        # derive franche
            cy = 100.0 + i * 0.5
            if 10 <= i < 15:
                frame = make_frame(w=120, h=200, center=(cx, cy), r=25.0, gain=0.02)
            elif 25 <= i < 30:
                frame = make_frame(w=120, h=200, center=(cx, cy), r=25.0, blur=5.0)
            else:
                frame = make_frame(w=120, h=200, center=(cx, cy), r=25.0)
            w.write(frame)
    return str(chemin)


def test_tailles_defaut_reproduit_le_calibrage_de_reference():
    """Sur la source de reference (1080x1920), les defauts derives doivent
    redonner exactement le calibrage mesure : fenetre 840x1494, sortie aux
    dimensions de la source. C'est ce qui garantit qu'aucun rendu existant
    ne change avec le passage aux defauts adaptatifs."""
    assert tailles_defaut(1080, 1920) == ((840, 1494), (1080, 1920))


def test_tailles_defaut_tient_dans_toute_source():
    """La fenetre par defaut doit etre paire et strictement contenue dans la
    source, paysage comme portrait : une fenetre plus grande que la source
    inverserait le corridor de planifie_trajectoire."""
    for src in ((1920, 1080), (1080, 1920), (1280, 720), (3840, 2160),
                (640, 480), (120, 200)):
        (w, h), (sw, sh) = tailles_defaut(*src)
        assert (sw, sh) == src
        assert w % 2 == 0 and h % 2 == 0
        assert 2 <= w <= src[0] and 2 <= h <= src[1]
        # Le rapport de la fenetre suit celui de la source a moins de 0,5 %
        # pres (l'arrondi au pixel pair interdit l'exactitude sur les
        # petites sources) : c'est le seuil d'ellipticite que render()
        # controle avant d'encoder, l'avertissement ne doit jamais se
        # declencher sur les defauts.
        assert abs((w / h) / (src[0] / src[1]) - 1.0) < 5e-3


def test_tolerance_bord_par_defaut_est_5_px():
    """Verrouille la constante mesuree. Passee de 25 a 5 px : sur un disque
    de 799 px de diametre, 25 px etaient 3 % du diametre — visibles. A 5 px
    (0,6 %) la coupe reste invisible.

    Et le gain ne se paye pas en coupures : la plus longue coupure de la
    sequence reelle tombe de 110 a 51 frames, parce que le grand trou est
    fait de frames rognees de 23,6 px en mediane, pas masquees. Une exigence
    stricte a 0 px la ramenerait a 110."""
    assert TOLERANCE_BORD_DEFAUT == 5.0


def test_seuil_masque_par_defaut_est_0_80():
    """Verrouille la constante mesuree : voir le docstring de
    SEUIL_MASQUE_DEFAUT (mediane 0,997, echecs francs tous sous 0,50)."""
    assert SEUIL_MASQUE_DEFAUT == 0.80


def test_analyze_ecrit_un_cache_complet(video_synthetique, tmp_path):
    cache = str(tmp_path / "a.json")
    donnees = analyze(video_synthetique, cache, scale=1.0)
    assert donnees["schema"] == SCHEMA_VERSION
    assert len(donnees["frames"]) == 40
    assert donnees["radius"] > 0
    with open(cache) as f:
        relu = json.load(f)
    assert len(relu["frames"]) == 40
    for cle in ("cx", "cy", "conf", "disk_p90", "limb_sharpness",
                "flare_ratio", "masse_captee", "level", "wb"):
        assert cle in relu["frames"][0]


def test_cache_carries_the_preset(video_synthetique, tmp_path):
    cache = str(tmp_path / "a.json")
    analyze(video_synthetique, cache, scale=1.0, preset="custom")
    d = json.load(open(cache, encoding="utf-8"))
    assert d["schema"] == 7 == SCHEMA_VERSION
    assert d["preset"] == "custom"
    assert d["analysis_params"] == analysis_params("custom")
    assert all(f["regime"] in ("bright", "dark") for f in d["frames"])


def test_render_refuses_a_cache_from_another_preset(video_synthetique, tmp_path):
    cache = str(tmp_path / "a.json")
    analyze(video_synthetique, cache, scale=1.0, preset="custom")
    with pytest.raises(ValueError, match="preset"):
        render(video_synthetique, str(tmp_path / "out.mp4"), cache,
               preset="moon")


def test_analyze_moon_preset_scans_the_radius(tmp_path):
    """A sequence of half-shadowed moons: area mode underestimates, the
    moon preset must land on the true radius."""
    chemin = str(tmp_path / "moon.mp4")
    with FrameWriter(chemin, width=270, height=480, fps=30.0) as w:
        for i in range(30):
            w.write(make_moon_frame(w=270, h=480,
                                    center=(135.0 + i * 0.2, 240.0),
                                    r=97.0, umbra=0.5, umbra_level=0.15))
    cache = str(tmp_path / "a.json")
    analyze(chemin, cache, scale=1.0, preset="moon")
    d = json.load(open(cache, encoding="utf-8"))
    assert abs(d["radius"] - 97.0) < 3.0
    assert d["preset"] == "moon"


def test_run_refuses_to_reuse_a_cache_from_another_preset(video_synthetique,
                                                          tmp_path, capsys):
    """The run branch checks the preset BEFORE announcing the reuse.

    The refusal itself proves nothing about WHERE the check lives: render()
    raises the very same ValueError (both go through
    pipeline._verifie_preset), so deleting the run-branch check would still
    give exit code 1 and the same stderr. What distinguishes the early check
    is the stdout: run must not print « Cache valide reutilise » for a cache
    it is about to refuse. That absence is the real assertion here.
    """
    cache = str(tmp_path / "a.json")
    analyze(video_synthetique, cache, scale=1.0, preset="custom")
    capsys.readouterr()                       # jette la sortie de l'analyse
    code = main(["run", video_synthetique, str(tmp_path / "o.mp4"),
                 "--cache", cache, "--taille", "60x100",
                 "--preset", "moon", "--processus", "1"])
    assert code == 1
    capture = capsys.readouterr()
    assert "preset" in capture.err and "--preset moon" in capture.err
    assert "Cache valide reutilise" not in capture.out


def test_run_refuses_to_reuse_a_cache_with_another_light_threshold(
        video_synthetique, tmp_path, capsys):
    """Name concordance is not enough: the resolved PARAMETERS must agree.

    --seuil-lumiere is a pass-1 parameter (quality.masse_captee): a cache
    measured at the preset's own cut says something else than one measured
    at 0.90. Reusing it would sort and frame the sequence against measures
    the flag claims to have changed -- silently, since the preset NAME
    still matches. Same shape as the preset refusal: exit 1, the reason on
    stderr, and no « Cache valide reutilise » on stdout.
    """
    cache = str(tmp_path / "a.json")
    analyze(video_synthetique, cache, scale=1.0, preset="custom")
    capsys.readouterr()                       # jette la sortie de l'analyse
    code = main(["run", video_synthetique, str(tmp_path / "o.mp4"),
                 "--cache", cache, "--taille", "60x100",
                 "--preset", "custom", "--seuil-lumiere", "0.9",
                 "--processus", "1"])
    assert code == 1
    capture = capsys.readouterr()
    assert "--seuil-lumiere 0.9" in capture.err
    assert "Cache valide reutilise" not in capture.out


def test_run_reuses_a_cache_whose_light_threshold_matches(video_synthetique,
                                                          tmp_path, capsys):
    """An EQUAL value is not a discordance: the cache still serves."""
    cache = str(tmp_path / "a.json")
    analyze(video_synthetique, cache, scale=1.0, preset="custom",
            seuil_lumiere=0.5)
    capsys.readouterr()
    code = main(["run", video_synthetique, str(tmp_path / "o.mp4"),
                 "--cache", cache, "--taille", "60x100",
                 "--preset", "custom", "--seuil-lumiere", "0.5",
                 "--processus", "1"])
    assert code == 0
    assert "Cache valide reutilise" in capsys.readouterr().out


def test_cli_preset_flag_reaches_the_cache(video_synthetique, tmp_path, capsys):
    cache = str(tmp_path / "a.json")
    assert main(["analyze", video_synthetique, "--cache", cache,
                 "--preset", "custom", "--processus", "1"]) == 0
    assert json.load(open(cache, encoding="utf-8"))["preset"] == "custom"


def test_analyze_without_preset_announces_the_detection(tmp_path, capsys):
    """umbra_level 0.25, not 0.15: the synthetic umbra must be as dark as a
    real one, no darker. Measured on the three real lunar videos (task 11),
    the shadowed part of the disc sits at 10-40 % of the frame peak -- median
    0.165 on Lunar-221924, 0.092 on Lunar-213307. An umbra_level of 0.15 puts
    the synthetic gray at ~8.4 % of the peak, below anything measured, and
    below detect.DIM_FRACTION: the umbra then never leaves the "dim" bucket
    and the frame stops reading as a moon at all. Same value, for the same
    reason, as the moon frame in test_detect.py.
    """
    chemin = str(tmp_path / "moon.mp4")
    with FrameWriter(chemin, width=270, height=480, fps=30.0) as w:
        for u in (0.2, 0.4, 0.6, 0.8) * 3:
            w.write(make_moon_frame(w=270, h=480, center=(135.0, 240.0),
                                    r=97.0, umbra=u, umbra_level=0.25))
    cache = str(tmp_path / "a.json")
    assert main(["analyze", chemin, "--cache", cache,
                 "--processus", "1"]) == 0
    assert "detecte : moon" in capsys.readouterr().out
    assert json.load(open(cache, encoding="utf-8"))["preset"] == "moon"


def test_charger_cache_rejette_un_schema_perime(video_synthetique, tmp_path):
    cache = str(tmp_path / "a.json")
    analyze(video_synthetique, cache, scale=1.0)
    with open(cache) as f:
        d = json.load(f)
    d["schema"] = SCHEMA_VERSION + 1
    with open(cache, "w") as f:
        json.dump(d, f)
    assert charger_cache(cache, video_synthetique) is None


def test_charger_cache_rejette_une_source_differente(video_synthetique, tmp_path):
    cache = str(tmp_path / "a.json")
    analyze(video_synthetique, cache, scale=1.0)
    with open(cache) as f:
        d = json.load(f)
    d["source"]["size"] += 1
    with open(cache, "w") as f:
        json.dump(d, f)
    assert charger_cache(cache, video_synthetique) is None


def test_render_ecarte_les_frames_noires_et_floues(video_synthetique, tmp_path):
    cache = str(tmp_path / "a.json")
    sortie = str(tmp_path / "out.mp4")
    analyze(video_synthetique, cache, scale=1.0)
    # taille explicite : la fixture (120x200) est plus petite que
    # TAILLE_DEFAUT (840x1494) ; la fenetre butee n'aurait alors plus de bord
    # source ou s'arreter (out_w/2 > src_w - out_w/2) et deborderait quand
    # meme, de facon degeneree.
    # interp_max=0 : les deux coupes (5 frames noires, 5 floues) sont assez
    # courtes pour etre comblees par defaut, ce qui masquerait le rejet dans
    # le compte de frames gardees. Ce test verifie le rejet, pas le comblage
    # (voir test_render_interpole_les_courtes_coupes pour ce dernier).
    stats = render(video_synthetique, sortie, cache, taille=(60, 100),
                  interp_max=0)
    assert stats["gardees"] < 40
    assert stats["rejetees"] >= 5
    assert os.path.isfile(sortie)


def test_render_produit_le_nombre_de_frames_annonce(video_synthetique, tmp_path):
    cache = str(tmp_path / "a.json")
    sortie = str(tmp_path / "out.mp4")
    analyze(video_synthetique, cache, scale=1.0)
    stats = render(video_synthetique, sortie, cache, taille=(60, 100))
    with FrameReader(sortie) as r:
        assert len(list(r)) == stats["gardees"]


def test_render_interpole_les_courtes_coupes(video_synthetique, tmp_path):
    """Les frames rejetees isolees doivent etre comblees, pas sautees."""
    cache = str(tmp_path / "a.json")
    sortie = str(tmp_path / "out.mp4")
    analyze(video_synthetique, cache, scale=1.0)
    sans = render(video_synthetique, sortie, cache, taille=(60, 100),
                  taille_sortie=(60, 100), interp_max=0)
    avec = render(video_synthetique, str(tmp_path / "out2.mp4"), cache,
                  taille=(60, 100), taille_sortie=(60, 100), interp_max=8)
    assert avec["interpolees"] > 0
    assert avec["gardees"] == sans["gardees"] + avec["interpolees"]


def test_render_sort_a_la_taille_finale(video_synthetique, tmp_path):
    cache = str(tmp_path / "a.json")
    sortie = str(tmp_path / "out.mp4")
    analyze(video_synthetique, cache, scale=1.0)
    render(video_synthetique, sortie, cache, taille=(60, 100),
           taille_sortie=(120, 200))
    info = probe(sortie)
    assert (info["width"], info["height"]) == (120, 200)


@pytest.mark.parametrize("scale", [1.0, 0.5])
def test_render_verrouille_le_disque_au_centre(video_synthetique, tmp_path, scale):
    """La verification qui compte : la derive doit avoir disparu.

    Parametre sur l'echelle pour exercer, en plus de scale=1.0, le chemin
    scale=0.5 de bout en bout : c'est le defaut de production, et aucun
    autre test ne l'exerce.

    Ce test ne distingue pas le bon facteur de conversion analyse -> pleine
    resolution (rapport exact des dimensions) du mauvais (1/scale) : avec
    les dimensions de cette fixture (120x200), lw=60 a scale=0.5 donne
    kx = 120/60 = 2.0, qui vaut exactement 1/scale. Les deux formules
    coincident aux deux valeurs parametrees, et un ecart entre elles sur
    cette fixture resterait de toute facon bien en-dessous de la tolerance
    de 3.0 px ci-dessous.

    Le rayon passe a locate_center est celui de la synthese en pleine
    resolution (25 px), pas donnees["radius"] qui est en coordonnees
    d'analyse et vaudrait la moitie a scale=0.5.
    """
    from eclipse.locate import locate_center
    cache = str(tmp_path / f"a-{scale}.json")
    sortie = str(tmp_path / f"out-{scale}.mp4")
    analyze(video_synthetique, cache, scale=scale)
    # taille explicite (60x100) : la fixture (120x200) est plus petite que
    # TAILLE_DEFAUT, dont la fenetre butee deborderait quand meme de facon
    # degeneree (voir plus haut). Le centre du cadre de sortie est donc
    # (30, 50), et non plus (60, 100).
    # taille_sortie egale a taille : sans cela, le defaut (1080x1920)
    # agrandirait la sortie et le rayon de synthese (25 px) ne correspondrait
    # plus a rien dans locate_center ci-dessous.
    render(video_synthetique, sortie, cache, taille=(60, 100),
          taille_sortie=(60, 100))
    with FrameReader(sortie) as r:
        centres = []
        for frame in r:
            gray = frame.astype(np.float32).mean(axis=2)
            cx, cy, conf = locate_center(gray, r=25.0)
            if conf > 0.02:
                centres.append((cx, cy))
    # Sans ce minimum, une sortie ne contenant qu'une frame localisable
    # passerait le test.
    assert len(centres) >= 20
    xs = np.array([c[0] for c in centres])
    ys = np.array([c[1] for c in centres])
    assert np.abs(xs - 30.0).max() < 3.0
    assert np.abs(ys - 50.0).max() < 3.0


def test_render_ne_melange_pas_deux_vues_eloignees(video_synthetique, tmp_path):
    """Un plafond de deplacement nul doit desactiver toute interpolation."""
    cache = str(tmp_path / "a.json")
    analyze(video_synthetique, cache, scale=1.0)
    large = render(video_synthetique, str(tmp_path / "a.mp4"), cache,
                   taille=(60, 100), taille_sortie=(60, 100))
    nul = render(video_synthetique, str(tmp_path / "b.mp4"), cache,
                 taille=(60, 100), taille_sortie=(60, 100),
                 interp_deplacement_max=0.0)
    assert nul["interpolees"] == 0
    assert large["interpolees"] >= nul["interpolees"]


def test_render_sans_cache_valide_leve_une_erreur(video_synthetique, tmp_path):
    with pytest.raises(FileNotFoundError):
        render(video_synthetique, str(tmp_path / "o.mp4"),
               str(tmp_path / "absent.json"))


def test_render_n_ecrase_jamais_la_source(video_synthetique, tmp_path):
    cache = str(tmp_path / "a.json")
    analyze(video_synthetique, cache, scale=1.0)
    with pytest.raises(ValueError):
        render(video_synthetique, video_synthetique, cache)


@pytest.mark.skipif(os.name != "nt",
                    reason="insensibilite a la casse propre a Windows")
def test_render_refuse_une_sortie_differant_par_la_casse(video_synthetique,
                                                         tmp_path):
    """Sur un systeme insensible a la casse, ce chemin designe la source.

    Une comparaison de chaines la laisserait passer, et l'encodeur, lance
    avec -y, tronquerait le fichier d'origine.
    """
    cache = str(tmp_path / "a.json")
    analyze(video_synthetique, cache, scale=1.0)
    with pytest.raises(ValueError):
        render(video_synthetique, video_synthetique.upper(), cache)


def test_charger_cache_rejette_une_source_retouchee(video_synthetique, tmp_path):
    """Un reencodage a taille identique doit invalider le cache.

    Sinon on rendrait avec des mesures perimees, sans rien qui le signale.
    """
    cache = str(tmp_path / "a.json")
    analyze(video_synthetique, cache, scale=1.0)
    with open(cache) as f:
        d = json.load(f)
    d["source"]["mtime"] += 1
    with open(cache, "w") as f:
        json.dump(d, f)
    assert charger_cache(cache, video_synthetique) is None


def test_main_run_de_bout_en_bout(video_synthetique, tmp_path):
    sortie = str(tmp_path / "out.mp4")
    # --taille explicite : la fixture (120x200) est plus petite que
    # TAILLE_DEFAUT (840x1494), dont la fenetre butee deborderait quand meme
    # de facon degeneree (voir test_render_ecarte_les_frames_noires_et_floues).
    code = main(["run", video_synthetique, sortie,
                 "--cache", str(tmp_path / "a.json"), "--scale", "1.0",
                 "--taille", "60x100"])
    assert code == 0
    assert os.path.isfile(sortie)


@pytest.fixture
def video_derive_bord(tmp_path):
    """40 frames, derive verticale franche qui sort du cadre 60x100.

    cy va de 10 (bord haut) a 185.5 (bord bas) dans une source 120x200 :
    avec une fenetre de sortie 60x100 (demi-hauteur 50), seules les frames
    ou cy est dans [50, 150] tiennent entierement dans la source. La fenetre
    n'est plus rejetee dans ce cas : elle est butee contre le bord (voir
    test_render_recadre_et_supprime_les_bords_noirs ci-dessous), ce qui evite
    exactement le defaut mesure sur la sequence reelle : recentrer un disque
    proche du bord en translatant du vide.
    """
    chemin = tmp_path / "src_bord.mp4"
    with FrameWriter(str(chemin), width=120, height=200, fps=30.0) as w:
        for i in range(40):
            cy = 10.0 + i * 4.5
            w.write(make_frame(w=120, h=200, center=(60.0, cy), r=20.0))
    return str(chemin)


def test_render_recadre_et_supprime_les_bords_noirs(video_derive_bord, tmp_path):
    """Aucune frame de sortie ne doit contenir de zone noire de bord.

    C'est le defaut signale sur la sequence reelle : 65% des frames avaient
    plus de 15% de surface en noir pur, et le bord de la source tranchait le
    disque en angle droit. La fixture pousse volontairement le disque hors
    de la fenetre de sortie sur une bonne partie de la sequence (i=0..8 et
    i=32..39, verifie dans le docstring de la fixture). La fenetre bute
    desormais contre le bord de la source au lieu d'etre rejetee : aucune
    frame n'est donc perdue (40/40 conservees). La fenetre planifiee peut
    deborder la source de plusieurs px (track.planifie_trajectoire, butee
    souple) ; la bande ainsi revelee, qui ne correspond a aucun pixel
    filme, est comblee par replication de bord (render.apply_frame,
    remplissage='bord') et non par un aplat noir — c'est ce remplissage,
    et non l'absence de depassement, qui garantit qu'aucune zone noire
    n'apparait.
    """
    cache = str(tmp_path / "a.json")
    sortie = str(tmp_path / "out.mp4")
    stats_analyze = analyze(video_derive_bord, cache, scale=1.0)
    assert stats_analyze  # cache non vide, mesures exploitables
    # tolerance_bord EXPLICITE, et non le defaut : ce test porte sur le
    # remplissage de la bande revelee, pas sur le critere de troncature. Le
    # defaut est passe a 5 px, ce qui ecarte 3 frames de cette fixture --
    # legitimement, elle pousse le disque au bord. Figer 25 px ici garde au
    # test les 40 frames dont il a besoin pour exercer le remplissage sur
    # toute la plage, y compris i=0..8 et i=32..39.
    stats = render(video_derive_bord, sortie, cache, taille=(60, 100),
                   tolerance_bord=25.0)
    assert "hors_source" not in stats["motifs"]
    assert stats["gardees"] == 40
    with FrameReader(sortie) as r:
        for frame in r:
            g = frame.astype(np.float32).mean(axis=2)
            assert (g < 1.0).mean() < 0.02


def test_render_conserve_les_frames_en_bord_de_cadre(video_synthetique, tmp_path):
    """Buter la fenetre doit conserver plus de frames que la rejeter."""
    cache = str(tmp_path / "a.json")
    analyze(video_synthetique, cache, scale=1.0)
    s = render(video_synthetique, str(tmp_path / "o.mp4"), cache,
               taille=(60, 100), taille_sortie=(60, 100))
    assert s["gardees"] > 0
    assert "hors_cadre" not in s["motifs"]


def test_render_exporte_la_sequence_png(video_synthetique, tmp_path):
    cache = str(tmp_path / "a.json")
    sortie = str(tmp_path / "out.mp4")
    dossier = str(tmp_path / "frames")
    analyze(video_synthetique, cache, scale=1.0)
    stats = render(video_synthetique, sortie, cache, taille=(60, 100),
                   frames_dir=dossier)
    assert len(os.listdir(dossier)) == stats["gardees"]


@pytest.mark.skipif(not os.path.isfile(SOURCE_REELLE),
                    reason="video reelle absente (data/ est gitignore)")
def test_fumee_sur_la_video_reelle(tmp_path):
    """Tranche de 100 frames de la vraie source : verifie que ca tourne."""
    tranche = str(tmp_path / "tranche.mp4")
    with FrameReader(SOURCE_REELLE, width=540, height=960) as r:
        with FrameWriter(tranche, width=540, height=960, fps=30.0) as w:
            for i, frame in enumerate(r):
                if i >= 100:
                    break
                w.write(frame)
    cache = str(tmp_path / "a.json")
    sortie = str(tmp_path / "out.mp4")
    analyze(tranche, cache, scale=1.0)
    # Taille arbitraire, paire, qui tient dans la tranche decodee en 540x960
    # (la moitie exacte de la source). taille_sortie egale : ce test verifie
    # que le pipeline tourne sur la video reelle, pas l'agrandissement
    # final, deja couvert ailleurs.
    stats = render(tranche, sortie, cache, taille=(400, 560),
                  taille_sortie=(400, 560))
    assert stats["gardees"] > 50
    with FrameReader(sortie) as r:
        assert len(list(r)) == stats["gardees"]


def test_render_conserve_une_frame_forcee(video_synthetique, tmp_path):
    """Une frame que l'algorithme rejette doit revenir si on la force."""
    from eclipse.decisions import enregistrer
    from eclipse.pipeline import _signature_source
    cache = str(tmp_path / "a.json")
    analyze(video_synthetique, cache, scale=1.0)
    sans = render(video_synthetique, str(tmp_path / "a.mp4"), cache,
                  taille=(60, 100), taille_sortie=(60, 100))
    dec = str(tmp_path / "d.json")
    # la frame 12 est noire dans la fixture, donc rejetee too_dark
    enregistrer(dec, _signature_source(video_synthetique), {12: "conserver"})
    avec = render(video_synthetique, str(tmp_path / "b.mp4"), cache,
                  taille=(60, 100), taille_sortie=(60, 100),
                  decisions_path=dec)
    assert avec["gardees"] > sans["gardees"]


def test_render_ecarte_une_frame_forcee(video_synthetique, tmp_path):
    from eclipse.decisions import enregistrer
    from eclipse.pipeline import _signature_source
    cache = str(tmp_path / "a.json")
    analyze(video_synthetique, cache, scale=1.0)
    sans = render(video_synthetique, str(tmp_path / "a.mp4"), cache,
                  taille=(60, 100), taille_sortie=(60, 100))
    dec = str(tmp_path / "d.json")
    enregistrer(dec, _signature_source(video_synthetique),
                {i: "ecarter" for i in range(0, 5)})
    avec = render(video_synthetique, str(tmp_path / "b.mp4"), cache,
                  taille=(60, 100), taille_sortie=(60, 100),
                  decisions_path=dec)
    assert avec["gardees"] < sans["gardees"]
    assert avec["motifs"].get("manuel", 0) >= 1


def test_render_sans_fichier_de_decisions_est_inchange(video_synthetique, tmp_path):
    cache = str(tmp_path / "a.json")
    analyze(video_synthetique, cache, scale=1.0)
    a = render(video_synthetique, str(tmp_path / "a.mp4"), cache,
               taille=(60, 100), taille_sortie=(60, 100))
    b = render(video_synthetique, str(tmp_path / "b.mp4"), cache,
               taille=(60, 100), taille_sortie=(60, 100),
               decisions_path=str(tmp_path / "absent.json"))
    assert a["gardees"] == b["gardees"]


def test_render_refuse_les_deux_options_contradictoires(video_synthetique, tmp_path):
    cache = str(tmp_path / "a.json")
    analyze(video_synthetique, cache, scale=1.0)
    with pytest.raises(ValueError, match="contradictoires"):
        render(video_synthetique, str(tmp_path / "o.mp4"), cache,
               taille=(60, 100), taille_sortie=(60, 100),
               decisions_path=str(tmp_path / "d.json"), sans_decisions=True)


def test_render_signale_bruyamment_un_fichier_de_decisions_refuse(
        video_synthetique, tmp_path, capsys):
    """Un fichier de decisions ecrit pour une autre source ne doit pas
    disparaitre en silence : des heures de revue humaine seraient perdues
    sans que personne ne le remarque (finding 3)."""
    from eclipse.decisions import enregistrer
    from eclipse.pipeline import _signature_source
    cache = str(tmp_path / "a.json")
    analyze(video_synthetique, cache, scale=1.0)
    dec = str(tmp_path / "d.json")
    autre_signature = dict(_signature_source(video_synthetique), size=999)
    enregistrer(dec, autre_signature, {1: "conserver"})
    render(video_synthetique, str(tmp_path / "o.mp4"), cache,
          taille=(60, 100), taille_sortie=(60, 100), decisions_path=dec)
    erreur = capsys.readouterr().err
    assert "ATTENTION" in erreur


def test_main_viewer_transmet_les_memes_seuils_que_render(video_synthetique,
                                                          tmp_path, monkeypatch):
    """Finding 1 : le sous-parseur viewer doit accepter les memes options
    que render/run (_ajoute_seuils/_ajoute_cadrage) et transmettre a sert()
    celles dont les verdicts dependent (seuils, tolerance_bord, seuil_masque) ;
    sinon un rendu ajuste (--blur-rel, --tolerance-bord...) serait revu
    contre des seuils par defaut, silencieusement. --taille n'en fait plus
    partie : la fenetre de recadrage ne pese plus sur les verdicts depuis
    que analyse_verdicts ne clippe plus (voir eclipse/verdicts.py)."""
    cache = str(tmp_path / "a.json")
    analyze(video_synthetique, cache, scale=1.0)
    appels = {}

    def faux_sert(*args, **kwargs):
        appels["args"] = args
        appels["kwargs"] = kwargs

    monkeypatch.setattr("eclipse.pipeline.sert", faux_sert)
    code = main(["viewer", video_synthetique, "--cache", cache,
                "--blur-rel", "0.35",
                "--seuil-masque", "0.4",
                "--tolerance-bord", "3"])
    assert code == 0
    assert appels["kwargs"]["seuils"] == {"blur_rel": 0.35}
    assert appels["kwargs"]["seuil_masque"] == 0.4
    assert appels["kwargs"]["tolerance_bord"] == 3.0


def test_main_viewer_transmet_le_cadrage_a_sert(video_synthetique, tmp_path,
                                               monkeypatch):
    """Le sous-parseur viewer accepte six options de cadrage que sert()
    n'acceptait pas : le rendu lance depuis la page rendait donc avec le
    cadrage par defaut, silencieusement, alors que la ligne de commande en
    demandait un autre. Meme classe que le finding 1 ci-dessus, etendue du
    tri au cadrage, et introduite par le bouton de rendu.

    Six et non sept : la septieme, --frames-dir, est REFUSEE par le viewer
    (voir main, et test_viewer_refuse_frames_dir). Ce test la passait encore
    et attendait code == 0 ; il echouait donc depuis que le refus existe, et
    l'assertion k["frames_dir"] visait un parametre que sert() n'a jamais
    eu. Le retirer, plutot que le refus, est le seul choix coherent : ce
    refus est la garde qui empeche la permutation de detruire un dossier de
    l'utilisateur."""
    cache = str(tmp_path / "a.json")
    analyze(video_synthetique, cache, scale=1.0)
    appels = {}

    def faux_sert(*args, **kwargs):
        appels["kwargs"] = kwargs

    monkeypatch.setattr("eclipse.pipeline.sert", faux_sert)
    code = main(["viewer", video_synthetique, "--cache", cache,
                "--taille", "100x160", "--sortie-taille", "200x320",
                "--interp-max", "7", "--interp-deplacement-max", "12.5",
                "--depassement-butee", "42"])
    assert code == 0
    k = appels["kwargs"]
    assert k["taille"] == (100, 160)
    assert k["taille_sortie"] == (200, 320)
    assert k["interp_max"] == 7
    assert k["interp_deplacement_max"] == 12.5
    assert "frames_dir" not in k
    assert k["depassement_butee"] == 42.0


def test_main_viewer_transmet_la_couleur_a_sert(video_synthetique, tmp_path,
                                                monkeypatch):
    """Meme classe que le cadrage ci-dessus : une option acceptee par le
    sous-parseur viewer mais non transmise a sert() donnerait un rendu
    lance depuis la page qui ne correspond pas a ce que la ligne de
    commande a demande, silencieusement."""
    cache = str(tmp_path / "a.json")
    analyze(video_synthetique, cache, scale=1.0)
    appels = {}

    def faux_sert(*args, **kwargs):
        appels["kwargs"] = kwargs

    monkeypatch.setattr("eclipse.pipeline.sert", faux_sert)
    code = main(["viewer", video_synthetique, "--cache", cache,
                "--sans-couleur", "--couleur-fenetre", "15",
                "--couleur-amplitude", "0.1"])
    assert code == 0
    k = appels["kwargs"]
    assert k["couleur"] is False
    assert k["couleur_fenetre"] == 15
    assert k["couleur_amplitude"] == 0.1


def test_main_viewer_refuse_sans_decisions(video_synthetique, tmp_path,
                                           monkeypatch):
    """--sans-decisions n'a pas de sens pour le viewer : il affiche
    toujours les ecarts manuels par definition."""
    cache = str(tmp_path / "a.json")
    analyze(video_synthetique, cache, scale=1.0)
    monkeypatch.setattr("eclipse.pipeline.sert",
                        lambda *a, **k: pytest.fail("sert() ne doit pas etre appele"))
    code = main(["viewer", video_synthetique, "--cache", cache,
                "--sans-decisions"])
    assert code == 1


def test_render_absorbe_un_echelon_de_reacquisition(tmp_path):
    """Le disque pres du bord bas, puis un echelon de visee de 80 px (re-
    acquisition du tracking apres masquage). Avec la butee dure le disque
    sautait de toute l'excursion d'un coup ; planifie, il ne bouge que de
    quelques px par frame dans la sortie."""
    src = str(tmp_path / "src.mp4")
    with FrameWriter(src, width=120, height=200, fps=30.0) as w:
        for i in range(60):
            # cy=195 : excursion de 45 px au-dela de la borne de fenetre
            # (150), donc AU-DELA du budget D=40 — le corridor force un
            # offset de 5 px, le glissement est reellement exerce. A 185
            # (excursion 35 < D), le planificateur centrerait tout par la
            # bande et le test ne discriminerait rien.
            cy = 195.0 if i < 30 else 115.0     # echelon a la frame 30
            w.write(make_frame(w=120, h=200, center=(60.0, cy), r=20.0))
    cache = str(tmp_path / "a.json")
    analyze(src, cache, scale=1.0)
    sortie = str(tmp_path / "out.mp4")
    render(src, sortie, cache, taille=(60, 100), taille_sortie=(60, 100),
           interp_max=0)
    from eclipse.locate import locate_center
    positions = []
    frames_sortie = []
    with FrameReader(sortie) as r:
        for f in r:
            frames_sortie.append(f)
            gray = f.astype(np.float32).mean(axis=2)
            cx, cy, _ = locate_center(gray, r=20.0)
            positions.append((cx, cy))
    sauts = [float(np.hypot(b[0] - a[0], b[1] - a[1]))
             for a, b in zip(positions, positions[1:])]
    # Excursion initiale : cy=195, borne de fenetre a 150, budget D=40 ->
    # le disque tient a 5 px sous le centre (offset force par le corridor).
    # Echelon de 80 px a la frame 30 : butee dure = saut de ~45 px d'un
    # coup ; planifie (v=2) = glissement a ~2 px/frame.
    assert max(sauts) < 6.0
    assert abs(positions[-1][1] - 50.0) < 3.0   # recentre a la fin
    # Pendant l'excursion initiale (frames 0-29), la fenetre planifiee est
    # butee au depassement maximal (D=40 px, verifie empiriquement : le
    # bord bas de la fenetre atteint alors src_h + 40 = 240 sur une source
    # haute de 200 px) : la bande sous le bord reel de la source, sur les
    # dernieres lignes de sortie, ne correspond a aucun pixel filme. Elle
    # doit etre comblee par replication de bord (render.apply_frame,
    # remplissage='bord'), pas par un aplat noir. Ceci fixe deliberement
    # ce cablage, jusqu'ici epingle par accident seulement (les bords bas
    # de la fixture n'etant jamais noirs).
    bas_frame0 = frames_sortie[0][-3:, :]
    assert bas_frame0.max() > 0


def test_render_transmet_vitesse_et_depassement(tmp_path, monkeypatch):
    """Les parametres CLI atteignent le planificateur."""
    import eclipse.pipeline as pl
    appels = []
    vrai = pl.planifie_trajectoire

    def espion(s, bornes, depassement):
        appels.append(depassement)
        return vrai(s, bornes, depassement)

    monkeypatch.setattr(pl, "planifie_trajectoire", espion)
    src = str(tmp_path / "src.mp4")
    with FrameWriter(src, width=120, height=200, fps=30.0) as w:
        for i in range(12):
            w.write(make_frame(w=120, h=200, center=(60.0, 100.0), r=20.0))
    cache = str(tmp_path / "a.json")
    analyze(src, cache, scale=1.0)
    render(src, str(tmp_path / "o.mp4"), cache, taille=(60, 100),
           taille_sortie=(60, 100), depassement_butee=10.0)
    assert appels == [10.0, 10.0]                  # un appel par axe


def test_render_stabilise_la_couleur_par_defaut(video_synthetique, tmp_path,
                                                monkeypatch):
    """Par defaut, chaque frame part chez les travailleurs avec un gain par
    canal : c'est la stabilisation de balance rapportee manquante sur la
    video publiee (« the white balance keeps changing »)."""
    import eclipse.pipeline as pl
    gains_vus = []
    vrai = pl.rend_frame

    def espion(travail):
        gains_vus.append(travail[3])
        return vrai(travail)

    monkeypatch.setattr(pl, "rend_frame", espion)
    cache = str(tmp_path / "a.json")
    analyze(video_synthetique, cache, scale=1.0)
    render(video_synthetique, str(tmp_path / "o.mp4"), cache,
           taille=(60, 100), taille_sortie=(60, 100))
    assert gains_vus
    assert all(np.shape(g) == (3,) for g in gains_vus)


def test_render_sans_couleur_garde_un_gain_scalaire(video_synthetique,
                                                    tmp_path, monkeypatch):
    """couleur=False retrouve exactement le comportement historique :
    luminance seule, canaux intacts."""
    import eclipse.pipeline as pl
    gains_vus = []
    vrai = pl.rend_frame

    def espion(travail):
        gains_vus.append(travail[3])
        return vrai(travail)

    monkeypatch.setattr(pl, "rend_frame", espion)
    cache = str(tmp_path / "a.json")
    analyze(video_synthetique, cache, scale=1.0)
    render(video_synthetique, str(tmp_path / "o.mp4"), cache,
           taille=(60, 100), taille_sortie=(60, 100), couleur=False)
    assert gains_vus
    assert all(np.shape(g) == () for g in gains_vus)


def test_render_transmet_fenetre_et_amplitude(video_synthetique, tmp_path,
                                              monkeypatch):
    import eclipse.pipeline as pl
    appels = []
    vrai = pl.solve_couleur

    def espion(wb, valid, fenetre, amplitude):
        appels.append((fenetre, amplitude))
        return vrai(wb, valid, fenetre, amplitude)

    monkeypatch.setattr(pl, "solve_couleur", espion)
    cache = str(tmp_path / "a.json")
    analyze(video_synthetique, cache, scale=1.0)
    render(video_synthetique, str(tmp_path / "o.mp4"), cache,
           taille=(60, 100), taille_sortie=(60, 100),
           couleur_fenetre=15, couleur_amplitude=0.10)
    assert appels == [(15, 0.10)]


def test_main_render_transmet_sans_couleur(video_synthetique, tmp_path,
                                           monkeypatch):
    appels = {}

    def faux_render(*args, **kwargs):
        appels["kwargs"] = kwargs
        return {"gardees": 0, "rejetees": 0, "motifs": {}, "interpolees": 0}

    monkeypatch.setattr("eclipse.pipeline.render", faux_render)
    code = main(["render", video_synthetique, str(tmp_path / "o.mp4"),
                "--cache", str(tmp_path / "a.json"), "--sans-couleur"])
    assert code == 0
    assert appels["kwargs"]["couleur"] is False


def test_main_render_transmet_les_reglages_de_couleur(video_synthetique,
                                                      tmp_path, monkeypatch):
    appels = {}

    def faux_render(*args, **kwargs):
        appels["kwargs"] = kwargs
        return {"gardees": 0, "rejetees": 0, "motifs": {}, "interpolees": 0}

    monkeypatch.setattr("eclipse.pipeline.render", faux_render)
    code = main(["render", video_synthetique, str(tmp_path / "o.mp4"),
                "--cache", str(tmp_path / "a.json"),
                "--couleur-fenetre", "15", "--couleur-amplitude", "0.1"])
    assert code == 0
    assert appels["kwargs"]["couleur"] is True
    assert appels["kwargs"]["couleur_fenetre"] == 15
    assert appels["kwargs"]["couleur_amplitude"] == 0.1


def test_analyze_consigne_la_masse_captee(video_synthetique, tmp_path):
    """La mesure du masque entre dans le cache : c'est elle qui decidera
    quelles positions entrent dans la trajectoire."""
    cache = str(tmp_path / "a.json")
    donnees = analyze(video_synthetique, cache, scale=1.0)
    assert all("masse_captee" in f for f in donnees["frames"])
    # La fixture a un disque net et centre sur la plupart des frames : la
    # mesure doit y etre proche de 1.
    valeurs = [f["masse_captee"] for f in donnees["frames"]
               if f["masse_captee"] is not None]
    assert len(valeurs) > 20
    assert max(valeurs) > 0.9


def test_charger_cache_rejette_le_schema_1(video_synthetique, tmp_path):
    """Le champ ajoute change le contrat du cache : un cache d'avant ce
    chantier doit etre refuse, pas lu a moitie."""
    cache = str(tmp_path / "a.json")
    analyze(video_synthetique, cache, scale=1.0)
    with open(cache, encoding="utf-8") as f:
        d = json.load(f)
    d["schema"] = 1
    with open(cache, "w", encoding="utf-8") as f:
        json.dump(d, f)
    assert charger_cache(cache, video_synthetique) is None


@pytest.mark.skipif(not os.path.isfile(SOURCE_REELLE),
                    reason="video source absente")
def test_les_frames_floues_recuperees_sont_cadrees_sur_le_disque(tmp_path):
    """Verification aux pixels, sur la sequence reelle.

    Les frames source 756, 762 et 792 sont classees motion_blur mais leur
    mesure est juste : le masque y capture 0,998 de la lumiere. Avant ce
    chantier leur position etait ecartee de la trajectoire et remplacee par une
    interpolation, et le rendu les cadrait 195 a 225 px a cote — c'est le
    defaut visible a 00:22 dans la video de sortie.

    On decode la frame, on situe la boite englobante de la zone lumineuse, et
    on verifie que la trajectoire pointe bien dessus. Un test sur la seule
    trajectoire ne verrait rien : c'est precisement l'erreur qui a fait
    refuter deux chantiers precedents.
    """
    from eclipse.verdicts import analyse_verdicts

    cache = str(tmp_path / "a.json")
    donnees = analyze(SOURCE_REELLE, cache, scale=0.5)
    info = probe(SOURCE_REELLE)
    r = analyse_verdicts(donnees, info["width"], info["height"])

    cibles = {756, 762, 792}
    images = {}
    with FrameReader(SOURCE_REELLE, width=info["width"],
                     height=info["height"]) as rd:
        for n, f in enumerate(rd):
            if n in cibles:
                images[n] = f.astype(np.float32).mean(axis=2)
                if len(images) == len(cibles):
                    break

    assert len(images) == len(cibles), "frames cibles absentes de la source"
    for n, g in images.items():
        m = g > g.max() * 0.35
        ys, xs = np.nonzero(m)
        reel_x = (float(xs.min()) + float(xs.max())) / 2.0
        reel_y = (float(ys.min()) + float(ys.max())) / 2.0
        ecart = float(np.hypot(r["traj_x"][n] - reel_x,
                               r["traj_y"][n] - reel_y))
        assert ecart < 60.0, (
            f"frame {n} : trajectoire a {ecart:.0f} px du disque reel")


def test_analyze_produit_le_meme_cache_en_parallele(video_synthetique, tmp_path):
    """Le critere d'acceptation du chantier : identite, pas equivalence.
    Les memes operations sur les memes donnees, executees ailleurs, doivent
    rendre exactement le meme cache.

    La comparaison texte a texte est le controle le plus fort : elle couvre
    en une seule assertion l'ordre des cles, les champs manquants ou en trop
    et les valeurs. Elle est possible ici parce que les deux appels portent
    sur la MEME source (meme chemin, taille, mtime -> meme signature) : rien
    dans le cache ne varie legitimement d'un appel a l'autre."""
    seq = str(tmp_path / "seq.json")
    par = str(tmp_path / "par.json")
    analyze(video_synthetique, seq, scale=1.0, processus=1)
    analyze(video_synthetique, par, scale=1.0, processus=3)
    texte_seq = open(seq, encoding="utf-8").read()
    texte_par = open(par, encoding="utf-8").read()
    assert texte_seq == texte_par, (
        "le cache differe entre sequentiel et parallele (contenu, valeurs "
        "ou ordre des cles)")

    # Comparaison structuree en plus, pour un message d'erreur plus lisible
    # si la comparaison texte echoue un jour.
    a = json.loads(texte_seq)
    b = json.loads(texte_par)
    assert a["radius"] == b["radius"]
    assert len(a["frames"]) == len(b["frames"])
    for i, (x, y) in enumerate(zip(a["frames"], b["frames"])):
        assert list(x.keys()) == list(y.keys()), (
            f"frame {i} : ordre des cles differe : "
            f"{list(x.keys())} vs {list(y.keys())}")
        assert x == y, f"frame {i} differe : {x} vs {y}"


def test_analyze_un_processus_ne_cree_pas_de_pool(video_synthetique, tmp_path,
                                                  monkeypatch):
    """processus=1 doit emprunter la boucle sequentielle, pas un pool de 1 :
    c'est le chemin de debogage et l'oracle des tests d'identite."""
    import eclipse.parallele as par

    def interdit(*a, **k):
        raise AssertionError("un Pool a ete cree alors que processus=1")

    monkeypatch.setattr(par, "Pool", interdit)
    analyze(video_synthetique, str(tmp_path / "a.json"), scale=1.0, processus=1)


def test_main_analyze_transmet_le_nombre_de_processus(video_synthetique, tmp_path,
                                                      monkeypatch):
    appels = {}

    def faux_analyze(*args, **kwargs):
        appels["kwargs"] = kwargs
        return {"frames": [], "schema": 2}

    monkeypatch.setattr("eclipse.pipeline.analyze", faux_analyze)
    code = main(["analyze", video_synthetique, "--cache",
                str(tmp_path / "a.json"), "--processus", "2"])
    assert code == 0
    assert appels["kwargs"]["processus"] == 2


def test_main_render_transmet_le_nombre_de_processus(video_synthetique, tmp_path,
                                                     monkeypatch):
    """Miroir du test analyze ci-dessus : --processus doit atteindre render(),
    dont le defaut de signature vaut desormais 1 (chemin sequentiel)."""
    appels = {}

    def faux_render(*args, **kwargs):
        appels["kwargs"] = kwargs
        return {"gardees": 0, "rejetees": 0, "motifs": {}, "interpolees": 0}

    monkeypatch.setattr("eclipse.pipeline.render", faux_render)
    code = main(["render", video_synthetique, str(tmp_path / "o.mp4"),
                "--cache", str(tmp_path / "a.json"), "--processus", "2"])
    assert code == 0
    assert appels["kwargs"]["processus"] == 2


def test_render_produit_le_meme_fichier_en_parallele(video_synthetique, tmp_path):
    """Identite octet pour octet. libx264 est deterministe avec les reglages
    du projet : deux encodages des memes frames donnent la meme empreinte.
    La comparaison vaut sur cette machine et ce binaire ffmpeg."""
    import hashlib
    cache = str(tmp_path / "a.json")
    analyze(video_synthetique, cache, scale=1.0, processus=1)
    seq = str(tmp_path / "seq.mp4")
    par = str(tmp_path / "par.mp4")
    s = render(video_synthetique, seq, cache, taille=(60, 100),
               taille_sortie=(60, 100), processus=1)
    p = render(video_synthetique, par, cache, taille=(60, 100),
               taille_sortie=(60, 100), processus=3)
    assert s == p, "les statistiques de rendu different"
    e1 = hashlib.sha256(open(seq, "rb").read()).hexdigest()
    e2 = hashlib.sha256(open(par, "rb").read()).hexdigest()
    assert e1 == e2, "la video parallele differe de la sequentielle"


def test_render_un_processus_ne_cree_pas_de_pool(video_synthetique, tmp_path,
                                                 monkeypatch):
    """processus=1 doit emprunter la boucle sequentielle, pas un pool de 1 :
    c'est le chemin de debogage et l'oracle des tests d'identite."""
    import eclipse.parallele as par
    cache = str(tmp_path / "a.json")
    analyze(video_synthetique, cache, scale=1.0, processus=1)

    def interdit(*a, **k):
        raise AssertionError("un Pool a ete cree alors que processus=1")

    monkeypatch.setattr(par, "Pool", interdit)
    render(video_synthetique, str(tmp_path / "o.mp4"), cache,
           taille=(60, 100), taille_sortie=(60, 100), processus=1)


def test_render_parallele_conserve_les_frames_interpolees(video_synthetique,
                                                          tmp_path):
    """L'interpolation reste dans le parent : elle a besoin de la frame
    precedemment ecrite. Son compte ne doit pas dependre du parallelisme.

    interp_max=8 et non 3 : les deux coupes de la video synthetique font
    exactement 5 frames, donc un plafond de 3 ne comble rien et la
    comparaison serait tautologique (0 == 0). C'est le meme plafond que
    test_render_interpole_les_courtes_coupes.
    """
    cache = str(tmp_path / "a.json")
    analyze(video_synthetique, cache, scale=1.0, processus=1)
    s = render(video_synthetique, str(tmp_path / "s.mp4"), cache,
               taille=(60, 100), taille_sortie=(60, 100), interp_max=8,
               processus=1)
    p = render(video_synthetique, str(tmp_path / "p.mp4"), cache,
               taille=(60, 100), taille_sortie=(60, 100), interp_max=8,
               processus=3)
    assert s["interpolees"] > 0, (
        "cas de test vide : sans interpolation reelle, la comparaison "
        "ci-dessous passerait meme sans branche d'interpolation")
    assert s["interpolees"] == p["interpolees"]
    assert s["gardees"] == p["gardees"]


def test_analyze_appelle_progression_une_fois_par_frame(video_synthetique,
                                                        tmp_path):
    """Exactement un appel par frame, fait strictement croissant de 1 a n.

    Pas « au moins un appel » : ce projet a deja laisse passer deux tests qui
    n'echouaient pas pour la raison qu'ils annoncaient, dont un qui comparait
    0 a 0. Un rappel appele une seule fois a la fin passerait un tel test.
    """
    appels = []
    analyze(video_synthetique, str(tmp_path / "a.json"), scale=1.0,
                  progression=lambda fait, total=None: appels.append(fait))
    with open(tmp_path / "a.json", encoding="utf-8") as f:
        nb = len(json.load(f)["frames"])
    assert appels == list(range(1, nb + 1))


def test_render_appelle_progression_une_fois_par_frame_gardee(
        video_synthetique, tmp_path):
    cache = str(tmp_path / "a.json")
    analyze(video_synthetique, cache, scale=1.0)
    appels = []
    res = render(video_synthetique, str(tmp_path / "o.mp4"), cache,
                 progression=lambda fait, total=None: appels.append((fait, total)))
    faits = [f for f, _ in appels]
    totaux = {t for _, t in appels}
    assert faits == list(range(1, res["gardees"] + 1))
    # Le total du rendu est exact, connu avant la boucle.
    assert totaux == {res["gardees"]}


def test_render_donne_le_meme_fichier_avec_et_sans_progression(
        video_synthetique, tmp_path):
    """Le rappel ne doit rien changer a la sortie, octet pour octet.

    Meme critere que le chantier de parallelisation : l'identite, pas
    l'equivalence.
    """
    cache = str(tmp_path / "a.json")
    analyze(video_synthetique, cache, scale=1.0)
    sans = str(tmp_path / "sans.mp4")
    avec = str(tmp_path / "avec.mp4")
    render(video_synthetique, sans, cache)
    render(video_synthetique, avec, cache, progression=lambda *a, **k: None)
    with open(sans, "rb") as f1, open(avec, "rb") as f2:
        assert f1.read() == f2.read()


def test_analyze_donne_le_meme_cache_avec_et_sans_progression(
        video_synthetique, tmp_path):
    sans, avec = str(tmp_path / "sans.json"), str(tmp_path / "avec.json")
    analyze(video_synthetique, sans, scale=1.0)
    analyze(video_synthetique, avec, scale=1.0,
            progression=lambda *a, **k: None)
    with open(sans, encoding="utf-8") as f1, open(avec, encoding="utf-8") as f2:
        assert f1.read() == f2.read()


def test_progression_qui_leve_interrompt_le_rendu_sans_laisser_de_fichier(
        video_synthetique, tmp_path):
    """Le rappel est le point d'annulation : ce qu'il leve doit remonter."""
    cache = str(tmp_path / "a.json")
    analyze(video_synthetique, cache, scale=1.0)
    sortie = str(tmp_path / "o.mp4")

    class Stop(Exception):
        pass

    def rappel(fait, total=None):
        if fait >= 3:
            raise Stop()

    with pytest.raises(Stop):
        render(video_synthetique, sortie, cache, progression=rappel)


def test_main_viewer_sans_source_passe_none(monkeypatch):
    """Le viewer doit pouvoir s'ouvrir sur rien depuis la ligne de commande.

    `source` est devenu nargs="?" : sans lui, main doit appeler sert(None,
    ...) plutot que refuser l'invocation. Seul test de la moitie pipeline de
    la tache 6 -- sans lui, un retour a l'argument obligatoire passerait la
    suite entiere.
    """
    appels = {}
    monkeypatch.setattr("eclipse.pipeline.sert",
                        lambda *a, **k: appels.update(args=a))
    assert main(["viewer"]) == 0
    assert appels["args"][0] is None


def test_un_cache_d_avant_le_correctif_de_mesure_est_refuse(video_synthetique, tmp_path):
    """Les champs de mesure changent de VALEUR sans changer de nom ni de
    forme -- limb_sharpness au passage du percentile 90 au 98, puis cx quand
    l'alignement a cesse de reprendre le x du pic concurrent, puis toutes
    les mesures quand le profil d'eclipse a choisi les strategies de la
    passe 1 (schema 6), puis radius_dark et les mesures du regime sombre
    quand le vote dual a cesse de voter les deux regimes au meme rayon
    (schema 7).

    charger_cache ne valide que le schema et la signature de la SOURCE ;
    celle-ci n'ayant pas bouge, un cache d'avant le correctif serait relu en
    silence et le viewer annoncerait « analyse deja faite ». Le correctif
    resterait inerte, sans qu'aucun signal ne le dise. C'est SCHEMA_VERSION
    qui l'empeche, et rien d'autre.
    """
    cache = str(tmp_path / "a.json")
    analyze(video_synthetique, cache, scale=1.0)
    with open(cache) as f:
        donnees = json.load(f)
    assert donnees["schema"] == 7, "un cache neuf porte la version courante"
    donnees["schema"] = 6                      # tel qu'ecrit avant le correctif
    with open(cache, "w") as f:
        json.dump(donnees, f)
    assert charger_cache(cache, video_synthetique) is None


def _video_totalite(tmp_path, nb_clair=60, nb_sombre=40, nom="tot.mp4"):
    """Une eclipse totale de synthese : croissants a r=55, totalite a r=63.

    Les deux rayons DIFFERENT, et c'est le point : le limbe solaire et le
    disque lunaire qui le couvre ne sont pas le meme cercle (mesure sur
    m2-res_852p : 87-88 px contre 93,8, soit 7,3 % d'ecart). La proportion
    reprend celle de la video reelle, ou la totalite occupe la majeure
    partie de la sequence : le balayage clair echantillonne les 300
    premieres frames, le balayage sombre toute la video.
    """
    chemin = str(tmp_path / nom)
    with FrameWriter(chemin, width=200, height=200, fps=30.0) as w:
        for i in range(nb_clair):
            phase = 0.6 * i / max(nb_clair - 1, 1)
            w.write(make_frame(w=200, h=200, center=(100.0, 100.0), r=55.0,
                               phase=phase, halo=0.1))
        for i in range(nb_sombre):
            w.write(make_totality_frame(w=200, h=200, center=(100.0, 100.0),
                                        r=63.0, corona=0.5))
    return chemin


def test_dual_preset_scans_one_radius_per_regime(tmp_path):
    """Le defaut diagnostique. Le preset sun votait les DEUX regimes au
    rayon estime sur les 300 premieres frames — toutes en croissant clair —
    donc au rayon du limbe SOLAIRE. Le vote sombre y devenait degenere
    (accumulateur en anneau, voir locate.locate_center_regime) et le centre
    mesure alternait entre deux modes faux de +/- 6 px.

    Le cache doit desormais porter les deux rayons, et les frames sombres
    doivent etre localisees sur le bon.
    """
    chemin = _video_totalite(tmp_path)
    cache = str(tmp_path / "a.json")
    d = analyze(chemin, cache, scale=1.0, preset="sun")
    assert abs(d["radius"] - 55.0) < 2.0
    assert abs(d["radius_dark"] - 63.0) < 2.0
    relu = json.load(open(cache, encoding="utf-8"))
    assert relu["radius_dark"] == d["radius_dark"]

    sombres = [f for f in relu["frames"] if f["regime"] == "dark"]
    assert len(sombres) >= 30, "la totalite doit etre reconnue comme sombre"
    for f in sombres:
        assert abs(f["cx"] - 100.0) < 1.5
        assert abs(f["cy"] - 100.0) < 1.5


def test_a_bright_only_video_falls_back_to_the_single_radius(tmp_path):
    """Une eclipse PARTIELLE n'a pas de disque sombre : le second balayage
    n'a rien a trouver et le rayon sombre ne doit pas partir a la derive."""
    chemin = _video_totalite(tmp_path, nb_clair=60, nb_sombre=0,
                             nom="partielle.mp4")
    d = analyze(chemin, str(tmp_path / "b.json"), scale=1.0, preset="sun")
    assert abs(d["radius_dark"] - d["radius"]) < 2.0


def test_the_dark_scan_falling_over_keeps_the_bright_radius(tmp_path,
                                                            monkeypatch):
    """Le repli documente : quand scan_radius ne trouve aucun pic sombre
    exploitable, il leve, et le rayon clair sert aux deux regimes."""
    from eclipse import pipeline

    chemin = _video_totalite(tmp_path, nb_clair=20, nb_sombre=10,
                             nom="repli.mp4")
    appels = []
    reel = pipeline.scan_radius

    def scan(grays, vote="bright", **kw):
        appels.append(vote)
        if vote == "dark":
            raise ValueError("aucun pic de vote exploitable")
        return reel(grays, vote=vote, **kw)

    monkeypatch.setattr(pipeline, "scan_radius", scan)
    d = analyze(chemin, str(tmp_path / "c.json"), scale=1.0, preset="sun")
    assert "dark" in appels, "le second balayage doit avoir lieu"
    assert d["radius_dark"] == d["radius"]


def test_a_non_dual_preset_keeps_a_single_radius(tmp_path):
    """Les profils non-dual ne changent pas : un seul rayon, et le champ
    radius_dark le repete pour que le cache ait toujours la meme forme."""
    chemin = _video_totalite(tmp_path, nb_clair=30, nb_sombre=0,
                             nom="mono.mp4")
    for preset in ("custom", "moon"):
        d = analyze(chemin, str(tmp_path / f"{preset}.json"), scale=1.0,
                    preset=preset)
        assert d["radius_dark"] == d["radius"]


def test_an_explicit_radius_wins_for_both_regimes(tmp_path):
    """--radius est un ordre, pas une suggestion : sans radius_dark
    explicite il vaut pour les deux regimes, et aucun balayage n'a lieu."""
    chemin = _video_totalite(tmp_path, nb_clair=20, nb_sombre=10,
                             nom="explicite.mp4")
    d = analyze(chemin, str(tmp_path / "d.json"), scale=1.0, preset="sun",
                radius=44.0)
    assert d["radius"] == 44.0 and d["radius_dark"] == 44.0


def test_an_explicit_dark_radius_is_honoured_alone(tmp_path):
    """radius_dark seul : le rayon clair est balaye, le sombre est impose."""
    chemin = _video_totalite(tmp_path, nb_clair=20, nb_sombre=10,
                             nom="explicite2.mp4")
    d = analyze(chemin, str(tmp_path / "e.json"), scale=1.0, preset="sun",
                radius_dark=70.0)
    assert d["radius_dark"] == 70.0 and d["radius"] != 70.0
