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
  you're back at the bottom. The **Copy** link in the log header copies the whole log to the
  clipboard, **respecting the current filter** (so *Conversation* copies only utterances and
  replies as clean, timestamped plain text). Hover any line for a per-line **⎘ copy** button.

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
- **Change & save** — edit values, then **Save changes** writes them to `overrides.json`
  (`config.toml` stays pristine).
- **Per-setting reset** — a changed setting shows a **Reset** button that reverts it to the default
  and drops it from the overrides.
- **Validation** — out-of-range or unknown values are rejected rather than saved.
- **Live where supported** — some settings (like the Whisper model) reload immediately; enabling or
  disabling a whole capability applies on the next restart.

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
