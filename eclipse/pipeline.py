"""Pipeline en deux passes.

Les deux passes sont une necessite, pas un choix : la courbe photometrique est
lissee et sa cible est la mediane de la sequence, ce qui exige de connaitre
toutes les mesures avant de rendre la premiere frame. La trajectoire, elle,
n'est plus lissee (voir track.smooth_track), mais elle reste calculee en passe 1
avec le reste des mesures.

La passe 1 decode en demi-resolution : 4x plus rapide, au prix d'une erreur de
localisation de +/- 1 px qui en vaut +/- 2 en pleine resolution — sans commune
mesure avec les centaines de pixels de mouvement reel de la sequence.
"""
import argparse
import json
import os
import re
import sys
from contextlib import closing
from itertools import islice

import numpy as np

from . import langues
from .decisions import DECISIONS_DEFAUT_NOM, applique, charger, diagnostique
from .io import FrameReader, FrameWriter, PngSequenceWriter, probe
from .locate import estimate_radius
from .parallele import applique as applique_travaux
from .parallele import (PROCESSUS_DEFAUT, mesure_frame, nombre_processus,
                        rend_frame)
from .photometry import solve_corrections
from .quality import SEUILS_DEFAUT
from .render import melange_lineaire
from .track import planifie_trajectoire
from .verdicts import analyse_verdicts
from .viewer import sert

# Version 2 : chaque frame porte "masse_captee", la fraction de lumiere que
# capture le masque solaire place au centre mesure (voir quality.masse_captee).
# C'est elle qui decide desormais si une position entre dans la trajectoire ;
# un cache de version 1 ne peut pas repondre a cette question, il est refuse.
#
# Version 3 : "limb_sharpness" est mesure au 98e percentile de l'anneau et
# non au 90e (voir quality.measure_quality). La VALEUR du champ change sans
# que son nom ni sa forme bougent, et c'est precisement pourquoi la version
# devait etre incrementee : charger_cache ne valide que le schema et la
# signature de la SOURCE, laquelle n'a pas change. Sans cette incrementation
# un cache d'avant le correctif aurait ete relu en silence, le viewer aurait
# annonce « analyse deja faite », et le correctif serait reste inerte —
# exactement le genre de peremption invisible que ce projet a deja paye.
#
# Version 4 : "cx"/"cy" changent de VALEUR — locate_center retient desormais
# le maximum local de plus grand cy quand l'accumulateur en porte plusieurs
# (voir locate._concurrent_vertical). Meme raison qu'a la version 3 : le
# champ ne change ni de nom ni de forme, et charger_cache ne valide que le
# schema et la signature de la SOURCE. Sans incrementation, un cache
# d'avant serait relu en silence et le correctif resterait inerte.
#
# Version 5 : l'alignement du pic ne reprend plus que le y du concurrent, et
# non son x (voir locate.locate_center). cx CHANGE de valeur sans que le
# format bouge -- meme piege qu'a la version 3 : charger_cache ne valide que
# le schema et la signature de la SOURCE, laquelle n'a pas bouge. Sans cette
# incrementation, le viewer aurait annonce « analyse deja faite » et le
# correctif serait reste inerte, sans qu'aucun signal ne le dise.
SCHEMA_VERSION = 5
FRAMES_ECHANTILLON_RAYON = 300

# Fraction de la source couverte par la fenetre de recadrage par defaut.
# Calibree sur la sequence de reference (1080x1920) : la tache 11 avait choisi
# une fenetre dimensionnee sur le rayon AJUSTE au limbe (793 px), mais
# l'etendue lumineuse visible du disque fait 798 px, ce qui ne laissait que
# 1 px de marge contre une erreur de verrouillage allant jusqu'a 5 px : 14,8 %
# des frames tranchaient le disque. 7/9 de la source (840x1494 sur la sequence
# de reference, meme rapport qu'elle) laisse 21 px de marge de chaque cote,
# soit 4x l'erreur maximale mesuree. La fraction, et non des pixels figes :
# une valeur absolue calee sur une source portrait 1080x1920 deborderait
# toute source paysage et inverserait le corridor de planifie_trajectoire.
FRACTION_FENETRE_DEFAUT = 7.0 / 9.0


def tailles_defaut(src_w, src_h):
    """((fenetre_w, fenetre_h), (sortie_w, sortie_h)) pour une source donnee.

    La sortie encodee reprend les dimensions de la source ; la fenetre de
    recadrage en couvre FRACTION_FENETRE_DEFAUT, au pixel pair (yuv420p exige
    des dimensions paires), la hauteur derivee de la largeur pour rester au
    rapport de la source — sans quoi l'agrandissement final deformerait le
    disque en ellipse (voir le controle de rapport dans render()).
    """
    w = max(2, int(round(src_w * FRACTION_FENETRE_DEFAUT / 2)) * 2)
    h = max(2, int(round(w * src_h / src_w / 2)) * 2)
    return (w, h), (int(src_w), int(src_h))

