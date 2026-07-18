# The control panel

Launch with the UI build and COVAS++ opens a local dashboard in your browser at
**[http://127.0.0.1:8765](http://127.0.0.1:8765)** — a status light, a live log, and full editors
for settings, personality, your checklist, your memory, and your crew.

```powershell
.\run_covas_ui.bat
# or:  .venv\Scripts\python.exe run_covas_ui.py
```

The panel is **local-only** (`127.0.0.1`) and isn't exposed to your network. Change the host/port
under `[ui]` in [`config.toml`](configuration.md) if you need to.

A small, muted **version tag** (`vX.Y.Z`) sits in the corner of every page — enough to tell at a
glance which build you're on when comparing notes or filing a bug, without competing for attention
with anything else on screen. The packaged app's window title carries the same version.

## Type a prompt (no mic)

Above the live log is a **text box with a ✈ send button**. Type a message and press **Enter** or
click **✈** to send it — it runs a **full normal turn** (model routing, game-state and memory
context, tools, conversation history, and a spoken reply), exactly like talking, just **without the
microphone**. The box clears on send and your prompt shows in the log as `Commander: …` like any
turn.

It's handy when a hot mic is awkward (streaming, a voice call, a sleeping house, no free hand
mid-combat), for **accessibility**, and for **exact** input speech-to-text tends to mangle — precise
system/station/Commander names, numbers, URLs, and odd glyphs (`café`, `→`, `🚀`) go through
verbatim. Like a push-to-talk press, sending **barges in** on anything already in flight.

## Live status & log

The main page shows a **status light** that tracks each turn (LISTENING → TRANSCRIBING → THINKING →
SPEAKING → IDLE) and a **live log** that scrolls with your prompts, COVAS++'s replies, and the
behind-the-scenes lines (routing decisions, usage/cost, status, searches) — all timestamped.

- **CANCEL button** — always stops an in-progress reply, wherever you are in the panel.
- **Log filter** — a **Conversation / All** toggle. *Conversation* (the default) shows just your
  utterances and the replies; *All* also shows status, thinking, search, and usage/cost lines. Your
  choice persists across reloads.
- **Selecting & copying** — the log is fully selectable. Scroll up (or start a selection) and
  auto-scroll **pauses** so a new line can't yank the view or drop your selection; it resumes once
  you're back at the bottom. The **"Copy log"** link in the log header copies the whole log to the
  clipboard, **respecting the current filter** (so *Conversation* copies only utterances and
  replies as clean, timestamped plain text), and briefly confirms with **"Copied N lines"**. Hover
  any line for a per-line **⎘ copy** button that copies just that line.
- **Jump to latest** — scrolling up to read or copy older lines reveals a floating **"↓ Jump to
  latest"** pill over the bottom of the log (badged with a running count once new lines arrive
  while you're away). Click it, or scroll back to the bottom yourself, to resume auto-follow.
- **Right-click Copy on a selection** — select some log text and right-click for a small **Copy**
  menu. This exists because the packaged native window (PyWebView/WebView2) suppresses the OS's own
  context menu entirely, so without it a selection there would have no Copy at all; the same menu
  also appears in the plain browser build for consistency.

## Quick configuration

The left card carries the handful of controls you reach for most, and it **reflects whichever
providers you're actually running**. The **LLM** block and the **Speech** block each name the active
provider and show only *its* quick controls — so if `[llm].provider` is OpenAI-compatible you get an
editable **model** combobox and a read-only view of the endpoint; if `[tts].provider` is Edge you get
the free Edge **voice** picker, not ElevenLabs fields you can't use. Between them sits the **Whisper
model** (speech-to-text) and a **Personality** toggle.

