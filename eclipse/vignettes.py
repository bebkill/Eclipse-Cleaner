"""Vignettes de toutes les frames source, pour la revue.

Le rendu n'exporte que les frames retenues ; or revoir ce qui a ete ecarte
suppose de le voir. On genere donc une vignette par frame source, y compris
rejetee.

Un seul processus ffmpeg produit toute la sequence : en lancer un par image
couterait 2556 processus.
"""
import json
import os
import subprocess

from .io import ffmpeg_exe

DOSSIER_DEFAUT = ".vignettes"
LARGEUR = 320
_MARQUEUR = "source.json"


def chemin_vignette(dossier, n):
    """Les vignettes sont numerotees a partir de 1 par ffmpeg."""
    return os.path.join(dossier, f"v-{n + 1:05d}.jpg")


def _marqueur(dossier):
    return os.path.join(dossier, _MARQUEUR)


def _a_jour(dossier, signature):
    try:
        with open(_marqueur(dossier), encoding="utf-8") as f:
            return json.load(f) == signature
    except (OSError, json.JSONDecodeError):
        return False


def a_jour(dossier, signature):
    """Vrai si les vignettes de dossier appartiennent bien a cette source.

    Compare le marqueur source.json a la signature courante. Sans ce
    controle, un dossier de vignettes issu d'une autre source (meme nombre
    de frames par coincidence) passerait pour a jour : compte() seul ne
    regarde que le nombre de fichiers, jamais leur provenance.
    """
    return _a_jour(dossier, signature)


class Interrompue(Exception):
    """La generation a ete interrompue avant d'avoir fini."""


def compte(dossier):
    """Nombre de vignettes deja ecrites. Sert de mesure d'avancement.

    genere() delegue a un unique processus ffmpeg, sans boucle Python ou
    accrocher un compteur : on compte donc les fichiers. C'est grossier, mais
    ffmpeg les ecrit dans l'ordre.
    """
    try:
        return len([n for n in os.listdir(dossier) if n.endswith(".jpg")])
    except OSError:
        return 0


def genere(source, dossier, signature, arret=None):
    """Genere les vignettes si besoin ; retourne leur nombre.

    arret : threading.Event optionnel. S'il est leve, le processus ffmpeg est
    termine et Interrompue est levee. Le marqueur n'etant ecrit qu'en cas de
    succes, la generation interrompue sera refaite au prochain lancement.
    """
    if _a_jour(dossier, signature):
        return compte(dossier)

    os.makedirs(dossier, exist_ok=True)
    for nom in os.listdir(dossier):
        if nom.endswith(".jpg"):
            os.remove(os.path.join(dossier, nom))

    cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
           "-i", source, "-vf", f"scale={LARGEUR}:-2", "-q:v", "5",
           os.path.join(dossier, "v-%05d.jpg")]
    proc = subprocess.Popen(cmd, stderr=subprocess.DEVNULL)
    try:
        while True:
            if arret is not None and arret.is_set():
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                raise Interrompue("generation des vignettes annulee")
            try:
                code = proc.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                continue
            break
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    if code != 0:
        raise subprocess.CalledProcessError(code, cmd)

    with open(_marqueur(dossier), "w", encoding="utf-8") as f:
        json.dump(signature, f)
    return compte(dossier)
