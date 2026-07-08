# COVAS++ — Design & Roadmap

*Working design doc. Treat the current app as a light MVP; this describes where it goes next and the principles for getting there without repainting the whole thing each time.*

Priorities agreed: **modular refactor**, **cut API costs** (both quick cloud wins *and* a local/cloud hybrid), and **Elite Dangerous log monitoring**. Keybind automation is a later phase, sketched here so the architecture leaves room for it.

---

## 1. Cost: what's already done, what's left

### Done now (safe, in-code)
- **Prompt caching** (`covas/llm.py`): the personality system prompt and the tool schemas are now sent with an ephemeral `cache_control` breakpoint. These are static within a session, so Anthropic serves them from cache at ~90% off input price instead of re-billing ~8 KB of preamble on every turn and every tool-loop round.
- **Cheaper default model** (`config.toml`): default switched `claude-opus-4-8` → `claude-sonnet-5`. Biggest single lever. Fully reversible in one line or via the UI dropdown; Opus is still available per-session.
- **Fewer web searches** (`config.toml`): `web_search.max_uses` 5 → 3. Each search result is pulled into context and then persists in rolling history, so it inflates the cost of *every following turn*, not just its own.

### Still available (your call — knobs, not rewrites)
- **`conversation.max_turns = 20`** means up to 40 messages are resent each turn. With the preamble now cached, history is the main variable cost. Dropping to ~10 halves it on long sessions, at the price of shorter memory.
- **Reply brevity.** ElevenLabs bills per character and Anthropic per output token, so a "be concise unless asked to elaborate" instruction saves on *both* APIs at once. Best added as an optional toggle rather than by editing `personality.txt` (which is loaded as-is).
- **Dev-mode mocking.** During development, a flag that returns canned LLM/TTS responses (or plays cached WAVs) stops test runs from spending real dollars. Cheap to add, big savings while iterating.

---

## 2. Design principles

1. **Provider-agnostic core.** The voice loop should know it needs "an LLM reply" and "speech from text," not *which* service produces them. Anthropic, Ollama/Qwen, ElevenLabs, and Piper sit behind small interfaces.
2. **Policy separate from mechanism.** *How* to call a provider (mechanism) is isolated from *which* provider to use for a given request (policy/router). Cost routing changes only the policy.
3. **Capabilities as plugins.** Checklist, ED-log monitoring, and keybind automation are self-contained capability modules that register their tools and event handlers with the core, rather than being wired into `app.py`.
4. **Event bus as the spine.** You already have `EventBus`. Make it the one-way nervous system: inputs (voice, ED journal, timers) publish events; capabilities and the UI subscribe. This is what keeps new features additive.
5. **Typed settings over raw dicts.** Every module currently reaches into `cfg["section"]["key"]`. Introduce small typed settings objects so a mis-key fails loudly at load, and providers receive only their slice.
6. **Fail soft, stay live.** The current code already swallows errors to keep the loop alive — preserve that. A dead TTS provider should degrade to text, not crash the session.

---

## 3. Target architecture

Think of it as three layers over the event bus.

```
            ┌───────────────────────────── EventBus ─────────────────────────────┐
            │  status • log • ed_event • timer • settings                        │
            └───────────────────────────────────────────────────────────────────┘
 INPUTS                    CORE / ORCHESTRATION                 PROVIDERS
 ─────────                 ────────────────────                 ─────────
 PTT + mic ──► STT ──►  Conversation loop  ──► Router ──► LLMProvider  {anthropic, ollama}
 ED journal ─► Watcher       │  (app.py)              └─► TTSProvider  {elevenlabs, piper}
 timers ─────► Scheduler     │                            STTProvider  {faster-whisper, ...}
 UI/web ─────────────────────┘
                             │
                     CapabilityRegistry
                     {checklist, ed_context, keybinds…}  ──► tools + event handlers
```

### 3.1 Provider interfaces
Three tiny protocols. Each has 1–2 methods; existing code becomes the first implementation of each.

- **`LLMProvider.stream_reply(messages, cfg, cancel, on_event, tool_handler) -> Iterator[(kind, chunk)]`** — exactly today's `llm.stream_reply` signature. `AnthropicProvider` wraps the current file unchanged. `OllamaProvider` is new (see §4). The tool-call protocol differs per provider, so each provider owns its own tool loop and normalizes to the same `("text"|"thinking"|"search"|"tool", data)` event stream the app already consumes.
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
4. Add the `Router` returning "always cloud" initially. Now the structure exists with zero functional change — a safe checkpoint.
5. Land Ollama/Qwen + Piper providers and turn on real routing (§4).
6. Add the ED journal watcher as an input + `EDContextCapability` (§5).

Each step is independently shippable and testable.

---

## 4. Hybrid local/cloud strategy

**Goal:** keep the cloud for what it's uniquely good at (nuanced conversation, current-events search, hard reasoning) and push everything routine to local Qwen 3.6 (Ollama) + Piper, which are effectively free after setup.

### Routing policy (first cut)
Route **local** by default; escalate to **cloud** when any escalation trigger fires.

- **Local (Qwen + Piper) handles:** short conversational replies, checklist reads/updates, status readouts ("what's my next objective," "am I docked"), acknowledgements, and anything answerable from ED context already in the prompt.
- **Escalate to cloud (Sonnet + ElevenLabs) when:** the request needs web search (current prices, system data the app doesn't have locally); the user explicitly asks for depth/analysis; the local model returns low confidence or an ill-formed tool call; or the user says a wake phrase like "ask the big brain."

### How to decide, in order of preference
1. **Cheap classifier first.** A fast local pass (Qwen with a terse routing prompt, or even keyword/intent rules) tags the request `local` vs `cloud`. Rules catch the obvious cases for free; the model handles the rest.
2. **Local attempt with escalation.** Try Qwen; if it emits an "I need to search" signal, an invalid tool call, or a self-flagged low-confidence marker, transparently re-run on cloud. Costs latency on the fallback path only.
3. **Explicit override.** Wake phrases and a UI toggle force a tier, so you're never at the mercy of the classifier.

Start with (1)+(3) — deterministic and debuggable — and add (2) once the provider seam is stable.

### TTS routing
Piper for everything by default (local, instant, no per-character cost). Reserve ElevenLabs for a "premium voice" toggle or specific moments. Because both providers emit the same PCM, switching is a router decision, not a code change. Note the voice character differs — Piper voices are good but not ElevenLabs-smooth; worth an A/B once wired.

### Practical notes
- **Ollama** exposes an OpenAI-compatible `/api/chat` with streaming and (model-dependent) tool-calling. Qwen supports tool calls, but formatting is less reliable than Claude's — keep tool schemas small and validate/repair tool JSON in the `OllamaProvider`. This is why each provider owns its own tool loop.
- **Model warmup:** keep Ollama's model resident (`keep_alive`) so first-token latency doesn't spike mid-flight.
- **Context discipline:** local models have smaller effective context and get slower with history — the `max_turns` trim matters more on the local path; consider a tighter local cap.

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

1. **Bank cost wins** *(done)* + optional knobs from §1.
2. **Provider seams + registry** (§3.4 steps 1–4). No behavior change; unlocks everything else. Safe checkpoint.
3. **ED log monitoring** (§5). High value on its own, improves replies immediately, and is a prerequisite for non-blind automation.
4. **Local hybrid** (§4). Ollama/Qwen + Piper providers, rules-based router, wake-phrase overrides. This is where the recurring cost really drops.
5. **Proactive callouts** (§5) once monitoring + TTS routing are stable.
6. **Keybind automation** (§6), one action at a time.

---

## 8. Watch-items / risks

- **Local tool-calling reliability.** Qwen's tool JSON is less consistent than Claude's — validate and repair in the provider; keep schemas minimal.
- **Latency vs. quality feel.** Local is cheaper but the voice/reasoning feel changes. A/B Piper vs ElevenLabs and Qwen vs Sonnet on real sessions before committing defaults.
- **Journal rollover & partial lines.** Tailing must handle new-file rollover and the occasional half-written final line (retry-on-parse-fail).
- **Key injection into ED.** Expect scancode/`SendInput` work; validate with the one-action prototype.
- **Secret hygiene.** `ElevenLabsAPIKey.txt` sits in a OneDrive-synced folder in plaintext — fine for you to decide, but worth noting; env var or a local-only path would be safer.
- **Provider abstraction creep.** Keep the interfaces tiny (1–2 methods). The moment they grow provider-specific params, the abstraction stops paying for itself.
