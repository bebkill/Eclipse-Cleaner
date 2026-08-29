import os

import numpy as np
import pytest

from eclipse.render import shift_bilinear, soft_knee, apply_frame, melange_lineaire
from eclipse.locate import locate_center
from eclipse.io import FrameReader
from tests.synth import make_frame
from tests.test_pipeline import SOURCE_REELLE


def test_shift_entier():
    img = np.zeros((10, 10, 1), np.float32)
    img[5, 5] = 100.0
    out = shift_bilinear(img, 2.0, 3.0)
    assert out[8, 7, 0] == 100.0
    assert out[5, 5, 0] == 0.0


def test_shift_sous_pixel_repartit_l_energie():
    img = np.zeros((10, 10, 1), np.float32)
    img[5, 5] = 100.0
    out = shift_bilinear(img, 0.5, 0.0)
    assert abs(out[5, 5, 0] - 50.0) < 1e-4
    assert abs(out[5, 6, 0] - 50.0) < 1e-4


def test_shift_sous_pixel_preserve_une_plage_uniforme():
    """L'interpolation bilineaire d'un champ constant reste constante."""
    img = np.full((40, 40, 3), 100.0, np.float32)
    out = shift_bilinear(img, 1.3, -2.7)
    assert np.allclose(out[10:30, 10:30], 100.0, atol=1e-3)


def test_shift_remplit_en_noir():
    img = np.full((10, 10, 3), 100.0, np.float32)
    out = shift_bilinear(img, 3.0, 0.0)
    assert out[:, 0:3].max() == 0.0


def test_soft_knee_est_transparent_sous_le_genou():
    x = np.array([0.0, 50.0, 100.0, 200.0], np.float32)
    out = soft_knee(x, knee=0.85, plafond=255.0)
    assert np.allclose(out[:3], x[:3], atol=1e-4)


def test_soft_knee_ne_depasse_jamais_le_plafond():
    x = np.array([300.0, 600.0, 5000.0], np.float32)
    out = soft_knee(x, knee=0.85, plafond=255.0)
    assert out.max() <= 255.0
    assert out[0] < 255.0           # une valeur modeste reste sous le plafond
    assert out.min() > 216.0        # 0.85 * 255


def test_soft_knee_est_monotone():
    x = np.linspace(0.0, 1000.0, 500, dtype=np.float32)
    out = soft_knee(x, knee=0.85, plafond=255.0)
    assert np.all(np.diff(out) >= -1e-5)


def test_soft_knee_accepte_un_scalaire_au_dessus_du_genou():
    """Regression : sur une entree 0-D, np.maximum(f, 0.0) renvoie un
    scalaire numpy et non un tableau 0-D, ce qui faisait lever TypeError a
    l'indexation booleenne out[au_dessus] introduite par l'optimisation.
    soft_knee(10.0), sous le genou, ne declenchait pas le bug."""
    assert abs(soft_knee(300.0) - 250.66087) < 1e-3
    assert soft_knee(10.0) == 10.0


def test_apply_frame_recentre_le_disque():
    """Le test le plus important du module : le disque doit finir au centre."""
    img = make_frame(w=300, h=300, center=(80.0, 220.0), r=50.0)
    out = apply_frame(img, cx=80.0, cy=220.0, gain=1.0)
    gray = out.astype(np.float32).mean(axis=2)
    cx, cy, _ = locate_center(gray, r=50.0)
    assert abs(cx - 150.0) < 0.5
    assert abs(cy - 150.0) < 0.5


def test_apply_frame_applique_le_gain():
    img = make_frame(w=200, h=200, center=(100.0, 100.0), r=40.0, gain=0.3)
    faible = apply_frame(img, 100.0, 100.0, 1.0)
    fort = apply_frame(img, 100.0, 100.0, 2.0)
    assert fort.astype(np.float32).mean() > faible.astype(np.float32).mean() * 1.6


def test_apply_frame_ne_corrige_pas_la_couleur():
    """Garde-fou : la couleur ne doit jamais etre rectifiee.

    Le filtre solaire de la premiere moitie de la sequence est un filtre
    rouge, canal bleu a 0,07 sur 255. Neutraliser cette teinte attenuait le
    rouge d'un facteur 370 et noircissait 41% de la video. Aucun autre test
    ne detecterait la reintroduction d'une correction de balance.
    """
    img = make_frame(w=200, h=200, center=(100.0, 100.0), r=40.0,
                     gain=0.5, wb=(1.0, 0.10, 0.0003))
    out = apply_frame(img, 100.0, 100.0, 1.0)
    avant = img.astype(np.float32).reshape(-1, 3)
    apres = out.astype(np.float32).reshape(-1, 3)
    vif = avant.mean(axis=1) > 20
    assert vif.sum() > 1000, "masque vide : l'assertion serait vacante"
    for canal in (1, 2):
        rapport_avant = avant[vif, canal].mean() / avant[vif, 0].mean()
        rapport_apres = apres[vif, canal].mean() / apres[vif, 0].mean()
        # Tolerance RELATIVE, et non absolue. Un ecart fixe de 0,02 sur le
        # rapport du bleu, qui vaut 0,023, exigeait une derive de 86 % pour
        # se declencher — or le bleu est justement le canal du defaut
        # historique. A 2 % relatif, une recolorisation partielle est vue.
        assert abs(rapport_apres - rapport_avant) <= 0.02 * rapport_avant


def test_apply_frame_sort_en_uint8_a_la_taille_demandee():
    img = make_frame(w=200, h=300, center=(100.0, 150.0), r=40.0)
    out = apply_frame(img, 100.0, 150.0, 1.0, taille=(200, 300))
    assert out.shape == (300, 200, 3)
    assert out.dtype == np.uint8


def _aire_eclairee(img):
    g = img.astype(np.float32).mean(axis=2)
    return int((g > g.max() * 0.5).sum())


def test_apply_frame_recentre_dans_un_cadre_plus_grand():
    """Le disque doit atteindre INTACT le centre d'un cadre plus large.

    L'assertion de position ne suffit pas et c'est contre-intuitif : le vote
    a rayon fixe retrouve le centre d'un disque tronque — c'est meme la
    propriete qui sauve les frames tranchees par l'horizon. Mesure faite sur
    la version bogue de ce module : 51% du disque perdu, et l'assertion de
    position passait quand meme. Seule la conservation de l'aire eclairee
    distingue les deux.
    """
    img = make_frame(w=200, h=200, center=(60.0, 140.0), r=30.0)
    out = apply_frame(img, 60.0, 140.0, 1.0, taille=(300, 400))
    assert out.shape == (400, 300, 3)
    cx, cy, _ = locate_center(out.astype(np.float32).mean(axis=2), r=30.0)
    assert abs(cx - 150.0) < 0.5
    assert abs(cy - 200.0) < 0.5
    attendu = _aire_eclairee(img)
    assert abs(_aire_eclairee(out) - attendu) < attendu * 0.02


def test_melange_aux_extremites_rend_les_originaux():
    a = make_frame(w=64, h=96, center=(30.0, 40.0), r=12.0)
    b = make_frame(w=64, h=96, center=(34.0, 52.0), r=12.0)
    assert np.array_equal(melange_lineaire(a, b, 0.0), a)
    assert np.array_equal(melange_lineaire(a, b, 1.0), b)


def test_melange_a_mi_chemin_est_intermediaire():
    a = np.zeros((10, 10, 3), np.uint8)
    b = np.full((10, 10, 3), 200, np.uint8)
    assert abs(int(melange_lineaire(a, b, 0.5)[0, 0, 0]) - 100) <= 1


def test_melange_borne_t():
    a = np.zeros((4, 4, 3), np.uint8)
    b = np.full((4, 4, 3), 100, np.uint8)
    assert np.array_equal(melange_lineaire(a, b, -1.0), a)
    assert np.array_equal(melange_lineaire(a, b, 5.0), b)


def test_apply_frame_recentre_dans_un_cadre_plus_petit():
    """Reduction de cadre. Ce cas passait deja avant correction : il
    documente le comportement, il ne garde pas la regression.
    """
    img = make_frame(w=300, h=300, center=(220.0, 80.0), r=30.0)
    out = apply_frame(img, 220.0, 80.0, 1.0, taille=(160, 200))
    assert out.shape == (200, 160, 3)
    cx, cy, _ = locate_center(out.astype(np.float32).mean(axis=2), r=30.0)
    assert abs(cx - 80.0) < 0.5
    assert abs(cy - 100.0) < 0.5
    attendu = _aire_eclairee(img)
    assert abs(_aire_eclairee(out) - attendu) < attendu * 0.02