# Tolerance, en pixels pleine resolution, sur l'amputation du disque par le
# bord de la source (verdicts_hors_source).
#
# Passee de 25 a 5 px. Sur un disque de 799 px de diametre, 25 px faisaient
# 3 % du diametre — visibles. A 5 px (0,6 %) la coupe ne se voit pas.
#
# Et le resserrement ne se paye PAS en coupures, contre l'intuition : la plus
# longue coupure de la sequence reelle tombe de 110 a 51 frames. Le grand trou
# de la sequence est fait de frames rognees de 23,6 px en mediane, que 25 px
# laissait passer et que 5 px ecarte — mais une exigence stricte a 0 px les
# ecarterait TOUTES et ramenerait la coupure a 110. Comptes mesures : 1785
# frames a 0 px, 1805 a 5, 1846 a 10, 1896 a 25.
#
# Couplage silencieux, sans garde-fou, avec render._bande_bornee_au_fond
# (plafond percentile 60 de la bande de bord repliquee, voir render.py) : ce
# plafond ne protege que tant que la corde du disque sur la ligne de bord la
# plus courte (la rangee horizontale, largeur out_w = 840 px de la fenetre de
# recadrage par defaut) reste sous ~40 % de cette largeur. Avec le rayon
# visible R = 399,5 px, la corde vaut 2*sqrt(R**2 - (R - tolerance)**2) :
# 278 px (33,1 %) a 25 px de tolerance, encore protege ; 336 px (40,0 %) a
# 37 px, le plafond cesse de mordre en horizontal (~155 px en vertical) ;
# 421 px (50,1 %) a 60 px, le percentile tombe DANS le Soleil et le plafond
# ne fait plus rien.
#
# La vraie voie non bornee n'est pas --tolerance-bord (un flottant libre en
# CLI, mais verifie par verdicts.analyse_verdicts) : ce sont les decisions
# humaines du viewer. `render` applique decisions.json APRES
# analyse_verdicts, donc une frame que l'utilisateur recupere a la main
# atteint apply_frame(..., remplissage="bord") avec l'amputation qu'elle a
# reellement, sans aucune verification de tolerance. Mesure faite sur un
# decisions.json de 228 entrees, toutes "conserver", depuis reinitialise a la
# demande de l'utilisateur — le chiffre date de cet etat-la : 2 frames
# franchissent le point de rupture horizontal (~37 px) et 3 le point de
# rupture vertical (~155 px) — environ 5 frames peuvent donc encore faire
# croitre l'appendice brillant que ce plafond existe pour prevenir. En
# pratique le croissant reel est plus fin que la corde geometrique — d'ou le
# 255 -> 4 mesure sur la sequence reelle meme a 150 px de tolerance — donc
# ceci reste une note de robustesse, pas une rupture averee sur le chemin
# --tolerance-bord.
TOLERANCE_BORD_DEFAUT = 5.0

# Marge entre le rayon ajuste au limbe (donnees["radius"], issu de locate_center)
# et l'etendue lumineuse reellement visible du disque. Ecart mesure : 798 px de
# large contre 793 px de limbe, soit 5 px de diametre, 2,5 px de rayon ; arrondi
# a 3.0 px pour rester du bon cote.
MARGE_HALO = 3.0

# Plafond des coupes comblees par interpolation lineaire (render.melange_lineaire).
# Mesure sur la sequence reelle : 113 trous de 8 frames ou moins (208 frames a
# synthetiser), contre 2 trous de 150 et 214 frames ou le disque est reellement
# absent de la source (seules 28 et 23 frames de ces trous ont le disque
# entierement a l'interieur du cadre source). Un troisieme trou, de 311 frames,
# n'etait pas de cette nature : c'etait le rejet a tort, par l'ancien critere
# de fenetre (verdicts_cadrage, supprime), des dernieres frames du coucher de
# Soleil ; il a disparu avec la fenetre butee (voir verdicts_hors_source). Les
# deux trous restants doivent rester des coupes franches : les interpoler
# donnerait un fondu visible sur plusieurs secondes.
# Ramene de 8 a 3 : un trou de 8 frames produisait 7 images de fondu
# consecutives entre deux sources distantes de 9 frames. Le plafond de
# deplacement (ci-dessous) ne s'en apercevait pas — la fenetre bougeait de
# 14,6 px seulement — mais le CONTENU, lui, evolue sur 9 frames : phase du
# croissant, nuages, horizon. Le resultat se lit comme un flou, pas comme
# un fondu. A 3, au plus 3 images synthetisees d'affilee ; cout mesure :
# 1903 -> 1844 frames de sortie.
INTERP_MAX_DEFAUT = 3

# Plafond de deplacement de la fenetre, en pixels pleine resolution, au-dela
# duquel une coupe courte n'est PLUS comblee par interpolation lineaire, meme
# si sa longueur est sous INTERP_MAX_DEFAUT. Sans ce plafond, une coupe en fin
# de sequence (croissant tranche par l'horizon, mesure de centre bruitee)
# pouvait melanger deux vues distantes de 120 a 177 px : l'interpolation
# produit alors un dedoublement rectangulaire visible plutot qu'un fondu.
# Mesure : ramene le deplacement p90 en fin de sequence de 87 a 15 px, sans
# toucher au milieu de sequence (42 px, inchange).
# Ces chiffres ont ete mesures sous l'ancienne butee dure (clip(s, bornes)) :
# la fenetre elle-meme ne pouvait pas depasser le cadre, ce qui plafonnait
# artificiellement le deplacement mesurable. Mesure sous le planificateur
# (track.planifie_trajectoire) : le plafond attrape desormais un doublement
# qu'il manquait avant — coupe 1826->1828, fenetre deplacee de 178 px (mesuree
# a 14 px sous l'ancien clamp, alors que le disque bougeait de 182 px dans la
# frame). Le p90 des deplacements de coupes courtes reste a 27,1 px : le
# defaut de 30 px tient toujours.
INTERP_DEPLACEMENT_MAX_DEFAUT = 30.0

