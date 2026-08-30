# Contributing to Eclipse Cleaner

Thank you for considering a contribution! This is a small hobby project born
from one rescued eclipse video, so the process is deliberately lightweight.
Issues and pull requests are welcome **in English or in French**.

## Reporting bugs and suggesting features

Open an [issue](https://github.com/bebkill/Eclipse-Cleaner/issues) using the
matching template. For bugs, the most useful things you can provide are:

- the exact command you ran and its full output;
- your OS and Python version (`python --version`);
- what the source video looks like (resolution, frame rate, roughly what
  happens in it) — a short sample clip is gold, but never share footage you
  don't have the rights to.

## Development setup

Requires **Python 3.12+**.

```bash
git clone https://github.com/bebkill/Eclipse-Cleaner.git
cd Eclipse-Cleaner
python -m pip install -e ".[dev]"
```

## Running the tests

```bash
python -m pytest
```

The suite runs against synthetic frames with known ground truth — no real
video is required. It is developed on Windows and runs in CI on Windows and
Ubuntu (Python 3.12 and 3.14); a few Windows-specific tests skip themselves
automatically elsewhere.

## Pull requests

- Open an issue first for anything bigger than a small fix, so we can discuss
  the approach before you invest time in it.
- Add or adjust tests for behavior changes; `python -m pytest` must pass.
- Keep the scope of a PR focused: one topic per PR is much easier to review.
- Update the README (both `README.md` and `README.fr.md`) if your change is
  user-visible.

## Ideas that are especially welcome

- **Internationalizing the CLI** — the command-line messages are currently in
  French (the viewer is already bilingual).
- Real-world reports: videos where the disk localization, stabilization, or
  exposure normalization struggles.

Clear skies! 🌘
