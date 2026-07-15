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

**Sound cues you should hear** (random pick from each type's folder; ship-original defaults, or
your own dropped into `<data dir>/sounds/<type>/` — see §2):
- **listen** — plays the instant you press to talk
- **processing** — plays while working / searching
- **completed** — plays right before the spoken answer
- **failure** — plays on any failure (no speech, API/TTS error)

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
**everything defaults ON** so the app shows full functionality out of the box):
- [ ] `[elite].enabled = true` — ED journal/Status monitoring. **Required by** proactive/route callouts, the keybind + honk combat guard, carriers, community goals, and the live "current system" used by every search. (§5, §6, §7, §8, §9, §10)
- [ ] `[proactive].enabled = true` — proactive callouts. (§5.2)
- [ ] `[route].enabled = true` — Route callouts while flying a plotted route. (§5.3)
- [ ] `[hud].enabled = true` — Companion HUD overlay (**off** by default; applies **live**, no restart). (§5a)
- [ ] `[keybinds].enabled = true` — Landing-gear automation. Keep `require_confirmation`/`combat_guard = true`. (§6.1)
- [ ] `[honk].enabled = true` — Auto-honk on arrival (**on** by default). No fire-group setup — it probes and backs out of a Surface-Scanner misfire. Set `[honk].trigger` only if your scanner is on secondary fire. (§6.2)
- [ ] `[nav].enabled = true` — outfitting "find the closest module". (§7)
- [ ] `[star_systems].enabled = true` / `[search].enabled = true` — voice search categories. (§8)
- [ ] `[cg].enabled` is implicit (`[cg].source`); add an **Inara API key** on the Settings API keys card to also see CGs you haven't visited. (§10)
- [ ] `[router].enabled = true` — cost router (cheap tier by default). (§4)
- [ ] `[web_search].enabled = true` — automatic web search. (§16)
- [ ] `[personality].enabled = true` — "Commander" address + campaign context.

Notes (which toggles you changed, and where):

---

## 1. Core voice loop  🔊 HW
- [ ] Hold **`[`** → you hear a **listen** cue immediately (before you even speak).
- [ ] While holding, say *"Hello COVAS, can you hear me? Keep it short."* then release.
- [ ] On release you hear a **processing** cue.
- [ ] 🌐 Panel status + log move through **LISTENING → TRANSCRIBING → THINKING → SPEAKING → IDLE**.
- [ ] 🌐 Your words appear as **Commander: …** and the reply as **COVAS: …** (timestamped) in the log.
- [ ] Just before the spoken answer you hear the **completed** cue, then the reply plays in the ElevenLabs voice.
- [ ] The reply addresses you as **"Commander"** (personality is on).

Notes:

## 2. Sound cues — defaults, override & rotation (I8)  🔊 HW 🌐 PANEL 📋 FILE
> Cues are drop-in **folders**, not config paths. Shipped originals live in
> `covas/assets/cues/<type>/`; your overrides go in `<data dir>/sounds/<type>/` (project root in a
> source run; `%APPDATA%\COVAS++\sounds\` when packaged). Types: `listen` / `processing` /
> `completed` / `failure` / `thinking`.
- [ ] **Out of the box (no user cues):** press to talk → you hear the **shipped default** `listen`
      chirp; on release a **processing** tick; a ready **completed** cue before speech.
- [ ] **Failure:** press and release **without speaking** → you hear the **failure** cue and the log notes no speech was detected.
- [ ] No spoken "looking it up / GalNet" filler ever plays — a processing beep covers searches.
- [ ] **Open cues folder:** in the panel, click **Open cues folder** → Explorer opens
      `<data dir>\sounds\` with `listen/ processing/ completed/ failure/ thinking/` subfolders (each with a README).
- [ ] **Override + rotation:** drop **2–3** of your own `.wav`s into `sounds/listen/`, restart →
      the press-to-talk cue now plays **your** files and **varies** across presses (your set
      **replaced** the default; any count works).
- [ ] **Fallback:** empty `sounds/listen/` again, restart → the **shipped default** `listen` cue returns.
- [ ] **Interdiction sting default:** with `[audio.interdiction].enabled` and no user sting, an
      interdiction plays the shipped original **sting** (not silence).
- [ ] **Thinking bed fills the wait (issue #5):** ask a slow question (*"Give me the full history of
      the Empire."*). After the one-shot `processing` tick you hear a **soft, looping** bed under the
      wait; it **stops the instant speech begins**. Confirm it also stops cleanly on **tap-cancel**,
      **barge-in**, and a **failure** (no double-up with the `completed`/`failure` cue).
- [ ] **Thinking bed level (issue #9):** during that multi-second turn, confirm the bed now sits
      **quietly in the background** (peak ~0.08 / ~-22 dBFS, tuned by ear) — it stays under COVAS's
      voice and the one-shot chimes without disappearing entirely.
- [ ] **Thinking bed toggle:** *"turn the thinking sound off"* (or the **Thinking sound** row on the
      Settings page) → the next slow turn plays only the single `processing` tick, no looping bed.
      Turn it back on and the bed returns.
- [ ] **Thinking bed override:** drop a loopable `.wav` into `sounds/thinking/`, restart → your file
      loops during the wait; empty the folder → the shipped default bed returns.

Notes:

## 3. Cancel (tap `[`), barge-in, and panel CANCEL  🔊 HW
- [ ] Ask a long question (*"Tell me the history of the Elite Dangerous galaxy in detail."*). While it's **thinking or speaking**, **tap `[` briefly** → it stops instantly and returns to **IDLE**.
- [ ] Confirm a normal **hold** still records fine (a hold is well over the 400 ms tap threshold).
- [ ] **Barge-in:** while a reply is being spoken, **hold `[`** again → the speech cuts off and a fresh capture starts.
- [ ] 🌐 The panel's **CANCEL / STOP** button also stops an in-progress reply.

Notes:

## 4. Cost router — cheap by default, escalates on demand  🔊 HW 🌐 PANEL
> Verify each turn via the session log's two lines: a **`[router] [<tier>] <model> max_tokens=N — <reason>`** line (the `[cheap]`/`[standard]`/`[premium]` tier prefix is from issue #11) and a **`[usage] in=… out=… ~$0.00XX [<model>]`** line. (Requires `[router].enabled = true`.)
- [ ] **Banter uses the cheap tier:** *"Morning, COVAS — how's it going?"* → router line shows **`[cheap] claude-haiku-4-5`**; cost a fraction of a cent.
- [ ] **"Think hard" escalates:** *"Think hard about the best way to break in a new ship."* → **`[standard] claude-sonnet-5`**.
- [ ] **Depth phrase escalates:** *"Walk me through the pros and cons of a fuel scoop."* → `[standard]` Sonnet.
- [ ] **Explicit premium:** *"Use Opus for this — summarize the Thargoid war."* → **`[premium] claude-opus-4-8`**.
- [ ] **Full breakdown raises the cap:** *"Give me the full breakdown of the engineering process."* → higher `max_tokens` (2048).
- [ ] (Optional) 🌐 Set the router **pin** in Settings (`cheap`/`standard`/`premium`, or `haiku`/`sonnet`/`opus`) and confirm the router line's tier + model reflect it.

Notes:

### 4.1 OpenAI-compatible LLM provider (issue #12)  🔊 HW 🌍 NET 📋 FILE
> One provider covers **OpenAI, Groq, DeepSeek, OpenRouter** — only `[openai].base_url` + model ids
> differ. A *cloud* LLM, so it's fine in-game and the router tiers it via `[openai.tiers]`. Needs a
> key in `OpenAIAPIKey.txt` (DPAPI-encrypted; add it in Settings — env vars are no longer read, #22).
> Restart after switching `[llm].provider`.
- [ ] **Conversation:** set `[llm].provider = "openai"` (default `base_url`/`model` = OpenAI
  `gpt-4o-mini`), restart, speak a turn → COVAS answers via OpenAI; the `[router]` line shows the
  OpenAI model (e.g. `[cheap] gpt-4o-mini`) and `[usage]` shows token counts (+ a cost if priced).
- [ ] **Tool calling works:** *"What's my next objective?"* / *"Mark fuel scooping complete."* → the
  checklist tool fires (log shows the tool call) and COVAS confirms — proving delta-assembled
  `tool_calls` are handled.
- [ ] **Escalation tiers:** *"Think hard…"* → the router line shows `[standard]` with the
  `[openai.tiers].standard` model; *"use opus/the big model"* wake phrase → `[premium]`.
- [ ] **Alt endpoint (the "one provider" claim):** point `[openai].base_url` at **Groq**
  (`https://api.groq.com/openai/v1`, model `llama-3.3-70b-versatile`) **or** OpenRouter, with that
  service's key, restart → conversation still works through the same provider.
- [ ] **Fail-soft:** clear the key (or set a bad `base_url`) → the turn degrades to text and the loop
  returns to IDLE; restore → it works again. No crash.

Notes:

### 4.2 Gemini LLM provider (issue #13)  🔊 HW 🌍 NET 📋 FILE
> Google Gemini on the **native** API — tool calling + Google-Search **grounding** + a cheap Flash
> default tier. A *cloud* LLM, tiered via `[gemini.tiers]` (Flash/Pro). Needs a key in
> `GeminiAPIKey.txt` (DPAPI-encrypted; add it in Settings — env vars are no longer read, #22).
> Restart after switching `[llm].provider`.
- [ ] **Conversation:** set `[llm].provider = "gemini"`, add your Gemini key in Settings, restart, speak a turn
  → COVAS answers via Gemini; `[router]` line shows the Gemini model (e.g. `[cheap] gemini-2.5-flash`)
  and `[usage]` shows token counts.
- [ ] **Tool calling works:** *"What's my next objective?"* / *"Mark fuel scooping complete."* → the
  checklist tool fires (log shows the tool call) and COVAS confirms.
- [ ] **Search grounding:** with `[web_search].enabled = true`, ask something current
  (*"What's the latest on the Thargoid war?"*) → the log shows a **`Searching…`** side-channel line
  (grounding queries) and the answer reflects live info.
- [ ] **Escalation tiers:** *"Think hard…"* → the router line shows `[standard]` with the
  `[gemini.tiers].standard` (Pro) model.
- [ ] **Fail-soft:** clear the key → the turn degrades to text and the loop returns to IDLE; restore →
  it works again. No crash.

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

## 5a. Companion HUD overlay (issue #47 — `[hud].enabled`)  🖥️ 🔊 HW 🎮 ED
> A transparent, always-on-top 2D overlay of the companion's own state. **Off by default**; the toggle applies **live** (no restart, unlike other capability toggles). Cannot be exercised offline/headless — needs Doug's desktop. Run ED **borderless/windowed** so an always-on-top window can float over it (full-screen exclusive can cover any overlay — expected).
- [ ] **Toggle on — Settings page:** flip **Companion HUD overlay** on the [Settings page](docs/using/hud.md) → a small panel appears **top-right**, background fully transparent (desktop/game shows through), staying **on top**.
- [ ] **Toggle on — voice:** with the HUD off, say *"turn the HUD on"* → the panel appears (settings-by-voice path). *"Turn the HUD off"* → it disappears. Toggling is live (no restart).
- [ ] **Voice-loop state row:** hold PTT → the state row tracks **Listening → Thinking → Speaking → Idle** as you talk and COVAS replies.
- [ ] **Checklist row:** with a checklist loaded, the row shows your next pending item + count (e.g. *"…  (2/10 done)"*); mark it done by voice → the row advances to the next pending item.
- [ ] **Route row:** plot a multi-jump route (writes `NavRoute.json`) → the row shows **"N jumps to <dest>"**; lock the next jump → it appends **scoopable / NOT scoopable**; each jump decrements the count; arrival shows **"Arrived at <dest>"**.
- [ ] **Callout row:** trigger a proactive or route callout (§5.2/§5.3) → the last-callout row shows that line.
- [ ] **Click-through (Windows):** move the mouse over the panel and click → the click lands on the window/game **behind** it (the HUD is non-interactive).
- [ ] **Fail-soft:** it never blocks startup or the voice loop; with `[hud].enabled = false` no window appears and nothing is logged as an error.
### 5.4 Blueprint / material sourcing (#66)  🎮 ED
> Requires `[elite].enabled = true`. The material inventory comes from the journal `Materials`
> event, written when you load into the game — so launch ED (any ship) before testing.
- [ ] **Missing-mat gap:** *"What do I need for a grade 5 FSD?"* → names the grade-5 Increased Range recipe **and** the materials you're **short** on (not the full list), each with a sourcing hint. Cross-check a couple of counts against your in-game Inventory → Materials.
- [ ] **Grade + blueprint phrasing:** *"What am I missing for grade 3 dirty drive tuning?"* → the grade-3 shortfall for that blueprint. Try a name-only form (*"increased range"*) and a module+grade form (*"grade 5 FSD"*).
- [ ] **Disambiguation:** *"Grade 5 FSD"* alone (a module with several blueprints) → COVAS lists the candidate blueprints and asks which, rather than guessing.
- [ ] **Have-everything path:** ask for a low grade whose mats you already hold → *"You have everything for a roll — nothing to farm."*
- [ ] **Farm plan onto the checklist (the differentiator):** after a shortfall, *"Add these to my checklist."* → one objective per short material appears (name + count + where to farm). Open the [checklist](using/checklist.md) panel and confirm; tick one off.
- [ ] **Honest when blind:** with ED not yet loaded (no `Materials` seen), the recipe is still spoken but COVAS says it hasn't read your materials yet.

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
- [ ] **Mode gating — on foot (#29):** **disembark** (on foot) and ask to toggle landing gear → it **refuses** with an "only works in your ship" style message, and doesn't offer the action. Back **in the ship**, the same request arms normally.
- [ ] **Mode gating — disembark after arming (#29):** in the ship, **arm** the toggle; before confirming, **disembark**; then *"confirm"* → **refused** (mode re-checked at confirm), nothing fires.
- [ ] **Binding preference (#29):** with a keyboard bind on **Primary** (the normal case), it presses it. If you set `[keybinds].binding_preference = "secondary"` and your keyboard key is on the Secondary slot, it uses that instead (falls back to the other slot if only one is bound). Startup log shows `landing_gear -> <Key>`.

Notes:

### 6.1.1 Tier-1 ship-systems toggles (#31 — opt-in via allowlist)
> Benign, repeatable **main-ship** toggles that **fire immediately** (no arm/confirm). Off until you add each macro NAME to `[keybinds].allowlist`; bind the matching control to a **key** in ED. Do these **parked/docked**. Names: `cargo_scoop`, `night_vision`, `ship_lights`, `hud_mode`, `pips_engines`, `pips_weapons`, `pips_systems`, `pips_balance`.
- [ ] **Opt-in fires immediately:** add `cargo_scoop` to the allowlist, then *"toggle my cargo scoop"* → the scoop deploys/retracts **right away** (no "armed, confirm separately" step). Startup log lists `cargo_scoop -> <Key>`.
- [ ] **Not allowlisted → refused:** with `ship_lights` **not** in the allowlist, *"turn on my ship lights"* → won't do it (off-allowlist), nothing presses.
- [ ] **Pips:** allowlist `pips_engines`, say *"pips to engines"* three times → three pips move into ENG. Then allowlist + say *"balance the pips"* (`pips_balance`) → distribution resets to 2/2/2.
- [ ] **HUD mode:** allowlist `hud_mode`, *"switch HUD to analysis mode"* → the HUD flips combat↔analysis.
- [ ] **Combat guard still applies:** with a benign toggle allowlisted, get **interdicted / into danger** and ask for it → **refuses** (benign toggles aren't exempt from the combat guard).
- [ ] **Mode gating:** **disembark** (on foot) and ask for cargo scoop → **refuses** ("only works in your ship") and isn't offered. Back in the ship it fires.
- [ ] **Unbound control:** if the matching ED control is on a HOTAS/mouse only (no keyboard bind), asking for it → "bind it to a key" message; nothing fires.
### 6.1a Flight / nav actions (#30 — opt in via `[keybinds].allowlist`)
> Off by default. For each action you want, add its name to `allowlist` **and** bind the matching control to a **key** in ED. Do these **parked/docked** first, then in open space with a clear area. Combat guard + mode gate still apply to every one.
- [ ] **Benign fires immediately:** allowlist `throttle_zero`; *"COVAS, cut the throttle."* → throttle drops to zero **at once** (no separate confirm), reply "Throttle at zero". Same for `throttle_50` / `throttle_100`.
- [ ] **Targeting (benign):** allowlist `cycle_next_target` + `select_target_ahead`; *"target the ship ahead"* then *"cycle to the next target"* → target reticle changes immediately each time.
- [ ] **Route target (benign):** with a route plotted, allowlist `target_next_route_system`; *"target the next system in my route"* → the next route system is selected immediately.
- [ ] **Consequential arms-and-confirms:** allowlist `supercruise`; *"engage supercruise"* → **armed but not done**; on a separate *"confirm"* it fires. Same shape for `frame_shift_drive`, `hyperspace`, and `flight_assist`.
- [ ] **Combat guard:** in danger/interdiction, any flight action **refuses**; with `[elite]` OFF it refuses too.
- [ ] **Mode gate — fighter:** deploy a **ship-launched fighter**; `throttle_*` and target cycling are still offered, but `supercruise` / `hyperspace` / `frame_shift_drive` / `target_next_route_system` / `nav_lock` are **not** (main-ship only) and refuse if asked.
- [ ] **Unbound token:** allowlist `nav_lock` but leave **WingNavLock** unbound in ED → asking to toggle nav lock says to **bind it in-game**; nothing fires.
- [ ] **Off-allowlist still refused:** an action you did **not** add (e.g. `hyperspace` when only `throttle_zero` is allowlisted) → won't do it.
### 6.1b Odyssey on-foot actions (#34 — `[keybinds].enabled = true`)
> **Disembark first** (be on foot in Odyssey). Add the macros under test to `[keybinds].allowlist`, e.g. `["landing_gear", "on_foot_flashlight", "on_foot_night_vision"]`. Bind the matching **On Foot** controls to keys in ED. These are benign, so they fire **immediately** (no separate confirm).
- [ ] **Mode gating — offered only on foot:** **in your ship**, ask to *"toggle my flashlight"* → **refused** ("only works on foot"), and the action isn't offered. **Disembark**, ask again → it fires immediately (flashlight toggles in-game). This is the core check: on-foot actions are hidden while flying.
- [ ] **Flashlight / night vision:** on foot, *"flashlight"* and *"night vision"* each toggle the suit light / night vision.
- [ ] **Weapon select + holster:** on foot, *"draw your primary weapon"* / *"secondary"* / *"utility"* selects that weapon; *"holster your weapon"* puts it away. It never **fires** — only draws/holsters.
- [ ] **Suit tools:** on foot, *"switch to your energy link"* / *"profile analyser"* / *"suit tool"* selects that gadget.
- [ ] **Crouch / galaxy map:** on foot, *"crouch"* and *"open the galaxy map"* work.
- [ ] **Combat guard on foot:** get into **danger** on foot, ask to toggle flashlight → **refused** (benign still guarded). With `[elite]` OFF it also refuses.
- [ ] **Off-allowlist refusal:** ask for an on-foot macro you did **not** add to the allowlist → won't do it.
- [ ] **Ship control hidden on foot (regression):** while on foot, *"toggle landing gear"* → **refused** and not offered (proves the gate both ways).
### 6.1a SRV / buggy controls (#35 — `[keybinds].enabled = true`, allowlist the SRV macros)
> New SRV batch. Add the ones you want to `[keybinds].allowlist`, e.g. `["landing_gear", "drive_assist", "srv_headlights", "srv_night_vision", "srv_cargo_scoop", "srv_auto_brake", "recall_ship"]`. Bind the matching **Buggy** controls to keys in ED. **Deploy the SRV first** (drive the buggy) — these are offered ONLY while driving.
- [ ] **Benign toggle fires immediately (in SRV):** while **driving the SRV**, say *"COVAS, turn on the headlights."* → headlights toggle **right away** (no separate confirm needed); same for *"toggle drive assist"*, *"night vision"*, *"cargo scoop"*, *"auto-brake"*. Log shows e.g. `executed srv_headlights -> <Key>`.
- [ ] **Recall ship arms-and-confirms:** in the SRV, *"recall my ship."* → says it's **armed but not done**; confirm on a **separate** turn (*"confirm"*) → the ship recall/dismiss fires. Same-turn confirm is refused.
- [ ] **Mode gating — not in the SRV:** back **in the main ship** (or on foot), ask for any SRV control (*"headlights"*, *"recall my ship"*) → **refused** with an "only works in the SRV" style message, and the SRV actions aren't offered.
- [ ] **Mode gating — exit SRV after arming recall:** in the SRV, **arm** `recall_ship`; before confirming, **board your ship** (leave the SRV); then *"confirm"* → **refused** (mode re-checked at confirm), nothing fires.
- [ ] **Combat guard:** in the SRV, get **into danger**, then ask for any SRV toggle → **refuses**. With `[elite]` OFF it also refuses.
- [ ] **Off-allowlist refusal:** ask for an SRV control you did **not** allowlist → won't do it. Weapons/turret are never offered.
- [ ] **Unbound control:** if a Buggy control (e.g. Night Vision) is HOTAS/mouse-only → COVAS says to bind it to a key; nothing fires.
### 6.1a Tier-1 UI actions — panels / maps / fire groups (#32)
> Benign, **fire-immediately** actions (no confirm step). Opt in by NAME: add to `[keybinds].allowlist`, e.g. `allowlist = ["landing_gear", "open_galaxy_map", "cycle_fire_group_next"]`. Each ED control must be **bound to a key** in-game; `[keybinds].enabled` and `[elite].enabled` on (combat guard). Do first tests **parked and docked**.
- [ ] **Fires immediately (no confirm):** with `open_galaxy_map` allowlisted, *"open the galaxy map"* → the map opens on the spoken command — no separate confirm turn. Say it again to close.
- [ ] **Panels:** allowlist a panel (e.g. `focus_left_panel`) → *"open the navigation panel"* focuses the correct HUD panel. Spot-check `focus_right_panel`, `focus_comms_panel`, `focus_role_panel`, `quick_comms`, `open_system_map`.
- [ ] **Fire groups:** with `cycle_fire_group_next` / `cycle_fire_group_previous` allowlisted, *"next fire group"* / *"previous fire group"* steps the active fire group (top-right HUD).
- [ ] **UI / head-look:** allowlist `ui_back`, `ui_focus`, `toggle_headlook` → each presses the matching control.
- [ ] **Not allowlisted = refused:** with a macro NOT in the allowlist, asking for it → won't do it (even though the action exists).
- [ ] **Combat guard still applies:** while **interdicted / in danger**, ask to open the galaxy map → **refused** (benign actions are still gated).
- [ ] **Mode gating:** **on foot**, ask to open the galaxy map / focus a panel → **refused** ("only works in your ship"); fire-group cycling also works **in a deployed fighter**.
- [ ] **Unbound control:** if the ED control is HOTAS/mouse-only (no keyboard bind), the action reports "bind it in-game" and nothing fires. Startup log shows each allowlisted macro `-> <Key>`.

Notes:

### 6.1c Status-checked timed sequence — `launch` (#33 — `[keybinds].enabled = true`, allowlist `launch`)
> The first **multi-step** macro: a scripted sequence that mixes press/hold/wait with **Status.json checks between steps**. Add `launch` to `[keybinds].allowlist` (e.g. `["landing_gear", "launch"]`) and bind, to **keys** in ED, the controls it uses: *Flight Throttle* → Set Speed 50%, *Flight Rotation/Thrusters* → **Thrust Up** (`UpThrustButton`), *Flight Miscellaneous* → **Engine Boost** (`UseBoostJuice`), and *Landing Gear*. `[elite].enabled` on (combat guard + the status checks). **Do this docked at a station**, ready to undock — expect the ship to actually fly off the pad.
- [ ] **Startup readiness:** launch reports `Keybind macro: launch (sequence) READY` (or `UNUSABLE (bind: <token>)` naming the control you still need to bind to a key).
- [ ] **Arm-and-confirm:** *"COVAS, launch."* → says it's **armed but not done**; nothing moves. Same-turn confirm is refused (must be a separate command).
- [ ] **Happy path:** press **undock** in the station menu (ED hovers you over the pad, gear down); then *"confirm"* → COVAS throttles up, **holds** thrust to rise off the pad, **boosts** clear, retracts the gear, and only reports success once Status.json shows the gear **up**. Log: `executed sequence launch`.
- [ ] **Precondition refuses (gear up):** while flying with the **gear up** (not on a pad), arm+confirm `launch` → it **refuses** ("your landing gear isn't down…") and presses **nothing**.
- [ ] **Verify step catches a miss:** if the gear never retracts (e.g. unbind Landing Gear from a key after arming) → after ~4 s it reports it **couldn't confirm the gear retracted** rather than claiming success.
- [ ] **Hard abort mid-sequence:** during the confirmed run, say *"abort"* → the sequence **stops**, the held thrust key **releases immediately**, and remaining steps don't fire.
- [ ] **Mode gating:** **on foot** or **in the SRV**, `launch` isn't offered and is refused ("only works in your ship").
- [ ] **Combat guard:** in **danger/interdiction** (or with `[elite]` off) arming/confirming `launch` is **refused**.
- [ ] **Off by default:** with the default allowlist (`landing_gear` only), *"launch"* is **not** offered and is refused — the sequence ships opt-in.

### 6.2 Auto-honk (N5 + K2 — `[honk].enabled = true`, **on by default**)
> Fires the Discovery Scanner shortly after you jump into a **new** system — no button press, and **no fire-group setup**. Bind the Discovery Scanner's fire to a **key** in ED (a HOTAS/mouse-only bind can't be pressed; a keyboard secondary, even with a modifier, is fine). At launch the log reports "Auto-honk ON …" or a "bind it in-game" warning.
- [ ] **Happy path:** with the **Discovery Scanner** in your current fire group, **jump** to a new system → after a short probe it **holds** the fire button ~`hold_seconds` (default 5) and honks; the system map populates. Log: `honked — current fire group`.
- [ ] **DSS misfire → recover:** deliberately select a fire group holding the **Detailed Surface Scanner**, jump near a planet → it probes, detects the Surface-Scanner (probe) view, presses your **Exit Mode** bind to back out, **speaks** a heads-up, and **disarms**. You end up back in the cockpit, NOT stuck in the DSS. Log: `disarmed: a honk opened the Surface Scanner`.
- [ ] **Re-arm (voice):** after a disarm, say *"re-arm auto honk"* → it confirms ("Auto-honk re-armed") and honks again next jump.
- [ ] **Re-arm (auto):** after a disarm, do a **manual** honk yourself → the discovery-scan event re-arms it. Log: `re-armed (a discovery scan completed)`.
- [ ] **Weapons group harmless:** select a weapons fire group, jump → no weapons fire (supercruise), no scan, no crash.
- [ ] **Guards:** jump in **combat mode** (not analysis) → skips (`in combat mode`); in **danger/interdiction** → `blocked`; in **normal space** → `not in supercruise`.
- [ ] **Unbound fire:** if the fire button is HOTAS/mouse-only (no keyboard bind) → it **skips** with a "no keyboard binding" note; nothing fires.
- [ ] **Hard abort:** with `[keybinds]` also on, jump and during the hold say *"abort"* → the held fire key releases immediately.
- [ ] **Disabled:** set `[honk].enabled = false` → no honk on arrival.

Notes (reliability quirks — probe / detect-window timing `_PROBE_SECONDS` / `_DETECT_WINDOW`, the Exit-Mode bind):

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

## 8a. Trade-route planner (#44)  🎮 ED 🔊 HW 📋 clipboard 🌍 NET
> `[route_plan].enabled = true` + `[elite].enabled = true`. Plans a Spansh **trade loop** from the station you're docked at, reads the **whole multi-hop loop** + round-trip total, and copies the next stop to the clipboard for the galaxy map. **The ⚠️ LIVE-VERIFY of the Spansh trade API request/result shape still applies — this is its on-hardware validation.**
- [ ] **Plan from docked (multi-hop, happy path):** dock at a busy station, then *"Plan me a trade route from here — 720 tons of cargo, 30 light-year jump range, 100 million to spend."* → speaks **every hop in the loop** (**buy X at A, sell at station Y in system Z, ~N credits/ton; then buy…**) and a **round-trip total**, then says the next stop was copied.
- [ ] **⚠️ LIVE-VERIFY the trade shape:** confirm the spoken commodities / destinations / profits are **real and correct** (cross-check on [spansh.co.uk/trade](https://spansh.co.uk/trade)). If any field is blank/wrong, the Spansh trade request/result field names have drifted — fix `build_trade_request` / `parse_trade_route` in `covas/search/routes.py` (they're isolated for exactly this). Also sanity-check the **round-trip total** roughly equals the sum of the per-hop profits.
- [ ] **Per-run options:** try *"…large pad only, up to 5 hops, nothing more than 2000 light-seconds out, and include planetary ports"* → the returned route respects the limits (large-pad stations, ≤5 hops, close-in stations, surface markets allowed). Cross-check the same filters on spansh.co.uk/trade. ⚠️ These map to LIVE-VERIFY param names (`requires_large_pad`, `max_hops`, `max_system_distance`, `allow_planetary`) in `build_trade_request` — if a filter is ignored, that's where to correct it.
- [ ] **Plot handoff:** after a plan, **paste** (Ctrl-V) into the galaxy-map search box → it's the **first destination system**; it sets course. (In-game auto course-set is the later keybind action #32.)
- [ ] **Asks for missing numbers:** *"Plan a trade route."* with nothing else → it asks for your cargo capacity, jump range, and budget rather than guessing.
- [ ] **Not docked:** while in space, ask for a trade route → it asks you to dock or name a start station (doesn't invent one).
- [ ] **Freshness — per hop & whole loop:** if any leg's price is old, that hop is read with an inline **"(price ~N days old)"** tag; if the *whole* loop is stale, the reply also adds a spoken **"the freshest prices on this route are about N days old"** caveat (hard to force on demand — note if you see either). Try *"…only prices from the last day"* to tighten the window and make it easier to trigger.
- [ ] **Fail-soft:** with the internet briefly off, ask for a route → a spoken "couldn't reach the trade planner" note, and the voice loop keeps working (no crash).

Notes:

## 8b. Neutron / long-range route planner (#43)  🎮 ED 🔊 HW 📋 clipboard 🌍 NET
> `[neutron_plan].enabled = true` + `[elite].enabled = true`. Plots a Spansh **neutron-highway** route to a distant system (start defaults to your current system) and copies the **first waypoint** to the clipboard for the galaxy map. Needs a real route.
- [ ] **Plot to a distant system (happy path):** somewhere with a real position, *"Plot a neutron route to Colonia — my laden jump range is 55 light-years."* → speaks a **total jump count**, the **number of waypoints**, and the **first waypoint system**, and says it was copied. Cross-check the jump count against [spansh.co.uk/plotter](https://spansh.co.uk/plotter) with the same range/efficiency.
- [ ] **Plot handoff:** after a plan, **paste** (Ctrl-V) into the galaxy-map search box → it's the **first waypoint system**; it sets course. (In-game auto course-set is the later keybind action #32.)
- [ ] **Asks for the destination:** *"Plot a neutron route."* with no target → it asks **where to**, rather than guessing.
- [ ] **Asks for jump range:** *"Plot a neutron route to Colonia."* with no range → it asks for your **laden jump range** rather than inventing one.
- [ ] **Explicit start:** *"Plot a neutron route from Sol to Colonia, 55 light-year jump range."* → uses **Sol** as the start even if you're elsewhere.
- [ ] **Efficiency nudge:** ask for a **more efficient** (or *more direct*) route → the jump count changes accordingly (higher efficiency = fewer jumps).
- [ ] **Fail-soft:** with the internet briefly off, ask for a route → a spoken "couldn't reach the neutron plotter" note, and the voice loop keeps working (no crash).

Notes:

## 8c. Road-to-Riches planner (#42)  🎮 ED 🔊 HW 📋 clipboard 🌍 NET
> `[riches_plan].enabled = true` + `[elite].enabled = true`. Plans a Spansh **Road to Riches** — nearby systems of high-value UNSCANNED bodies to First-Discovery-scan — from your current system and copies the first system to the clipboard for the galaxy map. **This is the on-hardware validation of the LIVE-VERIFY Spansh Road-to-Riches request/result shape.**
- [ ] **Plan from current system (happy path):** somewhere out in the black, *"Plan me a Road to Riches route — 40 light-year jump range."* → speaks a real first system (**start at system X, N bodies to scan worth ~V credits**) and a rough total, and says the first system was copied.
- [ ] **⚠️ LIVE-VERIFY the riches shape:** confirm the spoken **system name / body count / values** are **real and correct** (cross-check on [spansh.co.uk/riches](https://spansh.co.uk/riches) with the same reference system + jump range). If the summary is blank/zeroed/wrong, the Spansh Road-to-Riches request or result field names have drifted — fix `build_riches_request` / `parse_riches_route` in `covas/search/routes.py` (they're isolated for exactly this).
- [ ] **Plot handoff:** after a plan, **paste** (Ctrl-V) into the galaxy-map search box → it's the **first route system**; it sets course. (In-game auto course-set is the later keybind action #32.)
- [ ] **Asks for jump range:** *"Plan a Road to Riches."* with no jump range → it asks for your laden jump range rather than guessing.
- [ ] **Explicit start:** *"Plan a Road to Riches from Sol with a 30 light-year jump range."* → uses **Sol** as the reference, not your current system.
- [ ] **Fail-soft:** with the internet briefly off, ask for a route → a spoken "couldn't reach the Road-to-Riches planner" note, and the voice loop keeps working (no crash).

Notes:

## 8d. Mining helper (#45)  🎮 ED 🔊 HW 📋 clipboard 📝 checklist 🌍 NET
> `[mining_helper].enabled = true` + `[elite].enabled = true`. Finds the nearest ring **hotspot** for a material (Spansh bodies/search), the best **FRESH** place to **sell** it (Spansh stations/search), drops the go-mine-sell **loop onto your checklist**, and copies the hotspot system to the clipboard. **This is the on-hardware validation of the LIVE-VERIFY Spansh hotspot + sell-price request/result shapes.**
- [ ] **Plan a run (happy path):** somewhere near inhabited space, *"Where's the nearest Painite hotspot?"* → speaks a real **ring in a system, N light-years away, with a hotspot / M overlapping hotspots**, then the **best sell** (station, system, ~credits/ton), and says the loop was **added to your checklist** and the system **copied**.
- [ ] **⚠️ LIVE-VERIFY the hotspot shape:** confirm the spoken **ring / system / hotspot count** are real and correct (cross-check on [spansh.co.uk/bodies](https://spansh.co.uk/bodies) — filter `Ring signals` = Painite, reference = your system). If the ring/count is blank/wrong, the `ring_signals` request or result fields have drifted — fix `build_hotspot_request` / `parse_hotspots` in `covas/search/mining.py` (isolated for exactly this).
- [ ] **⚠️ LIVE-VERIFY the sell shape + FRESHNESS:** confirm the spoken **station / price** are real (cross-check on [spansh.co.uk/stations](https://spansh.co.uk/stations) — `Market` = Painite, sort by sell price). Crucially, verify the quote is **fresh** (not a years-old fleet carrier): the helper should skip carriers and either give a recent price or add *"that's the freshest quote I found and it's about N days old."* If a stale carrier price is quoted as fact, the freshness/carrier filter in `parse_sell_markets` / `best_sell` needs a look.
- [ ] **Checklist loop:** after a plan, *"What's next on my checklist?"* → the **three new steps** are there in order (fly to the hotspot → mine → sell at the named station). Check one off and it advances normally.
- [ ] **Plot handoff:** **paste** (Ctrl-V) into the galaxy-map search box → it's the **hotspot system**; it sets course. (In-game auto course-set is the later keybind action #32.)
- [ ] **Material aliases:** try *"Find me an LTD hotspot"* and *"…void opals"* → resolves to **Low Temperature Diamonds** / **Void Opal** (correct hotspot + sell commodity), not a miss.
- [ ] **Refinements:** *"…large pad only to sell it"* → the sell station has a large pad; *"…don't add it to my checklist"* → no new checklist lines; *"…just the hotspot, don't plot it"* → nothing copied.
- [ ] **Asks for the material:** *"Plan a mining run."* with nothing else → it asks **what to mine** rather than guessing.
- [ ] **Fail-soft:** with the internet briefly off, ask for a hotspot → a spoken "couldn't reach" note, and the voice loop keeps working (no crash). A **sell** lookup that fails still leaves you the **hotspot** and the plot.

Notes:

## 8e. Body finder (#68)  🎮 ED 🔊 HW 📋 clipboard 🌍 NET
> `[bodies].enabled = true` + `[elite].enabled = true`. Nearest **single body** by type or biological signal over Spansh's `bodies/search`, nearest-first from your current system, copying the match's **system** to the clipboard. Stateless conversational slot-filling; misheard types/genera are validated against a bundled vocabulary and corrected. **This is the on-hardware validation of the LIVE-VERIFY Spansh bodies request/result shape.**
- [ ] **Nearest body type (happy path):** *"Find the nearest Earth-like world."* → names a real body + its system + distance (and light-seconds from the star), and says the system was copied. Try *"the closest ammonia world"* / *"the nearest water world"*.
- [ ] **⚠️ LIVE-VERIFY the bodies shape:** confirm the spoken **body / system / distance** are **real and correct** (cross-check on [spansh.co.uk/bodies](https://spansh.co.uk/bodies) with the same reference system + subtype). If the answer is blank/wrong or the filter is ignored (a mismatched subtype comes back), the Spansh bodies request/result field names have drifted — fix the `BODIES` spec / `parse_bodies` in `covas/search/categories.py` and the vocab in `covas/search/bodies.py`.
- [ ] **Biological signal of type X:** *"Find the nearest body with Bacterium signals."* → nearest body listing a *Bacterium* species; it confirms the signal. Try a specific species (*"…with Bacterium Aurasus"*) and *"any biological signal"*.
- [ ] **Landable + close-in:** *"The nearest landable body with Aleoida, close to the star."* → respects both (a landable body, low arrival distance). Cross-check on spansh.co.uk/bodies.
- [ ] **Correction, not invention:** ask for a nonsense type/biology (*"the nearest chocolate planet"*, *"a body with space whales"*) → it offers the closest real value or asks again; it does **not** run a bogus search.
- [ ] **Plot handoff:** after a match, **paste** (Ctrl-V) into the galaxy-map search box → it's the **body's system**; it sets course. (There's no per-body plot; you plot the system, then fly in.)
- [ ] **Already-there rule:** if the nearest match is a body **in your current system**, it says you're already there and does **NOT** copy.
- [ ] **Fail-soft:** with the internet briefly off, ask for a body → a spoken "couldn't reach the bodies database" note, and the voice loop keeps working (no crash).

Notes:

## 9. Location & carriers (N3)  🎮 ED 🔊 HW 📋 clipboard
> `[elite].enabled = true`. The owned fleet carrier is tracked from the journal (pinned to its `CarrierID`).
- [ ] **Copy current system:** *"Copy my current system."* → copies your **current** system to the clipboard (paste to confirm).
- [ ] **Fleet carrier:** *"Where's my fleet carrier?"* → speaks its **current system** and copies it. (If you own no carrier, it says so rather than guessing.)
- [ ] **Squadron carrier:** *"Where's my squadron carrier?"* → explains it's only available **in-game** on the Squadron menu's Carrier Management tab (may name your squadron); it does **not** attempt a lookup or copy.
- [ ] **Already-there rule:** ask for the fleet carrier while you're **in the carrier's system** → it says you're already there and does **NOT** copy to the clipboard.

### 9.1 Fleet-carrier context voices (issue #19)  🎮 ED 🔊 HW
> Needs `[audio].enabled = true` (restart to apply) + `[audio.carrier].enabled = true` (on by
> default). Roles + voices are under `[audio.carrier.captain|tower|chatter]`. The context is pinned
> to your carrier's `CarrierID`, so a **squadron/other** carrier must **not** trigger it.
- [ ] **Aboard your own carrier:** dock at the carrier you **own** → within a minute or two you hear
      the **Captain**, **Tower Control**, and/or **deck chatter** on the radio-treated comms bus,
      each in a **different voice** from COVAS and from each other. Lines are spoken, never text.
- [ ] **In-system (not docked):** be in the carrier's **home system** without docking → the
      **Captain** greets you from across the system; **Tower Control** does **not** speak (docking
      control is docked-only).
- [ ] **Away:** somewhere that is **not** your carrier's system and not docked at it → **no** carrier
      voices at all.
- [ ] **Not a squadron carrier:** dock at a **squadron** or someone else's carrier → the carrier
      voices stay **silent** (identity mismatch), while normal station/NPC comms still work.
- [ ] **Configured voice + name:** set `[audio.carrier.captain].voice_ref` and `name` (e.g.
      "Reynolds"), restart → the Captain now uses **that** voice and the name is woven into the lines.
- [ ] **Voice control:** *"mute the carrier"* → carrier voices stop; *"carrier voices on"* → they
      resume. *"silence all the background audio"* also mutes them. Your own replies are unaffected.

Notes:

## 9a. Stored ships & modules finder (issue #67)  🎮 ED 🔊 HW 📋 clipboard
> `[elite].enabled = true`. **Dock at a station with a shipyard AND outfitting first** so the game writes the `StoredShips` / `StoredModules` events — the data (and the transfer quotes) are as of that last dock. Cross-check the spoken transfer figures against the in-game Shipyard / Outfitting transfer screen — they should match exactly.
- [ ] **Locate a stored ship:** *"Where's my \<ship\>?"* (one you have parked elsewhere) → names its **system** and speaks the **transfer cost + time**, then **copies** that system to the clipboard (paste to confirm). Numbers match the in-game transfer screen.
- [ ] **Ship that's here:** *"Where's my \<ship\>?"* for one stored **at your current station** → "it's here, no transfer needed" and does **NOT** copy anything.
- [ ] **Already-there rule:** ask for a stored ship while you're **in the system it's stored in** → it says you're already there and does **NOT** copy.
- [ ] **Stored-fleet rundown:** *"What ships do I have in storage?"* → a count plus which are here vs. elsewhere (with systems).
- [ ] **Locate a stored module:** *"Where's my spare \<module\>?"* (e.g. fuel scoop, shield generator, FSD) → its **system** + **transfer cost/time**, copying the system unless you're already there. In-transit items report "in transit".
- [ ] **Stored-modules rundown:** *"What modules do I have stored?"* → grouped here / elsewhere / in transit.
- [ ] **Honest miss:** ask for a ship/module you have **not** stored → it says it doesn't see one and **lists what you actually have** (never invents a location).
- [ ] **Not seen yet:** ask before docking at a shipyard/outfitting this session → it says to dock at one first (no crash).
## 9a. Engineers finder (#65)  🎮 ED 🔊 HW 📋 clipboard
> `[elite].enabled = true`. Unlock **status** is read live from the journal's `EngineerProgress`
> event (written at login); locations/requirements come from a bundled offline table. Log into the
> game at least once this session so progress has been read.
- [ ] **Locate by name + plot:** *"Where is Felicity Farseer?"* → speaks her system (Deciat) and base, what she engineers, and copies **Deciat** to the clipboard to plot a route (paste to confirm).
- [ ] **Journal-grounded status:** *"How do I unlock The Dweller?"* → your **actual** status (unlocked / invited / discovered / not started) from the journal, plus what's still needed. Compare against the in-game Engineers panel — it should match your real progress, not a generic answer.
- [ ] **By module:** *"Which engineer upgrades my FSD?"* → lists the FSD engineers (Farseer, Palin, …), each tagged with whether **you've** unlocked them.
- [ ] **Unlock rundown:** *"Which engineers have I unlocked?"* → a count plus what's unlocked, in-progress, and still locked — matching the in-game panel.
- [ ] **Already-there rule:** ask *"where is …"* an engineer while you're **in that engineer's system** → it says you're already there and does **NOT** copy.
- [ ] **No progress yet:** with the game not yet logged in this session, ask *"which engineers have I unlocked?"* → it says it hasn't read your progress yet rather than guessing.

Notes:

## 10. Community Goals (N6)  🎮 ED 🔊 HW 📋 clipboard 🌍 NET
> Journal-primary (works offline for CGs you've visited). Add an **Inara API key** (Settings API keys card, stored encrypted in `InaraAPIKey.txt`) to also surface CGs you HAVEN'T visited. Visit a CG board in-game first so the journal has your standing.
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
- [ ] **Version by voice:** *"What version are you?"* → speaks the running version (e.g. *"I'm running COVAS++ version 0.1.0."*), matching `covas/__version__.py`. Ask *"check for updates"* by voice → it does **NOT** update; it points you at the control panel's update banner instead.

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

## 13a. Persistent memory — store & recall (issue #59)  📋 FILE
> **Foundation only** — no voice surface yet (voice recall lands in #61). This verifies the
> transparent store on disk and fail-soft loading. Memory lives at `<data dir>/memory/memory.jsonl`
> (`memory/memory.jsonl` in a source run; `%APPDATA%\COVAS++\memory\memory.jsonl` when installed).
- [ ] 📋 Create `memory/memory.jsonl` and add a line by hand:
      `{"text": "CMDR prefers metric units", "type": "preference", "tags": ["units"]}` → save.
- [ ] 📋 Confirm the folder/file is **git-ignored** (`git status` does not list it) — memory stays private.
- [ ] 📋 **Fail-soft:** append a deliberately broken line (e.g. `{ not json`) and a `# comment` line, save.
      In a Python shell: `from covas.memory import MemoryStore; MemoryStore("memory/memory.jsonl").load()`
      → returns only the **valid** fact(s); a `!! [memory] skipping corrupt line…` warning prints; no crash.
- [ ] 📋 **Recall (offline, free):** `from covas.memory import MemoryStore, Retriever;`
      `Retriever(MemoryStore("memory/memory.jsonl")).recall("what units do I use")` → returns the units fact.
- [ ] Confirm `[memory.embedding].enabled` is **false** by default (no network on the recall path).

Notes:

## 13b. Persistent memory — automatic capture (issue #60)  🎮 ED 🔊 HW 📋 FILE
> Memory now populates itself. Needs `[memory].enabled = true` (default) and, for milestones,
> `[elite].enabled = true`. Memory lives at `<data dir>/memory/memory.jsonl` (git-ignored).
- [ ] On launch the log shows `Persistent memory ON (capture + recall).`
- [ ] 🎮 **Journal milestone (deterministic, no cost):** in-game, do something notable — jump to
      an unexplored system and **detailed-scan a first-discovery body**, or fully map a body. A new
      line appears in `memory/memory.jsonl` (`First to discover …` / `Fully mapped …`, `type:
      "milestone"`). No LLM/router/usage line accompanies it — capture is a local write.
- [ ] 🔊 **Conversation fact (piggybacked, no extra call):** say **"remember that I prefer the
      Krait Mk II"**. COVAS acknowledges in-character in the SAME reply; a `preference`/`note` line
      is added to the file. Confirm the router logged **one** turn, not two (no extra model call).
- [ ] 📋 **Dedup:** repeat the exact same "remember that…" — it is **not** added a second time
      (log: `Already knew that…`), and the file still has one copy.
- [ ] 📋 **Cap:** set `[memory].cap` low (e.g. `3`), add a couple of `remember that…` facts, then
      generate several milestones. The file stays at the cap; the oldest **milestones** drop first
      while your explicitly-remembered facts survive.
- [ ] 📋 Relaunch COVAS++ with the same journal present — old milestones are **not** re-captured
      (capture only sees live events; startup priming doesn't republish).
- [ ] Ask **"what can you do"** → the **memory** capability is listed; drilling in mentions
      remembering facts and milestones.

Notes:

## 13c. Persistent memory — recall in conversation (issue #61)  🔊 HW 📋 FILE
> Memory now comes back into a turn when you reach for it. Needs `[memory].enabled = true`
> (default). Seed a fact first: say **"remember that my main ship is a Krait Mk II"** (or hand-add
> a line to `memory/memory.jsonl`). Recall is keyword/tag, **offline and free** — no router/usage
> line for the recall block itself.
- [ ] 🔊 **Automatic recall injection:** ask **"do you remember my main ship?"** → COVAS answers
      **from the stored fact** ("a Krait Mk II"), not a guess. The log shows a `memory-recall`
      line with the matched reason.
- [ ] 🔊 **No-match is silent:** ask **"do you remember my favourite music?"** (nothing stored) →
      COVAS says it doesn't have that on file; the log's `memory-recall` note reads `(no matching
      memory)` and nothing is injected.
- [ ] 🔊 **Plain turn untouched:** ask an unrelated question (**"tell me a joke"**) → no
      `memory-recall` line, no memory block — recall only fires on past-referencing turns.
- [ ] 🔊 **Wake-word override:** say **"recall, what's my main ship"** → forces a lookup; the word
      *recall* is scrubbed from what COVAS answers about (it doesn't echo it back).
- [ ] 🔊 **Explicit tool path:** ask **"what do you know about my ship?"** → COVAS may call the
      `recall_memory` tool and reports the stored fact; a miss returns "nothing on file".
- [ ] 📋 **Cache-safe (no prefix growth):** across several recall turns, replies stay quick and the
      cached-prompt token count doesn't climb turn-over-turn — the memory block rides the current
      user message only, never the cached system prompt.

Notes:

## 14. Web control panel  🌐 PANEL 🔊 HW 📋 FILE

### 14.1 Live status & log
- [ ] The status light tracks state as you talk; the log scrolls with prompts, replies, router/usage, status/search lines (timestamped).

### 14.1a Voice-list filter (issue #26)  🌐 PANEL 🌍 NET
> Both voice dropdowns get a type-to-filter box: the **ElevenLabs voice** picker on the **main panel**
> (below the dropdown) and the schema-driven **ElevenLabs voice** picker on the **Settings** page
> (beside the dropdown). Needs an ElevenLabs key so the list actually populates.
- [ ] **Main panel:** type **3+ characters** in the filter box under **ElevenLabs voice** → the dropdown
      narrows to voices whose **name or category** contains the text (case-insensitive; try a category
      word like *"cloned"* or *"premium"*). Typing **1–2 chars** filters nothing; **clearing** the box
      restores the full list. The **currently-selected** voice stays visible even when it doesn't match.
- [ ] **Settings page:** same behavior in the filter box **next to** the schema `@elevenlabs_voices`
      picker — 3+ chars filters by substring, <3 clears. Picking a filtered voice still saves normally.

### 14.2 Settings page (N1) — http://127.0.0.1:8765/settings
- [ ] The page renders **grouped sections** with the **right control per type** (toggles, dropdowns, number/sliders, text/path) and inline help.
- [ ] **Filter box (issue #7):** type 3+ chars → the list narrows to settings whose **section, title, or description** contains the text (case-insensitive); sections with no matches hide entirely. Typing **1–2 chars** filters nothing (everything stays shown); **clearing** the box restores the full list. Verify a **section-name-only** match (e.g. type a group name that isn't in any title/help) still surfaces that section's settings.
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

### 14.5a Live Log — select & copy (issue #6)
- [ ] **Selection survives new lines:** during an active session (lines still arriving), **scroll up** and drag-select an older line → the selection is **not** lost and the view does **not** jump to the bottom while you're scrolled up / selecting. Scroll back to the bottom → auto-scroll **resumes**.
- [ ] **Copy button honours the filter:** in **Conversation** mode click **Copy** (log header) → clipboard holds **only** the timestamped Commander/COVAS lines (paste to check). Switch to **All**, Copy again → status/thinking/search/`[router]`/`[usage]`/system lines are included too. Text is clean `HH:MM:SS  who: text` — no HTML.
- [ ] **Per-line copy:** hover a line → a small **⎘** button appears; click it → just that line is on the clipboard (shows ✓ briefly).
- [ ] 🖥️ **Native window:** repeat the selection + Copy checks in the **packaged app's** window (not just the browser build) — selection highlights and both copy paths work there too.

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
- [ ] 🎮 **Jump to a new system** (with comms enabled) → the game's **"Entering channel &lt;system&gt;."**
  notification is **NOT spoken** (issue #56 — jump chrome is dropped by the comms gate). Listen for its
  absence on every jump regardless of population/settings.

### 18.3 Space chatter — populated-only + population-scaled frequency (C6)
- [ ] 🎮 With `[audio.cues].enabled`, sit in a **populated** system → occasional ambient **chatter**
  (rate-limited, never over-talking). Jump to an **unpopulated / deep-space** system → chatter goes
  **silent** (populated-only). 🎮 Trigger an **interdiction** → the layered sting + threat + pirate line.
- [ ] 🎮 Compare a **dense** system (population in the billions) with a **sparse** one (a few
  thousand) → chatter is noticeably **more frequent** in the dense one. Then lower
  `[audio.chatter].min_seconds` (or `full_population`) → chatter speeds up. Confirm each chatter line
  uses a **different random voice**.

### 18.4 Voice controls + live settings (C9)
- [ ] 🔊 By voice: *"mute the chatter"*, *"quiet the comms"*, *"turn the music down"*, *"turn the
  music up"*, *"stop the music"*, *"silence all the background audio"*, *"turn the ambient audio
  back on"* → each takes effect; your own replies are unaffected.
- [ ] 🌐 On the **Settings → Ambient audio** page, change a bus **volume**, the **cast provider**,
  **random ElevenLabs voices**, or the **chatter min/max seconds** / **full-population** → applies
  live (no restart). The **master** `audio.enabled` persists.
- [ ] 🌐 Confirm the old **Comms voice — male / female / default** dropdowns are **gone** from the
  Ambient audio group (superseded by the random voice cast; issue #8) — no stale `audio.comms.voices`
  keys are written to `overrides.json`.

### 18.5 Voice cast — random, persistent voices (C10+)
- [ ] 🔊 With the defaults (`cast_provider = "elevenlabs"`, `random_el = true`, empty pool) and an
  ElevenLabs key, receive comms from **two different NPCs/stations** → they sound **different**
  (random voices from your library), and the **same** speaker sounds the **same** for the whole time
  you're in that system. **Jump to a new system** → that speaker (or a new one) is **re-cast** to a
  fresh random voice. Confirm the cast voices are **distinct from your COVAS persona voice**.
- [ ] 🎮 In a **wing / multicrew / operation**, confirm each **player** keeps a **stable, distinct**
  voice — including **across system jumps** (the last 25 players are remembered).
- [ ] 🔊 Set `[audio.voices].cast_provider = "piper"` with a few `[[audio.voices.pool]]` `.onnx`
  entries → the cast reverts to **free local Piper** voices (no ElevenLabs credits). A voice you
  can't use (an ElevenLabs ™/famous voice) is never selected.

### 18.5a Context-aware voice quality — variety + perspective (issue #57)
- [ ] 🔊 **Variety (anti-repeat):** in a **busy populated** system with `[audio.cues].enabled` and
  the random ElevenLabs cast, listen to a run of **ambient chatter / NPC comms** lines → consecutive
  lines **spread across many voices** and you should **not** hear the same handful of voices repeat
  back-to-back (no "shuffled soundboard" feel). The bigger your ElevenLabs library, the more variety.
- [ ] 🔊 **Perspective (attribution):** when the **companion muses about the world** (an
  "our"-perspective line like *"nice to have some company out here"*), it's spoken in **your
  companion's OWN voice, clean** (the same voice as its replies), **not** a random radioed cast
  voice — and it does **not** carry the radio/static comms treatment. By contrast, **station
  traffic / patrol / market** ambient lines come from a **random radioed cast voice** on the
  comms bus. Confirm the perspective always matches the source.

### 18.6 Drop-in content (C11)
- [ ] On first run with the layer enabled, confirm the skeleton appears: **`audio/sfx/<cue>/`**,
  **`audio/music/<context>/`**, **`content/chatter/*.txt`**, **`content/interdiction_threat.txt`**,
  each with a README explaining the drop rule. The startup log shows a **content-status** line
  (how many files/lines per cue; what's still silent).
- [ ] 🔊 Drop a `.wav` into **`audio/sfx/thargoid_voices/`**, restart, jump to hyperspace → hear it
  on the ambient bus. Add lines to **`content/chatter/station_traffic.txt`** (one per line, `#` =
  comment), restart, dock → hear your lines (they override the built-in pool). Delete the file →
  falls back to the built-in pool. A missing/empty folder is simply silent (no error).

Notes:

### 18.7 Per-role cast providers (issue #14)  🎮 ED 🔊 HW 📋 FILE
> Every cast voice now routes through a **provider registry**, so each cast role can use a different
> TTS provider. This is behaviour-preserving by default — the first check is a regression check.
- [ ] **Default unchanged:** with no `[audio.voices.providers]` set, the cast sounds exactly as
  before (comms/chatter/player cast from `cast_provider`); COVAS's own voice is still your ElevenLabs
  persona.
- [ ] **Per-role override:** add to `config.toml` →
  ```toml
  [audio.voices.providers]
  chatter = "piper"
  comms   = "elevenlabs"
  ```
  (add Piper `.onnx` entries to `[audio.voices].pool` so chatter has local voices), restart → ambient
  **chatter** is spoken by **local Piper** voices (no ElevenLabs credits) while **station/NPC comms**
  stay on **ElevenLabs**. COVAS's persona voice is unaffected either way.
- [ ] **Fail-soft:** point a role at a provider with no working backend (e.g. `comms = "piper"` with
  an empty pool / no model) → those lines fall silent rather than crashing the audio layer; the rest
  keeps working.

Notes:

### 18.8 Edge (edge-tts) free neural voices (issue #15)  🎮 ED 🔊 HW 🌍 NET 📋 FILE
> **Free** neural TTS via `edge-tts` (Edge "Read Aloud" Azure voices) — hundreds of voices, **no
> key**, so ambient chatter never burns ElevenLabs credits. ⚠ It rides an **undocumented, no-SLA**
> endpoint that periodically breaks — it's **optional and never load-bearing**; **Piper** stays the
> guaranteed free floor. Install it first: `pip install -r requirements.txt`.
- [ ] **Persona voice:** set `[tts].provider = "edge"` (optionally `[edge].voice = "en-US-GuyNeural"`),
  restart, speak a turn → COVAS replies in the Edge voice with **zero** ElevenLabs usage.
- [ ] **Cast-eligible:** set `[audio.voices].cast_provider = "edge"` (or a per-role
  `[audio.voices.providers].chatter = "edge"`), restart, fly/dock in a populated system → ambient
  chatter/comms speak in distinct free Edge voices; COVAS's persona voice is unaffected.
- [ ] **Fail-soft to Piper:** with `[tts].provider = "edge"` **and** a valid `[piper].model`,
  disconnect the network (or block the endpoint) and speak → the persona voice **falls back to local
  Piper**; reconnect → Edge resumes. With **no** Piper model, a dead endpoint degrades to **text** and
  the loop returns to IDLE. Cast Edge voices fall **silent** on failure (never crash).
- [ ] **Catalog:** `.venv\Scripts\python.exe -m edge_tts --list-voices` lists the voices; a ShortName
  works as `[edge].voice` or a `[[audio.voices.pool]]` `ref`.

Notes:

### 18.9 Azure Neural TTS — reliable free-tier sibling of Edge (issue #17)  🎮 ED 🔊 HW 🌍 NET 📋 FILE
> **Official Azure Neural TTS** — the *same* voices as Edge, but with a real API, an **SLA**, and a
> **free monthly tier (~0.5M chars)**. No ToS/reliability asterisk. Needs a Speech resource: create one
> in the Azure portal, then add its key on the Settings **API keys** card (stored DPAPI-encrypted in
> `AzureSpeechKey.txt`; env vars are no longer read, #22) and set `[azure].region` to match it.
- [ ] **Persona voice:** set `[tts].provider = "azure"` (and `[azure].voice`, e.g. `en-US-GuyNeural`),
  restart, speak a turn → COVAS replies in the chosen Azure voice.
- [ ] **Speaking style:** set `[azure].style = "cheerful"` (on a voice that supports it), restart, speak
  → the delivery changes; an unsupported style is ignored (still speaks).
- [ ] **Cast-eligible:** set `[audio.voices].cast_provider = "azure"` (or `[audio.voices.providers].chatter
  = "azure"`), restart, fly/dock in a populated system → ambient chatter/comms use distinct Azure voices.
- [ ] **Fail-soft:** with `[tts].provider = "azure"` and a **wrong region or missing key**, speak → the
  reply degrades to **text** and the loop returns to IDLE (cast Azure voices fall silent); fix the
  key/region → voices return. No crash either way.

Notes:

### 18.10 OpenAI-compatible TTS — cheap cloud voice (issue #16)  🎮 ED 🔊 HW 🌍 NET 📋 FILE
> A **cheap cloud** voice over an OpenAI-compatible `audio/speech` endpoint (small fixed voice set —
> best as a persona or supplemental cast voice). Needs an OpenAI key (add it on the Settings **API keys**
> card — stored DPAPI-encrypted in `OpenAIAPIKey.txt`; env vars are no longer read, #22);
> `[openai_tts].base_url` is configurable for compatible endpoints.
- [ ] **Persona voice:** set `[tts].provider = "openai"` (and `[openai_tts].voice`, e.g. `nova`), add the
  OpenAI key on the Settings **API keys** card, restart, speak a turn → COVAS replies in the chosen OpenAI voice.
- [ ] **Model + tone:** try `[openai_tts].model = "tts-1"` (works) and `gpt-4o-mini-tts` with
  `[openai_tts].instructions = "Calm, professional ship-computer tone"` → the newer model reflects the
  instruction; `tts-1` ignores it (still speaks).
- [ ] **Cast-eligible:** set `[audio.voices].cast_provider = "openai"` (or a per-role override), restart,
  fly/dock in a populated system → ambient chatter/comms use OpenAI voices (a small set, so speakers
  repeat sooner than Edge/Azure).
- [ ] **Fail-soft:** clear the OpenAI key on the Settings **API keys** card (or set a bad `base_url`) and
  speak → the reply degrades to **text** and the loop returns to IDLE (cast OpenAI voices fall silent);
  restore the key → voices return.

Notes:

### 18.11 Cartesia (Sonic) low-latency persona voice (issue #18)  🎮 ED 🔊 HW 🌍 NET 📋 FILE
> A **low-latency premium PERSONA** voice (Cartesia Sonic) — a snappier alternative to ElevenLabs for
> COVAS's own voice; it **streams** so the first audio starts fast. **Persona-only** (not a cast
> provider). Needs a Cartesia key (add it on the Settings **API keys** card — stored DPAPI-encrypted in
> `CartesiaAPIKey.txt`; env vars are no longer read, #22) and a voice id.
- [ ] **Persona voice:** set `[tts].provider = "cartesia"`, a valid `[cartesia].voice` id (from
  play.cartesia.ai), add the Cartesia key on the Settings **API keys** card, restart, speak a turn →
  COVAS replies in the Cartesia voice, and audio starts **noticeably fast** (low time-to-first-audio).
- [ ] **Barge-in:** while COVAS is speaking a long Cartesia reply, tap push-to-talk → speech stops
  promptly (streaming cancel), loop returns to LISTENING/IDLE.
- [ ] **Persona-only:** set `[audio.voices].cast_provider = "cartesia"` → it has **no effect** on the
  cast (Cartesia isn't a cast backend); the cast keeps using its own provider. COVAS's own voice is
  unaffected.
- [ ] **Fail-soft:** clear the Cartesia key on the Settings **API keys** card (or blank `[cartesia].voice`)
  and speak → the reply degrades to **text** and the loop returns to IDLE; restore → the voice returns. No crash.

Notes:

---

## 19. Packaged build — install, first-run wizard & updates (I1–I9)  📦 🖥️ 🔊 HW 🌍 NET
> The **installed Windows app**: `COVAS++ Setup.exe` → native window, first-run wizard, and the
> Tier-2 self-updater. Extra markers: 📦 **PKG** — run the packaged build (not from source);
> 🖥️ **VM** — best done on a **clean Win11 snapshot** (VirtualBox/VMware; Windows Sandbox isn't
> available on Win11 Home) so "no Python/keys/model preinstalled" is actually proven. Revert the
> snapshot between passes. A partial dev-machine shortcut: delete `%APPDATA%\COVAS++` + the HF
> model cache to re-exercise the wizard (does **not** prove the no-runtimes case).

### 19.0 Provider bundle & default-voice self-test (issue #20)  📦 🔊 HW 🌍 NET
> The multi-provider epic (#10) added swappable providers imported **lazily** from
> `covas/providers/factory.py`, and **Edge (`edge-tts`) is the default TTS**. A lazy import the
> freeze misses would ship a bundle whose default voice silently degrades to text — so the freeze
> MUST bundle `edge_tts` + its `aiohttp` stack and prove it. `covas.spec` `collect_all`s them and
> `--selftest` imports the third-party `edge_tts` plus every provider module.
- [ ] **Frozen self-test (build machine):** `.\build.ps1 -Installer -SelfTest` → the freeze
  completes and the frozen `COVAS++.exe --selftest` prints `SELFTEST OK …incl. …edge_tts` and exits
  0. A missing bundle fails the build **loudly** instead of shipping. This proves `edge_tts` /
  `aiohttp` **and** `covas.providers.{edge_tts,azure_tts,openai_tts,cartesia_tts,piper_tts,elevenlabs_tts,openai_llm,gemini_llm,ollama_llm}`
  are all in the bundle.
- [ ] 📋 **Size delta:** note the onedir folder MB and `COVAS++ Setup.exe` MB the build prints; the
  `aiohttp` stack (~10 pkgs) should add only a few MB next to av/onnxruntime — record here: ____.
  (Measured at v0.5.0: onedir **264.4 MB**, Setup.exe **74.7 MB**; the `aiohttp`+`edge_tts` files
  total **~3 MB uncompressed** → **~1–2 MB** of the installer — negligible, as expected.)
- [ ] **Default Edge voice actually plays (not just imports):** launch the packaged `COVAS++.exe`
  with the **default** `[tts].provider = "edge"`, speak a turn → COVAS replies in the **Edge neural
  voice** (audible speech, not the text-only fallback), with **zero** ElevenLabs usage.
- [ ] **Other cloud providers construct in the frozen app:** in turn, set `[tts].provider` to
  `azure` / `openai` / `cartesia` and `[llm].provider` to `openai` / `gemini` (each with a valid
  key), relaunch, speak a turn → each **constructs and speaks without an `ImportError`** (they ride
  `requests`, already bundled — low risk, but confirm).

Notes:

### 19.1 Install (clean VM)  📦 🖥️
- [ ] Download **`COVAS++ Setup.exe`** from the Releases page → SmartScreen shows *"unknown publisher"* → **More info → Run anyway** installs (documented, expected).
- [ ] The installer runs **per-user with NO admin/UAC prompt** (installs to `%LOCALAPPDATA%\Programs\COVAS++`).
- [ ] It creates a **Start-menu entry** and a **desktop icon** (custom icon, not the generic exe icon), and registers an uninstaller.

Notes:

### 19.2 First-run wizard  📦 🖥️ 🔊 HW 🌍 NET
> On a machine with none of the dev state — that absence *is* the test.
- [ ] First launch (empty `%APPDATA%\COVAS++`) opens the **setup wizard**, not the panel.
- [ ] **Anthropic key** entry → accepted; **ElevenLabs key** entry → accepted (or skipped).
- [ ] **Mic** picker lists your input devices; pick one.
- [ ] **STT model** downloads (`small.en`, ~250 MB) with a **progress** indicator (needs internet); it's fetched **once**.
- [ ] Wizard **hands off to the control panel in the same window** — no second window, no browser. The finish message says it's **switching to the control panel** (NOT "close this tab"); the panel appears **without you closing anything** (closing the single native window quits the app).
- [ ] **No-ElevenLabs path:** finish the wizard with **no** EL key → the app runs **text-only** and says so; add a key later in Settings → spoken replies start working.
- [ ] **Default voice:** with an EL key, the voice defaults to **George** (or the first valid voice if George isn't in your catalog).
- [ ] 📋 After the wizard, `%APPDATA%\COVAS++` holds `config.toml`/keys/etc. and the model is under `%LOCALAPPDATA%`; **nothing** was written into the install tree (`%LOCALAPPDATA%\Programs\COVAS++`).

Notes:

### 19.3 Native window & quit  📦 🔊 HW
- [ ] App launches from the **desktop/Start-menu icon** as a **native window** (no browser tab, no URL bar) rendering the panel.
- [ ] **PTT works from the window:** hold `[`, speak, release → normal turn; audio plays.
- [ ] **Closing the window quits** the app — no tray icon, no lingering background process (check Task Manager: no `COVAS++`/python left running).

Notes:

### 19.4 ED files readable from the sandboxless install  📦 🎮 ED
> The reason MSIX was rejected — the install must read ED's journal + bindings with no container in the way.
- [ ] With ED running: *"Where am I?"* → names your **current system** (journal is readable from the installed app).
- [ ] With `[keybinds]`/`[honk]` on, the startup/log confirms your **`Custom.*.binds`** was found and parsed (not a "couldn't find binds" warning).

Notes:

### 19.5 Update banner → download → relaunch  📦 🖥️ 🌍 NET
> Best on a VM: install an **older** version, then publish/point at a **newer** GitHub Release.
- [ ] With a newer release available, an **"Update available → vX.Y"** banner appears in the panel on launch. (Already-current → **no** banner.)
- [ ] Click update → COVAS++ **downloads the new installer**, **exits**, and the installer launches (same SmartScreen step).
- [ ] After install, relaunch → *"What version are you?"* now reports the **bumped** version.

Notes:

### 19.6 Settings survive the update (decision #6)  📦 🖥️ 📋 FILE
- [ ] Before updating: change the **voice**, **mic**, and a couple of settings (panel or voice); 📋 note them in `%APPDATA%\COVAS++\overrides.json`.
- [ ] Run the update (§19.5) → after relaunch, **every changed setting is exactly as you left it** (defaults are NOT re-applied over your choices); `overrides.json` is unchanged.
- [ ] A setting **added** by the new version appears at its default **without** resetting your existing values.

Notes:

### 19.7 Uninstall  📦 🖥️
- [ ] Uninstall from **Apps & features** (or the Start-menu uninstaller) → the app and shortcuts are removed; the install tree under `%LOCALAPPDATA%\Programs\COVAS++` is gone.
- [ ] Note whether your `%APPDATA%\COVAS++` user data is retained (a reinstall should find your settings again).

Notes:

### 19.8 API keys encrypted at rest — Windows DPAPI (issue #22)  📋 FILE 🖥️
> Keys are stored ENCRYPTED with Windows DPAPI (CurrentUser scope): each `*APIKey.txt` holds a
> `DPAPI:<base64>` blob, never plaintext. Environment-variable key reads were REMOVED. A source run
> works for all of this (open the key files under `%APPDATA%\COVAS++`, or the dev data dir).
- [ ] 📋 **Encrypted on disk:** after entering keys (wizard or Settings), open `AnthropicAPIKey.txt`
  (and any other `*APIKey.txt`) → the content begins with **`DPAPI:`** and your raw key is **not**
  visible anywhere in the file.
- [ ] 📋 **Transparent migration:** drop a **plaintext** key into a fresh `AnthropicAPIKey.txt` (just
  the raw `sk-ant-…`, no `DPAPI:`), launch → the app works normally, and re-opening the file shows it
  has been **rewritten to `DPAPI:<blob>`** (migrated on first read). Your key still works.
- [ ] **Env var is ignored:** set `ANTHROPIC_API_KEY` in your environment but **remove** the key file
  → launch a source run → the app is **unconfigured** and the **setup wizard shows** (the env var is
  NOT used). Add the key via the wizard/Settings to proceed.
- [ ] **Wrong-machine blob = clear re-enter, not a crash:** copy a `DPAPI:` key file from another PC
  (or hand-edit the base64 to corrupt it) → launch → the app treats it as **no key** and logs a clear
  *"re-enter the key on this machine"* message (console/stderr); it does **not** crash. Re-enter the
  key and it works.
- [ ] 🔊 **check_setup:** `check_setup.bat` reports the **Anthropic key file** present (no
  `ANTHROPIC_API_KEY` env line anymore) and the Anthropic API call succeeds using the file key.
- [ ] 📋 **Inara key folded in (issue #24):** put a **plaintext** Inara key in `[cg].inara_api_key`
  (in `overrides.json`), launch → community goals still authenticate (unvisited CGs surface), a new
  **`InaraAPIKey.txt`** appears holding a **`DPAPI:`** blob, and the inline `inara_api_key` in
  `overrides.json` is **blanked**. A fresh key entered on the Settings **API keys** card also works
  (takes effect on restart).

### 19.9 Masked "API keys" Settings card — rotate any key (issue #23)  🌐 PANEL 📋 FILE
> The Settings page (`/settings`) has a write-only **API keys** card covering every provider
> (Anthropic, ElevenLabs, OpenAI, Gemini, Azure, Cartesia, Inara). Keys are never displayed — only a
> set/not-set badge — and are stored DPAPI-encrypted, never in `overrides.json`.
- [ ] 🌐 **Badges reflect reality:** open `/settings` → the **API keys** card shows **set** for
  providers whose key file has a key, **not set** for the rest. No key value is visible anywhere.
- [ ] 🌐📋 **Set / rotate:** paste a key into a **not set** provider → **Save** → the badge flips to
  **set**, the field clears, and the message says it takes effect on restart. The provider's
  `*APIKey.txt` now holds a **`DPAPI:`** blob (not your raw key). **Rotate** an already-set key the
  same way and the file's blob changes.
- [ ] 🌐📋 **Clear:** click **Clear** on a set provider → badge flips to **not set** and the key file
  is emptied. A **blank** Save is a no-op (an existing key is NOT wiped).
- [ ] 🌐 **Never leaks:** with a key set, reload `/settings` and check the field is still empty and
  the page source / network never contains the key text (only the boolean badge).

Notes:

---

## Needs-hardware / manual-only note
Everything in this file needs Doug's machine and can't be exercised in CI or a sandbox:
- 🔊 **HW** (mic + speakers) gates nearly every step — STT capture and TTS playback.
- 🎮 **ED** (§5–§10) needs Elite Dangerous running so the journal/Status.json feed live telemetry.
- ⌨️ **INJECT** (§6) sends real DirectInput scancodes into ED — do it parked and safe.
- 🌍 **NET** (§7, §8, §10, §16) needs internet (Spansh / Inara / web search).
- 🌐 **PANEL** / 📋 **FILE** checks need the running app and a browser / file access.
- 📦 **PKG** / 🖥️ **VM** (§19) need the built `Setup.exe` and, to prove the clean-install/first-run/updater story, a **fresh Win11 VM snapshot** (Windows Sandbox isn't available on Win11 Home).

The offline `pytest` suite covers the pure logic (parsing, routing, checklist ops, help
projection + grouping, query building, honk sequencing) for free — run `pytest` often; this
manual pass is for the on-hardware, in-game behavior it can't reach.

---

### Summary
- Passed: ___ / ___  ·  Failed: ___
- Anything to revisit:
