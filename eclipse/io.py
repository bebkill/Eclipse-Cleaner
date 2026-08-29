"""Frontiere ffmpeg : lecture et ecriture de flux video bruts.

Le binaire ffmpeg vient de imageio_ffmpeg. Il n'est pas sur le PATH de la
machine cible : ne jamais invoquer "ffmpeg" par son nom nu.
"""
import os
import re
import subprocess

import numpy as np


def ffmpeg_exe():
    """Chemin du binaire ffmpeg fourni par imageio-ffmpeg."""
    try:
        import imageio_ffmpeg
    except ImportError as exc:
        raise RuntimeError(
            "imageio-ffmpeg est introuvable. Installer les dependances : "
            "python -m pip install -e ."
        ) from exc
    return imageio_ffmpeg.get_ffmpeg_exe()


def _analyse_sortie_ffmpeg(texte):
    """Extrait dimensions, cadence et duree de la sortie d'analyse de ffmpeg.

    Fonction pure, separee de probe() pour etre testable sur des sorties
    reelles sans avoir a fabriquer les fichiers correspondants.
    """
    # Beaucoup d'appareils embarquent une vignette JPEG, qui apparait comme
    # un flux video AVANT le vrai. Prendre le premier flux venu donnerait
    # les dimensions de la vignette, et tout le pipeline travaillerait a la
    # mauvaise taille.
    lignes = [ligne for ligne in texte.splitlines()
              if "Video:" in ligne and "attached pic" not in ligne]
    if not lignes:
        raise ValueError("Aucun flux video lisible")
    ligne = lignes[0]

    # L'ancrage sur ",\s*" est necessaire : sans lui, le motif accrocherait
    # l'identifiant hexadecimal du codec (0x31637661).
    m = re.search(r"Video:.*?,\s*(\d+)x(\d+)", ligne)
    if not m:
        raise ValueError("Dimensions illisibles")
    largeur, hauteur = int(m.group(1)), int(m.group(2))

    # Cadence lue sur la ligne du flux retenu, pas sur tout le texte.
    m = re.search(r"([\d.]+)\s*fps", ligne)
    fps = float(m.group(1)) if m else 30.0

    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", texte)
    duree = (int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
             ) if m else 0.0

    return {"width": largeur, "height": hauteur, "fps": fps, "duration": duree}


def probe(path):
    """Dimensions, cadence et duree, lues sur la sortie d'analyse de ffmpeg."""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Fichier introuvable : {path}")

    res = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", path],
                         capture_output=True, text=True, errors="replace",
                         timeout=60)
    try:
        return _analyse_sortie_ffmpeg(res.stderr)
    except ValueError as exc:
        raise ValueError(f"{exc} dans {path}") from exc


def _ferme_processus(proc):
    """Termine un ffmpeg lecteur sans risque de blocage.

    Fermer stdout avant d'attendre est ce qui debloque un ffmpeg arrete sur
    un tube plein : il recoit une erreur d'ecriture et sort. Attendre en
    premier attendrait indefiniment. Les deux operations sont idempotentes,
    donc un double appel est sans effet.
    """
    if proc.stdout is not None:
        proc.stdout.close()
    proc.wait()


class FrameReader:
    """Itere les frames d'une video en RGB uint8, avec redimensionnement."""

    def __init__(self, path, width=None, height=None):
        info = probe(path)
        self.path = path
        self.width = int(width) if width else info["width"]
        self.height = int(height) if height else info["height"]
        self.fps = info["fps"]
        self._procs = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def __iter__(self):
        cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
               "-i", self.path,
               "-vf", f"scale={self.width}:{self.height}",
               "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]
        # Le processus appartient a CE generateur, pas a l'instance : deux
        # iterations simultanees sur le meme lecteur doivent rester
        # etanches, sinon la premiere lirait dans le tube de la seconde.
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, bufsize=10 ** 8)
        self._procs.append(proc)
        taille = self.width * self.height * 3
        try:
            while True:
                buf = proc.stdout.read(taille)
                if len(buf) < taille:
                    break
                yield np.frombuffer(buf, np.uint8).reshape(
                    self.height, self.width, 3)
        finally:
            _ferme_processus(proc)
            if proc in self._procs:
                self._procs.remove(proc)

    def close(self):
        for proc in list(self._procs):
            _ferme_processus(proc)
        self._procs.clear()


