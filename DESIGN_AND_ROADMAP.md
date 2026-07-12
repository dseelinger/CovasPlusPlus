# COVAS++ — Design & Roadmap

*Working design doc, kept in sync as features land. §1–§6 plus §3.5 describe the architecture as built; §7 tracks build status and the backlog; §9 is the testing strategy. The app is well past its MVP — the core voice loop, provider seam, cloud-tiering router, ED monitoring, proactive callouts, the keybind prototype, and outfitting voice search are all built and merged to `main`.*

Priorities agreed: **modular refactor**, **cut API costs** (quick cloud wins *and* cloud model tiering), and **Elite Dangerous log monitoring**. Keybind automation is a later phase, sketched here so the architecture leaves room for it.

> **Decision (updated):** local LLMs are out. A model capable enough to be useful wants the same GPU/VRAM Elite Dangerous is already saturating, so running both starves one. Cost mitigation is therefore *cloud tiering* (Haiku → Sonnet → Opus), not a local/cloud hybrid. Local **Piper TTS** and **Whisper STT** stay viable — they're light CPU work that coexists with the game — so the one worthwhile local move is dropping ElevenLabs for Piper. The provider seam already built makes this a config/router change, not a rewrite.

---

## 1. Cost: what's already done, what's left

### Done now (safe, in-code)
- **Killed the real burn:** `overrides.json` had been forcing `claude-opus-4-8` **plus High extended thinking** on every turn, silently overriding the cost-tuned config. Stripped back to just the voice preference, so model/thinking now fall back to the config defaults (Sonnet, thinking off). This was the dominant cost.
- **Prompt caching** (`covas/llm.py`): personality system prompt + tool schemas sent with an ephemeral `cache_control` breakpoint — served from cache at ~90% off input price instead of re-billing ~8 KB of preamble every turn and every tool-loop round.
- **Cheaper default model** (`config.toml`): `claude-opus-4-8` → `claude-sonnet-5`. (Becomes Haiku-by-default once the tiering router lands — see §4.)
- **`max_tokens` cap** (`config.toml`): 4096 → 1024. Replies are spoken, so a few sentences is plenty; a low cap trims Claude output tokens *and* ElevenLabs characters at once.
- **Fewer web searches** (`config.toml`): `web_search.max_uses` 5 → 3. Each result is pulled into context and persists in history, inflating *every following turn*.

### Still available (your call — knobs, not rewrites)
- **1-hour prompt-cache TTL.** The default cache lives 5 minutes; in-game you may go 15–20 minutes between voice turns, so it expires and every turn pays a full (premium) cache *write* with no read benefit. The 1-hour TTL costs a bit more per write but survives the gaps — a clear win if you talk a few times an hour.
- **`conversation.max_turns = 20`** resends up to 40 messages per turn. With the preamble cached, history is the main variable cost. Drop to ~8–10, or summarize older turns into a compact running note.
- **Drop ElevenLabs for Piper.** The one local move that survives running next to ED (TTS is light CPU, not GPU) — takes TTS cost to zero. Keep ElevenLabs as a "premium voice" toggle. See §4.
- **Usage logging + dev-mock.** Log the per-call token counts the API already returns (including cache hits) with a rough cost estimate, so tuning is data-driven; and a dev-mode flag that returns canned replies so iterating on code spends nothing.

---

## 2. Design principles

1. **Provider-agnostic core.** The voice loop should know it needs "an LLM reply" and "speech from text," not *which* service produces them. Anthropic, Ollama/Qwen, ElevenLabs, and Piper sit behind small interfaces.
2. **Policy separate from mechanism.** *How* to call a provider (mechanism) is isolated from *which* provider to use for a given request (policy/router). Cost routing changes only the policy.
3. **Capabilities as plugins.** Checklist, ED-log monitoring, and keybind automation are self-contained capability modules that register their tools and event handlers with the core, rather than being wired into `app.py`.
4. **Event bus as the spine.** You already have `EventBus`. Make it the one-way nervous system: inputs (voice, ED journal, timers) publish events; capabilities and the UI subscribe. This is what keeps new features additive.
5. **Typed settings over raw dicts.** Every module currently reaches into `cfg["section"]["key"]`. Introduce small typed settings objects so a mis-key fails loudly at load, and providers receive only their slice.
6. **Fail soft, stay live.** The current code already swallows errors to keep the loop alive — preserve that. A dead TTS provider should degrade to text, not crash the session.
7. **Inject dependencies; keep the default test run free.** Build real providers only at the composition root; everything downstream receives them as arguments so tests can pass fakes. Unit tests hit no network, API, or hardware; anything that does is an opt-in integration test (see §9). This is what lets you run tests constantly without draining accounts.

---

## 3. Target architecture

Think of it as three layers over the event bus.

```
            ┌───────────────────────────── EventBus ─────────────────────────────┐
            │  status • log • ed_event • timer • settings                        │
            └───────────────────────────────────────────────────────────────────┘
 INPUTS                    CORE / ORCHESTRATION                 PROVIDERS
 ─────────                 ────────────────────                 ─────────
 PTT + mic ──► STT ──►  Conversation loop  ──► Router ──► LLMProvider  {Haiku|Sonnet|Opus}
 ED journal ─► Watcher       │  (app.py)              └─► TTSProvider  {elevenlabs, piper}
 timers ─────► Scheduler     │                            STTProvider  {faster-whisper}
 UI/web ─────────────────────┘
                             │
                     CapabilityRegistry
                     {checklist, ed_context, keybinds…}  ──► tools + event handlers
```

### 3.1 Provider interfaces
Three tiny protocols. Each has 1–2 methods; existing code becomes the first implementation of each.

