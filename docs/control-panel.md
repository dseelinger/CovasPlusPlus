# The control panel

Launch with the UI build and COVAS++ opens a local dashboard in your browser at
**[http://127.0.0.1:8765](http://127.0.0.1:8765)** — a status light, a live log, and full editors
for settings, personality, and your checklist.

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

- **Filter box** — type to narrow the list to matching settings.
- **Change & save** — edit values, then **Save changes** writes them to `overrides.json`
  (`config.toml` stays pristine).
- **Per-setting reset** — a changed setting shows a **Reset** button that reverts it to the default
  and drops it from the overrides.
- **Validation** — out-of-range or unknown values are rejected rather than saved.
- **Live where supported** — some settings (like the Whisper model) reload immediately; enabling or
  disabling a whole capability applies on the next restart.

Everything here is the **same schema** the [voice settings](using/settings.md) use, so the two never
disagree.

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
- **Reload from disk** pulls in edits you made by voice.
- **Stale-write guard** — if a voice edit landed while you had the tab open, it warns you (with a
  choice to reload their version or overwrite) instead of clobbering.

The checklist editor uses a rich-text library loaded from a CDN, so it needs internet; if the CDN
is unreachable it falls back to a plain editor.

## What needs what

The panel itself just needs the app running. Individual features it exposes (settings that toggle
game-awareness, personas, etc.) have their own requirements, noted on each feature's page.
