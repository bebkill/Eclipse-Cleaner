"""Verdicts automatiques et trajectoire de recadrage.

Extrait de render() pour que le viewer et le rendu suivent exactement le
meme chemin : ce que l'utilisateur voit en rouge dans le viewer doit etre
ce que le rendu ecarte. Deux implementations divergeraient tot ou tard.
"""
import numpy as np

from .quality import (SEUILS_DEFAUT, classify, supprime_ilots,
                      verdicts_hors_source)
from .track import smooth_track


def _colonne(frames, cle, defaut=np.nan):
    return np.array([defaut if f[cle] is None else f[cle] for f in frames],
                    dtype=np.float64)


def analyse_verdicts(donnees, src_w, src_h, seuils=None,
                     tolerance_bord=None, seuil_masque=None):
    """Verdicts automatiques et trajectoire de fenetre, frame par frame.

    donnees : le cache d'analyse. src_w, src_h : dimensions de la SOURCE.
    Les valeurs None prennent les defauts du module pipeline.
    """
    from .pipeline import MARGE_HALO, SEUIL_MASQUE_DEFAUT, TOLERANCE_BORD_DEFAUT
    from .presets import sort_defaults

    profile = sort_defaults(donnees.get("preset", "custom"))
    # Preset defaults sit UNDER explicit thresholds: a preset proposes,
    # the operator disposes. The cache's preset drives both the renderer
    # and the viewer through this single path.
    seuils = dict(profile["seuils"], **(seuils or {})) or None
    if seuil_masque is None:
        seuil_masque = profile["seuil_masque"]

    tolerance_bord = (TOLERANCE_BORD_DEFAUT if tolerance_bord is None
                      else float(tolerance_bord))
    seuil_masque = (SEUIL_MASQUE_DEFAUT if seuil_masque is None
                    else float(seuil_masque))
    # The merged thresholds classify() computes internally, resolved once
    # here so mesure_ok (conf_ancre, below) and ilot_min (further down)
    # read the exact same preset-plus-CLI-override values classify() sorts
    # against.
    s_merged = (dict(SEUILS_DEFAUT) if seuils is None
               else dict(SEUILS_DEFAUT, **seuils))

    frames = donnees["frames"]
    conf = _colonne(frames, "conf", 0.0)
    # "bright" par defaut : une frame sans regime (cache anterieur, ou vote
    # unique) ne doit ni faire tomber classify() ni etre traitee comme une
    # frame sombre qu'elle n'a jamais mesuree.
    regime = np.array([f.get("regime") or "bright" for f in frames])
    verdicts = classify(_colonne(frames, "disk_p90", 0.0),
                        _colonne(frames, "limb_sharpness", 0.0),
                        _colonne(frames, "flare_ratio", 1e9),
                        conf, seuils,
                        level=_colonne(frames, "level", 0.0),
                        regime=regime)

    cx = _colonne(frames, "cx")
    cy = _colonne(frames, "cy")
    # La validite d'une mesure ne depend PAS du verdict de tri. Une frame
    # rejetee pour flou garde sa position juste, et la retrouve si
    # l'utilisateur la recupere dans le viewer — c'est ce qui corrige les 37
    # frames que le rendu cadrait jusqu'a 1109 px a cote, parce que la
    # decision humaine arrivait apres le calcul de la trajectoire.
    # Ce qui decide, c'est la coherence entre le centre et l'image : le masque
    # solaire capture-t-il la lumiere ? (voir quality.masse_captee)
    #
    # A vote that never locked has no position to offer, whatever
    # masse_captee says about the mask it was handed: masse_captee asks
    # "does this center explain the light", conf asks "did the vote find a
    # peak at all". conf_ancre (see quality.SEUILS_DEFAUT) is 0.0 for every
    # preset but sun (see presets.sort_defaults) -- there is no dedicated
    # CLI flag for it, an operator reaches it only through a preset or an
    # explicit --seuils override.
    capt = _colonne(frames, "masse_captee", 0.0)
    # conf_ancre gates the BRIGHT vote's confidence scale only (see
    # quality.SEUILS_DEFAUT's conf_ancre comment). The bright and dark
    # accumulators do not share a scale: on m2-res_852p six dark-regime
    # frames at third contact (1174-1179) measure correctly (0.0-0.2 px
    # error) at conf 0.030-0.035, but the sun preset's conf_ancre (0.04,
    # calibrated on the bright vote) discarded them, forcing smooth_track
    # to bridge to a neighbor ~200 px off (a 6x17 px window slide plus a
    # 154 px snap at third contact). The dark regime already has its own
    # gate above (masse_captee): a dark frame gets no confidence floor here.
    conf_ok = np.where(regime == "dark", True, conf >= s_merged["conf_ancre"])
    mesure_ok = np.isfinite(cx) & (capt >= seuil_masque) & conf_ok

    # kx/ky convertissent les coordonnees d'analyse en coordonnees SOURCE
    # pleine resolution — jamais en coordonnees de sortie, qui ne sont plus
    # les memes depuis le cadre resserre. Rapport exact des dimensions, et
    # non 1/scale, car les tailles d'analyse sont arrondies au pixel pair.
    kx = src_w / donnees["width"]
    ky = src_h / donnees["height"]
    if not mesure_ok.any():
        # Reported bug: with zero usable position, interpolate_invalid used
        # to raise and the viewer crashed while reloading its state. There
        # is nothing to interpolate FROM, so degrade honestly instead:
        # window centered on the source, every frame flagged no_lock. The
        # caller decides what to tell the user (see mesures_valides).
        n = len(frames)
        return {"verdicts": ["no_lock"] * n,
                "traj_x": np.full(n, src_w / 2.0),
                "traj_y": np.full(n, src_h / 2.0),
                "kx": kx, "ky": ky, "mesures_valides": 0}
    scx, scy = smooth_track(cx, cy, mesure_ok)

    # Le critere de bord s'applique a la trajectoire APRES interpolation
    # (scx, scy sont deja la sortie de smooth_track) : une frame interpolee
    # peut amputer le disque tout comme une frame mesuree. On fusionne avec
    # les verdicts de classify() puis on relance le nettoyage d'ilots, qui
    # peut reveler de nouvelles plages trop courtes que classify() ne
    # pouvait pas voir sans connaitre ces rejets.
    # ONE RADIUS PER FRAME, that of the regime it measured. A dual cache
    # carries two radii -- the solar limb and the larger lunar disc
    # covering it (see locate.locate_center_regime) -- and the hors_source
    # bound is about the DISC: computing it from the bright radius for a
    # totality frame would measure the wrong circle. Outside dual both
    # radii are equal and the array is constant, so the bound is the
    # previous one, byte for byte. A prior cache without radius_dark, or a
    # frame without a regime, count as bright.
    bright_radius = float(donnees["radius"])
    dark_radius = float(donnees.get("radius_dark") or bright_radius)
    is_dark = np.array([f.get("regime") == "dark" for f in frames], dtype=bool)
    rayon_visible = (np.where(is_dark, dark_radius, bright_radius) * kx
                     + MARGE_HALO)
    hors = verdicts_hors_source(scx * kx, scy * ky, rayon_visible,
                                src_w, src_h, tolerance_bord)

    # Le clip de fenetre et le sort des frames trop decentrees n'habitent
    # plus ici : la trajectoire de fenetre est PLANIFIEE dans render, apres
    # l'application des decisions, par track.planifie_trajectoire — le
    # corridor du planificateur depend des frames reellement gardees. Le
    # verdict decentre a ete supprime avec la butee dure : la revue humaine
    # a montre que son amplitude ne predisait pas le jugement de l'oeil
    # (medianes identiques, 76,9 px, des deux cotes du choix), et le
    # planificateur borne les sauts par construction.
    ilot_min = s_merged["ilot_min"]
    verdicts = supprime_ilots(
        [h or v for v, h in zip(verdicts, hors)], ilot_min)

    return {"verdicts": verdicts, "traj_x": scx * kx, "traj_y": scy * ky,
            "kx": kx, "ky": ky, "mesures_valides": int(mesure_ok.sum())}
