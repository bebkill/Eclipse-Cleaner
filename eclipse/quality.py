"""Mesures de qualite par frame et verdicts de tri.

Principe directeur : une frame decadree n'est pas une frame defectueuse.
Le stabilisateur corrige la position. On ne rejette que ce que le pipeline
ne sait pas reparer : le flou, la perte de lumiere, l'eblouissement,
l'echec de localisation.
"""
import numpy as np

from .locate import sobel
from .track import rolling_median

SEUILS_DEFAUT = {
    "dark_rel": 0.35,      # fraction de la mediane locale de disk_p90
    "dark_abs": 40.0,      # plancher absolu, attrape la perte durable de fin
    # Fraction de la mediane locale de limb_sharpness. A 0,50 les dernieres
    # frames du coucher etaient rejetees a tort : quand l'horizon mange le
    # disque, la nettete du limbe chute parce qu'il en reste moins a mesurer,
    # non parce que la camera bouge. 0,40 rend les frames 2495 a 2497, soit
    # les tout derniers instants du Soleil ; descendre plus bas n'en rend
    # aucune de plus.
    "blur_rel": 0.40,
    "flare_rel": 3.0,      # multiple de la mediane locale de flare_ratio
    "conf_min": 0.02,      # confiance minimale du vote de Hough
    # Anchor-confidence floor: whether a low-confidence vote may anchor the
    # cropping TRAJECTORY (see verdicts.analyse_verdicts's mesure_ok), a
    # question independent of conf_min above (which only drives the SORT
    # verdict no_lock -- a blurry or unlocked frame still keeps its correct
    # position there). Default 0.0 (off), so every preset but sun stays
    # bit-for-bit unchanged in pass 2: measured on the reference custom
    # video (2556 frames) and on Lunar-221924 (10548 frames), 44 and 163
    # respectively of the currently-valid frames sit under 0.02 despite
    # being correctly positioned -- their cx/cy track smoothly with their
    # confident neighbors, not the garbage jump a genuinely unlocked vote
    # produces. A universal floor would wrongly interpolate all of them.
    # The failure this floor targets -- an unlocked vote anchoring a
    # garbage center at masse_captee 1.0 -- is specific to the sun preset's
    # exposure-catastrophe transitions (see presets.sort_defaults), so it
    # ships scoped there instead of here.
    #
    # This floor gates the BRIGHT vote's confidence scale ONLY (see
    # verdicts.analyse_verdicts's mesure_ok): the bright and dark
    # accumulators do not share a scale, and applying the bright-calibrated
    # 0.04 to a dark-regime row is itself a measured failure, not a
    # hypothetical one. On m2-res_852p, third-contact frames 1174-1179
    # measure correctly in the dark regime (0.0-0.2 px error) at conf
    # 0.030-0.035, comfortably above the dark regime's own gate
    # (masse_captee) but below the bright-scaled 0.04 -- discarding them
    # forced smooth_track to bridge to frame 1184, ~200 px off, producing a
    # 6x17 px window slide plus a 154 px snap at third contact. Scoping the
    # floor to bright-regime frames fixed it: the third-contact zone's path
    # dropped from 277.9 to 77.6 px, and whole-video kept-frame window jumps
    # over 10 px fell from 14 to 8 (m2, user decisions and output size, pass
    # 2 only). The dark regime needs no floor of its own here because
    # masse_captee already rejects its garbage (the black frames 1180-1183
    # score masse_captee 0.000): measured, dark floors 0.030, 0.020, 0.010
    # and 0.0 all give identical verdicts on this video.
    "conf_ancre": 0.0,
    # Ecart maximal du niveau a sa mediane locale, en fraction. A 0,35 une
    # seule frame de la sequence reelle est ecartee (la 184 : niveau 36,3
    # contre 91,7 autour) ; a 0,25 il y en a neuf, a 0,15 onze, sans qu'aucun
    # saut residuel ne diminue -- ces frames-la n'etaient pas des defauts.
    "niveau_rel": 0.35,
    "fenetre_niveau": 31,  # reference du niveau : courte, voir classify()
    "fenetre_ref": 301,    # largeur de la reference locale, en frames
    # Longueur minimale d'une plage conservee. Neutralise (1 = aucun ilot
    # supprime) sur la revue humaine des 2556 frames (decisions du viewer,
    # 2026-08-15) : l'utilisateur a repris 29 ilots sur 29, y compris des
    # frames isolees entre deux coupes de la traversee nuageuse (28-38 s).
    # A l'ancienne valeur de 5, tous les ilots observes etaient des artefacts
    # de la fragmentation causee par des rejets hors_source trop stricts —
    # recalibres eux aussi (voir pipeline.TOLERANCE_BORD_DEFAUT) : au nouveau
    # reglage, ilot_min 5 ou 1 donnent le meme resultat sur la sequence. Le
    # verdict decentre et sa butee dure ont depuis disparu, remplaces par la
    # trajectoire planifiee (voir track.planifie_trajectoire). Le mecanisme
    # (supprime_ilots) reste en place, reactivable par --ilot-min si un autre
    # tournage produit de vrais eclairs.
    "ilot_min": 1,
}


