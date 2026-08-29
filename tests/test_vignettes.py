import os
import threading
import time

import pytest

from eclipse import vignettes
from eclipse.io import FrameWriter
from eclipse.vignettes import Interrompue, LARGEUR, chemin_vignette, compte, genere
from tests.synth import make_frame

SIG = {"path": "/x.mp4", "size": 1, "mtime": 2}


def _video(tmp_path, n=12):
    p = str(tmp_path / "src.mp4")
    with FrameWriter(p, width=64, height=96, fps=30.0) as w:
        for i in range(n):
            w.write(make_frame(w=64, h=96, center=(32.0, 48.0 + i), r=14.0))
    return p


def test_genere_une_vignette_par_frame(tmp_path):
    dossier = str(tmp_path / "v")
    assert genere(_video(tmp_path), dossier, SIG) == 12
    assert os.path.isfile(chemin_vignette(dossier, 0))
    assert os.path.isfile(chemin_vignette(dossier, 11))


def test_les_vignettes_sont_a_la_largeur_voulue(tmp_path):
    from eclipse.io import probe
    dossier = str(tmp_path / "v")
    genere(_video(tmp_path), dossier, SIG)
    assert probe(chemin_vignette(dossier, 0))["width"] == LARGEUR


def test_ne_regenere_pas_si_a_jour(tmp_path):
    dossier = str(tmp_path / "v")
    src = _video(tmp_path)
    genere(src, dossier, SIG)
    avant = os.path.getmtime(chemin_vignette(dossier, 0))
    assert genere(src, dossier, SIG) == 12
    assert os.path.getmtime(chemin_vignette(dossier, 0)) == avant


def test_regenere_si_la_source_a_change(tmp_path):
    dossier = str(tmp_path / "v")
    src = _video(tmp_path)
    genere(src, dossier, SIG)
    # Ecrire une sentinelle dans la premiere vignette
    vignette_0 = chemin_vignette(dossier, 0)
    with open(vignette_0, "wb") as f:
        f.write(b"sentinelle")
    # Regenerer avec une signature differente
    assert genere(src, dossier, dict(SIG, size=999)) == 12
    # Verifier que la vignette a ete regeneree (plus la sentinelle, mais un JPEG)
    with open(vignette_0, "rb") as f:
        contenu = f.read()
    assert contenu.startswith(b"\xff\xd8")  # Entete JPEG
    assert len(contenu) > 10  # Plus grand que la sentinelle


def test_compte_rend_zero_sur_un_dossier_absent(tmp_path):
    assert compte(str(tmp_path / "pas-la")) == 0


def test_compte_rend_le_nombre_de_jpg(tmp_path):
    d = tmp_path / "v"
    d.mkdir()
    for n in ("v-00001.jpg", "v-00002.jpg", "source.json"):
        (d / n).write_bytes(b"x")
    assert compte(str(d)) == 2


def test_arret_deja_leve_interrompt_sans_ecrire_le_marqueur(tmp_path):
    """Un arret leve avant le lancement doit interrompre tout de suite.

    Le marqueur n'etant ecrit qu'en cas de succes, une generation interrompue
    sera automatiquement refaite au prochain lancement — propriete qui existe
    deja et qu'il ne faut pas casser.
    """
    dossier = str(tmp_path / "v")
    arret = threading.Event()
    arret.set()
    with pytest.raises(Interrompue):
        genere(_video(tmp_path), dossier, SIG, arret=arret)
    assert not os.path.isfile(os.path.join(dossier, "source.json"))


@pytest.mark.skipif(os.name != "nt",
                    reason="remplace ffmpeg par un script .bat, propre a Windows")
def test_arret_leve_en_cours_interrompt_un_processus_vivant(tmp_path, monkeypatch):
    """Un arret leve pendant que le processus tourne doit aussi interrompre.

    Le test precedent leve arret avant meme le lancement : la toute premiere
    iteration de la boucle voit le drapeau deja leve et ne passe jamais par
    l'attente. Celui-ci verifie le chemin qui reste sinon non couvert :
    plusieurs tours de boucle (poll, TimeoutExpired, continue) pendant qu'un
    vrai processus enfant tourne encore, puis une terminaison reelle de ce
    processus par terminate().

    On ne peut pas se fier a la vitesse de decodage d'un vrai ffmpeg pour
    caler ce moment sans course : un ffmpeg sur une video synthetique
    minuscule peut finir avant que le fil d'annulation n'ait leve le
    drapeau, et le test passerait alors pour la mauvaise raison (chemin
    normal, pas annulation). On remplace donc ffmpeg par un faux programme
    controlable : un script .bat qui ecrit un fichier temoin des son
    demarrage, puis boucle indefiniment avec des instructions internes a
    cmd.exe (goto), sans lancer le moindre sous-processus — donc sans
    risque de laisser un enfant orphelin si on tue le processus principal.
    Le fil d'annulation attend ce temoin (synchronisation deterministe),
    ce qui garantit que genere() est encore dans sa boucle de sondage quand
    le drapeau est leve.
    """
    dossier = str(tmp_path / "v")
    temoin = tmp_path / "demarre.txt"
    faux_ffmpeg = tmp_path / "faux_ffmpeg.bat"
    faux_ffmpeg.write_text(
        "@echo off\r\n"
        f'echo demarre> "{temoin}"\r\n'
        ":boucle\r\n"
        "goto boucle\r\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(vignettes, "ffmpeg_exe", lambda: str(faux_ffmpeg))

    arret = threading.Event()

    def _guette_et_leve():
        for _ in range(100):  # jusqu'a 5 s, par pas de 50 ms
            if temoin.exists():
                break
            time.sleep(0.05)
        arret.set()

    guetteur = threading.Thread(target=_guette_et_leve)
    guetteur.start()
    debut = time.time()
    with pytest.raises(Interrompue):
        genere(_video(tmp_path), dossier, SIG, arret=arret)
    duree = time.time() - debut

    guetteur.join(timeout=5)
    assert not guetteur.is_alive()
    assert temoin.exists()  # le faux ffmpeg a bien demarre avant d'etre tue
    assert not os.path.isfile(os.path.join(dossier, "source.json"))
    # Sur Windows, TerminateProcess ne peut pas etre ignore : l'attente ne
    # doit pas avoir bascule sur le filet kill() apres les 10 s de
    # proc.wait(timeout=10).
    assert duree < 5.0
