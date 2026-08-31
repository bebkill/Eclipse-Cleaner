import pytest
from eclipse.presets import PRESET_NAMES, analysis_params, sort_defaults

def test_custom_is_the_current_behaviour():
    p = analysis_params("custom")
    assert p == {"lit_mode": "percentile", "radius_mode": "area",
                 "vote": "bright", "light_threshold": 0.35}
    assert sort_defaults("custom") == {"seuils": {}, "seuil_masque": None}

def test_every_preset_resolves_to_the_full_key_set():
    for nom in PRESET_NAMES:
        p = analysis_params(nom)
        assert set(p) == {"lit_mode", "radius_mode", "vote",
                          "light_threshold"}
        s = sort_defaults(nom)
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
