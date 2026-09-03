"""Localisation du disque solaire par vote de Hough dirige a rayon fixe.

Le rayon apparent du Soleil est constant sur toute la sequence. On l'estime
une fois, puis on ne resout plus que deux inconnues (cx, cy) au lieu de trois,
ce qui reste precis sur un croissant fin et tolere qu'un nuage mange une
partie de l'arc.
"""
import numpy as np

MAX_POINTS_CONTOUR = 20000


def sobel(gray):
    """Gradients horizontal et vertical, convention x vers la droite."""
    g = gray.astype(np.float32)
    p = np.pad(g, 1, mode="edge")
    gx = ((p[:-2, 2:] + 2.0 * p[1:-1, 2:] + p[2:, 2:])
          - (p[:-2, :-2] + 2.0 * p[1:-1, :-2] + p[2:, :-2]))
    gy = ((p[2:, :-2] + 2.0 * p[2:, 1:-1] + p[2:, 2:])
          - (p[:-2, :-2] + 2.0 * p[:-2, 1:-1] + p[:-2, 2:]))
    return gx, gy


#: "max" lit-mask mode: a pixel is lit above this fraction of the frame
#: maximum. Exists because the percentile mode fails BOTH ways off the
#: reference sequence (measured, spec 2026-08-31): on a small moon (1.7 %
#: of the pixels) the p99 falls in the sky; on a large half-shadowed moon
#: the halfway threshold cuts the umbral part (area radius 73-132 px for a
#: constant 195 px disc). 0.08 sits under the umbral level of the measured
#: videos (10-25 % of max) and above sensor noise on a black sky.
#:
#: CALIBRATION VERDICT (task 11, 2026-09-01): the mask is a real
#: improvement on the percentile one, and it is still not enough. Swept
#: against an independently measured true radius, the AREA method under
#: this mask never comes close on an eclipsed disc:
#:
#:     fraction        0.02    0.04    0.08    0.10    0.15    0.20
#:     Lunar-213307   -5.6 %  -7.7 % -10.8 % -12.8 % -17.1 % -21.8 %
#:     Moon-Eclipse  -37.0 % -38.0 % -40.2 % -41.0 % -43.8 % -46.6 %
#:
#: (percentile mode, for scale: -39.7 % and -32.3 %). No fraction rescues
#: it, because the error is not a threshold error: the lit AREA shrinks as
#: the umbra advances while the disc does not, so the estimate drifts
#: within a single video -- 178 px down to 110 across Lunar-213307, for a
#: disc that stays at 196. The vote scan lands at -0.3 % and -1.5 % on the
#: same two videos, which is why every profile setting lit_mode "max" also
#: sets radius_mode "scan" (see presets), and why no shipped profile
#: currently reaches this constant at all. 0.08 is left as measured: it is
#: the fallback for an explicit area+max combination, and the sweep gives
#: no ground to prefer another value on a path nothing takes.
LIT_MAX_FRACTION = 0.08


def lit_mask(gray, mode="percentile"):
    """Region eclairee : seuil a mi-chemin entre le fond et le p99.

    mode "max": threshold relative to the frame maximum instead, for
    profiles whose subject may be small or partly shadowed (see
    LIT_MAX_FRACTION). The default stays byte-identical to the historic
    behaviour.

    An unknown mode is refused rather than quietly served by the
    percentile path: a typo would otherwise measure a whole sequence under
    the wrong mask without a word.
    """
    if mode not in ("percentile", "max"):
        raise ValueError(f"Mode d'eclairement inconnu : {mode!r}")
    g = gray.astype(np.float32)
    if mode == "max":
        pic = float(g.max())
        if pic < 1e-6:
            return np.zeros(g.shape, dtype=bool)
        return g >= LIT_MAX_FRACTION * pic
    haut = float(np.percentile(g, 99.0))
    bas = float(np.percentile(g, 5.0))
    if haut - bas < 1e-6:
        return np.zeros(g.shape, dtype=bool)
    return g >= (bas + haut) * 0.5


