"""La boite de dialogue du systeme, pour designer une video.

L'explorateur web que ce module remplace listait un dossier a la fois, sans
completion, sans favoris, sans « recents » : l'utilisateur l'a essaye et a
demande « un vrai explorateur de fichier ». C'est celui du systeme, ouvert
par tkinter, qui est dans la bibliotheque standard -- aucune dependance
nouvelle, la contrainte du projet (numpy et imageio-ffmpeg seuls) tient.

LE FIL. Un interpreteur Tcl n'appartient qu'au fil qui l'a cree. La regle
est respectee ici en creant, utilisant et detruisant le Tk dans le MEME
appel, donc dans le fil du gestionnaire HTTP : rien n'est garde entre deux
appels, et aucun fil ne survit. Mesure avant d'ecrire ce module (sonde
tkinter) : un Tk depuis le fil principal, un Tk depuis un fil secondaire et
un askopenfilename depuis un fil secondaire aboutissent tous les trois, sans
laisser de fil derriere.

DEVANT LES AUTRES FENETRES. Un processus console dont un fil secondaire
ouvre une fenetre, alors que le premier plan appartient au navigateur, ne
la voit pas passer devant : elle s'ouvre DERRIERE. L'attribut -topmost,
pose sur la racine avant la boite, l'y remet -- mesure au corps du module.
Le focus clavier, lui, reste hors de portee : Windows le refuse a un
processus qui n'a pas recu la derniere entree de l'utilisateur.

LA SOURCE N'EST JAMAIS TOUCHEE. Ce module ne fait que NOMMER un fichier :
askopenfilename ouvre une boite de selection, pas le fichier. Rien ici ne
lit ni n'ecrit le chemin rendu.

Module pur : il ne connait ni HTTP ni le viewer. Il connait langues.charge,
lui aussi pur (aucune connaissance HTTP ni page) : le titre de la boite et
ses filtres sont la SEULE chaine qui doit atteindre une fenetre du systeme,
jamais le DOM -- aucune solution cote page ne les atteint.
"""
import sys

from . import langues

#: Conteneurs que ffmpeg lit dans ce projet. Compare en minuscules.
#: Venait de explorateur.py, supprime avec l'explorateur web ; la valeur n'a
#: pas bouge.
EXTENSIONS_VIDEO = (".mp4", ".mov", ".avi", ".mkv", ".m4v")

#: macOS is the exception to the THREAD paragraph above: the measurements
#: there were made on Windows. On macOS, AppKit only allows windows on the
#: MAIN thread, and this module runs in an HTTP handler thread — there,
#: tkinter.Tk() does not raise a TclError the except clauses could turn into
#: a status: it ABORTS the whole process (NSInternalInconsistencyException,
#: issue #4). _choisit_video_macos works around this by running the panel
#: in osascript's OWN process instead of in this one — the diagnosis and
#: the route are Mireia Nievas's (issue #1). This message now covers only
#: what that route cannot: osascript missing from the system entirely, with
#: no other way in yet. Like MESSAGE_INDISPONIBLE, this text only states the
#: WHY; the page adds the way out (restart with the source on the command
#: line) in its own language.
MESSAGE_MACOS = (
    "The system file dialog is not available on macOS: osascript, which "
    "this module relies on to avoid the AppKit main-thread restriction, "
    "was not found on this system.")

#: Raised when osascript IS present but the panel fails to open for a
#: reason other than the user closing it (permissions, a sandboxed
#: environment, a malformed script, ...). {detail} carries osascript's own
#: stderr, or the OS error that kept it from even starting: free diagnostic
#: text, like MESSAGE_INDISPONIBLE's parenthetical -- not localized, since
#: the page composes the surrounding sentence itself (key
#: "boite_indisponible").
MESSAGE_MACOS_OSASCRIPT_ECHEC = (
    "osascript failed to open the macOS file dialog: {detail}")

#: Ce qu'on peut dire a l'utilisateur quand la boite ne s'ouvre pas. Sans
#: explorateur web, il ne reste AUCUN moyen de choisir une source depuis la
#: page : le message doit donc porter la sortie de secours, pas seulement le
#: constat de la panne.
MESSAGE_INDISPONIBLE = (
    "La boite de dialogue du systeme n'a pas pu s'ouvrir (tkinter "
    "indisponible : pas d'affichage, session distante, ou Tcl/Tk absent).")


class Indisponible(Exception):
    """tkinter n'a pas pu demarrer, ou la boite n'a pas pu s'afficher."""


