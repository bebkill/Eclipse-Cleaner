"""Moteur des taches longues du viewer.

Une seule tache a la fois : deux passes en concurrence sur deux coeurs
physiques se nuiraient. La tache vit dans un fil du processus viewer et son
etat en memoire, ce qui suffit a la faire survivre a la fermeture d'un onglet
et a la rendre rattachable — tout onglet qui sonde voit l'avancement courant.

Le module ne connait ni HTTP ni le pipeline : on lui passe un appelable.
"""
import sys
import threading
import time
import traceback

#: Les genres de taches acceptes. Le viewer refuse tout autre.
GENRES = ("vignettes", "analyse", "rendu")


class TacheAnnulee(Exception):
    """Levee par le rappel de progression quand l'annulation est demandee.

    C'est le seul mecanisme d'annulation : le pipeline appelle le rappel a
    chaque frame, donc ce rappel est deja un point de passage regulier. La
    boucle se deroule, les with referment le pool, le lecteur et l'ecrivain.
    Rien n'est tue de force.
    """


class Occupe(Exception):
    """Une tache est deja en cours."""


def _instantane_vide():
    return {"id": None, "genre": None, "etat": None, "fait": 0,
            "total": None, "debut": None, "fin": None, "message": None,
            "avertissement": None, "options": None, "resultat": None}


class Moteur:
    """Detient la tache en cours, son avancement, et sait l'annuler."""

    def __init__(self):
        self._verrou = threading.Lock()
        self._arret = threading.Event()
        # _fil est protege par _verrou, comme _etat et _compteur : voir
        # lance() et attend(). Sans cela, un attend() concurrent pourrait
        # lire le fil de la tache precedente pendant que lance() est en
        # train d'affecter puis demarrer le nouveau.
        self._fil = None
        self._compteur = None
        self._dernier_id = 0
        self._etat = _instantane_vide()

    def lance(self, genre, fonction, total=None, compteur=None, apres=None,
              options=None):
        """Demarre fonction() dans un fil. Rend l'identifiant de la tache.

        compteur : appelable sans argument rendant le nombre d'unites faites,
        consulte par etat() pendant la tache. Les vignettes en ont besoin
        parce que genere() delegue a un unique processus ffmpeg, sans boucle
        Python ou accrocher un rappel.

        apres : appele apres un succes, jamais apres un echec. Une exception
        qu'il leverait n'invalide pas le travail accompli — voir etat().

        options : ce qui a ete DEMANDE au lancement, reporte tel quel dans
        l'instantane. Le moteur n'en fait rien : c'est le seul etat partage
        entre les onglets, donc le seul endroit ou un onglet ouvert en cours
        de tache peut retrouver les choix faits dans un autre (l'export PNG
        du rendu, aujourd'hui). Doit rester serialisable en JSON : l'appelant
        le construit, le moteur ne le copie ni ne le valide.

        Ce que la fonction RENVOIE va de meme dans l'instantane, sous
        "resultat" : sans cela la page ne peut rien dire de ce qu'un
        traitement a produit (voir _enveloppe).
        """
        with self._verrou:
            if self._etat["etat"] == "en_cours":
                raise Occupe(f"tache {self._etat['genre']} deja en cours")
            self._arret.clear()
            self._dernier_id += 1
            self._compteur = compteur
            self._etat = _instantane_vide()
            self._etat.update(id=self._dernier_id, genre=genre,
                              etat="en_cours", total=total, debut=time.time(),
                              options=options)
            ident = self._dernier_id
            # Affecter et demarrer le nouveau fil sous le meme verrou : un
            # attend() concurrent ne doit jamais pouvoir lire le fil de la
            # tache PRECEDENTE (deja termine) pendant cette fenetre, ce qui
            # lui ferait rendre True a tort alors qu'une tache vient de
            # demarrer.
            self._fil = threading.Thread(target=self._enveloppe,
                                         args=(fonction, apres), daemon=False)
            self._fil.start()
        return ident

    def _enveloppe(self, fonction, apres):
        try:
            # La valeur de retour n'est plus jetee : elle est le seul moyen
            # pour la page de dire ce qu'un traitement a produit (le rendu
            # rend ses comptes de frames). Le moteur ne l'interprete pas.
            resultat = fonction()
        except TacheAnnulee:
            self._acheve("annulee")
            return
        except BaseException as exc:            # noqa: BLE001
            # La trace complete va au terminal : le message seul ne permet
            # pas de diagnostiquer, et l'avaler serait pire que tout.
            traceback.print_exc(file=sys.stderr)
            self._acheve("echouee", message=f"{type(exc).__name__}: {exc}")
            return
        avertissement = None
        if apres is not None:
            try:
                apres()
            except BaseException as exc:        # noqa: BLE001
                traceback.print_exc(file=sys.stderr)
                # UN FAIT, pas une phrase : ce module ne sait pas dans quelle
                # langue la page s'affiche. {detail} porte le message
                # d'exception TEL QUEL, non traduit -- du texte de
                # diagnostic, pas de la copie d'interface.
                avertissement = {"code": "rechargement_impossible",
                                 "detail": f"{type(exc).__name__}: {exc}"}
        self._acheve("terminee", avertissement=avertissement,
                     resultat=resultat)

    def _acheve(self, etat, message=None, avertissement=None, resultat=None):
        with self._verrou:
            self._compteur = None
            self._etat.update(etat=etat, fin=time.time(), message=message,
                              avertissement=avertissement, resultat=resultat)
            genre = self._etat["genre"]
        # Le terminal est la ou l'operateur regarde tourner la passe : il n'y
        # lisait jusqu'ici RIEN de la fin d'une tache lancee depuis la page,
        # ni succes, ni annulation, ni echec (l'echec n'y laissait qu'une
        # trace nue). Hors du verrou : une ecriture sur un terminal lent ne
        # doit pas bloquer un sondage /api/tache.
        ligne = f"Tache {genre} : {etat}"
        print(ligne + (f" ({message})" if message else ""))

    def etat(self):
        """Un instantane de la derniere tache, en cours ou terminee.

        Rend une copie : l'appelant ne doit pas pouvoir muter l'etat du
        moteur, et une reponse HTTP serialisee hors verrou verrait sinon un
        dictionnaire changer sous elle.
        """
        with self._verrou:
            instantane = dict(self._etat)
            compteur = self._compteur
        if compteur is not None:
            try:
                instantane["fait"] = int(compteur())
            except OSError:
                # Un compteur qui lit le disque peut echouer sans que la
                # tache soit en cause : on garde la derniere valeur connue.
                pass
        return instantane

    @property
    def arret(self):
        """Le drapeau d'annulation, pour les traitements sans rappel.

        genere() delegue a un processus ffmpeg et n'appelle aucun rappel de
        progression : elle consulte ce drapeau. Le rendre public evite au
        viewer d'aller chercher un attribut prive.
        """
        return self._arret

    def annule(self):
        """Leve le drapeau. Ne tue rien : le rappel fera le reste."""
        self._arret.set()

    def attend(self, delai=None):
        """Joint le fil. Rend True s'il est termine, False si delai expire."""
        with self._verrou:
            fil = self._fil
        if fil is None:
            return True
        fil.join(delai)
        return not fil.is_alive()

    def progression(self, fait, total=None):
        """Rappel a passer au pipeline. Leve TacheAnnulee si on a annule."""
        if self._arret.is_set():
            raise TacheAnnulee()
        with self._verrou:
            self._etat["fait"] = int(fait)
            if total is not None:
                self._etat["total"] = int(total)
