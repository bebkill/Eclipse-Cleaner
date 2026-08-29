import os

import numpy as np
import pytest
from eclipse.io import (ffmpeg_exe, probe, FrameReader, FrameWriter,
                        PngSequenceWriter, _analyse_sortie_ffmpeg)
from tests.synth import make_frame

# Sortie reelle de ffmpeg 7.1 sur un fichier portant une vignette embarquee.
SORTIE_AVEC_VIGNETTE = """Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'x.mp4':
  Metadata:
    major_brand     : isom
  Duration: 00:01:25.20, start: 0.000000, bitrate: 7671 kb/s
  Stream #0:0[0x1]: Video: mjpeg (Baseline), yuvj420p(pc), 320x240 [SAR 1:1 DAR 4:3], 90k tbr, 90k tbn (attached pic)
  Stream #0:1[0x2](und): Video: h264 (Main) (avc1 / 0x31637661), yuv420p(progressive), 1080x1920, 7669 kb/s, 30.01 fps, 30 tbr, 90k tbn (default)
"""


def test_ffmpeg_exe_existe():
    import os
    assert os.path.isfile(ffmpeg_exe())


@pytest.fixture
def video_test(tmp_path):
    """Petite video ecrite avec FrameWriter, relue par FrameReader."""
    chemin = tmp_path / "test.mp4"
    with FrameWriter(str(chemin), width=64, height=96, fps=30.0) as w:
        for i in range(20):
            w.write(make_frame(w=64, h=96, center=(32.0 + i * 0.5, 48.0), r=14.0))
    return str(chemin)


def test_probe_retourne_les_dimensions(video_test):
    info = probe(video_test)
    assert info["width"] == 64
    assert info["height"] == 96
    assert abs(info["fps"] - 30.0) < 0.5


def test_reader_rend_le_bon_nombre_de_frames(video_test):
    with FrameReader(video_test) as r:
        frames = list(r)
    assert len(frames) == 20


def test_reader_rend_du_uint8_rgb(video_test):
    with FrameReader(video_test) as r:
        frame = next(iter(r))
    assert frame.shape == (96, 64, 3)
    assert frame.dtype == np.uint8


def test_reader_redimensionne(video_test):
    with FrameReader(video_test, width=32, height=48) as r:
        frame = next(iter(r))
    assert frame.shape == (48, 32, 3)
    assert r.width == 32 and r.height == 48


def test_aller_retour_preserve_l_image_approximativement(video_test):
    """H.264 est destructif : on verifie la structure, pas l'exactitude."""
    original = make_frame(w=64, h=96, center=(32.0, 48.0), r=14.0)
    with FrameReader(video_test) as r:
        relu = next(iter(r))
    ecart = np.abs(relu.astype(np.float32) - original.astype(np.float32)).mean()
    assert ecart < 12.0


def test_reader_sur_un_fichier_absent():
    with pytest.raises(FileNotFoundError):
        FrameReader("n_existe_pas.mp4")


def test_writer_refuse_une_frame_de_mauvaise_taille(tmp_path):
    chemin = tmp_path / "x.mp4"
    with pytest.raises(ValueError):
        with FrameWriter(str(chemin), width=64, height=96) as w:
            w.write(np.zeros((10, 10, 3), np.uint8))


def test_writer_refuse_d_ecrire_apres_fermeture(tmp_path):
    """Doit echouer avec un message clair, pas sur un attribut absent."""
    w = FrameWriter(str(tmp_path / "y.mp4"), width=64, height=96)
    w.write(make_frame(w=64, h=96, center=(32.0, 48.0), r=14.0))
    w.close()
    with pytest.raises(ValueError, match="apres fermeture"):
        w.write(make_frame(w=64, h=96, center=(32.0, 48.0), r=14.0))


def test_probe_retourne_la_duree(video_test):
    """20 frames a 30 fps."""
    assert abs(probe(video_test)["duration"] - 20 / 30.0) < 0.2


def test_analyse_ignore_une_vignette_embarquee():
    """Le premier flux video peut etre une vignette JPEG de 320x240.

    La retenir ferait tourner tout le pipeline a la mauvaise taille.
    """
    info = _analyse_sortie_ffmpeg(SORTIE_AVEC_VIGNETTE)
    assert info["width"] == 1080
    assert info["height"] == 1920
    assert abs(info["fps"] - 30.01) < 1e-6
    assert abs(info["duration"] - 85.2) < 1e-6


