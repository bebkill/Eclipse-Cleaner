"""Serveur local de revue des frames.

N'ecoute que sur 127.0.0.1 : c'est un outil local, pas un service expose.

Les verdicts viennent de verdicts.analyse_verdicts, le meme chemin que le
rendu — ce que l'utilisateur voit en rouge est donc exactement ce que le
rendu ecarte.
"""
import json
import os
import shutil
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import langues
from .version import version_affichee
from .decisions import DECISIONS_DEFAUT_NOM, charger, diagnostique, enregistrer
from .descripteur import compare, ecrit
from .dialogue import Indisponible, choisit_video
from .etapes import ETAPES, etats, faits
from .io import probe
from .taches import GENRES, Moteur, Occupe, TacheAnnulee
from .verdicts import analyse_verdicts
from .vignettes import (DOSSIER_DEFAUT, Interrompue, _marqueur, a_jour,
                        chemin_vignette, compte, genere)

_PAGE = os.path.join(os.path.dirname(__file__), "static", "viewer.html")

#: Taille maximale acceptee pour le corps d'un POST (/api/decision comme
#: /api/tache). Les deux tiennent en une ligne JSON de quelques octets ;
#: quelques Ko laissent une marge large sans laisser un client (ou un script
#: errant) gonfler la memoire du serveur avec un corps arbitrairement grand.
TAILLE_CORPS_MAX = 4096


def nb_frames_estime(info):
    """Nombre de frames deduit de la duree et de la cadence.

    C'est une ESTIMATION : probe() ne rend aucun compte de frames (voir
    io._analyse_sortie_ffmpeg), seulement une duree et une cadence. Sur la
    sequence reelle, 85,2 s x 30,01 fps donne 2557 pour 2556 frames. La barre
    d'avancement doit donc plafonner son pourcentage.
    """
    return max(1, int(round(info["duration"] * info["fps"])))


def _json_natif(valeur):
    """La meme valeur, en structures qui survivent a un aller-retour JSON.

    Tuples en listes, cles de dictionnaire en texte, recursivement.

    Le descripteur exige des structures JSON natives (voir
    descripteur.ecrit) et le cadrage n'en est pas : _parse_taille rend un
    COUPLE d'entiers, que --taille et --sortie-taille portent tel quel
    jusqu'ici. Ecrit brut, un couple revient en liste a la relecture,
    perime() compare alors (840, 1494) a [840, 1494] et repond « perime »
    pour toujours : le bandeau afficherait « rendu a refaire » en
    permanence, des la premiere seconde apres un rendu. Et aucun test ne
    crierait, puisque perime() penche deja de ce cote par construction —
    d'ou la conversion ici, et le test qui rend explicitement avec un
    cadrage.
    """
    if isinstance(valeur, (list, tuple)):
        return [_json_natif(v) for v in valeur]
    if isinstance(valeur, dict):
        return {str(cle): _json_natif(v) for cle, v in valeur.items()}
    return valeur


def _reglages(seuils, tolerance_bord, seuil_masque, cadrage, couleur):
    """Ce qui, hors decisions et hors cache, determine le rendu produit.

    UN SEUL constructeur, appele des deux cotes : l'ecriture du descripteur
    a la fin du rendu et la comparaison faite a chaque /api/frames. Deux
    constructeurs se desynchroniseraient au premier reglage ajoute, et le
    symptome — « a refaire » perpetuel — ressemble a un fonctionnement
    normal.

    couleur : les reglages de stabilisation de balance, TOUJOURS
    materialises (defauts compris, voir _couleur_normalisee) et jamais un
    dictionnaire creux : « valeur par defaut » et « valeur explicitement
    egale au defaut » doivent produire le meme descripteur, sans quoi
    toucher la case de la page sans rien changer dirait « a refaire ».
    """
    return _json_natif({"seuils": seuils, "tolerance_bord": tolerance_bord,
                        "seuil_masque": seuil_masque,
                        "cadrage": cadrage or {},
                        "couleur": couleur})


def _couleur_normalisee(actif=None, fenetre=None, amplitude=None):
    """Les reglages de stabilisation de balance, defauts materialises.

    UN SEUL endroit ou les defauts s'appliquent : le Porteur a sa
    construction, construit_etat pour un appel direct, et la route
    POST /api/couleur ne passent que par ici. L'import est local parce que
    pipeline importe viewer (voir pipeline.main) : l'inverse au niveau du
    module serait circulaire.
    """
    from .photometry import AMPLITUDE_COULEUR_DEFAUT, FENETRE_COULEUR_DEFAUT
    from .pipeline import COULEUR_DEFAUT
    return {
        "actif": COULEUR_DEFAUT if actif is None else bool(actif),
        "fenetre": (FENETRE_COULEUR_DEFAUT if fenetre is None
                    else int(fenetre)),
        "amplitude": (AMPLITUDE_COULEUR_DEFAUT if amplitude is None
                      else float(amplitude)),
    }


def _signature_analyse(donnees):
    """Ce qui identifie l'ANALYSE dont un rendu est issu. None sans cache.

    UN SEUL constructeur, appele des deux cotes, exactement comme _reglages
    et pour la meme raison : l'ecriture du descripteur a la fin du rendu et
    la comparaison faite a chaque /api/frames.

    CINQ CHAMPS, ET LES CINQ SONT NECESSAIRES. La signature de source dit sur
    quelle VIDEO l'analyse a porte ; la resolution dit A QUELLE ECHELLE elle
    l'a mesuree. Les deux peuvent bouger separement, et chacune sans l'autre
    laisse passer un faux negatif -- « deja fait » sur un rendu qui ne
    correspond plus, la seule direction que ce chantier interdit.

    Sans la RESOLUTION : rendre contre un cache fait a scale=0.25, perdre ce
    cache, cliquer « Refaire l'analyse » (qui reprend alors le defaut
    scale=0.5, faute de cache valide dont heriter, voir _reglages_reanalyse)
    et obtenir un descripteur identique -- alors que toutes les mesures que
    lit analyse_verdicts (disk_p90, conf, flare_ratio, radius) sont prises A
    L'ECHELLE D'ANALYSE et ont bouge.

    Sans la SIGNATURE DE SOURCE : reencoder la video EN PLACE (meme chemin,
    autre contenu) invalide le cache, donc l'etape d'analyse redevient a
    faire ; la relancer a la meme echelle redonne exactement la meme
    resolution, et le mp4 de la video PRECEDENTE, toujours sur le disque,
    passerait pour « deja fait ». Attention au raisonnement qui a failli
    couter ce champ : il est vrai que donnees["source"] egale toujours la
    signature courante de la source (charger_cache rend None sinon), mais
    perime() ne compare pas deux valeurs courantes -- il compare la valeur
    ECRITE AU MOMENT DU RENDU a celle d'aujourd'hui. C'est un champ
    d'HISTOIRE, pas un champ d'etat, et etapes.py nomme justement le
    reencodage de la source comme le cas ou un etat stocke ment.

    LA RESOLUTION D'ANALYSE, ET NON UNE SIGNATURE DU FICHIER DE CACHE.
    Prendre (taille, mtime) du cache serait plus exact au sens strict, mais
    perimerait le rendu a chaque reanalyse, y compris une reanalyse aux MEMES
    reglages qui redonne des mesures identiques : douze minutes d'encodage a
    refaire pour rien, et un « a refaire » que l'utilisateur apprendrait a
    ignorer. Ces champs-ci sont exactement ce dont les mesures dependent, et
    ils sont deja dans le cache -- aucune plomberie nouvelle.
    """
    if not donnees:
        return None
    return {"source": donnees.get("source"),
            "scale": donnees.get("scale"), "radius": donnees.get("radius"),
            "width": donnees.get("width"), "height": donnees.get("height")}


def construit_etat(source, cache_path, decisions_path, dossier_vignettes,
                   seuils=None, tolerance_bord=None, seuil_masque=None,
                   cadrage=None, couleur=None):
    """Ce que les routes ont besoin de connaitre, observe sans rien generer.

    Ne leve plus quand le cache manque, quand les vignettes manquent, ou
    quand leurs comptes divergent : ces trois situations sont l'etat NORMAL
    d'un amorcage depuis l'interface, ou l'analyse et les vignettes se font
    en tache. L'etat porte donc pret / manque, et l'interface propose ce qui
    manque.

    Ne genere plus les vignettes : c'est une tache du moteur, avec sa barre.

    seuils, tolerance_bord, seuil_masque : memes parametres de tri que
    pipeline.render, a transmettre identiques a ceux du rendu pour que le
    viewer affiche exactement les verdicts que le rendu applique.

    cadrage : les options de geometrie deja debarrassees de leurs None (voir
    Porteur). Elles ne changent aucun verdict ; elles sont ici parce que le
    descripteur du rendu les enregistre, et que la comparaison qui dit « a
    refaire » doit porter sur exactement le meme dictionnaire.

    couleur : les reglages de stabilisation de balance, deja materialises
    par le Porteur (defauts appliques ici pour un appel direct). Meme
    statut que le cadrage : aucun verdict n'en depend, le descripteur les
    enregistre.
    """
    from .pipeline import _signature_source, charger_cache

    if couleur is None:
        couleur = _couleur_normalisee()

    info = probe(source)
    signature = _signature_source(source)
    donnees = charger_cache(cache_path, source)
    nb_vignettes = compte(dossier_vignettes)

    manque = []
    if donnees is None:
        manque.append("analyse")
    # compte() seul ne regarde que le nombre de fichiers, jamais leur
    # provenance : sans a_jour(), des vignettes d'une AUTRE source (meme
    # nombre de frames par coincidence) passeraient pour pretes, et
    # l'utilisateur trierait silencieusement sur les mauvaises images.
    if (nb_vignettes == 0 or not a_jour(dossier_vignettes, signature)
            or (donnees is not None
                and nb_vignettes != len(donnees["frames"]))):
        manque.append("vignettes")

    base = {
        "source": source, "signature": signature,
        "decisions_path": decisions_path,
        "dossier_vignettes": dossier_vignettes,
        "nb_frames_estime": nb_frames_estime(info),
        "pret": not manque, "manque": manque,
        # Le cache deja charge et les reglages normalises : de quoi
        # recalculer les etats d'etape a chaque requete sans relire le cache
        # ni reconstruire l'etat (voir _etapes_courantes). Une reference,
        # pas une copie : donnees["frames"] est deja porte par l'etat pret.
        "cache": donnees,
        "reglages": _reglages(seuils, tolerance_bord, seuil_masque, cadrage,
                              couleur),
    }
    if manque:
        return {**base, "verdicts": [], "frames": [],
                "fps": info.get("fps", 30.0)}

    resultat = analyse_verdicts(donnees, info["width"], info["height"],
                                seuils, tolerance_bord, seuil_masque)
    avertissement = diagnostique(decisions_path, signature)
    if avertissement:
        print(f"ATTENTION : {langues.rend_fr(avertissement)}")
    return {**base, "verdicts": resultat["verdicts"],
            "frames": donnees["frames"], "fps": donnees.get("fps", 30.0)}


