# COVAS++

[![Docs](https://img.shields.io/badge/docs-COVAS%2B%2B-deeporange)](https://dseelinger.github.io/CovasPlusPlus/)

📖 **[Full documentation site →](https://dseelinger.github.io/CovasPlusPlus/)** — install, setup, and a
page per feature with example voice commands.

A local **voice AI companion** for [Elite Dangerous](https://www.elitedangerous.com/), for Windows.
Hold a key, talk, and get a spoken reply in character. It converses, watches your game
state, tracks a checklist, runs galaxy-wide searches by voice, presses the odd ship
control on request, and can look things up on the web — all through one push-to-talk loop.

It **does not fly your ship** — it's a conversation and knowledge companion, not a
flight-control tool: situational awareness, lookups, and banter, with a hard safety layer
around the few keystrokes it will send.

> Unofficial, fan-made. Elite Dangerous is a trademark of Frontier Developments plc.
> COVAS++ is not affiliated with, endorsed by, or supported by Frontier.

---

## The voice loop

1. **Hold** the push-to-talk key and speak. A *listening* chirp plays; your mic is captured while held.
2. **Release** → a *processing* chirp; speech is transcribed locally (faster-whisper — nothing leaves your machine for STT).
3. The transcript streams to the LLM with your personality, rolling history, and — when relevant — live game state.
4. A *done* chirp plays; the reply is spoken aloud (ElevenLabs cloud voice, or local Piper).
5. **Cancel** anything mid-flight with a brief tap of the same PTT key.

Every stage fails soft: a dead TTS degrades to on-screen text, a provider error returns you
to idle — the session never crashes out from under you.

---

## Features

### 🎙️ Conversation & voice
- **Push-to-talk voice loop** with instant local **sound cues** (listening / processing / done / failed), each a randomized set for variety.
- **Local speech-to-text** via faster-whisper (`tiny`…`large-v3`), CPU or CUDA.
- **Streaming replies** spoken aloud, kept short by design (they're spoken, not read).
- **Personality system** — the system prompt is composed as **Base + Persona + Campaign**: swap the *voice/register* (personas) without wiping your *personal Commander facts* (campaign). Ships with selectable presets; save your own custom personas.
- **Adjustable speaking speed** and voice selection for the cloud voice.
- **Rolling conversation history** so follow-ups ("what about the other one?") work in-session.

### 🧠 Cost-engineered LLM (cloud tiering)
Cost is controlled by **routing across cloud models**, not by running a local model (a useful
local LLM fights Elite Dangerous for the GPU). The design and rationale live in
[`DESIGN_AND_ROADMAP.md`](DESIGN_AND_ROADMAP.md).

- **Tiering router** — routine turns answer on **Haiku** (banter, acks, checklist reads, status readouts); escalates to **Sonnet** for depth/analysis or current-data turns; **Opus** only on explicit ask. Rules-based, explainable, and logged so you can tune it from real transcripts. Manual pin/override always available.
- **Prompt caching** on the (static) personality + tool schemas, with a **1-hour cache TTL** option that survives the long gaps between in-game voice turns.
- **`max_tokens` cap** kept low for spoken replies, auto-raised by the router for an explicit "full breakdown" turn.
- **Per-turn usage & cost logging** — the token counts the API returns (including cache reads/writes) turned into a rough dollar estimate per call, so tuning is data-driven.
- **Dev-mock mode** — swaps in fake LLM/TTS/STT so you can exercise the whole loop with **zero API calls and zero cost** while iterating.

### 🌐 Web search
Native web search runs automatically whenever a turn needs current information — capped per
reply to keep context (and cost) from ballooning.

### 🛰️ Elite Dangerous game-state awareness
Reads the same journal + `Status.json` files ED writes to disk — **no memory reading, no API keys**.

- **Journal & Status watchers** tail the newest journal and diff the status flags, publishing semantic events (jumps, docks, missions, low fuel, overheating, death…) on an internal event bus.
- **Live context** — a rolling snapshot (system, station, ship, fuel, cargo, danger/interdiction state) plus a recent-events feed.
- **Cheap local answers** — "where am I / how's my fuel / what's my cargo / check my logs" are answered from real telemetry via read tools (`where_am_i`, `ship_status`, `recent_events`), injected inline only on turns that need it, so the prompt cache stays intact.

### 📣 Proactive callouts *(opt-in)*
The companion **initiates** speech on notable events — arriving in a system, docking, mission
complete, low fuel, overheating, death — a short in-character line on the cheap tier. It never
talks over you (fires only when idle; a PTT tap cancels it), is rate-limited per event and
globally, and is mutable by voice ("stop the callouts").

### 🗺️ Route callouts *(opt-in)*
While flying a plotted route (`NavRoute.json` + jump progress): warns whether the **next star is
scoopable** (KGBFOAM) so you don't arrive dry, announces **jumps remaining** every Nth jump, and
calls out **arrival**. Each callout kind is individually toggleable.

### 🔎 Voice search (galaxy-wide, LLM-native)
Conversational, multi-turn slot-filling over the Spansh API — say what you want in plain speech,
the companion asks only for what it's missing, validates every spoken value against a bundled
canonical vocabulary (so a misheard filter is *corrected*, not silently widened), and copies the
result system to your clipboard.

- **Outfitting / modules** — "find the closest station that sells a Class 5 Frame Shift Drive." Offline module taxonomy resolves the module (name/size/mount/rating) before a single network call.
- **Star systems** — by allegiance, government, economy, security, population, Powerplay, or colonization state.
- **Stations** — by service, type, landing pad, distance, or faction ("nearest station with a shipyard and a large pad").
- **Minor factions** — where a faction is present or in control, or by allegiance/government/state.
- **Signals / structures** — nearest megaship, settlement, outpost, or starport.
- **Faction states** — nearest war, civil war, boom, election, infrastructure failure (and the missions those spawn).

Anti-hallucination is structural: any name spoken back must resolve against the registry or a
canonical source, or you get a templated "did you mean…" instead of an invented answer.

### 📍 Location & fleet carriers
"Copy my current system" and "where's my fleet carrier." Your **personal (owned) carrier** is
auto-tracked from the journal (name, callsign, location), pinned to its Carrier ID so a squadron
carrier you're aboard can't be mistaken for it.

### 🎯 Community Goals *(journal-primary, optional external feed)*
"What's my standing in \<CG\>" and the CGs you've visited come from your journal — offline, no
config. Add a free Inara API key to also surface active CGs you *haven't* visited. No key → stays
journal-only, fail-soft.

### ✅ Voice checklist
Your "ultimate checklist" markdown is read and updated by voice — "what's my next item," "mark it
complete" — exposed to the LLM as tools.

### 🎮 Keybind automation *(opt-in, heavily guarded)*
A one-action prototype — **toggle landing gear** — proving reliable ED input before generalizing:
- Reads your **actual bindings** (resolves the active preset, extracts the keyboard bind), so it's portable across setups.
- Injects at **scancode level** (`SendInput`), which is what ED actually listens to.
- Safety layer: **allowlist** of permitted macros, **explicit separate-turn confirmation** (the model can't arm and fire in one turn), a **combat/interdiction guard** (refuses when in danger *or* when it can't prove it's safe), a **confirmation expiry**, and a **hard abort** that releases any held key.

### 🛸 Auto-honk *(opt-in)*
Fires the **Discovery Scanner** on arrival in a new system, hands-free. Reads your current fire
group from Status, cycles to the scanner group, holds fire, cycles back — combat-gated, on a
non-blocking thread, sharing the keybind executor's hard abort.

### 🆘 Help subsystem
"What can you do?" is a **templated projection of the live capability registry** — no LLM
guesswork. It's a hierarchy (groups → capabilities → detail) that scales as capabilities grow,
and it doubles as **failure recovery**: an utterance it can't resolve becomes "I didn't recognize
'power distributer' — did you mean Power Distributor?"

### ⚙️ Settings, voice- and web-settable
A single settings schema is the source of truth, projected two ways: a clean **web settings page**
(writes `overrides.json`, layered over `config.toml`) and a **voice capability** so you can change
settings by talking.

### 🖥️ Local control panel
A localhost Flask UI (`run_covas_ui.py`) with a **Configuration** view, a **Personality** editor,
and a **live log** you can filter to conversation-only or everything — plus a CANCEL button that
always works.

---

## Provider seam — cloud or local

The three swappable pieces of the loop live behind a small provider interface (`covas/providers/`):

| Piece | Cloud (default) | Local (offline, free) |
|-------|-----------------|-----------------------|
| **LLM** | Anthropic Claude (Haiku / Sonnet / Opus, tiered) | Ollama (e.g. Qwen) — out-of-game / offline only |
| **TTS** | ElevenLabs | Piper |
| **STT** | — | faster-whisper (already local) |

Select providers in `config.toml` under `[llm]` and `[tts]`. In-game the LLM path is always
Anthropic by design — a capable local model competes with ED for the GPU. Piper TTS and Whisper
STT are light CPU work and run happily alongside the game.

---

## Quick start (from a fresh clone)

This is a public repo; **secrets and personal data are git-ignored**, so set them up locally:

```powershell
# 1. Python env (3.11+)
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# 2. Secrets & personal files (copies of the .example templates)
Copy-Item ElevenLabsAPIKey.txt.example ElevenLabsAPIKey.txt   # paste your key (cloud TTS only)
Copy-Item personality.example.txt personality.txt             # make the character yours
Set-Content AnthropicAPIKey.txt 'sk-ant-...'                  # paste your key (cloud LLM, required)
#   Keys are DPAPI-encrypted at rest on first read — never stored plaintext, never read from env vars.
#   Easier: skip this and enter keys in the first-run wizard / Settings "API keys" card.

# 3. (Optional) sound cues: drop your own .wav files in sounds/ — see config.toml

# 4. Verify
.\check_setup.bat        # or: .venv\Scripts\python.exe check_setup.py
```

### Run

```powershell
.venv\Scripts\python.exe run_covas.py        # headless voice loop
.venv\Scripts\python.exe run_covas_ui.py     # + localhost control panel (http://127.0.0.1:8765)
```

Most game-awareness features (`[elite]`, callouts, keybinds, honk) are **off by default** — opt in
per feature in `config.toml` (each section is commented with what it does and what it needs).

---

## Local offline mode (proof of concept)

Runs the whole loop with **no cloud and no cost** — Whisper + Qwen (Ollama) + Piper.

```powershell
# prereqs on this machine
ollama serve                             # in its own terminal (this one blocks)
ollama pull qwen3                        # then, in another terminal:
python -m piper.download_voices en_US-lessac-medium   # then set [piper].model in config.toml

# try it
python poc_local_loop.py                 # text REPL: type -> Qwen -> Piper speaks
python poc_local_loop.py --say "Systems nominal, Commander."   # TTS smoke test
python poc_local_loop.py --from-wav clip.wav                    # STT smoke test
python poc_local_loop.py --mic           # push-to-talk full local loop
```

To make the **main** app local, set `[llm].provider = "ollama"` and `[tts].provider = "piper"`.

---

## Configuration

Everything is driven by [`config.toml`](config.toml) — the single, fully-commented source of
default settings, with **relative paths** so the checkout stays portable. Anything you change in
the web UI (or by voice) is written to `overrides.json` and layered on top at runtime, so
`config.toml` stays pristine; delete a key from the overrides to reset it.

Notable sections: `[router]` (tiering), `[anthropic]` (model, caching, `max_tokens`),
`[elite]` (game monitoring + context detection), `[proactive]` / `[route]` (callouts),
`[keybinds]` / `[honk]` (guarded automation), `[nav]` / `[star_systems]` / `[search]` (voice
search), `[cg]` (community goals), `[personality]`, `[whisper]`, `[elevenlabs]` / `[piper]`.

---

## Testing

The default test run is **free and hermetic** — unit tests only, no network, API, audio, or
hardware (dependencies are injected; tests pass fakes). Anything that talks to a real service is
an opt-in, marked integration test.

```powershell
python -m compileall covas                    # fast sanity check after edits
pytest                                        # UNIT tests — offline, free, run often
pytest -m "integration and local"             # free integration (Ollama/Piper/Whisper/audio)
pytest -m "integration and paid"              # deliberate, COSTS money (Anthropic/ElevenLabs)
```

Ship-critical paths (audio devices, the running game, ElevenLabs) need real hardware and can't be
fully exercised in CI.

---

## Project layout

| Path | What it is |
|------|-----------|
| `covas/app.py` | Orchestration: PTT, threading, cancellation, worker loop. |
| `covas/providers/` | Cloud + local LLM/TTS/STT behind a common interface. |
| `covas/router.py` | The cost/tiering policy — the one place model routing lives. |
| `covas/llm.py` | Anthropic streaming: prompt caching + tools. |
| `covas/events.py` | Thread-safe event bus — the spine new inputs publish to. |
| `covas/capabilities/` | Self-contained feature modules (checklist, ED context, search, help, callouts, keybinds…). |
| `covas/ed/` | Journal + Status watchers, live context, route tracking. |
| `covas/search/` | Shared typed Spansh client + per-category builders/parsers + offline vocab. |
| `covas/nav/`, `covas/keybinds/`, `covas/cg/` | Outfitting search, guarded input executor, community-goal feeds. |
| `covas/web.py` + `covas/templates/` | Flask control panel. |
| `covas/config.py` | `config.toml` + `overrides.json`, relative paths resolved to absolute. |
| `poc_local_loop.py` | Standalone offline (local) proof of concept. |
| `config.toml` | All defaults, commented. Portable. |
| `overrides.json` | *(git-ignored)* live UI/voice changes, layered over `config.toml`. |
| `personality.txt` / `campaign.txt` | *(git-ignored)* your character + personal Commander facts. |
| `AnthropicAPIKey.txt` / `ElevenLabsAPIKey.txt` | *(git-ignored)* your provider keys, DPAPI-encrypted at rest. |
| `DESIGN_AND_ROADMAP.md` | Architecture, cost strategy, and build status. |
| `CLAUDE.md` | Repo context/conventions. |

---

## Keys & secrets

Enter keys in the **first-run wizard** or the Settings **API keys** card (or paste them into the
git-ignored files below). Every key is **encrypted at rest with Windows DPAPI** (`CurrentUser`
scope) — never stored plaintext, and **environment variables are no longer read for keys** (#22).
A plaintext key you drop into a file is migrated to an encrypted `DPAPI:<blob>` on first read; a
blob won't decrypt on a different machine/account, so re-enter keys after a move.

- **Anthropic key** — `AnthropicAPIKey.txt` (git-ignored). **Required.** *(Cloud LLM.)*
- **ElevenLabs key** — `ElevenLabsAPIKey.txt` (git-ignored). *(Cloud TTS only.)*
- **Inara key** (optional) — for the full Community Goals list; `InaraAPIKey.txt` (git-ignored).

This is a public repo: API keys, personality/campaign files, logs, checklists, sounds, voice
models, and `overrides.json` are all git-ignored. Keep them that way. Tip: create **spend-capped
or restricted keys** at each provider as defense-in-depth.

---

## License

MIT (see [`LICENSE`](LICENSE)) — covers the source only. Supply your own rights for any sound cues
or voice models you add locally.
