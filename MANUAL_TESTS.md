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

**Web panel** — http://127.0.0.1:8765 (opens automatically when you launch the UI build). The
**Settings** page is at http://127.0.0.1:8765/settings.

**Sound cues you should hear** (each is a random pick from a small set — swap in your own in `sounds/`):
- **voiceinput1–4** — plays the instant you press to talk
- **processing1–3** — plays while working / searching
- **inputok2** — plays right before the spoken answer
- **inputfailed1** — plays on any failure (no speech, API/TTS error)

**Legend for what each section needs:**
- 🎮 **ED** — Elite Dangerous must be running (reads live journal/Status.json).
- 🔊 **HW** — needs real hardware: microphone, speakers/headset. (Nearly every voice step is HW.)
- ⌨️ **INJECT** — sends real keypresses into ED (keybind automation / auto-honk).
- 📋 **FILE** — verify by opening a file on disk.
- 🌐 **PANEL** — verify in the web control panel.
- 🌍 **NET** — needs internet (Spansh / Inara / web search).

---

## 0. Prerequisites & setup

### 0.1 Environment health
- [ ] 🔊 Run **`check_setup.bat`** (or `.venv\Scripts\python.exe check_setup.py`) → every line reads `[ OK ]`, ending in "All systems go."
- [ ] Confirm `personality.txt` (or `campaign.txt`) exists and `ElevenLabsAPIKey.txt` holds your key — both git-ignored.

Notes:

### 0.2 Launch
- [ ] **Headless:** `run_covas.bat` (or `python run_covas.py`) → console banner shows your model, voice, Whisper size, and the capability on/off lines (Router, ED monitor, Proactive, Keybinds, **Auto-honk**, Find module, Personality). No browser.
- [ ] **With panel:** `run_covas_ui.bat` (or `python run_covas_ui.py`) → same banner **plus** the browser opens http://127.0.0.1:8765 and the status light reads **IDLE**.
- [ ] Console prints the PTT scan codes line, and `QUIT: Ctrl+Alt+Q`.

Notes:

### 0.3 Capability toggles — enable what you want to test FIRST
Capabilities are gated in **`config.toml`** (edit freely) or **`overrides.json`** (what the panel
writes). The Settings page (§14.2) can also flip these, but **capability enable/disable applies on
the next restart** (only Whisper reloads live). Confirm each before running its section (as shipped,
most are `true`; **keybinds and auto-honk ship OFF**):
- [ ] `[elite].enabled = true` — ED journal/Status monitoring. **Required by** proactive/route callouts, the keybind + honk combat guard, carriers, community goals, and the live "current system" used by every search. (§5, §6, §7, §8, §9, §10)
- [ ] `[proactive].enabled = true` — proactive callouts. (§5.2)
- [ ] `[route].enabled = true` — **DEFAULT OFF.** Route callouts while flying a plotted route. (§5.3)
- [ ] `[keybinds].enabled = true` — **DEFAULT OFF.** Landing-gear automation. Keep `require_confirmation`/`combat_guard = true`. (§6.1)
- [ ] `[honk].enabled = true` — **DEFAULT OFF.** Auto-honk on arrival. Set `fire_group` + `trigger` for cycling, or leave `fire_group = -1` for the hold-primary fallback. (§6.2)
- [ ] `[nav].enabled = true` — outfitting "find the closest module". (§7)
- [ ] `[star_systems].enabled = true` / `[search].enabled = true` — voice search categories. (§8)
- [ ] `[cg].enabled` is implicit (`[cg].source`); set `[cg].inara_api_key` to also see CGs you haven't visited. (§10)
- [ ] `[router].enabled = true` — cost router (cheap tier by default). (§4)
- [ ] `[web_search].enabled = true` — automatic web search. (§16)
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

Notes:

## 4. Cost router — cheap by default, escalates on demand  🔊 HW 🌐 PANEL
> Verify each turn via the session log's two lines: a **`[router] <model> max_tokens=N — <reason>`** line and a **`[usage] in=… out=… ~$0.00XX [<model>]`** line. (Requires `[router].enabled = true`.)
- [ ] **Banter uses the cheap tier:** *"Morning, COVAS — how's it going?"* → router line shows **`claude-haiku-4-5`**; cost a fraction of a cent.
- [ ] **"Think hard" escalates:** *"Think hard about the best way to break in a new ship."* → **`claude-sonnet-5`**.
- [ ] **Depth phrase escalates:** *"Walk me through the pros and cons of a fuel scoop."* → Sonnet.
- [ ] **Explicit premium:** *"Use Opus for this — summarize the Thargoid war."* → **`claude-opus-4-8`**.
- [ ] **Full breakdown raises the cap:** *"Give me the full breakdown of the engineering process."* → higher `max_tokens` (2048).
- [ ] (Optional) 🌐 Set the router **pin** in Settings and confirm the router line reflects it.

Notes:

## 5. ED monitoring, proactive & route callouts  🎮 ED 🔊 HW
> Requires `[elite].enabled = true` and ED running. Fly around so there's live telemetry.

### 5.1 Context-aware answers
- [ ] *"Where am I?"* → names your **current system** (from live telemetry, not a guess).
- [ ] *"How's my fuel?"* → reports **fuel level** / status.
- [ ] *"Am I docked?"* / *"What ship am I in?"* → answers from current status.
- [ ] *"What did I just do?"* / *"Check my logs."* → summarizes **recent journal events**.
- [ ] Say a word with **"context"** in it on an ambiguous question → forces a live status lookup (the wake word is scrubbed from what the model sees).

Notes:

### 5.2 Proactive callouts (`[proactive].enabled = true`)
- [ ] **Arrival:** **FSD jump** to a new system → within a few seconds COVAS speaks a short in-character callout **without** any PTT press (fires only when idle).
- [ ] **Dock** at a station → a `Docked` callout fires (at most one line amid a jump→supercruise→dock burst — min-interval throttle).
- [ ] **Mute by voice:** *"COVAS, stop the callouts."* → confirms; trigger another event → **no** callout. Then *"COVAS, turn callouts back on."* → next event announces again.
- [ ] A callout in progress is cancelable: hold `[` mid-callout → it cuts off.

Notes:

### 5.3 Route callouts (N4 — `[route].enabled = true`)  🎮 ED
> Plot a multi-jump galaxy-map route first (writes `NavRoute.json`). These go through the proactive path — spoken only when idle, cancelable, and silenced by the proactive mute too.
- [ ] **Scoopable heads-up:** as you lock/enter the next jump, COVAS says whether the next star is **scoopable** ("Next star's scoopable." / "…isn't scoopable. Top off your fuel if you're low.").
- [ ] **Jumps remaining:** every **Nth** jump (`[route].every_n`, default 5) it announces jumps remaining to the destination (singular "1 jump remaining" near the end).
- [ ] **Arrival:** on reaching the final system it says "Arrived at <system>. Route complete." and stops.
- [ ] **Replot:** plot a new route mid-flight → callouts follow the new route (counts reset).
- [ ] **Mute:** with the proactive mute on ("stop the callouts"), route callouts are silent too.

Notes:

## 6. Ship controls — keybinds & auto-honk  🎮 ED ⌨️ INJECT 🔊 HW
> Both send **real keypresses** into ED and need `[elite].enabled = true` (combat guard). Do these **parked/docked and safe**.