def chemins_derives(source):
    """Cache, decisions et vignettes a cote de la source.

    --cache vaut « analysis.json » et --decisions « decisions.json », deux
    noms RELATIFS au repertoire courant. Tant que le viewer voyait une seule
    source c'etait supportable ; des qu'on en choisit une dans la page, deux
    sources partageraient le meme cache et le meme tri -- les decisions
    prises sur la video A appliquees a la video B. Ce n'est pas une
    hypothese : le rendu lance depuis la page avait deja eu ce defaut.

    L'EXTENSION EST CONSERVEE, et c'est le point le plus important de cette
    fonction. En retirant l'extension, eclipse.mov et eclipse.mp4 -- tous
    deux offerts par dialogue.EXTENSIONS_VIDEO, et une source avec son
    transcodage dans un meme dossier est ordinaire ici -- donnaient des
    chemins identiques au bit. Le chemin destructeur : ouvrir eclipse.mov,
    voir l'avertissement « decisions d'une autre source », cliquer une frame,
    et enregistrer() (os.replace) ecrase TOUT le tri de eclipse.mp4. C'est
    exactement l'invariant que cette fonction existe pour tenir, et il
    tombait.
    Le nom complet en prefixe rend la derivation INJECTIVE par construction :
    deux sources distinctes ne peuvent pas produire le meme nom, sans avoir a
    raisonner sur des cas. Et le resultat est strictement plus long que la
    source, donc aucun derive ne peut jamais designer la source elle-meme.

    ASYMETRIE ASSUMEE avec _sortie_rendu et _dossier_png, qui retirent
    l'extension et gardent donc leur collision : les trois chemins d'ici
    portent du travail IRREMPLACABLE -- un tri manuel ne se reproduit pas, et
    ce projet vient d'en perdre un -- tandis qu'un rendu et un export PNG se
    regagnent en faisant tourner la machine, et ont deja leur porte de
    consentement (le drapeau `ecraser`). Ce n'est pas un oubli.

    Rien n'est cree ni efface ici : ce sont des NOMS.
    """
    return {"cache_path": source + "-analysis.json",
            "decisions_path": source + "-decisions.json",
            "dossier_vignettes": source + "-vignettes"}


def _etat_vide():
    """L'etat d'un viewer ouvert sur rien, avant tout choix de source.

    Un dictionnaire NEUF a chaque appel plutot qu'une constante de module :
    il porte des listes et un dictionnaire, que deux porteurs ne doivent pas
    partager.

    Memes cles que construit_etat, moins « cache » et « reglages » : leurs
    seuls lecteurs (_corps_frames et les _travail_*) sont court-circuites
    tant qu'il n'y a pas de source, et une valeur bidon y serait lue un jour
    comme si elle voulait dire quelque chose.
    """
    return {"source": None, "pret": False, "manque": list(ETAPES),
            "etapes": {nom: "indisponible" for nom in ETAPES},
            "verdicts": [], "frames": [], "fps": 30.0,
            "nb_frames_estime": 0, "signature": None,
            "decisions_path": None, "dossier_vignettes": None}


class Porteur:
    """Detient l'etat courant et sait le reconstruire.

    Le remplacement est une simple affectation d'attribut, atomique sous le
    GIL. Chaque requete saisit la reference une fois en entrant et travaille
    dessus jusqu'au bout : elle voit donc soit l'ancien etat soit le nouveau,
    jamais un panachage. Pas de verrou cote lecture, pas de lecteur bloque
    pendant une reconstruction.

    source vaut None tant qu'aucune video n'a ete choisie : le viewer peut
    s'ouvrir sur rien, et c'est POST /api/source qui lui en donne une (voir
    change_source). Les routes qui auraient besoin d'un chemin
    court-circuitent dans cet etat.
    """

    def __init__(self, source, cache_path, decisions_path, dossier_vignettes,
                 seuils=None, tolerance_bord=None, seuil_masque=None,
                 taille=None, taille_sortie=None, interp_max=None,
                 interp_deplacement_max=None,
                 depassement_butee=None,
                 couleur=None, couleur_fenetre=None, couleur_amplitude=None):
        #: Le cadrage, meme raison que les seuils etendue a la geometrie : le
        #: sous-parseur viewer accepte les memes options que render, et un
        #: utilisateur qui lance le viewer avec --taille 900x1600 puis clique
        #: « Lancer le rendu » doit obtenir CE cadrage, pas celui par defaut.
        #: Les None sont retires plutot que transmis : c'est render qui
        #: detient ses defauts, et un interp_max=None irait jusqu'a une
        #: comparaison numerique. Un dictionnaire deja fait, et non six
        #: attributs separes, parce qu'il part maintenant a DEUX endroits —
        #: les arguments de render (voir _travail_rendu) et les reglages
        #: inscrits au descripteur (voir construit_etat) — qui doivent voir
        #: exactement le meme contenu.
        self.cadrage = {nom: valeur for nom, valeur in (
            ("taille", taille),
            ("taille_sortie", taille_sortie),
            ("interp_max", interp_max),
            ("interp_deplacement_max", interp_deplacement_max),
            ("depassement_butee", depassement_butee),
        ) if valeur is not None}
        #: Le fichier de decisions demande en ligne de commande. Retenu
        #: parce que change_source lui substitue un chemin DERIVE : le tri
        #: revu sous « viewer A.mp4 » est alors ecrit dans ce fichier-ci,
        #: qu'un retour a A.mp4 depuis la page ne lirait plus. Sans cette
        #: memoire, personne ne pourrait le dire (voir _tri_orpheline).
        self._decisions_ligne_de_commande = decisions_path
        #: Publics, pour l'invariant du module : le rendu lance depuis la
        #: page doit ecarter exactement ce que la page affiche en rouge. Sans
        #: ces trois-la, render() reprendrait ses propres defauts et trierait
        #: autrement, en silence.
        self.seuils = seuils
        self.tolerance_bord = tolerance_bord
        self.seuil_masque = seuil_masque
        #: Les reglages de stabilisation de balance, TOUJOURS materialises
        #: (defauts compris) : contrairement au cadrage, la page peut les
        #: MODIFIER (POST /api/couleur, voir regle_couleur), et elle a
        #: besoin de valeurs a afficher, pas d'absences. Un seul
        #: dictionnaire, parce qu'il part a trois endroits qui doivent voir
        #: le meme contenu : les arguments de render (_travail_rendu), les
        #: reglages du descripteur (construit_etat) et la reponse de
        #: /api/frames qui seme les controles de la page.
        self.couleur = _couleur_normalisee(couleur, couleur_fenetre,
                                           couleur_amplitude)
        # Les six options de cadrage ne sont PLUS conservees une a une a
        # cote de self.cadrage : deux sources pour la meme chose, dont une
        # seule serait lue, laisserait un porteur.taille = ... sans effet et
        # un rendu au cadrage muet. self.cadrage est la seule.
        self._pose(source, cache_path, decisions_path, dossier_vignettes)

    def _pose(self, source, cache_path, decisions_path, dossier_vignettes):
        """Installe ces chemins et l'etat qui va avec, d'un bloc.

        L'etat est construit AVANT toute affectation, les attributs n'etant
        poses qu'ensuite : construit_etat commence par probe(), qui leve sur
        une source illisible, et le porteur doit alors rester exactement ou
        il etait plutot qu'a moitie bascule -- un cache_path neuf sur une
        source ancienne serait pire que le refus lui-meme.

        Le SEUL appel a construit_etat de la classe, et il lit les reglages
        sur les attributs publics. C'etait un tuple _args fige a la
        construction, qui dupliquait seuils / tolerance_bord / seuil_masque
        avec les attributs du meme nom, les deux nourrissant des
        consommateurs DIFFERENTS -- le rendu lit les attributs (voir
        _travail_rendu), le descripteur lisait le tuple. Rebinder un attribut
        faisait alors inscrire au descripteur des reglages que le rendu
        n'avait pas appliques : le bandeau aurait dit « faite » d'un rendu a
        refaire, le faux negatif que ce chantier interdit. Une seule
        orthographe, plus de desalignement possible.

        """
        etat = _etat_vide() if source is None else construit_etat(
            source, cache_path, decisions_path, dossier_vignettes,
            self.seuils, self.tolerance_bord, self.seuil_masque, self.cadrage,
            self.couleur)
        # Une DONNEE, pas un verdict : le fichier de decisions demande en
        # ligne de commande, que _tri_orpheline compare au chemin derive a
        # chaque requete. Le calcul n'est deliberement pas fige ici, voir
        # _tri_orpheline.
        etat["decisions_ligne_de_commande"] = self._decisions_ligne_de_commande
        #: Publics : les taches en ont besoin, et _pose les relit.
        self.source = source
        self.cache_path = cache_path
        self.decisions_path = decisions_path
        self.dossier_vignettes = dossier_vignettes
        self.etat = etat

    def recharge(self):
        # Delegue plutot que de reappeler construit_etat : une seule
        # orthographe du montage de l'etat, avertissement compris.
        self._pose(self.source, self.cache_path, self.decisions_path,
                   self.dossier_vignettes)

    def regle_couleur(self, actif, fenetre, amplitude):
        """Installe ces reglages de stabilisation de balance et recharge.

        Les valeurs arrivent deja validees (voir la route /api/couleur) et
        COMPLETES : un reglage partiel n'existe pas, la page envoie toujours
        les trois. Le rechargement reconstruit les reglages du descripteur,
        donc la peremption du rendu (« a refaire ») suit toute seule.
        """
        self.couleur = {"actif": bool(actif), "fenetre": int(fenetre),
                        "amplitude": float(amplitude)}
        self.recharge()

    def change_source(self, chemin):
        """Bascule sur cette video. Leve si elle n'est pas lisible.

        Le cache, les decisions et les vignettes sont DERIVES de la source
        (voir chemins_derives) et non repris de la ligne de commande : deux
        sources choisies dans la page partageraient sinon le meme cache et
        le meme tri.

        La validation est celle de construit_etat, qui commence par probe() :
        un fichier absent leve FileNotFoundError, un fichier qui n'est pas
        une video lisible leve ValueError, et dans les deux cas _pose n'a
        rien affecte. Pas de probe() supplementaire ici : il coute 275 ms et
        dirait exactement la meme chose une seconde fois.

        Rien n'est efface : le cache et les decisions de la source
        precedente restent ou ils sont, et y revenir les retrouve.
        """
        derives = chemins_derives(chemin)
        self._pose(chemin, derives["cache_path"], derives["decisions_path"],
                   derives["dossier_vignettes"])
        # Au terminal EN PLUS du corps de /api/frames : la bascule peut
        # orpheliner le tri de la ligne de commande, et l'operateur qui a
        # lance « viewer A.mp4 » regarde ce terminal-la. Ici et non dans
        # _pose, qui est aussi appelee a chaque recharge -- ce serait un
        # message par fin de tache.
        avertissement = _tri_orpheline(self.etat)
        if avertissement:
            print(f"ATTENTION : {_texte_avertissement(avertissement)}")


