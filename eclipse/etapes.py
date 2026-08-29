"""Les faits qui qualifient chaque etape, deduits du disque.

Pas de fichier d'etat, pas de machine a etats stockee : un etat stocke ment
des que l'utilisateur supprime un fichier, reencode sa source ou lance la
ligne de commande dans un autre terminal. Le disque, lui, ne ment jamais.

Module pur : il ne connait ni HTTP ni le moteur de taches.
"""
import os

from .vignettes import a_jour, compte

#: Les trois etapes du bandeau, dans leur ordre d'affichage.
ETAPES = ("vignettes", "analyse", "rendu")


def faits(dossier_vignettes, sortie_rendu, signature, donnees):
    """Constate les trois faits. donnees est le cache deja charge, ou None.

    Le cache est passe plutot que relu : l'appelant l'a deja charge pour
    calculer les verdicts, et le relire ici doublerait la lecture a chaque
    requete.
    """
    nb = compte(dossier_vignettes)
    # compte() seul ne regarde que le nombre de fichiers, jamais leur
    # provenance : sans a_jour(), des vignettes d'une AUTRE source
    # passeraient pour pretes et l'utilisateur trierait sur les mauvaises
    # images. Meme regle que construit_etat.
    vignettes = bool(
        nb and a_jour(dossier_vignettes, signature)
        and (donnees is None or nb == len(donnees["frames"])))
    return {"vignettes": vignettes,
            "analyse": donnees is not None,
            "rendu": os.path.isfile(sortie_rendu)}


def etats(faits_, perime_rendu):
    """Traduit les faits en etats d'affichage.

    « en_cours » n'est pas produit ici : il vient du moteur de taches, que ce
    module ne connait pas, et la page le superpose.
    """
    e = {}
    for nom in ("vignettes", "analyse"):
        # Ni l'une ni l'autre ne se perime : rien en aval ne consomme les
        # vignettes, et une reanalyse produit un cache equivalent. Seul un
        # changement de source les invalide, et il rend le fait faux.
        e[nom] = "faite" if faits_[nom] else "disponible"
    if not faits_["analyse"]:
        e["rendu"] = "indisponible"
    elif not faits_["rendu"]:
        # perime_rendu ne doit pas transformer une absence en peremption :
        # un descripteur orphelin ferait sinon afficher « a refaire » sur une
        # etape jamais lancee.
        e["rendu"] = "disponible"
    else:
        e["rendu"] = "a_refaire" if perime_rendu else "faite"
    return e