### 6.1 Toggle landing gear (`[keybinds].enabled = true`)
> The **Toggle Landing Gear** control must be bound to a key in ED. Only `landing_gear` is allowlisted.
- [ ] **Arm:** *"COVAS, toggle my landing gear."* → says it's **armed but not done**, asks you to confirm separately. Gear does **not** move yet.
- [ ] **Confirm on a SEPARATE turn:** *"Confirm."* (or *"do it"*) → the gear toggles in-game.
- [ ] **Same-turn confirm refused:** arm and, in the *same* utterance, say "…and do it now" → refuses to fire in the arming turn.
- [ ] **Combat guard:** get **interdicted / into danger**, then ask to toggle → **refuses**. With `[elite]` OFF it also refuses (can't prove it's safe).
- [ ] **Expiry:** arm it, wait past `confirm_window` (60 s), then *"confirm"* → says it expired; nothing fires.
- [ ] **Hard abort:** arm it, then *"Abort."* / *"Belay that."* → arm cleared, any held key released.
- [ ] **Off-allowlist refusal:** ask for a different control (*"deploy hardpoints"*) → won't do it.

Notes:

### 6.2 Auto-honk (N5 — `[honk].enabled = true`)
> Fires the Discovery Scanner shortly after you jump into a **new** system — no button press. Bind the scanner's fire button (and, for cycling, fire-group next/previous) to keys in ED; note the scanner's fire group number (0-based, right HUD panel). Set `[honk].fire_group`, `[honk].trigger` (primary/secondary). At launch the log reports the fire key + group, or a "bind it in-game" warning.
- [ ] **Configured honk:** with `fire_group` set to the scanner's group, **jump** to a new system → it cycles to the scanner group, **holds** the fire button ~`hold_seconds` (default 6), then cycles **back** — and does **NOT** fire weapons. Log shows a `honk` line.
- [ ] **Fallback:** set `fire_group = -1`, select the scanner group yourself, jump → it just **holds primary fire** and honks.
- [ ] **Combat guard:** get interdicted / into danger, then jump (or trigger arrival in danger) → it **refuses** with a logged reason; nothing fires.
- [ ] **Unknown fire group:** with `[elite]` OFF (or before Status has a fire group) and a configured `fire_group` → it **skips** rather than risk firing in the wrong group.
- [ ] **Hard abort:** with `[keybinds]` also on, jump and during the ~6 s hold say *"abort"* → the held fire key releases immediately.
- [ ] **Disabled:** set `[honk].enabled = false` → no honk on arrival.

Notes (reliability quirks — cycle timing, hold too short/long for a full honk):

## 7. Outfitting search — find the closest module  🎮 ED 🔊 HW 📋 clipboard 🌍 NET
> `[nav].enabled = true`. `require_confirmation` ships **off**, so it searches as soon as the module is fully resolved.
- [ ] **Happy path:** *"Find the closest fuel scoop."* → names the nearest station + system + distance, and **copies the system** to the clipboard (paste to confirm).
- [ ] **Disambiguation:** *"Find the closest multi-cannon."* → asks for **size and mount** instead of guessing; answer → it searches.
- [ ] **Mishear recovery:** *"Find the nearest multiple cannon."* → resolves to / suggests **Multi-Cannon**.
- [ ] **Already local:** search for a module sold in your **current** system → the reply says it's **"in your current system"** (see the N3 already-there rule in §9 for the copy behavior).
- [ ] **No current system:** with ED not running and no journal → it says it doesn't know your current system yet, rather than searching blindly.

Notes:

### 7.1 Ship search — find the closest ship (N8 + EDSM stock verification)
> Same `[nav]` section. `verify_stock = true` (default): every candidate's **current stock** is
> confirmed against EDSM before it's spoken, so answers should **match Inara's nearest-seller
> search** (inara.cz → the ship's page → Search ships, near your current system).
- [ ] **Happy path:** *"Where can I buy an Anaconda?"* → nearest station + system + distance + price, system copied. **Cross-check on Inara:** same station is Inara's #1 (or within the same distance when several tie).
- [ ] **Catalog-vs-stock (THE Type-10 bug):** ask for a ship your **current station catalogs but doesn't stock** (the in-game shipyard shows it "unavailable") → the answer is a **different** station that really stocks it, optionally noting the nearer listing was skipped ("current stock data says it isn't actually available there"). Verify the named station really sells it on Inara.
- [ ] **Family disambiguation:** *"Find the closest Krait."* → asks **MkII or Phantom**, doesn't guess; answer → searches once.
- [ ] **Unverified caveat:** rare in populated space — if the reply ends with *"I couldn't verify live stock…"*, the named station had no recent EDSM data; spot-check it on Inara.
- [ ] **Kill switch:** set `[nav].verify_stock = false`, restart → searches still work (startup line says `stock check off`), no EDSM calls, no caveats.

Notes:

## 8. Voice search categories  🎮 ED 🔊 HW 📋 clipboard 🌍 NET
> `[star_systems].enabled` and `[search].enabled` = true. Stateless conversational slot-filling over Spansh, nearest-first from your current system, each **copies the primary system** to the clipboard. Misheard filter values are validated against a bundled vocabulary and corrected.

### 8.1 Star systems
- [ ] *"Find the nearest Empire system with high security."* → closest matching system + distance, copied.

### 8.2 Stations
- [ ] *"Find the nearest station with a shipyard and a large pad."* → nearest matching station/system, copied. (Try *"no carriers"* or *"close to the star"*.)

### 8.3 Minor factions
- [ ] *"Where is the nearest system the Dark Wheel is present?"* → nearest system with that faction, copied. An unknown faction name triggers a recovery suggestion, not a bogus search.

### 8.4 Signals / structures
- [ ] *"Find the nearest megaship."* → nearest structure of that type, copied.

### 8.5 Faction states (misc)
- [ ] *"Find the nearest system at war."* → nearest system by controlling-faction state, copied.

### 8.6 Refinement re-query
- [ ] After any of the above, **refine in a follow-up** (*"actually, make it a low-security anarchy"*) → it **re-runs** the search with the changed filter and gives a new nearest result (doesn't ignore it or start over).

Notes:

## 9. Location & carriers (N3)  🎮 ED 🔊 HW 📋 clipboard
> `[elite].enabled = true`. The owned fleet carrier is tracked from the journal (pinned to its `CarrierID`).
- [ ] **Copy current system:** *"Copy my current system."* → copies your **current** system to the clipboard (paste to confirm).
- [ ] **Fleet carrier:** *"Where's my fleet carrier?"* → speaks its **current system** and copies it. (If you own no carrier, it says so rather than guessing.)
- [ ] **Squadron carrier:** *"Where's my squadron carrier?"* → explains it's only available **in-game** on the Squadron menu's Carrier Management tab (may name your squadron); it does **not** attempt a lookup or copy.
- [ ] **Already-there rule:** ask for the fleet carrier while you're **in the carrier's system** → it says you're already there and does **NOT** copy to the clipboard.

Notes:

## 10. Community Goals (N6)  🎮 ED 🔊 HW 📋 clipboard 🌍 NET
> Journal-primary (works offline for CGs you've visited). Set `[cg].inara_api_key` to also surface CGs you HAVEN'T visited. Visit a CG board in-game first so the journal has your standing.
- [ ] **List:** *"List the community goals."* → active CGs (title + system + expiry). With an Inara key, ones you haven't visited are flagged ("…one in <system> you haven't visited yet").
- [ ] **CG system:** *"What system is the <CG title> community goal in?"* → resolves by (fuzzy) title, speaks the system, and **copies** it — unless it's your current system (then says so, no copy).
- [ ] **Standing:** *"What's my standing in the <CG title> community goal?"* → "Top 10 Commanders" or "top X%". For a CG not in your journal it says it doesn't have your standing (visit the board).
- [ ] **No key / feed down:** with no Inara key it works journal-only and notes it can't see unvisited CGs right now (doesn't crash).

Notes:

## 11. Help — categories, drill-in & failure recovery  🔊 HW
> Help is templated from the capability registry (no LLM), so it never claims a capability that isn't loaded. It's a **hierarchy** so it scales as features grow.
- [ ] **Overview names CATEGORIES:** *"What can you do?"* → names the **groups** (e.g. navigation and search, your ship, your checklist, community goals, settings) with an invitation to drill in — it does **not** try to read every capability at once.
- [ ] **Drill into a category:** *"Tell me about navigation and search."* → lists the capabilities in that group (at most 3, then "there are others — ask about …"), each with an example.
- [ ] **Drill into a capability:** *"How do I find a module?"* → describes the **outfitting** capability and its refinements (size, mount, pad).
- [ ] **Coverage:** every capability you enabled in §0.3 is reachable — spot-check one from each group (e.g. *"tell me about your ship"* → ship status + ship controls; *"tell me about settings"*, *"tell me about community goals"*).
- [ ] **Failure recovery:** *"Find the closest power distributer."* (misspelled) → *"I didn't recognize 'power distributer' — did you mean Power Distributor?"* — never inventing a correction.
- [ ] **Unknown capability:** *"Can you plot me a route?"* (not built) → says it can't, offers to list what it can, **without** echoing the fake capability as real.

Notes:

## 12. Voice-settable settings (N2)  🔊 HW 📋 FILE
> Change settings by voice, validated against the same schema the Settings page uses. Changes write `overrides.json`; capability enables and a few others apply on restart (Whisper reloads live).
- [ ] **Set an enum:** *"Set the Whisper model to small."* → confirms the change ("Whisper model set to small"); 📋 appears in `overrides.json`.
- [ ] **Set a bool:** *"Turn personality off."* → confirms; a follow-up question no longer says "Commander". Turn it back on.
- [ ] **Natural value:** *"Set thinking to high."* / *"Set the voice speed to 1.1."* → applied.
- [ ] **Invalid value refused with options:** *"Set the Whisper model to gigantic."* → refuses and **lists the valid options** (doesn't guess or silently widen).
- [ ] **Unknown setting → help:** *"Set the warp factor to 9."* → routes to help / says it isn't a setting, rather than inventing one.
- [ ] **Get a setting:** *"What's my Whisper model set to?"* → reads the current value.

Notes:

## 13. Checklist — read, mark, edit  🔊 HW 📋 FILE
> Uses `ultimate_checklist.md`. Test edits with a **throwaway** line.
- [ ] *"What should I knock out next?"* → speaks your next pending objective **and progress** (e.g. "66 of 807").
- [ ] *"Give me my next three objectives."* → reads a few upcoming items.
- [ ] *"Mark that one done."* → confirms; 📋 that line is now `- [x]`.
- [ ] *"Actually reopen it."* → back to `- [ ]`.
- [ ] **Disambiguation:** ask to mark something matching several lines → it **asks which one**.
- [ ] **Add / Modify / Delete** a throwaway line → inserted after current with matching nesting / text updated (checkbox preserved) / removed; real objectives intact.
- [ ] **External edit:** hand-edit the file, save, then *"What's next?"* → reflects your edit (reads fresh).

Notes:

## 14. Web control panel  🌐 PANEL 🔊 HW 📋 FILE

### 14.1 Live status & log
- [ ] The status light tracks state as you talk; the log scrolls with prompts, replies, router/usage, status/search lines (timestamped).

### 14.2 Settings page (N1) — http://127.0.0.1:8765/settings
- [ ] The page renders **grouped sections** with the **right control per type** (toggles, dropdowns, number/sliders, text/path) and inline help.
- [ ] **Filter box:** type in the search box → the list narrows to matching settings.
- [ ] **Change + save:** change a value → the **save bar** appears with a count; **SAVE CHANGES** → 📋 written to `overrides.json` (config.toml stays pristine).
- [ ] **Per-setting reset:** a changed (overridden) setting shows **RESET** → click it → reverts to default and drops from `overrides.json`.
- [ ] **Validation:** try an out-of-range number (e.g. voice speed 2.0) → rejected client-side / server-side, not written.
- [ ] **Live where supported:** change the **Whisper model** → the log notes the model reloaded (no restart). (Capability enables apply on restart.)

### 14.3 Personality tab (N7)
- [ ] **Persona picker:** the Personality tab lists personas; selecting one shows a **preview**. Pick a different persona → the next reply's **voice/register changes**.
- [ ] **Campaign preserved:** switch persona and confirm your **Campaign** text (personal facts) is unchanged — switching voice never wipes it.
- [ ] **Save as custom:** edit the persona box → **SAVE AS CUSTOM** → a new custom persona appears in the list (written git-ignored under `personalities/custom/`).
- [ ] **Campaign editor:** edit the Campaign box → **SAVE CAMPAIGN** → a subsequent reply reflects the updated facts.

### 14.4 Voice speed (N7)
- [ ] Nudge the **Voice speed** slider (1.0–1.2×) and ask something → the reply is spoken **faster**; the value can't exceed 1.2 (clamped).

### 14.5 Log filter (N7)
- [ ] The Live Log has a **Conversation / All** toggle. **Conversation** (default) shows only your utterances and COVAS replies; **All** shows status/thinking/search/usage/system lines too.
- [ ] Switch to Conversation → status/thinking/usage lines **hide**; the choice **persists** across a reload.

### 14.6 Checklist editor (N10) — http://127.0.0.1:8765/checklist 🌍 NET (CDN)
> Edits the SAME `ultimate_checklist.md` the voice loop uses. Use a **throwaway** line.
- [ ] The tab renders the checklist as **rendered markdown** (headings, checkboxes) — not a plain textarea. The header shows the file name; ☑ checklist links exist on the panel and settings headers.
- [ ] **Toggle:** click a checkbox → **SAVE** → 📋 that line flips `- [ ]`/`- [x]` in the file; ask *"what's next?"* by voice → the change is heard (same file, read live).
- [ ] **Edit + nest:** edit an item's text inline; **Tab** nests it under the item above → SAVE → 📋 text and indentation land in the file; task lines stay `- [ ]` style (never `* [ ]`).
- [ ] **Voice → web:** mark an item by voice, then click **RELOAD FROM DISK** (or refocus the tab) → the voice edit appears.
- [ ] **Stale-write guard:** load the tab, make a voice edit, then click SAVE in the tab → an **amber warning** appears (file changed on disk) instead of clobbering; **RELOAD THEIR VERSION** shows the voice edit, or **OVERWRITE ANYWAY** forces yours.
- [ ] **Save feedback:** a successful save flashes "Saved — N/M complete" and the Live Log (All filter) shows "Checklist updated from the web editor".

Notes:

## 15. Settings persistence  🌐 PANEL 📋 FILE
- [ ] Set model, voice, thinking depth, and personality to non-default values (panel or voice).
- [ ] 📋 Open `overrides.json` → your changes are there.
- [ ] **Quit** (Ctrl+Alt+Q) and relaunch → the panel comes back with the **same settings**.

Notes:

## 16. Web search (automatic)  🔊 HW 🌐 PANEL 🌍 NET
- [ ] *"What's the latest Elite Dangerous update right now?"* → log shows **"Searching the web for …"**, status hits a searching state, you hear a **processing** beep.
- [ ] The spoken answer reflects **live/current** info.
- [ ] **Cancel mid-search:** start another current-info question, then tap `[` while searching → it stops.
- [ ] Searches are capped at `[web_search].max_uses` (3) per reply.

Notes:

## 17. Robustness & quit  🔊 HW 📋 FILE
- [ ] Ask for something with odd symbols/emoji (*"Draw me an ASCII arrow and explain it."*) → speaks/streams without the console crashing.
- [ ] 📋 After a session, open the newest **`logs\session_*.log`** → prompts + replies with timestamps, plus router/usage lines.
- [ ] A provider hiccup (briefly kill network) degrades gracefully — the loop survives and returns to IDLE; a dead TTS falls back to text.
- [ ] **Ctrl+Alt+Q** (or closing the console window) shuts it down cleanly.

Notes:

## 18. Audio / Comms / Chatter subsystem (C1–C9)  🎮 ED 🔊 HW
The atmospheric audio layer is now **wired into the live app** (C9). It's OFF by default: turn it
on in config (or the Settings page) before testing.

### 18.0 Enable it
- [ ] 🌐 Set `[audio].enabled = true` (master — reopens the audio device through the bus mixer),
  then enable the parts you want: `[audio.cues].enabled` (chatter/SFX), `[audio.comms].enabled`
  (on by default), `[music].enabled` (needs track files), `[audio.interdiction].enabled`. Requires
  `[elite].enabled` so game events drive it. Restart after flipping the master switch.
- [ ] 🔊 With the layer ON, confirm a normal spoken reply still sounds right — COVAS now streams
  through the mixer's **clean COVAS bus** (no change in character), and a **tap-`[` barge-in still
  cuts speech instantly**.

### 18.1 Bus mixer + comms radio treatment (device-level demos, app NOT running)
- [ ] 🔊 `.venv\Scripts\python.exe scripts\demo_comms_bus.py` → a tone CLEAN (COVAS bus) then
  RADIO-FILTERED (Comms bus). `demo_comms_variants.py` → NPC riff / tampered→verbatim / player DM.
  `demo_interdiction.py` → the three interdiction layers.

### 18.2 Comms voices in-game (C4/C5)
- [ ] 🎮 Receive an **NPC/station** comms-panel line (e.g. request docking) → it's read on the
  radio-treated comms bus. A **direct player DM** is read **verbatim** (fixed male voice). Confirm
  the Open-play **local/wing chatter is NOT voiced** (the fail-closed gate). Repeated station spam
  isn't re-read every jump (template dedup).

### 18.3 Space chatter + SFX + interdiction (C6/C8)
- [ ] 🎮 With `[audio.cues].enabled`, fly/dock around → occasional ambient **chatter** (rate-limited,
  never over-talking). 🎮 Trigger an **interdiction** → the layered sting + threat line + pirate line.

### 18.4 Voice controls + live settings (C9)
- [ ] 🔊 By voice: *"mute the chatter"*, *"quiet the comms"*, *"turn the music down"*, *"turn the
  music up"*, *"stop the music"*, *"silence all the background audio"*, *"turn the ambient audio
  back on"* → each takes effect; your own replies are unaffected.
- [ ] 🌐 On the **Settings** page, change a bus **volume** or toggle **comms/chatter/music** →
  applies live (no restart). The **master** `audio.enabled` and comms **voice pickers** persist.

Notes:

---

## Needs-hardware / manual-only note
Everything in this file needs Doug's machine and can't be exercised in CI or a sandbox:
- 🔊 **HW** (mic + speakers) gates nearly every step — STT capture and TTS playback.
- 🎮 **ED** (§5–§10) needs Elite Dangerous running so the journal/Status.json feed live telemetry.
- ⌨️ **INJECT** (§6) sends real DirectInput scancodes into ED — do it parked and safe.
- 🌍 **NET** (§7, §8, §10, §16) needs internet (Spansh / Inara / web search).
- 🌐 **PANEL** / 📋 **FILE** checks need the running app and a browser / file access.

The offline `pytest` suite covers the pure logic (parsing, routing, checklist ops, help
projection + grouping, query building, honk sequencing) for free — run `pytest` often; this
manual pass is for the on-hardware, in-game behavior it can't reach.

---

### Summary
- Passed: ___ / ___  ·  Failed: ___
- Anything to revisit:
