import pytest
from eclipse.presets import PRESET_NAMES, analysis_params, sort_defaults

def test_custom_is_the_current_behaviour():
    p = analysis_params("custom")
    assert p == {"lit_mode": "percentile", "radius_mode": "area",
                 "vote": "bright", "light_threshold": 0.35}
    assert sort_defaults("custom") == {"seuils": {}, "seuil_masque": None}

def test_every_preset_resolves_to_the_full_key_set():
    for name in PRESET_NAMES:
        p = analysis_params(name)
        assert set(p) == {"lit_mode", "radius_mode", "vote",
                          "light_threshold"}
        s = sort_defaults(name)
        assert set(s) == {"seuils", "seuil_masque"}

def test_moon_strategies():
    p = analysis_params("moon")
    assert p["lit_mode"] == "max" and p["radius_mode"] == "scan"
    assert p["vote"] == "bright"

def test_sun_scans_the_radius_and_votes_both_regimes():
    p = analysis_params("sun")
    assert p["radius_mode"] == "scan" and p["vote"] == "dual"

def test_unknown_preset_is_refused():
    with pytest.raises(ValueError):
        analysis_params("mars")

def test_sun_refuses_unlocked_votes_as_trajectory_anchors():
    """Measured on m2-res_852p (frames 270-279): an unlocked vote anchors
    a garbage center at conf 0.0040-0.0076, while the good frames around it
    sit at conf >= 0.042 -- better than 5x clear. Scoped to sun rather than
    universal: M2 showed a global 0.02 floor would wrongly flip 44 valid
    frames on the reference video and 163 on Lunar-221924 to interpolated."""
    assert sort_defaults("sun")["seuils"] == {"conf_ancre": 0.02}
    assert sort_defaults("custom")["seuils"] == {}
    assert sort_defaults("moon")["seuils"] == {"dark_abs": 5.0}