- **`LLMProvider.stream_reply(messages, cfg, cancel, on_event, tool_handler) -> Iterator[(kind, chunk)]`** — exactly today's `llm.stream_reply` signature. `AnthropicLLM` wraps the current file; tiering (Haiku/Sonnet/Opus) is a *parameter* to it, chosen by the Router, not a separate provider. An `OllamaLLM` implementation exists in the tree for out-of-game/offline use, but is not part of the in-game path (see §4). Each provider normalizes to the same `("text"|"thinking"|"search"|"tool", data)` event stream the app already consumes.
- **`TTSProvider.speak(text, cancel)`** and **`.synth_pcm(text) -> bytes`** — today's `tts.py` becomes `ElevenLabsTTS`; `PiperTTS` is new and runs fully local. Both emit the same 16-bit mono PCM the playback path already expects, so `audio`/cancellation code is untouched.
- **`STTProvider.transcribe(audio) -> str`** — today's `Transcriber` implements it directly. Rarely swapped, but the seam keeps STT symmetric with the others.

Providers are constructed once from config by a small factory (`providers/factory.py`) that reads `[llm]`, `[tts]`, `[stt]` sections and returns the configured implementation(s).

### 3.2 The Router (cost policy lives here)
A single `Router` object decides, per request, which LLM/TTS to use. It's the *only* place cost policy lives, so tuning it never touches provider or loop code. Input: the user text, conversation state, and config. Output: a chosen provider + parameters. See §4 for the decision logic.

