"""Mesure de l'exposition et de la couleur, normalisation de la luminance.

La couleur est mesuree mais volontairement pas corrigee : voir
solve_corrections.

Limite assumee : a partir de la frame ~1050 de la source, les hautes lumieres
sont ecretees a 255. Aucune normalisation ne recupere une information detruite
au tournage. Le code en tient compte en mesurant sur des statistiques non
ecretees, mais la continuite retrouvee est celle du niveau, pas du detail.
"""
import numpy as np

from .track import interpolate_invalid, median_filter_1d, savgol_1d

SEUIL_ECRETAGE = 250.0
GAIN_MIN, GAIN_MAX = 0.25, 4.0
FENETRE_LISSAGE = 61
MIN_PIXELS_BALANCE = 50


def measure_photometry(rgb, cx, cy, r):
    """Niveau et balance des blancs de la partie eclairee du disque."""
    if not (np.isfinite(cx) and np.isfinite(cy)):
        return {"level": float("nan"), "wb": [float("nan")] * 3}

    f = rgb.astype(np.float32)
    h, w = f.shape[:2]
    gray = f.mean(axis=2)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)

    interieur = dist <= r
    if not interieur.any():
        return {"level": float("nan"), "wb": [float("nan")] * 3}

    # La partie lunaire, sombre, s'exclut d'elle-meme par ce seuil.
    p99 = float(np.percentile(gray[interieur], 99.0))
    eclaire = interieur & (gray > 0.5 * p99)
    if not eclaire.any():
        return {"level": float("nan"), "wb": [float("nan")] * 3}

    # Mediane et non p90 : le p90 saturerait a 255 des la frame 1050 et
    # ne mesurerait plus rien.
    level = float(np.median(gray[eclaire]))

    # Un canal sature fausse violemment la balance : on n'utilise que les
    # pixels dont aucun canal ne depasse le seuil d'ecretage.
    non_ecrete = eclaire & (f.max(axis=2) < SEUIL_ECRETAGE)
    if int(non_ecrete.sum()) >= MIN_PIXELS_BALANCE:
        wb = [float(f[:, :, c][non_ecrete].mean()) for c in range(3)]
    else:
        wb = [float("nan")] * 3

    return {"level": level, "wb": wb}


def solve_corrections(levels, valid):
    """Gain par frame. La couleur n'est deliberement pas corrigee.

    Le filtre solaire employe sur la premiere moitie de la sequence est un
    filtre rouge : le canal bleu y vaut 0,07 sur 255. Il n'y a aucune
    information bleue a equilibrer, et forcer une balance neutre attenue le
    rouge d'un facteur 370 — jusqu'a 267000 sur certaines frames — ce qui
    noircit 41% de la video. La cible medianne n'est d'ailleurs une couleur
    filmee nulle part : la distribution est bimodale et sa mediane tombe
    entre les deux modes.

    La balance reste MESUREE par measure_photometry et consignee dans le
    cache : c'est ce qui a permis de diagnostiquer le probleme.

    La courbe de niveau est lissee avant inversion : c'est le bruit de
    mesure qui doit disparaitre, pas l'evolution reelle. La cible est la
    mediane de la sequence.
    """
    levels = np.asarray(levels, dtype=np.float64)
    valid = np.asarray(valid, dtype=bool)
    n = len(levels)

    sain = valid & np.isfinite(levels) & (levels > 1e-6)
    if not sain.any():
        raise ValueError("Aucune mesure photometrique exploitable")

    niveau = _lisse(interpolate_invalid(levels, sain), n)
    cible = float(np.median(niveau[sain]))
    return np.clip(cible / np.maximum(niveau, 1e-6), GAIN_MIN, GAIN_MAX)


def _lisse(x, n):
    """Mediane courte contre les aberrations, puis Savitzky-Golay."""
    k = min(5, n if n % 2 == 1 else n - 1)
    k = max(k if k % 2 == 1 else k - 1, 1)
    y = median_filter_1d(x, k)
    f = min(FENETRE_LISSAGE, n if n % 2 == 1 else n - 1)
    f = max(f if f % 2 == 1 else f - 1, 1)
    return savgol_1d(y, window=f, order=2)
