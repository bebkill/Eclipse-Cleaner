"""Ou passe la ligne entre ce qu'un test peut prouver et ce qui attend un humain.

askopenfilename BLOQUE jusqu'a ce qu'on clique. Un test qui l'ouvrirait pour
de vrai attendrait donc quelqu'un.

LA LIGNE EST TIREE ICI : `tkinter.Tk` est remplace par une DOUBLURE qui ne
demarre aucun interpreteur Tcl, et `askopenfilename` par un bouchon. Tout le
reste est le vrai code : les deux imports, les deux branches de rattrapage,
l'ordre des appels, le `finally`, et la conversion de la chaine vide en None.
La doublure enregistre ce qu'on lui fait, ce qui permet de garder l'invariant
du module -- « aucun Tk ne survit a l'appel » -- comme un compte de
`destroy()` plutot que comme une inspection de `tkinter._default_root`.

POURQUOI PAS DE Tk REEL, ALORS QUE C'ETAIT LE PREMIER CHOIX. Parce qu'il ne
peut pas etre rendu fiable sous pytest sur ce poste, et qu'un test qui echoue
une fois sur deux pour une raison d'environnement apprend a relancer plutot
qu'a lire. Ce qui a ete mesure, sans une ligne de ce projet :

  - 60 cycles Tk()/destroy() HORS pytest .............. 3 passes /3 vertes ;
  - 60 cycles dans UN SEUL test sous pytest .......... 5 passes /5 vertes ;
  - 15 tests a 1 cycle .............................. vertes ;
  - 40 tests a 1 cycle .............. 2 passes /3 avec au moins un echec ;
  - 15 tests a 2 cycles (fixture + test) ...... 1 echec a CHAQUE passe ;
  - fixture de portee SESSION + 1 cycle dans le test .. 4 echecs /4 ;
  - 15 tests a 1 cycle sous capfd.disabled() ... 1 a 2 echecs a chaque passe.

L'echec est toujours le meme : `Can't find a usable init.tcl`, avec
« couldn't read file ... : No error » -- un echec de lecture a errno NUL,
signature d'une lecture sur un descripteur redirige sous les pieds de Tcl.
La correction posee d'abord, `capfd.disabled()`, AGGRAVAIT le probleme : elle
ajoute deux redirections de descripteurs par test. Aucune des configurations
vertes ci-dessus ne s'explique par une regle que je sache enoncer -- et batir
la suite sur un effet qu'on ne sait pas expliquer, c'est promettre une
stabilite qu'on ne controle pas. Le Tk reel est donc sorti de la suite.

CE QUI EST PERDU, ET QU'AUCUN TEST DE CE DEPOT NE COUVRE PLUS :
  1. que Tcl accepte reellement la sequence Tk() / withdraw() / destroy()
     depuis un fil secondaire, et n'y laisse ni fil ni fenetre. C'etait
     verifie par une sonde manuelle (`sonde_tk.py`) : Tk depuis le fil
     principal, Tk depuis un fil secondaire, et askopenfilename depuis un fil
     secondaire referme par le planificateur Tk apres 700 ms -- les trois
     aboutissent, aucun fil ne survit. Cette sonde est le SEUL element de
     preuve pour ce point, et elle est manuelle ;
  2. que la boite s'affiche, qu'elle soit filtree sur les extensions
     annoncees, et qu'elle rende le chemin CHOISI : cela demande un clic ;
  3. que Windows pose reellement la boite au-dessus du navigateur. Mesure
     par une seconde sonde manuelle (voir le test qui porte le tableau) :
     l'API du systeme y est interrogee sur la fenetre de premier plan et sur
     la pile z pendant que la boite est ouverte. Un test peut garder l'APPEL
     a -topmost ; son effet, lui, se mesure hors pytest ;
  4. le comportement sur une machine SANS affichage. La branche qui le traite
     est exercee plus bas par une TclError posee a la place de Tk(), ce qui
     exerce NOTRE branche, pas le refus de Tcl.
Ce qui reste couvert, en revanche, l'est sur le vrai code : voir chaque test.
"""
import builtins
import shutil
import subprocess
import sys
import threading