def _grille(h, w):
    """Vecteurs de coordonnees diffuses (broadcast) pour une forme (h, w).

    Remplace np.mgrid[0:h, 0:w], qui alloue deux tableaux pleins (h, w) : ici
    yy est (h, 1) et xx est (1, w), combines par diffusion numpy au moment de
    l'usage sans jamais materialiser la grille complete. measure_quality et
    masse_captee batissaient chacune la meme grille ; partager cette fonction
    evite la double allocation sans introduire de cache garde par forme.
    """
    yy = np.arange(h, dtype=np.float32)[:, None]
    xx = np.arange(w, dtype=np.float32)[None, :]
    return yy, xx


#: Capture radius factor for dark-regime frames: the light to capture is
#: the corona around the disc, not the disc itself.
#:
#: Measured on m2-res_852p (a total solar eclipse, 901 dark-regime frames,
#: masse_captee recomputed on 50 of them at the cached centres):
#:
#:     factor   1.0     1.2     1.4     1.6     1.8     2.0     2.5
#:     median   0.001   0.435   0.913   1.000   1.000   1.000   1.000
#:     >= 0.80  0.000   0.020   0.920   0.960   1.000   1.000   1.000
#:
#: Without the widening (1.0) the climax of the video scores essentially
#: ZERO and every totality frame would be dropped from the trajectory. The
#: knee is between 1.4 and 1.6, and 1.6 is the first factor that captures
#: the ring whole -- median 1.000, p10 0.997.
#:
#: Deliberately not pushed to 1.8 or beyond for the last 4 %: the wider the
#: capture mask, the less masse_captee can tell a right centre from a wrong
#: one, and by 2.5 it returns 1.000 for every frame and has stopped
#: measuring anything. The minimum factor that captures the corona is the
#: one that keeps the check meaningful.
CORONA_FACTOR = 1.6

#: Radius factor bounding the halo margin in measure_quality, used TWICE and
#: necessarily by the same value: it is the outer edge of the dark-regime
#: disk_p90 ring, and the inner edge of the flare exclusion. Let the two
#: diverge and a dark frame would count its own corona as flare (ring outer
#: bound above the flare cut) or read a band no measure covers (below it).
FLARE_RING_FACTOR = 1.4


