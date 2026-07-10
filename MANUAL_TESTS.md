# COVAS++ — Manual Test Suite (MANUAL_TESTS.md)

A single human-run checklist to walk through **in-game** and confirm every user-facing
feature works reasonably well. Not exhaustive — happy-path plus a few key edge cases per
feature. This is separate from, and complementary to, the offline `pytest` suite (which
covers pure logic for free; this file covers the parts that need a mic, speakers, the web
panel, and — for several sections — Elite Dangerous actually running).

## How to use it
Work top to bottom. Tick each `- [ ]` as it passes; jot anything odd in the **Notes:** line
under each section. Most steps are done by **voice** with the app running; some check the web
panel or a file on disk. If a feature is disabled in config, its section says so up front —
enable it first (see **§0.3**).

**Keys** — hold **`[`** to talk · **tap `[`** briefly (under 400 ms) to cancel/stop · **Ctrl+Alt+Q** to quit.
(You can bind a joystick button to `[` via JoyToKey. There's no separate cancel key by default; the panel's **CANCEL** button always works too.)

**Web panel** — http://127.0.0.1:8765 (opens automatically when you launch the UI build).

**Sound cues you should hear** (each is a random pick from a small set — swap in your own in `sounds/`):
- **voiceinput1–4** — plays the instant you press to talk
- **processing1–3** — plays while working / searching
- **inputok2** — plays right before the spoken answer
- **inputfailed1** — plays on any failure (no speech, API/TTS error)

**Legend for what each section needs:**
- 🎮 **ED** — Elite Dangerous must be running (reads live journal/Status.json).
- 🔊 **HW** — needs real hardware: microphone, speakers/headset. (Nearly every voice step is HW.)
- ⌨️ **INJECT** — sends real keypresses into ED (keybind automation).
- 📋 **FILE** — verify by opening a file on disk.
- 🌐 **PANEL** — verify in the web control panel.

---

## 0. Prerequisites & setup

### 0.1 Environment health
- [ ] 🔊 Run **`check_setup.bat`** (or `.venv\Scripts\python.exe check_setup.py`) → every line reads `[ OK ]`, ending in "All systems go."
- [ ] Confirm `personality.txt` exists (copy `personality.example.txt` if not) and `ElevenLabsAPIKey.txt` holds your key — both are git-ignored.

Notes:

### 0.2 Launch
- [ ] **Headless:** `run_covas.bat` (or `python run_covas.py`) → console banner shows your model, voice, Whisper size, `Personality ON`, and `TALK: hold [`. No browser.
- [ ] **With panel:** `run_covas_ui.bat` (or `python run_covas_ui.py`) → same console banner **plus** the browser opens http://127.0.0.1:8765 and the status light reads **IDLE**.
- [ ] Console prints the PTT scan codes line, and `QUIT: Ctrl+Alt+Q`.

Notes:

### 0.3 Capability toggles — enable what you want to test FIRST
These features are gated in **`config.toml`** (edit values freely) or via **`overrides.json`**
(what the web panel writes). **The web Settings page does NOT toggle capabilities** — it only
changes model, thinking depth, web-search on/off, personality, ElevenLabs voice/model, and
Whisper size. Capability enablement is config-file only.

Confirm each of these before running its section (as shipped, most are already `true`; **keybinds is the one that ships OFF**):
- [ ] `[elite].enabled = true` — ED journal/Status monitoring. **Required by** proactive callouts, the keybind combat guard, and the live "current system" used by every search. (§5, §6, §7, §8)
- [ ] `[proactive].enabled = true` — proactive callouts. Whitelisted events: `FSDJump`, `Docked`, `MissionCompleted`, `LowFuel`, `Overheating`, `Died`. (§5.2)
- [ ] `[keybinds].enabled = true` — **DEFAULT OFF.** Flip it on to test §6. Keep `require_confirmation = true` and `combat_guard = true`. Allowlist is `["landing_gear"]`.
- [ ] `[nav].enabled = true` — outfitting "find the closest module". (§7)
- [ ] `[star_systems].enabled = true` — star-system voice search. (§8.1)
- [ ] `[search].enabled = true` — group toggle for stations, minor factions, signals, and faction-states searches. (§8.2–8.5)
- [ ] `[router].enabled = true` — cost router (cheap tier by default). (§4)
- [ ] `[web_search].enabled = true` — automatic web search. (§3)
- [ ] `[personality].enabled = true` — "Commander" address + campaign context.