def _filtre_agrandissement(taille_encodage):
    """Options ffmpeg -vf scale=...:flags=lanczos, ou liste vide si absent.

    Partagee par FrameWriter et PngSequenceWriter : les deux doivent pouvoir
    restituer le format d'origine a partir d'une fenetre recadree plus
    petite, et rendre visuellement la meme chose. lanczos est choisi pour
    preserver la nettete du limbe lors de cet agrandissement.
    """
    if taille_encodage is None:
        return []
    ew, eh = int(taille_encodage[0]), int(taille_encodage[1])
    return ["-vf", f"scale={ew}:{eh}:flags=lanczos"]


class FrameWriter:
    """Encode des frames RGB uint8 en H.264.

    taille_encodage : (largeur, hauteur) de sortie, si differente de
    (width, height). Les frames continuent d'etre ecrites en width x height ;
    c'est ffmpeg qui reechantillonne via un filtre scale. Sert a restituer le
    format d'origine a partir d'une fenetre recadree plus petite.
    """

    def __init__(self, path, width, height, fps=30.0, crf=16, preset="slow",
                 taille_encodage=None):
        self.width, self.height = int(width), int(height)
        cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
               "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", f"{self.width}x{self.height}", "-r", str(fps), "-i", "-"]
        cmd += _filtre_agrandissement(taille_encodage)
        cmd += ["-c:v", "libx264", "-preset", preset, "-crf", str(crf),
               "-pix_fmt", "yuv420p", path]
        self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def write(self, frame):
        if self._proc is None:
            raise ValueError("Ecriture apres fermeture de l'encodeur")
        if frame.shape != (self.height, self.width, 3):
            raise ValueError(
                f"Frame de forme {frame.shape}, attendu "
                f"{(self.height, self.width, 3)}"
            )
        self._proc.stdin.write(np.ascontiguousarray(frame, dtype=np.uint8).tobytes())

    def close(self):
        if self._proc is not None:
            self._proc.stdin.close()
            self._proc.wait()
            self._proc = None


class PngSequenceWriter:
    """Ecrit une sequence PNG numerotee, un seul processus ffmpeg.

    Lancer un ffmpeg par image couterait un processus par frame ; le
    motif %05d laisse ffmpeg numeroter lui-meme.

    taille_encodage : comme pour FrameWriter, agrandit chaque PNG vers cette
    taille au lieu de width x height. Sert a faire correspondre exactement
    la sequence PNG a la video, qui est elle aussi agrandie vers la sortie
    finale a partir de la meme fenetre recadree.
    """

    def __init__(self, dossier, width, height, prefixe="frame",
                 taille_encodage=None):
        os.makedirs(dossier, exist_ok=True)
        self.dossier = dossier
        self.width, self.height = int(width), int(height)
        motif = os.path.join(dossier, f"{prefixe}-%05d.png")
        cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
               "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", f"{self.width}x{self.height}", "-i", "-"]
        cmd += _filtre_agrandissement(taille_encodage)
        cmd += [motif]
        self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def write(self, frame):
        if self._proc is None:
            raise ValueError("Ecriture apres fermeture de la sequence")
        if frame.shape != (self.height, self.width, 3):
            raise ValueError(
                f"Frame de forme {frame.shape}, attendu "
                f"{(self.height, self.width, 3)}"
            )
        self._proc.stdin.write(np.ascontiguousarray(frame, dtype=np.uint8).tobytes())

    def close(self):
        if self._proc is not None:
            self._proc.stdin.close()
            self._proc.wait()
            self._proc = None