#: Depassement maximal de la fenetre au-dela des bords de la source, en px.
#: La bande sans pixels source est comblee par replication de bord
#: (render, remplissage 'bord').
#:
#: La fenetre se POSE sur le centre mesure (track.planifie_trajectoire) : ce
#: depassement est donc la seule chose qui decide si elle peut suivre le
#: disque ou doit le laisser se decentrer. Il n'y a plus de vitesse de
#: recentrage — la constante VITESSE_RECENTRAGE_DEFAUT et l'option
#: --vitesse-recentrage ont ete retirees avec le modele de poursuite.
#:
#: Porte de 200 a 400 : l'excursion verticale du disque sur la sequence
#: reelle atteint 1174 px, contre un corridor de 426 px de course. A 200 le
#: corridor total valait 826 px et le disque en sortait sur 11,6 % des frames
#: de fin ; a 400 il vaut 1226 px et couvre l'excursion. Mesure : saut du
#: disque entre frames rendues, p90 de 2,57 px a 0,00, maximum de 54 a 0 sur
#: le tri « disque entier ».
#:
#: Cout mesure : la part de frames portant une bande repliquee passe de
#: 20,9 % a 22,9 %, son p90 de 63,6 a 73,6 px. Sur le tri « disque entier »
#: elle DESCEND a 18,3 % et 57,7 px — moins qu'aujourd'hui, parce que la
#: fenetre n'a plus besoin de s'ecarter pour rattraper un retard.
DEPASSEMENT_BUTEE_DEFAUT = 400.0

#: Fraction minimale de la lumiere que le masque solaire doit capturer pour
#: qu'une mesure de centre entre dans la trajectoire (voir
#: quality.masse_captee). En dessous, la position est reprise des voisines.
#:
#: Mesure sur la sequence reelle : mediane 0,997, et les 33 echecs francs sont
#: tous sous 0,50, avec une zone lumineuse 1,4 a 2,4 fois plus large que le
#: Soleil. A 0,80 le critere ecarte 75 mesures, contre 114 pour l'ancien
#: critere fonde sur le verdict de tri.
#:
#: Ce seuil est un choix, pas une evidence : onze frames occupent l'intervalle
#: entre 0,50 et 0,92, la separation n'est pas bimodale. 0,80 signifie « au
#: moins quatre cinquiemes de la lumiere sont la ou le centre le pretend ».
SEUIL_MASQUE_DEFAUT = 0.80


def _chemin_canonique(path):
    """Chemin comparable : casse normalisee et liens resolus.

    Comparer des chemins bruts laisserait passer, sous Windows,
    'SOURCE.MP4' face a 'source.mp4' — et l'encodeur, lance avec -y,
    tronquerait la source. Le systeme de fichiers est insensible a la
    casse ; la comparaison doit l'etre aussi.
    """
    return os.path.normcase(os.path.realpath(path))


def _signature_source(path):
    st = os.stat(path)
    return {"path": _chemin_canonique(path), "size": st.st_size,
            "mtime": int(st.st_mtime)}


def charger_cache(cache_path, source):
    """Cache d'analyse, ou None s'il est absent, perime ou incompatible."""
    if not os.path.isfile(cache_path):
        return None
    try:
        with open(cache_path, encoding="utf-8") as f:
            donnees = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    if donnees.get("schema") != SCHEMA_VERSION:
        return None
    # Signature entiere, et pas la seule taille : un reencodage a taille
    # identique rendrait avec des mesures perimees, silencieusement et
    # avec une sortie d'apparence correcte.
    if donnees.get("source") != _signature_source(source):
        return None
    return donnees