def measure_quality(gray, cx, cy, r, regime="bright"):
    """Mesures de qualite d'une frame, autour du centre fourni.

    regime "dark" (the winning vote regime of locate_center_regime) reads
    disk_p90 on the corona ring instead of inside the disc.
    """
    if not (np.isfinite(cx) and np.isfinite(cy)):
        return {"disk_p90": float("nan"), "limb_sharpness": float("nan"),
                "flare_ratio": float("nan")}

    g = gray.astype(np.float32)
    h, w = g.shape
    yy, xx = _grille(h, w)
    dx = xx - cx
    dy = yy - cy
    d2 = dx * dx + dy * dy
    # d2 <= r*r n'est PAS bit-exact a dist <= r en float32 : les deux seuils
    # arrondissent differemment (mesure sur la sequence reelle : 4 frames sur
    # 150 changent l'appartenance d'exactement un pixel, jusqu'a 9,97e-06 sur
    # flare_ratio). Comme les seuils de tri sont calibres sur ces mesures,
    # une racine unique ici est preferee a la forme au carre : le gain de
    # _grille (plus de mgrid) reste acquis, seul celui du carre est abandonne.
    # Mesure sur la sequence reelle a 540x960, measure_quality + masse_captee
    # ensemble, apres cet abandon partiel : 54,5 ms/frame -> 25,0 ms (-54%),
    # contre -58% avec la forme au carre finalement rejetee pour cause de
    # derive numerique (voir rapport-perf.md).
    dist = np.sqrt(d2)

    if regime == "dark":
        # A totality's subject is the corona: its light lives in the ring
        # OUTSIDE the dark disc. disk_p90 read inside would send the
        # climax of the video to too_dark; read it on the ring instead.
        interieur = (dist > r) & (dist <= FLARE_RING_FACTOR * r)
    else:
        interieur = dist <= r
    disk_p90 = float(np.percentile(g[interieur], 90.0)) if interieur.any() else 0.0

    # Nettete du limbe : gradient de pointe sur l'anneau, rapporte au
    # contraste du disque pour rester comparable d'un regime a l'autre.
    anneau = (dist >= 0.85 * r) & (dist <= 1.15 * r)
    if anneau.any() and disk_p90 > 1e-6:
        gx, gy = sobel(g)
        # mag2 = gx**2 + gy**2 = mag**2, mais np.percentile INTERPOLE entre
        # les deux statistiques d'ordre voisines : sqrt(percentile(mag2)) !=
        # percentile(sqrt(mag2)) en general (la racine est croissante, donc
        # preserve le RANG, mais pas l'interpolation lineaire entre deux
        # valeurs). Mesure sur la sequence reelle : jusqu'a 1,07e-05 d'ecart
        # sur limb_sharpness avec la forme fautive, au-dessus du seuil de
        # 1e-4 relatif qui deplacerait le sens d'un seuil de tri sur des cas
        # synthetiques plus defavorables (jusqu'a 3,5e-04). La racine est
        # donc appliquee au sous-ensemble mag2[anneau] AVANT le percentile,
        # pas au resultat scalaire du percentile : l'anneau ne pesant que
        # 11-14% de la frame, l'essentiel du gain (pas de racine sur toute
        # la carte de gradient) est conserve.
        # PERCENTILE 98, ET NON 90. Le limbe n'occupe que 1,7 a 2,2 % des
        # pixels de l'anneau -- mesure sur les frames 700, 745, 756, 775 et
        # 798 de la sequence reelle, ou l'anneau compte 58 700 a 74 100 px
        # pour 1 016 a 1 596 px de limbe. Le limbe siege donc vers le 98e
        # percentile, et un p90 ne l'atteint JAMAIS : il rendait 7 a 17 la
        # ou la mediane des pixels de limbe vaut 140 a 256.
        #
        # La consequence n'etait pas theorique. Sur les frames 750 a 800 le
        # p90 s'effondrait de 15,5 a 6,5 pendant que le limbe s'AFFINAIT --
        # largeur de transition 80/20 mesuree a 1,5-2,0 px contre 5,0-6,0 px
        # avant --, ce qui rejetait pour « flou » les neuf frames les plus
        # nettes de la sequence. Au p98 le creux disparait et ces neuf
        # frames repassent, sans qu'aucune frame reellement floue ne passe.
        mag2 = gx * gx + gy * gy
        limb_sharpness = (float(np.percentile(np.sqrt(mag2[anneau]), 98.0))
                          / disk_p90 * 100.0)
    else:
        limb_sharpness = 0.0

    # Eblouissement : masse lumineuse loin du disque, marge de halo exclue.
    dehors = dist > FLARE_RING_FACTOR * r
    if dehors.any() and disk_p90 > 1e-6:
        flare_ratio = float(g[dehors].mean()) / disk_p90
    else:
        flare_ratio = 0.0

    return {"disk_p90": disk_p90, "limb_sharpness": limb_sharpness,
            "flare_ratio": flare_ratio}


#: Fraction du pic de luminosite au-dela de laquelle un pixel est tenu pour
#: lumineux. Relatif au maximum de la frame, donc insensible aux deux regimes
#: d'exposition de la sequence (filtre solaire puis prise de vue directe).
SEUIL_LUMIERE = 0.35