def _corps_frames(etat):
    if etat["source"] is None:
        # Le viewer ouvert sur rien. Sans ce court-circuit la route rendrait
        # une trace au lieu d'une reponse : charger() et _sortie_rendu
        # levent tous deux un TypeError sur un chemin None
        # (os.path.isfile(None), os.path.splitext(None)). Les etapes sont
        # celles de l'etat vide -- aucune n'est atteignable tant qu'aucune
        # video n'est choisie -- et c'est cette forme-la que la page recoit
        # au tout premier chargement.
        return {"source": None, "pret": False, "manque": etat["manque"],
                "nb_frames_estime": etat["nb_frames_estime"],
                "etapes": etat["etapes"], "divergentes": []}
    # Lu AVANT le retour anticipe : les deux formes de la reponse portent
    # desormais les etats d'etape, et la peremption du rendu se lit dans ce
    # meme fichier. Une seule lecture pour les deux usages, aussi parce que
    # deux lectures encadrant un POST /api/decision concurrent feraient
    # afficher une grille et un bandeau en desaccord.
    ecarts = charger(etat["decisions_path"], etat["signature"])
    # Un seul avertissement pour les DEUX formes de la reponse, et calcule
    # avant elles. Une source qu'on vient de choisir dans la page n'est
    # justement PAS prete -- pas de cache, pas de vignettes -- et c'est
    # precisement la que le tri orphelin doit se dire ; le reserver a la
    # forme prete le rendait inatteignable au moment ou il compte.
    # LES DEUX ORIGINES SE CUMULENT plutot que de se choisir. Elles ne sont
    # plus exclusives depuis que le tri orphelin ne s'eteint plus sur la
    # simple existence du fichier derive (voir _tri_orpheline) : un fichier
    # derive present mais REFUSE (schema perime, autre source) fait parler
    # les deux, et un « or » ferait taire le second sans que rien ne le dise.
    # UNE LISTE DE FAITS, et non une phrase assemblee ici : ce module ne sait
    # pas dans quelle langue la page s'affiche, c'est elle qui compose (voir
    # texteDuFait, viewer.html).
    avertissement = [f for f in (
        _tri_orpheline(etat),
        diagnostique(etat["decisions_path"], etat["signature"])) if f] or None
    # UN SEUL appel pour les deux formes de la reponse, comme charger()
    # au-dessus : il porte l'unique lecture du descripteur (voir
    # _etapes_courantes), et l'appeler deux fois la doublerait.
    etapes_, divergentes = _etapes_courantes(etat, ecarts)
    if not etat["pret"]:
        # Le bandeau s'affiche AVANT que quoi que ce soit existe : c'est
        # precisement son role de guide, et c'est cette forme-la de la
        # reponse que voit un premier lancement.
        # La couleur sur cette forme AUSSI : l'etape rendu peut etre
        # disponible sans que la source soit prete (cache d'analyse sans
        # vignettes), et la page seme alors ses controles depuis ici.
        corps = {"source": etat["source"], "pret": False,
                 "manque": etat["manque"],
                 "nb_frames_estime": etat["nb_frames_estime"],
                 "etapes": etapes_, "divergentes": list(divergentes),
                 "couleur": etat["reglages"]["couleur"]}
        if avertissement:
            corps["avertissement"] = avertissement
        return corps
    frames = []
    for i, f in enumerate(etat["frames"]):
        frames.append({
            "n": i,
            "verdict": etat["verdicts"][i],
            "ecart_utilisateur": ecarts.get(i),
            "conf": f["conf"],
            "disk_p90": f["disk_p90"],
        })
    # LES NUMEROS A PART, et non un drapeau par frame : la page n'a alors
    # qu'un Set a remplacer quand une decision change l'ecart (voir
    # rafraichitEtapes), sans toucher a etat.frames, dont elle garde
    # l'exemplaire charge. Et sur la sequence reelle c'est 228 entiers contre
    # 2556 booleens.
    corps = {"source": etat["source"], "pret": True, "fps": etat["fps"],
             "frames": frames, "etapes": etapes_,
             "divergentes": list(divergentes),
             "couleur": etat["reglages"]["couleur"]}
    # Surface au client un fichier de decisions present mais refuse (schema
    # perime, source differente...) : sans ca l'utilisateur croit reviser
    # avec ses decisions passees alors qu'elles ont ete silencieusement
    # ignorees (voir decisions.diagnostique). Ou, exclusivement, le tri
    # laisse derriere par un changement de source.
    if avertissement:
        corps["avertissement"] = avertissement
    return corps


def _texte_avertissement(fait):
    """La phrase francaise d'un fait d'avertissement, pour le terminal.

    Miroir de texteDuFait (viewer.html) cote ligne de commande : un fait
    "tri_orphelin" porte parfois un second fait, le conseil de renommage, que
    la page comme le terminal composent tous deux -- jamais _tri_orpheline
    elle-meme, qui ne sait pas dans quelle langue parler.
    """
    texte = langues.rend_fr(fait)
    if fait["code"] == "tri_orphelin" and fait.get("reprise_possible"):
        texte += " " + langues.rend_fr({**fait, "code": "tri_orphelin_reprise"})
    return texte


def _sortie_rendu(source):
    """La sortie par defaut : <source sans extension>-clean.mp4.

    Retire l'extension, contrairement a chemins_derives qui la conserve :
    eclipse.mov et eclipse.mp4 visent donc le meme -clean.mp4. Asymetrie
    assumee, voir chemins_derives -- un rendu se refait, un tri manuel non,
    et l'ecrasement passe ici par le consentement du drapeau `ecraser`.
    """
    from .pipeline import _chemin_canonique

    sortie = os.path.splitext(source)[0] + "-clean.mp4"
    # La contrainte du projet, transformee en assertion : la source ne doit
    # jamais etre ecrasee. Une extension inattendue ne doit pas suffire a
    # faire coincider les deux chemins. Comparaison canonique (casse
    # normalisee, liens resolus) et non brute : sous Windows, 'SOURCE.MP4'
    # et 'source.mp4' designent le meme fichier.
    if _chemin_canonique(sortie) == _chemin_canonique(source):
        raise ValueError("le chemin de sortie coincide avec la source")
    return sortie


def _dossier_png(source):
    """Le dossier de la sequence PNG : <source sans extension>-frames.

    Retire l'extension, contrairement a chemins_derives qui la conserve :
    eclipse.mov et eclipse.mp4 visent donc le meme dossier. Asymetrie
    assumee et non oubli, voir chemins_derives -- un export PNG se refait,
    un tri manuel non, et l'ecrasement passe ici par le consentement du
    drapeau `ecraser`.
    """
    return os.path.splitext(source)[0] + "-frames"


def _tri_orpheline(etat):
    """L'avertissement du tri que la bascule de source cesse de lire, ou None.

    Le trou qu'elle bouche : « viewer A.mp4 » ecrit les revues dans le
    fichier de la LIGNE DE COMMANDE (./decisions.json par defaut). Basculer
    vers B.mp4 puis revenir a A.mp4 depuis la page fait lire
    A.mp4-decisions.json, qui n'existe pas. charger() rend {} et, le fichier
    etant ABSENT, diagnostique() rend None : aucun avertissement n'atteignait
    la page, rien n'etait imprime, et la revue de A disparaissait de
    l'interface sans un mot.

    RECALCULE A CHAQUE REQUETE, comme _etapes_courantes et pour exactement la
    meme raison : POST /api/decision n'appelle pas porteur.recharge() -- il ne
    le peut pas -- et un avertissement fige a la bascule resterait affiche
    APRES une decision, sans plus rien decrire.

    IL S'ETEINT SUR « CE TRI EST DEJA REFLETE », ET NON SUR « LE FICHIER
    DERIVE EXISTE ». Le predicat precedent -- os.path.isfile(derive) --
    s'eteignait au PREMIER appui sur k : le fichier derive naissait, et le
    seul indice de l'existence du tri de la ligne de commande s'eteignait
    pour de bon, alors que ces decisions-la n'etaient toujours pas reprises.
    Cliquer avant de lire est l'ordre normal des choses ; l'avertissement ne
    doit pas dependre de la patience de l'operateur. Il ne devient pas pour
    autant un bandeau fige : des que le fichier derive contient les memes
    decisions, il n'y a plus rien a dire et il se tait.

    La condition est etroite a dessein. On exige que le fichier de la ligne
    de commande appartienne bel et bien a CETTE source (diagnostique le dit,
    signature comprise) : sans cela, basculer vers B.mp4 avertirait a propos
    du tri de A, qui n'a jamais concerne B.

    Le cout est de deux os.path.isfile, et de deux petites lectures JSON de
    plus dans le seul cas ou les premiers ne tranchent pas -- un cas rare,
    sur une route demandee au chargement de la page et a la fin d'une tache.
    Sur une source ouverte en ligne de commande, le chemin derive EST celui de
    la ligne de commande : les deux fichiers sont alors le meme, le tri est
    trivialement reflete, et la fonction rend None.
    """
    cli = etat.get("decisions_ligne_de_commande")
    derive, signature = etat["decisions_path"], etat["signature"]
    # signature None : l'etat vide, ou derive est None lui aussi et
    # os.path.isfile leverait un TypeError.
    if not cli or not signature or not derive:
        return None
    if not os.path.isfile(cli):
        return None
    if diagnostique(cli, signature) is not None:
        return None                  # ce tri n'est pas celui de cette source
    tri_cli = charger(cli, signature)
    tri_derive = charger(derive, signature)
    # Les decisions de la ligne de commande que ce fichier-ci ne porte pas,
    # a l'identique : une frame que le fichier derive conserve la ou l'autre
    # l'ecarte n'est pas « reprise », elle est contredite.
    absentes = [n for n, statut in tri_cli.items()
                if tri_derive.get(n) != statut]
    if not absentes:
        return None
    # UN FAIT -- un code et ses valeurs -- et non une phrase : ce module ne
    # sait pas dans quelle langue la page s'affiche, et ne doit pas
    # l'apprendre. reprise_possible porte la condition ci-dessous ; c'est la
    # page (ou _texte_avertissement, pour la ligne de commande) qui decide
    # d'afficher ou non le conseil de renommage.
    # Le conseil de renommage n'est donne QUE tant qu'il ne detruit rien : des
    # que le fichier derive existe, il porte des decisions prises depuis, et
    # le renommage les ecraserait -- exactement la perte que ce projet vient
    # de subir.
    return {"code": "tri_orphelin", "fichier_cli": cli,
            "fichier_derive": derive, "n": len(absentes),
            "reprise_possible": not os.path.isfile(derive)}