import pytest

from eclipse import langues
from eclipse.dialogue import (EXTENSIONS_VIDEO, MESSAGE_INDISPONIBLE,
                              MESSAGE_MACOS, MESSAGE_MACOS_OSASCRIPT_ECHEC,
                              Indisponible, _types_de_fichiers, choisit_video)

tkinter = pytest.importorskip("tkinter")


class FausseRacine:
    """Un Tk qui n'ouvre aucun interpreteur Tcl, et qui note ce qu'on lui fait.

    `journal` est partage avec le bouchon de la boite : c'est lui qui permet
    de verifier l'ORDRE (withdraw avant la boite, destroy apres), et pas
    seulement que chaque appel a eu lieu.
    """

    def __init__(self, journal, destroy_leve=False, attributs_levent=False):
        self.journal = journal
        self.destroy_leve = destroy_leve
        self.attributs_levent = attributs_levent
        self.attributs = []
        journal.append("Tk")

    def withdraw(self):
        self.journal.append("withdraw")

    def attributes(self, *args):
        self.journal.append("attributes")
        self.attributs.append(args)
        if self.attributs_levent:
            # Un Tk dont le gestionnaire de fenetres ignore -topmost.
            raise tkinter.TclError("attribut inconnu (simule)")

    def destroy(self):
        self.journal.append("destroy")
        if self.destroy_leve:
            # Un interpreteur deja parti : destroy() leve alors, et ce n'est
            # plus un probleme -- mais l'exception ne doit pas s'echapper.
            raise tkinter.TclError("interpreteur deja parti (simule)")


@pytest.fixture
def boite(monkeypatch):
    """Installe la doublure et le bouchon. Rend de quoi regler et observer.

    Aucun interpreteur Tcl n'est cree : voir la docstring du module pour la
    mesure qui a impose ce choix.
    """
    from tkinter import filedialog

    etat = {"journal": [], "rendu": "", "leve": None, "destroy_leve": False,
            "attributs_levent": False, "kw": {}, "racines": []}

    def fausse_Tk():
        r = FausseRacine(etat["journal"], etat["destroy_leve"],
                         etat["attributs_levent"])
        etat["racines"].append(r)
        return r

    def faux_dialogue(**kw):
        etat["journal"].append("askopenfilename")
        etat["kw"] = kw
        if etat["leve"] is not None:
            raise etat["leve"]
        return etat["rendu"]

    monkeypatch.setattr(tkinter, "Tk", fausse_Tk)
    monkeypatch.setattr(filedialog, "askopenfilename", faux_dialogue)
    return etat


def test_les_extensions_n_ont_pas_bouge():
    """Valeur reprise telle quelle de l'ancien explorateur web."""
    assert EXTENSIONS_VIDEO == (".mp4", ".mov", ".avi", ".mkv", ".m4v")


def test_le_filtre_est_construit_depuis_les_extensions():
    """Et non recopie a la main a cote : une extension ajoutee a la constante
    doit apparaitre dans la boite sans autre geste."""
    types = _types_de_fichiers()
    assert types[0][1] == "*.mp4 *.mov *.avi *.mkv *.m4v"
    # « Tous les fichiers » reste offert : le filtre porte sur l'EXTENSION,
    # et un conteneur que ffmpeg lit peut en porter une autre.
    assert types[-1][1] == "*"


def test_l_annulation_rend_None_et_non_la_chaine_vide(boite):
    """LE PIEGE DU MODULE. askopenfilename rend "" a l'annulation, pas None :
    sans conversion, l'appelant recoit une chaine vide et la prend pour un
    chemin -- le viewer repondrait 200 avec {"chemin": ""} et la page
    tenterait de choisir ce fichier-la."""
    boite["rendu"] = ""
    assert choisit_video() is None


def test_un_tuple_vide_rend_None_lui_aussi(boite):
    """Certaines plateformes rendent () plutot que "" a l'annulation. C'est
    ce que `or None` couvre, et le commentaire du module l'annonce : ce test
    est ce qui empeche l'annonce de devenir fausse."""
    boite["rendu"] = ()
    assert choisit_video() is None


def test_un_chemin_choisi_est_rendu_tel_quel(boite):
    boite["rendu"] = "D:/films/eclipse.mp4"
    assert choisit_video() == "D:/films/eclipse.mp4"


def test_le_dossier_initial_et_le_filtre_sont_transmis(boite):
    """Sinon la boite s'ouvre n'importe ou, et non a cote de la source
    courante -- l'endroit le plus probable de la suivante."""
    choisit_video("D:/films")
    assert boite["kw"]["initialdir"] == "D:/films"
    assert boite["kw"]["filetypes"] == _types_de_fichiers()
    # parent : sans lui, la boite n'est rattachee a rien et peut s'ouvrir
    # derriere les autres fenetres.
    assert boite["kw"]["parent"] is boite["racines"][0]


def test_le_titre_suit_la_langue_demandee(boite):
    """Le titre et les filtres de la boite s'affichent dans une fenetre du
    SYSTEME, jamais dans le DOM : aucune solution cote page ne les atteint,
    c'est pourquoi la langue doit leur parvenir ici, par choisit_video."""
    choisit_video(langue="en")
    assert boite["kw"]["title"] == langues.charge("en")["boite_titre"]


def test_la_langue_par_defaut_est_le_francais(boite):
    choisit_video()
    assert boite["kw"]["title"] == langues.charge("fr")["boite_titre"]


def test_les_filtres_suivent_la_langue_demandee(boite):
    choisit_video(langue="en")
    attendu = langues.charge("en")
    assert boite["kw"]["filetypes"] == [
        (attendu["boite_filtre_videos"], "*.mp4 *.mov *.avi *.mkv *.m4v"),
        (attendu["boite_filtre_tous"], "*")]


def test_une_langue_inconnue_replie_sur_le_francais(boite):
    """langues.charge leve FileNotFoundError pour une langue inconnue -- ici
    repliee sur le francais, parce qu'un choix d'interface ne doit pas
    empecher de designer un fichier. Le repli est verifie ATTEINT, pas
    seulement ecrit : sans cette assertion, une regression qui laisserait
    l'exception se propager casserait choisit_video("...", langue="de")
    sans qu'aucun test ne le remarque."""
    choisit_video(langue="de")
    assert boite["kw"]["title"] == langues.charge("fr")["boite_titre"]


def test_sans_dossier_initial_tcl_ne_recoit_pas_None(boite):
    """Tcl refuse None pour -initialdir. La chaine vide, elle, veut dire
    « decide toi-meme », ce qui est exactement l'intention."""
    choisit_video(None)
    assert boite["kw"]["initialdir"] == ""


def test_la_fenetre_est_retiree_avant_la_boite_et_detruite_apres(boite):
    """L'ORDRE, et non la seule presence des appels.

    withdraw APRES la boite laisserait une fenetre racine vide posee sur le
    bureau pendant toute la selection ; destroy avant elle detruirait
    l'interpreteur dont la boite depend. Un test qui se contenterait de
    compter les appels laisserait passer les deux.
    """
    choisit_video()
    assert boite["journal"] == ["Tk", "withdraw", "attributes",
                                "askopenfilename", "destroy"]


def test_aucune_racine_ne_survit_a_l_appel(boite):
    """L'invariant du module : le Tk est cree, utilise et detruit dans le
    MEME appel, donc dans le fil du gestionnaire HTTP. Une racine laissee
    vivante retiendrait son interpreteur Tcl dans un fil de requete qui, lui,
    va se terminer.

    Une seule racine par appel, detruite une seule fois -- pas zero (fuite),
    pas deux (double destruction)."""
    choisit_video()
    choisit_video()
    assert len(boite["racines"]) == 2
    assert boite["journal"].count("Tk") == 2
    assert boite["journal"].count("destroy") == 2