#: Flat-frame floor for masse_captee's gray std, below which the frame is
#: taken to carry no exploitable structure at all.
#:
#: Measured truth (review correction: the frames first suspected here were
#: NOT what this guard actually catches). Diagnosed on m2-res_852p (a
#: total solar eclipse, preset sun), this guard's one real, verified
#: effect is frames 1180-1183: near-black with no real gradient (mean
#: 0.001, max 3.3, std 0.0311-0.0318), yet locate_center still returns a
#: FINITE center there (conf 0.0835, comfortably above conf_ancre) --
#: every position scores close to the same masse_captee value because
#: there is no contrast left to tell a right center from a wrong one.
#: Without this guard that finite-but-baseless center anchors the
#: trajectory roughly 200 analysis px from its confident neighbors (118/210
#: just before, 94/155 just after).
#:
#: The originally-suspected frames on the same video are NOT caught here:
#: 277/279 (mean 0.11, max 3.7) measure std 0.365-0.368, well ABOVE this
#: floor -- their sparse hot pixels on an otherwise near-black frame keep
#: the std too high for a flatness guard to see. That garbage is instead
#: excluded by the anchor-confidence floor (see presets.sort_defaults's
#: conf_ancre): their vote never locks, conf 0.0040-0.0076. And 280-283
#: (uniformly blown out, std 0.000 exactly) never reach this guard at all
#: in the real pipeline: locate_center itself returns a non-finite center
#: on them, so masse_captee's own isfinite(cx) check (above) returns NaN
#: first -- this guard would ALSO catch them if it ran, but it is moot.
#:
#: The floor is calibrated DOWN from the 0.0311-0.0318 range it actually
#: targets, not up to it: the reference custom-preset video (2556 frames)
#: has a legitimate frame (1495, a cloud-lit dusk sky, mean 0.04 max 1.7)
#: at std 0.1285 with a real, useful masse_captee of 0.068 -- well above
#: the garbage this floor targets. A floor at or above 0.1285 would NaN
#: that frame too and change the custom preset's measures, which the guard
#: must not do (see test_masse_captee_normal_frames_unchanged and the
#: byte-identity gate on the reference video). 0.05 sits well under every
#: finite-masse frame found in that census, while still catching the
#: genuinely flat 1180-1183 case (and anything flatter).
FLAT_STD_FLOOR = 0.05

#: Lit-fraction cap for a saturated-but-not-perfectly-flat frame, and the
#: slightly looser std ceiling it is allowed to fire under. A frame where
#: the SEUIL_LUMIERE-relative "lit" mask already covers most of the image
#: is trivially uniform, whatever position is tried -- but taken alone, a
#: high lit fraction is not unusual on genuinely dim, low-contrast frames
#: (the reference video's frame 2277 reads 100% lit at std 0.359, with a
#: real, correctly-low masse_captee of 0.163): the cap only adds
#: information once corroborated by near-flatness.
#:
#: Measured truth (review correction: this branch was not isolated by any
#: test, and does not fire on any real frame found so far). Every frame
#: flat enough to trip it (std < 0.12) in the m2 and reference censuses is
#: ALSO flat enough to trip FLAT_STD_FLOOR first (std < 0.05) -- frames
#: 280-283's std 0.000 is the closest real candidate, and FLAT_STD_FLOOR
#: returns NaN before this line ever runs (see its own comment). This
#: branch exists for the case FLAT_STD_FLOOR alone would miss: a saturated
#: sensor with quantization noise (std strictly between the two floors,
#: lit fraction still near 100%) rather than an exactly constant one.
#: Isolated synthetically in test_masse_captee_isolates_the_saturated_branch,
#: pinning both floors, since no real frame does it yet. SATURATED_STD_FLOOR
#: stays under the same 0.1285 reference floor as FLAT_STD_FLOOR, with a
#: margin, so it cannot reach frame 2277 (std 0.359) either.
SATURATED_LIT_CAP = 0.60
SATURATED_STD_FLOOR = 0.12


