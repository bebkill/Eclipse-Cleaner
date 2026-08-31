"""Eclipse profiles: per-type strategies plus threshold defaults.

A profile is not just thresholds. The measured failure modes (spec of
2026-08-31, Solar-Eclipse repo) show that no threshold set can rescue an
empty lit mask or an inverted Hough vote: a profile therefore selects the
pass-1 STRATEGIES the measures depend on, and only then default sorting
thresholds for pass 2.

Pass-1 parameters shape the cached measures: pipeline stores them in the
cache (schema v6) and refuses to reuse a cache analyzed under another
preset. Pass-2 defaults never invalidate a cache; explicit CLI flags
always win over them.
"""

PRESET_NAMES = ("sun", "moon", "planetary", "custom")

_ANALYSIS_DEFAULTS = {
    "lit_mode": "percentile",   # locate.lit_mask threshold mode
    "radius_mode": "area",      # "area": estimate_radius; "scan": scan_radius
    "vote": "bright",           # locate.locate_center vote regime
    "light_threshold": 0.35,    # quality.masse_captee light cut (x max)
}

_SORT_DEFAULTS = {"seuils": {}, "seuil_masque": None}

# Initial values for moon are engineering estimates, measured and fixed
# against the three real lunar videos in the calibration task [CALIBRER-T11]:
# - light_threshold 0.10: the umbral part of the disc sits at 10-25 % of the
#   bright limb on the measured videos; 0.35 only saw the lit sliver.
# - dark_abs 5.0: a fully umbral moon is dim by nature, 40.0 rejected it.
_PRESETS = {
    "custom": {},
    "sun": {"radius_mode": "scan", "vote": "dual"},
    "moon": {"lit_mode": "max", "radius_mode": "scan",
             "light_threshold": 0.10,
             "seuils": {"dark_abs": 5.0}},
    "planetary": {"lit_mode": "max", "radius_mode": "scan"},
}


def _profil(preset):
    if preset not in _PRESETS:
        raise ValueError(
            f"Preset inconnu : {preset!r}. Choix : {', '.join(PRESET_NAMES)}")
    return _PRESETS[preset]


def analysis_params(preset):
    """Resolved pass-1 parameters (strategies) for this preset name."""
    profil = _profil(preset)
    return {cle: profil.get(cle, defaut)
            for cle, defaut in _ANALYSIS_DEFAULTS.items()}


def sort_defaults(preset):
    """Pass-2 sorting defaults; explicit CLI flags override them."""
    profil = _profil(preset)
    return {"seuils": dict(profil.get("seuils", {})),
            "seuil_masque": profil.get("seuil_masque")}
