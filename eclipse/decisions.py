"""Ecarts de l'utilisateur au tri automatique.

Ce module detient seul le format du fichier de decisions. Le viewer et le
rendu passent tous deux par lui.

Le fichier ne contient QUE les desaccords : les frames ou l'utilisateur
contredit l'algorithme. Enregistrer les 2556 statuts perimerait au premier
changement de seuil, sans qu'on puisse distinguer un choix humain d'un
reliquat.
"""
import json
import os
import shutil
import tempfile

SCHEMA_DECISIONS = 1

#: Nom par defaut du fichier de decisions. Defini ici parce que ce module
#: detient le format ; pipeline et viewer l'importent plutot que de le
#: redeclarer chacun de leur cote.
DECISIONS_DEFAUT_NOM = "decisions.json"

#: Motif porte par une frame que l'utilisateur a explicitement ecartee.
MOTIF_MANUEL = "manuel"

#: Suffixe de la copie de sauvegarde qu'enregistrer() laisse derriere elle.
#: Une seule generation : voir enregistrer() pour ce qu'elle rattrape et ce
#: qu'elle ne rattrape pas.
SUFFIXE_PRECEDENT = ".precedent"


def _lit(chemin):
    """JSON brut du fichier (n'importe quelle forme), ou None si absent/illisible."""
    if not os.path.isfile(chemin):
        return None
    try:
        with open(chemin, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None


def diagnostique(chemin, signature):
    """None si le fichier est absent ou exploitable ; sinon le fait du refus.

    Un FAIT -- un code et ses valeurs -- et non une phrase : ce module ne
    sait pas dans quelle langue la page s'affiche, et ne doit pas
    l'apprendre. C'est a l'appelant (la page, ou langues.rend_fr pour la
    ligne de commande) de composer le texte.

    charger() renvoie {} aussi bien pour un fichier absent (cas normal) que
    pour un fichier present mais refuse (schema perime, source differente,
    JSON corrompu) : sans ce sondage separe, un re-telechargement de la
    source qui change la signature ferait evaporer des heures de revue
    humaine sans que rien ne le signale. A appeler par les endroits qui
    doivent avertir (rendu, viewer), pas par charger() lui-meme qui doit
    rester silencieux et sans lever.
    """
    if not os.path.isfile(chemin):
        return None
    donnees = _lit(chemin)
    if donnees is None:
        return {"code": "fichier_illisible", "chemin": chemin}
    if not isinstance(donnees, dict):
        return {"code": "racine_invalide", "chemin": chemin}
    if donnees.get("schema") != SCHEMA_DECISIONS:
        return {"code": "schema_incompatible", "chemin": chemin,
                "trouve": donnees.get("schema"), "attendu": SCHEMA_DECISIONS}
    if donnees.get("source") != signature:
        return {"code": "autre_source", "chemin": chemin}
    if not isinstance(donnees.get("ecarts", {}), dict):
        return {"code": "ecarts_invalides", "chemin": chemin}
    return None


def charger(chemin, signature):
    """Ecarts enregistres, ou {} si le fichier est inutilisable.

    Un fichier absent, illisible, de racine ou de schema incompatible, ou
    appartenant a une autre source rend {} plutot que de lever : le rendu
    doit pouvoir tourner sans decisions, et des decisions prises sur une
    autre video seraient appliquees de travers. Pour distinguer une absence
    normale d'un refus qui merite un avertissement, voir diagnostique().
    """
    donnees = _lit(chemin)
    if not isinstance(donnees, dict):
        return {}
    if donnees.get("schema") != SCHEMA_DECISIONS:
        return {}
    if donnees.get("source") != signature:
        return {}
    ecarts_bruts = donnees.get("ecarts", {})
    if not isinstance(ecarts_bruts, dict):
        return {}
    ecarts = {}
    for cle, statut in ecarts_bruts.items():
        if statut in ("conserver", "ecarter"):
            try:
                ecarts[int(cle)] = statut
            except (TypeError, ValueError):
                continue
    return ecarts


def enregistrer(chemin, signature, ecarts):
    """Ecrit les ecarts. Les cles JSON sont du texte, d'ou la conversion.

    Ecriture atomique (fichier temporaire puis os.replace) : ce fichier
    n'est pas regenerable, une interruption en pleine ecriture en place le
    laisserait tronque ou vide, effacant silencieusement la revue humaine.

    UNE GENERATION DE SAUVEGARDE. L'etat precedent est COPIE sous
    <chemin><SUFFIXE_PRECEDENT> avant d'etre remplace. Ce n'est pas une
    precaution theorique : une mutation de verification a fait retomber un
    test sur le decisions.json relatif au repertoire courant, et ce
    os.replace a efface 228 decisions de tri sans aucune recuperation
    possible. Cette fonction est le SEUL endroit d'ou un fichier de
    decisions est ecrit -- du Python pur, un seul remplacement -- donc le
    seul endroit ou la prevention est complete.

    UNE COPIE, ET NON UN RENOMMAGE, et c'est le point delicat. Renommer la
    cible avant de livrer ouvre une fenetre pendant laquelle `chemin`
    N'EXISTE PLUS : si la livraison echoue et que la compensation echoue
    aussi -- ou si le processus meurt entre les deux, ou aucune compensation
    Python n'est possible -- il ne reste que le .precedent, le viewer lit {}
    en silence, et le DEUXIEME enregistrement suivant ecrase la sauvegarde.
    Le filet fabriquait la perte qu'il devait eviter. Avec une copie, la
    cible ne cesse jamais d'exister et le remplacement final reste l'unique
    operation atomique. Le prix est une copie d'un fichier de quelques Ko a
    chaque decision, et une copie interrompue qui laisse un .precedent
    tronque -- mais jamais un `chemin` absent, et c'est lui qui porte l'etat
    courant.

    Une seule generation, et elle se fait ecraser au prochain
    enregistrement : c'est un filet contre l'ecrasement accidentel, pas un
    historique. Un echec de la sauvegarde elle-meme (premier enregistrement,
    dossier en lecture seule) n'empeche pas l'ecriture : c'est un filet, il
    ne doit pas devenir un obstacle.
    """
    donnees = {"schema": SCHEMA_DECISIONS, "source": signature,
               "ecarts": {str(n): s for n, s in sorted(ecarts.items())}}
    dossier = os.path.dirname(os.path.abspath(chemin)) or "."
    fd, tmp = tempfile.mkstemp(prefix=".decisions-", suffix=".tmp", dir=dossier)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(donnees, f, indent=1)
        try:
            shutil.copy2(chemin, chemin + SUFFIXE_PRECEDENT)
        except OSError:
            pass                 # rien a sauvegarder, ou refus du systeme
        os.replace(tmp, chemin)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def applique(verdicts, ecarts):
    """Superpose les ecarts aux verdicts automatiques.

    A appeler APRES supprime_ilots. Applique avant, un ecart se ferait
    ecraser : une frame isolee que l'utilisateur force a conserver serait
    re-rejetee parce qu'elle est seule. L'humain gagne toujours.
    """
    out = list(verdicts)
    for n, statut in ecarts.items():
        if not 0 <= n < len(out):
            continue
        out[n] = None if statut == "conserver" else MOTIF_MANUEL
    return out