def masse_captee(gray, cx, cy, r, seuil_lumiere=SEUIL_LUMIERE):
    """Fraction de la lumiere de l'image contenue dans le disque (cx, cy, r).

    seuil_lumiere overrides SEUIL_LUMIERE: some profiles need the shadowed
    part of the subject counted as light to capture, not background.

    Repond a une question que ni la confiance du vote ni le verdict de tri ne
    posent : le centre trouve explique-t-il l'image ? La confiance mesure la
    force d'un pic de vote, pas sa justesse — les frames dont le cadrage etait
    faux affichaient 0,072 a 0,094, au-dessus de tous les seuils.

    Mesure sur la sequence reelle, 2525 frames : mediane 0,997, p10 0,964,
    p1 0,370. Les 33 frames sous 0,50 ont toutes une zone lumineuse 1,4 a 2,4
    fois plus large que le diametre du Soleil — nuages eclaires par derriere,
    eblouissements, ciel crepusculaire apres le coucher. Aucune n'a de disque
    reel mal localise : la mesure juge la coherence, elle ne cherche pas mieux.

    Attention a sa portee : ce qui compte est la zone LUMINEUSE, pas le
    disque entier, si bien que la sensibilite depend de la phase d'eclipse.
    Mesure sur la sequence reelle, pour une erreur de centre de 50 px en
    pleine resolution : la capture tombe a 0,87 au debut, 0,61 en eclipse
    profonde, mais reste a 0,99 sur le croissant fin de la fin. Ce critere
    attrape donc un centre qui tombe sur autre chose, pas un centre decale
    de quelques dizaines de pixels.

    Retourne NaN si le centre n'est pas fini, si l'image n'a pas de lumiere
    exploitable, ou si elle n'a pas de structure du tout (voir
    FLAT_STD_FLOOR / SATURATED_LIT_CAP) — a l'appelant de decider ce qu'il
    en fait.
    """
    if not (np.isfinite(cx) and np.isfinite(cy)):
        return float("nan")
    g = np.asarray(gray, dtype=np.float32)
    pic = float(g.max())
    if pic <= 1e-6:
        return float("nan")
    # No contrast, no position information: a flat frame (near-black with
    # no gradient, or blown-out uniformly white) scores every candidate
    # center almost the same, which is exactly the degeneracy that anchored
    # a wrong trajectory on m2-res_852p (see FLAT_STD_FLOOR). Checked BEFORE
    # any capture logic runs.
    std_dev = float(g.std())
    if std_dev < FLAT_STD_FLOOR:
        return float("nan")
    lumiere = g > seuil_lumiere * pic
    if std_dev < SATURATED_STD_FLOOR and float(lumiere.mean()) > SATURATED_LIT_CAP:
        return float("nan")
    masse = float(g[lumiere].sum())
    if masse <= 0.0:
        return float("nan")
    h, w = g.shape[:2]
    yy, xx = _grille(h, w)
    dx = xx - float(cx)
    dy = yy - float(cy)
    dedans = (dx * dx + dy * dy) <= float(r) ** 2
    return float(g[lumiere & dedans].sum()) / masse


def _odd_window(n, largeur):
    """The odd-window rule from classify, factored out: bounded to n,
    always odd, never zero."""
    k = min(largeur, n if n % 2 == 1 else n - 1)
    return max(k if k % 2 == 1 else k - 1, 1)


#: Below this many frames, a regime's own subsequence is too short for its
#: rolling median to measure anything: at length 1 the reference IS the
#: value itself (a lone frame's own median is itself), so a rare regime
#: with only a handful of frames would pass every relative criterion by
#: construction, whatever its actual value. Measured: a single spurious
#: dark-regime frame (sharpness 1.0 against 100.0 everywhere else) kept its
#: own value as its reference and was never flagged motion_blur. Below
#: this floor the regime falls back to the GLOBAL rolling reference
#: instead of its own -- exactly what a cache without regime splitting
#: already does for every frame. 15 is a fixed, deliberately generous
#: floor: it costs nothing on the sequences this splitting targets (m2's
#: two regimes run to hundreds of frames each) and only ever engages for a
#: minority regime too thin to say anything about itself.
MIN_REGIME_FRAMES = 15


