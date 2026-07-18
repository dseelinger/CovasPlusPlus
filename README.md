# COVAS++

[![Docs](https://img.shields.io/badge/docs-COVAS%2B%2B-deeporange)](https://dseelinger.github.io/CovasPlusPlus/)

📖 **[Full documentation site →](https://dseelinger.github.io/CovasPlusPlus/)** — install, setup, and a
page per feature with example voice commands.

A local **ship's AI** for [Elite Dangerous](https://www.elitedangerous.com/), for Windows.
The in-game COVAS reads canned lines; COVAS++ holds a conversation. Hold a key, talk, and get a
spoken reply in character. It knows where you are and how your ship is doing because it reads the
same journal Elite writes to disk — so its answers are **grounded in your actual game, not
guessed**. It searches the whole galaxy by voice, plans routes, tracks your engineering materials,
keeps a checklist, remembers what matters to you, presses guarded ship controls on request, and
fills the cockpit with personas, a voiced crew, and an ambient soundscape — all through one
push-to-talk loop, with a hard safety layer around every keystroke it will send.

> Unofficial, fan-made. Elite Dangerous is a trademark of Frontier Developments plc.
> COVAS++ is not affiliated with, endorsed by, or supported by Frontier.

---

## Why COVAS++

Other Elite voice assistants (COVAS:NEXT, EDCoPilot) converse and read your game state too. What
sets COVAS++ apart:

- **Grounded, not guessed.** Ship specs, module costs, blueprint materials, engineer unlocks and
  your credits come from **bundled datasets and your real journal**, not the model's training-cutoff
  memory — so the newest hulls stay accurate and money is never invented. Voice search is
  **structurally anti-hallucination**: any name spoken back must resolve against a canonical
  vocabulary, or you get a "did you mean…" instead of a made-up answer.
- **Galaxy-wide search & planning by voice** — 13 categories over the Spansh API: the nearest
  station selling a module or a ship, systems/stations/factions matching what you describe, bodies by
  type or exobiology, plus **trade-route, neutron, Road-to-Riches and mining planners** — each result
  copied to your clipboard for the galaxy map.
- **Cost-engineered for cloud LLMs.** A rules-based router keeps routine turns on cheap Haiku and
  escalates only when a turn earns it, with prompt caching and per-turn cost logging — so a
  always-connected companion stays affordable without a local model fighting Elite for your GPU.
- **Immersion, not just answers** — an optional cockpit **ambient-audio** layer, a **multi-voice
  interactive crew**, a glanceable **HUD** overlay, and swappable **personas**.
- **Local-first and private.** Speech-to-text (**Whisper**) and the optional **Piper** voice always
  run **on your machine** — CPU-only, no GPU contention with the game — and API keys are
  DPAPI-encrypted at rest. The LLM is cloud (cost handled by tiering, not a local model).
- **Safety-first automation.** The handful of keystrokes it will send sit behind an allowlist,
  a separate spoken confirmation, a combat/interdiction guard, and a hard abort.
- **Hands-free option** for accessibility — a voice-activity gate so you never have to touch a key.

---

## Features

### 🔎 Voice search & route planning (galaxy-wide, LLM-native)
Conversational, multi-turn slot-filling over the Spansh API — say what you want in plain speech, the
companion asks only for what it's missing, validates every spoken value against a bundled canonical
vocabulary (so a misheard filter is *corrected*, not silently widened), and copies the result to your
clipboard.

- **Outfitting / modules** — "find the closest station that sells a Class 5 Frame Shift Drive." Offline module taxonomy resolves the module before a single network call.
- **Shipyards** — "find the closest Anaconda."
- **Star systems** — by allegiance, government, economy, security, population, Powerplay, or colonization state.
- **Stations** — by service, type, landing pad, distance, or faction ("nearest station with a shipyard and a large pad").
- **Minor factions** — where a faction is present or in control, or by allegiance/government/state.
- **Faction states** — nearest war, civil war, boom, election, infrastructure failure (and the missions those spawn).
- **Signals / structures** — nearest megaship, settlement, outpost, or starport.
- **Body finder** — nearest Earth-like/ammonia/water world, or a body with a given exobiology signal.
- **Trade-route planner** — a profitable multi-hop loop from where you're docked, with stale-price warnings.
- **Neutron / long-range route** — a neutron-highway plot to a distant system; total jumps and first waypoint.
- **Road-to-Riches planner** — nearby systems full of high-value bodies to First-Discovery-scan.
- **Mining helper** — nearest ring hotspot for a material, the best *fresh* place to sell it, and the run dropped onto your checklist.

Anti-hallucination is structural: any name spoken back must resolve against the registry or a
canonical source, or you get a templated "did you mean…" instead of an invented answer.

### 🛰️ Elite Dangerous game-state awareness
Reads the same journal + `Status.json` files ED writes to disk — **no memory reading, no API keys**.

- **Journal & Status watchers** tail the newest journal and diff the status flags, publishing semantic events (jumps, docks, missions, low fuel, overheating, death…) on an internal event bus.
- **Live context** — a rolling snapshot (system, station, ship, fuel, cargo, danger/interdiction state) plus a recent-events feed.
- **Cheap local answers** — "where am I / how's my fuel / what's my cargo / check my logs" answered from real telemetry via read tools, injected inline only on turns that need it, so the prompt cache stays intact.

### 📚 Grounded ship & engineering knowledge
Answered from **bundled datasets + your live journal**, never the model's memory — a real number when
COVAS has one, an honest "I don't have that yet" when it doesn't.

- **Ship specifications** — pad size, hull mass, hardpoints, slots, cargo, for *any* hull including the newest (Panther Clipper Mk II, Python Mk II, Type-8, Mandalay, Cobra Mk V, Corsair).
- **Ship loadout & engineering** — reads your fitted modules, blueprints, grades and experimental effects from the journal, and can reason over the build.
- **Blueprint & material sourcing** — knows what every blueprint costs, checks your live inventory, tells you **only what you're short**, where to farm it, and can drop the farm plan onto your checklist.
- **Engineers finder** — who upgrades a given module and where, plus **your** unlock progress from `EngineerProgress`.
- **On-foot (Odyssey) engineering** — suit/weapon grade upgrades, materials, perks, and which of the 13 on-foot engineers unlocks each.
- **Stored ships & modules** — where each parked ship / shelved module is, and the cost/time to transfer it.
- **Credits & currencies** — your real balance from the journal; never an invented amount.

### 🎙️ Conversation, voice & personas
- **Push-to-talk voice loop** with instant local **sound cues** (listening / processing / done / failed), each a randomized set for variety.
- **Local speech-to-text** via faster-whisper (`tiny`…`large-v3`), CPU or CUDA — nothing leaves your machine for STT.
- **Streaming replies** spoken aloud, kept short by design (they're spoken, not read).
- **Personality system** — the system prompt is composed as **Base + Persona + Campaign**: swap the *voice/register* (personas) without wiping your *personal Commander facts* (campaign). Ships with selectable presets; save your own.
- **Interactive crew** *(opt-in)* — an ordinary reply can also voice a **named crew member** in its own distinct voice; the persona stays the default narrator, crew chime in only when a line is theirs.
- **Adjustable speaking speed** and voice selection for the cloud voice.
- **Rolling conversation history** so follow-ups ("what about the other one?") work in-session.
- **Hands-free listening** *(opt-in)* — a voice-activity gate hears you and runs the turn without touching a key; push-to-talk keeps working alongside it.

### 🧠 Persistent memory *(opt-in)*
Keeps a small, **transparent** set of facts about you — how you like to be addressed, your main ship,
standing preferences — in a plain text file you own and can edit or delete. It **captures**
automatically (journal milestones + durable facts you mention), **recalls** the right facts into a
turn when you reach into the past, and has a **memory browser** in the control panel. Recall is
injected per-turn so it never busts the prompt cache.

### 🎧 Ambient audio *(opt-in)*
An optional atmospheric layer — an in-cockpit soundscape of **radio comms, ambient chatter, music,
and layered alert cues**, all mixed underneath your companion's voice. A big, entirely optional
subsystem behind a master switch, with per-part toggles; most of it is driven by game events.

### 🧠 Cost-engineered LLM (cloud tiering)
Cost is controlled by **routing across cloud models**, not by running a local model (a useful local
LLM fights Elite Dangerous for the GPU). The design and rationale live in
[`DESIGN_AND_ROADMAP.md`](DESIGN_AND_ROADMAP.md).

- **Tiering router** — routine turns answer on **Haiku** (banter, acks, checklist reads, status readouts); escalates to **Sonnet** for depth/analysis or current-data turns; **Opus** only on explicit ask. Rules-based, explainable, and logged so you can tune it from real transcripts. Manual pin/override always available.
- **Prompt caching** on the (static) personality + tool schemas, with a **1-hour cache TTL** option that survives the long gaps between in-game voice turns.
- **`max_tokens` cap** kept low for spoken replies, auto-raised by the router for an explicit "full breakdown" turn.
- **Per-turn usage & cost logging** — the token counts the API returns (including cache reads/writes) turned into a rough dollar estimate per call, so tuning is data-driven.
- **Dev-mock mode** — swaps in fake LLM/TTS/STT so you can exercise the whole loop with **zero API calls and zero cost** while iterating.

### 📣 Proactive & route callouts *(opt-in)*
- **Proactive callouts** — the companion **initiates** a short in-character line on notable events (arriving in a system, docking, mission complete, low fuel, overheating, death) on the cheap tier. It never talks over you (fires only when idle; a PTT tap cancels it), is rate-limited, and is mutable by voice ("stop the callouts").
- **Route callouts** — while flying a plotted route (`NavRoute.json` + jump progress): warns whether the **next star is scoopable** (KGBFOAM), announces **jumps remaining** every Nth jump, and calls out **arrival**. Each callout kind is individually toggleable.

### 🎮 Guarded ship controls *(opt-in, heavily safety-gated)*
The few keystrokes COVAS++ will send read your **actual bindings** and inject at **scancode level**
(`SendInput`), which is what ED actually listens to — portable across setups.

- **Keybind automation** — the safe-state control path (e.g. **toggle landing gear**): an **allowlist**, an **explicit separate-turn confirmation** (the model can't arm and fire in one turn), a **combat/interdiction guard** (refuses in danger *or* when it can't prove it's safe), a **confirmation expiry**, and a **hard abort**.
- **Custom macros** — invent your **own** named macros by voice or in the control panel (an ordered recipe of ship actions, pauses, and game-state checks); COVAS remembers them, runs them on command, or fires them on a game event — under the same guard.
- **Combat reflexes** — the *inverted* policy for actions that only make sense **under fire**: **fire chaff** and **deploy heat sink**, on three paths (ask, a second push-to-talk hotword, or automatic), combat-*permissive* but never dangerous.
- **Send in-game messages (comms)** — compose and send ED chat (local/wing/squadron/direct) by voice; COVAS reads it back and sends only after you confirm.
- **Auto-honk** — fires the **Discovery Scanner** on arrival in a new system, hands-free: reads your current fire group from Status, cycles to the scanner group, holds fire, cycles back — combat-gated, on a non-blocking thread, sharing the keybind executor's hard abort.

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
complete" — exposed to the LLM as tools. Several features (blueprints, mining) can drop steps onto it.

### 🌐 Web search
Native web search runs automatically whenever a turn needs current information — capped per reply to
keep context (and cost) from ballooning.

### 🖥️ Companion HUD *(opt-in)*
A small, glanceable, transparent **overlay** on top of the game surfacing the companion-centric
information only COVAS++ has. A *view*, not a control surface — non-interactive and (on Windows)
click-through, so it never intercepts input meant for Elite. Absent (no error) on a headless machine.

### 🆘 Help subsystem
"What can you do?" is a **templated projection of the live capability registry** — no LLM guesswork.
It's a hierarchy (groups → capabilities → detail) that scales as capabilities grow, and it doubles as
**failure recovery**: an utterance it can't resolve becomes "I didn't recognize 'power distributer' —
did you mean Power Distributor?"

### ⚙️ Settings & local control panel
- A single settings schema is the source of truth, projected two ways: a clean **web settings page** (writes `overrides.json`, layered over `config.toml`) and a **voice capability** so you can change settings by talking.
- A localhost Flask UI (`run_covas_ui.py`) with **Configuration**, a **Personality** editor, a **memory browser**, and a **live log** you can filter to conversation-only or everything — plus a CANCEL button that always works.

### 🔄 Keeping game data current
The grounded datasets (ship specs, module costs, blueprints, engineers) are kept current with
Frontier's releases from community-maintained sources — *if the community maintains it, a developer
shouldn't have to re-type it* — and you can check how fresh your data is.

---

## The voice loop

1. **Hold** the push-to-talk key and speak. A *listening* chirp plays; your mic is captured while held. (Or switch on **hands-free** mode and just talk.)
2. **Release** → a *processing* chirp; speech is transcribed locally (faster-whisper — nothing leaves your machine for STT).
3. The transcript streams to the LLM with your personality, rolling history, and — when relevant — live game state.
4. A *done* chirp plays; the reply is spoken aloud (ElevenLabs cloud voice, or local Piper).
5. **Cancel** anything mid-flight with a brief tap of the same PTT key.

Every stage fails soft: a dead TTS degrades to on-screen text, a provider error returns you to idle —
the session never crashes out from under you.

---

## Provider seam — swappable pieces

The three swappable pieces of the loop live behind a small provider interface (`covas/providers/`):

| Piece | Cloud | Local (free) |
|-------|-------|--------------|
| **LLM** | Anthropic Claude / OpenAI-compatible / Google Gemini (tiered) | — (cloud only; cost handled by tiering) |
| **TTS** | ElevenLabs / Edge / Azure / OpenAI / Cartesia | Piper |
| **STT** | — | faster-whisper (CPU) |

Select providers in `config.toml` under `[llm]` and `[tts]`. The LLM is always cloud by design —
cost is handled by the tiering router, not a local model (a capable local model would compete with
ED for the GPU). Piper TTS and Whisper STT are light **CPU** work and run happily alongside the game.

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

Most game-awareness features (`[elite]`, callouts, keybinds, honk, ambient audio, HUD, crew, memory)
are **off by default** — opt in per feature in `config.toml` (each section is commented with what it
does and what it needs) or on the Settings page.

---

## Local, CPU-only speech

Speech never leaves your machine: **Whisper** STT and the optional **Piper** voice both run
locally on the **CPU**, so nothing competes with Elite Dangerous for the GPU. Set
`[tts].provider = "piper"` (and point `[piper].model` at a downloaded `.onnx` voice) for a fully
free, offline voice:

```powershell
python -m piper.download_voices en_US-lessac-medium   # then set [piper].model in config.toml
```

The LLM itself is cloud (Anthropic / OpenAI-compatible / Gemini) — cost is handled by the tiering
router, not a local model.

---

## Configuration

Everything is driven by [`config.toml`](config.toml) — the single, fully-commented source of default
settings, with **relative paths** so the checkout stays portable. Anything you change in the web UI
(or by voice) is written to `overrides.json` and layered on top at runtime, so `config.toml` stays
pristine; delete a key from the overrides to reset it.

Notable sections: `[router]` (tiering), `[anthropic]` (model, caching, `max_tokens`), `[elite]`
(game monitoring + context detection), `[proactive]` / `[route]` (callouts), `[keybinds]` /
`[macros]` / `[reflex]` / `[comms_send]` / `[honk]` (guarded automation), `[nav]` / `[star_systems]` /
`[search]` / `[bodies]` (voice search + planners), `[cg]` (community goals), `[audio]` (ambient),
`[hud]`, `[crew]`, `[memory]`, `[personality]`, `[whisper]`, `[elevenlabs]` / `[piper]`.

---

## Testing

The default test run is **free and hermetic** — unit tests only, no network, API, audio, or hardware
(dependencies are injected; tests pass fakes). Anything that talks to a real service is an opt-in,
marked integration test.

```powershell
python -m compileall covas                    # fast sanity check after edits
pytest                                        # UNIT tests — offline, free, run often
pytest -m "integration and local"             # free integration (Piper/Whisper/audio)
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
| `covas/capabilities/` | Self-contained feature modules (search, ED context, memory, checklist, help, callouts, keybinds, macros, reflexes…). |
| `covas/ed/` | Journal + Status watchers, live context, route tracking. |
| `covas/search/` | Shared typed Spansh client + per-category builders/parsers + offline vocab. |
| `covas/mixer/` | The ambient-audio subsystem (music, chatter, comms, cues, buses). |
| `covas/nav/`, `covas/keybinds/`, `covas/cg/` | Outfitting/route search, guarded input executor, community-goal feeds. |
| `covas/web.py` + `covas/templates/` | Flask control panel. |
| `covas/config.py` | `config.toml` + `overrides.json`, relative paths resolved to absolute. |
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

This is a public repo: API keys, personality/campaign files, logs, checklists, sounds, voice models,
and `overrides.json` are all git-ignored. Keep them that way. Tip: create **spend-capped or
restricted keys** at each provider as defense-in-depth.

---

## License

MIT (see [`LICENSE`](LICENSE)) — covers the source only. Supply your own rights for any sound cues
or voice models you add locally.
