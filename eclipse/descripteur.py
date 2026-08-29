"""Le descripteur livre a cote du rendu : avec quoi ce fichier a ete produit.

Comparer les dates du fichier de decisions et du rendu ne suffit pas. Le
rendu dure douze minutes et la revue reste utilisable pendant qu'il tourne :
une decision prise a la minute 3 donne un fichier de decisions PLUS ANCIEN
que le rendu termine, alors que le rendu ne l'a pas prise en compte. Le
signal dirait « a jour » a tort.

Invariant du module : toute defaillance penche vers « a refaire ». Un
descripteur absent, illisible, d'un schema inconnu ou discordant rend
perime() vrai. Jamais faux par defaut.

CE QUE LE SCHEMA 2 CHANGE. Le schema 1 n'enregistrait qu'une EMPREINTE du
tri applique : il savait dire QUE quelque chose avait change, jamais QUOI,
et le bandeau annoncait « a refaire » sans qu'on puisse juger si un rendu de
douze minutes en valait la peine. Le schema 2 enregistre les ecarts
eux-memes -- 228 entrees sur la sequence reelle, un petit dictionnaire --,
ce qui rend la comparaison frame par frame possible (voir compare). Un
descripteur de schema 1 devient donc de schema inconnu, ce que perime()
traite deja comme « a refaire » : direction sure, et cela se repare au rendu
suivant.

Module pur : il ne connait ni HTTP ni le viewer. Les ecarts, la signature du
cache et les reglages lui sont passes deja charges ; il ne lit lui-meme que
son propre fichier.
"""
import json
import os

#: Version du format du descripteur. Un schema inconnu vaut peremption.
NOM_SCHEMA = 2


def chemin_descripteur(sortie_rendu):
    """<sortie sans extension>.json, a cote du rendu.

    Pas <sortie>.json colle a l'extension existante : sur ce projet, un nom
    temporaire construit par simple concatenation (x.mp4.partiel) a deja
    fait ecrire zero octet en silence, ffmpeg deduisant le conteneur de
    l'extension finale. Rien ici n'ecrit de video, mais la meme discipline
    de nommage s'applique.
    """
    return os.path.splitext(sortie_rendu)[0] + ".json"


def _ecarts_canoniques(ecarts):
    """Les ecarts sous la forme qui survit a un aller-retour JSON.

    Cles en texte, parce que json les rendra telles a la relecture : sans
    cette conversion a l'ECRITURE, perime() comparerait {1: "ecarter"} a
    {"1": "ecarter"} et repondrait « perime » pour toujours -- le bandeau
    afficherait « a refaire » en permanence des la premiere seconde apres un
    rendu. Meme piege que le cadrage en tuple (voir la docstring d'ecrit).

    L'ordre d'insertion, lui, n'a pas besoin d'etre normalise : deux dict
    Python sont egaux quel qu'il soit. C'est le schema 1, qui hachait une
    chaine, qui devait trier ses cles.
    """
    return {str(n): statut for n, statut in ecarts.items()}


def _contenu(ecarts, signature_cache, reglages):
    return {"schema": NOM_SCHEMA,
            "decisions": _ecarts_canoniques(ecarts),
            "cache": signature_cache,
            "reglages": reglages}


def ecrit(sortie_rendu, ecarts, signature_cache, reglages):
    """Ecrit le descripteur. A appeler APRES la livraison du rendu.

    signature_cache et reglages doivent rester des structures JSON natives
    (str, int, float, bool, None, list, dict a cles str) : un tuple ou un
    dict a cles non-str survivrait a l'ecriture mais pas a la relecture
    (json les rend respectivement en liste et en cles str), et perime()
    comparerait alors deux formes qui ne seraient jamais egales, meme sans
    changement reel. Sans consequence dangereuse ici puisque perime() penche
    deja vers « a refaire », mais une peremption fantome et permanente
    serait genante a diagnostiquer.

    ecarts, lui, est normalise ici meme (voir _ecarts_canoniques) : c'est le
    viewer qui le fabrique, avec des cles ENTIERES, et exiger de lui une
    forme JSON native serait deplacer le piege chez l'appelant.
    """
    chemin = chemin_descripteur(sortie_rendu)
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(_contenu(ecarts, signature_cache, reglages), f)


def _ecarts_relus(brut):
    """Les ecarts du descripteur, ramenes a des cles entieres, ou None.

    None quand la forme n'est pas celle qu'ecrit ce module : on ne sait plus
    a quelle frame rattacher quoi, et inventer serait pire que se taire. Le
    descripteur reste perime par la comparaison de _contenu, qui, elle, ne
    depend pas de cette lecture.
    """
    if not isinstance(brut, dict):
        return None
    try:
        return {int(n): statut for n, statut in brut.items()}
    except (TypeError, ValueError):
        return None


def _divergences(anciens, courants):
    """Les numeros de frame dont le statut differe entre les deux tris.

    L'union des deux jeux de cles, et non l'un des deux : une frame qui
    porte un ecart d'UN SEUL cote diverge tout autant qu'une frame dont
    l'ecart a change de sens. dict.get rend None de l'autre cote, et None
    differe de « conserver » comme de « ecarter ».
    """
    return tuple(sorted(
        n for n in set(anciens) | set(courants)
        if anciens.get(n) != courants.get(n)))


def compare(sortie_rendu, ecarts, signature_cache, reglages):
    """Rend (perime, divergences) en UNE seule lecture du descripteur.

    Une seule lecture parce que /api/frames a besoin des deux a chaque
    requete : le bandeau lit le premier, la timeline le second, et les
    relire separement doublerait un acces disque par requete pour rien.

    divergences est un tuple, vide des que la comparaison frame par frame
    n'a pas de sens : descripteur absent, illisible, de schema inconnu, ou
    dont les decisions ne sont pas indexees par des entiers. « Vide » veut
    dire « rien a montrer », jamais « rien n'a change » -- c'est perime qui
    porte cette reponse-la, et lui penche toujours vers « a refaire ».

    signature_cache et reglages : memes structures JSON natives qu'attend
    ecrit(), pour la meme raison (voir sa docstring).
    """
    try:
        with open(chemin_descripteur(sortie_rendu), encoding="utf-8") as f:
            lu = json.load(f)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return True, ()
    if not isinstance(lu, dict) or lu.get("schema") != NOM_SCHEMA:
        return True, ()
    perime_ = lu != _contenu(ecarts, signature_cache, reglages)
    anciens = _ecarts_relus(lu.get("decisions"))
    if anciens is None:
        return perime_, ()
    return perime_, _divergences(anciens, ecarts)


def perime(sortie_rendu, ecarts, signature_cache, reglages):
    """Vrai si le rendu ne reflete plus ce qu'on lui demanderait aujourd'hui.

    Toute defaillance de lecture (fichier absent, illisible, encodage
    invalide, JSON corrompu) ou tout schema inconnu rend perime() vrai,
    jamais faux : voir l'invariant du module en tete de fichier.

    La moitie de compare() qui suffit a ceux qui ne peignent pas la
    timeline.
    """
    return compare(sortie_rendu, ecarts, signature_cache, reglages)[0]
