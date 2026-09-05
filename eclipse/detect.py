"""Eclipse-type detection: a light probe suggesting a profile.

A SUGGESTION, never silent: the CLI prints it and --preset overrides it;
the viewer shows it in a selector. Size is deliberately NOT a signal —
the moon spans 35 px in one user video and ~250 px in another (spec
2026-08-31). The discriminants are the sky background, the disc's
internal contrast (an umbra crossing the disc leaves a large dim part),
and, as a tie-break, a strongly warm bright part (filtered sun).

Thresholds are calibrated against the five real videos (task 11,
2026-09-01) and the synthetic frames; they are honest engineering
constants, not laws. Measured signals on the five, 24 samples each:

    video                       fond_clair  contraste  aire    chaleur
    Lunar-213307   (moon)          0.000      1.000    0.0284    0.755
    Lunar-221924   (moon)          0.042      0.875    0.0662    0.996
    Moon-Eclipse   (moon)          0.000      0.625    0.0041    1.015
    m2-res_852p    (sun)           0.292      1.000    0.1224    1.296
    Solar-timelapse(sun)           0.292      0.375    0.1077    3.505

All five land on the expected type, and so do 51 of the 54 combinations
of a (DIM_FRACTION, BRIGHT_SKY, CONTRAST_SHARE) sweep: the decision is
not balanced on a knife edge anywhere except where noted below.
"""
import numpy as np

from .io import FrameReader, probe

SAMPLE_COUNT = 24
PROBE_WIDTH = 270
BRIGHT_SKY = 8.0        # sky level (0-255) that says daylight or halo
# Share of samples with a bright sky -> sun. Measured separation on the
# five videos: the lunar ones read 0.000, 0.042 and 0.000, the two solar
# ones both read 0.292. The old 0.25 sat just under the solar values --
# 7 samples out of 24 against the 6 it required, a ONE-SAMPLE margin on
# both, and the branch is load-bearing: the reference solar sequence
# scores 0.375 on the contrast signal (above CONTRAST_SHARE) and m2
# scores 1.000, so either would be called `moon` if this test missed,
# and m2's warmth (1.296) is below WARM_RB and would not catch it back.
# 0.15 sits mid-gap instead: 3.6x the lunar maximum, half the solar
# minimum. No synthetic frame moves (their skies measure 25 and 34
# against 0 for the moons).
BRIGHT_SKY_SHARE = 0.15
BRIGHT_FRACTION = 0.5   # share of frame peak that counts as the bright part
# Share of frame peak that counts as the "dim" part, against which the
# bright part's area is compared for internal contrast. The real videos do
# NOT pin this one: every value from 0.03 to 0.20 classifies all five
# correctly. It is set from the measured UMBRAL LEVEL instead -- the median
# brightness of the shadowed part of the disc, as a fraction of the frame
# peak: 0.165 on Lunar-221924 (p25 0.104, p75 0.272) and 0.092 on
# Lunar-213307. A 10 % cut therefore sits just under the real umbral floor,
# which is what this signal is meant to see.
# Briefly lowered to 0.05 while the wiring test drew its umbra at
# umbra_level=0.15 (~8.4 % of the peak, unrealistically dark); that frame
# now uses umbra_level=0.25 and measures 1.86 of contrast at this 10 % cut,
# above INTERNAL_CONTRAST, so the reason for the lower value is gone.
DIM_FRACTION = 0.10
INTERNAL_CONTRAST = 1.8  # area(dim) / area(bright) that says umbra
# Share of samples with that contrast -> moon. Measured at the settings
# above: 1.000 / 0.875 / 0.625 on the three lunar videos against 0.375 on
# the reference solar sequence. 0.25 is below that 0.375, so this branch
# alone does not separate them -- the bright-sky test above runs first and
# does. Raising it to 0.50 (mid-gap) was measured and rejected: it would
# cut the margin on Moon-Eclipse, the hardest real case, from 2.5x to
# 1.25x for a redundancy the ordering already provides.
CONTRAST_SHARE = 0.25
PLANET_AREA = 0.002     # bright area under this frame fraction -> planetary
WARM_RB = 1.4           # median R/B of the bright part -> filtered sun


def sample_frames(source, count=SAMPLE_COUNT):
    """count RGB frames spread across the video, decoded small."""
    info = probe(source)
    w = PROBE_WIDTH
    h = max(2, int(round(info["height"] * w / info["width"])) // 2 * 2)
    total = max(1, int(info["duration"] * info["fps"]))
    step = max(1, total // count)
    frames = []
    with FrameReader(source, width=w, height=h) as reader:
        for i, f in enumerate(reader):
            if i % step == 0:
                frames.append(f)
                if len(frames) >= count:
                    break
    return frames


def classify_frames(frames):
    """{"type": name or None, "signals": aggregates} for these frames."""
    backgrounds, contrasts, areas, warmths = [], [], [], []
    for f in frames:
        g = f.astype(np.float32).mean(axis=2)
        peak = float(g.max())
        backgrounds.append(float(np.median(g)))
        if peak < 10.0:
            continue                      # black frame: no disc signals
        bright = g >= BRIGHT_FRACTION * peak
        dim = g >= DIM_FRACTION * peak
        bright_area = int(bright.sum())
        contrasts.append(float(dim.sum()) / max(bright_area, 1))
        areas.append(bright_area / g.size)
        r_ = float(np.median(f[:, :, 0][bright]))
        b_ = float(np.median(f[:, :, 2][bright]))
        warmths.append(r_ / max(b_, 1.0))

    n = max(len(frames), 1)
    signals = {
        "part_fond_clair": sum(v > BRIGHT_SKY for v in backgrounds) / n,
        "part_contraste": (sum(c >= INTERNAL_CONTRAST for c in contrasts)
                           / max(len(contrasts), 1)),
        "aire_mediane": float(np.median(areas)) if areas else 0.0,
        "chaleur_mediane": float(np.median(warmths)) if warmths else 1.0,
    }
    if signals["part_fond_clair"] >= BRIGHT_SKY_SHARE:
        kind = "sun"
    elif signals["part_contraste"] >= CONTRAST_SHARE:
        kind = "moon"
    elif areas and signals["aire_mediane"] < PLANET_AREA:
        kind = "planetary"
    elif signals["chaleur_mediane"] >= WARM_RB:
        kind = "sun"
    else:
        kind = None
    return {"type": kind, "signals": signals}


def classify_video(source):
    return classify_frames(sample_frames(source))
