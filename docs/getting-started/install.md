# Install & setup

COVAS++ is a **Windows app**. You download one installer, double-click it, and a first-run
wizard walks you through the rest — no Python, no terminal, no browser step. This page covers
that path; if you'd rather run from the source code, see
[Run from source](#run-from-source-advanced) at the bottom.

!!! note "This is a public project — your secrets stay local"
    Your API keys, personality/campaign files, logs, and checklist all live in your own
    per-user folder (`%APPDATA%\COVAS++`), never in the app itself. The installer never contains
    any keys — you supply them once, in the wizard, on your own machine.

## 1. Download the installer

Grab the latest **`COVAS++ Setup.exe`** from the project's
[**Releases page**](https://github.com/dseelinger/CovasPlusPlus/releases).

It installs **per-user** (into `%LOCALAPPDATA%\Programs\COVAS++`), so there's **no admin
prompt** — the same way Chrome, VS Code, and Discord install.

!!! warning "Windows will say \"unknown publisher\" — this is expected"
    COVAS++ isn't code-signed (a signing certificate is a paid, business-vetted thing this
    fan project skips), so Windows **SmartScreen** shows a blue *"Windows protected your PC"*
    screen on first run. To proceed:

    1. Click **More info**.
    2. Click **Run anyway**.

    Your antivirus may also flag it briefly for the same reason (no signature). The installer is
    the ordinary one-folder build described in the [design doc](https://github.com/dseelinger/CovasPlusPlus);
    if you'd rather verify everything yourself, [run from source](#run-from-source-advanced) instead.

## 2. Run the installer

Double-click **`COVAS++ Setup.exe`** and follow the prompts. It creates a **Start-menu entry**
and a **desktop icon**, and registers an uninstaller. Installing a newer version later just
installs cleanly over the old one — your settings are untouched (see
[Updating COVAS++](updating.md)).

## 3. First-run wizard

Launch COVAS++ from the **desktop icon** or the **Start menu**. The very first launch opens a
short setup wizard that builds your configuration from nothing. **You pick any AI + voice
combination you like** — you're not locked to one vendor:

1. **AI brain (LLM)** — choose the provider that powers the conversation and give it what it needs:
    - **Anthropic — Claude** (paste an `sk-ant-` key),
    - **OpenAI-compatible** — OpenAI / Groq / DeepSeek / OpenRouter (pick the endpoint preset, a
      model, and paste that service's key), or
    - **Google Gemini** (paste a Gemini key — Google's free tier is generous).

    All three are **cloud** providers — cost is handled by the tiering router, not a local model (a
    capable local model would compete with Elite for your GPU). This is **required** — without a
    working AI provider there's no brain — but it does **not** have to be Anthropic.
2. **Voice (TTS)** — choose how COVAS speaks. **Edge is the free default and needs no key**, so you
   always get a voice. You can also pick **ElevenLabs**, **Azure**, **OpenAI**, **Cartesia**, or
   local **Piper**. Only the premium cloud voices need their own key; leave a keyless cloud voice
   unset and COVAS runs **text-only** (it still listens and thinks, it just doesn't talk back) until
   you add one — but with Edge you never have to.
3. **Speech model** — COVAS++ downloads its local speech-to-text model
   (faster-whisper `small.en`, ~250 MB) with a progress bar. **This step needs internet**, and
   the model is downloaded **once** — it's not bundled in the installer, which keeps the download
   small. Speech-to-text always runs **locally** and free after that.
4. **Microphone** — pick the mic you'll push-to-talk with.

The **Launch** button lights up once your chosen AI provider is ready and the speech model is
installed — a voice is optional. A fully free path exists: **Gemini (free tier) or OpenRouter + Edge**
gets you talking without paying for a voice at all.

When the wizard finishes it hands straight off to the **control panel** in the same window, and
you're ready to talk. Everything you entered is saved under `%APPDATA%\COVAS++` and reused every
launch — you won't see the wizard again unless that folder is cleared.

!!! tip "Test my setup — one click, no terminal"
    If something isn't working, open the **Settings** page in the control panel and click
    **Test my setup**. It runs the same checks as `check_setup.py` — your keys, that each provider
    is reachable, your game data, and your mic/speakers — and shows a plain, readable pass/fail
    report right on the page. Nothing is changed and the provider calls are free. If you need help,
    **screenshot that report** and include it with your question — it's usually enough to spot the
    problem. Errors are shown as plain sentences ("COVAS couldn't sign in to Anthropic — the key
    looks wrong"), never a wall of code.

!!! tip "Where your keys and settings live"
    All of your writable state — keys, `overrides.json`, personality/campaign, checklist, and
    `logs/` — lives in **`%APPDATA%\COVAS++`**. The downloaded speech model lives in
    `%LOCALAPPDATA%`. Updates replace only the app itself and never touch these, so your setup
    survives every upgrade.

## Getting your keys

- **Anthropic** — create a key at [console.anthropic.com](https://console.anthropic.com/) under
  **API Keys**. Claude is billed per use; COVAS++ keeps costs low with a
  [cost router](../configuration.md) (cheap tier by default) and prompt caching.
- **ElevenLabs** *(optional)* — create a key at
  [elevenlabs.io](https://elevenlabs.io/) under your profile. Free/starter tiers work; the
  spoken replies are short by design.

## Next

You're set up. Head to **[Running COVAS++](running.md)**.

---

## Run from source (advanced)

Prefer to run the Python directly — to develop, to audit the code, or to use the fully-local
providers the packaged app doesn't ship? COVAS++ runs from source on **Windows** with
**Python 3.11 or newer**. Do everything in **PowerShell**.

### 1. Get the code

```powershell
git clone https://github.com/dseelinger/CovasPlusPlus.git
cd CovasPlusPlus
```

### 2. Create a Python environment and install

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

This installs the speech-to-text engine, the audio libraries, and the web control panel. The
first launch may download the local speech model, which can take a minute.

### 3. Set up your keys and personal files

A source run reads its state from the **project root** (not `%APPDATA%`). The easiest path is to
launch the app once (step 5) and enter your keys in the **first-run wizard** or the Settings
**API keys** card — each key is stored **DPAPI-encrypted** (see the note below). To set them up by
hand first, drop each key into its git-ignored file in the project root:

**Anthropic** *(required)* — paste your key (it starts with `sk-ant-`) into `AnthropicAPIKey.txt`:

```powershell
Set-Content AnthropicAPIKey.txt 'sk-ant-...'   # your key
```

The file is git-ignored, and the plaintext you paste is **migrated to a DPAPI-encrypted blob on
first read** — the app never keeps your key in plaintext on disk.

**ElevenLabs** *(optional)* — copy the template and paste your key in:

```powershell
Copy-Item ElevenLabsAPIKey.txt.example ElevenLabsAPIKey.txt
# then open ElevenLabsAPIKey.txt and paste your key
```

`ElevenLabsAPIKey.txt` is git-ignored (and encrypted at rest on first read, same as above). Prefer
not to use ElevenLabs? Switch the voice to the free local **Piper** (see below) and skip this.

!!! info "How your keys are stored"
    COVAS++ **never stores plaintext keys**. Every provider key (Anthropic, ElevenLabs, OpenAI,
    Gemini, Azure, Cartesia, Inara) is encrypted at rest with **Windows DPAPI** (`CurrentUser`
    scope) — Windows owns the encryption key, the app stores none, and the encrypted blob is
    useless on any other machine or account. **Environment variables are no longer read for keys.**
    If you move your data folder to a new PC or Windows account, the blobs won't decrypt there —
    just re-enter each key. As defense-in-depth, create **spend-capped or restricted keys** at each
    provider where you can.

**Your character** — start from the shipped default:

```powershell
Copy-Item personality.example.txt personality.txt
```

Edit it by hand, or — more easily — from the **Personality** tab in the
[control panel](../control-panel.md) once you're running. See
[Personas & voice](../using/personas-voice.md) for the full picture.

### 4. Verify everything

COVAS++ ships a preflight check that confirms your whole setup **without spending anything**. You
can run it two ways — both run the *same* checks:

- **In the app:** open **Settings → Test my setup** in the control panel and click the button (no
  terminal needed — this is what a packaged install uses).
- **From the command line:**

```powershell
.\check_setup.bat
# or:  .venv\Scripts\python.exe check_setup.py
```

It checks, in order: **config** loads, **Python packages** are installed, **keys & files** are
present, the **Anthropic API** answers a free model-list call, the **ElevenLabs API** answers a
free voices lookup (skipped if unused), and your **audio devices** are listed. Each line reads
`[ OK ]`, `[warn]`, or `[FAIL]`; when everything's good it ends with:

```text
[ OK ] All systems go, Commander. Ready for Phase 2.  o7
```

!!! tip "Pin your microphone"
    `check_setup.py` prints your audio device names. If Windows' default mic isn't the one you
    want, set `[audio].input_device` in [`config.toml`](../configuration.md) to the device name
    it printed.

### Local, CPU-only speech

The source tree has a swappable **provider seam**. Speech runs **locally on the CPU**, and the
voice can too — nothing here competes with Elite Dangerous for the GPU:

| Piece | Cloud | Local (free, CPU) |
|-------|-------|-------------------|
| Language model | Anthropic Claude / OpenAI-compatible / Gemini (tiered) | — (cloud only) |
| Voice (TTS) | ElevenLabs / Edge / Azure / OpenAI / Cartesia | Piper |
| Speech-to-text | *(already local)* | faster-whisper |

To use the free local **Piper** voice, set `[tts].provider = "piper"` in
[`config.toml`](../configuration.md) and download a Piper voice.

!!! info "The language model is always cloud by design"
    Cost is handled by the tiering router, not a local model: a local language model good enough to
    be useful would compete with Elite Dangerous for your GPU. Piper (voice) and Whisper
    (speech-to-text) are light CPU work and run happily alongside the game — Piper is the one local
    swap that saves money without fighting the game.
