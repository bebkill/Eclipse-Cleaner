"""Mesure de l'exposition et de la couleur, normalisation de la luminance.

La couleur est mesuree mais volontairement pas corrigee : voir
solve_corrections.

Limite assumee : a partir de la frame ~1050 de la source, les hautes lumieres
sont ecretees a 255. Aucune normalisation ne recupere une information detruite
au tournage. Le code en tient compte en mesurant sur des statistiques non
ecretees, mais la continuite retrouvee est celle du niveau, pas du detail.
"""
import numpy as np

from .track import interpolate_invalid, reference_deflicker

SEUIL_ECRETAGE = 250.0
GAIN_MIN, GAIN_MAX = 0.25, 4.0
MIN_PIXELS_BALANCE = 50

#: Fenetre de la reference de teinte de solve_couleur (voir
#: track.reference_deflicker : binomial puis mediane, et PAS une moyenne ni
#: un Savitzky-Golay — la mediane suit une marche franche, retrait du filtre
#: solaire compris, au lieu de l'etaler en rampe de fausse couleur).
#: 31 frames a 30 fps : une oscillation de balance automatique dure quelques
#: frames, un vrai changement de teinte (filtre, coucher de soleil) tient
#: sur des centaines.
FENETRE_COULEUR_DEFAUT = 31

#: Correction de chroma maximale, en fraction (0,25 = +/- 25 %). Ce plafond
#: est ce qui rend la stabilisation sure la ou la neutralisation etait
#: catastrophique : une oscillation de balance auto tient largement dedans,
#: une teinte reellement filmee (filtre rouge, canal bleu a 0,07/255) ne peut
#: pas etre alteree au-dela d'un quart.
AMPLITUDE_COULEUR_DEFAUT = 0.25


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

    Le gain s'inverse sur le niveau BRUT, sans lissage du denominateur. Le
    niveau est une mediane sur des milliers de pixels eclaires : son bruit
    de mesure est negligeable, et tout ecart frame a frame qui reste est un
    saut REEL d'auto-exposition — precisement le flicker que la correction
    existe pour supprimer. L'ancien lissage (mediane courte puis
    Savitzky-Golay sur 61 frames) rangeait ces sauts dans le bruit et les
    laissait donc passer tels quels dans la sortie. La cible est la mediane
    de la sequence.
    """
    levels = np.asarray(levels, dtype=np.float64)
    valid = np.asarray(valid, dtype=bool)

    sain = valid & np.isfinite(levels) & (levels > 1e-6)
    if not sain.any():
        raise ValueError("Aucune mesure photometrique exploitable")

    niveau = interpolate_invalid(levels, sain)
    cible = float(np.median(niveau[sain]))
    return np.clip(cible / np.maximum(niveau, 1e-6), GAIN_MIN, GAIN_MAX)


def solve_couleur(wb, valid, fenetre=FENETRE_COULEUR_DEFAUT,
                  amplitude=AMPLITUDE_COULEUR_DEFAUT):
    """Gains (n, 3) qui stabilisent la balance vers sa propre trajectoire.

    La cible n'est PAS le neutre — le filtre solaire rouge de la sequence de
    reference l'interdit (voir solve_corrections) — mais la trajectoire de
    teinte de la sequence elle-meme : l'oscillation de la balance
    automatique disparait, la teinte reellement filmee reste, marches
    franches comprises.

    La chroma de chaque frame (wb rapportee a sa moyenne) est comparee a sa
    reference locale (track.reference_deflicker sur `fenetre` frames) ;
    l'ecart est corrige, borne a +/- `amplitude`, puis chaque frame est
    renormalisee pour que la correction de teinte ne change pas sa
    luminance — le niveau appartient a solve_corrections, pas a cette
    fonction.

    Sans mesure exploitable (frame ecartee, balance NaN), le gain est 1 : la
    couleur est un raffinement, elle ne doit jamais empecher un rendu.
    """
    wb = np.asarray(wb, dtype=np.float64)
    valid = np.asarray(valid, dtype=bool)
    n = len(wb)
    gains = np.ones((n, 3), dtype=np.float64)

    moyenne = wb.mean(axis=1)
    sain = (valid & np.isfinite(wb).all(axis=1) & (moyenne > 1e-6))
    if not sain.any():
        return gains

    # Fenetre impaire (exigence du filtre median), bornee a la sequence.
    k = max(int(fenetre), 1)
    if k % 2 == 0:
        k += 1

    chroma = np.ones((n, 3), dtype=np.float64)
    chroma[sain] = wb[sain] / moyenne[sain, None]
    for canal in range(3):
        serie = interpolate_invalid(chroma[:, canal], sain)
        reference = reference_deflicker(serie, k)
        gains[:, canal] = np.clip(reference / np.maximum(serie, 1e-6),
                                  1.0 / (1.0 + amplitude), 1.0 + amplitude)
    gains[~sain] = 1.0

    # Renormalisation par frame : la teinte bouge, la luminance non. Sans
    # elle, une correction asymetrique des canaux reintroduirait par la
    # couleur le flicker de niveau que solve_corrections vient d'enlever.
    facteur = moyenne[sain] / np.maximum(
        (gains[sain] * wb[sain]).mean(axis=1), 1e-6)
    gains[sain] *= facteur[:, None]
    return gains
