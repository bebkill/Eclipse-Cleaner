"""Transformation geometrique et colorimetrique d'une frame.

Le remplissage noir est acceptable par defaut parce que le fond est du ciel
noir sur toute la sequence : la zone revelee par la translation est
visuellement indiscernable du fond reel.

Un second mode, 'bord' (replication de la derniere ligne/colonne couverte),
sert a la bande revelee quand la fenetre de recadrage deborde volontairement
la source en butee souple (voir track.planifie_trajectoire) : la ou un aplat
noir serait visible contre du ciel clair ou du sol, la replication du bord
reste discrete.
"""
import numpy as np


def _shift_entier(img, sx, sy, out_h, out_w):
    """Translation d'un nombre entier de pixels vers un cadre de sortie de
    taille libre, remplissage a zero.

    La destination peut etre plus grande ou plus petite que la source. Sans
    cela, recentrer le disque au milieu d'un cadre plus large serait
    impossible : la translation resterait bornee par la largeur de l'entree.
    """
    out = np.zeros((out_h, out_w) + img.shape[2:], dtype=img.dtype)
    h, w = img.shape[:2]
    x0, x1 = max(0, -sx), min(w, out_w - sx)
    y0, y1 = max(0, -sy), min(h, out_h - sy)
    if x1 <= x0 or y1 <= y0:
        return out
    out[y0 + sy:y1 + sy, x0 + sx:x1 + sx] = img[y0:y1, x0:x1]
    return out


def _bande_bornee_au_fond(ligne):
    """Ligne de bord (h, 3) ou (w, 3) bornee a son propre niveau de fond.

    Le percentile 60, par canal, estime le niveau de fond (ciel ou sol) de
    la ligne : sur une ligne homogene il est proche du contenu et le
    plafond ne change presque rien. La ou le Soleil traverse la ligne, ses
    pixels sont tres au-dessus de ce niveau ; les y ramener evite d'etaler
    le SUJET dans la bande au lieu du FOND — c'est l'appendice brillant
    mesure sur le rendu.

    Couplage silencieux avec pipeline.TOLERANCE_BORD_DEFAUT : ce plafond ne
    protege que tant que le Soleil occupe moins d'environ 40 % de la ligne de
    bord la plus courte (la rangee horizontale, largeur out_w = 840 px dans
    la fenetre de recadrage par defaut). Avec le rayon visible R = 399,5 px,
    la corde du disque sur cette ligne vaut 2*sqrt(R**2 - (R - tolerance)**2) ;
    elle franchit les 40 % (le percentile 60 tombe alors DANS le Soleil, et le
    plafond ne fait plus rien) vers 37 px de tolerance en horizontal, ~155 px
    en vertical.

    La vraie voie non bornee n'est pas --tolerance-bord (un flottant libre en
    CLI, mais verifie par verdicts.analyse_verdicts avant que la frame
    n'arrive ici) : ce sont les decisions humaines du viewer. `render`
    applique decisions.json APRES analyse_verdicts, donc une frame que
    l'utilisateur recupere a la main atteint apply_frame(...,
    remplissage="bord") avec l'amputation qu'elle a reellement, sans aucune
    verification de tolerance. Mesure sur le decisions.json courant (228
    entrees, toutes "conserver") : 2 frames franchissent le point de rupture
    horizontal (~37 px) et 3 le point de rupture vertical (~155 px) — environ
    5 frames peuvent donc encore faire croitre l'appendice brillant que ce
    plafond existe pour prevenir. En pratique le croissant reel est plus fin
    que la corde geometrique — d'ou le 255 -> 4 mesure sur la sequence reelle
    meme a 150 px de tolerance — donc ceci reste une note de robustesse, pas
    une rupture averee sur le chemin --tolerance-bord.
    """
    niveau = np.percentile(ligne, 60, axis=0)
    return np.minimum(ligne, niveau)


def _replique_bords(f, dx, dy, src_w, src_h):
    """Recopie la derniere ligne/colonne couverte sur la bande revelee.

    La geometrie vient de (dx, dy) et des dimensions source, jamais du
    contenu : detecter du noir serait faux sur une image reellement noire.
    Les pixels partiellement couverts (melange bilineaire avec le fond a
    zero) sont recouverts aussi, sinon ils laisseraient une couture sombre.

    La ligne recopiee est bornee au niveau de fond (voir
    _bande_bornee_au_fond) avant d'etre etalee sur la bande : sans cela, une
    ligne de bord qui traverse le disque solaire etale le Soleil jusqu'au
    bord du cadre au lieu du seul fond.

    Rank-agnostique comme _shift_entier (qui s'appuie sur img.shape[2:]) :
    f peut etre 2-D (niveaux de gris) ou 3-D (canaux couleur), d'ou l'usage
    de Ellipsis plutot que d'un nombre de ':' fige a 3-D.
    """
    out_h, out_w = f.shape[:2]
    gauche = min(max(int(np.ceil(dx)), 0), out_w)          # bande [0, gauche)
    droite = min(max(int(np.floor(dx + src_w)), 0), out_w)  # bande [droite, fin)
    haut = min(max(int(np.ceil(dy)), 0), out_h)
    bas = min(max(int(np.floor(dy + src_h)), 0), out_h)
    if 0 < gauche < out_w:
        f[:, :gauche] = _bande_bornee_au_fond(f[:, gauche])[:, None, ...]
    if 0 < droite < out_w:
        f[:, droite:] = _bande_bornee_au_fond(f[:, droite - 1])[:, None, ...]
    if 0 < haut < out_h:
        f[:haut, :] = _bande_bornee_au_fond(f[haut, :])[None, ...]
    if 0 < bas < out_h:
        f[bas:, :] = _bande_bornee_au_fond(f[bas - 1, :])[None, ...]
    return f