def test_shift_remplissage_bord_ne_laisse_pas_de_noir():
    """Une plage uniforme translatee avec remplissage bord reste uniforme :
    ni bande noire, ni couture assombrie par le melange bilineaire partiel."""
    img = np.full((40, 40, 3), 100.0, np.float32)
    out = shift_bilinear(img, 5.3, -2.7, remplissage="bord")
    assert np.allclose(out, 100.0, atol=1e-3)


def test_shift_remplissage_bord_replique_le_bord_reel():
    """La bande revelee reprend la derniere colonne valide, pas une autre."""
    img = np.zeros((10, 10, 1), np.float32)
    img[:, 0] = 50.0                     # colonne gauche distinctive
    out = shift_bilinear(img, 3.0, 0.0, remplissage="bord")
    assert np.allclose(out[:, 0:4, 0], 50.0)   # bande + colonne d'origine


def test_shift_remplissage_noir_reste_le_defaut():
    img = np.full((10, 10, 3), 100.0, np.float32)
    out = shift_bilinear(img, 3.0, 0.0)
    assert out[:, 0:3].max() == 0.0


def test_shift_remplissage_bord_preserve_un_degrade_sombre():
    """Ce test NE PEUT PAS echouer avec ces valeurs, et c'est assume : il ne
    demontre pas que le clamp preserve un degrade arbitraire.

    Plage 5 a 15 sur 30 lignes : plafonner les 40 % les plus hauts au
    percentile 60 (~11) donne un ecart maximal de 15 - 11 = 4, toujours sous
    la tolerance de 5.0 ci-dessous — le test passe donc que le clamp agisse
    ou non sur ce degrade precis. Ce qui rend le clamp sans consequence
    visuelle n'est PAS une propriete mathematique de la fonction, mais un
    fait mesure sur la sequence reelle : une ligne de bord traversee par le
    ciel ou le sol y est authentiquement du fond (ecart de pic mesure : 41
    niveaux), donc deja proche de son propre percentile 60 avant meme le
    clamp. Ce test documente ce cas reel ; il ne garde pas de regression sur
    un degrade quelconque."""
    h, w = 30, 10
    img = np.zeros((h, w, 3), np.float32)
    degrade = np.linspace(5.0, 15.0, h, dtype=np.float32)
    img[:, :, :] = degrade[:, None, None]
    out = shift_bilinear(img, 4.0, 0.0, remplissage="bord")
    bande = out[:, :4]
    original = np.broadcast_to(degrade[:, None, None], (h, 4, 3))
    assert np.abs(bande - original).max() < 5.0
    # La variation du degrade doit rester lisible, pas ecrasee en aplat.
    assert bande[:, 0, 0].std() > 0.5


def test_shift_remplissage_bord_n_etale_pas_une_zone_brillante():
    """Regression du defaut mesure : un appendice rectangulaire brillant
    colle au disque solaire dans le rendu. La ligne de bord reprise dans la
    bande contient une zone brillante (le Soleil qui la traverse) sur fond
    sombre ; la bande doit rester au niveau du fond, pas etaler le sujet."""
    h, w = 30, 10
    fond = 5.0
    img = np.full((h, w, 3), fond, np.float32)
    img[10:20, 0, :] = 220.0     # zone brillante sur la colonne de bord
    out = shift_bilinear(img, 4.0, 0.0, remplissage="bord")
    bande = out[:, :4]
    assert bande.max() < 50.0                    # pas d'appendice brillant
    assert abs(float(bande.mean()) - fond) < 5.0  # reste au niveau du fond


def test_shift_remplissage_bord_accepte_un_tableau_2d():
    """_replique_bords doit rester rank-agnostique comme _shift_entier (qui
    s'appuie sur img.shape[2:]) : un tableau 2-D (niveaux de gris, sans axe
    de canal) doit survivre a remplissage='bord' au lieu de lever
    IndexError sur l'indexation fixee a 3-D de la bande repliquee."""
    img = np.full((10, 10), 100.0, np.float32)
    out = shift_bilinear(img, 3.0, 0.0, remplissage="bord")
    assert out.shape == (10, 10)
    assert np.allclose(out, 100.0, atol=1e-3)


