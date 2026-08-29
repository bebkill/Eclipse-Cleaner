import pytest

from eclipse.io import FrameWriter
from eclipse.etapes import ETAPES, etats, faits
from tests.synth import make_frame


@pytest.fixture
def video_courte(tmp_path):
    p = str(tmp_path / "src.mp4")
    with FrameWriter(p, width=64, height=96, fps=30.0) as w:
        for i in range(12):
            w.write(make_frame(w=64, h=96, center=(32.0, 48.0 + i), r=14.0))
    return p


def _f(vignettes=False, analyse=False, rendu=False):
    return {"vignettes": vignettes, "analyse": analyse, "rendu": rendu}


def test_etats_rend_une_entree_par_etape():
    e = etats(_f(), perime_rendu=False)
    assert set(e) == set(ETAPES)
    assert all(v in ("indisponible", "disponible", "faite", "a_refaire")
               for v in e.values())


def test_le_rendu_est_indisponible_sans_analyse():
    assert etats(_f(vignettes=True), perime_rendu=False)["rendu"] == "indisponible"


def test_le_rendu_devient_disponible_avec_l_analyse():
    assert etats(_f(analyse=True), perime_rendu=False)["rendu"] == "disponible"


def test_une_etape_accomplie_est_faite():
    e = etats(_f(vignettes=True, analyse=True, rendu=True), perime_rendu=False)
    assert e["vignettes"] == "faite"
    assert e["analyse"] == "faite"
    assert e["rendu"] == "faite"


def test_un_rendu_perime_est_a_refaire():
    e = etats(_f(analyse=True, rendu=True), perime_rendu=True)
    assert e["rendu"] == "a_refaire"


def test_un_rendu_absent_n_est_jamais_a_refaire():
    """perime_rendu ne doit pas transformer une absence en peremption.

    Sans ce garde-fou, un descripteur orphelin ferait afficher « a refaire »
    sur une etape que l'utilisateur n'a jamais lancee.
    """
    e = etats(_f(analyse=True, rendu=False), perime_rendu=True)
    assert e["rendu"] == "disponible"


def test_les_vignettes_ne_sont_jamais_a_refaire():
    """Rien en aval ne les consomme : les reextraire n'invalide rien.

    Le bandeau ne doit donc jamais suggerer qu'une reextraction est due a
    autre chose qu'un choix de l'utilisateur.
    """
    e = etats(_f(vignettes=True, analyse=True, rendu=True), perime_rendu=True)
    assert e["vignettes"] == "faite"


def test_faits_sur_un_disque_vide(tmp_path, video_courte):
    from eclipse.pipeline import _signature_source
    f = faits(str(tmp_path / "v"), str(tmp_path / "sortie.mp4"),
              _signature_source(video_courte), None)
    assert f == {"vignettes": False, "analyse": False, "rendu": False}


def test_le_fait_rendu_est_l_existence_du_fichier(tmp_path, video_courte):
    from eclipse.pipeline import _signature_source
    sortie = tmp_path / "sortie.mp4"
    sortie.write_bytes(b"x")
    f = faits(str(tmp_path / "v"), str(sortie),
              _signature_source(video_courte), None)
    assert f["rendu"] is True
