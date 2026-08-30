"""Verification de l'executable gele, en conditions reelles.

Usage : python packaging/smoke_exe.py dist/eclipse-cleaner.exe

Trois epreuves, chacune visant un risque precis du gel :

1. `run` sur une video synthetique avec --processus 2 : le protocole
   multiprocessing des executables geles (freeze_support) et le binaire
   ffmpeg embarque, en lecture comme en ecriture.
2. `--help` d'une sous-commande : le demarrage nu et argparse.
3. le viewer interroge en HTTP : les ressources embarquees (viewer.html,
   tables de langues) retrouvees depuis l'archive du gel.

La video d'essai est fabriquee par le code SOURCE du depot (eclipse.io,
tests.synth), pas par l'exe : si elle est fausse, c'est le depot qui est
faux, pas le gel.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RACINE)

from eclipse.io import FrameWriter, probe          # noqa: E402
from tests.synth import make_frame                 # noqa: E402

PORT = 8641
NB_FRAMES = 36


def _echec(message):
    print(f"SMOKE ECHEC : {message}")
    return 1


def _fabrique_video(chemin):
    with FrameWriter(chemin, 270, 480, fps=30.0, preset="veryfast") as w:
        for i in range(NB_FRAMES):
            # Un leger tremblement : le stabilisateur a quelque chose a faire.
            w.write(make_frame(center=(135.0 + 3.0 * (i % 3 - 1),
                                       240.0 + 2.0 * (i % 2)), r=60.0))


def _attend_viewer(url, delai=30.0):
    fin = time.monotonic() + delai
    while time.monotonic() < fin:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                return r.status, r.read()
        except OSError:
            time.sleep(0.5)
    return None, b""


def main():
    if len(sys.argv) != 2:
        return _echec("usage : smoke_exe.py <chemin de l'exe>")
    exe = os.path.abspath(sys.argv[1])
    if not os.path.isfile(exe):
        return _echec(f"exe introuvable : {exe}")

    r = subprocess.run([exe, "analyze", "--help"], capture_output=True,
                       text=True, timeout=120)
    if r.returncode != 0:
        return _echec(f"analyze --help : code {r.returncode}\n{r.stderr}")
    print("SMOKE ok : demarrage et argparse")

    # ignore_cleanup_errors : sous Windows, le fichier de sortie peut rester
    # tenu un instant apres la fin des processus ; un temporaire orphelin
    # dans %TEMP% ne vaut pas un echec du smoke test.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        entree = os.path.join(d, "in.mp4")
        sortie = os.path.join(d, "out.mp4")
        _fabrique_video(entree)

        r = subprocess.run([exe, "run", entree, sortie, "--radius", "60",
                            "--processus", "2",
                            "--cache", os.path.join(d, "analysis.json")],
                           capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            return _echec(f"run : code {r.returncode}\n{r.stdout}\n{r.stderr}")
        if not os.path.isfile(sortie):
            return _echec("run : pas de fichier de sortie")
        info = probe(sortie)
        if info["width"] <= 0 or info["height"] <= 0:
            return _echec(f"run : sortie illisible ({info})")
        print(f"SMOKE ok : run --processus 2, sortie "
              f"{info['width']}x{info['height']}")

        proc = subprocess.Popen([exe, "viewer", "--port", str(PORT),
                                 "--cache", os.path.join(d, "analysis.json")],
                                cwd=d, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        try:
            statut, page = _attend_viewer(f"http://127.0.0.1:{PORT}/")
            if statut != 200 or b"<html" not in page.lower():
                return _echec(f"viewer : page racine statut {statut}")
            statut, corps = _attend_viewer(
                f"http://127.0.0.1:{PORT}/api/langues", delai=10.0)
            langues = json.loads(corps or b"{}")
            if statut != 200 or "fr" not in langues or "en" not in langues:
                return _echec(f"viewer : /api/langues statut {statut}, "
                              f"cles {sorted(langues)}")
            print("SMOKE ok : viewer servi, ressources embarquees presentes")
        finally:
            # En onefile, le bootloader est un PARENT qui extrait l'archive
            # puis se relance en enfant ; terminate() ne tuerait que le
            # parent, et l'enfant survivant garderait son cwd (ce dossier
            # temporaire) ouvert. taskkill /T abat l'arbre entier.
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/T", "/F", "/PID",
                                str(proc.pid)], capture_output=True)
            else:
                proc.terminate()
            proc.wait(timeout=30)

    print("SMOKE : tout est passe")
    return 0


if __name__ == "__main__":
    sys.exit(main())
