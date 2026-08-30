# Eclipse Cleaner — guide for absolute beginners (Windows)

**[🇫🇷 Version française](BEGINNERS.fr.md)**

Never used Python? Never typed a command? This page is for you.

First, some reassurance: **you will not write any code, and there is no script to create.** Eclipse Cleaner is a ready-made program. You only need to install it once by copy-pasting two commands, and then everything happens in your web browser, with buttons. Count about ten minutes for the installation.

## Step 1 — Install Python (once)

Python is the free software that Eclipse Cleaner runs on. The easiest way on Windows 10/11 is the Microsoft Store — no options to get wrong:

1. Click the **Start** button and type `Microsoft Store`, then open it.
2. In the Store's search box, type `Python 3.13`.
3. Pick the app named **Python 3.13** published by the *Python Software Foundation*, and click **Get** (or **Install**).
4. Wait for the installation to finish, then close the Store.

*(Alternative for those who prefer the classic installer from [python.org](https://www.python.org/downloads/): on the very first screen, tick the box **"Add python.exe to PATH"** before clicking Install. If you forget that box, Windows won't find Python later.)*

## Step 2 — Open a terminal

The "terminal" is simply a window where you type (or paste) commands. Windows already has one:

1. Click the **Start** button and type `terminal`.
2. Open the app called **Terminal** (or **Windows PowerShell** — either is fine).

A dark window opens with a blinking cursor. That's it — you're ready.

**How to run a command:** every gray box on this page has a small copy icon in its top-right corner (on the GitHub website). Click it, go to the terminal window, press **Ctrl+V** to paste (a simple right-click also pastes in the terminal), then press **Enter**. That's all a "command" is.

## Step 3 — Check that Python answers

Paste this in the terminal and press Enter:

```
python --version
```

- If it answers something like `Python 3.13.2` (any number **3.12 or higher**), perfect — go to step 4.
- If a Microsoft Store window opens instead, or you get *"'python' is not recognized"*, Python isn't installed yet: go back to step 1.
- If it answers a number **lower than 3.12**, install the current version from the Store (step 1); it will take over.

## Step 4 — Install Eclipse Cleaner (once)

Paste this command and press Enter:

```
python -m pip install https://github.com/bebkill/Eclipse-Cleaner/archive/refs/heads/main.zip
```

Lines will scroll by for a minute or two — that's normal, it's downloading. Yellow *warning* lines are harmless. When it's done, the last lines say **`Successfully installed …`**.

## Step 5 — Launch Eclipse Cleaner

Paste this command and press Enter:

```
python -m eclipse viewer
```

A page opens in your web browser. From there, no more commands — everything is buttons:

1. Click **Browse…** and pick your eclipse video.
2. Run the three steps shown on the page: extract the thumbnails, analyze the frames, produce the final video.
3. The cleaned video is written **next to your original**, with `-clean` added to its name (e.g. `myeclipse-clean.mp4`).

Keep the terminal window open while you use Eclipse Cleaner — it's the engine running behind the page. When you're done, simply close the browser tab and the terminal window.

## Every next time

Only one thing to do: open a terminal (step 2) and paste:

```
python -m eclipse viewer
```

## To update Eclipse Cleaner later

Paste this command (it's the step 4 command plus `--force-reinstall`, which makes sure the latest version really replaces the old one):

```
python -m pip install --force-reinstall https://github.com/bebkill/Eclipse-Cleaner/archive/refs/heads/main.zip
```

## Bonus — open a terminal directly in a folder

You don't need this for the viewer (the **Browse…** button finds your video for you), but it's handy if you later try the command-line mode described in the [README](README.md):

1. Open the **File Explorer** and navigate to the folder containing your video.
2. Right-click on an empty area of the folder and choose **"Open in Terminal"**.

The terminal opens already "inside" that folder, so you can refer to your files by their name alone.

## If something goes wrong

| Symptom | Fix |
|---|---|
| *"'python' is not recognized"* or the Store opens | Python isn't installed — do step 1. If you used the python.org installer, re-run it and tick **"Add python.exe to PATH"**. |
| *"'pip' is not recognized"* | Always type `python -m pip …` (as on this page), never `pip` alone. |
| Red error lines during step 4 | Check your internet connection and run the command again. If it persists, check `python --version` is 3.12 or higher. |
| The browser page doesn't open | Look in the terminal: it prints an address starting with `http://127.0.0.1` — copy it into your browser's address bar. |

Still stuck? Open an [issue](https://github.com/bebkill/Eclipse-Cleaner/issues) describing what you did and what the terminal answered — beginner questions are welcome.

Clear skies! 🌘