def test_aucune_racine_ne_survit_quand_la_boite_leve(boite):
    """Le meme invariant sur le chemin d'ECHEC. Sans le finally, une TclError
    pendant la boite laisserait la racine vivante, et l'appel suivant
    heriterait d'un interpreteur cree dans un autre fil."""
    boite["leve"] = tkinter.TclError("boite refusee (simulee)")
    with pytest.raises(Indisponible):
        choisit_video()
    assert boite["journal"] == ["Tk", "withdraw", "attributes",
                                "askopenfilename", "destroy"]


def test_un_destroy_qui_leve_ne_masque_pas_le_resultat(boite):
    """destroy() peut lever si l'interpreteur est deja parti -- ce n'est
    alors plus un probleme, et l'exception ne doit ni s'echapper ni effacer
    le chemin choisi. Branche que rien d'autre n'exerce."""
    boite["rendu"] = "D:/films/eclipse.mp4"
    boite["destroy_leve"] = True
    assert choisit_video() == "D:/films/eclipse.mp4"


def test_un_destroy_qui_leve_ne_masque_pas_l_indisponibilite(boite):
    """Et l'autre moitie : quand les DEUX levent, c'est Indisponible qui doit
    sortir -- l'appelant est un gestionnaire HTTP, une TclError nue lui
    donnerait une trace au lieu d'un statut."""
    boite["leve"] = tkinter.TclError("boite refusee (simulee)")
    boite["destroy_leve"] = True
    with pytest.raises(Indisponible):
        choisit_video()


def test_aucun_fil_ne_survit_a_l_appel(boite):
    """La contrainte du projet. Elle ne peut rien dire des fils que Tcl
    lui-meme creerait -- il n'y a pas de Tcl ici (voir la docstring du
    module) ; elle garde le code de ce depot."""
    fils_avant = set(threading.enumerate())
    choisit_video()
    boite["leve"] = tkinter.TclError("simulee")
    with pytest.raises(Indisponible):
        choisit_video()
    assert set(threading.enumerate()) == fils_avant


def test_la_boite_est_posee_au_dessus_des_autres_fenetres(boite):
    """LE DEFAUT SIGNALE : la boite s'ouvrait DERRIERE le navigateur.

    Elle nait dans un fil secondaire d'un processus console pendant que le
    navigateur tient le premier plan ; Windows ne la fait pas passer devant.

    MESURE, hors pytest, boite auto-fermee par le planificateur Tk, fenetre
    de premier plan et pile z lues par l'API du systeme, une autre
    application tenant le premier plan :

      sans rien ..................... boite SOUS l'autre fenetre, 3 fois /3
      avec -topmost ................. boite AU-DESSUS, 2 fois /2
      + lift + focus_force + update . identique a -topmost seul.

    Aucune variante n'obtient le premier plan (le focus clavier) : Windows
    le refuse a un processus qui n'a pas recu la derniere entree. -topmost
    seul est donc retenu -- le reste ne changeait rien de mesurable.

    Ce test garde l'APPEL et sa place ; que Windows le respecte est le fait
    mesure ci-dessus, qu'aucun test de ce depot ne peut rejouer.
    """
    choisit_video()
    racine = boite["racines"][0]
    assert racine.attributs == [("-topmost", True)]
    # AVANT la boite : pose apres, l'attribut arriverait une fois la boite
    # deja ouverte derriere -- c'est-a-dire trop tard.
    journal = boite["journal"]
    assert journal.index("attributes") < journal.index("askopenfilename")


def test_un_topmost_refuse_n_empeche_pas_de_choisir(boite):
    """Un gestionnaire de fenetres qui ignore -topmost coute un confort, pas
    la boite. Sans son propre rattrapage, la TclError tomberait dans celui de
    la boite et rendrait un 503 « boite indisponible » a un utilisateur dont
    la boite, elle, s'ouvre tres bien."""
    boite["attributs_levent"] = True
    boite["rendu"] = "D:/films/eclipse.mp4"
    assert choisit_video() == "D:/films/eclipse.mp4"
    assert boite["journal"] == ["Tk", "withdraw", "attributes",
                                "askopenfilename", "destroy"]