def analyze(source, cache_path, scale=0.5, radius=None, processus=1,
            progression=None):
    """Passe 1 : mesure chaque frame et ecrit le cache.

    processus : nombre de travailleurs pour le calcul par frame ; par defaut
    1, soit le chemin sequentiel. Le defaut de la BIBLIOTHEQUE est deliberement
    different de celui de la ligne de commande (parallele.PROCESSUS_DEFAUT, un
    de moins que les coeurs logiques) : un appel programmatique doit etre
    deterministe et independant de l'hote, sans quoi le simple fait d'appeler
    analyze() lance cpu_count() - 1 interpreteurs, ce qui coute plus que la
    mesure sur une courte sequence et fait dependre la duree de la machine.
    progression : appelable (fait, total) invoque une fois par frame mesuree,
    fait allant de 1 au nombre de frames. Le total reste None ici : le nombre
    de frames n'est connu qu'a la fin de la boucle. Ce rappel est aussi le
    point d'annulation du moteur de taches — ce qu'il leve remonte.
    """
    # Valide avant de decoder quoi que ce soit : sinon --processus 0 ne
    # echouerait qu'apres l'estimation du rayon, 300 frames plus tard.
    nb = nombre_processus(processus)
    info = probe(source)
    lw = max(2, int(round(info["width"] * scale)) // 2 * 2)
    lh = max(2, int(round(info["height"] * scale)) // 2 * 2)

    print(f"Analyse de {os.path.basename(source)} en {lw}x{lh}...")

    # Deux lectures, jamais d'accumulation d'images : la sequence reelle
    # pese 4 Go en 540x960 float32. La premiere lecture ne retient que des
    # aires, la seconde ne garde qu'une frame a la fois.
    if radius is None:
        with FrameReader(source, width=lw, height=lh) as reader:
            grays = (f.astype(np.float32).mean(axis=2) for f in reader)
            r = estimate_radius(islice(grays, FRAMES_ECHANTILLON_RAYON))
    else:
        # Meme rapport exact que dans render(), et non 1/scale : les
        # dimensions d'analyse sont arrondies au pixel pair.
        r = float(radius) * (lw / info["width"])

    frames = []
    # closing() libere le pool des que le with est quitte, y compris par une
    # exception : sans lui, une trace retenue garde vivante la frame d'analyze,
    # donc le generateur et son pool avec elle.
    with FrameReader(source, width=lw, height=lh) as reader, \
         closing(applique_travaux(
             mesure_frame, ((rgb, r) for rgb in reader), nb)) as mesures:
        # Le parent decode et distribue ; les travailleurs calculent. Les
        # resultats reviennent dans l'ordre des frames, ce qui rend le cache
        # identique a celui du chemin sequentiel.
        for n, mes in enumerate(mesures):
            q, p = mes["q"], mes["p"]
            frames.append({
                "n": n,
                "cx": None if not np.isfinite(mes["cx"]) else float(mes["cx"]),
                "cy": None if not np.isfinite(mes["cy"]) else float(mes["cy"]),
                "conf": float(mes["conf"]),
                "disk_p90": _ou_none(q["disk_p90"]),
                "limb_sharpness": _ou_none(q["limb_sharpness"]),
                "flare_ratio": _ou_none(q["flare_ratio"]),
                "masse_captee": _ou_none(mes["m"]),
                "level": _ou_none(p["level"]),
                "wb": [_ou_none(v) for v in p["wb"]],
            })
            if progression is not None:
                progression(n + 1)

    if not frames:
        raise ValueError(f"Aucune frame decodee depuis {source}")
    print(f"{len(frames)} frames, rayon apparent estime a {r:.1f} px")

    donnees = {"schema": SCHEMA_VERSION, "source": _signature_source(source),
               "scale": scale, "radius": r, "width": lw, "height": lh,
               "fps": info["fps"], "frames": frames}
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(donnees, f)
    print(f"Cache ecrit dans {cache_path}")
    return donnees


def _ou_none(v):
    return None if v is None or not np.isfinite(v) else float(v)


def _colonne(frames, cle, defaut=np.nan):
    return np.array([defaut if f[cle] is None else f[cle] for f in frames],
                    dtype=np.float64)


def render(source, sortie, cache_path, seuils=None, taille=None,
          taille_sortie=None, interp_max=INTERP_MAX_DEFAUT,
          tolerance_bord=None, frames_dir=None,
          interp_deplacement_max=INTERP_DEPLACEMENT_MAX_DEFAUT,
          seuil_masque=None,
          decisions_path=None, sans_decisions=False,
          depassement_butee=None,
          processus=1, progression=None):
    """Passe 2 : trie, stabilise, normalise et encode.

    taille : (largeur, hauteur) de la fenetre de recadrage, paire ; par
    defaut tailles_defaut() en derive une de la source (7/9 de ses
    dimensions, meme rapport). C'est a cette taille que le disque est
    recentre, la fenetre elle-meme etant butee contre les bords de la source
    (voir plus bas) plutot que rejetee quand elle deborde.
    taille_sortie : (largeur, hauteur) du fichier encode, paire ; par defaut
    les dimensions de la source. La fenetre de recadrage y est reagrandie
    par ffmpeg (voir io.FrameWriter), pour restituer le format d'origine.
    interp_max : longueur maximale, en frames, d'une coupe comblee par
    interpolation lineaire (render.melange_lineaire). Au-dela, la coupe
    reste franche. 0 desactive l'interpolation.
    tolerance_bord : tolerance en pixels, pleine resolution, sur l'amputation
    du disque par le bord de la source (verdicts_hors_source) ; par defaut
    TOLERANCE_BORD_DEFAUT.
    frames_dir : si fourni, exporte en plus les frames conservees (et les
    frames interpolees) en PNG numerote dans ce dossier, a la taille de
    sortie finale : la sequence PNG doit correspondre exactement a la
    video.
    interp_deplacement_max : plafond, en pixels pleine resolution, sur le
    deplacement de la fenetre entre les deux frames encadrant une coupe
    comblee ; par defaut INTERP_DEPLACEMENT_MAX_DEFAUT. Au-dela, melanger
    deux vues aussi distantes produirait un dedoublement visible au lieu
    d'un fondu (voir INTERP_DEPLACEMENT_MAX_DEFAUT). 0 desactive toute
    interpolation.
    seuil_masque : fraction minimale de lumiere que le masque solaire doit
    capturer pour qu'une mesure de centre entre dans la trajectoire (voir
    quality.masse_captee) ; par defaut SEUIL_MASQUE_DEFAUT.
    decisions_path : fichier de decisions manuelles (voir decisions.py) a
    superposer aux verdicts automatiques ; par defaut
    decisions.DECISIONS_DEFAUT_NOM s'il existe. Incompatible avec
    sans_decisions.
    sans_decisions : ignore toute decision manuelle, meme si le fichier par
    defaut existe ; sert a comparer le tri automatique au tri revu.
    depassement_butee : depassement maximal de la fenetre au-dela des bords
    de la source, en px ; par defaut DEPASSEMENT_BUTEE_DEFAUT.
    processus : nombre de travailleurs pour le calcul par frame ; par defaut
    1, soit le chemin sequentiel, pour la meme raison que dans analyze() — le
    defaut de la ligne de commande, lui, reste parallele.PROCESSUS_DEFAUT.
    progression : appelable (fait, total) invoque une fois par frame gardee,
    fait allant de 1 a len(gardes). Le total est exact, connu avant la boucle.
    Ce rappel est aussi le point d'annulation du moteur de taches.
    """
    if _chemin_canonique(source) == _chemin_canonique(sortie):
        raise ValueError("La sortie ne peut pas ecraser la source")

    donnees = charger_cache(cache_path, source)
    if donnees is None:
        raise FileNotFoundError(
            f"Cache d'analyse absent ou perime : {cache_path}. "
            f"Lancer d'abord : python -m eclipse analyze {source}"
        )

    frames = donnees["frames"]
    n = len(frames)

    info = probe(source)
    src_w, src_h = info["width"], info["height"]
    fenetre_defaut, sortie_defaut = tailles_defaut(src_w, src_h)
    out_w, out_h = fenetre_defaut if taille is None else (int(taille[0]), int(taille[1]))
    sortie_w, sortie_h = (sortie_defaut if taille_sortie is None
                          else (int(taille_sortie[0]), int(taille_sortie[1])))
    # La fenetre de recadrage doit respecter le rapport de la sortie finale :
    # sinon l'agrandissement par ffmpeg (scale=...) etire l'image de facon
    # non uniforme en x et en y, deformant le disque en ellipse. Le seuil est
    # RELATIF (0,5 % d'ellipticite, invisible a l'oeil) et non absolu : les
    # dimensions paires imposees par yuv420p empechent de suivre le rapport
    # exactement sur les petites sources, et les defauts derives par
    # tailles_defaut ne doivent pas declencher d'avertissement.
    if abs((out_w / out_h) / (sortie_w / sortie_h) - 1.0) > 5e-3:
        print(
            f"Attention : la fenetre de recadrage {out_w}x{out_h} ne respecte "
            f"pas le rapport {sortie_w}x{sortie_h} de la sortie ; "
            f"l'agrandissement va deformer l'image"
        )

    tolerance_bord = (TOLERANCE_BORD_DEFAUT if tolerance_bord is None
                     else float(tolerance_bord))

    # Verdicts automatiques et trajectoire : voir verdicts.analyse_verdicts,
    # partage avec le viewer pour que les deux suivent exactement le meme
    # chemin.
    resultat = analyse_verdicts(donnees, src_w, src_h, seuils,
                                tolerance_bord, seuil_masque)
    verdicts = resultat["verdicts"]
    traj_x, traj_y = resultat["traj_x"], resultat["traj_y"]
    kx, ky = resultat["kx"], resultat["ky"]
    if sans_decisions and decisions_path is not None:
        raise ValueError(
            "--decisions et --sans-decisions sont contradictoires ; "
            "n'en donner qu'une")
    if not sans_decisions:
        chemin = DECISIONS_DEFAUT_NOM if decisions_path is None else decisions_path
        signature = _signature_source(source)
        # Un fichier de decisions present mais refuse (schema perime, source
        # differente, JSON corrompu) doit se voir, pas disparaitre en
        # silence : sinon une revue humaine de plusieurs heures s'evapore au
        # premier re-telechargement de la source sans que personne ne le
        # remarque.
        raison = diagnostique(chemin, signature)
        if raison:
            print(f"ATTENTION : {langues.rend_fr(raison)}", file=sys.stderr)
        ecarts = charger(chemin, signature)
        if ecarts:
            print(f"{len(ecarts)} decision(s) manuelle(s) depuis {chemin}")
        verdicts = applique(verdicts, ecarts)
    garde = np.array([v is None for v in verdicts], dtype=bool)
    if not garde.any():
        raise ValueError(
            "Toutes les frames ont ete rejetees. Assouplir les seuils."
        )

    depassement = (DEPASSEMENT_BUTEE_DEFAUT if depassement_butee is None
                   else float(depassement_butee))
    # La planification consomme le garde FINAL, decisions comprises : le
    # corridor depend des frames reellement gardees. C'est pour cela
    # qu'elle vit ici et non dans analyse_verdicts, qui doit rester
    # identique entre viewer et rendu.
    centre_x = planifie_trajectoire(traj_x, (out_w / 2.0, src_w - out_w / 2.0),
                                    depassement)
    centre_y = planifie_trajectoire(traj_y, (out_h / 2.0, src_h - out_h / 2.0),
                                    depassement)

    gains = solve_corrections(_colonne(frames, "level"), garde)

    motifs = {}
    for v in verdicts:
        if v is not None:
            motifs[v] = motifs.get(v, 0) + 1

    print(f"{int(garde.sum())}/{n} frames conservees. Rejets : "
          + (", ".join(f"{k}={v}" for k, v in sorted(motifs.items())) or "aucun"))

    ecrites = 0        # frames reellement rendues depuis la source (hors
                       # interpolees) ; la progression s'appuie deliberement
                       # sur ce compteur : il exclut les interpolees, donc il
                       # ne peut jamais depasser len(gardes) et faire passer
                       # la barre au-dessus de 100 % (voir l'appel plus bas).
    interpolees = 0     # frames synthetiques comblant une coupe courte
    derniere = None     # derniere frame transformee ecrite, une seule en memoire
    dernier_indice = None

    def ecrire(f, writer, export):
        writer.write(f)
        if export is not None:
            export.write(f)

    nb = nombre_processus(processus)
    # Le lecteur decode toujours a la resolution source : c'est
    # render.apply_frame, appele chez les travailleurs par parallele.rend_frame
    # avec taille=(out_w, out_h), qui recadre au vol vers le cadre resserre.
    # FrameWriter et le PngSequenceWriter (le cas echeant) reagrandissent
    # ensuite vers (sortie_w, sortie_h), pour que la video et les PNG
    # correspondent exactement.
    with FrameReader(source, width=src_w, height=src_h) as reader:
        # Le parent selectionne et decode, les travailleurs transforment, le
        # parent recoit dans l'ordre puis interpole et ecrit. Seul le parent
        # connait l'ordre de sortie et la frame precedente, donc
        # l'interpolation reste ici.
        gardes = [i for i in range(n) if garde[i]]

        # travaux() ne lit que reader, garde, n, centre_x, centre_y,
        # gains, out_w et out_h : aucune de ces variables n'est assignee
        # ici, donc aucun nonlocal n'est necessaire. Le generateur
        # parcourt les frames gardees dans le meme ordre que la liste
        # gardes, ce que le zip ci-dessous suppose.
        def travaux():
            for i, frame in enumerate(reader):
                if i >= n:
                    break
                if not garde[i]:
                    continue
                yield (frame, centre_x[i], centre_y[i], gains[i],
                       (out_w, out_h), "bord")

        # closing() sur le generateur du pool, et non un simple appel : il est
        # ainsi libere des que le with est quitte, y compris par une exception,
        # sans attendre que le ramasse-miettes fasse parvenir GeneratorExit au
        # with interne. Place en dernier, il est aussi ferme en premier, donc
        # avant que FrameWriter ne finalise le fichier.
        with FrameWriter(sortie, width=out_w, height=out_h, fps=donnees["fps"],
                         taille_encodage=(sortie_w, sortie_h)) as writer, \
             _ouvre_sequence(frames_dir, out_w, out_h,
                            (sortie_w, sortie_h)) as export, \
             closing(applique_travaux(rend_frame, travaux(), nb)) as rendues:
            for i, rendue in zip(gardes, rendues):
                # Coupe courte depuis la derniere frame ecrite : on comble
                # avant d'ecrire la frame courante, en t croissant, pour que
                # la sequence reste monotone.
                if dernier_indice is not None:
                    manquantes = i - dernier_indice - 1
                    # Le deplacement est mesure entre les centres des deux
                    # fenetres PLANIFIEES encadrant la coupe (track.
                    # planifie_trajectoire, coordonnees source pleine
                    # resolution) : c'est ce que l'interpolation va
                    # reellement melanger. Sans ce plafond, une coupe
                    # courte mais distante (croissant tranche par
                    # l'horizon) produit un dedoublement rectangulaire au
                    # lieu d'un fondu.
                    deplacement = float(np.hypot(centre_x[i] - centre_x[dernier_indice],
                                                 centre_y[i] - centre_y[dernier_indice]))
                    if (0 < manquantes <= interp_max
                            and deplacement <= interp_deplacement_max
                            and derniere is not None):
                        for k in range(1, manquantes + 1):
                            ecrire(melange_lineaire(derniere, rendue,
                                                    k / (manquantes + 1)),
                                  writer, export)
                        interpolees += manquantes

                ecrire(rendue, writer, export)
                derniere = rendue
                dernier_indice = i
                ecrites += 1
                if progression is not None:
                    progression(ecrites, len(gardes))

    # Le deficit de decodage est signale, pas fondu dans le compte de
    # rejets : ces frames-la n'ont pas ete ecartees, elles ont manque.
    attendues = int(garde.sum())
    if ecrites != attendues:
        print(f"Attention : {attendues - ecrites} frames conservees n'ont pas "
              f"ete decodees en pleine resolution")

    total = ecrites + interpolees
    print(f"Ecrit {total} frames dans {sortie} ({interpolees} interpolees)")
    return {"gardees": total, "rejetees": n - attendues, "motifs": motifs,
            "interpolees": interpolees}


class _ouvre_sequence:
    """Contexte no-op si frames_dir est None, PngSequenceWriter sinon.

    Evite de dupliquer la boucle d'ecriture selon que l'export PNG est
    demande ou non.
    """

    def __init__(self, frames_dir, width, height, taille_encodage=None):
        self._writer = (PngSequenceWriter(frames_dir, width, height,
                                          taille_encodage=taille_encodage)
                        if frames_dir else None)

    def __enter__(self):
        return self._writer

    def __exit__(self, *exc):
        if self._writer is not None:
            self._writer.close()
        return False


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="eclipse",
        description="Nettoyage et stabilisation d'un timelapse d'eclipse solaire",
    )
    sous = p.add_subparsers(dest="commande", required=True)

    a = sous.add_parser("analyze", help="Passe 1 : mesurer et mettre en cache")
    a.add_argument("source")
    a.add_argument("--cache", default="analysis.json")
    a.add_argument("--scale", type=float, default=0.5)
    a.add_argument("--radius", type=float, default=None,
                   help="Rayon apparent en pixels pleine resolution, "
                        "si l'estimation automatique echoue")
    _ajoute_processus(a)

    r = sous.add_parser("render", help="Passe 2 : trier, stabiliser, encoder")
    r.add_argument("source")
    r.add_argument("sortie")
    r.add_argument("--cache", default="analysis.json")
    _ajoute_processus(r)
    _ajoute_seuils(r)
    _ajoute_cadrage(r)

    x = sous.add_parser("run", help="Les deux passes")
    x.add_argument("source")
    x.add_argument("sortie")
    x.add_argument("--cache", default="analysis.json")
    # default=None (et non 0.5) pour distinguer une valeur explicitement
    # fournie de l'absence d'argument : si un cache valide est reutilise,
    # --scale/--radius sont sans effet et on veut pouvoir le signaler.
    x.add_argument("--scale", type=float, default=None)
    x.add_argument("--radius", type=float, default=None)
    _ajoute_processus(x)
    _ajoute_seuils(x)
    _ajoute_cadrage(x)

    v = sous.add_parser("viewer", help="Revoir les frames et corriger le tri")
    # Optionnelle : sans source, le viewer s'ouvre sur rien et l'utilisateur
    # en choisit une depuis la page (voir viewer.sert). Avec une source, le
    # comportement de la ligne de commande ne bouge pas -- --cache et
    # --decisions gardent leurs valeurs.
    v.add_argument("source", nargs="?", default=None)
    v.add_argument("--cache", default="analysis.json")
    v.add_argument("--port", type=int, default=8000)
    # Memes options que render/run (et non une declaration maison) : sinon
    # un rendu lance avec --blur-rel ou --taille ajustes serait revu dans le
    # viewer contre les seuils par defaut, silencieusement (finding 1).
    # _ajoute_cadrage fournit deja --decisions ; --sans-decisions n'a pas de
    # sens ici et est refuse plus bas.
    _ajoute_seuils(v)
    _ajoute_cadrage(v)

    args = p.parse_args(argv)
    seuils = {k: getattr(args, k) for k in
              ("dark_rel", "dark_abs", "blur_rel", "flare_rel", "conf_min",
               "ilot_min")
              if getattr(args, k, None) is not None}

    try:
        if args.commande == "analyze":
            analyze(args.source, args.cache, args.scale, args.radius,
                    processus=args.processus)
        elif args.commande == "render":
            render(args.source, args.sortie, args.cache, seuils or None,
                  taille=args.taille, taille_sortie=args.sortie_taille,
                  interp_max=args.interp_max, tolerance_bord=args.tolerance_bord,
                    interp_deplacement_max=args.interp_deplacement_max,
                  seuil_masque=args.seuil_masque,
                  decisions_path=args.decisions_path,
                  sans_decisions=args.sans_decisions,
                  depassement_butee=args.depassement_butee,
                  processus=args.processus)
        elif args.commande == "viewer":
            if args.sans_decisions:
                raise ValueError(
                    "--sans-decisions n'a pas de sens pour le viewer : il "
                    "affiche toujours les ecarts manuels, par definition "
                    "(utiliser --decisions pour choisir un autre fichier)"
                )
            # Pourquoi ce refus, et non un simple passage de l'option : la
            # livraison de la sequence PNG lancee depuis la page PERMUTE le
            # dossier d'export en entier (elle l'ecarte, met le neuf a sa
            # place, puis supprime l'ancien -- voir viewer._permute_dossier).
            # Dirige vers un dossier a l'utilisateur, ce mecanisme en
            # detruirait tout le contenu, PNG ou non. En ligne de commande,
            # render() se contente d'ecrire DANS le dossier : le danger
            # n'existe que pour le viewer, d'ou le refus ici seulement.
            if args.frames_dir is not None:
                raise ValueError(
                    "--frames-dir n'est pas accepte par le viewer. "
                    "Le rendu lance depuis la page exporte la sequence PNG "
                    "dans <source>-frames, a cote de la video source. "
                    "Pour choisir un autre dossier, utiliser la commande "
                    "render."
                )
            # Le cadrage suit le meme chemin que les seuils, jusqu'au rendu
            # lance depuis la page : le sous-parseur accepte ces options, il
            # ne doit pas les avaler (voir viewer.sert). Seul --frames-dir en
            # est exclu, refuse juste au-dessus.
            sert(args.source, args.cache, args.decisions_path, port=args.port,
                seuils=seuils or None,
                tolerance_bord=args.tolerance_bord,
                seuil_masque=args.seuil_masque,
                taille=args.taille, taille_sortie=args.sortie_taille,
                interp_max=args.interp_max,
                interp_deplacement_max=args.interp_deplacement_max,
                depassement_butee=args.depassement_butee)
        else:
            if charger_cache(args.cache, args.source) is None:
                analyze(args.source, args.cache,
                        args.scale if args.scale is not None else 0.5,
                        args.radius, processus=args.processus)
            else:
                print(f"Cache valide reutilise : {args.cache}")
                if args.scale is not None or args.radius is not None:
                    print(
                        "Attention : --scale/--radius sont ignores, la "
                        "resolution d'analyse du cache est conservee. "
                        "Relancer 'analyze' pour en changer."
                    )
            render(args.source, args.sortie, args.cache, seuils or None,
                  taille=args.taille, taille_sortie=args.sortie_taille,
                  interp_max=args.interp_max, tolerance_bord=args.tolerance_bord,
                    interp_deplacement_max=args.interp_deplacement_max,
                  seuil_masque=args.seuil_masque,
                  decisions_path=args.decisions_path,
                  sans_decisions=args.sans_decisions,
                  depassement_butee=args.depassement_butee,
                  processus=args.processus)
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1
    return 0


def _ajoute_processus(parseur):
    """--processus, pour analyze, render et run.

    Deliberement hors de _ajoute_seuils et _ajoute_cadrage : ceux-la servent
    aussi le sous-parseur viewer, qui n'expose pas cette option. Non parce
    qu'elle y serait sans objet — la page execute desormais les deux passes,
    et l'analyse qu'elle lance est sequentielle (7,7 min au lieu de 5,9) —
    mais parce que l'exposer est une decision qui n'est pas prise : elle
    attend le sous-systeme B.

    default=PROCESSUS_DEFAUT et non 1 : les fonctions de la bibliotheque, elles,
    sont sequentielles par defaut (voir analyze()). C'est ici, et ici seulement,
    que le nombre de travailleurs est deduit du materiel.
    """
    parseur.add_argument(
        "--processus", type=int, default=PROCESSUS_DEFAUT,
        help="Nombre de travailleurs pour le calcul par frame "
             "(defaut : un de moins que les coeurs logiques ; "
             "1 = chemin sequentiel)")


def _ajoute_seuils(parseur):
    parseur.add_argument("--dark-rel", dest="dark_rel", type=float, default=None)
    parseur.add_argument("--dark-abs", dest="dark_abs", type=float, default=None)
    parseur.add_argument("--blur-rel", dest="blur_rel", type=float, default=None)
    parseur.add_argument("--flare-rel", dest="flare_rel", type=float, default=None)
    parseur.add_argument("--conf-min", dest="conf_min", type=float, default=None)
    parseur.add_argument(
        "--ilot-min", dest="ilot_min", type=int, default=None,
        help="Longueur minimale d'une plage conservee (defaut "
             f"{SEUILS_DEFAUT['ilot_min']} : aucun ilot supprime)")


def _parse_taille(s):
    """Parse 'LARGEURxHAUTEUR' en couple d'entiers pairs.

    Pairs : utilise aussi bien pour --sortie-taille, dimensions du fichier
    encode en yuv420p qui exige des dimensions paires, que pour --taille, la
    fenetre de recadrage, par coherence.
    """
    m = re.match(r"^(\d+)x(\d+)$", s)
    if not m:
        raise argparse.ArgumentTypeError(
            f"Taille invalide : {s!r} (attendu LARGEURxHAUTEUR, ex. 800x1120)"
        )
    largeur, hauteur = int(m.group(1)), int(m.group(2))
    if largeur % 2 or hauteur % 2:
        raise argparse.ArgumentTypeError(
            f"Taille invalide : {s!r} (largeur et hauteur doivent etre paires)"
        )
    return (largeur, hauteur)


def _ajoute_cadrage(parseur):
    parseur.add_argument(
        "--taille", type=_parse_taille, default=None,
        help="Taille de la fenetre de recadrage LARGEURxHAUTEUR, paire "
             "(defaut : 7/9 des dimensions de la source, meme rapport)")
    parseur.add_argument(
        "--sortie-taille", dest="sortie_taille", type=_parse_taille,
        default=None,
        help="Taille du fichier encode LARGEURxHAUTEUR, paire (defaut : les "
             "dimensions de la source) ; la fenetre de recadrage y est "
             "reagrandie")
    parseur.add_argument(
        "--interp-max", dest="interp_max", type=int,
        default=INTERP_MAX_DEFAUT,
        help="Longueur maximale, en frames, d'une coupe comblee par "
             f"interpolation lineaire (defaut {INTERP_MAX_DEFAUT}, 0 pour "
             "desactiver)")
    parseur.add_argument(
        "--tolerance-bord", dest="tolerance_bord", type=float,
        default=TOLERANCE_BORD_DEFAUT,
        help="Tolerance en pixels, pleine resolution, sur l'amputation du "
             f"disque par le bord de la source (defaut {TOLERANCE_BORD_DEFAUT:g})")
    parseur.add_argument(
        "--frames-dir", dest="frames_dir", default=None,
        help="Dossier d'export de la sequence PNG numerotee des frames "
             "conservees, a la taille de sortie finale")
    parseur.add_argument(
        "--interp-deplacement-max", dest="interp_deplacement_max", type=float,
        default=INTERP_DEPLACEMENT_MAX_DEFAUT,
        help="Plafond en pixels pleine resolution sur le deplacement de la "
             "fenetre entre les frames encadrant une coupe comblee (defaut "
             f"{INTERP_DEPLACEMENT_MAX_DEFAUT:g}, 0 pour desactiver toute "
             "interpolation)")
    parseur.add_argument(
        "--seuil-masque", dest="seuil_masque", type=float, default=None,
        help="Fraction minimale de lumiere que le masque solaire doit "
             "capturer pour qu'une mesure serve au cadrage (defaut "
             f"{SEUIL_MASQUE_DEFAUT:g})")
    parseur.add_argument(
        "--decisions", dest="decisions_path", default=None,
        help=f"Fichier de decisions manuelles (defaut {DECISIONS_DEFAUT_NOM} "
             "s'il existe)")
    parseur.add_argument(
        "--sans-decisions", dest="sans_decisions", action="store_true",
        help="Ignore les decisions manuelles, pour comparer le tri "
             "automatique au tri revu")
    parseur.add_argument(
        "--depassement-butee", dest="depassement_butee", type=float,
        default=None,
        help="Depassement maximal de la fenetre au-dela de la source, en px "
             f"(defaut {DEPASSEMENT_BUTEE_DEFAUT:g})")
