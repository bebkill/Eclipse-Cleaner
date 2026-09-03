import numpy as np
from eclipse.verdicts import analyse_verdicts


def _cache(n=60, cx=270.0, cy=480.0, conf=0.20, p90=200.0):
    """Cache d'analyse minimal, toutes frames nominales."""
    return {
        "radius": 100.0, "width": 540, "height": 960, "scale": 0.5,
        "frames": [{"n": i, "cx": cx, "cy": cy, "conf": conf,
                    "disk_p90": p90, "limb_sharpness": 30.0,
                    "flare_ratio": 0.05, "masse_captee": 0.99, "level": 100.0,
                    "wb": [1.0, 0.6, 0.3]} for i in range(n)],
    }


def test_sequence_nominale_est_entierement_conservee():
    r = analyse_verdicts(_cache(), 1080, 1920)
    assert all(v is None for v in r["verdicts"])


def test_retourne_les_facteurs_d_echelle_de_la_source():
    """kx et ky convertissent l'analyse vers la SOURCE, pas vers la sortie."""
    r = analyse_verdicts(_cache(), 1080, 1920)
    assert r["kx"] == 2.0 and r["ky"] == 2.0


def test_retourne_la_trajectoire_en_coordonnees_source():
    """Le clip de fenetre n'est plus ici : il appartient au planificateur
    (track.planifie_trajectoire), qui depend du garde final apres decisions.
    analyse_verdicts expose la trajectoire brute en coordonnees source."""
    d = _cache(cy=900.0)          # 1800 px en pleine resolution
    r = analyse_verdicts(d, 1080, 1920)
    assert np.allclose(r["traj_y"], 1800.0)
    assert np.allclose(r["traj_x"], 540.0)


def test_une_frame_noire_est_rejetee():
    d = _cache()
    for i in range(20, 30):
        d["frames"][i]["disk_p90"] = 5.0
    r = analyse_verdicts(d, 1080, 1920)
    assert r["verdicts"][25] == "too_dark"


def test_longueur_des_sorties_egale_au_nombre_de_frames():
    r = analyse_verdicts(_cache(n=37), 1080, 1920)
    assert len(r["verdicts"]) == 37
    assert len(r["traj_x"]) == 37 and len(r["traj_y"]) == 37


def test_ilot_min_s_applique_aussi_aux_ilots_reveles_par_le_bord():
    """Un ilot_min personnalise doit valoir pour les DEUX passes de
    supprime_ilots : celle de classify et celle qui suit les rejets
    hors_source. Avant correction, la seconde passe figeait le defaut du
    module et --ilot-min n'etait honore qu'a moitie.

    Ici classify conserve tout ; ce sont les rejets hors_source (disque
    ampute de plus de tolerance_bord) qui isolent un ilot de 2 frames.
    """
    d = _cache()
    # cy=960 -> 1920 px pleine resolution : amputation = 1920 + rayon - 1920
    # = 203 px, au-dela de toute tolerance raisonnable. Frames 10-19 et 22-31
    # hors source, frames 20-21 nominales : ilot de 2.
    for i in list(range(10, 20)) + list(range(22, 32)):
        d["frames"][i]["cy"] = 960.0
    args = dict(tolerance_bord=150.0)
    sans = analyse_verdicts(d, 1080, 1920, **args)
    assert sans["verdicts"][20] is None          # defaut : ilot conserve
    avec = analyse_verdicts(d, 1080, 1920, seuils={"ilot_min": 5}, **args)
    assert avec["verdicts"][20] == "ilot"        # seuil actif : ilot supprime
    assert avec["verdicts"][21] == "ilot"


def test_une_frame_rejetee_garde_sa_propre_mesure():
    """Le coeur du chantier. Une frame que classify rejette, mais dont le
    masque capture la lumiere, doit garder SA position dans la trajectoire.

    Avant ce changement sa mesure etait ecartee et remplacee par une
    interpolation entre voisines : sur la sequence reelle, 37 frames finalement
    conservees se retrouvaient cadrees jusqu'a 1109 px a cote, parce que la
    decision humaine de les garder arrivait apres le calcul de trajectoire.
    """
    d = _cache(n=41)
    # La frame 20 est floue (rejetee par classify) mais bien localisee, et
    # elle est ailleurs que ses voisines.
    d["frames"][20]["limb_sharpness"] = 1.0
    d["frames"][20]["cx"] = 400.0            # voisines a 270.0
    r = analyse_verdicts(d, 1080, 1920)
    assert r["verdicts"][20] == "motion_blur"      # toujours rejetee
    assert abs(r["traj_x"][20] - 800.0) < 1.0      # mais cadree sur elle-meme


def test_une_mesure_incoherente_est_interpolee():
    """A l'inverse : un masque qui ne capture pas la lumiere signale un centre
    qui n'explique pas l'image. Sa position est reprise des voisines."""
    d = _cache(n=41)
    d["frames"][20]["masse_captee"] = 0.10
    d["frames"][20]["cx"] = 400.0
    r = analyse_verdicts(d, 1080, 1920)
    assert abs(r["traj_x"][20] - 540.0) < 1.0      # valeur des voisines


def test_une_masse_captee_absente_invalide_la_mesure():
    """Cache incomplet ou mesure impossible : on n'invente pas une validite."""
    d = _cache(n=41)
    d["frames"][20]["masse_captee"] = None
    d["frames"][20]["cx"] = 400.0
    r = analyse_verdicts(d, 1080, 1920)
    assert abs(r["traj_x"][20] - 540.0) < 1.0


def test_no_valid_measure_degrades_instead_of_raising():
    """When no frame passes the mask threshold, the analysis must not
    crash the caller (this is the reported viewer bug): fallback
    trajectory at the frame center, every frame marked no_lock."""
    d = _cache(n=30)
    for f in d["frames"]:
        f["masse_captee"] = 0.40          # all below the 0.80 default
    r = analyse_verdicts(d, 1080, 1920)
    assert r["mesures_valides"] == 0
    assert all(v == "no_lock" for v in r["verdicts"])
    assert np.allclose(r["traj_x"], 1080 / 2.0)   # source coordinates
    assert np.allclose(r["traj_y"], 1920 / 2.0)
    assert len(r["traj_x"]) == 30


def test_sort_defaults_follow_the_cache_preset():
    """A dim but steady moon sequence: dark_abs 40 (custom default) would
    reject everything, the moon preset's dark_abs must keep it."""
    d = _cache(n=40, p90=12.0)
    d["preset"] = "moon"
    r = analyse_verdicts(d, 1080, 1920)
    assert all(v is None for v in r["verdicts"])
    d["preset"] = "custom"
    r = analyse_verdicts(d, 1080, 1920)
    assert all(v == "too_dark" for v in r["verdicts"])


def test_explicit_seuils_override_the_preset_defaults():
    d = _cache(n=40, p90=12.0)
    d["preset"] = "moon"
    r = analyse_verdicts(d, 1080, 1920, seuils={"dark_abs": 40.0})
    assert all(v == "too_dark" for v in r["verdicts"])


def test_valid_measure_count_is_reported():
    d = _cache(n=30)
    d["frames"][5]["masse_captee"] = 0.10
    r = analyse_verdicts(d, 1080, 1920)
    assert r["mesures_valides"] == 29


def test_hors_source_uses_the_radius_of_each_frame_s_regime():
    """Le disque visible n'a pas la meme taille dans les deux regimes.

    Un cache dual porte deux rayons ; la borne hors_source est calculee sur
    le DISQUE, donc sur celui que la frame a reellement montre. Ici le
    rayon sombre est deux fois le clair : le meme centre tient dans la
    source en regime clair et deborde en regime sombre.
    """
    # kx = 2 : la borne vaut rayon*2 + 3 (MARGE_HALO) - 5 (tolerance), soit
    # 198 px au rayon clair et 398 px au rayon sombre. Un centre a 300 px
    # tient dans la premiere et pas dans la seconde.
    d = _cache(n=60, cx=150.0)              # 300 px en pleine resolution
    d["radius_dark"] = 200.0                # 400 px, contre 200 pour le clair
    for f in d["frames"]:
        f["regime"] = "bright"
    for i in range(20, 40):
        d["frames"][i]["regime"] = "dark"
    r = analyse_verdicts(d, 1080, 1920)
    assert r["verdicts"][10] is None            # clair : 300 >= 198
    assert r["verdicts"][30] == "hors_source"   # sombre : 300 < 398


