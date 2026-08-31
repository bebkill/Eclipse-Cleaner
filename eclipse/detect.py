"""Eclipse-type detection: a light probe suggesting a profile.

A SUGGESTION, never silent: the CLI prints it and --preset overrides it;
the viewer shows it in a selector. Size is deliberately NOT a signal —
the moon spans 35 px in one user video and ~250 px in another (spec
2026-08-31). The discriminants are the sky background, the disc's
internal contrast (an umbra crossing the disc leaves a large dim part),
and, as a tie-break, a strongly warm bright part (filtered sun).

Thresholds are calibrated against the five real videos and the synthetic
frames; they are honest engineering constants, not laws. [CALIBRER-T11]
"""
import numpy as np

from .io import FrameReader, probe

SAMPLE_COUNT = 24
PROBE_WIDTH = 270
BRIGHT_SKY = 8.0        # sky level (0-255) that says daylight or halo
BRIGHT_SKY_SHARE = 0.25  # share of samples with a bright sky -> sun
BRIGHT_FRACTION = 0.5   # share of frame peak that counts as the bright part
# Share of frame peak that counts as the "dim" part, against which the
# bright part's area is compared for internal contrast. Measured against
# the wiring test's umbra (make_moon_frame umbra_level=0.15, r=97 on a
# 270x480 probe): the shadowed gray sits at ~8.4 % of the frame peak, below
# the original 10 % cut, so it never left the "dim" bucket and the umbra was
# invisible to the contrast signal - only its blurred boundary ring was
# counted, never enough to cross INTERNAL_CONTRAST. Lowered to 5 %, comfortably
# below that measured 8.4 %, while staying above the ~2-3 % antialiasing-ring
# contrast the tiny-disc (planetary) and undivided-disc (ambiguous) synthetic
# frames produce, which must NOT read as an umbra.
DIM_FRACTION = 0.05
INTERNAL_CONTRAST = 1.8  # area(dim) / area(bright) that says umbra
CONTRAST_SHARE = 0.25   # share of samples with that contrast -> moon
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