Notes (which toggles you changed, and where):

---

## 1. Core voice loop  🔊 HW
- [ ] Hold **`[`** → you hear a **voiceinput** cue immediately (before you even speak).
- [ ] While holding, say *"Hello COVAS, can you hear me? Keep it short."* then release.
- [ ] On release you hear a **processing** cue.
- [ ] 🌐 Panel status + log move through **LISTENING → TRANSCRIBING → THINKING → SPEAKING → IDLE**.
- [ ] 🌐 Your words appear as **Commander: …** and the reply as **COVAS: …** (timestamped) in the log.
- [ ] Just before the spoken answer you hear **inputok2**, then the reply plays in the ElevenLabs voice.
- [ ] The reply addresses you as **"Commander"** (personality is on).

Notes:

## 2. Sound cues & failure cue  🔊 HW
- [ ] Press to talk several times → the **voiceinput** cue **varies** (random of 4).
- [ ] The **processing** cue also varies across turns (random of 3).
- [ ] **Failure:** press and release **without speaking** → you hear **inputfailed1** and the log notes no speech was detected.
- [ ] No spoken "looking it up / GalNet" filler ever plays — a processing beep covers searches.

Notes:

## 3. Cancel (tap `[`), barge-in, and panel CANCEL  🔊 HW
- [ ] Ask a long question (*"Tell me the history of the Elite Dangerous galaxy in detail."*). While it's **thinking or speaking**, **tap `[` briefly** → it stops instantly and returns to **IDLE**.
- [ ] Confirm a normal **hold** still records fine (a hold is well over the 400 ms tap threshold).
- [ ] **Barge-in:** while a reply is being spoken, **hold `[`** again → the speech cuts off and a fresh capture starts.
- [ ] 🌐 The panel's **CANCEL / STOP** button also stops an in-progress reply.
- [ ] The old **`]`** key does nothing (retired).

Notes:

## 4. Cost router — cheap by default, escalates on demand  🔊 HW 🌐 PANEL
> Verify each turn via the session log's two lines: a **`[router] <model> max_tokens=N — <reason>`** line and a **`[usage] in=… out=… ~$0.00XX [<model>]`** line. (Requires `[router].enabled = true`.)
- [ ] **Banter uses the cheap tier:** say *"Morning, COVAS — how's it going?"* → router line shows **`claude-haiku-4-5`**; the usage line's model is Haiku and cost is a fraction of a cent.
- [ ] **"Think hard" escalates:** say *"Think hard about the best way to break in a new ship."* → router line shows **`claude-sonnet-5`** (escalate phrase).
- [ ] **Depth phrase escalates:** say *"Walk me through the pros and cons of a fuel scoop."* → router escalates to Sonnet.
- [ ] **Explicit premium:** say *"Use Opus for this — summarize the Thargoid war."* → router line shows **`claude-opus-4-8`**.
- [ ] **Full breakdown raises the cap:** say *"Give me the full breakdown of the engineering process."* → router line shows a higher `max_tokens` (2048).
- [ ] (Optional) 🌐 Set the router **pin** off/on, or change the base model in the panel, and confirm the router line reflects it.

Notes:

## 5. ED monitoring & proactive callouts  🎮 ED 🔊 HW
> Requires `[elite].enabled = true` and Elite Dangerous running. Fly around a little so there's live telemetry.

