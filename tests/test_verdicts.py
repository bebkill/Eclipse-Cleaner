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