def test_un_Tk_qui_ne_demarre_pas_leve_Indisponible(monkeypatch):
    """Le cas « machine sans affichage », que cette machine-ci ne sait pas
    produire : la TclError est posee a la place de Tk(). Bouchon assume --
    il exerce NOTRE branche, pas le refus de Tcl.

    Sans elle, la TclError nue remonterait au gestionnaire HTTP, qui rendrait
    une trace socketserver et AUCUN statut.
    """
    def boum():
        raise tkinter.TclError("no display name (simule)")

    monkeypatch.setattr(tkinter, "Tk", boum)
    with pytest.raises(Indisponible):
        choisit_video()


def test_tkinter_absent_leve_Indisponible(monkeypatch):
    """L'autre porte : tkinter pas installe du tout. Rien n'est bouchonne ici
    -- le module est retire de sys.modules et son import interdit, ce qui est
    l'etat exact d'une installation sans Tcl/Tk."""
    reel = builtins.__import__

    def refuse(nom, *a, **kw):
        if nom == "tkinter" or nom.startswith("tkinter."):
            raise ImportError("No module named 'tkinter' (simule)")
        return reel(nom, *a, **kw)

    monkeypatch.delitem(sys.modules, "tkinter", raising=False)
    monkeypatch.delitem(sys.modules, "tkinter.filedialog", raising=False)
    monkeypatch.setattr(builtins, "__import__", refuse)
    with pytest.raises(Indisponible):
        choisit_video()


def test_on_macos_without_osascript_tkinter_is_never_touched(monkeypatch):
    """AppKit only allows windows on the MAIN thread, and choisit_video runs
    in an HTTP handler thread. There, tkinter.Tk() does not raise a TclError
    the except clauses could turn into a status: it ABORTS the whole process
    (NSInternalInconsistencyException — issue #4, macOS 26). osascript (see
    below) is the real fix, tried first; this is the one case it cannot
    cover, osascript itself missing, and there is no third way in yet. The
    guard must therefore fall back before tkinter is even imported; the
    bombed Tk proves the call never gets that far, and Indisponible (not
    AssertionError) proves the refusal takes the same path the page already
    knows how to display.
    """
    def boom():
        raise AssertionError("Tk must never be instantiated on macOS")

    monkeypatch.setattr(tkinter, "Tk", boom)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(shutil, "which", lambda nom: None)
    with pytest.raises(Indisponible) as exc:
        choisit_video()
    assert str(exc.value) == MESSAGE_MACOS


def test_the_macos_message_says_why():
    """Like MESSAGE_INDISPONIBLE below: the user reads it in the page, so it
    must name the platform and the reason. The WHAT TO DO is added by the
    page (key "boite_indisponible"), in its own language — not here."""
    assert "macOS" in MESSAGE_MACOS


def test_on_macos_osascript_is_tried_before_the_fallback(monkeypatch):
    """The real fix (issue #1, Mireia Nievas): osascript runs the panel in
    its own process, so it is reached even though choisit_video runs in the
    HTTP handler thread that tkinter.Tk() would abort on (see above). A
    chosen path comes back unchanged."""
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(shutil, "which", lambda nom: "/usr/bin/osascript")

    def faux_run(commande, capture_output, text, timeout):
        return subprocess.CompletedProcess(
            commande, 0, stdout="/Users/x/eclipse.mp4\n", stderr="")

    monkeypatch.setattr(subprocess, "run", faux_run)
    assert choisit_video() == "/Users/x/eclipse.mp4"


