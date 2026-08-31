"""Distribution du calcul par frame sur plusieurs processus.

Les deux passes du pipeline font, par frame, un calcul independant d'une frame
a l'autre : localisation, qualite et photometrie en passe 1, recadrage et
normalisation en passe 2. C'est ce calcul, et lui seul, que ce module repartit.
Le decodage n'est pas fait dans le parent : un unique processus ffmpeg enfant
s'en charge (voir eclipse.io), le parent ne faisant que lire son tube.

Mesure sur la sequence reelle a 3 processus, 2 coeurs physiques : 1,31x en
passe 1, 0,97x en passe 2 — soit aucun gain en passe 2, cause non etablie. Le
plafond de l'architecture, cite a environ 7x dans la conception, n'est pas
mesure : il decoule du debit d'un unique decodeur ffmpeg, propriete de ce
processus enfant et non du CPU du parent, et reste a verifier sur une machine a
six ou huit coeurs.

Les deux fonctions de travail de ce module sont la frontiere qu'un futur
backend GPU remplacera : elles ne font que du calcul, sur des donnees deja
extraites.
"""
import os
from itertools import islice
from multiprocessing import Pool
from operator import index

import numpy as np

from .locate import locate_center_regime
from .photometry import measure_photometry
from .quality import CORONA_FACTOR, masse_captee, measure_quality
from .render import apply_frame

#: Sentinelle : deduire le nombre de processus du materiel.
PROCESSUS_DEFAUT = None

#: Plafond du nombre de travaux en vol dans un bloc, quel que soit le nombre de
#: processus. Un travail de passe 2 porte une frame source pleine resolution
#: (1080x1920x3 octets = 6,2 Mo) plus son resultat (3,8 Mo), soit ~10 Mo :
#: 24 travaux bornent la memoire en vol a ~240 Mo. Sans plafond, 4x le nombre
#: de processus la fait croitre avec le materiel — ~280 Mo a 8 processus,
#: ~600 Mo a 16 — soit l'inverse de ce qu'on veut quand la cible grandit.
#: 24 laisse le defaut mesure (3 processus, bloc 12) inchange et garde au moins
#: un travail et demi par travailleur jusqu'a 16 processus.
BLOC_MAX = 24


def nombre_processus(demande=None):
    """Nombre de travailleurs a lancer.

    Par defaut, un de moins que les coeurs logiques : le parent decode et
    ecrit pendant que les travailleurs calculent, et le mettre en concurrence
    avec eux lui ferait perdre ce qu'ils gagnent.
    """
    if demande is None:
        return max(1, (os.cpu_count() or 1) - 1)
    # index() accepte les entiers (y compris ceux de numpy) et refuse tout le
    # reste : int(2.9) rendrait 2 en silence, piege pour un appelant
    # programmatique meme si la ligne de commande, en type=int, ne peut pas y
    # tomber.
    try:
        demande = index(demande)
    except TypeError:
        raise ValueError(
            f"Nombre de processus invalide : {demande!r}. Il faut un entier "
            "(2,9 ne sera pas tronque en 2 en silence).") from None
    if demande < 1:
        raise ValueError(
            f"Nombre de processus invalide : {demande}. Il en faut au moins 1 "
            "(1 = chemin sequentiel).")
    return demande


def applique(fonction, travaux, processus, bloc=None):
    """Resultats de fonction sur chaque travail, DANS L'ORDRE des travaux.

    processus <= 1 emprunte la boucle sequentielle, sans pool ni
    serialisation : c'est le chemin de debogage, et l'oracle des tests
    d'identite.

    Le travail est distribue par BLOCS, et non par un unique Pool.imap. Le fil
    d'alimentation d'imap consomme son iterable aussi vite qu'il peut, sans
    attendre que les resultats soient consommes : alimente par le decodeur, il
    decoderait la sequence entiere en memoire — 4 Go en passe 1, 16 Go en
    passe 2. Un bloc borne ce qui est en vol.

    Le prix est une barriere en fin de bloc, les derniers travailleurs finissant
    pendant que les autres attendent. Un bloc de 4x le nombre de processus rend
    cette perte petite devant le gain, dans la limite de BLOC_MAX travaux en
    vol.
    """
    if processus <= 1:
        for travail in travaux:
            yield fonction(travail)
        return

    bloc = min(processus * 4, BLOC_MAX) if bloc is None else int(bloc)
    iterateur = iter(travaux)
    # Le premier paquet est tire AVANT le pool : sans travail, aucun
    # interpreteur n'est lance pour produire une liste vide.
    paquet = list(islice(iterateur, bloc))
    if not paquet:
        return
    # Pool et non concurrent.futures.ProcessPoolExecutor, arbitrage assume :
    # un travailleur qui meurt brutalement — tue par l'OOM, faute de
    # segmentation — n'a jamais sa tache reattribuee, et ce Pool.map attend
    # alors indefiniment, la ou ProcessPoolExecutor leverait
    # BrokenProcessPool. Une exception Python normale, elle, est serialisee et
    # relevee correctement ici, et le with termine le pool. La mort brutale
    # devient d'autant moins probable que BLOC_MAX borne la memoire en vol
    # (voir plus haut), sans etre exclue ; si elle se produit un jour,
    # l'executeur est le remplacement, lui aussi dans la bibliotheque standard.
    with Pool(processus) as pool:
        while paquet:
            # Pool.map preserve l'ordre du paquet.
            yield from pool.map(fonction, paquet)
            paquet = list(islice(iterateur, bloc))


def mesure_frame(travail):
    """Mesures d'une frame, pour la passe 1. travail = (rgb, rayon, params).

    params: resolved presets.analysis_params dict (vote regime and light
    threshold). The winning regime is returned so the cache can carry it:
    quality reads a bright disc inside r but a dark disc's light lives in
    the corona ring (see quality.measure_quality / CORONA_FACTOR).

    Rend les valeurs BRUTES : c'est l'appelant qui les met en forme pour le
    cache (voir pipeline._ou_none). La fonction reste ainsi purement
    calculatoire, ce qui la rend testable seule et remplacable par un backend
    accelere.
    """
    rgb, rayon, params = travail
    gray = rgb.astype(np.float32).mean(axis=2)
    (cx, cy, conf), regime = locate_center_regime(gray, rayon,
                                                  params["vote"])
    capture_radius = rayon * (CORONA_FACTOR if regime == "dark" else 1.0)
    return {
        "cx": cx, "cy": cy, "conf": conf, "regime": regime,
        "q": measure_quality(gray, cx, cy, rayon, regime=regime),
        "m": masse_captee(gray, cx, cy, capture_radius,
                          seuil_lumiere=params["light_threshold"]),
        "p": measure_photometry(rgb, cx, cy, rayon),
    }


def rend_frame(travail):
    """Frame transformee, pour la passe 2.

    travail = (rgb, cx, cy, gain, taille, remplissage). L'interpolation des
    coupes courtes n'est PAS ici : elle a besoin de la frame precedemment
    ecrite et reste donc dans le parent, seul a connaitre l'ordre de sortie.
    """
    rgb, cx, cy, gain, taille, remplissage = travail
    return apply_frame(rgb, cx, cy, gain, taille=taille,
                       remplissage=remplissage)
