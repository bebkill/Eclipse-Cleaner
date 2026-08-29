"""Les tables de libelles, chargees depuis le disque.

De la DONNEE, pas du code : c'est ce qui autorise les accents du francais
affiche sans toucher a la convention du projet, qui continue de regir les
commentaires, les docstrings et les identifiants.

Deux consommateurs, une seule source de verite : la page recoit les deux
tables par GET /api/langues, et pipeline.py rend en francais les faits que
decisions.diagnostique lui donne (il n'a pas de page pour le faire).
"""
import json
import os

NOMS = ("fr", "en")
_DOSSIER = os.path.dirname(os.path.abspath(__file__))
_CACHE = {}


def charge(nom):
    """La table de la langue demandee.

    Leve FileNotFoundError pour une langue inconnue -- et non un repli
    silencieux sur le francais, qui masquerait une faute de frappe.
    """
    if nom in _CACHE:
        return _CACHE[nom]
    if nom not in NOMS:
        raise FileNotFoundError(f"langue inconnue : {nom!r}")
    with open(os.path.join(_DOSSIER, f"{nom}.json"), encoding="utf-8") as f:
        _CACHE[nom] = json.load(f)
    return _CACHE[nom]


def toutes():
    """Les deux tables, pour la page."""
    return {nom: charge(nom) for nom in NOMS}


def rend_fr(fait):
    """La phrase francaise d'un fait, pour la ligne de commande.

    La page fait ce travail elle-meme, dans la langue choisie ; la ligne de
    commande n'a que le francais et pas de moteur de gabarit. Une seule
    source de verite pour les deux : fr.json.
    """
    modele = charge("fr")[fait["code"]]
    if isinstance(modele, dict):
        modele = modele["one"] if fait.get("n") == 1 else modele["other"]
    for nom, valeur in fait.items():
        modele = modele.replace("{" + nom + "}", str(valeur))
    return modele
