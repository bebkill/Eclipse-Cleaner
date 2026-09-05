# Roadmap

**[🇫🇷 Version française](ROADMAP.fr.md)**

Ideas and improvements under consideration, mostly coming from user feedback. No dates and no promises — this is a hobby project maintained on spare time. If one of these matters to you, or if you have another suggestion, [open an issue](https://github.com/bebkill/Eclipse-Cleaner/issues): it is the best way to get an item prioritized.

## Eclipse types

**Shipped.** Repeated requests to process lunar eclipses — and a crash report on one — led to eclipse-type presets: `sun`, `moon`, `planetary` and `custom`, auto-detected from the source video and overridable with `--preset` or the viewer's Source-panel selector. See [Eclipse types and presets](README.md#eclipse-types-and-presets) for the parameter tables.

- **Real planetary/transit footage for calibration.** The `planetary` preset has only been validated against synthetic frames, never against a real video. If you have footage of a planetary transit (or any small, uniformly bright disc on a black sky) that this tool handles poorly, [open an issue](https://github.com/bebkill/Eclipse-Cleaner/issues) with a sample: it's the fastest way to get it tuned.

## Input formats

- **Raw Bayer AVI (planetary cameras).** An undebayered AVI — e.g. straight out of a planetary capture tool — currently comes out with a checkerboard-like "pixelation": the frames are decoded as-is and the Bayer mosaic is never interpreted. Planned: recognize (or let the user specify) the Bayer pattern and debayer the frames at extraction. *(Reported by a user, August 2026.)*
- **SER input.** The native capture format of SharpCap and of most planetary-astronomy tools, and a common container for small solar videos. *(Suggested by the same user.)*

## Output formats

- **Choice of output container: MP4, AVI or SER.** Today the output is always an MP4 written next to the source (plus, optionally, a numbered PNG sequence).

## Viewer

- **Dynamic cropping (ROI). Partly shipped.** The crop window's *size* is now under mouse control in the viewer — drag the handle on the central thumbnail's crop frame, or reset it to the recommendation — and used exactly as `--taille` would be, without touching the command line. Its *position* stays deliberately automatic: the window follows the tracked disk, and that tracking is the point of the whole pipeline, so there is no manual-position control and none is planned. *Manual rotation* was not carried over either, for the same reason: nothing in this project's own footage has needed a tilted crop yet. Both remain open to a new issue if a real case shows up. This built on [@mireianievas' fork](https://github.com/mireianievas/Eclipse-Cleaner), a working prototype of a fully manual crop (position, size and rotation) that first showed the need — see [#1](https://github.com/bebkill/Eclipse-Cleaner/issues/1). Her fork also diagnosed and fixed the macOS file-selection dialog crash; that fix is integrated (co-authored).

## Command line

- **English CLI messages.** The viewer is fully bilingual, but the command line still speaks French only ([known limitation](README.md#known-limitations)). A welcome contribution.