def estimate_radius(grays, n_candidats=50, lit_mode="percentile"):
    """Rayon apparent du Soleil, estime sur les frames les plus pleines.

    Pour un disque plein, l'aire donne le rayon exactement. On retient les
    n_candidats frames de plus grande aire eclairee et on prend la mediane :
    elle ecarte les mesures parasitees sans exiger que chaque frame soit
    parfaite.

    grays est consomme paresseusement : seules les aires sont retenues,
    jamais les images. Une sequence de 2556 frames en 540x960 pesant 4 Go,
    l'appelant doit passer un generateur, pas une liste.

    lit_mode is forwarded to lit_mask, see its "max" mode.
    """
    aires = np.array([float(lit_mask(g, mode=lit_mode).sum()) for g in grays],
                     dtype=np.float64)
    if not np.any(aires > 0):
        raise ValueError(
            "Aucune frame eclairee : impossible d'estimer le rayon. "
            "Fournir --radius explicitement."
        )
    k = min(n_candidats, int((aires > 0).sum()))
    meilleurs = np.argsort(aires)[-k:]
    return float(np.median(np.sqrt(aires[meilleurs] / np.pi)))


#: Radius scan bounds and steps. Geometric coarse sweep (12 % steps), one
#: linear refinement, then a 1 px refinement: 25-35 votes per frame. The
#: scan runs on a handful of sample frames once per analysis, never per
#: frame. Validated on the two user Seestar videos before being coded:
#: best-confidence radius 194-196 px on all 8 probed frames while the lit
#: area said 73-132 (spec 2026-08-31).
SCAN_COARSE_STEP = 1.12
SCAN_RMAX_FRACTION = 0.6


def scan_radius(grays, vote="bright", r_min=8.0, r_max=None):
    """Radius maximizing the directed-vote peak confidence.

    grays: a LIST of grayscale frames, already sampled by the caller.
    Per frame: coarse geometric sweep, then two refinements around the
    best candidate. Frames whose best confidence is under half the best
    of the batch are dropped (empty sky, clouds); the median of the
    survivors is returned. Raises ValueError when nothing is usable —
    the caller should suggest an explicit --radius.
    """
    grays = list(grays)
    if not grays:
        raise ValueError("Aucune frame pour balayer le rayon")
    h, w = grays[0].shape
    if r_max is None:
        r_max = SCAN_RMAX_FRACTION * min(h, w)

    candidates = []
    r = float(r_min)
    while r <= r_max:
        candidates.append(r)
        r *= SCAN_COARSE_STEP

    best_per_frame = []            # (confidence, radius) per usable frame
    for g in grays:
        scored = [(locate_center(g, rc, vote=vote)[2], rc)
                  for rc in candidates]
        conf, rc = max(scored)
        if conf <= 0.0:
            continue
        # Linear refinement inside the geometric step, then at 1 px.
        for step in (max(1.0, 0.04 * rc), 1.0):
            low, high = rc - 3.0 * step, rc + 3.0 * step
            fine = np.arange(max(r_min, low), high + step / 2, step)
            conf, rc = max((locate_center(g, float(rf), vote=vote)[2],
                            float(rf)) for rf in fine)
        best_per_frame.append((conf, rc))

    if not best_per_frame:
        raise ValueError(
            "Aucune frame ne donne de pic de vote exploitable : "
            "impossible de balayer le rayon. Fournir --radius.")
    ceiling = max(c for c, _ in best_per_frame)
    radii = [rc for c, rc in best_per_frame if c >= 0.5 * ceiling]
    return float(np.median(radii))