def _etapes_courantes(etat, ecarts):
    """(etats des trois etapes, frames divergentes), a chaque requete.

    Recalcule ici, et NON fige dans construit_etat : POST /api/decision
    n'appelle pas porteur.recharge() — il ne le peut pas, recharge() relance
    probe() et analyse_verdicts sur toutes les frames a chaque clic — et un
    bandeau fige resterait « rendu : faite » apres la decision qui vient
    justement de perimer le rendu. C'est exactement le signal pour lequel ce
    bandeau existe.

    Le cout est un listdir, un os.path.isfile et un petit JSON. /api/frames
    n'est demandee qu'au chargement de la page et a la fin d'une tache ;
    celle qui est sondee en boucle est /api/tache, qui ne passe pas par ici.

    « en_cours » n'apparait pas : il vient du moteur de taches, et la page le
    superpose (voir etapes.etats).

    Les divergences sont le SECOND produit de la meme lecture : descripteur.
    compare rend la peremption et la liste des frames dont le statut a change
    depuis le rendu, en un seul acces au fichier que perime() lisait deja.
    Cout disque inchange.
    """
    sortie = _sortie_rendu(etat["source"])
    donnees = etat["cache"]
    faits_ = faits(etat["dossier_vignettes"], sortie, etat["signature"],
                   donnees)
    perime_, divergentes = compare(sortie, ecarts, _signature_analyse(donnees),
                                   etat["reglages"])
    # Aucune marque sans rendu sur le disque, pour la meme raison qui
    # empeche etapes.etats de transformer une absence en peremption : un
    # descripteur orphelin -- un rendu efface a la main, son .json reste --
    # ferait sinon peindre des « ecarts avec le rendu » sur une timeline
    # dont le rendu n'existe plus.
    if not faits_["rendu"]:
        divergentes = ()
    return etats(faits_, perime_), divergentes


def _travail_vignettes(porteur, moteur):
    etat = porteur.etat
    dossier = etat["dossier_vignettes"]

    def travail():
        # Le marqueur part AVANT genere(), sinon la tache est un cul-de-sac :
        # construit_etat met "vignettes" dans manque des que leur nombre
        # differe de celui du cache, marqueur a jour ou non (des .jpg effaces
        # a la main, une extraction tuee apres l'ecriture du marqueur), tandis
        # que genere() rend la main immediatement quand le marqueur est a
        # jour. La tache finissait alors "terminee" sans rien avoir fait, le
        # rechargement reproduisait exactement le meme etat, et l'utilisateur
        # pouvait cliquer indefiniment sans aucun diagnostic nulle part.
        # Ici, dans le fil de la tache et non au moment du _prepare : un 409
        # ne doit rien changer sur le disque.
        # Les vignettes ne sont pas un artefact a proteger — elles se
        # refabriquent depuis la source en une quinzaine de secondes — et un
        # marqueur retire sur une extraction qui echoue laisse un etat
        # honnete : « vignettes a refaire », relancable par le meme bouton.
        try:
            os.remove(_marqueur(dossier))
        except OSError:
            pass                  # absent, ou dossier absent : rien a forcer
        try:
            genere(etat["source"], dossier, etat["signature"],
                   arret=moteur.arret)
        except Interrompue as exc:
            # genere() ne connait pas le moteur et leve son exception a elle ;
            # sans cette traduction, une annulation demandee par
            # l'utilisateur serait rapportee comme un echec, message
            # d'exception a l'appui.
            raise TacheAnnulee() from exc

    # Pas de rappel de progression possible : genere() delegue a un unique
    # processus ffmpeg. On compte les fichiers ecrits.
    return travail, etat["nb_frames_estime"], lambda: compte(dossier)


def _reglages_reanalyse(source, cache_path):
    """La resolution d'analyse a reprendre d'un cache valide, s'il y en a un.

    Sans cela, « Refaire l'analyse » peut RETOURNER LE SENS des decisions
    manuelles deja prises, en silence. Le chemin est le suivant :

    - analyze() prend scale=0.5 par defaut, et _travail_analyse ne lui passait
      rien ; un cache produit par `analyze --scale 0.25` etait donc remesure
      a 0,5.
    - toutes les mesures dont analyse_verdicts derive ses verdicts (disk_p90,
      limb_sharpness, flare_ratio, conf, masse_captee, radius) sont prises A
      L'ECHELLE D'ANALYSE : les remesurer ailleurs deplace les verdicts.
    - or POST /api/decision n'enregistre un ecart que lorsqu'il DESACCORDE
      avec le verdict automatique (voir do_POST). Une frame dont le verdict
      bascule voit donc son ecart stocke vouloir dire l'INVERSE de ce que
      l'utilisateur avait enregistre.
    - et rien ne l'arrete : charger_cache valide le schema et la signature de
      la source, pas la resolution, et _signature_source ne couvre que
      chemin / taille / mtime — le fichier de decisions reste donc accepte.

    Reprendre plutot qu'avertir : c'est ce que la branche `run` de la ligne de
    commande documente deja (« --scale/--radius sont ignores, la resolution
    d'analyse du cache est conservee »), et cela rend la relance sure au lieu
    de la rendre bruyante. Sans cache valide, rien n'est repris : les defauts
    d'analyze s'appliquent.

    ATTENTION au rayon, qui ne se transmet pas tel quel. Le cache stocke
    "radius" A L'ECHELLE D'ANALYSE (analyze y ecrit le r qu'elle a calcule,
    deja ramene a lw x lh), tandis que le PARAMETRE radius d'analyze attend
    une pleine resolution qu'elle multipliera a son tour par lw/width. Le
    renvoyer brut le rapetisserait une seconde fois : verifie, un rayon de 50
    px a l'echelle 0,25 revient a 12,5. On le ramene donc en pleine resolution
    ici, et les deux conversions se compensent exactement puisque le meme
    scale sur la meme source redonne le meme lw.

    Pourquoi reprendre le rayon et pas seulement l'echelle : a scale egal,
    estimate_radius est deterministe et redonnerait la meme valeur — sauf si
    le cache a ete produit avec un --radius EXPLICITE, que le cache
    n'enregistre pas comme tel. Le reprendre couvre ce cas aussi.
    """
    from .pipeline import charger_cache

    donnees = charger_cache(cache_path, source)
    if donnees is None:
        return {}
    reglages = {}
    scale = donnees.get("scale")
    if scale is not None:
        reglages["scale"] = scale
    rayon = donnees.get("radius")
    largeur_analyse = donnees.get("width")
    if rayon is not None and largeur_analyse:
        reglages["radius"] = rayon * probe(source)["width"] / largeur_analyse
    return reglages


def _travail_analyse(porteur, moteur):
    from .pipeline import analyze

    etat = porteur.etat
    # Saisi ICI, avec l'etat, et non relu dans le fil de la tache : depuis
    # POST /api/source, porteur.cache_path peut CHANGER en cours de route.
    # Relu plus tard, il ferait analyser l'ancienne source dans le cache de
    # la nouvelle. La route refuse deja de changer de source pendant une
    # tache ; cette saisie ferme la fenetre qui reste entre son controle et
    # la bascule.
    cache_path = porteur.cache_path
    # Lu hors du fil de la tache : c'est une lecture, elle ne touche a rien
    # sur le disque, et un 409 Occupe n'a donc rien a annuler ici.
    reglages = _reglages_reanalyse(etat["source"], cache_path)

    def travail():
        # Le total reste celui de l'estimation : analyze() n'en connait pas
        # (elle ne sait combien de frames qu'a la fin de sa boucle) et ne
        # passe qu'un compte fait.
        analyze(etat["source"], cache_path, **reglages,
                progression=lambda fait, total=None: moteur.progression(fait))

    return travail, etat["nb_frames_estime"], None


def _sortie_partielle(sortie):
    """Le fichier ou le rendu ecrit tant qu'il n'est pas complet.

    '-partiel' AVANT l'extension, et non un '.partiel' ajoute au bout :
    ffmpeg deduit le conteneur de l'extension du fichier de sortie, et sur un
    nom qu'il ne reconnait pas il n'ecrit RIEN — sans que FrameWriter.close()
    s'en apercoive, puisqu'il ne verifie pas le code de retour. Verifie sur
    cette machine : 'essai.mp4.partiel' ne produit aucun fichier,
    'essai-partiel.mp4' en produit un valide.
    """
    racine, extension = os.path.splitext(sortie)
    return racine + "-partiel" + extension