def _per_regime_reference(valeurs, largeur, regime):
    """Local reference (rolling median), split by vote regime.

    regime absent, or a single regime present over the whole sequence:
    reproduces the GLOBAL rolling_median(valeurs, ...) byte for byte --
    this is what keeps the custom preset (single vote) unchanged.

    The two regimes MIXED inside a single window is the defect measured on
    m2-res_852p: bright (median sharpness 196) and dark (median sharpness
    385) alternate 9 times between frames 250 and 299, and a normal bright
    frame (sharpness ~196, not blurry) ends up judged against a reference
    inflated by its dark neighbors. So the sequence is split into two
    subsequences (one per regime, temporal order preserved), each gets its
    own rolling median with the same odd-window rule but sized to ITS OWN
    length, and the result is redistributed back to the original
    positions.

    A regime whose subsequence is shorter than MIN_REGIME_FRAMES instead
    reads the GLOBAL rolling reference at its own positions (see
    MIN_REGIME_FRAMES): a subsequence that short has no median worth
    computing on its own, and would otherwise validate itself no matter
    its value.
    """
    valeurs = np.asarray(valeurs, dtype=np.float64)
    n = len(valeurs)
    modes = () if regime is None else np.unique(np.asarray(regime))
    if len(modes) < 2:
        return rolling_median(valeurs, _odd_window(n, largeur))
    regime = np.asarray(regime)
    ref = np.empty(n, dtype=np.float64)
    ref_globale = None
    for mode in modes:
        idx = np.flatnonzero(regime == mode)
        if len(idx) < MIN_REGIME_FRAMES:
            if ref_globale is None:
                ref_globale = rolling_median(valeurs, _odd_window(n, largeur))
            ref[idx] = ref_globale[idx]
            continue
        sous = valeurs[idx]
        ref[idx] = rolling_median(sous, _odd_window(len(sous), largeur))
    return ref


def classify(disk_p90, limb_sharpness, flare_ratio, confiance, seuils=None,
             level=None, regime=None):
    """Verdict par frame : motif de rejet, ou None si conservee.

    Chaque mesure est comparee a sa mediane glissante plutot qu'a un seuil
    absolu. La nettete passe de ~13 a ~150 a la frame 1050 de la video reelle,
    a cause du changement d'exposition : un seuil global rejetterait toute la
    premiere moitie de la sequence.

    regime, s'il est fourni ET que les DEUX regimes apparaissent, calcule
    ref_p90/ref_sharp/ref_flare PAR REGIME plutot que sur toute la sequence
    (voir _per_regime_reference) : un cache a vote unique, ou un regime
    absent, reste identique a l'octet. La reference COURTE du niveau
    (fenetre_niveau) reste volontairement GLOBALE, meme ici : les paliers
    d'exposition qu'elle doit suivre (voir plus bas) sont un phenomene de
    la CAMERA, pas du regime de vote, et scinder cette reference-la
    n'apporterait rien.

    level, s'il est fourni, ajoute le verdict « niveau_aberrant » : une frame
    dont la luminance s'ecarte trop de sa mediane locale. Facultatif pour que
    les appelants anterieurs restent valides ; le viewer et le rendu le
    passent tous les deux.
    """
    s = dict(SEUILS_DEFAUT) if seuils is None else dict(SEUILS_DEFAUT, **seuils)
    p90 = np.nan_to_num(np.asarray(disk_p90, dtype=np.float64), nan=0.0)
    sharp = np.nan_to_num(np.asarray(limb_sharpness, dtype=np.float64), nan=0.0)
    flare = np.nan_to_num(np.asarray(flare_ratio, dtype=np.float64), nan=1e9)
    conf = np.nan_to_num(np.asarray(confiance, dtype=np.float64), nan=0.0)
    n = len(p90)

    ref_p90 = _per_regime_reference(p90, s["fenetre_ref"], regime)
    ref_sharp = _per_regime_reference(sharp, s["fenetre_ref"], regime)
    ref_flare = _per_regime_reference(flare, s["fenetre_ref"], regime)

    trop_sombre = (p90 < s["dark_rel"] * ref_p90) | (p90 < s["dark_abs"])
    pas_de_lock = conf < s["conf_min"]
    floue = sharp < s["blur_rel"] * ref_sharp
    eblouie = flare > s["flare_rel"] * np.maximum(ref_flare, 1e-6)

    # Ecart de luminance a la mediane locale. La correction de gain
    # (photometry.solve_corrections) LISSE la courbe de niveau avant de
    # l'inverser -- volontairement, pour ne pas effacer l'evolution reelle --
    # et laisse donc passer une pointe d'une seule frame. Mesure sur la
    # sequence reelle : saut de 189 % entre deux frames conservees avant
    # correction, 188,7 % apres. Le gain ne rattrape pas ce genre de defaut,
    # il faut donc l'ecarter au tri.
    #
    # La fenetre est COURTE (31) la ou les autres references en prennent 301 :
    # l'exposition automatique du smart-telescope change par paliers francs
    # -- 99,7 puis 156 puis 209 autour de la frame 1085 de la sequence de
    # reference --, et une fenetre longue prendrait ces paliers pour des
    # aberrations. A 31 frames la reference suit le palier et ne signale que
    # ce qui s'en detache.
    if level is None:
        aberrante = np.zeros(n, dtype=bool)
    else:
        lv = np.nan_to_num(np.asarray(level, dtype=np.float64), nan=0.0)
        kn = min(s["fenetre_niveau"], n if n % 2 == 1 else n - 1)
        kn = max(kn if kn % 2 == 1 else kn - 1, 1)
        ref_lv = rolling_median(lv, kn)
        aberrante = (np.abs(lv - ref_lv)
                     > s["niveau_rel"] * np.maximum(ref_lv, 1e-6))

    verdicts = []
    for i in range(n):
        if pas_de_lock[i]:
            verdicts.append("no_lock")
        elif trop_sombre[i]:
            verdicts.append("too_dark")
        elif aberrante[i]:
            verdicts.append("niveau_aberrant")
        elif floue[i]:
            verdicts.append("motion_blur")
        elif eblouie[i]:
            verdicts.append("flare")
        else:
            verdicts.append(None)

    return supprime_ilots(verdicts, s["ilot_min"])