def test_on_macos_the_type_filter_comes_from_extensions_video(monkeypatch):
    """Pas une seconde liste tenue a la main a cote : une extension ajoutee a
    EXTENSIONS_VIDEO doit apparaitre dans le filtre osascript sans autre
    geste -- le meme principe que _types_de_fichiers pour tkinter. Les UTI
    "public.movie"/"public.video" restent en plus : ce sont elles qui font
    filtrer le panneau natif par nature de fichier."""
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(shutil, "which", lambda nom: "/usr/bin/osascript")
    scripts = []

    def faux_run(commande, capture_output, text, timeout):
        scripts.append(commande[-1])
        return subprocess.CompletedProcess(commande, 0, stdout="/x.mp4\n",
                                           stderr="")

    monkeypatch.setattr(subprocess, "run", faux_run)
    choisit_video()
    script = scripts[0]
    assert '"public.movie"' in script and '"public.video"' in script
    for extension in EXTENSIONS_VIDEO:
        assert '"%s"' % extension.lstrip(".") in script


def test_on_macos_a_user_cancel_returns_none_via_the_stderr_text(monkeypatch):
    """AppleScript reports a closed panel as a non-zero exit whose stderr
    names it "User canceled" — mireianievas's fork returned None here too,
    but so did every OTHER osascript failure, which is exactly what the
    HTTP caller's contract (Indisponible vs. None) cannot tolerate."""
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(shutil, "which", lambda nom: "/usr/bin/osascript")

    def faux_run(commande, capture_output, text, timeout):
        return subprocess.CompletedProcess(
            commande, 1, stdout="", stderr="execution error: User canceled. (-128)")

    monkeypatch.setattr(subprocess, "run", faux_run)
    assert choisit_video() is None


def test_on_macos_a_user_cancel_is_recognized_by_the_dash_128_code(monkeypatch):
    """The other spelling osascript uses for the same cancel, seen without
    the English wording on some locales/OS versions."""
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(shutil, "which", lambda nom: "/usr/bin/osascript")

    def faux_run(commande, capture_output, text, timeout):
        return subprocess.CompletedProcess(commande, 1, stdout="",
                                           stderr="(-128)")

    monkeypatch.setattr(subprocess, "run", faux_run)
    assert choisit_video() is None


def test_a_dash_128_substring_in_an_unrelated_code_is_not_a_cancel(monkeypatch):
    """LOOSE MATCH TIGHTENED. Matching the bare digits "-128" would also
    catch an unrelated OSStatus that merely contains that substring --
    -1280 does, digit for digit, without being the cancel code -- silently
    turning a real failure into a cancel. Only the parenthesized form
    osascript actually emits, "(-128)", counts: "(-1280)" contains the
    bare substring "-128" but not "(-128)", so the old loose match would
    have wrongly returned None here, and this must still raise
    Indisponible."""
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(shutil, "which", lambda nom: "/usr/bin/osascript")

    def faux_run(commande, capture_output, text, timeout):
        return subprocess.CompletedProcess(
            commande, 1, stdout="",
            stderr="execution error: some failure (-1280)")

    monkeypatch.setattr(subprocess, "run", faux_run)
    with pytest.raises(Indisponible) as exc:
        choisit_video()
    assert "-1280" in str(exc.value)


def test_on_macos_a_real_osascript_failure_raises_indisponible(monkeypatch):
    """LE PIEGE DU FORK D'ORIGINE : une erreur qui n'a rien a voir avec une
    annulation (ici, un droit refuse) y rendait None comme un simple
    abandon -- l'appelant HTTP n'aurait jamais su que la boite n'a pas pu
    s'ouvrir. Ici, seul le texte d'annulation rend None ; tout le reste leve
    Indisponible, avec le detail utile a l'intérieur."""
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(shutil, "which", lambda nom: "/usr/bin/osascript")

    def faux_run(commande, capture_output, text, timeout):
        return subprocess.CompletedProcess(
            commande, 1, stdout="", stderr="not authorized to send Apple events")

    monkeypatch.setattr(subprocess, "run", faux_run)
    with pytest.raises(Indisponible) as exc:
        choisit_video()
    assert "not authorized to send Apple events" in str(exc.value)