def _vide_les_png(dossier):
    """Retire les PNG d'un export precedent avant d'en ecrire un nouveau.

    PngSequenceWriter ne vide pas le dossier (voir io.py) : ffmpeg reecrit
    frame-00001.png et les suivants, mais tout numero SUPERIEUR laisse par un
    export plus long survit a cote des nouveaux. Un consommateur qui globe
    *.png obtient alors une sequence moitie d'une execution, moitie de
    l'autre, sans aucun signal — et un second export plus court est le cas
    NORMAL, puisque la page existe pour ecarter davantage de frames.

    Le vidage n'est pas atomique : si le n-ieme os.remove echoue (fichier
    verrouille, droits), les n-1 premiers sont deja partis. L'OSError remonte
    telle quelle et la tache finit "echouee", avec le nom du fichier fautif.
    Elle n'est PAS traduite en refus : annoncer 409 « requete refusee » sur
    un export deja a moitie detruit serait un mensonge.

    Un dossier absent n'est pas une erreur : c'est le cas du tout premier
    export, ou PngSequenceWriter le creera lui-meme.
    """
    if not os.path.isdir(dossier):
        return
    for nom in os.listdir(dossier):
        if nom.lower().endswith(".png"):
            os.remove(os.path.join(dossier, nom))


def _permute_dossier(neuf, cible):
    """Met `neuf` a la place de `cible`, l'ancien n'etant efface qu'ensuite.

    os.replace ne sert a rien ici : d'un dossier vers un dossier existant, il
    echoue sous Windows. On ecarte donc l'ancien, on met le neuf en place, et
    on ne supprime l'ancien qu'une fois le neuf installe — a aucun instant
    l'utilisateur n'est sans export complet. Si la mise en place echoue,
    l'ancien est remis d'ou il vient avant que l'exception ne remonte.
    """
    ancien = cible + "-ancien"
    if os.path.isdir(ancien):
        shutil.rmtree(ancien)          # reste d'une permutation interrompue
    ecarte = os.path.isdir(cible)
    if ecarte:
        os.rename(cible, ancien)
    try:
        os.rename(neuf, cible)
    except BaseException:
        if ecarte:
            os.rename(ancien, cible)
        raise
    if ecarte:
        # ignore_errors : a ce point le nouvel export est LIVRE, il ne reste
        # qu'a jeter l'ancien. Sous Windows une poignee d'apercu de
        # l'explorateur ou un antivirus font echouer ce rmtree de facon
        # plausible ; laisser l'exception remonter annoncerait « rendu :
        # echec » pour un rendu qui a reussi, et ferait relancer douze minutes
        # d'encodage. On le dit, et on garde le succes.
        shutil.rmtree(ancien, ignore_errors=True)
        if os.path.isdir(ancien):
            print(f"ATTENTION : l'export precedent n'a pas pu etre supprime "
                  f"et reste dans {ancien}")


def _verifie_dossier_png(frames_dir, source):
    """Refuse un dossier d'export qui contiendrait la source.

    --frames-dir est maintenant transmis par le viewer jusqu'au rendu, et le
    rendu lance depuis la page PERMUTE ce dossier : il l'ecarte sous
    <dossier>-ancien, met le neuf a sa place, puis supprime l'ancien. Vise sur
    le dossier qui contient la video, cette sequence detruirait la source —
    la contrainte permanente du projet, transformee en assertion, comme dans
    _sortie_rendu. En ligne de commande, render() se contente d'ecrire DANS le
    dossier et n'en supprime rien : le danger n'existe qu'ici.
    """
    from .pipeline import _chemin_canonique

    canon_dossier = _chemin_canonique(frames_dir)
    canon_source = _chemin_canonique(source)
    if (canon_source == canon_dossier
            or canon_source.startswith(canon_dossier + os.sep)):
        raise ValueError(
            f"le dossier d'export PNG ({frames_dir}) contient la source : le "
            f"rendu lance depuis la page le remplace en entier, ce qui "
            f"detruirait {source}")


def _recupere_permutation(frames_dir):
    """Remet en place un export laisse a mi-permutation par un arret brutal.

    _permute_dossier renomme d'abord <cible> en <cible>-ancien, puis le neuf
    en <cible>. Un arret entre les deux (courant coupe, processus tue) laisse
    <cible> ABSENT et l'export complet sous -ancien. Sans cette reprise, le
    controle de vacuite du lancement suivant ne voit rien a la cible, accepte
    donc la requete sans demander de consentement, et le rendu qui suit
    detruit cet export neuf : _vide_les_png d'abord, le rmtree du reste
    -ancien ensuite. C'est pour cela que la reprise est ICI, avant le controle
    de vacuite, et non dans _permute_dossier ou elle arriverait trop tard.
    """
    ancien = frames_dir + "-ancien"
    if os.path.isdir(ancien) and not os.path.isdir(frames_dir):
        try:
            os.rename(ancien, frames_dir)
        except OSError as exc:
            # Ne pas faire echouer la requete pour autant : l'export reste
            # sous son nom -ancien, et on le nomme au lieu de le taire.
            print(f"ATTENTION : l'export precedent est reste dans {ancien} "
                  f"et n'a pas pu etre remis en place ({exc})")


def _restes(chemins):
    """Ceux de ces chemins qui existent encore, a nommer dans un echec."""
    return [c for c in chemins if c and os.path.exists(c)]


def _livre(partiel, sortie, frames_partiel, frames_dir):
    """Met le rendu complet a sa place definitive, et dit ou il est.

    Appelee HORS du try qui nettoie : a partir d'ici `partiel` est un rendu
    COMPLET, et l'effacer sur un echec de mise en place detruirait des minutes
    d'encodage.

    Deux gardes, memes membres d'une meme famille de defauts — un chemin qui
    detruit un artefact termine sans en livrer de remplacement :

    - un `partiel` absent ou vide n'est PAS livre. FrameWriter.close() ne lit
      jamais le code de retour de ffmpeg (voir io.py) : une finalisation en
      erreur rend la main normalement, et os.replace deplacerait alors un
      fichier vide ou tronque par-dessus le bon rendu de l'utilisateur, la
      tache rapportant "terminee".
    - un echec de permutation nomme les artefacts de recuperation. Le message
      du moteur vaut "PermissionError: [WinError 5] Access is denied: ..." et
      ne dit nulle part que le rendu complet existe, sous -partiel : le
      lancement suivant l'ecrase, et l'utilisateur n'aura jamais su qu'il
      etait la.
    """
    if not os.path.isfile(partiel) or os.path.getsize(partiel) == 0:
        restes = _restes([frames_partiel])
        raise RuntimeError(
            f"rendu non livre : {partiel} est absent ou vide, ffmpeg n'a "
            f"donc rien encode. {sortie} n'a pas ete touche."
            + (f" Sequence PNG conservee dans : {restes[0]}" if restes else ""))
    try:
        os.replace(partiel, sortie)
        if frames_partiel is not None:
            _permute_dossier(frames_partiel, frames_dir)
    except Exception as exc:
        restes = _restes([partiel, frames_partiel])
        detail = ""
        if restes:
            # "Incomplete" et non "non livree" : os.replace a pu reussir et la
            # permutation du dossier echouer ensuite, auquel cas le mp4 est
            # bien en place et seul l'export PNG reste a cote. On ne nomme donc
            # que ce qui EXISTE encore, plutot que les deux par principe.
            detail = (" Ce qui reste a livrer est conserve sous : "
                      + ", ".join(restes)
                      + " ; le renommer a la main termine la livraison.")
        raise RuntimeError(
            f"rendu termine, livraison incomplete "
            f"({type(exc).__name__}: {exc})." + detail) from exc
    # Le seul endroit qui connaisse le chemin DEFINITIF : le "Ecrit N frames
    # dans ..." de pipeline.render nomme le fichier partiel, qui n'existe plus
    # une fois la permutation faite.
    message = f"Rendu livre : {sortie}"
    if frames_dir is not None:
        message += f" ; sequence PNG : {frames_dir}"
    print(message)


def _comptes_rendu(comptes):
    """Ce que le rendu a produit, remis en forme pour la page.

    render() rend "gardees", et ce nom trompe : c'est le TOTAL ecrit,
    interpolees comprises (voir pipeline.render, `total = ecrites +
    interpolees`). Le compte de frames reellement rendues depuis la source
    n'est donc pas dans le dictionnaire, il se deduit. C'est exactement la
    surprise que la page doit lever : l'utilisateur qui ecarte 4 frames sur
    200 trouve 200 PNG et non 196, parce que les 4 coupes sont assez courtes
    pour etre comblees par interpolation.

    On reconstruit ici un dictionnaire explicite, aux noms non ambigus et aux
    valeurs entieres : il part tel quel dans la reponse JSON de /api/tache, et
    ce qui n'est pas serialisable y ferait echouer la route, pas la tache.

    Rend None sur toute forme inattendue plutot que de lever : l'appel a lieu
    APRES _livre, donc sur un rendu deja en place chez l'utilisateur. Une
    exception ici ferait rapporter "echouee" a une tache qui a livre, et
    enverrait relancer douze minutes d'encodage pour un compte manquant. La
    page retombe alors sur son message d'avant, « rendu : termine. » — meme
    famille de defauts que les gardes de _livre : aucun chemin ne doit
    desavouer un artefact complet.
    """
    try:
        interpolees = int(comptes["interpolees"])
        total = int(comptes["gardees"])
        ecartees = int(comptes["rejetees"])
    except (TypeError, KeyError, ValueError):
        return None
    return {"total": total, "ecrites": total - interpolees,
            "interpolees": interpolees, "ecartees": ecartees}