### 3.3 Capability registry (plugin system)
A `Capability` is a small class exposing:
- `tools()` → list of tool schemas to advertise to the LLM (checklist tools move here verbatim),
- `run_tool(name, input)` → handler (today's `app._run_tool` body, relocated),
- optional `on_event(event)` → subscribe to bus events (e.g. ED-context capability reacts to journal events),
- optional `system_context()` → a short string injected into the system prompt (e.g. "Commander is currently docked at …").

`app.py` shrinks to: capture → STT → build messages (+ capability context) → Router picks provider → stream → capabilities handle tool calls → TTS. Adding a feature = dropping in a new `Capability`, not editing the loop.

### 3.4 Refactor sequence (low-risk, incremental)
1. Extract `LLMProvider`/`TTSProvider`/`STTProvider` protocols; make current Anthropic/ElevenLabs/Whisper code implement them. No behavior change — pure seams.
2. Introduce the provider factory + `[llm]`/`[tts]`/`[stt]` config sections (defaulting to today's services).
3. Move checklist tools into a `ChecklistCapability`; add a `CapabilityRegistry`. Loop behavior identical.
4. Add the `Router` returning a single fixed tier initially. Now the structure exists with zero functional change — a safe checkpoint.
5. Turn on cloud tiering in the Router (Haiku default → Sonnet/Opus escalation); optionally make Piper the default TTS (§4).
6. Add the ED journal watcher as an input + `EDContextCapability` (§5).

Each step is independently shippable and testable. *(All six steps are complete — the seam, factory, `[llm]`/`[tts]`/`[stt]` config, `CapabilityRegistry`, the tiering Router, and the checklist capability are on `main`.)*

### 3.5 Voice search & help (LLM-native) — design decision

The outfitting search (§5) generalizes into a six-category Spansh voice-search surface — stations, outfitting, minor factions, star systems, signals, misc (**bodies out of scope**, seam only) — plus a first-class help subsystem. Three decisions shape it:

- **LLM-native, not an explicit state machine.** Each category is a *stateless* tool whose description steers the model through conversational slot-filling and disambiguation; conversation history *is* the state, and multi-turn refinement is just the model re-calling with accumulated constraints. We deliberately did **not** build a separate intent-classifier or query-state machine — natural language beats a rigid script for a voice-only UI, and it's already how the working outfitting capability behaves. The one stations-vs-outfitting routing rule lives in the tool descriptions (if a module/ship is named, use outfitting), not a classifier.
- **Help is a templated projection of the registry — no LLM in help *generation*.** The existing `CapabilityRegistry` is *extended* with help metadata (one_liner, example, `group`, per-slot phrasings + help_text) — ONE registry, not a parallel one, so the drift that kills help systems is prevented structurally (a registry test fails if a capability ships without complete help metadata). Help composes registered strings; it never generates prose. It's a **hierarchy so it scales as capabilities grow** (there are already ~13): `idle` ("what can you do") names the GROUPS (navigation & search, your ship, your checklist, community goals, settings…), not every capability; asking about a group lists its capabilities (≤3 + tail); asking about a capability gives its detail. A capability with no `group` is its own singleton group, so nothing is ever unreachable. Every invokable capability carries help metadata (checklist, ship status, ship controls included); ambient-only features (route/auto-honk callouts, the proactive mute) are deliberately out. The other mode — the important one — is *failure recovery* ("I didn't recognize 'power distributer' — did you mean Power Distributor?"). An unresolved utterance is a help request in disguise.
- **Anti-hallucination is structural.** Any capability/slot/module/system name in spoken output must resolve against the registry or a canonical source (Spansh, the journal) before it's spoken; on failure, fall back to a templated error. Never invent a filter or capability. This is why the module taxonomy is bundled and validated offline, and why the LLM is used to *understand* messy speech, not to assert facts.

The prompts are in `CLAUDE_CODE_PROMPTS.md` (Search Prompts 1–6). The shared Spansh client is extracted from the existing `nav/closest.py`; `nav/modules.py` is reused as the outfitting resolver.

**Implemented** (`covas/search/` + `help_capability.py` + the `*_search_capability.py` set): `HelpCapability` (templated idle + failure-recovery, deterministic phrasing rotation); the registry-contract test that fails when a capability ships without complete help metadata; the shared typed `search/spansh.py` client with per-category builders/parsers (`categories.py`, `stations.py`, `systems.py`, `factions.py`) and offline fuzzy `faction_index.py` / `vocab.py`; and five LLM-native category capabilities (star systems, stations, minor factions, signals, misc) built on the outfitting pattern. Outfitting is refactored onto the shared client.

---

## 4. Cloud model tiering strategy

Local LLMs are off the table (see the decision note up top): a useful model competes with Elite Dangerous for the GPU. So instead of local-vs-cloud, tier across **cloud** models — answer routine turns on the cheapest capable one, escalate only when the turn earns it.

### Tiers
- **Default — Haiku 4.5.** The workhorse: in-cockpit banter, acknowledgements, checklist reads/updates, status readouts, anything answerable from ED context already in the prompt. Far cheaper than Sonnet/Opus and plenty for these.
- **Escalate — Sonnet.** Nuance, multi-step reasoning, or turns that need web search for current data.
- **Rare — Opus.** Reserve for explicitly hard asks; usually not worth it for a voice companion.

### Routing policy (deterministic first)
The Router (§3.2) decides per turn. Keep it rules-based and explainable to start:

- Escalate to Sonnet when the request needs current/web data, asks for depth/analysis, or matches a wake phrase ("think hard", "ask the big brain").
- Otherwise stay on Haiku.
- Always allow a manual override (wake phrase / UI toggle) to pin a tier.

Log every decision with its reason, so you can tune the rules from real transcripts. A cheap classifier (a Haiku pass that tags cheap/premium) can come later if rules aren't enough — leave the extension point, don't build it yet.

### Cost levers that stack with tiering
- **Prompt caching** (done) on system + tools. For sporadic in-game talking, use the **1-hour cache TTL** so it survives the gaps between turns rather than expiring every 5 minutes.
- **`max_tokens` cap** (done, 1024). The Router can raise it for an explicit "give me the full breakdown" turn.
- **Thinking off by default** — make extended thinking opt-in per turn, never global (a High-thinking default was the original burn).
- **Trim history** — lower `conversation.max_turns` or summarize older turns.
- **Usage logging** — log the token counts the API returns per call (including cache reads/writes) plus a rough cost estimate; pair with a dev-mode mock for zero-cost iteration.

### TTS: the one worthwhile local move
TTS is a light CPU burst, not a GPU hog, so **Piper runs fine alongside the game**. Defaulting TTS to Piper takes ElevenLabs cost to zero; keep ElevenLabs as a "premium voice" toggle for relaxed sessions. Because both emit the same PCM, this is a config/router choice, not a code change. Whisper STT is already local for the same reason. Note the voice character differs — Piper is good but not ElevenLabs-smooth; worth an A/B once wired.

---

## 5. Elite Dangerous log monitoring

ED continuously writes game state to disk — the same source other Elite Dangerous tools read. No memory reading, no API keys.

### What ED writes
- **Journal** — `%USERPROFILE%\Saved Games\Frontier Developments\Elite Dangerous\Journal.<timestamp>.<part>.log`. Newline-delimited JSON, **one event per line, append-only**. Events include `FSDJump`, `Docked`/`Undocked`, `Scan`, `Bounty`, `FuelScoop`, `MissionAccepted/Completed`, `Died`, `LoadGame`, etc.
- **Status.json** — a single-object snapshot rewritten frequently: a `Flags` bitfield (docked, landing gear, hardpoints, night vision, low fuel…), plus pips, fuel, cargo, position. This is your real-time "what's happening right now."
- **Companion snapshots** — `Cargo.json`, `NavRoute.json`, `Market.json`, `Outfitting.json`, `ModulesInfo.json`, `ShipLocker.json` — rewritten when relevant.

### Watcher design (a new input, mirroring PTT)
- A `JournalWatcher` thread tails the **newest** journal file: seek to end, read appended lines, and roll over when a new file appears (ED starts a fresh file per session/part). Parse each line as JSON → publish `{"type": "ed_event", "event": "FSDJump", ...}` on the EventBus.
- A `StatusWatcher` polls `Status.json` (or watches for file-change), diffs the `Flags` bitfield, and publishes semantic transitions (`{"type":"ed_event","event":"Docked"}` when the docked bit flips) rather than raw spam.
- Debounce/whitelist: publish only events worth reacting to; keep a rolling "current context" object (system, station, ship, docked?, fuel%) that the `EDContextCapability` can inject into the prompt via `system_context()`.

### What it unlocks
- **Ambient awareness** — replies grounded in real state: "You're low on fuel and the next scoopable star is two jumps out." Feed the current-context object into the system prompt (cached!) so even local Qwen sounds situationally aware.
- **Proactive callouts** (opt-in) — the app *initiates* speech on key events (arrival, mission complete, near-death), routed through TTS without a PTT press. Gate behind a config toggle and a cooldown so it isn't chatty. **Implemented** as `ProactiveCapability` (`[proactive]`, default off): a pure `ProactivePolicy` (per-event whitelist, per-event + global cooldowns, runtime mute) gates a live-only **event pump** — a daemon thread that subscribes to the bus (`replay=False`) and fans events to any capability's `on_event` hook, so a slow callout never blocks a watcher. A qualifying event asks `app._speak_proactive`, which speaks **only when Idle** (never over an in-progress user turn) via the existing speak/cancel path (a PTT press cancels a callout like any utterance), generating one short line on the **cheap tier** (`Router.cheap_route`). Callouts are logged + spoken but kept out of conversation history. Mutable at runtime by voice via `mute_proactive`/`unmute_proactive` tools.
- **Cheap local answers** — "where am I / what's my cargo / how's my fuel" become zero-cost local reads from context, no LLM round-trip needed for the trivial ones.

Design the watcher to publish events **only**; capabilities decide what to *do* with them. That keeps monitoring reusable for both conversation grounding and future automation.

### Context delivery — decided (inline injection, not the cached system prompt)
The "feed it into the system prompt (cached!)" note above turned out to be a cache **anti**-pattern: the prompt cache breakpoints sit on the personality block *and* the last tool, so anything added to `system` lives inside the cached prefix — a context line that changes as you fly would bust the tools cache every turn (the exact re-send cost we're trying to kill). Two things resolve it:

- **`EDContextCapability` exposes read tools** (`where_am_i`, `ship_status`, `recent_events`) — cache-safe, and the model calls them on demand. These are also the "cheap local answers" above (answered from context, no game knowledge needed).
- **A rules-based `ContextDetector`** (mirrors the cost Router) classifies each turn: does it reference current status or recent activity? When it does, `app.py` prepends a compact **`context_block()`** — current status, plus the recent-events feed for a "what just happened" turn — to **that turn's user message only**. It's uncached by design (tiny, ~30–60 tokens, only on matched turns) and never stored in history, so stale telemetry can't accumulate. An explicit **"context" wake word** forces a lookup and is scrubbed from what the model sees.

Net: the model answers from real state in one shot (no tool round-trip) on the common "where am I / how's my fuel / check my logs" turns, the prompt cache stays intact, and off-topic turns pay nothing. The `system_context()` hook remains on the capability for a future carefully-cached use, but is not wired into the request.

The **recent-events feed** is a small rolling buffer on `EDContext` (bounded, `[elite].recent_events_kept`), fed by both watchers via curated describers — narrative events from the journal (jumps, docks, missions, deaths), fuel/heat alerts from Status flags — with journal-spam (auto-scans, fuel-scoop ticks, bounties) filtered out. Priming warms it from the tail of the current journal so "what did I just do" works right after launch.

### Implemented — find-closest-module (`[nav]`, default off)
Voice outfitting search: "find the closest station that sells module X." A capability
(`covas/nav/` + `FindClosestCapability`) that resolves the module **conversationally** over
multiple turns, then finds the nearest station selling it and copies the SYSTEM name to the
clipboard. The tool is **stateless** — the dialogue state *is* the message history, so each
re-call just passes more-complete args (module → +size/mount → +confirmed); there's no
pending-request object.

- **Two data sources, split on purpose.** Module *taxonomy* (names/sizes/mounts/ratings) is a
  bundled static table — the **complete** module set generated from EDCD/FDevIDs
  `outfitting.csv` into `nav/module_data.py` (regenerate via `scripts/gen_module_taxonomy.py`)
  and exposed through `modules.py` (which adds friendly mishear aliases) — so the whole
  ask/confirm/cancel disambiguation is **offline, fast, unit-testable — no network**, and it
  recognises every purchasable module, not a hand-picked subset. Only *station location*
  touches the wire (`closest.py`, Spansh), and only after confirmation.
- **`resolve(query, size?, mount?)`** is pure and returns one of `Resolved` /
  `NeedAttrs(missing, options)` / `Ambiguous(candidates)` / `Unknown(suggestions)`. The LLM does
  the fuzzy *understanding* (mishears like "multiple cannon" → Multi-Cannon); the tool
  *validates* and guides the next question. It never guesses a missing attribute — but a module
  sold in exactly one size or mount has that value *determined*, not asked.
- **Staying current with Frontier (two layers, symmetric for modules and ships).** The offline
  table is authoritative but a point-in-time snapshot, so both `modules.py` and `ships.py` are
  backed by a live, fail-soft startup index (`module_index.py` / `ship_index.py`) that harvests
  the names Spansh currently knows and folds any the bundle is missing into `resolve(...,
  extra_names=…)`. A brand-new module/ship is then findable with **no code change**; if Spansh is
  unreachable the index is empty and the bundle is in charge. A live-learned *module* has no known
  size/mount, so it searches by name only until the EDCD CSV is regenerated
  (`scripts/gen_module_taxonomy.py`) — the refresh upgrades it to full size/mount guidance.
  Curated aliases and ambiguous-family disambiguation stay hand-maintained either way.
- **Confirmation is configurable (`[nav].require_confirmation`, default OFF).** By default a
  fully-resolved module searches immediately — this is a read-only lookup, so the extra
  "confirm" turn is friction (on-hardware testing showed Haiku just self-confirms it anyway).
  When turned ON, a real **turn-gate** (mirroring the keybind safety layer — `new_turn()`
  driven by `app.py`) enforces it: a `confirmed=true` call is refused unless it arrives on a
  Commander turn *after* the resolve, so the model can't arm-and-confirm in one turn. The tool
  schema + description are generated per mode so the model's instructions match the behavior.
  Verbal "cancel/never mind" is an LLM-recognized intent (the model just stops calling the
  tool) — separate from the hard PTT-cancel.
- **Spansh quirks (verified live 2026-07, cross-checked against EDDiscovery / corenting-ED-API /
  RatherRude-ED-AI)** — drove the request/parsing design: the station-search POST is
  *synchronous* (returns `results` directly, no job/poll despite the shareable `/search/<uuid>`
  URLs and the separate `search/save`+`search/recall` variant; EDDiscovery reads `results`
  straight off the POST too). The module filter honours only `name`/`class`/`rating` —
  `ed_symbol`, `weapon_mode`/`mount`, and the top-level `landing_pad` key are **silently ignored**
  (a bogus value returns everything). So **mount** can't be server-filtered and is post-filtered
  from each result's full `modules` list (`weapon_mode`). **Pad**, however, *is* filterable via the
  boolean `has_large_pad`/`has_medium_pad`/`has_small_pad` filters (`{"value": true}` — the form
  EDDiscovery uses), so the pad constraint is pushed server-side (client check kept as a backstop).
  **Fleet carriers** ("Drake-Class Carrier") are dropped from results — they sell modules but jump
  around, so they're a stale "nearest station" answer (both EDDiscovery and RatherRude exclude them;
  in one live sample 11 of 30 nearby results were carriers). `distance` is ly from `reference_system`;
  results are pre-sorted ascending, so the first surviving result is the nearest. An unknown
  `reference_system` → HTTP 400 (generic body).
- **Current system**: live `EDContext.system`, falling back to the newest journal's last
  jump/location when monitoring is off. **Clipboard**: `clip.exe` (built-in; no `pyperclip`
  dep — payload is an ASCII system name). Both, plus the HTTP poster, are **injected** so the
  default `pytest` stays hermetic; a recorded Spansh fixture drives the parse/nearest tests.
- **EDSM fallback** (design note) is stubbed via config intent but not yet wired — Spansh is the
  sole live source for now; a lookup failure fails soft (spoken, never crashes the loop).

### Implemented — find-closest-ship (shares `[nav]`)
Voice shipyard search: "find the closest station that sells SHIP X." The direct sibling of
find-closest-module, built on the identical LLM-native, stateless pattern (`covas/nav/ships.py`
+ `ship_search.py` + `FindClosestShipCapability`). It resolves the ship **conversationally**
against a bundled offline roster, then finds the nearest station selling it and copies the
SYSTEM name to the clipboard. Shares the `[nav]` config (pad default, base URL, enable) and the
whole `search/spansh.py` transport — no duplicated plumbing.

- **Bundled ship roster (`ships.py`)** — the 48 canonical Spansh ship names (harvested live from
  station `ships` arrays, 2026-07), each with aliases for short names / STT mishears ("conda" →
  Anaconda, "fdl" → Fer-de-Lance, "clipper" → Imperial Clipper). `resolve_ship(query)` is pure/
  offline and returns `ResolvedShip` / `AmbiguousShip(candidates)` / `UnknownShip(suggestions)`.
  There's **no NeedAttrs** step (ships have no size/mount) and **no confirmation gate** — a ship
  is a single unambiguous decision, so a resolved ship searches immediately.
- **Genuine families ASK, never guess.** Bare "krait" → Krait MkII vs Phantom; "cobra" → MkIII/
  MkIV/MkV; "viper" → MkIII/MkIV; "asp" and "diamondback" → Explorer/Scout; "type" → Type-6…11.
  A discriminator ("krait phantom", "type 9") resolves directly. Modelled as an explicit family
  table checked before substring/fuzzy matching, so disambiguation is deterministic + testable.
- **Spansh `ships` filter quirks (verified live 2026-07)** — the station `ships` name filter IS
  honoured server-side (unlike the module *mount* key), and is CASE-SENSITIVE exact-match: "Krait
  Mk II" and "anaconda" both return **zero**, and an unknown name returns zero (not everything).
  That's exactly why resolution to the exact canonical name happens offline before the search
  fires. There's no variant to post-filter (a belt-and-braces `_sells_ship` guard over each
  result's `ships` list stays, and reads back the ship's PRICE for the spoken line).
- **Carriers excluded SERVER-SIDE.** Ships are stocked at far fewer stations than modules, and
  near populated space fleet carriers are ~95% of nearby shipyards — dropping them only
  client-side would blow the whole search window on carriers and return nothing. So the request
  constrains `type` to the non-carrier station types (`search/stations.STATION_TYPES`, which omits
  Drake-Class Carrier); the client-side `is_fleet_carrier` check stays as a backstop.
- **Routing** lives in the tool descriptions (no classifier): "where can I buy a SHIP" → this tool;
  a MODULE → find_closest_module; a station by SERVICE/TYPE/PAD → search_stations. Current-system,
  clipboard, and HTTP are injected exactly as find-closest-module, and a recorded ship-query
  fixture drives the offline parse/nearest tests.
- **Staying current as Frontier adds hulls (`ShipIndex`).** The bundled roster is a point-in-time
  snapshot, and the exact-match `ships` filter means a hull we don't know the exact name of is one
  we refuse to search for — so a new release would otherwise be unfindable until a code change. A
  `ShipIndex` (a direct mirror of `search/faction_index.py`: lazy, cached, fail-soft) reconciles
  the bundle against Spansh's live shipyard data — Spansh has no ship reference endpoint (verified
  404), so the source of truth is its own shipyards, harvested around Shinrarta Dezhra (the hub
  stocks the full roster). `app.py` kicks the reconciliation on a **background startup thread** (so
  the first query never pays the fetch latency, and the voice loop is never blocked); resolution
  folds any newly-learned names in via `resolve_ship(query, extra_names=…)`, falling back to the
  bundle until/if the fetch lands, and logs any hulls newer than the bundle. Only NAMES self-update
  — aliases and ambiguous-family disambiguation stay curated in `ships.py` (a new hull resolves by
  exact/fuzzy name but won't get a nickname or join a "which one?" family until edited). A free
  `integration+local` canary (`test_live_ship_index_harvest_covers_the_bundle`) fails if the live
  roster ever drifts far from the bundle, prompting a curated top-up.

### Implemented — search data freshness + local shipyard ground truth
Born from a live bug (2026-07): COVAS recommended a Type-8 at the very station the Commander was
docked at, while the vendor showed "does not currently stock this ship." Two findings, two layers
of fix — both hardcoded policy, deliberately not configurable:

- **Staleness filter (all volatile searches).** Spansh's crowdsourced records update only when
  someone running an EDDN uploader visits, and stock/BGS facts go stale underneath them. Volatile
  searches now constrain the matching `*_updated_at` **server-side** (a date-only `<=>` range —
  datetime strings are rejected with 400; verified honoured live on `shipyard_updated_at`,
  `outfitting_updated_at`, and systems `updated_at`), with a client-side `is_fresh` backstop.
  Policy in `search/spansh.py`: **stock 2 days** (ships, outfitting), **BGS states 7 days**
  (minor-faction/misc searches, only when a state slot is in play). Structural searches (station
  types/services, system attributes, faction presence) are deliberately unfiltered — a Coriolis
  doesn't stop existing because nobody docked lately. When nothing fresh matches, ONE retry
  without the window answers from stale data **spoken with an age caveat** ("Fair warning — that
  listing is N days old…"), so sparse space still gets an answer instead of a dead end.
- **Local `Shipyard.json` stock veto (ships).** The deeper finding: Spansh's per-station `ships`
  array is the station's **CATALOG, not its stock** — a minutes-fresh record listed 18 ships at a
  station whose own `Shipyard.json` PriceList (written when the shipyard is opened; the in-stock,
  credit-purchasable list) held exactly one. So no freshness makes a Spansh listing prove
  buyability. The one ground truth available is the Commander's own last shipyard visit:
  `ed/shipyard.py` reads it fail-soft, and `nav/ship_search.py` **vetoes** a candidate only on
  positive local evidence (same station by MarketID/name+system, snapshot ≤ the 2-day stock
  window, hull's symbol absent from the PriceList), skipping to the next-nearest with a spoken
  reason.
- **EDSM current-stock check (ships).** The "remote stations are unverifiable" ceiling turned out
  to be false — proven by a second live bug (2026-07-11, the Type-10 from Diaguandri): a fresh
  Spansh record listed 34 ships at Laplace Ring / Balante; the real stock was 16 (no Type-10, no
  Chieftain). Both Inara and **EDSM** store the **latest EDDN shipyard message** — the actual
  purchasable list — and agree with each other byte-for-byte, so EDSM's free per-station GET
  (`api-system-v1/stations/shipyard`) is an Inara-grade stock oracle. `nav/edsm_stock.py` wraps
  it (injected GET seam, normalized names — EDSM writes "Krait Mk II", Spansh "Krait MkII");
  `nav/ship_search.py` walks Spansh's distance-sorted candidates and answers the **nearest one
  EDSM confirms in stock** (≤ 12 checks/pass, memoized across the stale-fallback pass),
  vetoing contradicted candidates with a spoken reason (`skipped_stock`). Absence of EDSM data
  is never treated as absence of stock — an unconfirmable answer is spoken **with a caveat**
  (`stock_unverified`), and a dead EDSM degrades to the old behavior (fail soft). Verified live:
  the Type-10 and Chieftain answers now match Inara's own nearest-seller search #1 exactly
  (Stronghold Carrier / Ebor, 19.3 ly). Kill switch: `[nav].verify_stock`.
- **Seam left open:** outfitting could adopt the same two checks via `Outfitting.json` and
  EDSM's outfitting endpoint if module stock proves similarly catalog-shaped; the shared
  plumbing is in place.

---

## 6. Keybind automation (future phase — sketch)

Hands-off keybind automation — pressing your bound keys to do things. Genuinely useful but the twitchy part, so isolate it hard behind a capability with a safety layer.

- **Read bindings, don't hardcode keys.** ED stores bindings as XML in `%LOCALAPPDATA%\Frontier Developments\Elite Dangerous\Options\Bindings\Custom.4.0.binds`. Parse it to map an *action* (e.g. `LandingGearToggle`, `HyperSuperCombination`, `SetSpeed100`) to the physical key the Commander bound. The app targets actions; the binds file resolves keys. This is what makes it portable across setups.
- **Injection gotcha.** ED often ignores plain virtual-key events; reliable input usually needs **scancode-level `SendInput`** (DirectInput-style), and timed press/hold/release for things like "hold to charge FSD." Budget for this being fiddly — reliable key injection into ED is finicky work. Prototype one action (toggle landing gear) end-to-end before generalizing.
- **Macros over single keys.** Real tasks are sequences with waits and state checks ("launch": request docking→wait→boost→retract gear). Model these as small scripted macros that can *read Status.json between steps* to verify state instead of firing blind. The log watcher (§5) is what makes automation non-blind.
- **Safety.** Confirmation for consequential actions, a hard global abort, an allowlist of permitted actions, and a "never during combat/interdiction" guard from Status flags. Keep it strictly opt-in.
- **LLM as intent layer, not button-masher.** Claude/Qwen decides *which* macro matches the spoken request; a deterministic executor runs the keystrokes. Don't let the model synthesize raw key sequences.

Because it's a capability behind the registry and driven by the same event bus + context, this can land much later without disturbing the rest.

### Implemented — one-action prototype (`[keybinds]`, default off)
The single-action proof from this sketch is built (`covas/keybinds/` + `KeybindCapability`),
proving toggle-landing-gear end-to-end before any generalization:

- **`binds.py`** resolves the **active** preset by name — reads `StartPreset.4.start`
  (fallback `StartPreset.start`), loads `<preset>.4.0.binds` — rather than globbing `*.binds`
  and guessing (stale/default preset files commonly sit alongside the real one). It parses the
  XML and, per action, extracts the **keyboard** binding specifically (the Primary/Secondary
  slot with `Device="Keyboard"`, Primary preferred), capturing keyboard modifiers. A
  joystick-only or unbound action is marked *unusable* with a "bind it to a key in-game"
  message. `[keybinds].binds_file` overrides auto-detection.
- **`executor.py`** injects via scancode-level `SendInput` (`KEYEVENTF_SCANCODE`) — ED ignores
  plain virtual-key events — with press / hold(duration) / release + a `release_all()` used by
  the hard abort. The `SendInput` call sits behind an injectable backend so the whole path is
  unit-tested with a recording fake; `scancodes.py` is a pure ED-token→scancode map.
- **`KeybindCapability`** exposes exactly one macro (`toggle_landing_gear`) behind the safety
  layer: **allowlist** (only `[keybinds].allowlist` macros are advertised/run), **explicit
  confirmation** (arming never fires; the Commander confirms via `confirm_keybind` on a
  *separate* command — turn-gated by an `app`-driven `new_turn()` so the model can't
  arm-and-confirm in one turn, with a `confirm_window` expiry), a **combat/interdiction guard**
  (refuses when ED Status reports danger/interdiction, and when status is unavailable it can't
  prove it's safe → also refuses; re-checked at confirm time), and a **hard abort**
  (`abort_keybinds` clears pending + `release_all()`). The LLM only *selects* the named macro;
  the executor runs deterministic keystrokes. The guard reads two new `EDContext` flags
  (`in_danger`, `being_interdicted`) folded from Status.json.

Next actions stay gated behind a go/no-go after on-hardware validation of this one.

### Implemented — auto-honk (`[honk]`, default off, N5)
The second keybind-driven action, and the first PROACTIVE one — fire the Discovery Scanner
("honk") on arrival in a new system, no button press (`covas/capabilities/honk_capability.py`).
It's an **ambient** capability like route callouts (no LLM tools): it subscribes to the bus and
reacts to the journal's `FSDJump`. It reuses the keybind executor + safety layer:

- **Sequence.** *Configured* (a scanner `fire_group` index + `trigger` are set): read the
  CURRENT fire group from `Status.json` (folded into `EDContext.fire_group`), compute the exact
  cycle to the scanner group (`cycle_plan(current, target)` — deterministic `|delta|` steps of
  `CycleFireGroupNext`/`Previous`, so no total-group-count guess and no wrapping), HOLD the
  configured `PrimaryFire`/`SecondaryFire` for `hold_seconds` (~6s), then cycle back. *Not
  configured* (`fire_group = -1`): the accepted fallback — just hold primary fire without cycling.
- **Safety (reused).** The combat/interdiction guard (`combat_state` + guard messages from the
  keybind capability) refuses during danger/interdiction AND when ED status is unavailable
  (can't prove safe). If cycling is needed but the current group is unreadable or a cycle bind is
  missing, it REFUSES rather than risk holding fire in the wrong group (which could fire weapons).
  The **shared** `KeyExecutor` (one instance for keybinds + honk) means the hard abort
  (`abort_keybinds` -> `release_all()`) lifts the held fire key; the executor also clamps hold
  duration. Every honk and skip is logged.
- **Non-blocking.** The ~6s hold runs on a spawned daemon thread (injected `spawn`) so it never
  blocks the event pump; a non-blocking lock drops a second honk while one is in progress.
- Off by default; needs `[elite].enabled` for the arrival event, the fire group, and the guard.
  Everything (binds, executor, status snapshot, spawner) is injected, so the whole sequence is
  unit-tested offline with a recording fake executor.

---

## 7. Build status & roadmap

### Built and merged (on `main`)
The original seven-phase plan is done and tested:

1. **Cost instrumentation & guardrails** — overrides fix, prompt caching (+1h TTL), Sonnet default, `max_tokens` cap, per-turn usage/cost logging, dev-mock, the unit/integration test harness (§9).
2. **Provider seam + capability registry** — `providers/` (Anthropic/ElevenLabs/Whisper behind Protocols; Ollama offline-only), `CapabilityRegistry`, checklist relocated to a capability.
3. **Cloud tiering router** (§4).
4. **ED monitoring** — journal + Status watchers, `EDContext`, read-tool + inline context delivery (§5).
5. **Proactive callouts** — `ProactiveCapability` (§5).
6. **Keybind automation** — one-action prototype behind the safety layer (§6).
7. **Outfitting voice search** — `find-closest-module` (§5, §3.5).
8. **Voice search & help subsystem** — templated `HelpCapability` (idle + failure-recovery) on one unified registry with a structural help-metadata contract; a shared typed Spansh client (`search/spansh.py`, outfitting refactored onto it); and five LLM-native category capabilities — star systems, stations, minor factions, signals, misc — with offline fuzzy resolution for factions/systems (§3.5). *(Search Prompts 1–6 merged, including the voice-polish pass: refinement re-query, error-mode wiring, low-confidence confirmation.)*
9. **Settings** — one schema as source of truth, projected to both the web settings page (N1) and the voice `SettingsCapability` (N2).
10. **Location & carrier commands** (N3) — copy current system; owned-carrier tracking pinned to `CarrierID`; the "already there → don't copy" rule.
11. **Route callouts** (N4) — scoopable-star + jumps-remaining via the proactive path.
12. **Auto-honk** (N5, §6).
13. **Community Goals** (N6) — journal-primary with the Inara feed for completeness.
14. **Personality tab, voice speed & log filter** (N7).
15. **Find-closest-ship** (N8, §5) — including the self-updating `ShipIndex` roster.
16. **"Copy that to my clipboard"** (N11) — one LLM-native `copy_to_clipboard(text, label?)` tool; the model resolves "that" from conversation; explicit request copies even in the current system.
17. **Search data freshness + local shipyard ground truth** (§5) — the staleness filter on volatile Spansh data and the `Shipyard.json` stock veto, from the live Type-8 bug.
18. **Ship loadout & engineering** (N9) — the full journal `Loadout` snapshot on `EDContext`, offline symbol→spoken-name mapping (`ed/module_names.py`), and a `LoadoutCapability` answering "what's on my FSD" / experimental effects / the fitted rundown, with upgrade suggestions offered onto the checklist.
19. **EDSM current-stock verification for ships** (§5) — every ship-search candidate confirmed against EDSM's live shipyard snapshot (the same data Inara shows) before being spoken, from the live Type-10 bug; answers now match Inara's nearest-seller search.
20. **Web checklist editor** (N10) — a `/checklist` tab rendering `ultimate_checklist.md` as WYSIWYG markdown (TOAST UI from CDN, dark theme, first-class task lists; plain-textarea fallback when the CDN is unreachable). Saves round-trip losslessly through `checklist.py` (editor `* [ ]` bullets normalized back to `- [ ]`, nesting preserved); voice and web share the file (reads are per-call fresh, the cursor is clamped on save), and a content-hash stale-write guard 409s a save when a voice edit landed underneath, offering reload-vs-overwrite instead of clobbering.

### Backlog
Prompts 1–7, Search 1–6, and N1–N11 are all built and merged. **Outstanding: the Audio / Comms / Chatter subsystem (C1–C8)** in `CLAUDE_CODE_PROMPTS.md` — the atmospheric audio layer (multi-bus mixer + per-bus DSP, cue registry, game-state driver, the fail-closed `ReceiveText` channel gate, comms variants, space chatter, music crossfade, and worked-example cues). None of it is implemented yet; ordering is infra → registry → driver → the safety-critical C4 gate → variants → chatter → music → example cues. New work otherwise starts as a new prompt in the pack (one branch per prompt, one fresh session each). **The prompt pack carries the live worklist; this doc carries the architecture.**

---

## 8. Watch-items / risks

- **Tier quality feel.** Haiku-by-default changes the companion's voice/reasoning feel vs. Sonnet/Opus. A/B on real sessions and tune the escalation rules; keep the manual override so you can force a tier when it matters.
- **Reply truncation.** A low `max_tokens` can cut off a genuinely long answer mid-sentence — bad over TTS. Let the Router raise the cap for explicit "full breakdown" turns rather than setting it too low globally.
- **Journal rollover & partial lines.** Tailing must handle new-file rollover and the occasional half-written final line (retry-on-parse-fail).
- **Key injection into ED.** Expect scancode/`SendInput` work; validate with the one-action prototype.
- **Secret hygiene.** `ElevenLabsAPIKey.txt` is git-ignored and now outside OneDrive; keep it that way. An env var would be marginally safer still.
- **Provider abstraction creep.** Keep the interfaces tiny (1–2 methods). The moment they grow provider-specific params, the abstraction stops paying for itself.

---

## 9. Testing strategy — fast unit tests, opt-in integration

The rule: **the default test run is free and hermetic.** `pytest` runs *unit tests only* — no network, no API calls, no ElevenLabs, no Ollama, no audio hardware — so you can run it on every save without touching your accounts. Anything that talks to a real service is an *integration* test, marked and excluded from the default run.

### Layers
- **Unit (default — run constantly).** Pure logic and wiring with all I/O faked: router decisions, journal/`Status.json` parsing + `Flags` decode, checklist ops, config resolution, tool-JSON validation/repair, `_build_kwargs`/cache-control construction, event-stream normalization. Fast (<1s), deterministic, offline.
- **Integration — local (opt-in, free).** Real but no-cost dependencies: Ollama, Piper, Whisper, audio devices. Marked `@pytest.mark.integration` + `@pytest.mark.local`. Run when you touch those paths.
- **Integration — paid (opt-in, deliberate).** Real Anthropic / ElevenLabs calls. Marked `@pytest.mark.integration` + `@pytest.mark.paid`. Run rarely and on purpose — cheapest model, one-line prompt. Never in the default run or a pre-commit hook.

### How the seam makes this cheap
The provider seam *is* the dependency-injection boundary. Build real providers only at the composition root (app entry, via `factory`); everything downstream receives providers as arguments:
- `App(cfg, *, llm=None, tts=None, stt=None)` — `None` means "build the real one from config via the factory"; unit tests pass **fakes** instead.
- `tests/fakes.py` provides `FakeLLM` (yields scripted `("text", …)` chunks and optional tool calls), `FakeTTS` (records calls, plays nothing), `FakeSTT` (returns canned text). They satisfy the same Protocols in `providers/base.py`, so nothing else changes.
- The same fakes power the **dev-mode mock** (§1) for running the app by hand for free.

### Guardrails
- `pyproject.toml` registers the `integration` / `local` / `paid` markers and sets `addopts = "-m 'not integration'"`, so a bare `pytest` is unit-only. *(Done.)*
- A unit-test `conftest.py` fixture blocks the network (monkeypatch `socket.socket`) so an accidental real call fails loudly instead of billing you.
- Commands: `pytest` (unit) · `pytest -m "integration and local"` (free) · `pytest -m "integration and paid"` (costs money). Pre-commit/CI, if added, runs unit only.