def _vote_center(gray, r, sign):
    """Centre du disque solaire par vote dirige.

    En chaque point de contour, la normale n du gradient pointe vers les
    luminances croissantes. Sur le limbe solaire elle pointe vers l'interieur
    du disque : le vote p + r*n tombe exactement sur le centre. Sur le bord
    lunaire elle pointe a l'oppose du centre de la Lune, et les votes tracent
    un arc de rayon r_lune + r_soleil : etales, sans pic.

    Retourne (cx, cy, confiance). confiance est la fraction du poids des
    votes tombes dans l'accumulateur (ceux hors cadre sont deja ecartes)
    qui atterrit dans le pic ; 0.0 si aucun contour exploitable.
    """
    g = gray.astype(np.float32)
    h, w = g.shape
    gx, gy = sobel(g)
    mag = np.sqrt(gx * gx + gy * gy)

    mag_max = float(mag.max())
    if mag_max < 1e-6:
        return float("nan"), float("nan"), 0.0

    forts = mag >= 0.25 * mag_max
    ys, xs = np.nonzero(forts)
    if len(xs) == 0:
        return float("nan"), float("nan"), 0.0

    poids = mag[ys, xs]
    if len(xs) > MAX_POINTS_CONTOUR:
        garde = np.argsort(poids)[-MAX_POINTS_CONTOUR:]
        ys, xs, poids = ys[garde], xs[garde], poids[garde]

    nx = gx[ys, xs] / poids
    ny = gy[ys, xs] / poids
    vx = xs + sign * r * nx
    vy = ys + sign * r * ny

    # L'accumulateur deborde du cadre : le centre peut tomber dehors quand
    # l'horizon tranche le disque.
    pad = int(np.ceil(r)) + 2
    acc = np.zeros((h + 2 * pad, w + 2 * pad), dtype=np.float32)
    ax = vx + pad
    ay = vy + pad
    dans = (ax >= 0) & (ax < acc.shape[1] - 1) & (ay >= 0) & (ay < acc.shape[0] - 1)
    ax, ay, wv = ax[dans], ay[dans], poids[dans]
    if len(ax) == 0:
        return float("nan"), float("nan"), 0.0

    # Depot bilineaire : la position fractionnaire du vote porte la
    # precision sous-pixel.
    x0 = np.floor(ax).astype(np.int64)
    y0 = np.floor(ay).astype(np.int64)
    fx = (ax - x0).astype(np.float32)
    fy = (ay - y0).astype(np.float32)
    for dx, dy, poids_coin in (
        (0, 0, (1 - fx) * (1 - fy)),
        (1, 0, fx * (1 - fy)),
        (0, 1, (1 - fx) * fy),
        (1, 1, fx * fy),
    ):
        np.add.at(acc, (y0 + dy, x0 + dx), wv * poids_coin)

    acc = _lisse3(_lisse3(acc))

    pic = int(np.argmax(acc))
    py, px = np.unravel_index(pic, acc.shape)
    if acc[py, px] <= 0.0:
        return float("nan"), float("nan"), 0.0

    concurrent = _concurrent_vertical(acc, py, px, r)
    if concurrent is not None and concurrent[0] > py:
        # ALIGNEMENT EN HAUT : entre les deux pics on retient toujours celui
        # de plus grand cy, c'est-a-dire celui qu'alimente l'arc SUPERIEUR
        # du disque. Voir _concurrent_vertical pour le pourquoi.
        #
        # SEUL LE Y EST REPRIS, et c'est mesure. Le defaut du rayon fixe
        # dedouble le pic VERTICALEMENT ; les deux pics ne different en x que
        # par le bruit de l'accumulateur, et reprendre le x du concurrent
        # transportait ce bruit dans la mesure horizontale. Erreur horizontale
        # contre une verite de terrain independante, sur 114 frames de la zone
        # difficile : maximum de 20,6 px en reprenant les deux coordonnees,
        # 10,3 px en ne reprenant que le y. Le vertical, lui, ne bouge pas
        # (p90 14,29 contre 14,53) : ce x n'apportait rien et coutait.
        py = concurrent[0]

    raffine = _barycentre(acc, py, px, pad)
    if raffine is None:
        return float("nan"), float("nan"), 0.0
    cx, cy, somme = raffine
    return cx, cy, min(float(somme / max(wv.sum(), 1e-9)), 1.0)