def _travail_rendu(porteur, moteur, ecraser, png):
    from .pipeline import render

    etat = porteur.etat
    sortie = _sortie_rendu(etat["source"])
    if os.path.exists(sortie) and not ecraser:
        raise FileExistsError(sortie)
    frames_dir = frames_partiel = None
    if png:
        # Toujours le dossier DERIVE de la source, jamais un dossier choisi
        # par l'utilisateur : la livraison permute le dossier en entier, donc
        # un --frames-dir pointant sur un dossier a lui en detruirait tout le
        # contenu, PNG ou non. Le sous-parseur viewer refuse l'option (voir
        # pipeline.main) ; ce qui suit garde l'invariant a portee de lecture.
        frames_dir = _dossier_png(etat["source"])
        _verifie_dossier_png(frames_dir, etat["source"])
        # AVANT le controle de vacuite : voir _recupere_permutation.
        _recupere_permutation(frames_dir)
        # Un dossier vide ne bloque pas : c'est le cas normal d'un premier
        # export dont le dossier a ete cree a l'avance.
        if os.path.isdir(frames_dir) and os.listdir(frames_dir) and not ecraser:
            raise FileExistsError(frames_dir)
        # Meme raison que pour le mp4 : ffmpeg ecrit dans le dossier au fur
        # et a mesure. Vider l'export precedent avant d'avoir le nouveau le
        # perdrait a la premiere annulation. PngSequenceWriter cree le
        # dossier qu'on lui donne, celui-ci convient donc tel quel.
        frames_partiel = frames_dir + "-partiel"
    # On rend A COTE, puis on remplace au succes. ffmpeg ouvre sa sortie avec
    # -y et la tronque des le demarrage : ecrire directement dans `sortie`
    # detruirait le rendu precedent de l'utilisateur des la premiere frame,
    # et toute annulation ou tout echec le lui ferait perdre. Ici, plus aucun
    # chemin ne touche le fichier precedent avant que le nouveau ne soit
    # complet.
    partiel = _sortie_partielle(sortie)
    # Le cadrage demande en ligne de commande, transmis jusqu'a render() :
    # sans cela, "viewer src.mp4 --taille 900x1600" puis un clic sur Lancer le
    # rendu produit silencieusement le cadrage par defaut. Le Porteur le
    # construit une fois pour toutes, parce que la meme geometrie part aussi
    # dans le descripteur ecrit a la livraison.
    cadrage = porteur.cadrage
    # Copie saisie ICI, dans le fil HTTP, comme le cadrage : un POST
    # /api/couleur pendant l'encodage ne doit pas changer les gains d'un
    # rendu deja parti — le descripteur ecrit plus bas (etat["reglages"])
    # date du meme instant, les deux restent d'accord.
    couleur = dict(porteur.couleur)
    # Meme raison que dans _travail_analyse : saisi dans le fil HTTP, ou
    # l'etat et les chemins sont encore ceux d'une meme source.
    cache_path = porteur.cache_path

    def travail():
        # Vidage ICI, dans le fil de la tache, et non au moment du _prepare :
        # la, c'est encore le fil HTTP, AVANT que moteur.lance() ait accepte
        # la tache. Un 409 Occupe aurait donc detruit l'export precedent sans
        # rien rendre en echange. Et c'est le dossier PARTIEL qu'on vide : il
        # peut porter les restes d'un export avorte, jamais l'export livre.
        if frames_partiel is not None:
            _vide_les_png(frames_partiel)
        # Lu ICI, avant render() et non apres : c'est le tri que render va
        # appliquer. Une decision prise pendant les douze minutes d'encodage
        # ne doit pas etre inscrite au descripteur comme si le rendu en avait
        # tenu compte — ce serait le faux negatif que tout ce chantier
        # existe pour eviter. render() relit le fichier lui-meme une fraction
        # de seconde plus tard ; si une decision se glisse entre les deux, le
        # descripteur note l'ancien tri et le bandeau dira « a refaire » a
        # tort plutot qu'a jour a tort.
        ecarts_appliques = charger(etat["decisions_path"], etat["signature"])
        try:
            # Memes reglages de tri, memes decisions manuelles que ceux dont
            # la page montre les verdicts : sinon le rendu ecarte autre chose
            # que ce que l'utilisateur vient de revoir, et la revue humaine
            # part a la poubelle sans un mot (voir Porteur).
            comptes = render(etat["source"], partiel, cache_path,
                             seuils=porteur.seuils,
                             tolerance_bord=porteur.tolerance_bord,
                             seuil_masque=porteur.seuil_masque,
                             decisions_path=etat["decisions_path"],
                             frames_dir=frames_partiel,
                             couleur=couleur["actif"],
                             couleur_fenetre=couleur["fenetre"],
                             couleur_amplitude=couleur["amplitude"],
                             progression=moteur.progression, **cadrage)
        except BaseException:
            # Un mp4 tronque laisse en place serait pris pour un rendu
            # valide : la carcasse part. Le fichier de sortie, lui, n'a
            # jamais ete ouvert par ce rendu — il n'y a rien a y reparer.
            # Les PNG partiels partent avec lui : ils sont incomplets au meme
            # titre, et sur la sequence reelle ce sont 8 a 13 Go de doublon
            # qui resteraient sinon en place jusqu'au prochain lancement,
            # sans etre nommes nulle part. L'export LIVRE n'est pas concerne :
            # celui-ci est le dossier partiel. ignore_errors, parce que rien
            # ici ne doit masquer l'echec d'origine, qui est ce que
            # l'utilisateur doit lire.
            if os.path.isfile(partiel):
                os.remove(partiel)
            if frames_partiel is not None:
                shutil.rmtree(frames_partiel, ignore_errors=True)
            raise

        # Hors du try : le partiel est desormais un rendu COMPLET. Les gardes
        # de la livraison, et le nom des artefacts si elle echoue, sont dans
        # _livre.
        _livre(partiel, sortie, frames_partiel, frames_dir)

        # APRES la livraison, jamais avant, et hors du try de nettoyage : si
        # cette ecriture echoue, le rendu est en place sans descripteur a
        # jour et le bandeau dira « a refaire ». Un faux positif, jamais un
        # faux negatif — c'est l'invariant du descripteur.
        #
        # Et l'echec ne fait pas echouer la tache : meme regle que
        # _comptes_rendu et que les gardes de _livre, aucun chemin ne doit
        # desavouer un artefact complet ni faire relancer douze minutes
        # d'encodage. Exception large et non OSError seule : un descripteur
        # non serialisable leverait TypeError, et le rendu, lui, est livre.
        # BaseException est exclue, l'annulation doit rester une annulation.
        try:
            ecrit(sortie, ecarts_appliques,
                  _signature_analyse(etat["cache"]), etat["reglages"])
        except Exception as exc:                # noqa: BLE001
            print(f"ATTENTION : descripteur non ecrit "
                  f"({type(exc).__name__}: {exc}) ; le rendu sera signale a "
                  f"refaire", file=sys.stderr)
        return _comptes_rendu(comptes)

    # Total None au lancement : il est fixe par le premier appel au rappel,
    # qui passe len(gardes) — le seul compte exact, connu de render() seule.
    return travail, None, None


def _prepare(porteur, moteur, genre, ecraser, png=False):
    """Rend (travail, total, compteur) pour le genre demande.

    Leve FileExistsError si la sortie du rendu existe deja et qu'ecraser est
    faux. png n'a de sens que pour le rendu : il exporte en plus la sequence
    d'images. Ce n'est pas un genre a part, parce que c'est le meme
    traitement et la meme barre.
    """
    if porteur.etat["source"] is None:
        # Aucune source choisie : les trois taches leveraient plus loin, et
        # deux d'entre elles DANS le fil de la tache, ou l'echec ne serait
        # qu'un message d'erreur exotique. Le ValueError est rattrape par
        # _lance_tache, qui rend 400 : la requete n'a pas de sens dans cet
        # etat, ce n'est pas une panne.
        raise ValueError("aucune source choisie")
    if genre == "vignettes":
        return _travail_vignettes(porteur, moteur)
    if genre == "analyse":
        return _travail_analyse(porteur, moteur)
    if genre == "rendu":
        return _travail_rendu(porteur, moteur, ecraser, png)
    # Explicite plutot qu'un dernier genre par defaut : un genre errant ne
    # doit pas pouvoir declencher un rendu, la seule tache qui ecrit un
    # fichier de sortie.
    raise ValueError(f"genre de tache inconnu : {genre!r}")


