# Thargoid Voices

A self-contained, **local** web app for generating, auditioning, and curating
Thargoid-style alien "voice" sound effects. Generate variants in the browser,
listen inline, and save the keepers into a folder of your choosing.

This is a standalone utility. It does **not** depend on, import from, or know
anything about any other project — it only writes WAV files into the output
folder you point it at.

- **Synthesis** runs server-side (numpy additive/FM synthesis + a
  [pedalboard](https://github.com/spotify/pedalboard) DSP chain). The browser
  cannot do this, which is why there's a Python backend.
- **Binds to `127.0.0.1` only.** No auth, no external exposure — it's a personal
  local tool.
- **Audio format:** 48 000 Hz, peak-normalised, PCM_16 mono WAV.

---

## Install

`pedalboard` currently ships wheels up to Python 3.13 (not 3.14 yet), so create
the virtual environment with a **3.13** interpreter.

```powershell
cd thargoid-voices-app
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Launch

```powershell
.\.venv\Scripts\python.exe app.py
```

It starts the server on <http://127.0.0.1:5005/> and tries to open your browser.
If it doesn't open automatically, browse there yourself.

---

## Workflow

The loop is: **generate → listen → save keepers → regenerate → repeat.**

1. **Set your output folder** (top right). Paste the path to your target
   "Thargoid Voices" folder and click **Set**. The path is validated (must exist
   and be writable) and remembered between runs in `config.json`.
2. **Pick an utterance type** from the tabs:
   - **Hostile Shriek** — aggressive rising screech.
   - **Scan Query** — inquisitive warble that lifts at the end.
   - **Distress Wail** — mournful falling vibrato wail.
   - **Short Click-Chirp** — quick clicks then a rising chirp.
3. **Set the sliders** (each type loads sensible defaults):
   - **Pitch** — size / alien-ness (maps to PitchShift).
   - **Harshness** — distortion drive.
   - **Reverb** — space / size (maps to Reverb room + wet).
   Choose how many **variants** to render (1–12) and, optionally, a **seed**
   (blank = fresh randomness each time; set it to reproduce a result).
4. **Generate** (this type) or **Generate all types**. Variants appear below,
   newest first, each with an inline player plus duration and peak/RMS levels.
   Clipping variants are flagged in red (normalisation makes this rare).
5. **Save** the ones you like. Each variant's **Save** button writes *that* WAV
   into `<output folder>/<utterance_type>/` with a timestamped, non-colliding
   filename. **Nothing is ever overwritten or deleted.** Un-saved variants are
   discarded when you regenerate or clear.

Generated audio is served from an in-memory render — variants are **not**
auto-written to disk. Only clicking **Save** writes a file.

### Reproducing a result

Every variant shows its **seed** (click it to copy). Re-enter the same seed with
the same slider values and utterance type to regenerate an identical variant.

---

## Optional: real-sample excitation

`synth.render_variant(..., source_path="some.wav")` will use a real WAV as the
excitation instead of pure synthesis (it's forced to mono @ 48 kHz, given a
ring-mod edge, then run through the same DSP chain). This seam exists in the
engine but is not wired into the UI — synthesis-only is the primary path.

---

## Files

```
thargoid-voices-app/
├── app.py             # Flask backend (localhost only): generate / audio / save / config
├── synth.py           # audio engine: synthesis + pedalboard DSP + WAV encode
├── requirements.txt   # pinned deps (Flask, numpy, soundfile, pedalboard)
├── static/
│   ├── index.html     # single-page UI
│   ├── style.css
│   └── app.js
├── config.json        # created at runtime; remembers last-used output folder (git-ignored)
└── README.md
```

---

## License note

This app uses **[pedalboard](https://github.com/spotify/pedalboard)**, which is
licensed under the **GPLv3**. That's fine for this use: pedalboard runs as a
local tool on your own machine and you distribute the *rendered WAV files*, not
the tool. If you ever redistribute this app itself, mind the GPLv3 terms.