def locate_center(gray, r, vote="bright", r_dark=None):
    """Disc center by directed vote; see _vote_center for the method.

    vote "dark" flips the normals (p - r*n): on a dark disc ringed by
    light — a solar totality — the gradient at the limb points OUTWARD,
    and the bright vote scatters its votes 2r away from the center. vote
    "dual" evaluates both regimes and returns the sharper peak: the
    crescent -> totality -> crescent transition happens inside a single
    video, each frame must pick for itself.

    r_dark: the radius the DARK regime votes at, see locate_center_regime.
    """
    return locate_center_regime(gray, r, vote, r_dark)[0]


def locate_center_regime(gray, r, vote="dual", r_dark=None):
    """((cx, cy, conf), regime) — the winning regime alongside the fix.

    mesure_frame stores the regime in the cache: quality measures read a
    BRIGHT disc inside the radius but a DARK disc's light lives in the
    ring outside it (see quality.measure_quality's regime parameter).

    r_dark is the radius the DARK vote uses; None means "the same r",
    which keeps every historic call byte-identical.

    A DUAL sequence has TWO radii and needs both, because the two regimes
    do not measure the same circle: the bright vote fits the SOLAR limb,
    the dark vote fits the LUNAR disc that covers it, and the moon is the
    larger of the two. Measured on m2-res_852p (a total solar eclipse):
    solar limb 87-88 analysis px against 93.8 +/- 0.3 for the dark disc,
    7.3 % apart. Voting the dark regime at the bright radius is not merely
    imprecise, it is DEGENERATE: a limb point at c + r_true*u votes at
    c + (r_true - r)*u, so every vote lands on a CIRCLE of radius
    |r_true - r| = 6.9 px around the true centre instead of on a peak.
    The accumulator then carried four near-equal maxima (weakest/strongest
    up to 0.99) and the argmax alternated between x = 113.9 and x = 125.1
    for a true centre of 119.9 -- 58 horizontal jumps of 24 source px over
    the totality, at 1.95 jumps per second. Scanned at 93.93 the jumps fall
    to 2 (both real, at third contact), the peak confidence rises 4.5x
    (0.095 -> 0.423) and the measured centre lands on ground truth.
    """
    if vote == "bright":
        return _vote_center(gray, r, +1.0), "bright"
    r_dark = r if r_dark is None else r_dark
    if vote == "dark":
        return _vote_center(gray, r_dark, -1.0), "dark"
    if vote != "dual":
        raise ValueError(f"Regime de vote inconnu : {vote!r}")
    bright = _vote_center(gray, r, +1.0)
    dark = _vote_center(gray, r_dark, -1.0)
    return (bright, "bright") if bright[2] >= dark[2] else (dark, "dark")


def _barycentre(acc, py, px, pad):
    """(cx, cy, masse) du pic, raffine sur un voisinage 5x5 ; None si vide."""
    y1, y2 = max(0, py - 2), min(acc.shape[0], py + 3)
    x1, x2 = max(0, px - 2), min(acc.shape[1], px + 3)
    bloc = acc[y1:y2, x1:x2].astype(np.float64)
    somme = bloc.sum()
    if somme <= 0.0:
        return None
    yy, xx = np.mgrid[y1:y2, x1:x2]
    return (float((bloc * xx).sum() / somme) - pad,
            float((bloc * yy).sum() / somme) - pad,
            somme)


#: Bande verticale ou chercher le pic concurrent, en fractions du rayon, et
#: poids minimal pour qu'il compte. Le disque solaire s'APLATIT en approchant
#: de l'horizon -- refraction atmospherique, aplatissement mesure de 0 % au
#: debut de la sequence reelle a 6 % vers la frame 2400. Le vote emploie un
#: rayon FIXE, estime sur les frames les plus pleines donc les premieres : il
#: devient trop grand de dR, et l'arc SUPERIEUR vote alors dR trop bas quand
#: l'arc INFERIEUR vote dR trop haut. L'accumulateur porte deux pics separes
#: de 2 dR, soit au plus 0,12 r pour 6 % d'aplatissement.
#:
#: La bande va donc de 0,06 a 0,25 r, et la tolerance horizontale vaut 0,06 r :
#: les deux pics ne different qu'en vertical.
BANDE_CONCURRENT = (0.06, 0.25)
TOLERANCE_X_CONCURRENT = 0.06
POIDS_CONCURRENT = 0.55


def _concurrent_vertical(acc, py, px, r):
    """(y, x) du pic concurrent vertical du pic principal, ou None.

    L'argmax bascule d'un pic a l'autre selon lequel des deux arcs est le
    mieux eclaire — un nuage suffit a inverser l'equilibre. Le centre mesure
    saute alors de 2 dR d'une frame a l'autre, et le disque oscille dans le
    cadre : mesure sur la sequence reelle, erreur de mesure verticale de p90
    42,9 px et jusqu'a 55,8 px, contre 2,3 px en horizontal.

    On retient TOUJOURS le pic de l'arc superieur. Le choix n'est pas plus
    juste — les deux pics sont faux de dR — mais il est CONSTANT : un biais
    fixe ne fait que decaler le cadrage, la ou une alternance se voit. Mesure
    contre une verite de terrain independante (correlation de phase entre
    frames source), sur 114 frames de la zone : erreur p90 de 42,9 a 14,0 px,
    et 30 pas de plus de 20 px ramenes a 10. Le milieu des deux pics fait
    mieux sur le maximum (29,1 contre 35,0) mais nettement moins bien sur le
    p90, qui est ce qui se voit sur une sequence.
    """
    # La demi-largeur de comparaison, et le PLANCHER de la bande. Un pic de
    # l'accumulateur fait quelques pixels de large apres les deux lissages 3x3 :
    # si la bande commence plus pres que cela, elle tombe DANS le pic principal
    # et son propre epaulement passe pour un maximum local. C'est arrive sur un
    # disque de 20 px de rayon, ou 0,06 r ne fait que 1 px : la mesure se
    # deplacait de quelques pixels et le cadrage revelait des bords noirs.
    demi = max(3, int(0.02 * r))
    ymin = max(int(BANDE_CONCURRENT[0] * r), 2 * demi + 1)
    ymax = int(BANDE_CONCURRENT[1] * r)
    tx = int(TOLERANCE_X_CONCURRENT * r)
    if ymax <= ymin:
        return None                      # rayon trop petit : pas de double pic
    x1, x2 = max(0, px - tx), min(acc.shape[1], px + tx + 1)
    if x2 <= x1:
        return None

    # Profil vertical : le meilleur de chaque ligne, dans la tolerance en x.
    profil = acc[:, x1:x2].max(axis=1)

    # ON CHERCHE UN MAXIMUM LOCAL, ET NON LE PLUS FORT DE LA BANDE. Entre les
    # deux pics il y a un COL, plus haut que le fond mais plus bas qu'eux ;
    # comme il est plus PROCHE que le pic oppose, un simple argmax sur la
    # bande le retenait lui, et l'alignement ne se faisait plus. Mesure sur
    # une ellipse de synthese aplatie de 7,5 % : pics a 191 et 209, col a
    # 200 de poids 4946 contre 7595 -- l'argmax de bande rendait 200.
    meilleur, mieux = None, POIDS_CONCURRENT * float(acc[py, px])
    for signe in (-1, 1):
        for dy in range(ymin, ymax + 1):
            qy = py + signe * dy
            if qy - demi < 0 or qy + demi >= acc.shape[0]:
                continue
            v = float(profil[qy])
            if v <= mieux:
                continue
            if v < profil[qy - demi] or v < profil[qy + demi]:
                continue                 # sur une pente : c'est un col
            mieux = v
            qx = x1 + int(np.argmax(acc[qy, x1:x2]))
            meilleur = (qy, qx)
    return meilleur


def _lisse3(a):
    """Moyenne 3x3 separable, en numpy pur."""
    p = np.pad(a, 1, mode="constant")
    h = (p[:, :-2] + p[:, 1:-1] + p[:, 2:]) / 3.0
    return ((h[:-2] + h[1:-1] + h[2:]) / 3.0).astype(np.float32)