def fabrique_handler(porteur, moteur):
    """Construit la classe de handler liee a ce porteur et a ce moteur.

    Le verrou est porte par la fermeture (une instance par serveur, donc par
    porteur) et non par le module : le serveur est maintenant threade (voir
    sert), et charger()/enregistrer() forment une lecture-modification-
    ecriture sur le meme fichier qui doit rester atomique face a deux
    requetes POST concurrentes.
    """
    verrou = threading.Lock()
    # UNE SEULE BOITE A LA FOIS. Deux onglets de la meme page peuvent
    # cliquer sur « Parcourir... », et deux fenetres modales sur le meme
    # bureau seraient deroutantes : la seconde requete repart en 409 plutot
    # que d'en ouvrir une autre. Porte par la fermeture, donc par serveur,
    # comme le verrou ci-dessus : c'est exactement la portee du danger, un
    # viewer et ses onglets. Deux viewers lances a la main sont deux
    # processus que rien ici ne coordonne, et ce n'est pas ce qu'on protege.
    verrou_dialogue = threading.Lock()

    def _origine_acceptable(entetes, port):
        """Vrai si la requete ne vient pas d'une page tierce.

        S'applique aux routes qui AGISSENT : les POST et le DELETE. Les GET
        servent la page, l'etat et les vignettes -- ils ne font que lire, et
        la politique d'origine empeche deja une page tierce d'en lire la
        reponse. C'est ce qui a fait choisir POST pour /api/parcourir, qui
        OUVRE UNE FENETRE sur le bureau de l'utilisateur : la garde lui vient
        de la methode, sans qu'un mot soit ajoute ici.

        Un GET de MEME origine ne porte pas d'en-tete Origin, et c'est
        precisement pourquoi une origine absente est acceptee -- sinon la
        page elle-meme serait refusee. curl et les tests n'en envoient pas non
        plus. La garde attrape donc les requetes inter-origine qui, elles,
        portent un Origin : fetch et XMLHttpRequest. Elle N'ATTRAPE PAS ce qui
        n'en envoie aucun. Un <img> ou un <script src> ne peuvent plus
        atteindre ces routes -- ce sont des GET, et il n'y a plus de route GET
        qui agisse depuis le retrait de /api/dossier ; il resterait a une page
        tierce un <form method=post>, dont le comportement quant a l'en-tete
        Origin n'a pas ete verifie ici. La garde est une couche, pas une
        preuve.
        """
        origine = entetes.get("Origin")
        if origine is None:
            return True
        return origine in (f"http://127.0.0.1:{port}",
                           f"http://localhost:{port}")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass                      # silence : le terminal sert au pipeline

        def handle(self):
            """Un client parti n'est pas une panne : il ne laisse pas de trace.

            LE FAIT. Le navigateur annule ses requetes de vignettes des qu'on
            traverse la pellicule plus vite qu'elles ne chargent. Le
            gestionnaire, lui, ecrit encore : le systeme lui rend alors
            ECONNABORTED (WinError 10053, celle vue dans le terminal),
            ECONNRESET (WinError 10054, ou le meme nom sur POSIX) ou EPIPE
            (POSIX). Sans cette garde, socketserver imprime une
            trace complete par requete annulee -- et le terminal est justement
            l'endroit ou l'operateur suit ses rendus et ses annulations.

            POURQUOI ICI, ET NON DANS _envoie. handle() est l'unite qui
            possede UNE connexion cliente : les deux sens y passent. Le
            raccrochage se voit a l'ecriture de la reponse (le cas observe),
            mais aussi a la LECTURE de la requete suivante d'une connexion
            gardee ouverte, que _envoie ne verrait pas. Et rien d'autre, dans
            le fil d'une requete, ne parle a une socket ou a un tuyau : ces
            trois erreurs-la ne peuvent venir que du client.

            POURQUOI PAS handle_error du serveur. Le crochet de socketserver
            ferait aussi l'affaire, mais il est porte par le SERVEUR : chaque
            montage -- sert() et chaque fixture de test -- devrait penser a
            employer une sous-classe. Ici, la garde suit le gestionnaire que
            fabrique_handler rend, donc tout ce qui sert cette page.

            CE QUI RESTE BRUYANT : tout le reste, avec sa trace entiere.
            Pas OSError en bloc -- une vignette illisible sur le disque est
            un defaut, pas un client parti. Pas ConnectionError, qui
            engloberait ConnectionRefusedError, qu'un serveur qui ACCEPTE ne
            peut pas produire. Pas TimeoutError : un client qui traine n'a
            pas raccroche.
            """
            try:
                super().handle()
            except (ConnectionAbortedError, ConnectionResetError,
                    BrokenPipeError):
                # La connexion est morte : plus rien a ecrire, plus rien a
                # lire. finish() de socketserver tolere deja de ne pas
                # pouvoir vider son tampon sur une socket fermee.
                self.close_connection = True

        def _envoie(self, code, corps, type_mime):
            self.send_response(code)
            self.send_header("Content-Type", type_mime)
            self.send_header("Content-Length", str(len(corps)))
            self.end_headers()
            self.wfile.write(corps)

        def do_GET(self):
            etat = porteur.etat        # une seule saisie par requete
            if self.path in ("/", "/index.html"):
                with open(_PAGE, "rb") as f:
                    return self._envoie(200, f.read(), "text/html; charset=utf-8")
            if self.path == "/api/langues":
                corps = json.dumps(langues.toutes()).encode("utf-8")
                return self._envoie(200, corps, "application/json")
            if self.path == "/api/version":
                # Calculee a CHAQUE appel et non figee au demarrage : le
                # viewer tourne pendant qu'on modifie le depot, et une
                # version figee dirait « propre » sur un arbre devenu sale.
                corps = json.dumps(
                    {"version": version_affichee()}).encode("utf-8")
                return self._envoie(200, corps, "application/json")
            if self.path == "/api/frames":
                corps = json.dumps(_corps_frames(etat)).encode("utf-8")
                return self._envoie(200, corps, "application/json")
            if self.path == "/api/tache":
                # etat() seul, jamais porteur.recharge() : cette route est
                # sondee en boucle par la page, et recharge() relance probe()
                # (un processus ffmpeg) et analyse_verdicts sur toutes les
                # frames. Le rechargement est le rappel de fin de tache.
                corps = json.dumps(moteur.etat()).encode("utf-8")
                return self._envoie(200, corps, "application/json")
            if self.path.startswith("/thumb/"):
                nom = self.path[len("/thumb/"):]
                try:
                    n = int(nom.split(".")[0])
                except ValueError:
                    return self._envoie(404, b"", "text/plain")
                dossier = etat["dossier_vignettes"]
                if dossier is None:
                    # Pas encore de source : rien a servir, et
                    # chemin_vignette leverait sur un dossier None.
                    return self._envoie(404, b"", "text/plain")
                chemin = chemin_vignette(dossier, n)
                if not os.path.isfile(chemin):
                    return self._envoie(404, b"", "text/plain")
                with open(chemin, "rb") as f:
                    return self._envoie(200, f.read(), "image/jpeg")
            return self._envoie(404, b"", "text/plain")

        def _lit_corps_json(self):
            """L'objet JSON du corps, ou None s'il est inexploitable.

            Partage par les quatre routes POST : les trois qui exigeaient
            deja un corps, et /api/parcourir, dont le corps ne porte que la
            langue et reste facultatif -- une absence de corps se lit ici
            comme Content-Length absent, donc une lecture de 0 octet, que
            json.loads refuse et que cette methode rend comme None, exactement
            le meme chemin qu'un corps invalide. Le plafond de taille evite
            qu'un client (ou un script errant) gonfle la memoire du serveur
            avec un corps arbitrairement grand.
            """
            brut = self.headers.get("Content-Length")
            try:
                taille = int(brut) if brut is not None else 0
            except ValueError:
                # Content-Length present mais non numerique : un client mal
                # forme, pas une exception a laisser remonter.
                return None
            if not 0 <= taille <= TAILLE_CORPS_MAX:
                return None
            try:
                objet = json.loads(self.rfile.read(taille))
            except (ValueError, json.JSONDecodeError):
                return None
            # Une liste ou un nombre franchiraient la lecture JSON, mais
            # aucune des deux routes n'en ferait quoi que ce soit.
            return objet if isinstance(objet, dict) else None

        def _lance_tache(self, requete):
            genre = requete.get("genre")
            if genre not in GENRES:
                return self._envoie(400, b"", "text/plain")
            png = bool(requete.get("png"))
            try:
                travail, total, compteur = _prepare(
                    porteur, moteur, genre, bool(requete.get("ecraser")),
                    png=png)
            except FileExistsError:
                # La sortie existe deja : refus, pas ecrasement silencieux.
                return self._envoie(409, b"", "text/plain")
            except ValueError:
                # Chemin de sortie coincidant avec la source, genre errant :
                # une requete invalide, pas une panne. Sans ce rattrapage
                # l'exception s'echappe de do_POST, socketserver imprime une
                # trace et le client n'obtient aucun statut.
                return self._envoie(400, b"", "text/plain")
            try:
                # apres=porteur.recharge : l'etat est reconstruit une fois,
                # a la fin de la tache, jamais a chaque sondage.
                # options : ce qui a ete demande, reporte dans l'instantane.
                # C'est le seul etat partage entre onglets, donc le seul
                # endroit ou un onglet ouvert PENDANT le rendu peut retrouver
                # la case « exporter aussi la sequence PNG » telle qu'elle a
                # ete cochee dans l'onglet qui a lance (voir viewer.html).
                ident = moteur.lance(genre, travail, total=total,
                                     compteur=compteur, apres=porteur.recharge,
                                     options={"png": png} if genre == "rendu"
                                     else None)
            except Occupe:
                return self._envoie(409, b"", "text/plain")
            corps = json.dumps({"id": ident}).encode("utf-8")
            return self._envoie(202, corps, "application/json")

        def _parcourir(self, etat, requete):
            """Ouvre la boite de dialogue du systeme et rend le chemin choisi.

            POST et non GET parce qu'elle AGIT : elle ouvre une fenetre sur le
            bureau de l'utilisateur. Appelee depuis do_POST, donc APRES la
            garde d'origine, qu'elle herite sans la reimplementer.

            requete : le corps JSON, ou {} -- do_POST le rend ainsi des qu'il
            est absent ou inexploitable. Sa cle "langue" est la SEULE voie
            par laquelle le titre et les filtres de la boite, affiches dans
            une fenetre du systeme et jamais dans le DOM, peuvent connaitre
            la langue choisie sur la page : "fr" sert de repli, ici comme
            dans choisit_video, qu'une valeur absente, non textuelle ou
            inconnue.

            LE FIL DE LA REQUETE RESTE BLOQUE tant que la boite est ouverte,
            et c'est voulu : c'est un ThreadingHTTPServer (voir sert), les
            autres requetes continuent d'etre servies, et la page dit qu'elle
            attend plutot que de paraitre figee.

            Rien ici n'ouvre le fichier : la boite ne fait que le NOMMER. La
            source ne devient la source qu'au POST /api/source suivant, qui
            la sonde en lecture seule.
            """
            langue = requete.get("langue")
            if not isinstance(langue, str):
                langue = "fr"
            # non bloquant : une seconde demande doit repartir tout de suite
            # avec un refus, pas attendre que la premiere boite se ferme.
            if not verrou_dialogue.acquire(blocking=False):
                return self._envoie(409, b"", "text/plain")
            try:
                # La ou est la source courante, faute de quoi le systeme
                # choisit : c'est le dossier le plus probable de la suivante.
                source = etat["source"]
                depart = os.path.dirname(source) if source else None
                chemin = choisit_video(depart, langue=langue)
            except Indisponible as exc:
                # SANS EXPLORATEUR WEB, il ne reste plus aucun moyen de
                # choisir une source depuis la page : ce refus doit dire quoi
                # faire, pas seulement constater. Muet serait inacceptable.
                # UN FAIT, pas une phrase : le detail (str(exc)) est du texte
                # de diagnostic libre, non traduit -- voir dialogue.py -- et
                # la cle "boite_indisponible" porte le conseil (relancer avec
                # la source en argument), compose par la page dans sa langue.
                corps = json.dumps({"code": "boite_indisponible",
                                    "detail": str(exc)})
                return self._envoie(503, corps.encode("utf-8"),
                                    "application/json")
            finally:
                verrou_dialogue.release()
            # chemin vaut None a l'annulation, et c'est une reponse normale :
            # 200 avec un chemin nul, pas une erreur.
            corps = json.dumps({"chemin": chemin}).encode("utf-8")
            return self._envoie(200, corps, "application/json")

        def _regle_couleur(self, requete):
            """Installe les reglages de stabilisation de balance du porteur.

            Les TROIS champs sont exiges, aux types JSON stricts : la page
            les a toujours tous, et accepter un reglage partiel laisserait
            deux onglets se composer un etat que ni l'un ni l'autre n'a
            demande. Un rendu en cours n'est pas un refus : il a saisi sa
            copie des reglages au lancement (voir _travail_rendu), et le
            changement ne vaut que pour le suivant — c'est exactement ce que
            le bandeau « a refaire » dira.
            """
            actif = requete.get("actif")
            fenetre = requete.get("fenetre")
            amplitude = requete.get("amplitude")
            # bool est un int en Python : l'exclure explicitement, sans quoi
            # fenetre=true passerait pour la fenetre 1.
            if not isinstance(actif, bool):
                return self._envoie(400, b"", "text/plain")
            if (isinstance(fenetre, bool) or not isinstance(fenetre, int)
                    or not 1 <= fenetre <= 9999):
                return self._envoie(400, b"", "text/plain")
            if (isinstance(amplitude, bool)
                    or not isinstance(amplitude, (int, float))
                    or not 0.0 <= amplitude <= 1.0):
                return self._envoie(400, b"", "text/plain")
            porteur.regle_couleur(actif, fenetre, amplitude)
            corps = json.dumps(porteur.couleur).encode("utf-8")
            return self._envoie(200, corps, "application/json")

        def _change_source(self, requete):
            """Bascule le porteur sur la video demandee.

            Appelee depuis do_POST, donc APRES la garde d'origine : cette
            route agit, et une page tierce ne doit pas pouvoir faire changer
            de source le viewer ouvert dans le navigateur de l'utilisateur.
            """
            chemin = requete.get("chemin")
            if not isinstance(chemin, str):
                return self._envoie(400, b"", "text/plain")
            # Changer de source sous une tache qui tourne lui ferait ecrire
            # ses resultats a cote de l'ancienne video, ou pire, melanger les
            # deux. Les taches deja parties, elles, gardent les chemins
            # qu'elles ont saisis au lancement (voir _travail_analyse).
            if moteur.etat()["etat"] == "en_cours":
                return self._envoie(409, b"", "text/plain")
            try:
                porteur.change_source(chemin)
            except (OSError, ValueError):
                # probe() leve FileNotFoundError si le fichier manque,
                # ValueError s'il n'est pas une video que ffmpeg sait lire --
                # le cas d'un fichier a l'extension trompeuse, ou d'un fichier
                # pris via « Tous les fichiers » dans la boite de dialogue,
                # qui filtre par extension et ne sonde rien.
                return self._envoie(400, b"", "text/plain")
            return self._envoie(200, b'{"ok":true}', "application/json")

        def do_DELETE(self):
            if not _origine_acceptable(self.headers, self.server.server_port):
                return self._envoie(403, b"", "text/plain")
            if self.path != "/api/tache":
                return self._envoie(404, b"", "text/plain")
            # Rien n'est tue : le drapeau est leve, le rappel de progression
            # de la tache en cours fera le reste (voir taches.Moteur).
            #
            # Et on le dit au terminal : c'est la que l'operateur regarde
            # tourner la passe, et un rendu qui s'arrete sans un mot cote
            # terminal ressemble a une panne. Meme forme que l'annulation par
            # Ctrl+C, plus bas dans sert(). La fin de la tache, elle, est
            # annoncee par le moteur (voir taches.Moteur._acheve).
            instantane = moteur.etat()
            if instantane["etat"] == "en_cours":
                print(f"Annulation de la tache {instantane['genre']} "
                      f"demandee depuis la page...")
            else:
                print("Annulation demandee depuis la page : aucune tache "
                      "en cours.")
            moteur.annule()
            return self._envoie(202, b'{"ok":true}', "application/json")

        def do_POST(self):
            if not _origine_acceptable(self.headers, self.server.server_port):
                return self._envoie(403, b"", "text/plain")
            etat = porteur.etat        # une seule saisie par requete
            # A PART, avant le if des trois autres routes : le corps de
            # /api/parcourir est FACULTATIF (il ne porte que la langue), et
            # une absence de corps ou un JSON invalide y valent {} -- donc
            # "fr" plus loin -- alors que les trois autres routes rendent 400
            # dans ce cas. _lit_corps_json rend deja None pour un corps
            # absent (Content-Length nul, lu comme 0 octet puis refuse par
            # json.loads) : c'est le meme chemin qu'un JSON invalide, `or {}`
            # couvre les deux sans les distinguer.
            if self.path == "/api/parcourir":
                return self._parcourir(etat, self._lit_corps_json() or {})
            if self.path not in ("/api/decision", "/api/source",
                                 "/api/tache", "/api/couleur"):
                return self._envoie(404, b"", "text/plain")
            # Une seule lecture du corps pour les quatre routes, et non une
            # par branche : c'est elle qui porte le plafond de taille, et le
            # flux ne se lit qu'une fois.
            requete = self._lit_corps_json()
            if requete is None:
                return self._envoie(400, b"", "text/plain")
            if self.path == "/api/source":
                return self._change_source(requete)
            if self.path == "/api/tache":
                return self._lance_tache(requete)
            if self.path == "/api/couleur":
                return self._regle_couleur(requete)
            try:
                n = int(requete["n"])
                statut = requete["statut"]
            except (ValueError, KeyError, TypeError):
                return self._envoie(400, b"", "text/plain")
            if statut not in ("conserver", "ecarter"):
                return self._envoie(400, b"", "text/plain")
            if not 0 <= n < len(etat["verdicts"]):
                return self._envoie(400, b"", "text/plain")

            # Le serveur est threade : charger()+enregistrer() forment une
            # lecture-modification-ecriture sur le meme fichier, qui doit
            # rester exclusive face a deux POST concurrents.
            with verrou:
                ecarts = charger(etat["decisions_path"], etat["signature"])
                # Un ecart qui rejoint le verdict automatique est retire
                # plutot qu'ajoute : le fichier ne garde que de vrais
                # desaccords.
                auto_garde = etat["verdicts"][n] is None
                veut_garder = statut == "conserver"
                if auto_garde == veut_garder:
                    ecarts.pop(n, None)
                else:
                    ecarts[n] = statut
                enregistrer(etat["decisions_path"], etat["signature"], ecarts)
            return self._envoie(200, b'{"ok":true}', "application/json")

    return Handler