def _types_de_fichiers(libelles=None):
    """Le filtre de la boite : les videos d'abord, tout le reste ensuite.

    libelles : la table de langue (langues.charge(...)) dont les deux
    entrees "boite_filtre_videos" et "boite_filtre_tous" nomment les
    filtres ; None reprend le francais, pour les appelants qui ne portent
    pas de langue (et pour les tests existants, ecrits avant elle).

    « Tous les fichiers » reste offert parce que le filtre porte sur
    l'EXTENSION et qu'un conteneur lisible par ffmpeg peut en porter une
    autre. Un fichier choisi par cette voie qui n'est pas une video est
    refuse plus loin, par probe() (voir viewer._change_source) : le refus
    existe deja, il n'y a pas de raison de fermer la porte ici.
    """
    if libelles is None:
        libelles = langues.charge("fr")
    motifs = " ".join("*" + e for e in EXTENSIONS_VIDEO)
    return [(libelles["boite_filtre_videos"], motifs),
            (libelles["boite_filtre_tous"], "*")]


def _choisit_video_macos(titre, dossier_initial):
    """Opens the native macOS panel through osascript ("choose file").

    osascript runs the panel in ITS OWN process, spawned fresh for this
    call and gone once it returns: the HTTP handler thread that called
    choisit_video never touches AppKit itself, which is exactly what
    dodges the main-thread abort described above. This is Mireia Nievas's
    diagnosis and route (issue #1); this function keeps her approach
    recognizable, with two fixes on top: user-cancel and osascript-failure
    no longer collapse into the same return value, and the type filter is
    built from EXTENSIONS_VIDEO instead of a second, hand-kept list.

    titre : the dialog's prompt, already resolved to the caller's language
    (libelles["boite_titre"]) -- this function does not touch langues
    itself, matching the module's "one string only" rule at the top.
    dossier_initial : where osascript should start browsing; ignored if it
    is not an existing directory, since a bad POSIX file clause is itself
    an osascript failure.

    Renders the chosen path, or None at a user cancel -- AppleScript's
    "choose file" reports that as an error whose text contains "User
    canceled" or the OSStatus -128, which is how it is told apart here
    from a genuine failure.

    Raises Indisponible if osascript starts but exits non-zero for any
    OTHER reason, or if it cannot be started at all (missing binary
    despite the shutil.which check below, no permission, ...): those are
    infrastructure failures, not a choice the user made, and the HTTP
    caller (viewer._parcourir) depends on that distinction to turn only
    the latter into a 503.
    """
    import os
    import subprocess

    morceaux = ["choose file"]
    if titre:
        morceaux.append('with prompt "%s"' % titre.replace('"', '\\"'))
    if dossier_initial and os.path.isdir(dossier_initial):
        chemin_pose = dossier_initial.replace('"', '\\"').replace("\\", "/")
        morceaux.append('default location POSIX file "%s"' % chemin_pose)
    # Les UTI d'abord : ce sont elles qui font vraiment filtrer le panneau
    # natif par NATURE de fichier. Les extensions restent a cote, une video
    # que ffmpeg lit pouvant ne relever d'aucune des deux UTI.
    types = ['"public.movie"', '"public.video"']
    types += ['"%s"' % e.lstrip(".") for e in EXTENSIONS_VIDEO]
    morceaux.append("of type {%s}" % ", ".join(types))
    script = "POSIX path of (%s)" % " ".join(morceaux)

    try:
        resultat = subprocess.run(["osascript", "-e", script],
                                  capture_output=True, text=True)
    except OSError as exc:
        # shutil.which l'avait trouve, mais le lancer a quand meme echoue
        # (retire entre-temps, permissions, ...).
        raise Indisponible(
            MESSAGE_MACOS_OSASCRIPT_ECHEC.format(detail=str(exc))) from exc

    if resultat.returncode != 0:
        erreur = (resultat.stderr or "").strip()
        if "User canceled" in erreur or "-128" in erreur:
            # La formule d'AppleScript pour « l'utilisateur a ferme le
            # panneau » -- ce n'est pas un echec, voir la docstring.
            return None
        raise Indisponible(MESSAGE_MACOS_OSASCRIPT_ECHEC.format(
            detail=erreur or ("osascript exited with code %d" %
                              resultat.returncode)))

    return resultat.stdout.strip() or None


