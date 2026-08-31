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
#: [CALIBRER-T11]
LIT_MAX_FRACTION = 0.08


def lit_mask(gray, mode="percentile"):
    """Region eclairee : seuil a mi-chemin entre le fond et le p99.

    mode "max": threshold relative to the frame maximum instead, for
    profiles whose subject may be small or partly shadowed (see
    LIT_MAX_FRACTION). The default stays byte-identical to the historic
    behaviour.
    """
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


def locate_center(gray, r, vote="bright"):
    """Disc center by directed vote; see _vote_center for the method.

    vote "dark" flips the normals (p - r*n): on a dark disc ringed by
    light — a solar totality — the gradient at the limb points OUTWARD,
    and the bright vote scatters its votes 2r away from the center. vote
    "dual" evaluates both regimes and returns the sharper peak: the
    crescent -> totality -> crescent transition happens inside a single
    video, each frame must pick for itself.
    """
    return locate_center_regime(gray, r, vote)[0]


def locate_center_regime(gray, r, vote="dual"):
    """((cx, cy, conf), regime) — the winning regime alongside the fix.

    mesure_frame stores the regime in the cache: quality measures read a
    BRIGHT disc inside the radius but a DARK disc's light lives in the
    ring outside it (see quality.measure_quality's regime parameter)."""
    if vote == "bright":
        return _vote_center(gray, r, +1.0), "bright"
    if vote == "dark":
        return _vote_center(gray, r, -1.0), "dark"
    if vote != "dual":
        raise ValueError(f"Regime de vote inconnu : {vote!r}")
    bright = _vote_center(gray, r, +1.0)
    dark = _vote_center(gray, r, -1.0)
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