- **It mirrors, it doesn't switch.** To change *which* provider is active, use the **Settings page**
  (there's a `change ⚙` link on each block). The quick card then re-renders to match.
- **Anthropic (Claude)** — a **model** dropdown plus a **Thinking depth** control. Thinking is
  Anthropic-only for now; other LLM providers simply don't show it.
- **OpenAI-compatible / Gemini** — an editable **model** combobox: pick from the endpoint's
  live catalog, or type any model id (free text is always accepted). If the catalog can't be
  fetched (no key, offline), it quietly degrades to a plain text box with your current value kept.
- **ElevenLabs** — **model**, a searchable **voice** picker (with the type-to-filter box and 🔍
  search palette), and a **voice speed** slider. The voice/model lists are fetched from ElevenLabs
  **only when ElevenLabs is the active TTS provider**.
- **Edge / Azure / OpenAI / Cartesia / Piper** — each shows its own voice fields (an Edge/Azure/
  Cartesia voice combobox, the OpenAI TTS model/voice, a local Piper `.onnx` path, etc.).

Every control writes straight to `overrides.json` through the same validated schema the Settings
page and voice commands use, so nothing here can drift from the rest of the app.

## Settings page — `/settings`

A schema-driven [settings page](using/settings.md) that renders every setting with the right
control for its type — toggles, dropdowns, number fields and sliders, text/path boxes — each with
inline help:

- **Filter box** — type to narrow the list to matching settings. It matches a setting's section,
  title, or description (case-insensitive substring) and hides sections with no matches. Filtering
  kicks in at 3+ characters; shorter or empty shows everything.
- **Voice-list filter** — the **ElevenLabs voice** picker (long lists) has its own little filter box
  beside it. Type 3+ characters to narrow the dropdown to voices whose name or category contains the
  text (case-insensitive); shorter or empty restores the full list. The same box appears next to the
  **ElevenLabs voice** dropdown on the main control panel.
- **Alphabetical order** — the ElevenLabs voice and model dropdowns are sorted A→Z by display name
  (case-insensitive), not raw API order, so long voice libraries are easy to scan.
- **Change & save** — edit values, then **Save changes** writes them to `overrides.json`
  (`config.toml` stays pristine).
- **Per-setting reset** — a changed setting shows a **Reset** button that reverts it to the default
  and drops it from the overrides.
- **Validation** — out-of-range or unknown values are rejected rather than saved.
- **Applies live** — a saved change takes effect immediately, no relaunch. Switching your **LLM or
  TTS provider** (or its model/voice/base URL) hot-swaps it for the **next turn** — an in-flight
  turn finishes on the previous one, and a failed switch keeps the working provider and tells you.
  Changing the **talk/cancel/reflex keys** or the **microphone** rebinds them in place; the Whisper
  model, activation mode, bus volumes, and toggles all reload immediately. The only exceptions that
  still need a restart are `audio.enabled`, `audio.mix_sample_rate`, `ui.host`/`ui.port`, and
  `dev.mock` (see the Configuration reference).

Everything here is the **same schema** the [voice settings](using/settings.md) use, so the two never
disagree.

### API keys

At the top of the Settings page is a hand-built **API keys** card — one write-only field per
provider (Anthropic, ElevenLabs, OpenAI, Gemini, Azure, Cartesia, and Inara). It's how you set or
**rotate** any key without hunting for files or re-running setup:

- **Write-only & masked** — each field is a password box. Keys are **never shown back**; every row
  just carries a **set / not-set** badge, so a stored key can't leak through the page.
- **Encrypted at rest** — a saved key is written to that provider's key file **encrypted with
  Windows DPAPI** (never plaintext, and never into `overrides.json`).
- **Set, rotate, or clear** — paste a key and **Save** (or **Rotate** if one's already set); **Clear**
  removes it. A blank box is ignored, so you can't accidentally wipe a stored key.
- **Takes effect on restart** — providers read their key at launch, so a new or rotated key applies
  the next time you start COVAS++.

## Appearance & themes

Under the **Appearance** group on the Settings page is a **Theme** selector with three built-in
looks. Pick one and it applies **live, with no reload** — the whole panel recolours on the spot —
and persists to `overrides.json`, so it's remembered across restarts and shown on **every** page.

- **Dark** *(default)* — the original control-panel palette.
- **Light** — light surfaces with dark text, tuned for daytime desk use, screenshots, and streaming
  overlays (body text meets WCAG AA contrast).
- **Elite Dangerous** — the game's cockpit look: near-black backgrounds with the in-game **HUD
  orange** (`#ff7100`) and a cyan secondary, so the panel reads as part of the cockpit. The accent
  is deliberately identical to the [companion HUD](using/hud.md)'s orange.

You can also switch by **voice** — *"switch to the light theme"*, *"use the Elite Dangerous theme"* —
since the theme is an ordinary [setting](using/settings.md). Because the theme is baked into each
page as it's served, opening or navigating to any page shows the right palette **immediately**, with
no flash of the wrong colours. Set it in `config.toml` too:

```toml
[ui]
theme = "dark"   # "dark" | "light" | "elite"
```

Only the control-panel colours change; the desktop/VR [HUD overlays](using/hud.md) keep their own
palettes, and the checklist's rich Markdown editor stays on its dark editor theme.

## Personality tab

Manage your [personas and campaign](using/personas-voice.md):

- **Persona picker** — list, preview, and select a persona; the next reply changes voice/register.
- **Save as custom** — turn the edited persona box into your own custom persona (stored git-ignored).
- **Campaign editor** — edit your personal Commander facts, saved separately so switching persona
  never wipes them.

## Checklist tab — `/checklist`

A **WYSIWYG markdown editor** for your [checklist](using/checklist.md) — rendered headings and real,
clickable checkboxes, not a plain text box. It edits the **same file** the voice loop uses:

- **Toggle** a checkbox, **edit** an item inline, **nest** items with Tab, then **Save**.
- **Live updates** — a voice or tool change (marking an item done, adding, editing, or deleting a
  line) appears in the open editor **in place**, with no manual reload. It only does this when you
  have **no unsaved edits**; if you're mid-edit it keeps your work and shows the stale-write warning
  instead.
- **Reload from disk** pulls in edits you made by voice (still there for the mid-edit case).
- **Stale-write guard** — if a voice edit landed while you had unsaved changes, it warns you (with a
  choice to reload their version or overwrite) instead of clobbering.

The checklist editor uses a rich-text library loaded from a CDN, so it needs internet; if the CDN
is unreachable it falls back to a plain editor.

## Memory tab — `/memory`

A browser for your [persistent memory](using/memory.md) — the transparent ship's log other Elite
assistants don't let you see into. It reads and writes the **same `memory.jsonl`** the voice loop
uses:

- **List & search** — every remembered fact with its type, tags, and timestamp; filter live by any
  word, tag, or type.
- **Edit** — change a memory's text, type, or tags in place (its id and original timestamp are kept,
  so it round-trips losslessly).
- **Delete** — drop a memory you don't want kept.
- **Add** — record a fact by hand.
- **Stale-write guard** — if the voice loop wrote to memory while you had the tab open, it warns you
  and offers a reload instead of clobbering — the same protection the checklist editor uses.

Pure vanilla JS with **no CDN**, so this tab works fully offline.

## Crew tab — `/crew`

An editor for your [interactive crew](using/crew.md) — define the characters the companion can voice:

- **Name** — the identity the companion refers to them by and prefixes their lines with.
- **Personality** — an optional flavor line woven into the (static) system prompt so a character
  stays consistent turn to turn.
- **Voice** — pick a specific cast voice, or leave it on **Auto** for the deterministic assignment.
- **Save / add / delete** — the whole roster is saved at once to a git-ignored `crew.json` the
  voice and prompt paths read live.
- **Stale-write guard** — if the roster file changed underneath you, it warns and offers reload or
  overwrite instead of clobbering — the same protection the checklist and memory editors use.

Pure vanilla JS with **no CDN**, so this tab works fully offline.

## Macros tab — `/macros`

An editor for your [custom macros](automation/custom-macros.md) — the named, triggerable macros
you author yourself. It reads and writes the **same `custom_macros.jsonl`** the voice loop uses:

- **List** — every saved macro with its steps, trigger, and whether it needs a spoken confirm.
- **Author** — name a macro, add ordered steps (an allowlisted **action**, a **wait**, a **require
  status** check, or an **await status** wait), optionally pick a **trigger**, and save. The
  dropdowns only offer real, allowlisted actions and known triggers/status flags.
- **Delete** — remove a macro you no longer want.

Saving here runs the **exact same validation** as voice authoring, so a web-created macro can't
reference an action outside your allowlist or a trigger COVAS++ doesn't track. Pure vanilla JS with
**no CDN** — works fully offline. Requires `[macros].enabled` to actually run the macros you build.

## What needs what

The panel itself just needs the app running. Individual features it exposes (settings that toggle
game-awareness, personas, etc.) have their own requirements, noted on each feature's page.
