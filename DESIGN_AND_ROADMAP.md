# COVAS++ — Design & Roadmap

*Working design doc. Treat the current app as a light MVP; this describes where it goes next and the principles for getting there without repainting the whole thing each time.*

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

Each step is independently shippable and testable.

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

ED continuously writes game state to disk — the same source EDCopilot, EDMC, and EDDN read. No memory reading, no API keys.

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
- **Proactive callouts** (opt-in) — the app *initiates* speech on key events (arrival, mission complete, near-death), routed through TTS without a PTT press. Gate behind a config toggle and a cooldown so it isn't chatty.
- **Cheap local answers** — "where am I / what's my cargo / how's my fuel" become zero-cost local reads from context, no LLM round-trip needed for the trivial ones.

Design the watcher to publish events **only**; capabilities decide what to *do* with them. That keeps monitoring reusable for both conversation grounding and future automation.

### Context delivery — decided (inline injection, not the cached system prompt)
The "feed it into the system prompt (cached!)" note above turned out to be a cache **anti**-pattern: the prompt cache breakpoints sit on the personality block *and* the last tool, so anything added to `system` lives inside the cached prefix — a context line that changes as you fly would bust the tools cache every turn (the exact re-send cost we're trying to kill). Two things resolve it:

- **`EDContextCapability` exposes read tools** (`where_am_i`, `ship_status`, `recent_events`) — cache-safe, and the model calls them on demand. These are also the "cheap local answers" above (answered from context, no game knowledge needed).
- **A rules-based `ContextDetector`** (mirrors the cost Router) classifies each turn: does it reference current status or recent activity? When it does, `app.py` prepends a compact **`context_block()`** — current status, plus the recent-events feed for a "what just happened" turn — to **that turn's user message only**. It's uncached by design (tiny, ~30–60 tokens, only on matched turns) and never stored in history, so stale telemetry can't accumulate. An explicit **"context" wake word** forces a lookup and is scrubbed from what the model sees.

Net: the model answers from real state in one shot (no tool round-trip) on the common "where am I / how's my fuel / check my logs" turns, the prompt cache stays intact, and off-topic turns pay nothing. The `system_context()` hook remains on the capability for a future carefully-cached use, but is not wired into the request.

The **recent-events feed** is a small rolling buffer on `EDContext` (bounded, `[elite].recent_events_kept`), fed by both watchers via curated describers — narrative events from the journal (jumps, docks, missions, deaths), fuel/heat alerts from Status flags — with journal-spam (auto-scans, fuel-scoop ticks, bounties) filtered out. Priming warms it from the tail of the current journal so "what did I just do" works right after launch.

---

## 6. Keybind automation (future phase — sketch)

The EDCopilot-style "press buttons to do stuff." Genuinely useful but the twitchy part, so isolate it hard behind a capability with a safety layer.

- **Read bindings, don't hardcode keys.** ED stores bindings as XML in `%LOCALAPPDATA%\Frontier Developments\Elite Dangerous\Options\Bindings\Custom.4.0.binds`. Parse it to map an *action* (e.g. `LandingGearToggle`, `HyperSuperCombination`, `SetSpeed100`) to the physical key the Commander bound. The app targets actions; the binds file resolves keys. This is what makes it portable across setups.
- **Injection gotcha.** ED often ignores plain virtual-key events; reliable input usually needs **scancode-level `SendInput`** (DirectInput-style), and timed press/hold/release for things like "hold to charge FSD." Budget for this being fiddly — it's the same reason EDCopilot can feel kludgy. Prototype one action (toggle landing gear) end-to-end before generalizing.
- **Macros over single keys.** Real tasks are sequences with waits and state checks ("launch": request docking→wait→boost→retract gear). Model these as small scripted macros that can *read Status.json between steps* to verify state instead of firing blind. The log watcher (§5) is what makes automation non-blind.
- **Safety.** Confirmation for consequential actions, a hard global abort, an allowlist of permitted actions, and a "never during combat/interdiction" guard from Status flags. Keep it strictly opt-in.
- **LLM as intent layer, not button-masher.** Claude/Qwen decides *which* macro matches the spoken request; a deterministic executor runs the keystrokes. Don't let the model synthesize raw key sequences.

Because it's a capability behind the registry and driven by the same event bus + context, this can land much later without disturbing the rest.

---

## 7. Suggested phase order

1. **Bank cost wins** *(done)* — overrides fix, caching, Sonnet default, `max_tokens` cap + optional knobs from §1.
2. **Provider seams + registry** (§3.4 steps 1–4). No behavior change; unlocks everything else. Safe checkpoint.
3. **Cloud tiering router** (§4). Haiku default → Sonnet/Opus escalation, rules-based with wake-phrase overrides, usage logging. This is where recurring cost really drops. (Optionally flip TTS to Piper here.)
4. **ED log monitoring** (§5). High value on its own, improves replies immediately, and is a prerequisite for non-blind automation.
5. **Proactive callouts** (§5) once monitoring + TTS are stable.
6. **Keybind automation** (§6), one action at a time.

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