def test_a_cache_without_radius_dark_keeps_the_single_bound():
    """Compatibilite de forme : sans radius_dark ni colonne de regime, la
    borne est celle d'avant, a l'octet."""
    d = _cache(n=40)
    avec = _cache(n=40)
    avec["radius_dark"] = avec["radius"]
    for f in avec["frames"]:
        f["regime"] = "dark"
    assert (analyse_verdicts(d, 1080, 1920)["verdicts"]
            == analyse_verdicts(avec, 1080, 1920)["verdicts"])


def test_a_missing_regime_counts_as_bright():
    """Une frame sans regime ne doit pas faire tomber l'analyse ni heriter
    du rayon sombre par defaut."""
    d = _cache(n=40, cx=210.0)
    d["radius_dark"] = 200.0
    for f in d["frames"]:
        f["regime"] = None
    assert all(v is None for v in analyse_verdicts(d, 1080, 1920)["verdicts"])


def test_analyse_verdicts_passe_le_regime_a_classify():
    """La reference de nettete de classify() doit etre scindee par regime
    (voir quality.classify), et analyse_verdicts est ce qui lui fournit la
    colonne : sans elle, une frame claire normale prise dans un flottement
    de regime pres d'un long regime sombre bien plus net est lue comme
    floue -- exactement le defaut mesure sur m2-res_852p entre les frames
    250 et 309."""
    n = 1000
    d = _cache(n=n)
    for f in d["frames"]:
        f["limb_sharpness"] = 100.0
        f["regime"] = "bright"
    for depart in range(260, 320, 12):
        for i in range(depart, depart + 6):
            d["frames"][i]["limb_sharpness"] = 1000.0
            d["frames"][i]["regime"] = "dark"
    for i in range(320, n):
        d["frames"][i]["limb_sharpness"] = 1000.0
        d["frames"][i]["regime"] = "dark"

    verdicts = analyse_verdicts(d, 1080, 1920)["verdicts"]
    zone = [i for i in range(260, 320) if d["frames"][i]["regime"] == "bright"]
    assert all(verdicts[i] is None for i in zone)


def test_an_unlocked_vote_does_not_anchor_under_the_sun_preset():
    """The sun preset's exposure-catastrophe transitions (measured on
    m2-res_852p, frames 270-279) leave some frames with a fully-captured
    mask (masse_captee 1.0) but a vote that never locked (conf 0.0040-
    0.0076, against >= 0.042 for the good frames around them): the center
    they report is garbage and must not anchor the trajectory."""
    d = _cache(n=41)
    d["preset"] = "sun"
    d["frames"][20]["conf"] = 0.005
    d["frames"][20]["cx"] = 400.0
    r = analyse_verdicts(d, 1080, 1920)
    assert abs(r["traj_x"][20] - 540.0) < 1.0       # interpolated from neighbors


def test_the_same_low_confidence_row_still_anchors_under_custom():
    """conf_ancre defaults to 0.0 everywhere but sun (see
    quality.SEUILS_DEFAUT): M2 found 44 correctly-positioned frames on the
    reference custom video (and 163 on Lunar-221924) sitting under 0.02
    despite being legitimate measures, so a universal floor is wrong and
    the same low-confidence row must keep anchoring on itself here."""
    d = _cache(n=41)
    d["preset"] = "custom"
    d["frames"][20]["conf"] = 0.005
    d["frames"][20]["cx"] = 400.0
    r = analyse_verdicts(d, 1080, 1920)
    assert abs(r["traj_x"][20] - 800.0) < 1.0       # still anchors on itself