def choisit_video(dossier_initial=None, langue="fr"):
    """Ouvre la boite native et rend le chemin choisi, ou None a l'annulation.

    dossier_initial : ou ouvrir la boite ; None laisse le systeme decider.
    langue : celle de la page appelante ("fr" ou "en") ; le titre et les
    filtres de la boite du systeme n'ont pas d'autre chemin pour la
    connaitre, puisqu'ils s'affichent hors du DOM. Une langue inconnue leve
    FileNotFoundError dans langues.charge -- repliee ici sur le francais,
    parce qu'un choix d'interface ne doit pas empecher de designer un
    fichier.

    Leve Indisponible si tkinter/osascript ne demarre pas ou si la boite ne
    peut pas s'afficher. Ne leve jamais autre chose de previsible :
    l'appelant est un gestionnaire HTTP, et une TclError nue lui donnerait
    une trace au lieu d'un statut.

    On macOS, osascript runs the panel from its own process (see
    _choisit_video_macos) and is tried FIRST: a Tk created outside the main
    thread does not fail there, it kills the process (issue #4). Only when
    osascript itself cannot be found does this fall back to the refusal in
    MESSAGE_MACOS -- there is no third way in yet.
    """
    try:
        libelles = langues.charge(langue)
    except FileNotFoundError:
        libelles = langues.charge("fr")

    if sys.platform == "darwin":
        import shutil
        if shutil.which("osascript") is None:
            raise Indisponible(MESSAGE_MACOS)
        return _choisit_video_macos(libelles.get("boite_titre"),
                                    dossier_initial)

    try:
        import tkinter
        from tkinter import filedialog
    except ImportError as exc:
        # tkinter absent de l'installation, ou _tkinter non compile.
        raise Indisponible(MESSAGE_INDISPONIBLE) from exc
    try:
        racine = tkinter.Tk()
    except tkinter.TclError as exc:
        # Pas d'affichage : DISPLAY absent, session sans bureau.
        raise Indisponible(MESSAGE_INDISPONIBLE) from exc
    try:
        # withdraw AVANT la boite : sans lui, une fenetre racine vide reste
        # posee sur le bureau derriere la boite de selection.
        racine.withdraw()
        # LA BOITE S'OUVRAIT DERRIERE LE NAVIGATEUR. Elle nait dans un fil
        # secondaire d'un processus console, pendant que la fenetre de
        # premier plan appartient au navigateur : Windows ne la fait pas
        # passer devant. MESURE (sonde hors pytest, boite auto-fermee,
        # fenetre de premier plan et pile z lues par l'API du systeme, une
        # autre application tenant le premier plan) :
        #   sans rien ..................... boite SOUS l'autre fenetre, 3 /3
        #   avec -topmost ................. boite AU-DESSUS, 2 /2
        #   + lift + focus_force + update . identique a -topmost seul.
        # Aucune des variantes n'obtient le PREMIER PLAN (le focus clavier) :
        # Windows le refuse a un processus qui n'a pas recu la derniere
        # entree de l'utilisateur. -topmost ne donne donc pas le focus, il
        # rend la boite VISIBLE ; un clic dessus fait le reste. C'est la
        # moitie du probleme qui soit atteignable d'ici.
        try:
            racine.attributes("-topmost", True)
        except tkinter.TclError:
            # Un gestionnaire de fenetres qui ignore -topmost n'empeche pas
            # de choisir un fichier : ce serait un confort en moins, pas une
            # boite indisponible. D'ou ce rattrapage separe, place avant
            # celui qui entoure la boite elle-meme.
            pass
        chemin = filedialog.askopenfilename(
            parent=racine,
            title=libelles["boite_titre"],
            # Une chaine vide, et non None : Tcl refuse None ici.
            initialdir=dossier_initial or "",
            filetypes=_types_de_fichiers(libelles))
    except tkinter.TclError as exc:
        raise Indisponible(MESSAGE_INDISPONIBLE) from exc
    finally:
        # DANS TOUS LES CAS, y compris apres une exception : un Tk laisse
        # vivant retiendrait son interpreteur Tcl dans un fil de requete HTTP
        # qui, lui, va se terminer. destroy() peut lui-meme lever si
        # l'interpreteur est deja parti -- ce n'est alors plus un probleme.
        try:
            racine.destroy()
        except tkinter.TclError:
            pass
    # askopenfilename rend la chaine VIDE a l'annulation, pas None : sans
    # cette conversion, l'appelant recevrait "" et le prendrait pour un
    # chemin. (Sur certaines plateformes elle rend un tuple vide ; `or None`
    # couvre les deux.)
    return chemin or None
