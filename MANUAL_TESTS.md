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
- 🥽 **VR** — needs a VR headset + SteamVR (and the optional `openvr` package) for the in-headset overlay.
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
- [ ] **Headless:** `run_covas.bat` (or `python run_covas.py`) → console banner shows your model, voice, Whisper size, and the capability on/off lines (Router, ED monitor, Proactive, Keybinds, **Reflexes** — shows `(fast-PTT […])` / `(auto ON)` when those are set, **Auto-honk**, Find module, Personality). No browser.
- [ ] **With panel:** `run_covas_ui.bat` (or `python run_covas_ui.py`) → same banner **plus** the browser opens http://127.0.0.1:8765 and the status light reads **IDLE**.
- [ ] Console prints the PTT scan codes line, and `QUIT: Ctrl+Alt+Q`.

Notes:

### 0.3 Capability toggles — enable what you want to test FIRST
Capabilities are gated in **`config.toml`** (edit freely) or **`overrides.json`** (what the panel
writes). The Settings page (§14.2) can also flip these; **most settings now apply live** (issue #90
— providers, keys, mic, Whisper, volumes, toggles) with only a tiny `RESTART_REQUIRED` set needing a
relaunch (`audio.enabled`, `audio.mix_sample_rate`, `ui.host`/`ui.port` — see §14.3).
Confirm each before running its section (as shipped,
**everything defaults ON** so the app shows full functionality out of the box):
- [ ] `[elite].enabled = true` — ED journal/Status monitoring. **Required by** proactive/route callouts, the keybind + honk combat guard, carriers, community goals, and the live "current system" used by every search. (§5, §6, §7, §8, §9, §10)
- [ ] `[proactive].enabled = true` — proactive callouts. (§5.2)
- [ ] `[route].enabled = true` — Route callouts while flying a plotted route. (§5.3)
- [ ] `[hud].enabled = true` — Companion HUD overlay (**off** by default; applies **live**, no restart). (§5a)
- [ ] `[hud].vr_enabled = true` — in-headset VR HUD overlay (**off** by default; needs SteamVR running, nothing to install). (§5b)
- [ ] `[keybinds].enabled = true` — Landing-gear automation. Keep `require_confirmation`/`combat_guard = true`. (§6.1)
- [ ] `[reflex].enabled = true` — Tier-2 combat reflexes (fire chaff / heat sink). **Off** by default, allowlist ships empty — set `[reflex].allowlist = ["chaff", "heat_sink"]` to opt in the spoken/hotword path. Keep `combat_guard = true`. (§6.3)
- [ ] `[reflex.auto].enabled = true` — Tier-2 **ambient** auto-reflexes (no voice). **Off** by default; needs `[reflex].enabled` too, plus a per-reflex enable (`[reflex.auto.heat_sink].enabled` / `[reflex.auto.chaff].enabled`). (§6.3.2)
- [ ] `[honk].enabled = true` — Auto-honk on arrival (**on** by default). No fire-group setup — it probes and backs out of a Surface-Scanner misfire. Set `[honk].trigger` only if your scanner is on secondary fire. (§6.2)
- [ ] `[comms_send].enabled = true` — send in-game chat by voice (**off** by default). Bind **Quick Comms Panel** to a key; outward-facing, so it always reads back and sends only on a separate confirm. (§6.4)
- [ ] `[macros].enabled = true` — Voice/UI-authored **custom macros** (#50). **Off** by default. Needs `[keybinds]` set up (macros only use allowlisted actions) + `[elite].enabled` (combat guard + triggers). (§6.5)
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
- [ ] **Reload without restart (issue #109):** trigger a **failure** cue and note the sound. Click
      **Open cues folder**, drop a new `.wav` into `sounds/failure/`. Click **Reload cues** →
      the message shows a per-type count (e.g. "reloaded — 3 failure, 1 thinking"). Trigger a
      failure again → the new clip is in rotation, **no restart**. Remove all files from
      `sounds/failure/`, click **Reload cues** again → falls back to the shipped default cue live.
      Drop a deliberately corrupt file into any type's folder and reload → no crash, other cues
      still play. (The **same** button also reloads the ambient drop-in content — SFX/music/chatter
      under `audio/`/`content/`, issue #110 — see §18.6.)
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
- [ ] **Barge-in tail check (issue #71):** while COVAS is **mid-sentence**, hold `[`, immediately say a short new phrase, then release. In the session log the captured `Commander:` line contains **only your new phrase** — **no trailing fragment of COVAS's previous reply** leaks in from the speakers. (Playback is silenced before the mic opens; a short leading mute window is the backstop.)
- [ ] 🌐 The panel's **CANCEL / STOP** button also stops an in-progress reply.

Notes:

## 3c. Type a prompt in the control panel (issue #76 — feature 02)  🌐 PANEL 🔊 HW
> The main panel has a **text box + ✈ send button** above the live log. A typed prompt runs a
> **full normal turn** (routing, context, tools, history, spoken reply) — just no microphone.
- [ ] 🌐 Type *"what time is it? keep it short"* in the box and press **Enter** → it appears in the log as
      `Commander: …`, the status light runs **THINKING → SPEAKING → IDLE**, and you **hear** the reply.
- [ ] 🌐 Type another prompt and click the **✈** button → same result (both Enter and click send).
- [ ] 🌐 The box **clears** on send.
- [ ] 🌐 An **empty / whitespace-only** box does nothing when sent (no turn, no log line).
- [ ] 🌐 **Precise glyphs:** type a system with an odd glyph (e.g. *"how far is Col 285 Sector →"* or a
      name with `café`) → it goes through **verbatim** (something STT would mangle by voice).
- [ ] 🌐 **Barge-in parity:** while a spoken reply is playing, send a typed prompt → it interrupts and
      starts the new turn (like a push-to-talk press).

Notes:

## 3d. Concurrency & lifecycle edge cases (#156)  🌐 PANEL 🔊 HW 🎮 ED
> Foundation hardening for four near-zero-probability races in `app.py`. No new behavior — these
> confirm nothing regressed and nothing doubles up. Covered offline by `tests/test_app_concurrency.py`;
> these are the on-hardware spot-checks.
- [ ] 🌐 **Typed prompt vs. proactive callout:** with `[proactive].enabled = true` and ED live, fire a
      typed prompt (§3c) at the same moment an ED event would trigger a callout (e.g. right after an FSD
      jump). You get **one** turn — the typed reply — never two overlapping voices / a doubled reply.
- [ ] 🌐 **Reset a setting while holding PTT:** hold the PTT key, and from the panel (another device or a
      second hand) click **Reset** on any setting. Release PTT → the turn dispatches normally; no crash,
      no dropped/garbled hotkey, log stays clean (no `KeyError`).
- [ ] 🖥️ **Enable HUD/route mid-session:** toggle the Companion HUD (§5a) or route callouts (§5.3) on
      while the app runs → each ED event is announced/repainted **once**, never twice (no double-dispatch).
- [ ] 🔊 **Cancel wins over a callout:** hold `[` (or click **CANCEL**) exactly as an ED event lands →
      silence; the pending callout does **not** sneak through after the cancel.

Notes:

## 3a. Hands-free / continuous listening (issue #63 — `[listen].mode = "continuous"`)  🔊 HW 🎧 headset
> Off by default. Switch to continuous by voice (*"switch to continuous listening"*), on the Settings
> page (**Activation mode** under *Voice input*), or in `config.toml` (`[listen].mode`). Best tested
> with a **headset** so COVAS doesn't hear its own voice. PTT must keep working the whole time.
- [ ] **Switch on live by voice:** in PTT mode, hold `[` and say *"switch to continuous listening."*
      The log shows **Hands-free continuous listening ON**; no restart needed.
- [ ] **Hands-free turn:** with your hands off the keyboard, just say *"COVAS, what time is it? Keep it
      short."* → you hear the **listen** cue at your speech onset, then the normal
      **LISTENING → TRANSCRIBING → THINKING → SPEAKING → IDLE** turn runs and the reply plays.
- [ ] **Trailing silence ends the turn:** the capture closes only after you **stop** talking for a
      moment (not on a short mid-sentence pause) — a brief breath doesn't cut you off.
- [ ] **Noise rejection:** a single cough / key clack / short "uh" does **not** start a turn.
- [ ] **Barge-in preserved:** while COVAS is speaking a reply, **start talking** → the speech cuts off
      and a fresh capture begins (same as a PTT barge-in).
- [ ] **PTT still works in continuous mode:** hold `[` and speak → that PTT turn runs normally, and a
      simultaneous VAD capture does **not** double-fire (PTT wins while held).
- [ ] **Sensitivity tuning:** if background noise keeps opening captures, raise **Voice-detect
      sensitivity** (`listen.energy_threshold`) on the Settings page and confirm it settles down.
- [ ] **Switch back:** say *"switch to push-to-talk"* (or set mode to `ptt`) → log shows **Hands-free
      continuous listening OFF (push-to-talk)**; the mic listener stops and only PTT starts turns.
- [ ] **Fail-soft:** with a bad/absent mic in continuous mode, startup logs a fall-back to PTT and the
      app still runs (it does not crash).

Notes:

## 3b. Wake word — hands-free gating (issue #64 — `[listen].wake_word`)  🔊 HW 🎧 headset 🌐 PANEL
> Off by default (blank). Only affects **continuous** mode; PTT is never gated. Set a wake word by
> voice (*"set the wake word to COVAS"*), on the Settings page (**Wake word** under *Voice input*), or
> in `config.toml` (`[listen].wake_word`). Turn on continuous mode first (section 3a).
- [ ] **Set it by voice:** in continuous mode, hold `[` and say *"set the wake word to COVAS."* The
      log/Settings show the wake word is now `COVAS`.
- [ ] **Armed turn:** hands off the keyboard, say *"COVAS, what time is it? Keep it short."* → the turn
      runs normally and the reply plays. The transcript printed as **Commander:** has the wake word
      **stripped** (it reads *"what time is it? ..."*, not *"COVAS, ..."*).
- [ ] **Stray utterance dropped:** with the wake word still set, say something WITHOUT it (e.g. talk to
      someone else in the room, *"is dinner ready yet?"*) → the log shows **`[listen] wake word 'COVAS'
      not heard`** and **NO turn runs** (no Thinking, no reply, no cost).
- [ ] **Wake word only:** say just *"COVAS."* on its own → it returns to Idle (nothing to answer), no
      LLM call.
- [ ] **Fuzzy tolerance:** say the call sign slightly off (*"Kovas, what's my fuel?"*) → with
      `wake_word_fuzzy` on (default) it still arms and answers. (Set it off to require an exact match.)
- [ ] **PTT bypasses the gate:** with the wake word set, **hold `[`** and just ask a question WITHOUT
      the wake word → the PTT turn runs normally (a deliberate press is never gated).
- [ ] **Clear it:** *"clear the wake word"* (blank) → continuous mode again runs on any capture, exactly
      as section 3a.

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
> differ. A *cloud* LLM, so it's fine in-game and the router tiers it via `[openai.tiers]`, which ship
> **unset** so every tier reuses `[openai].model` (that's why a bare model swap to another endpoint
> works even with the router ON). Needs a key in `OpenAIAPIKey.txt` (DPAPI-encrypted; add it in
> Settings — env vars are no longer read, #22). Switching `[llm].provider` applies **live** (issue
> #90) — the **next turn** uses it, no restart (an in-flight turn finishes on the old one).
>
> **Provider limits matter.** COVAS sends a large tool set (~10K tokens) every turn and runs many
> turns per session, so the endpoint needs headroom — roughly **≥100K TPM and ≥1,000 requests/day**.
> **Groq's FREE tier (12K TPM / 100K tokens-per-day ≈ ~9 turns/day) cannot run COVAS and is not
> supported** — it returns HTTP 413/429; no app-side tuning changes that (the daily-token ceiling is
> the wall). For a paid/high-limit endpoint (paid Groq, DeepSeek, OpenRouter-with-credits, OpenAI) it
> works fine. For a **free** option that actually fits the load, use the **Gemini** provider (§4.2).
- [ ] **Conversation:** set `[llm].provider = "openai"` (default `base_url`/`model` = OpenAI
  `gpt-4o-mini`), restart, speak a turn → COVAS answers via OpenAI; the `[router]` line shows the
  OpenAI model (e.g. `[cheap] gpt-4o-mini`) and `[usage]` shows token counts (+ a cost if priced).
- [ ] **Tool calling works:** *"What's my next objective?"* / *"Mark fuel scooping complete."* → the
  checklist tool fires (log shows the tool call) and COVAS confirms — proving delta-assembled
  `tool_calls` are handled.
- [ ] **Escalation tiers:** first set distinct `[openai.tiers]` ids (they're unset by default, so every
  tier reuses `[openai].model` — the router line would otherwise show the same model for all tiers).
  Then *"Think hard…"* → the router line shows `[standard]` with the `[openai.tiers].standard` model;
  *"use opus/the big model"* wake phrase → `[premium]`.
- [ ] **Alt endpoint (the "one provider" claim):** point `[openai].base_url` + `model` at a viable
  OpenAI-compatible service — **DeepSeek** (`https://api.deepseek.com/v1`, `deepseek-chat`),
  **OpenRouter**, or **paid Groq** — with that service's key, restart → conversation still works
  through the same provider. Leave `[openai.tiers]` unset (the default) so the router uses your
  `[openai].model`; with the router ON the log line reads e.g. `[cheap] deepseek-chat`, **not**
  `gpt-4o-mini`. (Do **not** use Groq's *free* tier here — see the limits note above: it 413/429s.)
- [ ] **Fail-soft:** clear the key (or set a bad `base_url`) → the turn degrades to text and the loop
  returns to IDLE; restore → it works again. No crash.
- [ ] **o-series reasoning model (issue #153):** set `[openai].model = "o3-mini"` (or another o-series
  id) against OpenAI with a key, restart, speak a turn → COVAS answers (the request sends
  `max_completion_tokens`, **not** `max_tokens`). Before the fix every o-series turn 400'd and spoke
  the misconfig heads-up. A regular `gpt-4o-mini` turn still works unchanged.

Notes:

### 4.2 Gemini LLM provider (issue #13)  🔊 HW 🌍 NET 📋 FILE
> Google Gemini on the **native** API — tool calling + Google-Search **grounding** + a cheap Flash
> default tier. A *cloud* LLM, tiered via `[gemini.tiers]` (Flash-Lite/Flash/Pro). Needs a key in
> `GeminiAPIKey.txt` (DPAPI-encrypted; add it in Settings — env vars are no longer read, #22).
> Switching `[llm].provider` applies **live** (issue #90) — the next turn uses it, no restart.
>
> **Model ids are deprecation-proof aliases (issue #91).** The shipped ids are the `-latest` aliases
> (`gemini-flash-lite-latest` default, `gemini-flash-latest` standard, `gemini-pro-latest` premium),
> which always resolve to Google's current GA model per class — pinning a concrete id kept breaking
> (the guessed `gemini-3.1-flash-lite` 404'd; GA `gemini-2.5-*` is now "superseded"). You can still set
> a concrete id from <https://ai.google.dev/gemini-api/docs/models>. `check_setup.py` warns only if a
> **concrete** configured id isn't in the live list; `-latest` aliases are always accepted (they don't
> appear verbatim in `GET /models`).
>
> **Recommended free provider.** Gemini's Flash **free** tier (~250K TPM / 1,500 requests-per-day)
> comfortably fits COVAS's per-turn tool load — unlike Groq's free tier (§4.1) — so it's the
> zero-cost path that actually works. Google trims free quotas without notice, so treat exact
> numbers as best-effort.
- [ ] **Model-id guard:** run `.venv\Scripts\python.exe check_setup.py` with `[llm].provider = "gemini"` and a
  key set → the **Gemini API** section reports the live model count and confirms `[gemini].model` / tiers are
  all in the live list (or WARNs which id is stale). No crash without a key.
- [ ] **Conversation:** set `[llm].provider = "gemini"`, add your Gemini key in Settings, restart, speak a turn
  → COVAS answers via Gemini (no 404 on the first word); `[router]` line shows the Gemini model
  (e.g. `[cheap] gemini-flash-lite-latest`) and `[usage]` shows token counts.
- [ ] **Tool calling works:** *"What's my next objective?"* / *"Mark fuel scooping complete."* → the
  checklist tool fires (log shows the tool call) and COVAS confirms.
- [ ] **Search grounding:** with `[web_search].enabled = true`, ask something current
  (*"What's the latest on the Thargoid war?"*) → the log shows a **`Searching…`** side-channel line
  (grounding queries) and the answer reflects live info.
- [ ] **Escalation tiers:** *"Think hard…"* → the router line shows `[standard]` with the
  `[gemini.tiers].standard` (`gemini-flash-latest`) model.
- [ ] **Fail-soft:** clear the key → the turn degrades to text and the loop returns to IDLE; restore →
  it works again. No crash.

Notes:

### 4.3 Transient provider outage — retry, slow heads-up, degraded line (issue #97)  🔊 HW 🌐 PANEL
> Cloud LLMs have bad minutes (Anthropic **529 Overloaded**, 429s, 503s). COVAS should **retry** with
> backoff, speak a **"still slow"** heads-up if a turn drags, and — if it can't recover — say the
> provider is **overloaded** (named) instead of dying. Simulate an outage without waiting for a real
> one by pointing a provider at a URL that returns errors, or by using a throwaway/over-quota key.
> **How to force a 529/5xx or timeout (pick one):**
> - **Fail-then-succeed** (for "Retry then recover"): run `python scripts\flaky_llm_stub.py` — a local
>   OpenAI-compatible endpoint that returns 503 twice **then** streams a real reply, re-arming each turn.
>   Set `[llm].provider = "openai"` and `[openai].base_url = "http://127.0.0.1:8799/v1"` (any throwaway
>   key/model — a hand-dropped plaintext `OpenAIAPIKey.txt` is accepted), restart, and speak/type a turn.
> - **Always-fail** (for "Exhausted → degraded"): point `[openai].base_url` at an endpoint that returns
>   5xx/429 (e.g. `https://httpstat.us/529`), or at an unroutable host/port to force a **connection timeout**.
> - Or temporarily lower `[llm.retry].max_total_wait` / raise `attempts` to watch the backoff.
> - **Note:** retry logging is wired for the raw providers (**openai / gemini**). The default
>   **Anthropic** provider retries *inside its own SDK*, which is silent to the COVAS log — so use the
>   OpenAI stub above to see the retry lines.
- [ ] **Retry then recover:** with the flaky stub (fails twice then succeeds), one turn **still answers** —
      the log shows `OpenAI HTTP 503 — retry N/4, backing off …s` lines (backoff) before the reply. No
      user-visible error.
- [ ] **Slow heads-up (watchdog):** set `[llm].slow_warning_seconds` low (e.g. `5`) against a slow/hung
      endpoint → after ~5 s COVAS **speaks** *"the AI service is being slow… I'm still trying"* in the
      **current voice**, and still delivers the real reply (or the degraded line) afterward.
- [ ] **Exhausted → degraded line:** with an endpoint that always returns 529/5xx, a turn ends with a
      short spoken, **provider-named** *"…is overloaded right now, Commander…"* line — not a raw error —
      and 🌐 the log shows a precise reason (e.g. `provider degraded: … 529 … — retried 4×, giving up`).
- [ ] **Fail-fast (no pointless retry):** point at a **404** model or a **bad key (401)** → the turn
      fails **immediately** (no long backoff) and returns to Idle — and now (issue #108) **speaks** a
      "check your settings" heads-up instead of failing silently; see **§4.3a** for the full check.
- [ ] **Cancel during backoff:** while a turn is retrying/slow, **tap `[`** (or panel **CANCEL**) →
      it aborts **instantly**, no waiting out the backoff, back to IDLE.
- [ ] **Text-only fail-soft:** in text-only mode (no TTS key), the slow/degraded messages appear as
      **log lines** (not spoken) and the loop never crashes.
- [ ] **History intact:** after a degraded/failed turn, the **next** turn answers its own question
      (the failed turn left no orphaned prompt behind).

Notes:

### 4.3a LLM misconfiguration — spoken "check your settings" heads-up (issue #108)  🔊 HW 🌐 PANEL
> A bad model id, a wrong/missing key, or a bad endpoint is NOT a transient blip (§4.3 above) — it
> won't fix itself on retry, and only you can fix it. Unlike an overload, this line is deliberately
> **not** silenced after the first turn: it speaks on every failed turn until you fix the setting.
- [ ] **Bad model (404):** set `[gemini].model` (or the active provider's model field) to a nonsense
      id, e.g. `gemini-does-not-exist`, on the Settings page. PTT and speak → COVAS **speaks**
      *"I can't reach Gemini, Commander — the model name looks wrong. Check the AI settings."* and
      🌐 the log shows the precise `404` reason (`provider misconfigured: Gemini …`).
- [ ] **Bad/missing key (401/403):** clear or scramble the active provider's API key. PTT → COVAS
      speaks the **key** variant of the line (*"…the API key looks wrong or missing…"*).
- [ ] **Fixed → normal again:** restore the correct model/key → the next turn answers normally, no
      warning, no residual state.
- [ ] **Repeats, not rate-limited:** with the bad setting still in place, speak **two** separate
      turns in a row → the heads-up is spoken **both** times (not just the first).
- [ ] **Text-only mode:** repeat the bad-model case in text-only mode (no TTS key) → the heads-up
      appears as a `COVAS` **log line**, not silence, and nothing crashes.
- [ ] **Off switch:** set `[llm].speak_config_errors = false` on the Settings page, repeat the
      bad-model case → the **failure cue** still plays and the log still records the precise reason,
      but nothing is spoken. Set it back to `true` afterward.
- [ ] **Doesn't cross with §4.3:** a genuine transient outage (e.g. `https://httpstat.us/529`) still
      says *"…is overloaded right now, Commander…"* — never the settings line — and a bad-model/key
      case never says "overloaded".
- [ ] **History intact:** after a misconfigured turn, history stays empty (no orphaned prompt) and
      fixing the setting lets the very next turn answer cleanly.

Notes:

### 4.4 Optimization level — capability/token tiering (issue #84)  🔊 HW 🌐 PANEL
> `[llm].optimization_level` picks how many tool clusters COVAS advertises (the full set is ~10K
> tokens) **and** whether background LLM calls (proactive callouts, chatter flavor, comms variants)
> run. `auto` (default) chooses per provider; the five manual levels are `Full` / `Standard` / `Lean`
> / `Minimal` / `Bare`. Chosen **once at startup** — restart after changing it. Watch the startup log
> for the `Optimization level: … — tools: …; background: …` line.
- [ ] **Auto default (Anthropic):** with the stock config, the startup log reports **`Full (auto)`**
      with all tool groups listed and all three background flags **on**. Tool-using turns (checklist,
      "where am I", a Spansh search) all still work.
- [ ] **Manual `Minimal`:** set `[llm].optimization_level = "Minimal"`, restart → the log shows
      **`Minimal (manual)`** with **`tools: core, checklist`** and **all background off**. Checklist
      voice commands still work; a **Spansh/engineering** request is politely declined / not tool-driven
      (those tools aren't advertised), and **proactive callouts stay silent** even with
      `[proactive].enabled = true`.
- [ ] **Manual `Bare`:** set `Bare`, restart → **`tools: no tools`**; COVAS still converses but drives
      no tools at all (e.g. it won't tick the checklist).
- [ ] **Groq-free auto → Minimal:** set `[llm].provider = "openai"` and `[openai].base_url =
      "https://api.groq.com/openai/v1"`, keep `optimization_level = "auto"`, restart → the log reports
      **`Minimal (auto)`**. (A **paid** Groq user would set `Full` manually.) 🌍 NET
- [ ] **Custom TPM:** with an unknown `[openai].base_url` and `auto`, set `[llm].custom_tpm = 20000`,
      restart → the log reports **`Lean (auto)`** (the TPM mapped it), not `Full`.
- [ ] **Background suppressed vs. Full:** at `Full`, an ambient chatter line (with `[audio.cues].flavor
      = true`) can be **LLM-flavored**; at `Standard`/`Lean`/etc. the same line is **canned/pooled only**
      — and comms lines are read **verbatim** — with **no extra LLM cost** in the usage log.
- [ ] **Cache stays warm:** the level doesn't change mid-session (no per-turn tool churn), so repeated
      turns keep hitting the prompt cache (usage log shows cache reads, not full re-writes each turn).

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

### 5.1a Credits & currencies — grounded wallet + honest degradation (issue #101)
> Requires `[elite].enabled = true` and ED launched this session (balances come from the journal's `LoadGame` / `CarrierStats`, which only arrive at login).
- [ ] *"How many credits do I have?"* / *"What's my balance?"* → reports your **real credit balance** from the journal, **hedged** ("as of login…"), not an invented or round number.
- [ ] *"How much is on my fleet carrier?"* (if you own one) → reports the **carrier balance**, same login hedge.
- [ ] **Honest degradation:** *"How many merc coins do I have?"* (a currency COVAS doesn't track) → it says plainly it **doesn't have data on that currency** (its game knowledge may predate it) and offers to **web-search** — it must **never** invent an amount.
- [ ] Ask about credits **out of the game** (ED not launched) → it says it has no balance yet, rather than guessing.

Notes:

### 5.1b Journal monitoring survives a bad line (#152)  🎮 ED 📋 FILE
> Requires `[elite].enabled = true`. The journal watcher must fail **soft**: one malformed/unexpected event, or a journal file vanishing during a rollover, may not silently stop all further monitoring for the session.
- [ ] **Bad line is skipped, tailing continues:** with COVAS++ running and ED live, append a garbage line to the current journal (e.g. `echo '{"event":"__notreal__","x":{}}' >> Journal.<latest>.log`), then do a real in-game action (FSD-jump / dock). The action is still reflected — *"where am I?"* / *"what did I just do?"* stays current. At most a single warning is logged for the bad line; monitoring does **not** go dark.
- [ ] **Rollover race is harmless:** let ED roll to a new journal (long session, or relog) while COVAS++ runs → it picks up the new file and keeps narrating; no watcher-dead silence, no traceback in the log.
- [ ] **Event straddling startup is not lost (#161):** start COVAS++ **while a jump/dock is landing** in the journal (relog COVAS++ mid-action, or start it the instant you jump), so its *final* journal line is half-written at startup. Once ED finishes writing that line, the action still lands — *"where am I?"* reflects the new system/station within a poll or two; the straddling event is **not** silently dropped.

### 5.1c Registry persistence never stalls the voice loop (#161)  🎮 ED 📋 FILE
> Requires `[elite].enabled = true`. The journal thread now persists its disk-backed registries (visit ledger #138, owned-ships #134, per-ship loadouts #135, NPC-crew #125) **outside** the EDContext lock, so a slow/locked disk can't stall a `snapshot()`/`summary()` read.
- [ ] **Responsive under a busy disk:** during heavy fleet activity (jumping, docking, boarding different ships so the registries write often), COVAS++ keeps answering *"where am I?"* / *"what did I just do?"* promptly — no perceptible pause tied to journal writes.
- [ ] **Registry files still update:** confirm the git-ignored registry files under the data dir (`memory/`, owned-ships, ship-loadouts, visit ledger) still change on disk as you play — persistence moved off the lock but was **not** dropped.

Notes:

### 5.2 Proactive callouts (`[proactive].enabled = true`)
- [ ] **Arrival:** **FSD jump** to a new system → within a few seconds COVAS speaks a short in-character callout **without** any PTT press (fires only when idle).
- [ ] **Dock** at a station → a `Docked` callout fires (at most one line amid a jump→supercruise→dock burst — min-interval throttle).
- [ ] **Mute by voice:** *"COVAS, stop the callouts."* → confirms; trigger another event → **no** callout. Then *"COVAS, turn callouts back on."* → next event announces again.
- [ ] A callout in progress is cancelable: hold `[` mid-callout → it cuts off.

Notes:

### 5.2a On-foot / SRV awareness & callouts (#54)  🎮 ED (Odyssey)
> Requires `[elite].enabled = true`. Callout checks also need `[proactive].enabled = true`. Needs **Odyssey** (on-foot) and a ship with an SRV bay. Read-tool checks work without proactive.

Read tools (any time, no PTT-free callout needed):

- [ ] **On foot:** disembark, then *"how's my oxygen?"* / *"am I okay out here?"* → reports **oxygen / health / temperature / gravity** from live telemetry.
- [ ] **SRV:** deploy the SRV, then *"SRV status."* / *"how's the buggy?"* → reports **SRV hull** and cargo.
- [ ] **Exobiology:** with the Genetic Sampler, log a sample of an organism, then *"how many samples do I need?"* → reports the **genus and samples-so-far** (e.g. "1 of 3 — 2 more needed"). After the third (Analyse), it reports **complete**.
- [ ] **Mode-appropriate:** the on-foot/SRV readings only make sense in their mode; back in the ship they clear (a stale oxygen reading shouldn't linger).

Proactive callouts (`[proactive].enabled = true`; each fires only when idle, throttled, mutable):

- [ ] **Bio sample:** log your **second** sample of an organism → a callout like *"sample two of three — one more to analyse."*
- [ ] **Oxygen low:** let on-foot oxygen fall **below ~25%** → an *"oxygen's getting low"* callout (once — cooldown-gated, no repeat while it stays low).
- [ ] **SRV hull low:** take the SRV **below ~30% hull** → a *"hull's getting low"* callout.
- [ ] **Mute applies:** with the proactive mute on ("stop the callouts"), none of the above speak.

Notes:

### 5.2b Place-aware & visit-history callouts (#138)  🎮 ED
> Requires `[elite].enabled = true` **and** `[proactive].enabled = true`. On arrival the companion recognises notable places and remembers how often you've been there — grounded facts it voices, never invents. History remarks are occasional and ride a dedicated cooldown (`[proactive].place_cooldown`).

- [ ] **Engineer base recognised:** dock at an engineer's base (e.g. **Farseer Inc** in Deciat) with proactive on → the arrival callout **names the place / engineer** ("Farseer's workshop"), not a generic "docked". (Grounded — it should never invent a wrong engineer.)
- [ ] **Frequency remark:** dock at the **same** base several times in a session → after the first, a later callout references the **repeat / count** ("back again", "tenth time today") — accurate to how many times you actually arrived.
- [ ] **Not every dock:** repeated docks in quick succession do **not** each get a history remark — colour stays occasional (the place cooldown gates it); ordinary stations still get today's plain callout.
- [ ] **First visit to a system:** FSD-jump into a **brand-new** system → the callout can note it's your first time there.
- [ ] **Own carrier / landmark:** dock at your **own fleet carrier**, or somewhere famous (e.g. **Hutton Orbital**) → recognised as such.
- [ ] **Persists + private:** after a restart, prior visit counts survive (the ledger is on disk); confirm `visit_ledger.json` is **git-ignored** and never committed.
- [ ] **UTC windows (not local-skewed) (#155):** on a machine whose local time zone is **not UTC** (e.g. UTC-8), dock somewhere, then dock again within the hour → the "N times in the last 24 hours" count is accurate (the arrival is not pushed in/out of the 24h/7d window by your UTC offset). Journal stamps are UTC; the ledger now parses them as UTC.
- [ ] **Mute applies:** with the proactive mute on ("stop the callouts"), none of these speak.

Notes:

### 5.2c Long-hyperspace flavor remark (#149)  🎮 ED
> Requires `[elite].enabled = true` **and** `[proactive].enabled = true`. On a longer-than-normal **plotted** jump, COVAS passes the tunnel time with one short, LLM-varied, in-character remark (Thargoid/hyperdiction flavor). Pure atmosphere — no game facts asserted. Plot a route so `NavRoute.json` has coordinates.

- [ ] **Long jump → a remark:** plot a route with a **long** hop (≥ `[proactive].long_jump_ly`, default 50 ly) and jump → part-way through hyperspace, hear a short, light, in-character line (e.g. a playful Thargoid/"orange sidewinder" quip).
- [ ] **Varied, not canned:** do **several** long jumps → the remarks **differ** each time (no fixed pool).
- [ ] **Short jump → silence:** a normal short jump (below the threshold) produces **no** flavor remark.
- [ ] **Cooldown:** back-to-back long jumps within `[proactive].long_jump_cooldown` don't each get a line.
- [ ] **Mute / disable:** with the proactive mute on ("stop the callouts"), or `[proactive].long_jump_enabled = false`, long jumps stay silent.
- [ ] **No false facts:** the line never claims a Thargoid *is* present or names a real place/number — it's speculative flavor only.

Notes:

### 5.3 Route callouts (N4 — `[route].enabled = true`)  🎮 ED
> Plot a multi-jump galaxy-map route first (writes `NavRoute.json`). These go through the proactive path — spoken only when idle, cancelable, and silenced by the proactive mute too.
- [ ] **Scoopable heads-up:** as a target locks in, COVAS names whether the star you're **arriving at** is scoopable — never a bare "next star". Fly a route where the immediate destination is scoopable and the one after it isn't → hear the two-star line: "This star's scoopable — but the one after isn't, so top off here before you jump on."
- [ ] **Arriving not scoopable:** plot a route where the immediate destination isn't scoopable → "Heads up — the star you're jumping to isn't scoopable." (clearly *this* jump's destination, not the one after).
- [ ] **Both scoopable:** immediate destination and the one after are both scoopable → the brief "Next star's scoopable."
- [ ] **No false "next star" while headed to a scoopable star:** confirm you never hear "isn't scoopable" while mid-hyperspace toward a star that **is** scoopable (the old off-by-one bug, #148) — speaking during hyperspace itself is fine/expected, just correct about which star.
- [ ] **Hazard warning — neutron star (#147):** plot a route with a **neutron star** as the immediate next hop → on locking the target, hear "Heads up, Commander — next jump's a neutron star. Mind the exclusion zone, and no fuel there." **and no separate** "isn't scoopable" line right before/after it (supersede, not double up).
- [ ] **Hazard warning — white dwarf (#147):** same with a **white dwarf** next hop → "Careful — a white dwarf next. Watch the jets; you can't scoop it." (also superseding the plain "not scoopable" line).
- [ ] **Hazard toggle:** set `[route].callout_hazard = false` → flying toward a neutron star/white dwarf gets **no hazard warning**, but you still hear the plain "isn't scoopable" line (it's still non-scoopable) and normal scoopable callouts for other stars are unaffected.
- [ ] **Jumps remaining:** every **Nth** jump (`[route].every_n`, default 5) it announces jumps remaining to the destination (singular "1 jump remaining" near the end).
- [ ] **Arrival:** on reaching the final system it says "Arrived at <system>. Route complete." and stops.
- [ ] **Replot:** plot a new route mid-flight → callouts follow the new route (counts reset).
- [ ] **Mute:** with the proactive mute on ("stop the callouts"), route callouts are silent too.

Notes:

## 5a. Companion HUD overlay (issue #47 — `[hud].enabled`)  🖥️ 🔊 HW 🎮 ED
> A transparent, always-on-top 2D overlay of the companion's own state. **Off by default**; the toggle applies **live** (no restart, unlike other capability toggles). Cannot be exercised offline/headless — needs Doug's desktop. Run ED **borderless/windowed** so an always-on-top window can float over it (full-screen exclusive can cover any overlay — expected).
- [ ] **Toggle on — Settings page:** flip **Companion HUD overlay** on the [Settings page](docs/using/hud.md) → a small panel appears **top-right**, background fully transparent (desktop/game shows through), staying **on top**.
- [ ] **Setup guide → links (issue #121):** on the Settings page, each of the **three** Companion-HUD rows (**Companion HUD overlay**, **VR HUD overlay**, **Web HUD (OpenKneeboard)**) shows a **Setup guide →** link under its help. Clicking each opens the published HUD doc **in a new tab** at the right section — the 2D-overlay row → *Turning it on and off*, VR row → *In VR — the in-headset overlay*, web row → *In-headset without SteamVR — the web HUD*. No non-HUD row shows a Setup-guide link. The link survives a settings **filter/search** (it's still there after clearing the filter).
- [ ] **Toggle on — voice:** with the HUD off, say *"turn the HUD on"* → the panel appears (settings-by-voice path). *"Turn the HUD off"* → it disappears. Toggling is live (no restart).
- [ ] **Voice-loop state row:** hold PTT → the state row tracks **Listening → Thinking → Speaking → Idle** as you talk and COVAS replies.
- [ ] **Checklist row:** with a checklist loaded, the row shows your next pending item + count (e.g. *"…  (2/10 done)"*); mark it done by voice → the row advances to the next pending item.
- [ ] **Markdown is stripped, not shown literally (issue #122):** add a checklist item containing Markdown (e.g. `**Location:** Long Sight Base`) → the row shows clean prose (`Location: Long Sight Base  (…)`) with **no literal asterisks/backticks**.
- [ ] **snake_case / filenames survive stripping (issue #158):** add a checklist item with an underscored identifier or filename (e.g. `run check_setup_now` or `edit overrides.json then check_setup.py`) → the row shows the token **intact** (`check_setup_now`, not `checksetupnow`); real `_emphasis_` in the same line is still stripped.
- [ ] **Rows keep their fixed order after blinking out and back (issue #158):** with the HUD showing several rows, let the **checklist** or **route** row go empty (e.g. clear the checklist / the route), then re-populate it → the row returns to its **original top-to-bottom slot** (state · checklist · route · callout), it does **not** jump to the bottom of the panel.
- [ ] **Route row:** plot a multi-jump route (writes `NavRoute.json`) → the row shows **"N jumps to <dest>"**; lock the next jump → it appends **scoopable / NOT scoopable**; each jump decrements the count; arrival shows **"Arrived at <dest>"**.
- [ ] **Callout row:** trigger a proactive or route callout (§5.2/§5.3) → the last-callout row shows that line.
- [ ] **Click-through (Windows):** move the mouse over the panel and click → the click lands on the window/game **behind** it (the HUD is non-interactive).
- [ ] **Fail-soft:** it never blocks startup or the voice loop; with `[hud].enabled = false` no window appears and nothing is logged as an error.
- [ ] **Tk lifecycle — no leaked root / no double-build (issue #158):** toggle the HUD **on/off rapidly** several times (Settings page and/or voice) → exactly **one** panel exists at a time (never two stacked overlays), toggling stays live, and after turning it off there is **no orphaned window** and no leaked `hud-view` thread (Task Manager shows the process settle; no runaway CPU). On a machine/session where the window **fails to build** (e.g. no interactive desktop, a `HUD window failed` log), the app **continues** with no overlay and no leaked hidden Tk root — the build path destroys the root on failure and a build that exceeds the `start()` timeout is torn down rather than orphaned. *(These are the concurrency/lifecycle guards from #158 — the pure ordering + markdown pieces are covered by `pytest`; this row is the on-hardware confirmation.)*

## 5b. VR HUD overlay (issue #48 — `[hud].vr_enabled`)  🥽 VR 🎮 ED
> The **same** four-row HUD as §5a rendered as a true in-headset **SteamVR overlay** (reuses the identical data adapter — only the rendering surface differs). **Off by default** and **independent** of the 2D HUD. Cannot be exercised offline/headless — needs a VR headset and SteamVR. **Setup:** start **SteamVR**, and run **Elite Dangerous in VR through SteamVR** (native SteamVR headset, or Quest via Link/Air Link/Virtual Desktop in SteamVR mode). Nothing to install — `openvr` is bundled.
>
> **Test the FROZEN build (`build.ps1 -Installer`), not just the venv.** The venv has `openvr` because it's a build dep; that tells you nothing about whether the *shipped* app does. Releases through v0.12.0 froze without it and the VR HUD was dead code in every one — running from source would never have caught it.
- [ ] **Binding survives the freeze (do this first, no headset, no SteamVR):** run the **installed/frozen** app with `[hud].vr_enabled = true` and SteamVR **not** running. The log must say **`SteamVR not running`** — reaching that line proves the bundled binding *imported* (its DLL loads at import). If it instead says **`openvr unavailable`**, the freeze is broken (the binding didn't load) and the VR HUD is dead code in this build, exactly as it was through v0.12.0. Only the second message is a bug.
- [ ] **Enabling the VR HUD must NOT launch SteamVR (issue: attach-only):** with SteamVR **closed**, start the app with `[hud].vr_enabled = true` → **SteamVR does not start** (check for `vrserver.exe` / the SteamVR window). The overlay only ever *attaches* to a SteamVR that's already running; it must never drag it up (which would be pure nuisance under VDXR/OpenComposite or flat desktop). Then start SteamVR and the overlay appears on its next enable/toggle.
- [ ] **Bundled files:** `dist/COVAS++/_internal/openvr/` exists and contains `libopenvr_api_64.dll`, and `dist/COVAS++/_internal/PIL/` exists. (Absent → the build log will also have shouted a WARNING; see `covas.spec`.)
- [ ] **Toggle on — Settings page:** with SteamVR + ED-in-VR running, flip **VR HUD overlay** on the [Settings page](docs/using/hud.md) → the panel appears **floating in the headset** at a readable size, showing the state/checklist/route/callout rows.
- [ ] **Toggle on — voice:** say *"turn the VR HUD on"* → the overlay appears; *"turn the VR HUD off"* → it disappears. Live, no restart.
- [ ] **Crisp font + content-sized panel:** the rows render in **anti-aliased Segoe UI** (mixed case, not blocky caps), matching the 2D HUD; a long system name is **not** clipped with `..`; the panel **hugs its rows** (no big empty area below the text), and shrinks/grows as rows appear/disappear.
- [ ] **Live content:** confirm the in-headset rows track the same live data as §5a — hold PTT and watch the **state** row (Listening→Thinking→Speaking→Idle); with a checklist loaded the **step** row shows the next item; plot a route and the **route** row shows jumps-remaining (+ scoopable); a proactive/route callout fills the **callout** row.
- [ ] **Live placement by voice (no re-toggle):** with the overlay shown, say each of these and watch it move **immediately** — *"set the VR HUD distance to 1.0"* then *"…to 2.0"* (closer/farther); *"set the VR HUD height to 0.1"* / *"…to -0.3"* (up/below eye-line); *"set the VR HUD left right to 0.3"* (lateral). No re-enable needed.
- [ ] **Nudges (relative):** *"move the HUD left"* / *"…right"* / *"up"* / *"down"* shift it a step each time; *"closer"* / *"farther"* (and *"back"* / *"forward"*) change distance; *"bigger"* / *"smaller"* the size. An explicit amount works too: *"move it left 20 centimetres."* Each nudge persists and applies live.
- [ ] **Look-to-place:** turn to face a spot, say *"pin the HUD here"* → the panel **swings to your gaze**, centred, keeping distance/width. Turn elsewhere and pin again to confirm it follows your heading. *"Reset the HUD position"* returns it to straight-ahead defaults. (With the VR overlay **off**, *"pin the HUD here"* now **turns it on and places it** in one step — see §5b.1.)
- [ ] **Look-to-place captures pitch too (#107/#142):** look **down** at the dash, *"pin the HUD here"* → the panel **drops to your gaze** and its top **leans toward you** so it reads head-on (not edge-on). Look **up** and pin → it rises and tilts the other way. Looking level is unchanged. Look nearly straight down and pin → it **clamps** (~60° tilt, ≤2 m down), no glitch. (The tilt-direction is the 🖐 confirmation for #142 — see §5b.1.)
- [ ] **Pinned pitch/height survive a settings change (#107):** after a down-pin, say *"move it left"* (or toggle the HUD off/on) → the panel **stays at the pinned height and tilt**, it doesn't snap back to eye level — proving pin persists the full placement, not just heading.
- [ ] **Tilt:** place it low (`vr_offset_y_m = -0.3`), then *"set the VR HUD tilt to 25"* → the panel's **top leans toward you**, angling up to face you; `0` is dead vertical.
- [ ] **Curvature:** *"set the VR HUD curvature to 0.0"* (flat) → *"…to 0.1"* → the panel **wraps gently** like ED's/Virtual Desktop's screens; `1.0` would be a full cylinder (too much). Pick a comfortable curve.
- [ ] **Size:** adjust `[hud].vr_width_m` (e.g. `0.4` vs `0.8`) → the panel's physical width changes **live**; pick a comfortable, legible size.
- [ ] **Placement — world vs head:** with `[hud].vr_placement = "world"` the panel stays **cockpit-fixed** as you turn your head; switch to `"head"` and it **follows your view**. (Mode change may re-create the overlay; the position/tilt/curve settings above do not.)
- [ ] **Both surfaces at once:** enable **both** `[hud].enabled` and `[hud].vr_enabled` → the desktop window and the headset overlay show simultaneously and independently.
- [ ] **Quest boundary (if applicable):** confirm the overlay shows on **Quest via SteamVR** (Link/Air Link/Virtual Desktop-SteamVR), and does **not** on **OpenComposite / VDXR / Virtual Desktop** (expected — no SteamVR compositor there; use the **web HUD** in §5c for those, *not* OVR Toolkit, which is itself SteamVR-only).

### 5b.1 VR-HUD placement-model fixes (#140–#144)  🥽 VR 🎮 ED
> The §3.8.1 placement model landing as five fixes. Most are code-provable (green in `pytest`); the two 🖐 items below are **direction/visual** and need Doug's rig **switched into SteamVR mode** (Valve DLL + Virtual Desktop's SteamVR mode) — the overlay can't be dogfooded in his normal OpenComposite/VDXR setup (use the §5c web HUD there).

**#140 — late-SteamVR recovery + the spoken-reason matrix.** With `openvr` bundled (so the binding imports), run each row and confirm the **specific spoken line**, not a generic "isn't running":

- [ ] **Late SteamVR, no restart:** start COVAS++ with SteamVR **closed** and `[hud].vr_enabled = true`; then start **SteamVR + ED-in-VR**; then say *"turn the VR HUD on"* (or *"pin the HUD here"*) → the overlay **comes up with no COVAS++ restart** (the old one-shot latch no longer locks it out).
- [ ] **VR HUD off → "pin here" enables and places:** with `[hud].vr_enabled = false` but SteamVR up, say *"pin the HUD here"* → it **turns the VR HUD on and pins** in one step (no separate "turn the VR HUD on" first).
- [ ] **Reason: SteamVR not running:** SteamVR **closed**, say *"pin the HUD here"* → spoken line says **SteamVR isn't running** and **points at the web HUD** for OpenComposite/VDXR (not a generic failure).
- [ ] **Reason: no headset pose:** overlay up but headset **set down / pose not tracking**, *"pin the HUD here"* → **"couldn't read your headset position — try again"** (distinct from "not running").
- [ ] **Reason: component missing (frozen build only):** a build without the bundled binding → **"the VR overlay component isn't installed"** (the permanent case; distinct from the transient SteamVR one).
- [ ] 🖐 **#142 tilt direction (visual, SteamVR mode):** look **down** and *"pin the HUD here"* → panel low **and top toward you** (head-on), NOT tipped away. Then *"tilt the HUD up"* leans the top **toward** you and *"tilt down"* **away** — matching the words. (If a low pin or "tilt up" leans the top *away*, the shared pitch sign regressed.)
- [ ] **#143 no fabricated confirmation:** ask *"what's the tilt right now?"* (reads the real value), then complain *"it's tilted the wrong way, tilt it back up at me"* → COVAS **actually calls the tool and the panel moves**; it must NOT say "corrected/done" with no movement. If it can't act, it says so — never a false confirmation.
- [ ] 🖐 **#144 recentre & offset (visual, SteamVR mode):** with a **world-locked** panel, turn your head so it sits off to the side (offset still reads `0.0`) → *"recentre the HUD on me"* snaps it **back in front**, keeping distance/height/tilt/size. Separately, change **`vr_offset_x_m` 0 → 1.0** on the Settings page → the shown overlay **slides** (view-relative), confirming live-apply; the `hud` log shows a `placement -> …` line each apply.

## 5c. Web HUD via OpenKneeboard (issue #103 — `[hud].web_enabled`)  🥽 VR 🎮 ED
> The **same** four-row HUD as §5a/§5b served as a **transparent web page** at `/hud` for OpenKneeboard's Web Dashboard tab, so it composites in-headset on **OpenComposite / VDXR / Virtual Desktop** where the SteamVR overlay structurally can't. **Off by default**, **independent** of the 2D/VR HUDs, and **requires the control panel** (`run_covas_ui.py`). Beats the community EDCoPilot route (OpenKneeboard *window-capturing* an opaque app) on one axis: a **natively transparent** page — no black box. The in-headset checks need Doug's Quest 3 + OpenComposite/VDXR rig and cannot run in CI.
- [ ] **Headless is fail-soft (no headset):** with `run_covas.py` (headless, no control panel) and `[hud].web_enabled = true`, enabling the web HUD **logs that the control panel is required** and continues — no crash, no `/hud` served.
- [ ] **Page renders offline (no headset):** with `run_covas_ui.py` running, open `http://127.0.0.1:8765/hud` in a normal browser → with the web HUD **on**, the four rows show live data (transparent background); with it **off**, the page is **empty**. View source: it references **no external URL** (self-contained).
- [ ] **In-headset composite (Doug's rig):** ED in OpenComposite mode (`Toggle-VR-2D-Steam.ps1 status` → `vr`); OpenKneeboard installed with a **Web Dashboard** tab → `http://127.0.0.1:8765/hud`; `run_covas_ui.py`; say *"turn the web HUD on"*; launch ED via Virtual Desktop (VDXR). Confirm the panel composites over the cockpit with a **transparent background** (no opaque rectangle — the EDCoPilot-beating claim) and legible text.
- [ ] **Live data + toggle (in-headset):** voice state tracks Listening/Thinking/Speaking; plot a route → jumps-remaining updates each jump. Say *"turn the web HUD off"* → the panel **blanks** (no OpenKneeboard interaction); *"on"* → it returns.
- [ ] **Streaming-perf check (only this rig can answer):** compare frametimes/encode with the OpenKneeboard tab **present vs. absent** over Virtual Desktop. If the overhead is meaningful, note it — it reshapes the recommendation.

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

### 5.4a Materials inventory (#132)  🎮 ED
> Requires `[elite].enabled = true`, same live `Materials` inventory as §5.4 — launch ED (any
> ship) before testing. Cross-check counts against your in-game Right Panel → Inventory.
- [ ] **Single-material count:** *"How many arsenic do I have?"* → the exact count, its grade, and
      its cap; matches the in-game figure.
- [ ] **Fuzzy naming:** ask for a material by a shortened/spoken name (e.g. *"wake solutions"* for
      "Strange Wake Solutions") → resolves to the right material, not a different one.
- [ ] **Bucket listing:** *"List my raw materials."* → only materials you're actually **holding**
      are named (no zero-count ones recited), kept short.
- [ ] **Near-cap filter:** *"What raw materials am I near-capped on?"* → same listing, narrowed to
      ones at/close to their grade cap only (or an honest "nothing near-capped" if none qualify).
- [ ] **What am I capped on:** *"What am I capped on?"* → materials at or close to their grade cap
      across raw/manufactured/encoded; cross-check one against the in-game "MAX" indicator.
- [ ] **Unrecognized material:** ask about a made-up material name → says it doesn't recognize it,
      never invents a count.
- [ ] **Honest when blind:** with ED not yet loaded (no `Materials` seen), any of the above says it
      hasn't read your materials yet — no count, listing, or cap claim.

Notes:

## 6. Ship controls — keybinds, auto-honk & comms  🎮 ED ⌨️ INJECT 🔊 HW
> These send **real keypresses** into ED. Keybinds/auto-honk need `[elite].enabled = true` (combat guard) — do them **parked/docked and safe**. Comms send (§6.4) needs no ED monitoring but is **outward-facing**, so test it in a quiet/solo instance.

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
- [ ] **Abort interrupts a hold promptly (#159):** during the pad-clearing **hold** step, say *"abort"* → the held thrust key releases **the moment you abort** and the sequence ends — it does **not** keep holding for the rest of the hold duration before reacting.
- [ ] **Mode gating:** **on foot** or **in the SRV**, `launch` isn't offered and is refused ("only works in your ship").
- [ ] **Combat guard:** in **danger/interdiction** (or with `[elite]` off) arming/confirming `launch` is **refused**.
- [ ] **Off by default:** with the default allowlist (`landing_gear` only), *"launch"* is **not** offered and is refused — the sequence ships opt-in.

### 6.1d Focus the Elite window (#105 — `[keybinds].enabled = true`)  🎮 ED ⌨️ INJECT 🔊 HW
> Makes injection deterministic by bringing ED to the foreground. The explicit *"focus Elite"* command is always available (no allowlist/mode/combat gate). Auto-focus (`[keybinds].focus_before_inject`, **on by default**) pulls ED forward right before a keybind macro or a comms send.
- [ ] **Explicit focus:** **alt-tab** from ED to a browser, then *"COVAS, set focus on Elite."* → the **ED window comes to the front**. Log: `focused ED window`.
- [ ] **Auto-focus before a keybind:** with `focus_before_inject = true`, alt-tab to a browser, then arm+confirm *"toggle landing gear"* → ED is **foregrounded first** and the gear toggles **in the game** (the key does not land in the browser).
- [ ] **Restores when minimised:** **minimise** ED, then *"focus the game"* → ED **restores** and comes to the front.
- [ ] **Not running → fail soft:** with ED **closed**, *"focus Elite"* → COVAS says it **can't find the Elite window — is the game running?** No crash, and it doesn't claim success.
- [ ] **Auto-focus off:** set `[keybinds].focus_before_inject = false`, alt-tab away, arm+confirm landing gear → focus is **not** changed (the keypress goes to whatever window has focus, the old behaviour). The explicit *"focus Elite"* command still works.
- [ ] **Comms send focuses too:** with comms on (§6.4) and `focus_before_inject = true`, alt-tab away, compose+confirm a local message → ED is **foregrounded before the paste** so the message lands in ED chat, not the other window.
- [ ] **VR rig (VDXR) 🥽:** with ED running in-headset via Virtual Desktop/OpenComposite, repeat the *explicit focus* and *auto-focus before a keybind* checks and confirm **desktop foreground** behaves — i.e. VD streaming doesn't defeat `SetForegroundWindow`. This is the one path only the real rig can settle.

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

### 6.3 Tier-2 combat reflex — fire chaff (#36 — `[reflex].enabled = true`, allowlist `chaff`)
> The **inverse** of §6.1: reflexes fire ONLY while you're in danger. Chaff is purely **defensive**, so firing it is always safe — you never shoot at anyone. Set `[reflex].enabled = true` and `[reflex].allowlist = ["chaff"]`, keep `[reflex].combat_guard = true`, and bind your **chaff launcher** to a **key** in ED (a HOTAS/mouse-only bind can't be pressed). Requires `[elite].enabled`.
- [ ] **Startup readiness:** launch reports `Reflex: chaff -> <key>` (or `chaff UNUSABLE (no keyboard bind for FireChaffLauncher)` if it's not bound to a key), and a `Tier-2 combat reflexes ON …` line. (Add `heat_sink` to the allowlist to also see `Reflex: heat_sink -> <key>` and say *"heat sink!"* to deploy one under fire — same guard as chaff.)
- [ ] **Refused when safe (fully combat-SAFE test — do this parked/docked):** say *"chaff"* while NOT in danger → COVAS **refuses** ("you're not in combat…") and **nothing is pressed**. This is the safe way to prove the guard without a fight.
- [ ] **Refused with monitoring off:** set `[elite].enabled = false`, say *"chaff"* → refused ("can't confirm you're in danger — … status isn't available"); nothing fires.
- [ ] **Fires under fire (defensive — safe):** let a **weak NPC interdict** you (or take fire from a low-threat hostile), say *"chaff"* → it presses your chaff key **once**, log `fired chaff -> <key>`, and chaff deploys. Because chaff is defensive, this is safe even mid-combat.
- [ ] **Not offered unless allowlisted:** remove `chaff` from `[reflex].allowlist` (leave enabled), ask for chaff → it's neither advertised nor run.
- [ ] **Hard abort:** say *"abort"* → releases every held key (shared with keybinds/honk). Log: `aborted — released all keys`.
- [ ] **Tier-1 unaffected:** with reflexes on, confirm §6.1 landing gear still behaves exactly as before — it still **refuses in combat** (the two policies are independent).
- [ ] **Disabled:** set `[reflex].enabled = false` → no chaff tool offered; asking for chaff does nothing.

### 6.3.1 Tier-2 reflex FAST PATH — second push-to-talk phrase-spotter (#38 — `[reflex].ptt`)
> A **local** hotword path for snap combat calls: a capture on the second PTT is matched against a fixed vocabulary and, on a hit, fires the reflex **without the LLM** (latency ≈ speech-to-text only), through the **same** guard/allowlist/abort as §6.3. Set `[reflex].enabled = true`, `[reflex].allowlist = ["chaff"]`, and bind `[reflex].ptt` to a **DIFFERENT** key than `[keys].push_to_talk` (e.g. `"]"` or a spare HOTAS button via JoyToKey). Requires `[elite].enabled`.
- [ ] **Startup readiness:** launch reports the reflex scancodes on the `(PTT scan codes … reflex …)` line, and the banner's `Reflexes` line shows `(fast-PTT [<key>])`.
- [ ] **Instant fire under fire (defensive — safe):** while a **weak NPC** is interdicting/shooting you, tap the **reflex** key and say *"chaff!"* → chaff deploys **noticeably faster than the assistant path** (no "thinking"), log `phrase-spot fired chaff …`. Because chaff is defensive this is safe mid-combat.
- [ ] **Same guard when safe:** parked/docked (not in danger), tap the reflex key and say *"chaff"* → **refused** ("you're not in combat…"), nothing pressed — the fast path is faster, not looser.
- [ ] **Synonyms:** on the reflex key, *"flares"* / *"break lock"* also map to chaff (fire under fire to confirm; refused when safe).
- [ ] **Snap abort:** on the reflex key, say *"abort"* (or *"stop"*/*"release"*) → releases every held key immediately (shared abort). Log: `aborted — released all keys`.
- [ ] **Falls through to a normal turn:** on the reflex key, say a **non-combat** request (*"what's my fuel level?"*) → it is **not** treated as a reflex; it runs as an ordinary conversation turn and COVAS answers.
- [ ] **Main PTT untouched:** the normal `[keys].push_to_talk` key still opens a normal conversation turn exactly as before; the two keys don't interfere.
- [ ] **Disabled by default:** clear `[reflex].ptt` (blank) → no second hook; only the main PTT works.

### 6.3.2 Tier-2 ambient auto-reflexes (#37 — `[reflex.auto].enabled = true`, per-reflex enable)
> The **automatic** (no-voice, no-key) version of §6.3: the SAME reflexes fire the instant your ED status crosses a threshold — no command. Same combat-permissive guard, same shared abort. Set `[reflex].enabled = true` and `[reflex.auto].enabled = true`, then opt a reflex in: `[reflex.auto.heat_sink].enabled = true` and/or `[reflex.auto.chaff].enabled = true`. Keep `[reflex].combat_guard = true`. Bind **DeployHeatSink** and/or **FireChaffLauncher** to **keys** in ED. Requires `[elite].enabled`. **These fire real keypresses with no prompt — test the "fires" cases against weak NPCs only.**
- [ ] **Startup readiness:** launch reports the banner line `Reflexes : ON (auto ON)` and, per enabled reflex, `Auto-reflex: heat_sink -> <key>` / `chaff -> <key>` (or `… UNUSABLE (no keyboard bind for DeployHeatSink/FireChaffLauncher)`).
- [ ] **Auto chaff under fire (defensive — safe):** with auto-chaff on, let a **weak NPC interdict** you → chaff fires **automatically once** shortly after the danger/interdiction begins. Log: `auto-chaff on EnteredDanger|Interdicted: Chaff away …`.
- [ ] **Cooldown holds the repeat:** stay in the fight past the danger onset → it does **not** re-fire until the `[reflex.auto.chaff].cooldown` (default 20s) elapses. Log shows `chaff suppressed: chaff cooldown (20s)` for held attempts.
- [ ] **Auto heat sink on overheat (in combat):** while in danger, push your ship over **100% heat** (e.g. hard boosting/weapons in a fight) → a heat sink deploys automatically. Log: `auto-heat_sink on Overheating: Heat sink deployed …`.
- [ ] **Guard blocks when safe:** overheat while **NOT** in danger (e.g. flying too close to a star, parked) with `combat_guard = true` → it **refuses** and nothing fires (log `auto-heat_sink … refused: you're not in combat …`). Then set `[reflex].combat_guard = false`, repeat → it **does** deploy on overheat regardless of danger (the escape hatch).
- [ ] **Disabled reflex stays quiet:** turn `[reflex.auto.chaff].enabled = false` (leave heat_sink on) → interdiction fires **no** chaff; overheat still deploys a heat sink.
- [ ] **Hard abort:** say *"abort"* mid-reaction → releases every held key (shared with keybinds/honk/verbal reflexes).
- [ ] **Master off:** set `[reflex.auto].enabled = false` → nothing auto-fires (verbal §6.3 still works if allowlisted).

Notes (reliability quirks — probe / detect-window timing `_PROBE_SECONDS` / `_DETECT_WINDOW`, the Exit-Mode bind):

### 6.4 Send in-game messages by voice (#49 — `[comms_send].enabled = true`)  ⌨️ INJECT
> **Outward-facing — other Commanders SEE the message.** COVAS composes ED chat from what you say, **reads it back**, and sends only on a **separate** confirm. Set `[comms_send].enabled = true` and bind **Quick Comms Panel** to a **key** in ED (a HOTAS/mouse-only bind can't be pressed). No ED monitoring needed — the read-back is the safety, not a combat guard. Do your first tests **in a quiet/solo instance** so a slip doesn't spam a populated one. Per-channel switching is optional: leave `channel_*` blank to send on your currently-selected channel, or set the ED tokens if you've bound channel-switch keys.
- [ ] **Startup readiness:** launch reports `Comms: open box QuickCommsPanel -> <key>` (or `Comms UNUSABLE (bind QuickCommsPanel …)`), and a `Comms send ON (read-back-before-send confirmation required).` line.
- [ ] **Compose reads back, does NOT send:** *"Tell local o7."* → COVAS says it's *ready to send to local/system chat: "o7"* and asks you to confirm. **Nothing is typed into ED yet.**
- [ ] **Send on a separate turn:** then say *"Send it"* (or "confirm") → the comms box opens, **"o7"** is pasted, and the message sends on your current/local channel. Log: `sent comms to local: 'o7'`.
- [ ] **No same-turn send:** confirm in the SAME breath as composing → refused ("that isn't a separate confirmation…"); nothing sends. (The model can't compose-and-send in one turn.)
- [ ] **Cancel:** compose a message, then *"cancel"* → *"Discarded that message"*; a later "confirm" finds nothing armed.
- [ ] **Reword:** compose, then compose a DIFFERENT message before confirming → only the **latest** is sent.
- [ ] **Longer message + channel:** *"Message my wing: forming up at the nav beacon."* → reads it back as wing chat; confirm → it sends. (If you set `channel_wing`, verify it switches to the wing tab first.)
- [ ] **Multi-line / dictation artefacts:** a message that transcribes with a line break sends as a **single line** (no early send).
- [ ] **Unbound open key:** with **Quick Comms Panel** NOT bound to a key, ask to send → spoken *"bind QuickCommsPanel to a key…"*; nothing sends.
- [ ] **Channel binds on the Settings page (issue #129):** Settings → **Comms** shows **Local / Wing / Squadron / Direct-message chat bind** fields (plus **Comms settle delay**). Set **Squadron chat bind** to your ED squadron-chat action token and SAVE → *"message my squadron: forming up"* → read-back → *"send it"* lands in **squadron** chat, no restart. Also voice-settable: *"set the squadron chat bind to &lt;token&gt;"*. Leaving a field blank sends on the current channel (unchanged).
- [ ] **Configured-but-unbound channel key:** set `channel_wing` to a token you haven't bound → asking to message the wing is refused with a *"bind it in-game"* message; nothing sends.
- [ ] **Expiry:** compose, wait past `confirm_window` (default 60 s), say "confirm" → *"that message expired for safety"*; nothing sends.
- [ ] **Hard abort:** say *"abort"* → releases any held key (shared executor with keybinds/honk).
- [ ] **Disabled:** set `[comms_send].enabled = false` → no send/confirm/cancel tools offered; asking to message someone does nothing.

### 6.5 Custom macros — author your own (#50 — `[macros].enabled = true`)
> The headline feature: **you** compose named macros by voice or in the control panel; they run through the same executor + guards as §6.1. Set `[macros].enabled = true` and `[keybinds].enabled = true` with an allowlist that includes the actions your macros use (e.g. `[keybinds].allowlist = ["landing_gear", "throttle_zero"]`), bind those controls to **keys** in ED, and keep `[elite].enabled` on (combat guard + triggers). Startup log shows `Custom macros ON (N saved, M triggered; …)`. Do first tests **parked/docked**.
- [ ] **Author by voice:** *"Create a macro called gear up: retract the landing gear."* → COVAS confirms it saved the macro (and, since landing gear is consequential, that it'll ask you to confirm). It appears in the `/macros` panel and survives a restart.
- [ ] **Anti-hallucination refusal (the point):** *"Create a macro that ejects all cargo and self-destructs."* → **refused**, nothing saved — those actions aren't available. Ask for a real action you did **not** allowlist (e.g. `supercruise`) → refused, telling you to allowlist it. COVAS never invents an action.
- [ ] **Run by name (benign):** with a benign macro (e.g. steps = throttle to zero), *"run \<name\>"* → it fires **immediately** and reports success; the throttle drops in-game.
- [ ] **Run by name (consequential) — arm/confirm:** *"run gear up"* → says **armed, not done**; same-turn *"confirm"* is refused; a separate *"confirm"* runs it and the gear moves.
- [ ] **Trigger (benign):** author *"when I dock, throttle to zero"* (benign). Dock → it auto-runs once and speaks the outcome (the doubled journal/Status `Docked` does **not** run it twice).
- [ ] **Trigger (consequential) — arms + asks:** author *"when docking is granted, drop the gear"* (consequential). Get docking granted → COVAS **speaks a prompt** and arms it; it does **not** move the gear until you say *"confirm"*.
- [ ] **Two macros, one trigger — neither dropped (#159):** author **two** consequential macros on the **same** trigger (e.g. `docked`): one drops the gear, one sets throttle to zero. Dock → COVAS **announces both** (arms the first, says it **queued** the second). Say *"confirm"* → the first runs; COVAS then offers the **queued** one → *"confirm"* again → the second runs. Neither is silently dropped. Saying *"abort"* while both are pending clears **both**.
- [ ] **Combat guard:** in **danger/interdiction** (or with `[elite]` off) running/confirming any macro is **refused**; nothing fires.
- [ ] **Cross-mode rejected at authoring:** try to author a macro mixing a ship action and an on-foot action → **refused** ("mixes actions from different game modes").
- [ ] **Unbound key:** if a macro's action isn't bound to a **key** in ED → running it reports "bind it in-game" and nothing fires.
- [ ] **Hard abort:** with a macro armed (or mid-run), say *"abort"* → clears the pending macro and releases every held key (shared with §6.1/§6.3).
- [ ] **Concurrent abort isn't defeated (#154):** start a **multi-step keybind sequence** (e.g. `launch`) and, while it's mid-run, say *"abort"* at the same moment a **triggered custom macro** fires (e.g. a benign `on-dock` macro, or manually *"run \<benign macro\>"* in the same breath). The sequence must **stop and stay stopped** — its remaining steps do **not** fire after `release_all`, and the concurrently-starting macro does **not** wipe the abort. (Before the #154 fix, the macro's start could clear the sequence's just-set abort and let it re-press its remaining keys.)
- [ ] **Panel authoring + delete:** open **🎛 macros**, build a macro with the step editor (dropdowns only offer allowlisted actions / known triggers), SAVE → it appears in voice too; DELETE removes it. An out-of-allowlist action can't be picked, and the server rejects a hand-crafted bad request.
- [ ] **Disabled:** set `[macros].enabled = false` → no macro tools offered; *"run \<name\>"* does nothing.

Notes:

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

### 7.2 Ship specifications — grounded specs, newest hulls (#83)  🔊 HW
> **Always on** — no config, no ED monitoring, no network. Grounds ship-SPEC answers in a
> bundled dataset so newer hulls don't hallucinate. Cross-check figures against
> [EDSY](https://edsy.org/) or Coriolis.
- [ ] **Newer hull, real numbers:** *"How much cargo can a Type-8 carry?"* → a concrete figure (≈406 t), not a hedge or a wrong guess. Repeat for *"what pad does a Mandalay need?"* (medium) and *"how many hardpoints has the Corsair?"*.
- [ ] **Panther Clipper Mk II:** *"What are the specs on a Panther Clipper?"* → large pad, Zorgon Peterson, big cargo — the model does **not** claim ignorance of it.
- [ ] **Nickname + family:** *"Specs on a conda"* → Anaconda; *"tell me about the cobra"* → asks **MkIII / MkIV / MkV**, doesn't guess.
- [ ] **No invented jump range:** *"What's the jump range of a Python Mk II?"* → it does **not** state a hull figure; it points to your loadout (for your own ship) or web search, per the guardrail.
- [ ] **Won't confabulate:** ask about a hull with no bundled data (*"specs on a Lynx Highliner"*) → it says it has no data and offers to web-search, instead of making numbers up.
- [ ] **Guardrail holds with personality OFF:** with `[personality].enabled = false`, ask a ship-spec question → it still calls the tool / refuses to invent (the guardrail is always in the system prompt).

Notes:

### 7.3 Game-data freshness & new-content refresh (#101)  🔊 HW 🌍 NET (dev tool)
> **Always on** — no config. The honest companion to 7.2: reports how current the bundled
> ship/module/engineering data is, and how a new FDev hull enters the app as a **data update, not
> a code edit**. The refresh commands need network; the voice question does not.
- [ ] **"How up to date is your ship data?"** → COVAS++ names its datasets (ship specs, modules, engineering, roster), each with a source and a *generation date*, and says it'll web-search rather than guess for anything newer. It does **not** invent a version or claim to be perfectly current.
- [ ] **Startup freshness:** run `.venv\Scripts\python.exe check_setup.py` → a **Game data freshness** section lists each dataset's age; datasets older than ~6 months are flagged `[warn]` with a hint to run `scripts\refresh_datasets.py`.
- [ ] **Refresh diff (dev):** run `.venv\Scripts\python.exe scripts\refresh_datasets.py` → it fetches, regenerates, and prints a **diff summary** (new hulls / modules / blueprints, orphaned overlay rows, hulls with no bundled spec) plus a *last refreshed* nag for the hand-curated engineer tables. Re-running with no game change shows "no change" everywhere. Then `pytest` is green.
- [ ] **New-hull detection is loud (dev):** the mechanism to verify when Frontier ships a hull — after a refresh that pulls a coriolis ship file with no roster id yet, `scripts\gen_ship_specs.py` **fails loudly naming the ship** ("new FDev hull?") instead of silently dropping it; running `scripts\gen_ship_roster.py --fetch` first harvests its name/symbol so the next spec regen matches it with no hand edits.
- [ ] **Offline determinism:** `.venv\Scripts\python.exe scripts\refresh_datasets.py --no-fetch` regenerates from the committed snapshots with **no network** and leaves the generated files byte-identical (`git status` clean apart from the manifest date).

Notes:

### 7.4 "Match Current Ship Size" landing pad (#117)  🎮 ED 🔊 HW 📋 clipboard 🌍 NET
> `[nav].enabled = true`; set `nav.default_pad_size = match` (Settings → Navigation & search →
> Default landing pad → **Match Current Ship Size**). Applies to both the outfitting (§7) and
> ship (§7.1) searches — same `[nav]` config.
- [ ] **Small/medium ship widens the search:** while flying a **Courier or Python**, ask *"find the closest fuel scoop"* → nearby **outposts** (medium/small pads) are now eligible, not just Large-pad starports.
- [ ] **Large ship unchanged:** switch to a **Large** ship (Anaconda/Cutter/Corvette) → the same search behaves as the old fixed `L` default (Large-pad stations only).
- [ ] **Fallback to Large before any Loadout:** a **fresh session** with ED not yet having emitted a `Loadout`/`LoadGame` event (or ED not running) → the search still runs, filtered for a **Large** pad (never "any") — it should never suggest a station your ship can't use.
- [ ] **One-off override:** with the default pad size set to something else (e.g. `L`), say *"find the closest multi-cannon for my current ship"* → that ONE search resolves to your current ship's pad size without changing the `nav.default_pad_size` setting.
- [ ] **Voice-settable:** *"set default landing pad to match my ship"* → confirms it's set to match; *"what's my default landing pad?"* → reports it.

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

### 8.7 Spec-driven family — collapse smoke (#111)
> The six "thin" search categories became instances of one spec-driven generic capability (`covas/capabilities/search_family.py`), with the LLM- and help-facing surface **frozen byte-for-byte** (guarded by `tests/test_search_family_snapshot.py`). This is a purely internal refactor — the spoken experience must be **identical**. Enable `[star_systems]`, `[search]`, and `[bodies]`, then run one search per collapsed category and confirm each still answers in the **same shape** as before (a nearest result + distance, with the system copied to the clipboard). No deep per-category retest: behaviour is snapshot-frozen.
- [ ] **Star systems:** *"Find the nearest Empire system with high security."* → nearest matching system + distance, copied.
- [ ] **Stations:** *"Find the nearest station with a shipyard and a large pad."* → nearest station/system + pad + distance, copied.
- [ ] **Minor factions:** *"Where's the nearest system the Dark Wheel is present?"* → nearest system with that faction, copied.
- [ ] **Signals / structures:** *"Find the nearest megaship."* → nearest structure of that type + system + distance, copied.
- [ ] **Faction states:** *"Find the nearest system at war."* → nearest system by controlling-faction state, copied.
- [ ] **Bodies:** *"Find the nearest Earth-like world."* → nearest body + system + distance (+ arrival distance), copied.

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

### 9.1a Carrier Captain — Settings-UI name/voice + arrival/departure responses (issue #137)  🎮 ED 🔊 HW 🌐 PANEL
> Needs the control panel (`run_covas_ui.py`) + `[audio].enabled` + `[elite].enabled`, and you must
> own a fleet carrier. The Captain's arrival/departure lines are **guaranteed** at the transition.
- [ ] **Set name + voice in the UI:** open the control panel → **Settings → "Carrier voices"** group.
      Type a **Captain name** (e.g. "Reynolds"), pick a **Captain voice** from the 🔍 searchable
      dropdown (and set the matching **voice provider**), **Save**. Reopen the page → the values
      **persisted**. Do the same for **Tower Control**.
- [ ] **Applies live:** with the app running, change the Captain name/voice and Save → the **next**
      Captain line uses the new name and voice (no restart).
- [ ] **Arrival response:** supercruise to **your** carrier and **drop out of supercruise** at/near
      it → the **Captain welcomes you** (e.g. *"dropping in nicely — good to have you back,
      Commander"*), in the configured voice/name.
- [ ] **Departure response:** dock at your carrier, then **undock to leave** → the **Captain gives a
      send-off** (e.g. *"safe flying, Commander; we'll hold station"*).
- [ ] **No double-fire:** the arrival/departure line does **not** immediately double up with a second
      ambient Captain greeting in the same moment (short dedup).
- [ ] **Carrier voices off = silence:** set the **"Carrier voices"** master switch off (or say *"mute
      the carrier"*) → dropping in / undocking at your carrier produces **no** Captain line.
- [ ] **Not a normal station:** undock from a **normal station** that happens to be in your carrier's
      system → **no** send-off (only leaving the carrier itself triggers it).

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
## 9a-2. Owned-ships registry (issue #134)  🎮 ED 🔊 HW 📋 FILE
> `[elite].enabled = true`. The Commander's **owned** fleet, folded from the journal's ownership events + reconciled from Loadout / StoredShips, persisted git-ignored to `owned_ships.json`. Distinct from 9a (that's ships in *storage*; this is the *whole fleet* identity).
- [ ] **List the fleet:** *"What ships do I own?"* → reads back your ships, **flags the one you're flying**, and gives each one's last-known system. (Confirm it matches your in-game shipyard fleet list.)
- [ ] **Buy updates it:** buy a new ship in-game (`ShipyardBuy` → `ShipyardNew`) → ask again → the **new hull is listed and is the active ship**.
- [ ] **Switch updates active:** switch to another owned ship in the shipyard (`ShipyardSwap`) → *"which ship am I flying?"* → names the one you switched into.
- [ ] **Sell removes it:** sell a ship (`ShipyardSell`) → ask again → it's **gone** from the fleet.
- [ ] **Persists across restart:** quit COVAS++, relaunch, ask *"what ships do I own?"* **before docking** → the fleet is still there (loaded from `owned_ships.json`).
- [ ] **Voice add:** *"I bought a Python"* (or one you own that wasn't captured) → "Added Python to your fleet"; it appears in the list.
- [ ] **Voice remove + disambiguation:** *"remove the Cobra"* → removed; if you own **two** Cobras it **asks which one** rather than guessing.
- [ ] **Corrections survive:** add a ship (or rename one) by voice, then dock somewhere with a shipyard (fires `StoredShips` / `Loadout`) → your manual entry / custom name is **still there**, not clobbered.
- [ ] **Fail-soft before data:** ask *"what ships do I own?"* on a brand-new install with no journal history → it says it hasn't recorded any yet (no crash).
## 9a-3. Per-ship engineering planning (issue #135)  🎮 ED 🔊 HW 📋 FILE
> `[elite].enabled = true`. Each owned ship's **build** (modules + applied engineering) is remembered
> per journal ShipID and persisted git-ignored to `ship_loadouts.json`, so it survives ship switches
> and restarts. Planning is grounded on that remembered build crossed with your live materials (#66)
> and engineer unlock status (#65); the plan writes to the **checklist** via the existing CRUD. Board
> your ships at least once this session so their loadouts are captured.
- [ ] **Remembered build of the active ship:** *"What's the engineering on my ship?"* → lists your engineered modules (blueprint + grade) and which core modules are still stock. Cross-check against the in-game outfitting/engineering panel.
- [ ] **Config remembered per ship — survives a switch:** note the build of ship A, switch to ship B in the shipyard (fires a new `Loadout`), then ask *"what's the engineering on my <ship A>?"* (naming ship A, which you're **not** flying) → it recalls **ship A's** build, not ship B's.
- [ ] **Survives a restart:** quit COVAS++, relaunch, and **before boarding anything** ask *"what should I engineer next on my <ship>?"* → the remembered build is still there (loaded from `ship_loadouts.json`).
- [ ] **Grounded upgrade plan:** *"What's left to grade 5 my FSD?"* → reports the FSD's **current** grade (from memory), the material **shortfall** for grade 5 (matching your real inventory), and **which engineer** applies it with your unlock status. Confirm the shortfall against the in-game materials count.
- [ ] **No remembered build → honest:** ask about a ship COVAS++ hasn't seen a `Loadout` for this install → it says it has no remembered build for that ship and to board it, rather than inventing modules.
- [ ] **Stock module isn't guessed:** *"Plan grade 5 on my <a stock module>"* → it says the module is stock and **asks which blueprint** (offering real options), rather than assuming one.
- [ ] **Plan → checklist round-trip:** *"Add engineering my FSD to grade 5 to my checklist"* → it adds a checklist objective naming the module/grade/engineer/shortfall; *"what's next?"* shows it; *"mark engineering the FSD done"* completes it. Confirm the line appears/updates on the Checklist page too.
## 9a-4. Ship metrics — jump range & fleet ranking (issue #139)  🎮 ED 🔊 HW 📋 FILE
> `[elite].enabled = true`. Computes jump range from each ship's **remembered build** (#135) crossed
> with bundled FSD reference data, and ranks your **owned fleet** (#134). The current ship's figure
> uses **live** cargo & fuel; other ships are quoted at a reference load (full tank, empty cargo).
> Board your ships at least once this session so their loadouts are captured, and fly the ship you
> want a live figure for.
- [ ] **Current jump range matches the FSD panel:** in your current ship, *"what's my current jump range?"* → the spoken figure matches the in-game right-hand FSD/navigation panel's jump range for your **current** cargo, within a light-year or so. It also states the load basis (e.g. "laden — 32t fuel, 40t of cargo").
- [ ] **Cargo moves the figure:** note the current jump range, then **load or unload cargo** (buy/sell/collect) and ask again → the figure moves the right way (more cargo → shorter range; less → longer), tracking the panel.
- [ ] **Top three small ships:** *"top three small ships by jump range"* → ranks your **small-pad** hulls (that have a remembered build) by range, best first, and says it used a reference load. Cross-check the order against what you know of those ships' ranges (Coriolis/EDSY if you like).
- [ ] **Class filter is honored:** *"top three large ships by jump range"* → only large-pad ships appear; small/medium hulls are excluded.
- [ ] **Never-flown ship → unknown, not guessed:** own a ship you have **not** boarded since setup (so it has no remembered build) and ask *"rank my ships by jump range"* → that ship is reported as **unknown** ("no remembered build yet"), never given an invented number.
- [ ] **Named other ship at reference load:** *"what's the jump range of my &lt;a ship you're not flying&gt;?"* → a computed figure quoted at the reference load (full tank, empty cargo), since only the current ship's live cargo is known.
- [ ] **Unknown metric is declined:** *"rank my ships by shield strength"* → it says it doesn't compute that yet and names what it can do (jump range), rather than inventing a ranking. (Confirms the metric registry is honest about its one metric today.)
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

## 9b. On-foot (Odyssey) engineering (#73)  🎮 ED 🔊 HW 📋 clipboard
> `[elite].enabled = true`. Suit/weapon recipes, the modification catalogue and engineer
> locations come from a bundled offline table; unlock **status** for an engineer is joined live
> from the same `EngineerProgress` event (log into the game once this session). This is a **data
> + read** capability — it does not cross-reference your live material stock (planned follow-up).
- [ ] **Suit upgrade recipe:** *"How do I engineer my Maverick suit?"* → speaks its role, the grade-5 materials (12× Carbon Fibre Plating, 12× Graphene, plus the schematic/monitor/instructions), where to source them, and the suit mods you can add. Cross-check the numbers against the in-game Pioneer Supplies upgrade screen.
- [ ] **Explicit grade:** *"What do I need to upgrade my Dominator to grade 3?"* → the grade-3 counts (5× Titanium Plating, 5× Graphene, 2× each good), not grade 5.
- [ ] **Weapon:** *"Engineer my Manticore Oppressor."* → names the family (Manticore / plasma) and its materials (Chemical Superbase, Microelectrode, Ionised Gas…).
- [ ] **Modification → engineers:** *"Which engineer gives Greater Range?"* → lists the engineers who offer it (Domino Green, Wellington Beck, Rosa Dayette), each tagged with **your** unlock status from the journal.
- [ ] **Engineer locate + plot:** *"How do I unlock Domino Green?"* → her system (Orishis) and workshop (The Jackrabbit), the access + unlock task, who she refers you to (Kit Fowler), the mods she offers, and it copies **Orishis** to the clipboard (paste to confirm). Ask while in Orishis → says you're already there and does **NOT** copy.
- [ ] **Overview:** *"Give me the full on-foot engineering breakdown."* → the two-halves summary (grade upgrades vs modifications) and the 9-bubble / 4-Colonia engineer split.
- [ ] **Never guesses:** *"How do I engineer my flight suit?"* → says the Flight Suit isn't engineerable and names the real suits; a made-up modification is refused with real examples.

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

### 14.0 Version label (issue #78)
- [ ] A small, muted **`vX.Y.Z`** tag sits in the bottom-right corner of the panel (matches `__version__` / `check_setup.py`'s reported version) — visible but out of the way of every control. 🖥️ **Native window:** its title bar also reads **"COVAS++ vX.Y.Z"** in the packaged app (not the plain browser build).

### 14.0a Quick panel reflects the active LLM/TTS provider (issue #86)  🌐 PANEL 🌍 NET 📋 FILE
> The left **Configuration** card's **LLM** and **Speech** blocks must MIRROR `[llm].provider` /
> `[tts].provider`, rendered generically from the schema — not a hardcoded Anthropic/ElevenLabs panel.
> Change providers by editing `overrides.json` / the Settings page and **restart** between checks.
- [ ] **Anthropic + ElevenLabs** (`[llm].provider = "anthropic"`, `[tts].provider = "elevenlabs"`, EL key set):
      the **LLM** block header reads *"Anthropic (Claude)"* and shows a **Claude model** dropdown **and a
      Thinking depth** control; the **Speech** block reads *"ElevenLabs"* and shows **model + a searchable
      voice picker (filter box + 🔍) + a voice-speed slider**. Changing any of them speaks/persists as before.
- [ ] **Alternate LLM — e.g. Gemini or OpenAI-compatible** (`[llm].provider = "gemini"` / `"openai"`):
      the LLM header names that provider, shows an **editable model combobox** (pick from the live catalog
      with the provider key set, or type any id), and **no Thinking control** (Anthropic-only). For OpenAI,
      the **base URL** shows read-only. With no key/offline the combobox degrades to a plain text box keeping
      the current value (no error, no empty blocking control).
- [ ] **Alternate TTS — e.g. Edge** (`[tts].provider = "edge"`, no ElevenLabs key needed): the Speech header
      reads *"Edge (free)"* and shows an **Edge voice** combobox — NOT ElevenLabs fields. (Spot-check Azure /
      OpenAI / Cartesia / Piper similarly if configured — each shows only its own voice fields.)
- [ ] **`/api/elevenlabs` only when relevant:** with a **non-ElevenLabs** TTS active, open the browser
      devtools **Network** tab and reload the panel → there is **no request to `/api/elevenlabs`**. Switch
      `[tts].provider` back to elevenlabs, restart, reload → the request reappears (voice/model lists load).
- [ ] **Catalog dropdown throttle (#163):** with devtools **Network** open, open a fetched combobox
      (e.g. the OpenAI/Gemini **model** or an **Edge/Azure voice** list) → **one** `/api/catalog?source=…`
      request. Close and reopen it within a minute → **no** second request (served from the ~60s cache).
      Even opening it twice near-simultaneously fires the network fetch **once** (per-key in-flight guard).
- [ ] **Bad settings payload is a clean 400 (#163):** in a shell, `curl -s -o /dev/null -w "%{http_code}"
      -X POST http://127.0.0.1:8765/api/settings/update -H "Content-Type: application/json"
      -d '{"updates": [1,2,3]}'` → **400** (a non-object `updates` is rejected cleanly, not a 500).

### 14.1 Live status & log
- [ ] The status light tracks state as you talk; the log scrolls with prompts, replies, router/usage, status/search lines (timestamped).

### 14.1a Voice-list filter (issue #26 / #100)  🌐 PANEL 🌍 NET
> `requires:` **both voice dropdowns must be POPULATED via a valid ElevenLabs key** (set it on the
> Settings *API keys* card and restart; `tts.provider` doesn't need to be elevenlabs, but the list only
> loads with a key) — an empty list has nothing to filter and the test reads as "not implemented".
> Verify in BOTH the browser (`run_covas_ui.py`) AND the packaged native window.
>
> **#100 resolution (do NOT re-mark NYI on an empty list):** the filter code IS wired on both surfaces —
> `index.html` `#el_voice_filter` → `filterOptions(#el_voice)` and `settings.html` `voiceFilter(sel)` on
> the `@elevenlabs_voices` picker. The earlier `panel-voice-list-filter` NYI failure was a **populate
> artifact** (the voice dropdowns never loaded — no valid key / non-EL TTS active — so there was nothing
> to filter), not a code regression. If it fails again, first confirm the list actually populated. The
> inline box coexists with the richer command palette (§14.1d).
- [ ] The **ElevenLabs voice** picker on the **main panel** (below the dropdown) and the schema-driven
      picker on the **Settings** page (beside the dropdown) both show a filter box once the list loads.
- [ ] **Main panel:** type **3+ characters** in the filter box under **ElevenLabs voice** → the dropdown
      narrows to voices whose **name or category** contains the text (case-insensitive; try a category
      word like *"cloned"* or *"premium"*). Typing **1–2 chars** filters nothing; **clearing** the box
      restores the full list. The **currently-selected** voice stays visible even when it doesn't match.
- [ ] **Settings page:** same behavior in the filter box **next to** the schema `@elevenlabs_voices`
      picker — 3+ chars filters by substring, <3 clears. Picking a filtered voice still saves normally.

### 14.1d Command-palette voice/model search (issue #94)  🌐 PANEL 🌍 NET
> A reusable searchable palette (magnifier 🔍 button) beside the long voice/model pickers on BOTH the
> **main panel** (ElevenLabs voice) and the **Settings** page (ElevenLabs voice/model + the #92 model/
> voice comboboxes). `requires:` a populated list (valid ElevenLabs key for the voice palettes; the
> relevant provider key for a model palette). Verify in the browser AND the packaged native window.
- [ ] **Open + search:** click 🔍 beside **ElevenLabs voice** → a palette opens with a search box and the
      full list below. Type a few letters → results **filter live** and the matched substring is **bold**;
      each row shows the voice **category** as secondary text. Empty query lists **alphabetically**.
- [ ] **Keyboard-first:** **↑/↓** move the highlighted row, **Enter** selects it (applies + saves), **Esc**
      closes without changing. A mouse **click** also selects. The list **scrolls** for the long tail.
- [ ] **Current pick reachable (fail-soft, #26/#100):** the currently-selected voice is marked (✓). With
      the ElevenLabs key cleared/offline, 🔍 still opens the palette showing *"list unavailable — type a
      value and press Enter"* so the current pick is kept and a value can still be entered — never blocks.
- [ ] **Reused for model lists:** on the Settings page, the 🔍 beside a fetched **model** combobox
      (e.g. OpenAI/OpenRouter with a key) opens the same palette over the hundreds of model ids.

### 14.1b Voice/model dropdowns sorted alphabetically (issue #93)  🌐 PANEL 🌍 NET
> Both the **ElevenLabs voice** and **ElevenLabs model** dropdowns should list entries A→Z by
> display name, case-insensitive, regardless of the order the ElevenLabs API returns them in.
- [ ] **Main panel:** open the **ElevenLabs voice** dropdown → names read alphabetically
      (case-insensitive — e.g. a lowercase name like *"bella"* sorts with the *B*s, not after *Z*).
- [ ] **Settings page:** the schema-driven **ElevenLabs voice** (`@elevenlabs_voices`) and
      **ElevenLabs model** (`@elevenlabs_models`) pickers are both alphabetical too.
- [ ] **Selection preserved:** with a voice/model selected that happens to sort near the bottom
      (e.g. starts with *"Z"* or *"™"*), reload the Settings page → it's still the selected value
      (sorting is presentational only, never drops or changes the current selection).

### 14.1c Fetched-catalog dropdowns — editable comboboxes (issues #92 + #88)  🌐 PANEL 🌍 NET 📋 FILE
> On the **Settings** page the model-id and endpoint fields are editable comboboxes: a dropdown fed
> from the provider's LIVE catalog plus free-text for anything custom. `requires:` the relevant
> provider key/endpoint for the list to actually populate (OpenAI/Groq key for `openai.model`, Gemini
> key for `gemini.model`, Azure key+region for `azure.voice`,
> Cartesia key for `cartesia.voice`; Edge needs no key). Verify in BOTH the browser (`run_covas_ui.py`)
> and the packaged native window.
- [ ] **Base-URL presets:** the **OpenAI LLM base URL** field offers the four presets
      (OpenAI/Groq/DeepSeek/OpenRouter) in its dropdown; picking one fills the box. Typing a custom URL
      shows a **"custom (unsupported)"** flag but is accepted.
- [ ] **Model list populates:** with an OpenAI (or Groq/OpenRouter) key set, open **OpenAI LLM model**
      → the datalist lists that endpoint's models; the row footer shows a count. Change the **base URL**
      to another preset → the model list **refetches** for the new endpoint.
- [ ] **Gemini:** with a Gemini key, **Gemini model** lists Google's live models.
- [ ] **Edge/Azure/Cartesia voices:** **Edge voice** populates with no key; **Azure voice** populates
      once the Azure key + region are set; **Cartesia voice** once the Cartesia key is set.
- [ ] **Custom value accepted + flagged:** type a model/voice id NOT in the list → it's kept (flagged
      "custom (unsupported)"), saves to `overrides.json`, and is still the value on reload.
- [ ] **Fail-soft (no key / offline):** with the relevant key cleared or offline, the field still shows
      the **current value** and lets you type — the footer reads *"catalog unavailable (…) — type a
      value"*; never an empty or blocking dropdown, and the existing value is never lost.

### 14.1e One reusable voice picker everywhere (issue #120)  🌐 PANEL 🌍 NET
> Every voice field — provider voices, the Player-DM voice, the Piper voice, the crew per-character
> voice — renders through the SAME searchable control: a `<select>` (current value always visible) +
> the 🔍 command palette + the type-to-filter box. Verify in BOTH the browser and the native window.
- [ ] **Player-DM voice is searchable:** Settings → Ambient audio → **Player-DM voice** is a dropdown,
      not a bare text box. With an ElevenLabs key, the 🔍 palette lists your library voices; type to
      filter, pick one → saves. It sits **beside** a leading **"(random session voice)"** = blank.
- [ ] **Custom path / id accepted (allowCustom):** open its 🔍 palette, type a Piper `.onnx` path (or
      any unlisted id) → the **"custom"** entry appears at the top; pick it → it's saved and stays the
      selected value on reload. Clear it back to blank → random-per-session behavior returns.
- [ ] **Piper voice is searchable too:** set `[tts].provider = piper`, point **Piper voice** at a voice
      in a folder of `.onnx` files → the picker lists the **other `.onnx` voices in that folder**
      (each with its sibling `.onnx.json`); typing a custom path still works; an empty/missing folder
      degrades to type-a-path (no error).
- [ ] **Identical to a provider voice field:** compare the Player-DM voice side-by-side with the
      **ElevenLabs voice** field — same look and behavior (the ElevenLabs one just doesn't allow a
      custom id).
- [ ] **Crew page reuses the SAME control:** the **🎙 crew** page's per-character **Voice** is the same
      searchable picker (🔍 + filter), with **"Auto (deterministic)"** = blank; pick/search/type a
      custom voice, **SAVE ROSTER**, reload → the choice persists.

### 14.2 Settings page (N1) — http://127.0.0.1:8765/settings
- [ ] The page renders **grouped sections** with the **right control per type** (toggles, dropdowns, number/sliders, text/path) and inline help.
- [ ] **Frequency-first ordering (issue #80):** the grouped sections/cards are ordered **most-used first** — provider, voice, and speed near the top; rarely-touched advanced/dev options lower — so common controls are reachable without scrolling past niche settings.
- [ ] **Filter box (issue #7):** type 3+ chars → the list narrows to settings whose **section, title, or description** contains the text (case-insensitive); sections with no matches hide entirely. Typing **1–2 chars** filters nothing (everything stays shown); **clearing** the box restores the full list. Verify a **section-name-only** match (e.g. type a group name that isn't in any title/help) still surfaces that section's settings.
- [ ] **Change + save:** change a value → the **save bar** appears with a count; **SAVE CHANGES** → 📋 written to `overrides.json` (config.toml stays pristine).
- [ ] **Per-setting reset:** a changed (overridden) setting shows **RESET** → click it → reverts to default and drops from `overrides.json`.
- [ ] **Validation:** try an out-of-range number (e.g. voice speed 3.0, above the 2.0 max) → rejected client-side / server-side, not written.
- [ ] **Live where supported:** change the **Whisper model** → the log notes the model reloaded (no restart). Most settings now apply live — see §14.2a; only the `RESTART_REQUIRED` set (`audio.enabled`, `audio.mix_sample_rate`, `ui.host`/`ui.port`) needs a relaunch.
- [ ] **No Dev-mock setting in the UI (issue #130):** the Settings page has **no "Dev mock mode" row** and **no "Developer" group**; voice *"turn mock mode on"* does nothing. The mechanism still works out of band: launch with `[dev] mock = true` in `config.toml` (or `$env:COVAS_MOCK=1`) → the startup log prints `Dev mock ON …` and a turn runs with fakes (no API calls).

### 14.2a Settings apply LIVE — hot-swap providers & keys (issue #90)  🔊 HW 🌐 PANEL 🌍 NET
> #90 makes almost every Settings change take effect **without a restart**.
- [ ] **Switch the LLM provider live:** on the Settings page change **LLM provider** (e.g. anthropic → gemini, with that provider's key set) and **SAVE CHANGES**. The log shows `LLM now: <provider> / <model>`. Speak a turn → the **next** turn is answered by the new provider (check the router/usage line), **no restart**. A turn already in flight when you saved finishes on the old provider.
- [ ] **No Ollama, no GPU option (issue #128):** the **LLM provider** dropdown offers exactly **Anthropic / OpenAI-compatible / Gemini** — **no "Ollama"** entry, and there's no Ollama model field. The **Whisper device** row shows **cpu only** (no `cuda`), and its help mentions no GPU. Voice command *"turn on ollama"* / *"set the whisper device to cuda"* does nothing (not a valid setting/option).
- [ ] **Switch the TTS provider/voice live:** change **TTS provider** (e.g. edge → elevenlabs, or just a different voice) and SAVE → the log shows `Voice now: <provider>` and the next spoken reply uses the new voice. The mixer is **not** rebuilt.
- [ ] **Ambient audio follows the swap (issue #90 review):** with the **bus mixer + audio layer ON**, switch the TTS voice/provider and SAVE, then trigger an ambient/comms line (or a persona musing) → it speaks in the **new** voice, not the old one (no half-swap). Likewise, switching the **LLM** keeps opt-in chatter-flavor / comms-variants generating on the new provider (canned/verbatim lines are unaffected).
- [ ] **Failed switch is fail-soft:** switch to a provider whose key is missing/bad and SAVE → the log shows `Couldn't switch … keeping the previous one`, and the next turn **still works** on the previous provider (no dead loop).
- [ ] **Hotkeys live:** change the **push-to-talk key** (and/or cancel/reflex key) and SAVE → the log notes the hotkeys updated; the **new** key holds-to-talk immediately and the **old** key no longer does — **no restart, no re-hook**.
- [ ] **Mic change is safe mid-press (issue #90 review):** while **holding PTT and talking**, change the **input device** in Settings and SAVE → the log notes the change is **deferred** until the capture ends; the utterance you were speaking still transcribes (not dropped), and the **next** press uses the new mic.

### 14.2b Microphone picker on the Settings page (issue #89)  🔊 HW 🌐 PANEL
> Previously the mic could only be chosen in the first-run wizard; #89 adds it to the Settings page,
> riding the live mic-reconcile path from #90.
- [ ] **Picker present:** under *Voice input* on the Settings page there's a **Microphone** combobox listing your capture devices. It's **de-duplicated** — the truncated short-name copy of a device (often a **silent** MME clone, e.g. `Microphone (Logi 4K Stream Edit`) is dropped in favour of the **full-name** entry (`…Edition)`). Blank = the Windows default.
- [ ] **Pick + apply live:** choose a **different** mic and **SAVE CHANGES** → the log notes the recorder was rebuilt (no restart). Hold PTT and speak → the turn transcribes from the **newly selected** mic. Pick the full-name entry that used to be silent → capture now has audio.
- [ ] **Continuous mode too:** with `[listen].mode = continuous`, changing the mic restarts the VAD listener on the new device (a subsequent hands-free utterance is captured from it).
- [ ] **Survives a saved-but-absent device:** the combobox keeps a saved mic name even if that device isn't currently connected (it's an editable combobox — the value is never silently wiped); blank falls back to the default.

### 14.2c Settings search highlights the match (issue #95)  🌐 PANEL
> Rides the §14.2 filter box (#7): matched text in the filtered rows gets a **yellow background** so you can see *why* each row matched.
- [ ] Type **3+ chars** in the Settings search box → matching settings stay visible and the matched substring is wrapped in a **yellow highlight** within the setting's title/description.
- [ ] **Refine** the query → the highlight tracks the new match; a query matching only a **section name** still surfaces that section's settings.
- [ ] **Clear** the search box → all highlights are removed and the full list is restored.

### 14.2d Settings page left-aligned (issue #106)  🌐 PANEL
> The Settings body used to be centered under a full-bleed header, leaving a wide empty gutter on
> the left of a maximized window. #106 left-aligns it to match the control panel.
- [ ] **Maximized on a wide (≥1600px) monitor:** the Settings body (group nav + cards) sits **under
      the header logo**, hard against the left — **no** large empty gutter on the left.
- [ ] **Matches the control panel:** open the control panel (`/`) and the Settings page (`/settings`)
      side by side — both are left-aligned; Settings is no longer the odd one out.
- [ ] **Still readable / collapses gracefully:** setting rows stay readable (help text still capped);
      narrow the window below 1200px → the layout stays sensible and the **sticky group nav** still
      scrolls the page and stays usable.

### 14.2e Theme selector — Dark / Light / Elite (issue #104)  🌐 PANEL
> Settings → **Appearance** → **Theme** picks the control-panel palette. Dark is the default and
> must look exactly as before; Light and Elite must recolour **every** page fully.
- [ ] **Default is Dark, unchanged:** with a fresh config (or `ui.theme = "dark"`), the control panel
      looks identical to before this feature — no colour shifts anywhere.
- [ ] **Switch to Light (live, no reload):** Settings → Appearance → Theme → **light** → the page
      recolours **immediately** (no save needed to preview, no reload). Then **SAVE CHANGES**. Every
      page — index, settings, checklist, crew, macros, memory, the 🔍 command palette, and the
      first-run setup wizard — is fully light: light surfaces, **legible** dark text, and readable
      toggles / secondary buttons / the red CANCEL & danger buttons / the amber conflict banners. No
      orphaned dark patch.
- [ ] **Switch to Elite:** Theme → **elite** and SAVE → orange-on-near-black cockpit look; the accent
      matches the in-game / [companion HUD](docs/using/hud.md) orange (`#ff7100`).
- [ ] **No flash on load / navigation:** with Light or Elite saved, hard-reload each page and click
      between the header links → the chosen palette is present on **first paint** (no flash of Dark).
- [ ] **Persists across restart:** quit and relaunch → the panel opens in the saved theme.
- [ ] **By voice:** say *"switch to the light theme"* (or *"use the Elite Dangerous theme"*) → the
      setting changes; open/navigate to a page and it shows the new theme.
- [ ] **REVERT restores the saved theme:** change the Theme dropdown to preview a different look, then
      click **REVERT** (don't save) → the page snaps back to the saved theme.

### 14.2f Settings left-nav scrollspy (issue #119)  🌐 PANEL
> The left group nav highlights the section you're in — by scroll, and (while editing) by the focused
> control. Standard docs-site behaviour on a long page. JS-only; verify in the browser + native window.
- [ ] **Scroll tracks the section:** open Settings and scroll slowly top→bottom → **exactly one** nav
      entry is highlighted at a time (accent text + a 2px accent left-border + subtle bg, clearly
      distinct from hover), and it's the section currently at the **top of the content area** (just
      below the sticky header). The active entry carries `aria-current="location"`.
- [ ] **Focus overrides scroll:** without scrolling, **Tab** (or click) into a control several sections
      down → that control's section highlights immediately, overriding the scroll highlight; it stays
      until your next scroll, which hands the highlight back to scroll tracking.
- [ ] **Click jumps cleanly:** click a nav entry → it highlights **immediately** and the page scrolls
      to that section (landing below the sticky header), with no flicker through the passed sections.
- [ ] **Filter never highlights a hidden group:** type in **Filter settings…** to hide some sections →
      the hidden groups also drop from the nav, and the highlight only ever lands on a **visible**
      section (never a filtered-out one). Clear the filter → all nav entries return.
- [ ] **Long nav stays usable:** shrink the window so the 28-group nav overflows → the active entry
      scrolls into view in the nav as it changes.

### 14.3 Personality tab (N7)
- [ ] **Persona picker:** the Personality tab lists personas; selecting one shows a **preview**. Pick a different persona → the next reply's **voice/register changes**.
- [ ] **Campaign preserved:** switch persona and confirm your **Campaign** text (personal facts) is unchanged — switching voice never wipes it.
- [ ] **Save as custom:** edit the persona box → **SAVE AS CUSTOM** → a new custom persona appears in the list (written git-ignored under `personalities/custom/`).
- [ ] **Campaign editor:** edit the Campaign box → **SAVE CAMPAIGN** → a subsequent reply reflects the updated facts.

### 14.3b Auto-paired persona voices (issue #96 — ElevenLabs)  🔊 HW 🌍 NET
> requires: `[tts].provider = "elevenlabs"` with a valid key, `[personality].enabled = true`,
> `[personality].auto_voice_pairing = true`, optimization level `Full` (or a non-lean level), and a
> couple of distinct voices in your ElevenLabs library. First launch makes ONE cheap-tier call.
- [ ] **A fitting voice on selection:** on first launch, wait a few seconds (the pairing runs in the
  background), then pick a persona you've **never** set a voice for → the next reply speaks in a
  **paired voice that suits the character** (not always the same one configured voice). Confirm
  `personalities/voice_pairings.json` was written and is **git-ignored** (`git status` shows it
  untracked/ignored).
- [ ] **Startup is never blocked:** the app is fully usable **immediately** on launch (before the
  pairing lands) on the current default voice — no hang, no delay waiting on the pairing call.
- [ ] **Explicit override persists + wins:** set a voice for a persona yourself (Settings →
  Text-to-speech, or *"use the George voice"*) while that persona is active → re-select another
  persona and come back → **your** chosen voice returns, NOT the auto-paired one (it's remembered
  under `[personality].persona_voices` and never overwritten).
- [ ] **Cached — no call on the next launch:** restart with the same personas + voice library →
  there is **no** new pairing call (watch the cost/voice log); the cached `voice_pairings.json` is
  reused. Add/remove an ElevenLabs voice (or edit a persona) → the next launch **recomputes** once.
- [ ] **Fail-soft / gated:** with no ElevenLabs key (or offline, or `[tts].provider` set to
  Edge/Piper, or the optimization level set to `Lean`/lower, or `auto_voice_pairing = false`) → **no
  pairing happens**, the current default voice is kept, and nothing errors or leaves a persona
  voiceless.

### 14.3a Persona stays in character (issue #98)
- [ ] **Voice persists on a practical turn:** with a persona selected (personality ON), ask a plain lookup ("how far is Sol?") → the answer is accurate **and** unmistakably in that persona's voice, not a flat neutral sentence.
- [ ] **Can't-fly-the-ship, in character:** say *"retract the landing gear"* / *"boost"* / *"turn us to two-ninety."* → COVAS **declines in character** (never a flat "that's not my department") and still answers the real need (a heading/target). Spot-check across at least **Butler** (declines like a valet), **War-Weary Veteran** (grunts, redirects to a target), and **Overeager Rookie** (crestfallen-then-eager).
- [ ] **Refusal turn keeps the voice:** ask for something it genuinely can't know or do → the refusal is delivered **in persona**, not a bare apology.
- [ ] **Escape hatch survives:** say *"just give it to me straight"* (or "no jokes") → the very next reply drops the bit and answers plainly; the turn after, the voice returns.
- [ ] **Accuracy guard:** confirm the added flavor never invents a station, price, or system value — numbers/names still come only from real data.
- [ ] **Per-persona spot check:** cycle a few distinct personas (e.g. Stoic Zen = terse; Sassy Diva/Noir = a beat more room) and confirm each *reads* as its character on the same question.

### 14.4 Voice speed — normalized, per-provider (N7 / issue #99)
The **Voice speed** control is now ONE normalized value (`[tts].speed`, 0.5–2.0×, 1.0 = normal) that
applies to *whichever* TTS provider is active; each provider maps + clamps it to its own real range.
- [ ] **Faster on the default (Edge):** with `tts.provider = edge`, set the voice speed **above 1.2** (e.g. *"set the voice speed to 1.6"* or the slider) and ask something → the reply is audibly **faster than 1.2×** (proving it's no longer clipped at the old ElevenLabs cap).
- [ ] **ElevenLabs slow-down below 1.0:** switch `tts.provider = elevenlabs`, set the voice speed to **0.8** → the reply is spoken **slower** than normal (the old 1.0 floor is gone; ElevenLabs clamps to its 0.7 floor at extreme values).
- [ ] **Piper faster is actually faster:** with `tts.provider = piper` (a local `.onnx` voice set), set the speed to **1.5** → the reply is **faster**, not slower (guards the `length_scale` inversion — a sign error would make "faster" slow it down). Set it to **0.7** → **slower**.
- [ ] **Voice command respects range:** say *"set the voice speed to 1.5"* → applied and confirmed. Say *"set the voice speed to 5"* → **rejected** with the valid range (0.5–2.0), not written.
- [ ] **Provider switch carries no bad value:** set a slow speed (e.g. 0.6) on one provider, then switch providers → the new provider speaks at its own capped version of 0.6, never an API error.
- [ ] **Live apply:** change the speed mid-session (no restart) → the **next** reply reflects the new speed.

### 14.5 Log filter (N7)
- [ ] The Live Log has a **Conversation / All** toggle. **Conversation** (default) shows only your utterances and COVAS replies; **All** shows status/thinking/search/usage/system lines too.
- [ ] Switch to Conversation → status/thinking/usage lines **hide**; the choice **persists** across a reload.

### 14.5a Live Log — select & copy (issue #6, relabelled #74)
- [ ] **Selection survives new lines:** during an active session (lines still arriving), **scroll up** and drag-select an older line → the selection is **not** lost and the view does **not** jump to the bottom while you're scrolled up / selecting. Scroll back to the bottom → auto-scroll **resumes**.
- [ ] **"Copy log" affirms the count (issue #74):** the log-header link reads **"Copy log"** (its tooltip still notes it respects the filter). In **Conversation** mode click it → button briefly reads **"Copied N lines"** (N = the Commander/COVAS lines currently in the log) and the clipboard holds only those, timestamped `HH:MM:SS  who: text` — no HTML. Switch to **All**, click again → N is bigger and status/thinking/search/`[router]`/`[usage]`/system lines are included too.
- [ ] **Per-line copy:** hover a line → a small **⎘** button appears; click it → just that line is on the clipboard (shows ✓ briefly), distinct from the header's whole-log "Copy log".
- [ ] 🖥️ **Native window:** repeat the selection + Copy checks in the **packaged app's** window (not just the browser build) — selection highlights and both copy paths work there too.

### 14.5b Jump to latest (issue #77)
- [ ] Let the log fill past one screen, **scroll up** → a floating **"↓ Jump to latest"** pill appears centered at the bottom of the log box; it stays hidden while you're already at the bottom.
- [ ] While scrolled up, trigger a few more lines (talk to COVAS, or wait for status/search lines under **All**) → the pill's label switches to **"↓ N new messages"**, counting up.
- [ ] Click the pill → the log jumps to the bottom, the pill hides, and auto-follow **resumes** (new lines after that keep the view pinned to the bottom without re-showing the pill).
- [ ] Toggle **Conversation ⇄ All** while scrolled up → the pill's visibility stays correct for the filtered content (hidden lines don't count toward "at the bottom").

### 14.5c Right-click Copy on a selection (issue #75)
- [ ] **Browser:** select some log text, right-click it → a small dark **Copy** menu appears at the cursor (not the browser's native menu); click it → the selection is on the clipboard. Right-clicking with **no selection** leaves the browser's normal menu alone.
- [ ] 🖥️ **Native window (the real point of #75):** in the **packaged app** (`run_covas_app.py` / installed `COVAS++.exe`, not `run_covas_ui.py`'s browser tab) the native right-click menu is suppressed entirely — select log text and right-click → the same custom **Copy** menu appears there and copies correctly. Right-clicking a **per-line ⎘ button** does not bring up this menu.
- [ ] Click elsewhere (or scroll, or Alt-Tab away) while the menu is open → it dismisses without side effects.

### 14.6 Checklist editor (N10) — http://127.0.0.1:8765/checklist 🌍 NET (CDN)
> Edits the SAME `ultimate_checklist.md` the voice loop uses. Use a **throwaway** line.
- [ ] The tab renders the checklist as **rendered markdown** (headings, checkboxes) — not a plain textarea. The header shows the file name; ☑ checklist links exist on the panel and settings headers.
- [ ] **Toggle:** click a checkbox → **SAVE** → 📋 that line flips `- [ ]`/`- [x]` in the file; ask *"what's next?"* by voice → the change is heard (same file, read live).
- [ ] **Edit + nest:** edit an item's text inline; **Tab** nests it under the item above → SAVE → 📋 text and indentation land in the file; task lines stay `- [ ]` style (never `* [ ]`).
- [ ] **Voice → web:** mark an item by voice, then click **RELOAD FROM DISK** (or refocus the tab) → the voice edit appears.
- [ ] **Live in-place sync (#82) — the headline test:** with the tab open and **no unsaved edits**, mark an item done **by voice** → within a moment the matching checkbox **flips to checked in place** (no reload click), a green *"Updated — N/M complete"* flashes, and the item renders **identically to a fresh reload**. Repeat with *"add …"*, *"change … to …"*, and *"delete …"* → the added/edited/removed line updates live too.
- [ ] **Bulk coalesce (#82):** with several pending items, say *"mark the next three done"* → the checkboxes update in **one** smooth batch, not a flickering series of re-renders.
- [ ] **Dirty-guard (#82) — never clobbers:** start editing a line (leave it **unsaved**), then make a voice change to a *different* line → your in-progress edit is **kept** and the **amber "changed on disk" warning** appears instead of a live overwrite; **RELOAD THEIR VERSION** discards your edit and loads the voice change, **OVERWRITE ANYWAY** keeps yours.
- [ ] **Stale-write guard:** load the tab, make a voice edit while you have unsaved changes, then click SAVE in the tab → an **amber warning** appears (file changed on disk) instead of clobbering; **RELOAD THEIR VERSION** shows the voice edit, or **OVERWRITE ANYWAY** forces yours.
- [ ] **Save feedback:** a successful save flashes "Saved — N/M complete" and the Live Log (All filter) shows "Checklist updated from the web editor".
- [ ] **Two tabs (#82):** open `/checklist` in **two** browser tabs; **SAVE** an edit in one → the *other* clean tab reflects it live in place.
- [ ] **Concurrency never clobbers (#163):** hammer SAVE from two tabs (and/or a voice edit) at almost the same instant → each write either lands or gets the amber "changed on disk" 409; you never see one silently overwrite the other. (Internal fix: the version check and the write are now atomic under one save lock — hard to trigger by hand, covered by `tests/test_web_robustness.py`.)

### 14.7 Memory browser (issue #62) — http://127.0.0.1:8765/memory  🌐 PANEL 📋 FILE
> Reads/writes the SAME `memory/memory.jsonl` the voice loop uses. Needs `[memory].enabled = true`
> (default). Pure vanilla JS — **no CDN**, so it works offline. Use **throwaway** facts.
- [ ] **Tab + nav:** the 🧠 memory link exists on the control-panel, settings, and checklist headers; opening it lists **every** memory with its type, tags, and timestamp; the header shows the file name.
- [ ] **Add:** type a fact (e.g. *"prefers metric units"*), pick a type, add a tag → **ADD** → 📋 a new JSON line appears in `memory/memory.jsonl`; ask by voice *"do you remember what units I use?"* → COVAS answers **from the new fact** (same file, read live).
- [ ] **Search:** type in the search box → the list filters live by text, tag, or type; the count shows `N / total`; clearing restores all.
- [ ] **Edit:** click **EDIT** on a memory, change its text/type/tags → **SAVE** → 📋 the file line updates; the memory's `id` and original `when` are **unchanged** (round-trips losslessly), only the edited fields differ.
- [ ] **Edit keeps tags (#163):** EDIT a **tagged** memory, change only its **text** (leave the tag chips as-is) → SAVE → 📋 the tags are **still present** on the file line (a partial edit that omits `tags` no longer wipes them). An explicit *clear all tags* → SAVE still empties them.
- [ ] **Delete:** click **DELETE** on a throwaway memory, confirm → 📋 that line is gone from the file; the rest survive.
- [ ] **Voice → web:** say *"remember that my callsign is Ghost"*, then click **RELOAD FROM DISK** (or refocus the tab) → the captured fact appears in the list.
- [ ] **Stale-write guard:** load the tab, make a voice memory (*"remember that…"*) so the file changes, then try to **ADD/EDIT/DELETE** in the tab → an **amber warning** appears (file changed on disk) and the write is refused instead of clobbering; **RELOAD** pulls in the voice memory. The Live Log (All filter) shows "Memory updated from the web browser" on a successful web write.

Notes:

### 14.8 Engineer dashboard (issue #133) — http://127.0.0.1:8765/engineers  🌐 PANEL 🎮 ED
> A **read-only** at-a-glance grid of every ship engineer × {locked / invited / unlocked+grade}
> with the outstanding requirement for the locked ones. Reads the SAME data the voice tools (#65)
> use — the bundled table joined with the live `EngineerProgress` journal map — no new data, no
> writes. Pure vanilla JS — **no CDN**, works offline.
- [ ] **Tab + nav:** the 🔧 engineers link exists on the control-panel, checklist, memory, and crew
  headers; opening it lists **every** engineer (20+) as a card with base, system, and the modules
  they engineer.
- [ ] 🎮 **Live status matches the journal:** with Elite running (so `EngineerProgress` has been
  read), each card's badge matches your real progress — **Unlocked** shows the correct **grade**,
  invited/discovered show **In progress**, the rest **Locked**. Spot-check one of each against the
  in-game Engineers screen. The tally chips (All / Unlocked / In progress / Locked) sum to the full
  count and the footer says "N of M engineers unlocked."
- [ ] **Outstanding requirement:** every not-yet-unlocked card shows what it **still needs** (the
  invitation task and/or the unlock gift); unlocked cards show no requirement. These agree with the
  voice answer for the same engineer (*"how do I unlock The Dweller"*).
- [ ] **Filter + search:** click a status chip → the grid filters to that bucket; type in the search
  box (e.g. `FSD`, `Colonia`, an engineer name) → the grid filters live by name, system, or module.
- [ ] **Fail-soft with no journal data:** open the page with Elite **closed** (or before any
  `EngineerProgress` this session) → an amber **"no engineer data from the journal yet"** note shows
  and every engineer is listed **locked** with its requirements — **no error**, still a useful
  reference.

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
- [ ] Odd/long reply doesn't crash the loop: ask for a punctuation- and symbol-heavy answer (*"Explain em dashes, en dashes, hyphens, and arrows, with examples."*) → COVAS streams + speaks the whole reply and returns to IDLE with no console crash. (It's a voice app — the model describes symbols in words and STT can't feed it glyphs, so you're confirming the loop **survives any reply**, not that specific glyphs print. Real non-cp1252 console safety is covered by the offline unit test `tests/test_console_hardening.py`.)
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

### 18.3.1 Context-grounded chatter (issue #85 — `[audio.cues].flavor`, `Full` level)
- [ ] 🎮 With `[audio.cues].enabled` **and** `[audio.cues].flavor = true`, optimization level `Full`,
  fly a varied session: dock at a **busy core-world** station, then jump out into **deep/unpopulated
  space**, then get **interdicted** or run **low on fuel**. Listen to the ambient musings → the lines
  are **situationally motivated and varied** (a lived-in-hub mood vs. a lonely-black mood vs. a rattled
  post-danger mood), not the same generic pool loop. Confirm they never state a **name or number**
  (fact-safe) and don't obviously **repeat** back-to-back.
- [ ] 🎮 **Fail-soft to canned:** stop/misconfigure the LLM (or pull the network) with `flavor` still
  on → ambient chatter keeps playing from the **canned pool**, the loop never stalls or errors.
- [ ] 🎮 **Off at lean tiers:** set the optimization level to `Standard` (or leaner), or leave
  `[audio.cues].flavor = false` → chatter is **pool-only**, and no background LLM call is made for a
  musing (watch the cost log — no cheap-tier chatter calls).

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
  "our"-perspective line like *"feels good to have people around us again, Commander"*), it's spoken
  in **your companion's OWN voice, clean** (the same voice as its replies), **not** a random radioed
  cast voice — and it does **not** carry the radio/static comms treatment. By contrast, **station
  traffic / patrol / market** ambient lines come from a **random radioed cast voice** on the
  comms bus. Confirm the perspective always matches the source.
- [ ] 🎮 **Persona voice = Commander-directed only, never a broadcast (issue #131):** sit in a
  **populated** system until the `populated_musing` line fires in COVAS's own voice → it must sound
  like COVAS speaking **an aside TO you** (*"feels good to have people around us again, Commander"* /
  *"somewhere lived-in for a change — I'll take it"*), **not** an outward greeting/broadcast that
  reads as another ship radioing in. There must be **no** *"nice to have some company out here"*-style
  hail in the persona voice. Broadcast-flavored chatter (traffic/patrol/market "some company on the
  scope") should only ever come over the **COMMS bus in a radioed cast voice**, never in COVAS's voice.

### 18.5c Persona speech arbiter — queue, preempt, flush (issue #146)  🎮 ED 🔊 HW
> The Ship's-AI (persona) voice now plays **one line at a time** through a single arbiter, so a
> proactive/route callout and an ambient musing can't talk over each other on the COVAS bus.
> Needs `[audio].enabled`, `[audio.cues].enabled` (ambient PERSONA musings), and `[proactive]`/
> route callouts on, with `[elite].enabled` driving events. Persona voice ONLY — radioed cast/comms
> voices are separate and may still overlap the persona voice (that's intended).
- [ ] 🔊 **Queue, don't overlap:** in a **populated** system with ambient chatter on, arrange for an
  ambient PERSONA musing and a **proactive/route callout** to come due around the same time (e.g.
  jump into a populated system) → you hear them **one after the other in the companion's voice, never
  mixed on top of each other**. (Before #146 they could play simultaneously and garble.)
- [ ] 🔊 **Preempt — cut short when superseded:** while an ambient **musing** is being read, trigger a
  **route/status callout** on the same subject (or a hazard-style warning) → the musing **cuts off
  mid-word** and the newer line is spoken **immediately**, not after the stale one finishes. An
  updated **route** callout arriving while an older route line is still being read likewise replaces
  it mid-word.
- [ ] 🔊 **PTT flushes stale ambient:** while a persona line (or a queued musing) is speaking, **press
  push-to-talk** → speech stops instantly **and nothing stale plays after your turn** (the queue is
  flushed — you don't hear a musing resume once you've finished speaking).
- [ ] 🔊 **Stale musing dropped, not spoken late:** queue up a musing behind a longer reply/callout,
  then move on (jump away). After ~8s (`[audio].persona_ttl_seconds`) the now-irrelevant musing is
  **dropped** rather than spoken belatedly. Lower/raise `persona_ttl_seconds` on the Settings page and
  confirm the drop window changes.
- [ ] 🔊 **Ordinary lines still queue (no chopping):** two unrelated **callouts** close together →
  the second **waits** for the first to finish (equal priority queues); it does **not** chop the
  first off. A **reply to you** always jumps ahead of a queued ambient musing.

### 18.5b Interactive crew (issue #69 — `[crew].enabled`)  🔊 HW
> Turn on `[crew].enabled = true` (Settings → **Interactive crew**, or *"turn crew on"*). Distinct
> crew voices need the bus mixer (`[audio].enabled = true`) + a cast pool (the default random
> ElevenLabs pool works); with no pool, crew lines fall back to the persona voice. Optionally set
> `[crew].roster = ["Nyx", "Vela"]` to steer the names.
- [ ] 🔊 **Crew speaks in character:** ask something that invites a crew voice (e.g. *"have your
  sensor officer read off any contacts"*) → the reply that comes from the crew member is spoken in a
  **distinct, radio-filtered voice**, while the rest of the reply stays your **companion's own**
  clean voice. The persona is still the default speaker.
- [ ] 🔊 **Deterministic voice:** over several turns, the **same crew name** keeps the **same**
  voice, and **different** names sound **different**.
- [ ] 🔊 **Barge-in mid-crew:** while a crew line is speaking, tap cancel (`[`) → it stops promptly
  and returns to Idle (barge-in works across the persona→crew segment boundary).
- [ ] 🔊 **Off by default:** with `[crew].enabled = false`, replies are spoken **exactly as before**
  — a single voice, no attribution, and any literal `[bracketed]` text is just read as text.

### 18.5c Crew editor (issue #70 — control-panel Crew tab)  🖥️
> Run `run_covas_ui.py` and open the panel; click **🎙 crew**. This edits `crew.json` (`[crew].file`),
> git-ignored. Personas fold into the system prompt; a chosen voice overrides the auto-assignment.
- [ ] 🖥️ **Add a character:** click **+ ADD CHARACTER**, enter a name (e.g. *Nyx*), a personality
  line, pick a **Voice** from the dropdown (or leave **Auto**), **SAVE ROSTER** → a green *Saved*
  and a `crew.json` appears in the data dir with your entry.
- [ ] 🖥️ **Persists across sessions:** restart the UI, reopen the Crew tab → your roster is still
  there (loaded from `crew.json`).
- [ ] 🔊 **Persona shows up in conversation:** with crew on, ask something that invites that
  character → they respond **in the personality you wrote**, in their assigned voice.
- [ ] 🔊 **Assigned voice wins:** pin a character to a **specific** voice, save, then invoke them a
  few times → they use **that** voice every time (not the deterministic auto pick). Switch back to
  **Auto** and save → they revert to the deterministic voice.
- [ ] 🖥️ **Stale-write guard:** open the Crew tab, hand-edit `crew.json` in a text editor and save,
  then click **SAVE ROSTER** in the tab → it warns the file changed and offers **reload** or
  **overwrite** (no silent clobber).
- [ ] 🖥️ **Disabled banner:** with `[crew].enabled = false`, the Crew tab shows a banner noting crew
  won't speak until enabled, but the roster still saves/loads.

### 18.5d Crew role + adopt a hired NPC pilot (issue #125 — Crew tab)  🖥️ 🎮 🔊
> Adds a **Role** field to every crew row, and lets the **Name** box suggest — and **adopt** — your
> actual hired NPC fighter pilots from the journal. Needs `[elite].enabled = true` for the
> suggestions; hiring a fighter pilot in game (or replaying a journal containing `CrewHire` /
> `NpcCrewPaidWage` events) is what populates them.
- [ ] 🖥️ **Role weaves into character:** on the Crew tab, give a character a **Role** (e.g.
  *Quartermaster*) and save. With crew on, invite them in conversation → they answer *in that role*,
  not just their temperament. (Role also shows in the static crew instruction as
  `"Name (Role) — persona."`.)
- [ ] 🖥️ **Legacy roster still loads:** an existing `crew.json` written before this change (no
  `role` key) loads with a blank Role and no error.
- [ ] 🎮 **Hired pilot appears in the datalist:** in game, hire an NPC **fighter pilot** from a Crew
  Lounge (or replay a journal with a `CrewHire`/`NpcCrewPaidWage`), then open the Crew tab and click
  the **Name** box → the pilot's name appears as a suggestion. (Confirm `npc_crew.json` appears in
  the data dir, git-ignored.)
- [ ] 🖥️ **Adopt prefills + generates:** pick the hired pilot from the Name suggestions → **Role**
  fills to *Fighter pilot* and a short **personality** is generated in the box (one cheap-tier call).
  Edit or clear the text, then **SAVE ROSTER**. Typing a *custom* name (not a hired pilot) still
  works exactly as before — no prefill.
- [ ] 🔊 **Hear the role in a reply:** with crew on and the adopted pilot saved, ask something that
  invites them (e.g. *"have my fighter pilot call it out"*) → the reply reflects their **Fighter
  pilot** role, in their voice.
- [ ] 🖥️ **Suggest fail-soft:** with no API key / network, adopting a pilot still fills a canned
  personality ("Steady in the seat; professional and brief on comms.") instead of erroring.

### 18.5e Crew best-fit auto voice pairing (issue #124 — Crew tab)  🔊 HW
> Reuses the #96 persona voice-casting machine over the crew roster. Needs an ElevenLabs key,
> `[personality].auto_voice_pairing = true` (default), and a non-lean optimization level. Give a
> character a **Personality** and leave its **Voice** on **Auto**, then **SAVE ROSTER**.
- [ ] 🔊 **Auto picks a fitting voice:** with crew on, invite the persona'd Auto character into
  conversation a few times → they consistently use ONE voice that plausibly matches the written
  personality (not the arbitrary deterministic pick from before this issue).
- [ ] 🖥️ **Editor shows the pick:** reload the Crew tab → that character's Voice dropdown's blank
  option reads **"Auto — currently: `<voice name>`"** instead of the plain "Auto (deterministic)".
- [ ] 🔊 **Pin overrides Auto:** pin that character to a DIFFERENT specific voice and save → they
  now use the pinned voice, not the paired one; switch back to Auto and save → the paired voice
  (or the deterministic fallback, if no persona) returns.
- [ ] 🖥️ **No LLM call on an unrelated save:** with a pairing already computed, save the roster
  again with only a cosmetic change (e.g. reordering rows, editing an unrelated member) → no new
  LLM call fires (nothing to observe directly, but there's no delay/cost and the SAME voice is
  kept — confirms the cache-key recompute-only-on-change guarantee).
- [ ] 🖥️ **Persona-less members stay deterministic:** a character with NO personality text stays on
  the old deterministic per-name voice, Auto or not.
- [ ] 🖥️ **Fail-soft:** with no ElevenLabs key / `auto_voice_pairing = false` / a lean optimization
  level, Auto characters still get the deterministic fallback voice — never silent, never an error.
- [ ] 🖥️ **Separate cache file:** confirm `crew_voice_pairings.json` appears in the data dir
  (git-ignored) alongside — and independent of — `personalities/voice_pairings.json`.

### 18.5f Crew chatter + addressing (issue #126 — `[crew]`)  🔊 🎮 HW
> Roster members speak in-character AMBIENT lines, and you can talk TO a member and get an in-voice
> reply. Needs `[crew].enabled = true` with at least one roster member that has a **Role**, the
> ambient layer on (`[audio].enabled`, `[audio.cues].enabled`) **with `[audio.cues].flavor = true`**
> (crew chatter is LLM-only), an LLM key, and a non-lean optimization level. For a fast smoke test
> lower `[crew].chatter_min_seconds`/`chatter_max_seconds` so lines come more often.
- [ ] 🔊 🎮 **Role-flavored ambient line in the member's voice:** with crew on and in your ship, play
  for a few minutes → a roster member occasionally speaks a short, in-character line **in their own
  radio-filtered voice** (not the persona's clean voice), and it fits their **role** + the moment (a
  gunner during a hardpoints-out fight sounds different from a cook while docked). The line asserts
  nothing checkable (no names/numbers/places).
- [ ] 🔊 **Not population-gated:** crew chatter still occurs out in **empty/unpopulated** space
  (unlike station chatter, which needs an inhabited system) — crew are aboard regardless.
- [ ] 🔊 **Address a member, get an in-voice reply:** say *"Nyx, how are we looking?"* → the reply
  comes back in **Nyx's** voice, in character; the companion may add its own unprefixed line. Try
  *"all hands, sound off"* → each crew member gives a brief line in turn.
- [ ] 🔊 **Barge-in mid-crew-line:** while a crew chatter (or addressed) line is speaking, tap cancel
  (`[`) → it stops promptly and returns to Idle.
- [ ] 🔊 **Silent when flavor off / crew off:** set `[audio.cues].flavor = false` → no crew chatter
  at all (it's generated-or-nothing, no canned pool). With `[crew].enabled = false`, neither crew
  chatter nor in-voice addressing occurs — replies are a single voice as before.

### 18.5g Per-ship crew rosters (issue #127 — Crew tab)  🖥️ 🎮 🔊 HW
> The Crew tab grows an **Editing roster** selector (Default + each fleet ship) and a **Copy crew
> from…** control; the roster that speaks is the one for the ship you're flying. The fleet is read
> from your journal (`Loadout` + `StoredShips`), so a real replay/game session is needed for the
> ship list. Needs `[crew].enabled = true`; distinct voices need the bus mixer + a cast pool.
- [ ] 🖥️ **Fleet appears in the selector:** with [Elite monitoring](docs/elite/monitoring.md) on and
  a journal seen, open the Crew tab → the **Editing roster** selector lists **Default** plus your
  owned ships, with the ship you're flying marked *active*. A ship you've never given a roster shows
  the *"inherits Default"* hint when selected.
- [ ] 🖥️ **Build a second ship's roster + copy:** select a non-active ship, click **Copy crew from…
  → Default → COPY** → the Default cast is cloned in; edit one member and save. Confirm editing the
  copy does **not** change Default (reselect Default → unchanged). `crew.json` now holds a `ships`
  block keyed by ShipID.
- [ ] 🎮 🔊 **Swap ships, hear the crew change:** give two ships different rosters (different
  names/voices), then **swap ships in game** (`ShipyardSwap`, which is followed by a fresh
  `Loadout`). Ask something that invites a crew line on each → the roster that answers/chatters is the
  one for the ship you're now flying. The other ship's crew stays silent.
- [ ] 🎮 🔊 **Swap works on OpenAI **and** Gemini (issue #151):** repeat the swap-ships check above
  with `[llm].provider = "openai"`, then again with `"gemini"` (no restart between the swap and the
  next turn). The crew roster must follow the swap on **both** — the old ship's names must stop
  prefixing crew lines the very next turn. (Regression: these providers used to freeze the system
  prompt — and its roster — at startup, so the swap never took until you restarted.)
- [ ] 🖥️ **Back-compat:** a pre-#127 `crew.json` (a bare JSON *list*) still loads as your Default
  roster with no error; the first save rewrites it to the `{default, ships}` shape.
- [ ] 🖥️ **File-known ship survives a stale snapshot:** with a ship roster already saved, restart
  before docking anywhere your ships are stored (so `StoredShips` is stale/absent) → that ship is
  **still selectable** in the editor (remembered from the file).
- [ ] 🖥️ **Limit to seats blocks over-crewing:** turn **Limit crew to ship seats** ON (Settings →
  Personality), select a **small-seat hull** (e.g. a **Sidewinder**, 1 seat) → the selector shows
  **N of 1**, **+ Add character** disables at the seat count, and **Copy crew from…** truncates a
  larger source to the seats (with a note). Confirm the **Default** roster is **not** capped, and
  that turning the setting OFF restores the generic cap.

### 18.6 Drop-in content (C11)
- [ ] On first run with the layer enabled, confirm the skeleton appears: **`audio/sfx/<cue>/`**,
  **`audio/music/<context>/`**, **`content/chatter/*.txt`**, **`content/interdiction_threat.txt`**,
  each with a README explaining the drop rule. The startup log shows a **content-status** line
  (how many files/lines per cue; what's still silent).
- [ ] 🔊 Drop a `.wav` into **`audio/sfx/thargoid_voices/`**, restart, jump to hyperspace → hear it
  on the ambient bus. Add lines to **`content/chatter/station_traffic.txt`** (one per line, `#` =
  comment), restart, dock → hear your lines (they override the built-in pool). Delete the file →
  falls back to the built-in pool. A missing/empty folder is simply silent (no error).
- [ ] **Reload ambient content without a restart (issue #110):** with the layer enabled and the app
  running, add/change lines in **`content/chatter/station_traffic.txt`** (or drop a `.wav` into an
  **`audio/sfx/<cue>/`** folder, or a track into **`audio/music/<context>/`**), then click the
  panel's **Reload cues** button (§2, next to **Open cues folder**). The message reports an
  **ambient:** count (e.g. *"reloaded — … ambient: 2 sfx, 4 chatter, …"*) alongside the turn-cue
  counts. Dock again (populated system) → your **new chatter lines** are in rotation, **no restart**.
  Delete the chatter file and reload → falls back to the built-in pool live. Drop a **corrupt** file
  and reload → no crash, other content still plays.
- [ ] **Reload respects live state (issue #110):** while **music is playing**, click **Reload cues**
  → the current track **keeps playing** (no restart of the track, no re-crossfade); a new track only
  takes over on the next genuine context change (e.g. jump into combat/deep space). Likewise, a cue
  that fired just before the reload does **not** immediately re-fire — its cooldown is preserved.

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

### 18.12 Experimental feature-flag convention (issue #123)  🖥️ 🌐 PANEL
> Nine half-baked features are gated behind `[experimental.<name>]` (all default **off**): Azure/Cartesia
> TTS, hands-free voice activation, crew, trade-route, custom macros, automatic reflexes, ambient music,
> and the Companion HUD. Off, each is **invisible** (no tool/help/Settings surface); on, it works. Doug
> self-enables via the git-ignored `overrides.json`. Most of this is covered offline by
> `tests/test_experimental.py`; these checks confirm the public-facing surface on the real app.
- [ ] **Invisible when off:** with a clean config (no `[experimental]` overrides) but the underlying
  toggles *on* (`route_plan.enabled`, `macros.enabled`, `crew.enabled`, `hud.enabled = true`), start the
  app and ask *"what can you do?"* → **no** trade-route, macros, crew, or HUD is mentioned; *"plan a trade
  route"* / *"turn the HUD on"* → COVAS says it can't (no such tool), not an error.
- [ ] **Not on the public Settings surface:** open the control panel → the **Voice provider** dropdown
  does **not** list Azure or Cartesia, the **Activation mode** control does **not** offer *continuous*, and
  there is **no** `[experimental]` group anywhere on the page.
- [ ] **Self-enable via `overrides.json`:** add `{ "experimental": { "trade_route": { "enabled": true } } }`
  to `overrides.json` (with `route_plan.enabled = true`), restart, ask *"what can you do?"* → the
  trade-route planner now appears and *"plan a trade route from here"* runs. Repeat for one more (e.g.
  `experimental.hud` + `hud.enabled` → *"turn the HUD on"* shows the overlay).
- [ ] **First-run wizard:** on a clean install the wizard's **Voice provider** list offers edge/elevenlabs/
  openai/piper only — **no** Azure or Cartesia.
- [ ] **Docs badges:** each gated feature's docs page (HUD, crew, hands-free, trade-routes, custom-macros,
  reflexes§Automatic, ambient-audio§music, personas-voice§Azure/Cartesia) shows the **"Experimental — off
  by default"** badge with the exact `overrides.json` key.

Notes:

---

## 19. Packaged build — install, first-run wizard & updates (I1–I9)  📦 🖥️ 🔊 HW 🌍 NET
> The **installed Windows app**: `COVAS++ Setup.exe` → native window, first-run wizard, and the
> Tier-2 self-updater. Extra markers: 📦 **PKG** — run the packaged build (not from source);
> 🖥️ **VM** — best done on a **clean Win11 snapshot** (VirtualBox/VMware; Windows Sandbox isn't
> available on Win11 Home) so "no Python/keys/model preinstalled" is actually proven. Revert the
> snapshot between passes. A partial dev-machine shortcut: delete `%APPDATA%\COVAS++` + the HF
> model cache to re-exercise the wizard (does **not** prove the no-runtimes case).

### 19.0a Release prep — refresh bundled game data (issue #101)  🌍 NET
> Before cutting a release, bring the bundled FDev-content datasets up to date so a downloaded
> build ships current ship/module/engineering data (the app is offline at runtime, so this is the
> only moment it converges on live community data). Do this **before** the version bump / build.
- [ ] Run `.venv\Scripts\python.exe scripts\refresh_datasets.py` → review the printed **diff summary** (new hulls / modules / blueprints / orphaned overlay rows) and the *last refreshed* nag for the hand-curated engineer tables (refresh those by hand if they've drifted).
- [ ] `.venv\Scripts\python.exe check_setup.py` → the **Game data freshness** section shows no `[warn]` (nothing older than ~6 months).
- [ ] `pytest` is green, then commit the regenerated data + manifest as part of release prep.

### 19.0 Provider bundle & default-voice self-test (issue #20)  📦 🔊 HW 🌍 NET
> The multi-provider epic (#10) added swappable providers imported **lazily** from
> `covas/providers/factory.py`, and **Edge (`edge-tts`) is the default TTS**. A lazy import the
> freeze misses would ship a bundle whose default voice silently degrades to text — so the freeze
> MUST bundle `edge_tts` + its `aiohttp` stack and prove it. `covas.spec` `collect_all`s them and
> `--selftest` imports the third-party `edge_tts` plus every provider module.
- [ ] **Frozen self-test (build machine):** `.\build.ps1 -Installer -SelfTest` → the freeze
  completes and the frozen `COVAS++.exe --selftest` prints `SELFTEST OK …incl. …edge_tts` and exits
  0. A missing bundle fails the build **loudly** instead of shipping. This proves `edge_tts` /
  `aiohttp` **and** `covas.providers.{edge_tts,azure_tts,openai_tts,cartesia_tts,piper_tts,elevenlabs_tts,openai_llm,gemini_llm}`
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

### 19.2 First-run wizard — pick any LLM/TTS combo (issue #87)  📦 🖥️ 🔊 HW 🌍 NET
> On a machine with none of the dev state — that absence *is* the test. The wizard must let you
> finish with ANY supported LLM + TTS, **not** only Anthropic + ElevenLabs.
- [ ] First launch (empty `%APPDATA%\COVAS++`) opens the **setup wizard**, not the panel.
- [ ] **LLM provider picker** offers **Anthropic / OpenAI-compatible / Gemini** (all cloud); selecting
      one reveals just its fields (Anthropic → key; OpenAI → endpoint preset + model + key; Gemini →
      key (+ model)). The **"AI ready"** badge names the chosen provider.
- [ ] **TTS provider picker** offers **Edge (free, no key) / ElevenLabs / Azure / OpenAI / Cartesia /
      Piper**; selecting one reveals just its fields. Edge/Piper show **no key field**.
- [ ] **Non-Anthropic + non-ElevenLabs onboarding (the key case): pick Gemini (or OpenRouter) + Edge**,
      paste ONLY the Gemini/OpenRouter key, download the STT model → the **Launch** button enables with
      **no Anthropic key and no ElevenLabs key**. Finish → the app starts and speaks a turn in the
      **Edge** voice (not text-only).
- [ ] **Mic** picker lists your input devices; pick one.
- [ ] **STT model** downloads (`small.en`, ~250 MB) with a **progress** indicator (needs internet); it's fetched **once**.
- [ ] Wizard **hands off to the control panel in the same window** — no second window, no browser. The finish message says it's **switching to the control panel** (NOT "close this tab"); the panel appears **without you closing anything** (closing the single native window quits the app).
- [ ] **Keyless-cloud-voice → text-only:** pick a cloud voice (e.g. ElevenLabs) but leave its key blank
      → the voice badge shows **text-only** and the app still finishes (on the LLM + STT); add the key
      later in Settings → spoken replies start working. (Edge/Piper never hit this — they're free.)
- [ ] **ElevenLabs default voice:** with an EL key + TTS = ElevenLabs, "Save voice" resolves **George**
      (or the first valid voice if George isn't in your catalog).
- [ ] 📋 After the wizard, `%APPDATA%\COVAS++` holds `overrides.json` with your `[llm].provider` /
      `[tts].provider` choices + keys, and the model is under `%LOCALAPPDATA%`; **nothing** was written
      into the install tree (`%LOCALAPPDATA%\Programs\COVAS++`).

Notes:

### 19.3 Native window & quit  📦 🔊 HW
- [ ] App launches from the **desktop/Start-menu icon** as a **native window** (no browser tab, no URL bar) rendering the panel.
- [ ] **PTT works from the window:** hold `[`, speak, release → normal turn; audio plays.
- [ ] **Closing the window quits** the app — no tray icon, no lingering background process (check Task Manager: no `COVAS++`/python left running).

Notes:

### 19.3a Control-panel zoom (issue #116)  📦 🖥️
> The **packaged window** is the surface that lacked zoom — a real browser already has Ctrl+±/scroll.
- [ ] In the native window, press **Ctrl+`+`** a few times → panel content scales up and reflows; **Ctrl+`-`** shrinks it; **Ctrl+`0`** returns to 100%. Clamped 50%–200% (mashing past the ends stops there, doesn't error).
- [ ] **Ctrl+scroll** up/down over the panel zooms in/out the same way.
- [ ] The header's **`− 100% +`** cluster mirrors the shortcuts; clicking the `%` resets to 100%.
- [ ] Set zoom to **130%**, **close and relaunch** the app → panel reopens at 130% with **no flash** of 100% before it snaps to 130%.
- [ ] Navigate between pages (panel ↔ settings ↔ checklist ↔ crew ↔ macros ↔ memory) → zoom stays applied on every page.
- [ ] Trackpad/touch **pinch-zoom** also works in the packaged window (`zoomable=True`).
- [ ] Sanity: `run_covas_ui.py` in a real browser still zooms natively (Ctrl+±/scroll), and the in-page `− % +` control also works there.

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

### 19.6a Config migration on upgrade (issue #79)  📦 🖥️ 📋 FILE
> On upgrading an existing install, `load_config` loads the **shipped** `config.toml` as the base then
> layers the user's data-dir `config.toml` + `overrides.json` on top — so an upgraded install **gains**
> new config sections/defaults (e.g. a new `[openai]` section) instead of keeping a stale config forever.
- [ ] On an install seeded by an **older** build (its data-dir `config.toml` missing a newer section such as `[openai]`), note the stale config lacks that section.
- [ ] Install the new build **over** it and launch.
- [ ] Open Settings → the **API keys card** for the newer provider (e.g. OpenAI) works — the new section is now present — rather than erroring *"no api_key_file configured for this provider"*.
- [ ] Confirm your **prior settings / overrides** (voice, mic, keys, etc.) are preserved, **not** reset to defaults.

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

### 19.10 Control-panel cross-origin (CSRF) guard — GHSA-3mxj-5926-rqmr  🌐 PANEL
> The panel binds to `127.0.0.1:8765` and now refuses state-changing requests driven from any OTHER
> web origin (the fix for the drive-by RCE / key-exfiltration / destructive-CSRF advisory). Verify a
> foreign page can't reach the write endpoints, while the panel itself still works. Do this with the
> UI build running; the "attacker" page is any HTML file opened from a *different* origin (a
> `file://` page, or a page served on another port).
- [ ] 🌐 **Panel still fully works:** with the app running, use the panel normally — change a setting,
  send a typed prompt, hit **CANCEL**, save a key. All succeed (same-origin requests are unaffected).
- [ ] 🌐 **Cross-origin write is refused:** open a scratch page on another origin (e.g. save
  `<script>fetch("http://127.0.0.1:8765/api/cancel",{method:"POST"}).then(r=>alert(r.status))</script>`
  as a `.html` and open it from disk, or serve it on `:8000`). The alert shows **403** and the app
  does **not** cancel / act. In DevTools the response is a `cross-origin request refused` JSON body.
- [ ] 🌐 **Forged update can't run:** from that same foreign page, POST to `/api/update/apply` with
  `{asset_url:"http://attacker/malware.exe"}` → **403** (guard), and even if it reached the endpoint
  the server ignores the body and only ever downloads the real GitHub release asset. Nothing launches.
- [ ] 🌐 **Key isn't exfiltrated:** with an OpenAI-compatible key set, load
  `http://127.0.0.1:8765/api/catalog?source=@openai_models&base_url=https://example.com` from the
  foreign page → response is `{options:[], error:"… not an allowed endpoint"}` and no request carrying
  your key reaches `example.com` (watch the network tab). Picking a real preset in Settings still lists
  models normally.

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