### 5.1 Context-aware answers
- [ ] *"Where am I?"* → names your **current system** (from live telemetry, not a guess).
- [ ] *"How's my fuel?"* → reports your **fuel level** / status.
- [ ] *"Am I docked?"* / *"What ship am I in?"* → answers from current status.
- [ ] *"What did I just do?"* / *"Check my logs."* → summarizes **recent journal events** (jumps, docks, missions).
- [ ] Say a word with **"context"** in it on an ambiguous question → forces a live status lookup for that turn (the wake word is scrubbed from what the model sees).

Notes:

### 5.2 Proactive callouts (`[proactive].enabled = true`)
- [ ] **Arrival:** perform an **FSD jump** to a new system → within a few seconds COVAS speaks a short in-character callout **without** any PTT press. (Fires only when idle.)
- [ ] **Dock** at a station → a `Docked` callout fires (at most one line even amid a jump→supercruise→dock burst — min-interval throttle).
- [ ] **Mute by voice:** say *"COVAS, stop the callouts."* → it confirms; trigger another event → **no** callout. Then *"COVAS, turn callouts back on."* → next event announces again.
- [ ] A callout in progress is cancelable: hold `[` mid-callout → it cuts off like any speech.

Notes:

## 6. Keybind automation — landing gear  🎮 ED ⌨️ INJECT 🔊 HW
> Requires `[keybinds].enabled = true` **and** `[elite].enabled = true` (combat guard reads ED
> Status). The **Toggle Landing Gear** control must be bound to a key in ED. Only `landing_gear`
> is allowlisted. Do this **while parked/docked and safe** — it sends a real keypress.
- [ ] **Arm:** say *"COVAS, toggle my landing gear."* → it says it's **armed but not done**, and asks you to confirm on a separate command. Gear does **not** move yet.
- [ ] **Confirm on a SEPARATE turn:** say *"Confirm."* (or *"do it"*) → the gear actually toggles in-game (watch the ship).
- [ ] **Same-turn confirm is refused:** arm and, in the *same* utterance, say "…and do it now" → it refuses to fire in the arming turn.
- [ ] **Combat guard:** get **interdicted / into a danger state**, then ask to toggle gear → it **refuses** ("won't touch ship controls mid-interdiction / in danger"). With `[elite]` OFF it also refuses (can't prove it's safe).
- [ ] **Expiry:** arm it, wait past `confirm_window` (60 s), then say *"confirm"* → it says the action expired; nothing fires.
- [ ] **Hard abort:** arm it, then say *"Abort."* / *"Belay that."* → the arm is cleared and any held key is released.
- [ ] **Off-allowlist refusal:** ask for a different control (e.g. *"deploy hardpoints"*) → it won't do it (only `landing_gear` is permitted).

Notes:

## 7. Outfitting search — find the closest module  🎮 ED 🔊 HW 📋 clipboard
> `[nav].enabled = true`. `require_confirmation` ships **off**, so it searches as soon as the
> module is fully resolved. Result sentence: *"Closest <module>: <station> in <system>, N.N
> light-years away. Largest pad X. … I've copied <system> to your clipboard."*
- [ ] **Happy path:** *"Find the closest fuel scoop."* → names the nearest station + system and distance, and **copies the system name** to the clipboard (paste it somewhere to confirm).
- [ ] **Disambiguation:** *"Find the closest multi-cannon."* → because a multi-cannon needs a **size and mount**, it **asks** (e.g. size small/medium/large/huge and fixed/gimballed/turreted) instead of guessing. Answer the questions → it then searches.
- [ ] **Mishear recovery:** *"Find the nearest multiple cannon."* → it resolves to / suggests **Multi-Cannon** rather than dead-ending on the misheard word.
- [ ] **Clipboard:** after any successful search, the clipboard holds the **system** name (what you paste into the galaxy map).
- [ ] **Already local:** search for a module sold in your **current** system → the reply says the station is **"in your current system"** (distance ~0). *(Note: it still copies that system name — pasting your own system is a harmless no-op; there's no separate "skip copy" behavior.)*
- [ ] **No current system:** with ED not running and no journal, ask for a module → it says it doesn't know your current system yet, rather than searching blindly.

Notes:

## 8. Voice search categories  🎮 ED 🔊 HW 📋 clipboard
> `[star_systems].enabled` and `[search].enabled` = true. Each search is stateless conversational
> slot-filling over Spansh, nearest-first from your current system, and **copies the primary
> system to the clipboard**. Misheard filter values are validated against a bundled vocabulary
> and corrected, not silently widened.

### 8.1 Star systems
- [ ] *"Find the nearest Empire system with high security."* → names the closest matching system + distance and copies it. (Slots: allegiance, government, economy, security, Powerplay power/state, population, permit, colonization.)

Notes:

### 8.2 Stations
- [ ] *"Find the nearest station with a shipyard and a large pad."* → nearest matching station/system, copied. (Try *"no carriers"* to exclude fleet carriers, or *"close to the star"* for within ~1000 Ls.)

Notes:

### 8.3 Minor factions
- [ ] *"Where is the nearest system the Dark Wheel is present?"* → nearest system with that faction present (or *"controlled by the Dark Wheel"* for controlling only), copied. An unknown faction name triggers a recovery suggestion instead of a bogus search.

Notes:

### 8.4 Signals / structures
- [ ] *"Find the nearest megaship."* → nearest structure of that type (megaship / settlement / outpost / starport), copied.

Notes:

### 8.5 Faction states (misc)
- [ ] *"Find the nearest system at war."* → nearest system by controlling-faction state (war, civil war, boom, election, infrastructure failure), copied.

Notes:

### 8.6 Refinement re-query
- [ ] After any of the above, **refine in a follow-up turn** (e.g. after 8.1 say *"actually, make it a low-security anarchy"*) → it re-runs the search with the added/changed filter and gives a new nearest result (doesn't ignore the refinement or start from scratch).

Notes:

## 9. Help — what can you do & failure recovery  🔊 HW
> Help is templated from the capability registry (no LLM), so it never claims a capability that isn't loaded.
- [ ] **Idle overview:** while idle, *"What can you do?"* → lists **at most 3** capabilities, each with an example utterance, then a short "there are others — ask about …" tail. The categories named match what you enabled in §0.3.
- [ ] **Topic detail:** *"How do I find a module?"* → describes the **outfitting** capability and its refinements (size, mount, pad).
- [ ] **Failure recovery:** say a slightly-wrong term, e.g. *"Find the closest power distributer."* (misspelled) → it replies with a suggestion drawn from real values: *"I didn't recognize 'power distributer' — did you mean Power Distributor?"* — never inventing a correction.
- [ ] An unrecognized **capability** ask (*"Can you plot me a route?"* — not built) → it says it can't do that and offers to list what it can, **without** echoing the fake capability as if it were real.

Notes:

## 10. Checklist — read, mark, edit  🔊 HW 📋 FILE
> Uses `ultimate_checklist.md`. Tip: test edits with a **throwaway** line so real objectives are untouched; watch the file change after each step.
- [ ] *"What should I knock out next?"* → speaks your next pending objective **and overall progress** (e.g. "66 of 807").
- [ ] *"Give me my next three objectives."* → reads a few upcoming pending items.
- [ ] *"Mark that one done."* → confirms it's checked; open `ultimate_checklist.md` → that line is now `- [x]`.
- [ ] *"Actually reopen it."* → back to `- [ ]`.
- [ ] **Disambiguation:** ask to mark something whose wording matches several lines (e.g. *"mark the Colonia one done"*) → it **asks which one** rather than guessing.
- [ ] **Add:** *"Add a line after the current one that says 'Reload carrier with Tritium'."* → inserted **directly after**, with matching indentation/nesting; becomes the current line. (`- [ ]` in the file.)
- [ ] **Modify:** *"Change that line to 'Reload carrier with 25000 tons of Tritium'."* → text updates, checkbox state preserved.
- [ ] **Delete:** *"Delete the current line."* → the throwaway line is removed; your real objectives are intact.
- [ ] **External edit:** hand-edit `ultimate_checklist.md`, save, then ask *"What's next?"* → it reflects your edit (reads the file fresh).

Notes:

## 11. Web control panel — live status & persistent settings  🌐 PANEL 🔊 HW 📋 FILE
- [ ] **Live status:** the status light tracks state as you talk; the log scrolls with prompts, replies, router/usage, and status/search lines (timestamped).
- [ ] Change **Claude model** (e.g. to `claude-sonnet-5`), ask something → still replies. (Note: with the router ON, per-turn tiering may override this base model — see §4.)
- [ ] Change **Thinking depth** to **High**, ask a reasoning-heavy question → replies with no error; an approach/thinking summary line may appear before the answer.
- [ ] Toggle **Personality OFF**, ask *"Who am I?"* → reply is plain and does **not** say "Commander". Toggle **ON** → "Commander" returns.
- [ ] Change **ElevenLabs voice**, ask something → the next reply is in the **new voice**.
- [ ] (Optional) Change **ElevenLabs model** and **Whisper model** — a Whisper change logs that the model reloaded.
- [ ] Toggle **web search** off/on and confirm a current-info question does / doesn't search.

Notes:

## 12. Settings persistence  🌐 PANEL 📋 FILE
- [ ] Set model, voice, thinking depth, and personality to specific non-default values in the panel.
- [ ] 📋 Open `overrides.json` → your changes are written there (config.toml stays pristine).
- [ ] **Quit** (Ctrl+Alt+Q) and relaunch `run_covas_ui.bat` → the panel comes back with the **same settings**.

Notes:

## 13. Web search (automatic)  🔊 HW 🌐 PANEL
- [ ] Ask a current-info question: *"What's the latest Elite Dangerous update right now?"* → the log shows **"Searching the web for &lt;query&gt;"**, the status hits a searching state, and you hear a **processing** beep.
- [ ] The spoken answer reflects **live/current** info, not just memory.
- [ ] **Cancel mid-search:** start another current-info question, then tap `[` while it's searching → it stops.
- [ ] Searches are capped at `[web_search].max_uses` (3) per reply.

Notes:

## 14. Robustness & quit  🔊 HW 📋 FILE
- [ ] Ask for something with odd symbols/emoji (*"Draw me an ASCII arrow and explain it."*) → it speaks/streams without the console crashing.
- [ ] 📋 After a session, open the newest **`logs\session_*.log`** → it holds your prompts and replies with timestamps, plus the router/usage lines.
- [ ] A provider hiccup (e.g. briefly kill network) degrades gracefully — the loop survives and returns to IDLE rather than crashing; a dead TTS falls back to text.
- [ ] **Ctrl+Alt+Q** (or closing the console window) shuts it down cleanly.

Notes:

---

## Needs-hardware / manual-only note
Everything in this file needs Doug's machine and can't be exercised in CI or a sandbox:
- 🔊 **HW** (mic + speakers) gates nearly every step — STT capture and TTS playback.
- 🎮 **ED** (§5–§8) needs Elite Dangerous running so the journal/Status.json feed live telemetry; the searches also need internet (Spansh) and the clipboard checks need a desktop clipboard.
- ⌨️ **INJECT** (§6) sends real DirectInput scancodes into ED — do it parked and safe.
- 🌐 **PANEL** / 📋 **FILE** checks need the running app and a browser / file access.

The offline `pytest` suite covers the pure logic (parsing, routing, checklist ops, help
projection, query building) for free — run `pytest` often; this manual pass is for the
on-hardware, in-game behavior it can't reach.

---

### Summary
- Passed: ___ / ___  ·  Failed: ___
- Anything to revisit:
