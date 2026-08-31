"""Frames synthetiques d'eclipse a verite terrain connue.

La video reelle n'a pas de verite terrain : elle ne peut rien prouver.
Toutes les assertions du projet portent sur ces frames-ci.
"""
import numpy as np


def _couverture_disque(w, h, cx, cy, r):
    """Couverture par pixel d'un disque, antialiasee sur le bord.

    Un bord dur empecherait de tester la precision sous-pixel de la
    localisation : c'est la rampe d'un pixel qui porte l'information
    de position fractionnaire.
    """
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    return np.clip(r - dist + 0.5, 0.0, 1.0)


def _flou_gaussien(img, sigma):
    """Convolution gaussienne separable, en numpy pur."""
    rayon = max(1, int(3 * sigma))
    x = np.arange(-rayon, rayon + 1, dtype=np.float32)
    noyau = np.exp(-(x ** 2) / (2 * sigma ** 2))
    noyau /= noyau.sum()
    out = img.astype(np.float32)
    for axe in (0, 1):
        pad = [(0, 0)] * out.ndim
        pad[axe] = (rayon, rayon)
        p = np.pad(out, pad, mode="edge")
        empile = np.stack(
            [np.take(p, range(i, i + out.shape[axe]), axis=axe)
             for i in range(len(noyau))],
            axis=-1,
        )
        out = empile @ noyau
    return out


def make_frame(w=270, h=480, center=(135.0, 240.0), r=60.0, phase=0.0,
               angle=0.0, gain=0.8, wb=(1.0, 0.55, 0.25), blur=0.0,
               flare=None, cloud=None, horizon=None, fond=3.0, halo=0.0):
    """Construit une frame d'eclipse synthetique.

    center  : (cx, cy) du disque solaire, en pixels, sous-pixel autorise
    r       : rayon apparent du Soleil en pixels, constant par construction
    phase   : 0.0 = disque plein, 1.0 = occultation totale
    angle   : direction d'approche de la Lune, en radians
    gain    : niveau du disque, 1.0 sature a 255
    wb      : multiplicateurs (R, G, B)
    blur    : sigma du flou gaussien, 0 = net
    flare   : (fx, fy, frayon, fintensite) d'une tache parasite, ou None
    cloud   : (y0, y1, attenuation) d'une bande nuageuse horizontale, ou None
    horizon : y au-dela duquel tout est noir, ou None
    fond    : niveau du ciel
    halo    : intensite d'un halo gaussien autour du disque
    """
    cx, cy = center
    sol = _couverture_disque(w, h, cx, cy, r)

    # La Lune a le meme rayon apparent ici ; sa distance decroit avec la phase.
    d = 2.0 * r * (1.0 - phase)
    lune = _couverture_disque(w, h, cx + d * np.cos(angle), cy + d * np.sin(angle), r)

    eclaire = sol * (1.0 - lune)

    niveau = eclaire * (gain * 255.0)
    if halo > 0.0:
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
        niveau = niveau + halo * 255.0 * np.exp(-((dist / (2.5 * r)) ** 2))

    img = niveau[:, :, None] * np.array(wb, dtype=np.float32)[None, None, :]
    img = img + fond

    if cloud is not None:
        y0, y1, attenuation = cloud
        img[int(y0):int(y1)] *= attenuation

    if flare is not None:
        fx, fy, frayon, fint = flare
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        d2 = (xx - fx) ** 2 + (yy - fy) ** 2
        img = img + (fint * 255.0 * np.exp(-d2 / (2.0 * frayon ** 2)))[:, :, None]

    if horizon is not None:
        img[int(horizon):] = 0.0

    if blur > 0.0:
        img = _flou_gaussien(img, blur)

    return np.clip(img, 0, 255).astype(np.uint8)


def make_moon_frame(w=270, h=480, center=(135.0, 240.0), r=60.0,
                    umbra=0.5, umbra_level=0.15, angle=0.0, gain=0.8,
                    wb=(0.92, 0.95, 1.0), umbra_wb=(1.0, 0.40, 0.25),
                    fond=0.0, blur=0.8):
    """Lunar-eclipse frame: one disc, partly covered by the Earth's shadow.

    umbra       : fraction of the diameter covered by the shadow (0..1)
    umbra_level : luminance of the shadowed part, fraction of the lit part
    angle       : direction the shadow comes from, radians
    The shadow is a large circle (2.5 r), like the real umbra: its edge
    through the disc is an arc, not a straight line.
    """
    cx, cy = center
    disc = _couverture_disque(w, h, cx, cy, r)
    rs = 2.5 * r
    d = rs + r - 2.0 * r * float(umbra)
    shadow = _couverture_disque(w, h, cx - d * np.cos(angle),
                                cy - d * np.sin(angle), rs)
    lit = disc * (1.0 - shadow)
    shadowed = disc * shadow
    level = gain * 255.0
    img = (lit * level)[:, :, None] * np.array(wb, np.float32)
    img = img + (shadowed * level * umbra_level)[:, :, None] \
        * np.array(umbra_wb, np.float32)
    img = img + fond
    if blur > 0.0:
        img = _flou_gaussien(img, blur)
    return np.clip(img, 0, 255).astype(np.uint8)


def make_totality_frame(w=270, h=480, center=(135.0, 240.0), r=60.0,
                        corona=0.5, fond=0.0):
    """Solar totality: a black disc ringed by a corona glow.

    The gradient at the limb points AWAY from the center (dark inside,
    bright outside): the ground truth for the dark-disc vote regime.
    """
    cx, cy = center
    disc = _couverture_disque(w, h, cx, cy, r)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    outside = np.clip(dist - r, 0.0, None)
    glow = corona * 255.0 * np.exp(-((outside / (0.45 * r)) ** 2))
    img = (glow * (1.0 - disc) + 2.0 * disc + fond)[:, :, None] \
        * np.ones(3, np.float32)
    return np.clip(img, 0, 255).astype(np.uint8)
