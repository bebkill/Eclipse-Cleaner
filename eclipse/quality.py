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
#: Diagnosed on m2-res_852p (a total solar eclipse, preset sun): an
#: exposure catastrophe around frames 264-285 leaves some frames near-black
#: with no real gradient (277/279: mean 0.11, max 3.7, std 0.365-0.368) and
#: others uniformly blown out (280/281: mean/max 252.0, std 0.000 exactly).
#: On either, EVERY position scores close to the same masse_captee value
#: because there is no contrast left to tell a right center from a wrong
#: one -- measured returning 1.000 for a center 165 analysis px from the
#: true one, ahead of the correct center's own 0.865.
#:
#: The floor is calibrated DOWN from that 0.365-0.368 range, not up to it:
#: the reference custom-preset video (2556 frames) has a legitimate frame
#: (1495, a cloud-lit dusk sky, mean 0.04 max 1.7) at std 0.1285 with a
#: real, useful masse_captee of 0.068 -- lower std than m2's own diagnosed
#: garbage. A floor at or above 0.1285 would NaN that frame too and change
#: the custom preset's measures, which the guard must not do (see
#: test_masse_captee_frames_normales_inchangees and the byte-identity gate
#: on the reference video). 0.05 sits well under every finite-masse frame
#: found in that census, while still catching genuinely flat input.
FLAT_STD_FLOOR = 0.05

#: Lit-fraction cap for the saturated-uniform half of the same guard, and
#: the slightly looser std ceiling it is allowed to fire under. A frame
#: where the SEUIL_LUMIERE-relative "lit" mask already covers most of the
#: image is trivially uniform, whatever position is tried -- but taken
#: alone, a high lit fraction is not unusual on genuinely dim, low-contrast
#: frames (the reference video's frame 2277 reads 100% lit at std 0.359,
#: with a real, correctly-low masse_captee of 0.163): the cap only adds
#: information once corroborated by near-flatness. SATURATED_STD_FLOOR
#: stays under the same 0.1285 reference floor as FLAT_STD_FLOOR, with a
#: margin, so it cannot reach frame 2277 (std 0.359) either -- it only
#: widens the exact-flat case to frames a hair less than perfectly constant
#: (quantization noise on an otherwise blown-out sensor, say), which
#: FLAT_STD_FLOOR alone would miss.
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
    ecart = float(g.std())
    if ecart < FLAT_STD_FLOOR:
        return float("nan")
    lumiere = g > seuil_lumiere * pic
    if ecart < SATURATED_STD_FLOOR and float(lumiere.mean()) > SATURATED_LIT_CAP:
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


def _fenetre_impaire(n, largeur):
    """La regle de fenetre impaire de classify, factorisee : bornee a n,
    toujours impaire, jamais nulle."""
    k = min(largeur, n if n % 2 == 1 else n - 1)
    return max(k if k % 2 == 1 else k - 1, 1)


def _reference_par_regime(valeurs, largeur, regime):
    """Reference locale (mediane glissante), scindee par regime de vote.

    regime absent, ou un seul regime present sur toute la sequence :
    reproduit rolling_median(valeurs, ...) globale, a l'octet -- c'est ce
    qui garde le preset custom (vote unique) inchange.

    Les deux regimes MELANGES dans une seule fenetre, c'est le defaut
    mesure sur m2-res_852p : le clair (nettete mediane 196) et le sombre
    (nettete mediane 385) alternent 9 fois entre les frames 250 et 299, et
    une frame claire normale (nettete ~196, pas floue) se retrouve jugee a
    l'aune d'une reference gonflee par ses voisines sombres. On scinde donc
    la sequence en deux sous-suites (une par regime, ordre temporel
    preserve), on calcule la mediane glissante de CHACUNE avec la meme
    regle de fenetre impaire mais dimensionnee a SA PROPRE longueur, puis
    on redistribue le resultat aux positions d'origine.
    """
    valeurs = np.asarray(valeurs, dtype=np.float64)
    n = len(valeurs)
    modes = () if regime is None else np.unique(np.asarray(regime))
    if len(modes) < 2:
        return rolling_median(valeurs, _fenetre_impaire(n, largeur))
    regime = np.asarray(regime)
    ref = np.empty(n, dtype=np.float64)
    for mode in modes:
        idx = np.flatnonzero(regime == mode)
        sous = valeurs[idx]
        ref[idx] = rolling_median(sous, _fenetre_impaire(len(sous), largeur))
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
    (voir _reference_par_regime) : un cache a vote unique, ou un regime
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

    ref_p90 = _reference_par_regime(p90, s["fenetre_ref"], regime)
    ref_sharp = _reference_par_regime(sharp, s["fenetre_ref"], regime)
    ref_flare = _reference_par_regime(flare, s["fenetre_ref"], regime)

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

    Les coordonnees sont en pleine resolution source. rayon_visible accepte
    un scalaire OU un tableau par frame : un cache a vote dual n'a pas le
    meme rayon visible dans les deux regimes (voir verdicts.analyse_verdicts).
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
