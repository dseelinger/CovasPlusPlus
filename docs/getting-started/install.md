# Install & setup

COVAS++ runs on **Windows** and needs **Python 3.11 or newer**. This page walks you through a
fresh install step by step. You'll do everything in **PowerShell** (Windows' built-in terminal).

!!! note "This is a public project — your secrets stay local"
    API keys, your personality/campaign files, logs, and your checklist are all **git-ignored**,
    so you set them up on your own machine. Nothing personal is stored in the project itself.

## 1. Get the code

If you have `git`:

```powershell
git clone https://github.com/dseelinger/CovasPlusPlus.git
cd CovasPlusPlus
```

Or download the project as a ZIP from the repository and unzip it, then `cd` into the folder.

## 2. Create a Python environment and install

From inside the project folder:

```powershell
# Create an isolated Python environment
python -m venv .venv

# Install everything COVAS++ needs to run
.venv\Scripts\pip install -r requirements.txt
```

This installs the speech-to-text engine, the audio libraries, and the web control panel. The
first launch may download the local speech model, which can take a minute.

## 3. Set up your keys and personal files

COVAS++ uses two cloud services by default. You can run it fully local instead (see
[The provider seam](#optional-run-fully-local-no-cloud) below), but the standard setup is:

### Anthropic (the language model)

Claude is what COVAS++ talks *with*. Set your key as a **Windows User environment variable**
named `ANTHROPIC_API_KEY`:

1. Press <kbd>Win</kbd> and type **"environment variables"**, then open
   **"Edit environment variables for your account."**
2. Under **User variables**, click **New…**
3. Name: `ANTHROPIC_API_KEY` — Value: your key (it starts with `sk-ant-`).
4. Click OK, then **restart PowerShell** so it picks up the new variable.

!!! warning "Never paste your key into a file in the project"
    The Anthropic key lives only in that environment variable — never in a file that could be
    committed. COVAS++ reads it from the environment.

### ElevenLabs (the spoken voice) — optional

ElevenLabs is the default cloud voice. If you want it, copy the template and paste your key in:

```powershell
Copy-Item ElevenLabsAPIKey.txt.example ElevenLabsAPIKey.txt
# then open ElevenLabsAPIKey.txt and paste your key
```

`ElevenLabsAPIKey.txt` is git-ignored. If you'd rather not use ElevenLabs at all, you can switch
the voice to the free local **Piper** (see below) and skip this.

### Your character

The companion's personality is composed from a shared **Base**, a selectable **Persona** (its
voice and register), and your **Campaign** (your personal Commander facts). To start from the
shipped default:

```powershell
Copy-Item personality.example.txt personality.txt
```

You can edit this by hand, or — more easily — from the **Personality** tab in the
[control panel](../control-panel.md) once you're running. See
[Personas & voice](../using/personas-voice.md) for the full picture. Your personal facts live in
a git-ignored campaign file, kept separate from the persona so switching voices never wipes them.

### Sound cues — optional

COVAS++ plays a short chirp when it starts listening, while it works, when an answer is ready,
and on a failure. These live in a git-ignored `sounds/` folder — the app runs fine without them.
Drop your own `.wav` files in and point [`config.toml`](../configuration.md) at them if you want
audible cues. See [The voice loop](voice-loop.md#sound-cues) for which cue plays when.

## 4. Verify everything

COVAS++ ships a preflight check that confirms your whole setup **without spending anything** — it
changes nothing and only makes free lookups:

```powershell
.\check_setup.bat
# or:  .venv\Scripts\python.exe check_setup.py
```

It checks, in order:

1. **Config** loads.
2. **Python packages** are all installed.
3. **Keys & files** — your `ANTHROPIC_API_KEY` is present, the ElevenLabs key file is readable,
   and your personality file exists.
4. **Anthropic API** — a free model list call actually works.
5. **ElevenLabs API** — a free voices lookup works (skipped if you're not using it).
6. **Audio devices** — it lists your microphones and speakers and names the defaults.

Each line reads `[ OK ]`, `[warn]`, or `[FAIL]`. When everything's good it ends with:

```text
[ OK ] All systems go, Commander. Ready for Phase 2.  o7
```

If a line fails, fix that item and run it again.

!!! tip "Pin your microphone"
    `check_setup.py` prints your audio device names. If Windows' default mic isn't the one you
    want, set `[audio].input_device` in [`config.toml`](../configuration.md) to the device name
    it printed.

## Optional: run fully local (no cloud)

COVAS++ has a swappable **provider seam** — you can replace the cloud pieces with local ones that
run free on your machine:

| Piece | Cloud (default) | Local (offline, free) |
|-------|-----------------|-----------------------|
| Language model | Anthropic Claude (tiered) | Ollama (e.g. Qwen) — *out-of-game / offline only* |
| Voice (TTS) | ElevenLabs | Piper |
| Speech-to-text | *(already local)* | faster-whisper |

Speech-to-text is **always local**. To use the free local **Piper** voice, set
`[tts].provider = "piper"` in [`config.toml`](../configuration.md) and download a Piper voice on
your machine. To use a fully local language model for out-of-game/offline use, see the local
proof-of-concept in the project README.

!!! info "In-game, the language model is Claude by design"
    A local language model good enough to be useful competes with Elite Dangerous for your GPU,
    so the in-game brain is always cloud Claude. Piper (voice) and Whisper (speech-to-text) are
    light CPU work and run happily alongside the game — Piper is the one local swap that saves
    money without fighting the game.

## Next

You're set up. Head to **[Running COVAS++](running.md)**.