def _soft_knee_oracle(x, knee=0.85, plafond=255.0):
    """Copie figee de l'implementation pre-optimisation de soft_knee (calcule
    l'exponentielle sur toute la frame avant de la jeter sous le genou via
    np.where). Sert d'oracle pour la tache perf/calcul-par-frame ; ne pas la
    faire evoluer avec le code de production."""
    f = np.asarray(x, dtype=np.float32)
    k = knee * plafond
    reste = plafond - k
    if reste <= 0.0:
        return np.clip(f, 0.0, plafond)
    haut = plafond - reste * np.exp(-np.maximum(f - k, 0.0) / reste)
    return np.where(f <= k, np.maximum(f, 0.0), haut).astype(np.float32)


def test_soft_knee_equivalent_a_l_oracle_sur_fixture_synthetique():
    """Meme preuve d'equivalence que le test video reelle ci-dessous, mais
    sur une fixture synthetique qui tourne partout (data/ est gitignore, le
    test reel se saute sans la video source). Melange de valeurs sous et
    au-dessus du genou, dont un cas scalaire (voir aussi la regression
    dediee test_soft_knee_accepte_un_scalaire_au_dessus_du_genou)."""
    rng = np.random.default_rng(20260821)
    x = rng.uniform(-50.0, 900.0, size=(64, 64)).astype(np.float32)
    for gain in (0.3, 1.0, 2.0, 5.0):
        attendu = _soft_knee_oracle(x * gain)
        obtenu = soft_knee(x * gain)
        assert np.array_equal(obtenu, attendu)
    assert soft_knee(300.0) == _soft_knee_oracle(np.float32(300.0))


@pytest.mark.skipif(not os.path.isfile(SOURCE_REELLE),
                    reason="video reelle absente (data/ est gitignore)")
def test_soft_knee_equivalent_a_l_oracle_sur_video_reelle():
    """Preuve d'equivalence numerique de l'optimisation (exponentielle
    calculee seulement au-dessus du genou, voir _soft_knee_oracle ci-dessus)
    sur des frames reellement decodees, a plusieurs gains representatifs de
    ce que fait apply_frame (f = rgb * gain, puis soft_knee).

    40 frames a 540x960 : memes dimensions que le profilage cite dans le
    docstring de soft_knee (67,9 ms -> 43,8 ms).
    """
    n = 40
    frames = []
    with FrameReader(SOURCE_REELLE, width=540, height=960) as reader:
        for i, frame in enumerate(reader):
            if i >= n:
                break
            frames.append(frame.astype(np.float32))
    assert len(frames) == n

    ecart_max = 0.0
    fraction_au_dessus = []
    for f in frames:
        for gain in (0.5, 1.0, 1.5, 2.5):
            x = f * gain
            attendu = _soft_knee_oracle(x)
            obtenu = soft_knee(x)
            ecart_max = max(ecart_max, float(np.abs(obtenu - attendu).max()))
            fraction_au_dessus.append(float((x > 0.85 * 255.0).mean()))

    # Le sous-ensemble au-dessus du genou doit etre effectivement exerce,
    # sinon la comparaison serait vacante (les deux implementations
    # degenerent au meme resultat trivial si aucun pixel ne depasse le genou).
    assert max(fraction_au_dessus) > 0.05

    # Ecart maximal mesure sur la sequence reelle (40 frames, 540x960, 4
    # gains) : 0.0 exactement, comme annonce dans le docstring de soft_knee.
    assert ecart_max == 0.0


def test_apply_frame_transmet_le_remplissage():
    """Le disque colle au bord gauche est recentre dans un cadre plus large :
    la bande revelee a gauche doit etre repliquee, pas noire."""
    img = np.full((200, 200, 3), 80, np.uint8)     # fond uniforme non noir
    out = apply_frame(img, 10.0, 100.0, 1.0, taille=(200, 200),
                      remplissage="bord")
    assert out[:, :90].min() >= 75                  # bande repliquee, pas noire
    sans = apply_frame(img, 10.0, 100.0, 1.0, taille=(200, 200))
    assert sans[:, :80].max() == 0                  # defaut : noir inchange