def shift_bilinear(img, dx, dy, taille=None, remplissage="noir"):
    """Translation sous-pixel par combinaison de quatre translations entieres.

    taille : (largeur, hauteur) du cadre de sortie ; par defaut celle de
    l'entree.
    remplissage : 'noir' (defaut) ou 'bord'. Le noir convient au ciel noir
    de la sequence ; 'bord' sert a la bande revelee quand la fenetre depasse
    la source en butee souple (voir track.planifie_trajectoire) — un aplat
    noir y serait visible contre du ciel clair ou du sol.
    """
    f = img.astype(np.float32)
    h, w = f.shape[:2]
    out_w, out_h = (w, h) if taille is None else (int(taille[0]), int(taille[1]))
    x0, y0 = int(np.floor(dx)), int(np.floor(dy))
    fx, fy = float(dx - x0), float(dy - y0)
    out = (_shift_entier(f, x0, y0, out_h, out_w) * ((1.0 - fx) * (1.0 - fy))
           + _shift_entier(f, x0 + 1, y0, out_h, out_w) * (fx * (1.0 - fy))
           + _shift_entier(f, x0, y0 + 1, out_h, out_w) * ((1.0 - fx) * fy)
           + _shift_entier(f, x0 + 1, y0 + 1, out_h, out_w) * (fx * fy))
    if remplissage == "bord":
        out = _replique_bords(out, dx, dy, w, h)
    return out


def soft_knee(x, knee=0.85, plafond=255.0):
    """Compression douce des hautes lumieres.

    Transparente sous le genou, asymptotique au plafond au-dessus, et de
    derivee continue au raccord. Sans elle, un gain superieur a 1 sur un
    disque deja ecrete produirait un aplat blanc.

    Les valeurs negatives, qui n'ont pas de sens physique, sont ramenees a
    zero au passage.

    Sur la sequence reelle, environ 15% des pixels seulement depassent le
    genou : calculer l'exponentielle sur ce seul sous-ensemble, plutot que
    sur toute la frame avant de jeter le resultat sous le genou via
    np.where, donne 67,9 ms -> 43,8 ms (-35%) sur la mesure initiale, 25,9 ms
    -> 11,4 ms (-56%) mesure independamment sur la machine de developpement.
    Ecart maximal mesure entre les deux implementations, sequence reelle et
    fixture synthetique : 0.0 (exactement).
    """
    f = np.asarray(x, dtype=np.float32)
    k = knee * plafond
    reste = plafond - k
    if reste <= 0.0:
        return np.clip(f, 0.0, plafond)
    # np.array(...) et non np.maximum(f, 0.0) directement : sur une entree
    # 0-D (scalaire), np.maximum renvoie un scalaire numpy et non un tableau
    # 0-D, ce qui fait lever TypeError a l'indexation booleenne juste apres.
    out = np.array(np.maximum(f, 0.0), dtype=np.float32)
    au_dessus = f > k
    if au_dessus.any():
        out[au_dessus] = plafond - reste * np.exp(-(f[au_dessus] - k) / reste)
    return out.astype(np.float32)


def melange_lineaire(a, b, t):
    """Frame intermediaire entre deux frames, par interpolation lineaire.

    Sert a combler les courtes coupes laissees par les frames rejetees.
    Apres stabilisation le disque ne bouge plus d'une frame a l'autre : ce
    qui evolue est lent (avancee de la Lune, couleur du couchant), et un
    melange lineaire suffit a masquer la saccade. Sur une coupe longue il
    donnerait un fondu visible — d'ou le plafond a quelques frames.
    """
    t = float(np.clip(t, 0.0, 1.0))
    m = a.astype(np.float32) * (1.0 - t) + b.astype(np.float32) * t
    return np.clip(m, 0.0, 255.0).astype(np.uint8)


def apply_frame(rgb, cx, cy, gain, taille=None, knee=0.85, remplissage="noir"):
    """Recentre le disque, applique le gain, sort en uint8.

    gain : un scalaire (luminance seule, la couleur reste telle qu'elle a
    ete filmee — voir photometry.solve_corrections), ou un vecteur (r, g, b)
    par canal (stabilisation de balance, voir photometry.solve_couleur).
    apply_frame ne decide rien de la couleur : elle applique ce qu'on lui
    donne.

    taille : (largeur, hauteur) de sortie ; par defaut celle de l'entree.
    remplissage : 'noir' (defaut) ou 'bord'.

    Le changement de taille est porte par la translation elle-meme, et non
    par un recadrage applique apres coup : sinon le disque ne pourrait
    jamais atteindre le centre d'un cadre plus large que l'entree.
    """
    f = rgb.astype(np.float32)
    h, w = f.shape[:2]
    out_w, out_h = (w, h) if taille is None else (int(taille[0]), int(taille[1]))

    # asarray et non float() : un vecteur (r, g, b) se diffuse sur le dernier
    # axe, un scalaire garde exactement le comportement historique.
    f = f * np.asarray(gain, dtype=np.float32)
    f = soft_knee(f, knee=knee)

    if not (np.isfinite(cx) and np.isfinite(cy)):
        raise ValueError("Centre non fini : la frame aurait du etre rejetee")
    f = shift_bilinear(f, out_w / 2.0 - float(cx), out_h / 2.0 - float(cy),
                       taille=(out_w, out_h), remplissage=remplissage)

    return np.clip(f, 0.0, 255.0).astype(np.uint8)
