"""Filtres temporels 1D, en numpy pur.

Aucune dependance scipy : l'environnement est en Python 3.14 et le projet ne
doit pas dependre de la disponibilite d'une roue.
"""
import numpy as np


def _fenetres(x, k):
    """Vue glissante de largeur k, bords repliques.

    La replication maintient le point extreme la ou il est. Une reflexion
    le decalerait vers l'interieur, ce qui reviendrait a inventer du
    mouvement au bord de la sequence.
    """
    demi = k // 2
    p = np.pad(np.asarray(x, dtype=np.float64), demi, mode="edge")
    return np.lib.stride_tricks.sliding_window_view(p, k)


def median_filter_1d(x, k):
    """Mediane glissante. k doit etre impair."""
    if k % 2 == 0:
        raise ValueError("La fenetre du filtre median doit etre impaire")
    if k <= 1:
        return np.asarray(x, dtype=np.float64).copy()
    return np.median(_fenetres(x, k), axis=1)


#: Meme operation que median_filter_1d, nomme pour son autre usage :
#: fournir une reference locale a laquelle comparer une mesure.
rolling_median = median_filter_1d


def reference_deflicker(x, k):
    """Binomial 3 points puis mediane glissante. k doit etre impair.

    La reference de deflicker de photometry.solve_couleur. La mediane seule
    est transparente a une oscillation de periode 2 : la parite du centre y
    est toujours majoritaire (16 contre 15 dans une fenetre de 31), la
    « reference » suit alors le flicker qu'elle devait gommer — et exclure
    le centre INVERSE le defaut (16 voisins de parite opposee contre 14)
    au lieu de le corriger. Le binomial (x[i-1] + 2 x[i] + x[i+1]) / 4, lui,
    annule une periode 2 exactement, quelle que soit la parite.

    La mediane qui suit garde ce que le binomial ne sait pas faire : suivre
    une marche franche (retrait du filtre solaire) sans l'etaler — le
    binomial ne la lisse que sur les deux frames qui l'encadrent, deux
    valeurs transitionnelles que la mediane ignore partout ailleurs — et
    ignorer une aberration isolee, que le binomial etale sur 3 frames :
    d'ou une fenetre utile d'au moins 7.

    Bords repliques, comme median_filter_1d et pour la meme raison ; le
    voisinage y est domine par les replicats, une oscillation n'y est donc
    absorbee qu'a partir de k//2 frames du bord.
    """
    serie = np.asarray(x, dtype=np.float64)
    p = np.pad(serie, 1, mode="edge")
    binomial = (p[:-2] + 2.0 * p[1:-1] + p[2:]) / 4.0
    return median_filter_1d(binomial, k)


def savgol_1d(x, window, order):
    """Lissage de Savitzky-Golay : ajustement polynomial glissant.

    Au centre, les coefficients sont ceux de la ligne du terme constant de
    la pseudo-inverse de la matrice de Vandermonde : appliques a la fenetre,
    ils donnent la valeur ajustee au centre.

    Aux bords, on ajuste le polynome sur la premiere (resp. derniere)
    fenetre complete et on l'evalue hors centre, au lieu de rembourrer.
    Aucun mode de rembourrage ne preserve un polynome : la replication se
    trompe de 0,27 sur une droite, la reflexion de 0,55. Un bord biaise
    decalerait le recentrage des premieres et dernieres frames.
    """
    if window % 2 == 0:
        raise ValueError("La fenetre de Savitzky-Golay doit etre impaire")
    serie = np.asarray(x, dtype=np.float64)
    n = len(serie)
    if window <= order + 1 or n < window:
        return serie.copy()

    demi = window // 2
    idx = np.arange(-demi, demi + 1, dtype=np.float64)
    vandermonde = np.vander(idx, order + 1, increasing=True)

    out = np.empty(n, dtype=np.float64)
    fenetres = np.lib.stride_tricks.sliding_window_view(serie, window)
    out[demi:n - demi] = fenetres @ np.linalg.pinv(vandermonde)[0]

    coefs_gauche = np.linalg.lstsq(vandermonde, serie[:window], rcond=None)[0]
    coefs_droite = np.linalg.lstsq(vandermonde, serie[n - window:], rcond=None)[0]
    for i in range(demi):
        out[i] = np.polyval(coefs_gauche[::-1], idx[i])
        out[n - demi + i] = np.polyval(coefs_droite[::-1], idx[demi + 1 + i])
    return out


def interpolate_invalid(x, valid):
    """Remplace les entrees invalides par interpolation lineaire des valides.

    Les bords sont extrapoles en plateau (premiere/derniere valeur valide).
    """
    x = np.asarray(x, dtype=np.float64)
    valid = np.asarray(valid, dtype=bool)
    if not valid.any():
        raise ValueError("Aucune mesure valide : rien a interpoler")
    idx = np.arange(len(x), dtype=np.float64)
    return np.interp(idx, idx[valid], x[valid])


def smooth_track(cx, cy, valid):
    """Trajectoire du centre : la mesure brute, interpolee la ou elle manque.

    La tache 10 avait etabli qu'un filtre temporel degrade le recentrage : le
    mouvement est en marches d'escalier, pas en derive lente. Une exception
    avait ete conservee — une mediane courte sur les frames de faible
    confiance, pour mater un tremblement de fin de sequence.

    Cette exception est supprimee. Elle corrigeait de plus de 60 px sur 39
    frames dont le masque juge pourtant la mesure bonne ; douze ont ete
    decodees, et douze fois sur douze la mesure brute etait plus proche du
    disque reel que la valeur lissee. La sequence est un timelapse : ses
    frames consecutives sont eloignees dans le temps reel et le Soleil s'y
    deplace vraiment de l'ordre de 110 px. Le lissage aplatissait ce mouvement
    et faisait paraitre la trajectoire lisse tout en placant le Soleil au
    mauvais endroit.

    Rien ne le remplace : planifie_trajectoire transforme deja une marche
    reelle en panoramique borne. Mesure decisive — les sauts du disque entre
    frames RENDUES consecutives sont identiques avec et sans lissage (p50 0,00,
    p90 0,00, p99 2,00, maximum 34,0 px). Le lissage ne protegeait donc plus
    rien.
    """
    return interpolate_invalid(cx, valid), interpolate_invalid(cy, valid)


def planifie_trajectoire(s, bornes, depassement):
    """Position de fenetre : le centre mesure, borne au corridor atteignable.

    LA FENETRE SE POSE, ELLE NE POURSUIT PLUS. La version precedente faisait
    poursuivre le centre par l'offset du disque a vitesse bornee (2 px/frame),
    ce qui modelisait une progression stellaire lente. Le smart-telescope de
    la sequence de reference est une monture altazimutale sans suivi
    equatorial : ce qu'on observe n'est pas une derive, ce sont ses erreurs
    de cadrage.

    Mesure decisive sur la sequence reelle : la correlation d'un pas de
    trajectoire au suivant vaut -0,43 en vertical et -0,50 en horizontal --
    une anticorrelation, signature d'une monture qui chasse sa cible. Une
    progression reguliere donnerait une correlation franchement positive. Et
    une mediane glissante de 51 frames n'explique que 0,9 % de la variance
    des pas verticaux : il n'y a quasiment pas de derive a preserver.

    Consequence de l'ancien modele, mesuree sur le rendu livre : le disque
    etait exactement centre sur 89,2 % des frames rendues, et sur les 300
    dernieres la fenetre panotait a sa vitesse maximale 68,3 % du temps --
    un rampement vertical uniforme de 2,57 px par frame, parfaitement
    lisible a l'oeil. Avec le placement geometrique : 99,5 % des frames
    exactement centrees, saut p90 nul.

    Quand le disque sort du corridor, la fenetre y saute au lieu d'y ramper.
    C'est voulu : sur un timelapse la coupe se lit comme la suite logique de
    l'image si la monture avait parfaitement suivi, la ou une derive se lit
    comme un defaut.

    s : trajectoire du disque, coordonnees source, un axe. bornes :
    (bmin, bmax) du centre de fenetre. depassement : debordement tolere
    au-dela des bords de la source, en px ; la bande sans pixels source est
    comblee par replication (render.apply_frame, remplissage 'bord').
    """
    s = np.asarray(s, dtype=np.float64)
    bmin, bmax = float(bornes[0]), float(bornes[1])
    d = float(depassement)
    return np.clip(s, bmin - d, bmax + d)