def test_on_macos_osascript_missing_at_launch_raises_indisponible(monkeypatch):
    """shutil.which l'avait trouve, mais le lancer echoue quand meme (retire
    entre-temps, permissions, ...) : une infrastructure defaillante, pas un
    choix de l'utilisateur -- Indisponible, pas None."""
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(shutil, "which", lambda nom: "/usr/bin/osascript")

    def faux_run(commande, capture_output, text, timeout):
        raise FileNotFoundError("osascript disparu (simule)")

    monkeypatch.setattr(subprocess, "run", faux_run)
    with pytest.raises(Indisponible) as exc:
        choisit_video()
    assert "osascript disparu (simule)" in str(exc.value)


def test_on_macos_a_stalled_osascript_raises_indisponible(monkeypatch):
    """No timeout would let a stalled osascript -- a TCC/Automation prompt
    hidden behind another window, or Apple events denied outright -- hang
    the HTTP handler thread forever, with no visible dialog and no way
    out. subprocess.run is given DELAI_OSASCRIPT_S and TimeoutExpired must
    turn into the same Indisponible any other infrastructure failure
    does."""
    from eclipse.dialogue import DELAI_OSASCRIPT_S

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(shutil, "which", lambda nom: "/usr/bin/osascript")

    def faux_run(commande, capture_output, text, timeout):
        assert timeout == DELAI_OSASCRIPT_S
        raise subprocess.TimeoutExpired(commande, timeout)

    monkeypatch.setattr(subprocess, "run", faux_run)
    with pytest.raises(Indisponible):
        choisit_video()


def test_on_macos_a_double_quote_in_the_folder_name_is_escaped(monkeypatch):
    """F1, CRITICAL. A folder name containing a double quote must arrive
    ESCAPED in the generated AppleScript, or it breaks out of the string
    literal -- an injection that could run arbitrary AppleScript (e.g. via
    `do shell script`). The previous code escaped the quote and only then
    replaced every backslash with "/", which erased the escape it had just
    inserted; this asserts the fix by inspecting the actual osascript
    argument, not just the visible behavior."""
    import os

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(shutil, "which", lambda nom: "/usr/bin/osascript")
    monkeypatch.setattr(os.path, "isdir", lambda chemin: True)
    scripts = []

    def faux_run(commande, capture_output, text, timeout):
        scripts.append(commande[-1])
        return subprocess.CompletedProcess(commande, 0, stdout="/x.mp4\n",
                                           stderr="")

    monkeypatch.setattr(subprocess, "run", faux_run)
    dossier = 'D:/films/a " malicious " folder'
    choisit_video(dossier)
    script = scripts[0]
    # Le guillemet du nom de dossier doit atteindre le script ECHAPPE : sa
    # forme brute ("a " malicious " folder) romprait le litteral AppleScript
    # a la premiere occurrence.
    assert 'a \\" malicious \\" folder' in script
    assert 'a " malicious " folder' not in script


def test_on_macos_the_default_location_clause_uses_the_folder(monkeypatch):
    """The normal-path case: no quotes to escape, just the expected
    `default location POSIX file "..."` clause built from dossier_initial."""
    import os

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(shutil, "which", lambda nom: "/usr/bin/osascript")
    monkeypatch.setattr(os.path, "isdir", lambda chemin: True)
    scripts = []

    def faux_run(commande, capture_output, text, timeout):
        scripts.append(commande[-1])
        return subprocess.CompletedProcess(commande, 0, stdout="/x.mp4\n",
                                           stderr="")

    monkeypatch.setattr(subprocess, "run", faux_run)
    choisit_video("D:/films")
    assert 'default location POSIX file "D:/films"' in scripts[0]


def test_le_message_d_indisponibilite_dit_pourquoi():
    """Il est lu par l'utilisateur, dans la page : « Indisponible » nu ne lui
    apprendrait rien. Le QUE FAIRE, lui, est ajoute par le viewer, seul a
    connaitre la forme de la ligne de commande -- pas par une constante
    CONSEIL_SANS_BOITE, supprimee par 0b5a068 : voir viewer._parcourir, qui
    enveloppe str(exc) dans un fait {"code": "boite_indisponible", "detail":
    ...} que la page compose dans sa langue (cle "boite_indisponible")."""
    assert "tkinter" in MESSAGE_INDISPONIBLE