def verdicts_hors_source(cx, cy, rayon_visible, src_w, src_h, tolerance):
    """Rejette les frames ou le DISQUE sort de l'image filmee.

    A distinguer du critere precedent (verdicts_cadrage, supprime), qui
    exigeait que toute la fenetre de recadrage tienne dans la source : il
    rejetait le coucher de Soleil, dont le centre descend a y = 1504 px
    quand la fenetre n'en tolerait que 1173.

    Ce qui compte n'est pas que la fenetre deborde — elle est butee contre
    les bords, voir pipeline.render — mais que le disque lui-meme soit
    ampute. Une tolerance de quelques dizaines de pixels sur un disque de
    798 px reste invisible.

    Les coordonnees sont en pleine resolution source.

    rayon_visible accepts a scalar OR a per-frame array: a dual-vote cache
    does not have the same visible radius in both regimes (see
    verdicts.analyse_verdicts).
    """
    cx = np.asarray(cx, dtype=np.float64)
    cy = np.asarray(cy, dtype=np.float64)
    r = np.asarray(rayon_visible, dtype=np.float64) - float(tolerance)
    ok = (np.isfinite(cx) & np.isfinite(cy)
          & (cx >= r) & (cx <= src_w - r) & (cy >= r) & (cy <= src_h - r))
    return [None if o else "hors_source" for o in ok]


def supprime_ilots(verdicts, ilot_min):
    """Rejette les plages conservees trop courtes pour ne pas saccader.

    Un ilot n'est supprime que s'il est borde de part et d'autre par des
    plages rejetees d'au moins ilot_min frames.
    """
    n = len(verdicts)
    garde = np.array([v is None for v in verdicts], dtype=bool)
    out = list(verdicts)

    i = 0
    while i < n:
        if not garde[i]:
            i += 1
            continue
        j = i
        while j < n and garde[j]:
            j += 1
        longueur = j - i
        if longueur < ilot_min:
            avant = _longueur_rejet(garde, i - 1, -1)
            apres = _longueur_rejet(garde, j, +1)
            if avant >= ilot_min and apres >= ilot_min:
                for k in range(i, j):
                    out[k] = "ilot"
        i = j
    return out


def _longueur_rejet(garde, depart, pas):
    """Longueur de la plage rejetee contigue a partir de depart."""
    n = len(garde)
    compte = 0
    i = depart
    while 0 <= i < n and not garde[i]:
        compte += 1
        i += pas
    return compte