def test_analyse_sans_flux_video():
    with pytest.raises(ValueError):
        _analyse_sortie_ffmpeg("Input #0\n  Stream #0:0: Audio: aac, 48000 Hz\n")


def test_reader_abandonne_tot_ne_bloque_pas(tmp_path):
    """Tache 8 abandonne le generateur apres 300 frames pour estimer le rayon.

    Un lecteur qui se bloquerait sur un tube plein transformerait ca en
    pipeline fige. Ce test ne s'acheve pas si le blocage revient.
    """
    chemin = str(tmp_path / "long.mp4")
    with FrameWriter(chemin, width=64, height=96, fps=30.0) as w:
        for i in range(200):
            w.write(make_frame(w=64, h=96, center=(32.0, 48.0 + i * 0.1), r=14.0))

    with FrameReader(chemin) as r:                 # abandon dans un with
        for i, _ in enumerate(r):
            if i >= 5:
                break

    lecteur = FrameReader(chemin)                  # abandon hors with
    gen = iter(lecteur)
    for i, _ in enumerate(gen):
        if i >= 5:
            break
    gen.close()
    lecteur.close()
    lecteur.close()                                # doit rester sans effet


def test_deux_iterations_simultanees_restent_etanches(tmp_path):
    """Deux generateurs sur un meme lecteur ne doivent pas se voler leur tube.

    L'entrelacement est necessaire pour discriminer : si les deux
    generateurs partagent un tube unique, leurs lectures se consomment
    mutuellement et la deuxieme frame de b devient la TROISIEME du fichier.
    Comparer a et b entre eux ne montrerait rien, les deux lisant le meme
    fichier.
    """
    chemin = str(tmp_path / "deux.mp4")
    with FrameWriter(chemin, width=64, height=96, fps=30.0) as w:
        for i in range(30):
            w.write(make_frame(w=64, h=96, center=(20.0 + i, 48.0), r=10.0))

    with FrameReader(chemin) as ref:
        attendues = list(ref)

    with FrameReader(chemin) as r:
        a, b = iter(r), iter(r)
        assert np.array_equal(next(a), attendues[0])
        assert np.array_equal(next(b), attendues[0])
        assert np.array_equal(next(a), attendues[1])
        assert np.array_equal(next(b), attendues[1])
        a.close()
        b.close()


def test_png_sequence_ecrit_un_fichier_par_frame(tmp_path):
    dossier = str(tmp_path / "frames")
    with PngSequenceWriter(dossier, width=64, height=96) as w:
        for i in range(5):
            w.write(make_frame(w=64, h=96, center=(32.0, 48.0), r=14.0, phase=i * 0.2))
    fichiers = sorted(os.listdir(dossier))
    assert fichiers == [f"frame-{i:05d}.png" for i in range(1, 6)]
    assert all(os.path.getsize(os.path.join(dossier, f)) > 0 for f in fichiers)


def test_png_sequence_refuse_une_frame_de_mauvaise_taille(tmp_path):
    with pytest.raises(ValueError):
        with PngSequenceWriter(str(tmp_path / "f"), width=64, height=96) as w:
            w.write(np.zeros((10, 10, 3), np.uint8))


def test_writer_agrandit_vers_la_taille_demandee(tmp_path):
    chemin = str(tmp_path / "gros.mp4")
    with FrameWriter(chemin, width=64, height=96, fps=30.0,
                     taille_encodage=(128, 192)) as w:
        for _ in range(5):
            w.write(make_frame(w=64, h=96, center=(32.0, 48.0), r=14.0))
    info = probe(chemin)
    assert (info["width"], info["height"]) == (128, 192)


def test_png_sequence_refuse_d_ecrire_apres_fermeture(tmp_path):
    w = PngSequenceWriter(str(tmp_path / "f"), width=64, height=96)
    w.write(make_frame(w=64, h=96, center=(32.0, 48.0), r=14.0))
    w.close()
    with pytest.raises(ValueError, match="apres fermeture"):
        w.write(make_frame(w=64, h=96, center=(32.0, 48.0), r=14.0))