def sert(source, cache_path, decisions_path=None, dossier_vignettes=None,
         port=8000, ouvrir=True, seuils=None,
         tolerance_bord=None, seuil_masque=None, moteur=None,
         taille=None, taille_sortie=None, interp_max=None,
         interp_deplacement_max=None,
         depassement_butee=None,
         couleur=None, couleur_fenetre=None, couleur_amplitude=None):
    """Lance le serveur local et ouvre le navigateur.

    source : la video a revoir, ou None pour ouvrir le viewer sur rien et en
    choisir une depuis la page (POST /api/source). Quand elle vient de la
    ligne de commande, cache_path / decisions_path / dossier_vignettes sont
    ceux de la ligne de commande et rien ne change ; quand elle est choisie
    dans la page, les trois sont DERIVES d'elle (voir chemins_derives).

    seuils, tolerance_bord, seuil_masque : transmis tels quels a
    construit_etat, memes parametres de tri que pipeline.render — voir
    construit_etat pour pourquoi ils doivent etre identiques a ceux du rendu.

    taille, taille_sortie, interp_max, interp_deplacement_max,
    depassement_butee : le cadrage, transmis au Porteur
    et de la au rendu lance depuis la page. Le sous-parseur viewer accepte
    ces options depuis toujours ; tant que la page ne faisait qu'afficher,
    les ignorer etait sans consequence. Depuis qu'elle lance le rendu, les
    ignorer donnerait un rendu qui ne correspond pas a ce qui a ete demande.

    couleur, couleur_fenetre, couleur_amplitude : la stabilisation de
    balance, memes parametres que pipeline.render, transmis au Porteur pour
    la meme raison que le cadrage — ils sement en plus les controles de la
    page, qui peut ensuite les modifier (POST /api/couleur).

    moteur : moteur de taches a utiliser ; un neuf par defaut. L'injecter
    rend les tests possibles sans lancer de vrai traitement.
    """
    decisions_path = decisions_path or DECISIONS_DEFAUT_NOM
    dossier_vignettes = dossier_vignettes or DOSSIER_DEFAUT
    moteur = Moteur() if moteur is None else moteur
    porteur = Porteur(source, cache_path, decisions_path, dossier_vignettes,
                      seuils, tolerance_bord, seuil_masque,
                      taille=taille, taille_sortie=taille_sortie,
                      interp_max=interp_max,
                      interp_deplacement_max=interp_deplacement_max,
                      depassement_butee=depassement_butee,
                      couleur=couleur, couleur_fenetre=couleur_fenetre,
                      couleur_amplitude=couleur_amplitude)
    httpd = ThreadingHTTPServer(("127.0.0.1", port),
                                fabrique_handler(porteur, moteur))
    url = f"http://127.0.0.1:{httpd.server_port}/"
    # La version au terminal AUSSI, et pas seulement dans la page : c'est
    # elle qu'on recopie dans un rapport de bug, et elle doit etre lisible
    # sans ouvrir le navigateur.
    print(f"Viewer {version_affichee()} sur {url}  (Ctrl+C pour arreter)")
    if source is None:
        print("Aucune source : en choisir une depuis la page.")
    if ouvrir:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        if moteur.etat()["etat"] == "en_cours":
            # Le fil de la tache n'est pas demon : le tuer laisserait un mp4
            # tronque. On demande l'annulation et on attend qu'elle referme
            # ses fichiers.
            print("\nAnnulation de la tache en cours...")
            moteur.annule()
            if not moteur.attend(delai=30.0):
                # Rendre la main ici ne rendrait rien du tout : le fil n'est
                # pas demon, donc l'interpreteur l'attendra de toute facon a
                # la sortie. On attend donc explicitement, en le disant,
                # plutot qu'afficher "arrete" devant un processus qui ne
                # s'arrete pas.
                #
                # Et on ne menace pas d'un fichier tronque : un second Ctrl+C
                # ne leve que dans le fil principal, et le fil de la tache
                # n'etant pas demon, threading._shutdown() le joint quand
                # meme. Le fichier est finalise dans tous les cas ; insister
                # ne fait qu'ajouter une trace disgracieuse a la meme
                # attente.
                print("La tache ne s'est pas arretee dans les 30 s ; attente "
                      "de sa fin (l'interrompre a nouveau n'abregera pas "
                      "l'attente : le fil de la tache est joint a la sortie "
                      "de l'interpreteur, avec une trace en plus).")
                moteur.attend()
        print("\nViewer arrete.")
