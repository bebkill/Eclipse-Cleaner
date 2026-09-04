# Eclipse Cleaner

*Clean up and stabilize a shaky solar-eclipse timelapse — and maybe rescue a video you thought was lost.*

**[🇫🇷 Version française](README.fr.md)**

[![CI](https://github.com/bebkill/Eclipse-Cleaner/actions/workflows/ci.yml/badge.svg)](https://github.com/bebkill/Eclipse-Cleaner/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-blue.svg)](https://www.python.org/)
[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-support-yellow?logo=buymeacoffee&logoColor=white)](https://www.buymeacoffee.com/bebkill)

> 🔰 **Never used Python or a terminal?** Follow the **[guide for absolute beginners (Windows)](BEGINNERS.md)** — copy-paste only, no experience needed, no code to write.

## The story behind this project

This project began with the capture of the solar eclipse of **August 12, 2026**. My equipment setup was far from ideal — people kept walking in front of my gear — but I still managed to capture the eclipse. Then I opened the video produced by my smart telescope, and my heart sank: it was unusable. Very unstable, with long masked sequences followed by tracking-recovery phases and abrupt brightness adjustments.

I tried to fix it with the tools available (SIRIL and PIPP), but I probably didn't know how to use them well enough — or it was simply too much to ask of them. So I set out to build a tool of my own, with the help of my favorite AI assistant. Here it is.

## Before / after

| Original (straight from the smart telescope) | Cleaned with Eclipse Cleaner |
| :---: | :---: |
| ![Original, unstable video](docs/assets/before.gif) | ![Cleaned, stabilized video](docs/assets/after.gif) |

*Both clips play at 6× speed.*

## What it does

- **Adapts to what's actually eclipsing** — sun, moon, or a planetary transit each fail differently, so an eclipse-type preset picks the right measurement strategy and sorting defaults for each. The type is detected automatically and always overridable. See [Eclipse types and presets](#eclipse-types-and-presets).
- **Sorts the frames** — only irreparable frames are rejected: blur, darkness, glare, failed disk localization. A badly framed frame is *not* a defective frame; the stabilizer fixes the position.
- **Locks the solar disk to the center** — a fixed-radius directed Hough vote finds the disk even on a thin crescent, and tolerates clouds masking part of the limb.
- **Moves the frame like a camera operator would** — the crop window is planned, bounded softly against the source edges, and absorbs the tracking re-acquisition jumps (hundreds of pixels in a single frame) instead of jerking.
- **Removes auto-exposure flicker** — brightness is corrected frame by frame toward the sequence median, and white balance is stabilized toward its *own* trajectory, never toward neutral: a red solar filter stays red, a sunset stays warm, and removing the filter mid-sequence stays a real change. (`--sans-couleur` disables the color part.)
- **Bridges short gaps** with linear interpolation, and never invents footage for long ones: a real hole stays a clean cut.
- **A review viewer in your browser** — inspect every frame, overrule the automatic sorting with one keystroke, then re-render. Bilingual (English/French), served on 127.0.0.1 only. It shows a loading state while a newly picked video is probed, and offers a collapsible raw-video player to watch the source itself.

Everything adapts to your video: resolution, aspect ratio, frame rate and apparent solar radius are measured, not assumed, and every threshold can be overridden from the command line.

## Installation

**On Windows you can skip Python entirely**: each release ships a standalone `eclipse-cleaner-…-windows-x64.exe` — download it from the [latest release](https://github.com/bebkill/Eclipse-Cleaner/releases/latest) and double-click. Details (and the SmartScreen warning to expect) in [the beginners guide](BEGINNERS.md#the-shortcut--a-ready-made-windows-program-no-python-at-all).

Otherwise: requires **Python 3.12+** — check with `python --version` first: on a system whose default `python` is older (Homebrew, some Linux distros), the install fails with an unrelated-looking dependency error rather than a clear version message. The ffmpeg binary is bundled through `imageio-ffmpeg`: nothing else to install on your system.

```bash
python -m pip install git+https://github.com/bebkill/Eclipse-Cleaner.git
```

This `git+` form requires [git](https://git-scm.com/) to be installed (it usually isn't, on Windows). No git? Use the zip form instead — same result:

```bash
python -m pip install https://github.com/bebkill/Eclipse-Cleaner/archive/refs/heads/main.zip
```

Or from a clone:

```bash
git clone https://github.com/bebkill/Eclipse-Cleaner.git
cd Eclipse-Cleaner
python -m pip install .
```

⚠️ Don't miss the trailing dot in `pip install .` — it means "install from this directory".

Once installed, run the tool with:

```bash
python -m eclipse viewer
```

This form always works. The install also creates a shorter `eclipse-cleaner` command, but it is only found if your Python `Scripts` directory is on the PATH — on Windows it often isn't. If your terminal answers *"'eclipse-cleaner' is not recognized…"*, nothing is broken: just use `python -m eclipse` instead. (An install through `pipx install git+https://github.com/bebkill/Eclipse-Cleaner.git` sets up the PATH for you, if you prefer the short command.)

## Usage

### The easy way: the viewer (recommended)

```bash
python -m eclipse viewer
```

This opens a local page in your browser. Click **Browse…** to pick your video, then run the three steps from the page:

1. **Extract the thumbnails** (for review);
2. **Analyze the frames** (measurements and automatic verdicts);
3. **Produce the final video** → written into the video's work folder: `eclipse.mp4` gives `eclipse.mp4-eclipse/eclipse-clean.mp4` (optionally with a numbered PNG sequence, in `eclipse.mp4-eclipse/frames/`).

Everything the page derives from a video — the analysis cache, your review decisions, the review thumbnails, the render and the PNG export — lives in one work folder created next to it, `<source>-eclipse/`: a folder holding several eclipses stays readable, and two videos can never share a cache or a review. The rendered `-clean.mp4` keeps a name of its own inside that folder, since it is the file you copy out. Work files left loose next to a video by an earlier version are moved into the folder the first time the viewer opens that video, and it says so on the terminal. A rendered video moves only when the descriptor written beside it names *this* video: the old render name was shared between a source and its transcode, so an unvouched one is left exactly where it is.

Under the render button, the **stabilize color** checkbox (on by default) removes the frame-to-frame white-balance oscillations of automatic exposure, toward the sequence's own tint — never toward neutral. Its collapsible **parameters** section holds the fine-tuning: the tint-reference *window* (typical: 31 frames, capped at the sequence length — beyond that the reference saturates and a larger window changes nothing) and the *max correction* per channel (typical: 0.25, i.e. ±25 %). Brightness is always normalized, checkbox or not. Changing a parameter marks an existing render as *to redo*, so the banner never claims a stale output is current.

The central thumbnail shows the **crop frame**: the exact rectangle the render will cut out of the current frame. Its *position* is always automatic — it follows the tracked disk, so there is nothing to place by hand — but its *size* can be dragged from the handle in its bottom-right corner, staying centered and locked to the output's aspect ratio. A label under the thumbnail reads **auto** or **custom (W×H)**, with a ↺ button to snap back to the recommended size. The choice is remembered per video (in its work folder) and is exactly what the render uses, the same value `--taille` would set from the command line.

Between steps 2 and 3, **review the sorting**: the timeline shows kept frames in green, rejected ones in red, your own overrides in blue. Press `k` on any frame to keep or discard it — each change is saved immediately, and the render applies your decisions automatically.

| Key | Effect |
|---|---|
| `space` | Play / pause |
| `←` `→`, `n` / `p` | Previous / next frame in the current selection |
| `k` | Toggle keep / discard on the current frame |
| `r` | Filter: kept frames only |
| `e` | Filter: rejected frames only |
| `m` | Filter: my overrides only |
| `+` / `-` | Zoom the thumbnail strip |

Progress bars, cancellation, and task state all live server-side: closing the tab loses nothing.

### The command-line way

```bash
python -m eclipse run input.mp4 output.mp4
```

Or in two steps, so you can re-render with different thresholds without re-analyzing:

```bash
python -m eclipse analyze input.mp4 --cache analysis.json
python -m eclipse render input.mp4 output.mp4 --cache analysis.json --blur-rel 0.35
```

### Main options

The CLI flags are in French (see [Known limitations](#known-limitations)); here is what they mean:

| Flag | Meaning |
|---|---|
| `--processus N` | Number of worker processes (default: logical cores − 1; `1` = sequential) |
| `--preset sun\|moon\|planetary\|custom` | Eclipse-type profile — see [Eclipse types and presets](#eclipse-types-and-presets) (default: automatic detection at analysis, the cache's own preset at render) |
| `--seuil-lumiere F` | `analyze`/`run` only — fraction of the frame's peak counted as "light" for the mask-capture measurement (default: the preset's own value) |
| `--radius R` | Apparent solar radius in pixels, if automatic estimation fails |
| `--taille WxH` | Crop-window size (default: 7/9 of the source, same aspect ratio) |
| `--sortie-taille WxH` | Encoded output size (default: same as the source) |
| `--tolerance-bord PX` | How much the disk may be clipped by the source edge before rejection (default 5) |
| `--depassement-butee PX` | How far the window may overshoot the source edges, filled by edge replication (default 400) |
| `--interp-max N` | Longest gap (in frames) bridged by interpolation (default 3, `0` disables) |
| `--interp-deplacement-max PX` | Largest window displacement across a bridged gap (default 30) |
| `--seuil-masque F` | Minimum fraction of the light the solar mask must capture for a center measurement to be trusted (default 0.80) |
| `--sans-couleur` | Disable white-balance stabilization (brightness stays normalized) |
| `--couleur-fenetre N` | Window, in frames, of the stabilization tint reference (default 31) |
| `--couleur-amplitude F` | Maximum tint correction per channel, as a fraction (default 0.25) |
| `--dark-rel`, `--dark-abs`, `--blur-rel`, `--flare-rel`, `--conf-min`, `--ilot-min` | Sorting thresholds (darkness, blur, glare, localization confidence, minimal kept-run length) |
| `--decisions FILE` / `--sans-decisions` | Use a specific manual-review file / ignore manual reviews entirely |

## Eclipse types and presets

A solar crescent, a shadowed moon and a small bright planetary disc do not fail the same way: a threshold set tuned for one can leave another with an empty light mask or an inverted vote. A **preset** therefore fixes, per eclipse type, both the pass-1 measurement strategies (baked into the analysis cache) and the pass-2 sorting defaults (which plain CLI flags can still override without invalidating the cache):

| Preset | lit mode | radius mode | vote | `--seuil-lumiere` | `--dark-abs` | `--seuil-masque` |
|---|---|---|---|---|---|---|
| `sun` | percentile | scan | dual | 0.70 | 40.0 | 0.80 |
| `moon` | max | scan | bright | 0.35 | 5.0 | 0.80 |
| `planetary` | max | scan | bright | 0.35 | 40.0 | 0.80 |
| `custom` | percentile | area | bright | 0.35 | 40.0 | 0.80 |

- **lit mode** — how the illuminated region is thresholded to estimate the apparent radius: `percentile` (midway between the frame's background and its 99th percentile, the historic behavior) or `max` (relative to the frame's peak instead), needed when the subject can be small or largely shadowed.
- **radius mode** — `area` turns the lit region's area into a radius (exact for a full disc, but drifts as an eclipse's umbra advances) or `scan`, which finds the radius that maximizes the directed Hough-vote confidence (accurate to within about ±1.5 % on the calibration videos even as the umbra grows).
- **vote** — the Hough-vote regime used to find the disc center: `bright` (a lit disc), `dark` (a dark disc ringed by light — a solar totality, where the limb gradient points outward), or `dual` (evaluates both per frame and keeps the sharper peak, needed when a video crosses from crescent to totality and back).
- `custom` reproduces the tool's original, pre-eclipse-types behavior exactly (byte-identical on the reference solar sequence).

For a **custom analysis**, or to understand what moving a preset's own defaults does, here is what the code documents about each sorting/analysis threshold:

| Option | Default | Documented range / behavior | Effect of moving it |
|---|---|---|---|
| `--dark-rel` | 0.35 (fraction of the frame's local median `disk_p90`) | Not swept in the calibration; no documented bound. | Higher requires a frame to stay closer to its own local brightness reference to avoid `too_dark`; lower tolerates more local dimming. |
| `--dark-abs` | 40.0 (5.0 under the `moon` preset) | On the calibration videos, every value from 0.0 to 10.0 gives identical verdicts, and 20.0 already costs measurably more kept frames — the `moon` preset's 5.0 sits mid-plateau, not on an edge. | Absolute brightness floor below which a frame is rejected outright, regardless of its local reference; needed low for a fully-umbral moon, whose darkest kept frames are dim by nature. |
| `--blur-rel` | 0.40 (fraction of the local median `limb_sharpness`) | Measured on the reference sequence: raising it to 0.50 wrongly rejected the last, naturally softer sunset frames; lowering it below 0.40 rejected no additional frame. | Higher is stricter about limb sharpness relative to the local reference; pushed too high it starts rejecting frames that are only softer for a legitimate reason (the horizon eating the limb, a thin crescent). |
| `--flare-rel` | 3.0 (multiple of the local median `flare_ratio`) | Not swept in the calibration; no documented bound. | Higher tolerates more light captured far from the disc (glare, a lit cloud) before rejecting as `flare`; lower is stricter. |
| `--conf-min` | 0.02 (minimum Hough-vote confidence) | Not swept, but a documented weak spot: on the reference sequence, frames with a wrongly-placed center still scored 0.072–0.094 — well above this default. | This alone rarely tells a right center from a wrong one; `--seuil-masque` (see below) is the measurement that actually catches a center that doesn't explain the image. |
| `--ilot-min` | 1 (island removal effectively disabled) | Neutral by design: on a full human review of the reference sequence, all 29 one-frame "islands" the old, stricter setting would have removed turned out to be real, wanted keeps. | Raising it (e.g. 5) drops short kept runs bordered on both sides by at least that many rejected frames — worth reactivating only if a video produces genuine one-frame flicker. |
| `--seuil-masque` | 0.80 (fraction of the frame's light the disc mask must capture) | On the reference sequence, the 33 clear failures all score under 0.50; between 0.50 and 0.92 the scores spread continuously rather than falling into two clusters, so no single cutoff in that band is objectively "the" right one. | Higher trusts a center only if the mask explains a larger share of the frame's light; too high starts discarding correct centers whenever the lit area legitimately shrinks (deep crescent, an unfiltered totality's halo — see `--seuil-lumiere` below). |
| `--seuil-lumiere` | preset-dependent: 0.35 (`custom`/`moon`/`planetary`), 0.70 (`sun`) | Measured on a solar-totality video: 0.60 → a `masse_captee` median of 0.730, 0.70 → 0.898 (the knee), no further gain above; by 0.90 every frame scores 1.000 and the measurement stops discriminating at all. | Fraction of a frame's peak brightness counted as "light" when checking whether the disc mask captures it (pass 1, baked into the cache). Too low on an unfiltered totality lets the corona's halo escape a disc-sized mask and wrongly fails a correct center; too high saturates and the check stops telling correct centers from wrong ones. |
| `--tolerance-bord` | 5.0 px (disc clipping tolerated at the source edge before rejection) | Measured on a 799 px reference disc: 25 px is 3 % of the diameter and visibly clipped, 5 px (0.6 %) is not; kept-frame counts rise slowly with it (1785 at 0 px, 1805 at 5 px, 1846 at 10 px, 1896 at 25 px). An unrelated edge-replication safety margin also stops fully protecting the frame past a source-geometry-dependent point (around 37 px on that same video). | Higher tolerates more clipping of the disc by the source edge before a frame is rejected as `hors_source`, trading fewer rejected frames against visible cropping once it grows large relative to the disc. |

**Automatic detection, and how to override it.** `analyze` and `run` probe a spread of sample frames across the source and print the detected type before analyzing — the CLI prints, for example, `Type d'eclipse detecte : moon`. Detection looks at the sky background (bright daylight/halo vs. a black sky), the disc's own internal contrast (a shadow crossing it), a size cutoff for a small planetary disc, and, as a tie-break, a warm color cast (a filtered sun). It is a *suggestion*, never applied silently and never final: pass `--preset sun|moon|planetary|custom` on the command line to force one, or use the selector in the viewer's Source panel, which shows the type currently in force: as long as nothing has been chosen there, the type the cache was analyzed under applies, or, failing that, the suggestion. Because the preset drives pass-1 measurement strategies, **changing it — from the CLI or the viewer — always requires a fresh analysis**: the cache records its own preset and refuses to be reused under a different one, with a message describing what to relaunch.

## Known limitations

- **Command-line messages are in French** (the viewer itself is fully bilingual). Internationalizing the CLI is a welcome contribution.
- **Clipped highlights cannot be reconstructed** — exposure is re-leveled, but detail lost to saturation stays lost.
- **A solar totality's corona can be blown out by brightness normalization.** Photometry itself is untouched by the eclipse-type feature; this is an existing limitation that a totality simply makes more visible.
- **The `planetary` preset has never been calibrated against real footage** — only against synthetic frames. If you have real planetary or transit footage, [open an issue](https://github.com/bebkill/Eclipse-Cleaner/issues) with a sample: it's the fastest way to get it tuned.
- **On a partial-only solar video, automatic detection selects the `sun` preset**, whose sorting is slightly stricter than the historic (`custom`) behavior — 1661 vs. 1726 frames kept on the reference sequence. Pass `--preset custom` to reproduce the exact pre-preset result.
- **Two color renditions can coexist in one film** if a solar filter was removed mid-sequence. That is what really happened in front of the camera, so it is kept, like cloud crossings.
- The viewer's **Browse…** dialog needs a graphical session (it uses the system file dialog via `tkinter`). On a headless machine, pass the video path on the command line instead.
- On **macOS**, the Browse… dialog now opens through the system's native panel (`osascript`), which sidesteps the main-thread crash the viewer used to hit there ([#4](https://github.com/bebkill/Eclipse-Cleaner/issues/4)) — see [acknowledgments](#acknowledgments). The command-line fallback still applies on the rare system where `osascript` itself is unavailable: `python -m eclipse viewer path/to/video.mp4`.
- The test suite is developed on **Windows**; four Windows-specific tests skip themselves automatically on Linux/macOS.

What's being considered next (SER input, raw Bayer AVI, choice of output format…) lives in the [roadmap](ROADMAP.md).

## Tests

```bash
python -m pip install -e ".[dev]"
python -m pytest
```

Assertions run against synthetic frames with known ground truth; no real video is required.

## Share your results — and support the project

This program saved my video, and I hope it can help other eclipse chasers in the same situation. Don't hesitate to share your results, comments, and suggestions for improving this humble program — [issues](https://github.com/bebkill/Eclipse-Cleaner/issues) and pull requests are very welcome.

And if, like me, it rescued your eclipse video, you can support the project:

<a href="https://www.buymeacoffee.com/bebkill"><img src="https://img.shields.io/badge/☕%20Buy%20Me%20a%20Coffee-thank%20you!-yellow?style=for-the-badge" alt="Buy Me a Coffee"></a>

## Acknowledgments

Thanks to [@mireianievas](https://github.com/mireianievas) ([#1](https://github.com/bebkill/Eclipse-Cleaner/issues/1)), who diagnosed the macOS crash in the Browse… dialog and prototyped the `osascript` fix now shipping (co-authored), and who first prototyped dynamic cropping in her fork — the prompt behind the crop-frame control described above.

## License

[MIT](LICENSE) — free to use, modify, and share.

Clear skies! 🌘
