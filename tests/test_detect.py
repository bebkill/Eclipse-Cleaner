import numpy as np
from eclipse.detect import classify_frames
from tests.synth import make_frame, make_moon_frame, make_totality_frame


def test_a_daylight_sky_says_sun():
    frames = [make_frame(w=270, h=480, center=(135.0, 240.0), r=65.0,
                         phase=p, fond=25.0) for p in (0.0, 0.5, 0.9)]
    assert classify_frames(frames)["type"] == "sun"

def test_a_haloed_crescent_on_black_sky_says_sun():
    """m2-res_852p.mp4's regime: black sky but a massive halo."""
    frames = [make_frame(w=270, h=480, center=(135.0, 240.0), r=65.0,
                         phase=0.9, halo=0.5, fond=0.0) for _ in range(3)]
    assert classify_frames(frames)["type"] == "sun"

def test_a_shadow_crossed_disc_on_black_sky_says_moon():
    """umbra_level 0.25: the umbral GRAY sits at ~14 % of the frame max
    (umbra_wb dims the gray), above the 10 % dim cut - like the real
    videos, where the umbra measures 13-40 % of the max."""
    frames = [make_moon_frame(w=270, h=480, center=(135.0, 240.0), r=97.0,
                              umbra=u, umbra_level=0.25)
              for u in (0.2, 0.5, 0.8)]
    assert classify_frames(frames)["type"] == "moon"

def test_a_tiny_bright_disc_says_planetary():
    frames = [make_frame(w=270, h=480, center=(135.0, 240.0), r=6.0,
                         fond=0.0) for _ in range(3)]
    assert classify_frames(frames)["type"] == "planetary"

def test_an_ambiguous_video_stays_unclassified():
    """A full, uneclipsed moon: dark sky, uniform disc, no dynamics.
    Honesty over guessing: None -> the CLI falls back to custom."""
    frames = [make_moon_frame(w=270, h=480, center=(135.0, 240.0), r=97.0,
                              umbra=0.0) for _ in range(3)]
    assert classify_frames(frames)["type"] is None
