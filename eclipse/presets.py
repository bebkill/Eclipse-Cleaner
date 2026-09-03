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

# Calibrated against the five real videos (task 11, 2026-09-01). What the
# measurements settled, profile by profile:
#
# moon -- radius_mode "scan" is the whole fix, and it is worth more than any
#   threshold. Against an independent ground truth (the radius at which the
#   azimuthally averaged profile falls to half its interior level) the scan
#   lands at 195.7 px for a measured 196.2 on Lunar-213307 (-0.3 %) and at
#   41.9 for a measured 42.5 on Moon-Eclipse (-1.5 %). The lit-area estimate
#   not only misses, it DRIFTS as the umbra advances: across Lunar-213307 it
#   slides from 122.7 px down to 71.5 for a disc that stays at 196.2, and on
#   Moon-Eclipse it wanders between 27.8 and 36.8 for a disc of 42.5 (see
#   locate.LIT_MAX_FRACTION, where the same sweep is run per mask mode).
#   Effect on Moon-Eclipse, where the old pipeline scored a masse_captee
#   median of 0.673 with a single frame in 1010 above the sorting threshold:
#   median 0.996, and 1010 frames in 1010 above it.
#
#   No light_threshold override. 0.10 was carried here on the assumption that
#   0.35 "only saw the lit sliver"; measurement refutes it. The three lunar
#   videos have a BLACK sky (p99.9 of the sky measures 0.000 of the frame
#   peak on Lunar-213307 and Moon-Eclipse), so no background light competes
#   with the disc and the cut has almost nothing to separate. Where it does
#   bite it bites the wrong way: on Lunar-221924, whose hazy frames put sky
#   light as high as 0.26 of the peak, moving 0.10 -> 0.35 takes the frames
#   falling under the 0.80 sorting threshold from 589 to 12 out of 10548, and
#   the valid measures from 9959 to 10536 -- a full re-analysis, not a sample
#   -- because the haze counted as light sat outside the disc and only ever
#   subtracted. On the other two the verdicts are identical either way
#   (2495/2592 and 981/1010 kept), the median merely rising from 0.9970 to
#   0.9987 and from 0.9884 to 0.9959. Inheriting the default is therefore
#   both simpler and measurably better.
#
#   dark_abs 5.0 (against the 40.0 default) is real and stays: a fully umbral
#   moon is dim by nature. It keeps 623 frames more on Lunar-221924 (7498
#   against 6875) and 28 more on Moon-Eclipse (981 against 953). The measured
#   plateau runs from 0.0 to 10.0 -- identical verdicts throughout -- and 20.0
#   already costs 100 frames, so 5.0 sits mid-plateau rather than on an edge.
#
#   No seuil_masque override: swept from 0.50 to 0.95 it moves nothing at all
#   on Lunar-213307 and Moon-Eclipse (masse_captee is bimodal there, ~0.34 or
#   ~0.99, and the gap between is empty), and on Lunar-221924 it is not even
#   monotonic (71.1 % kept at 0.80, 72.8 % at 0.70, 73.3 % at 0.90). Nothing
#   in the measurements picks a value, so the default 0.80 stands.
#
# sun -- light_threshold 0.70, against the 0.35 default. A totality filmed
#   without a solar filter carries a halo through its partial phases: on
#   m2-res_852p the light above 0.35*peak reaches 1.47 r at its p90 and
#   1.68 r at its maximum, so a disc-sized mask captured only half of it and
#   the frames scored a masse_captee median of 0.513 -- under the 0.80
#   threshold, which threw away a centre that was in fact CORRECT (an
#   independent re-scan puts it at the same place to within a pixel).
#   Measured at 0.70, the knee of the sweep (0.50 -> 0.598, 0.60 -> 0.730,
#   0.70 -> 0.898, and no further gain above): partial-phase frames rise to a
#   median of 0.900 with 0.997 of them above the threshold, totality frames
#   from 0.948 to 0.996 above it, valid measures from 872 to 1275 of 1284,
#   frames rejected as hors_source from 302 to 28, frames kept from 916 to
#   1180. The whole partial phase enters the render instead of being dropped.
#   Left at the knee rather than pushed higher: at 0.90 every frame scores
#   1.000 and the measure stops discriminating at all.
#
#   seuils={"conf_ancre": 0.04}: an anchor-confidence floor for the cropping
#   TRAJECTORY (see verdicts.analyse_verdicts and quality.SEUILS_DEFAUT),
#   0.0 (off) everywhere else. Three strata measured on m2-res_852p: an
#   unlocked Hough vote anchors a garbage center (cx as far off as -38.9)
#   at conf <= 0.0076 (frames 270-279); a photospheric-bead lock -- a real
#   peak, just on the wrong feature (cx 97 against a true ~120.9, second
#   contact) -- sits at conf 0.0242-0.0356 (frames 287-293); every good
#   frame around them (264-269, 274-276, 284-286) sits at conf >= 0.042.
#   0.02 (the conf_min value, see quality.SEUILS_DEFAUT) only cleared the
#   first stratum and left the bead anchoring frames 290-293 kept at the
#   wrong center -- two ~50-130 px jumps in the rendered trajectory at
#   second contact. 0.04 sits between the bead and the good frames,
#   clearing both -- but the margin is thin (0.0356 to 0.042, 0.006 wide):
#   re-measure before raising it further. Collateral, checked across the
#   whole video: 9 frames (294-296, 1167-1179's tail 1174-1179) drop from
#   0.02 to under 0.04, all of them already rejected motion_blur under
#   EITHER floor (never reach the render) and all still correctly
#   positioned by their own measure -- only a manual viewer recovery of
#   one of them would notice the interpolated stand-in.
#   Scoped to sun rather than made universal because a global floor is
#   measurably wrong even at 0.02: it would flip 44 legitimate,
#   correctly-positioned frames on the reference custom video and 163 on
#   Lunar-221924 from valid to interpolated (see quality.SEUILS_DEFAUT's
#   conf_ancre comment).
_PRESETS = {
    "custom": {},
    "sun": {"radius_mode": "scan", "vote": "dual",
            "light_threshold": 0.70,
            "seuils": {"conf_ancre": 0.04}},
    "moon": {"lit_mode": "max", "radius_mode": "scan",
             "seuils": {"dark_abs": 5.0}},
    "planetary": {"lit_mode": "max", "radius_mode": "scan"},
}


def _profile(preset):
    if preset not in _PRESETS:
        raise ValueError(
            f"Preset inconnu : {preset!r}. Choix : {', '.join(PRESET_NAMES)}")
    return _PRESETS[preset]


def analysis_params(preset):
    """Resolved pass-1 parameters (strategies) for this preset name."""
    profile = _profile(preset)
    return {key: profile.get(key, default)
            for key, default in _ANALYSIS_DEFAULTS.items()}


def sort_defaults(preset):
    """Pass-2 sorting defaults; explicit CLI flags override them."""
    profile = _profile(preset)
    return {"seuils": dict(profile.get("seuils", {})),
            "seuil_masque": profile.get("seuil_masque")}
