# Claude Code prompt pack — COVAS++

Sequenced, copy-paste prompts for building COVAS++ with Claude Code. Do them **in
order**; each is a self-contained, independently shippable increment on its own branch.
**Paste one prompt per fresh Claude Code session** (or `/clear` between them) — don't
reuse a session across prompts, so stale context doesn't leak in.

> **Direction (current):** local LLMs are out — a useful model fights Elite Dangerous for
> the GPU. Cost is handled by **cloud tiering** (Haiku → Sonnet → Opus). Local **Piper
> TTS** and **Whisper STT** stay viable (light CPU) and are the one place local saves
> money. The `OllamaLLM` code in the tree is for offline/out-of-game use only; it is not
> part of the in-game path.

Ground rules baked into every prompt (also in `CLAUDE.md`):
- Read `CLAUDE.md` and `DESIGN_AND_ROADMAP.md` first.
- Public repo — never commit secrets or personal data.
- Keep provider interfaces tiny; add features as capabilities, not `app.py` branches.
- **Tests: unit by default, integration opt-in.** Bare `pytest` must stay offline and free
  (no network/API/ElevenLabs/Ollama/audio) — inject dependencies and use `tests/fakes.py`.
  Anything hitting a real service is `@pytest.mark.integration` + `local` (free) or `paid`
  (costs money), excluded from the default run. See `DESIGN_AND_ROADMAP.md` §9.
- Byte-compile; call out what needs manual on-hardware testing.
- Small commits, one increment per branch. Implement, self-review the diff, tell me
  exactly what to test by hand, and wait for my confirmation before considering it done.

---

## Prompt 1 — Cost instrumentation & guardrails

```
Read CLAUDE.md and DESIGN_AND_ROADMAP.md (§1, §4).

Goal: make cost observable and lock in the guardrails, so every later change is
measurable. (The quick wins — overrides stripped, prompt caching, Sonnet default,
max_tokens=1024, web_search max_uses=3 — are already in place; this instruments them.)

Branch: cost/instrumentation

Tasks:
1. Per-turn usage logging. After each Anthropic call, read the response usage
   (input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens)
   and log it to the session log + EventBus with a rough $ estimate. Put per-model
   $/Mtok rates in config under a new [pricing] table so the estimate is tunable.
2. Switch prompt caching to the 1-hour cache TTL (cache_control with the extended TTL),
   since in-game voice turns are sporadic and the 5-minute cache expires between them.
   Confirm system prompt + tools still cache.
3. Dev-mode mock: a [dev].mock flag (config or env) that short-circuits the LLM and TTS
   with canned text/audio so iterating on code costs nothing. Wire it in the factory.
4. Add a startup log line summarizing effective cost settings (model, thinking, max_tokens,
   web_search uses, cache TTL, mock on/off).
5. Stand up the test harness (see DESIGN §9). `tests/fakes.py` with FakeLLM/FakeTTS/FakeSTT
   satisfying the base.py Protocols; a unit-test `conftest.py` fixture that blocks the
   network (monkeypatch socket) so an accidental real call fails loudly. The markers +
   default `-m "not integration"` are already in pyproject.toml — keep bare `pytest` unit-
   only and free. The dev-mode mock from task 3 should reuse the same fakes.
6. Keep and green the existing dev tooling (tests/, pyproject.toml, requirements-dev.txt):
   `pytest` and `ruff check` must pass.

Constraints: no change to reply behavior beyond the length cap; caching stays intact; the
default `pytest` run makes ZERO network calls.

Acceptance: a session log shows per-turn token counts + estimated cost; mock mode completes
a full turn with zero API calls; bare `pytest` is green and offline (the network-guard
fixture proves it); ruff green. Give me manual test steps, then stop.
```

---

## Prompt 2 — Adopt the provider seam + capability registry in app.py

```
Read CLAUDE.md and DESIGN_AND_ROADMAP.md (§3). This is the "safe checkpoint": pure
structure, zero behavior change on the default config.

Branch: refactor/provider-seam

Goal: route app.py through covas/providers/factory instead of constructing Anthropic /
ElevenLabs / Whisper directly, and move the checklist tools into a Capability.

Tasks:
1. In covas/app.py, inject the providers for testability: `App(cfg, *, llm=None, tts=None,
   stt=None)` where None means "build the real one from config via factory.make_llm/
   make_tts/make_stt" (the composition root), and unit tests pass fakes. The Anthropic and
   ElevenLabs providers already wrap existing code, so default behavior is identical.
   Update _process() to call the injected llm.stream_reply(...) and tts.speak(...).
2. Create covas/capabilities/base.py with a Capability protocol: tools(), run_tool(name,
   input), optional on_event(event), optional system_context(). Create a
   CapabilityRegistry that aggregates tools() and dispatches run_tool().
3. Move the checklist tool schemas (CHECKLIST_TOOLS in llm.py) and the _run_tool body
   (in app.py) into covas/capabilities/checklist_capability.py. _build_kwargs should take
   tools from the registry rather than hardcoding.
4. Wire the registry into app.py and the Anthropic tool loop. Keep prompt caching intact
   (cache_control still on the last tool + system).

Constraints: no functional change with the shipped config. Keep the fail-soft guards.

Acceptance: behaves exactly as before (verify checklist add/find/complete by voice still
works — tell me how to test); a unit test drives App with FakeLLM/FakeTTS through a full
turn (no network); bare `pytest` green and offline. Stop for my review.
```

---

## Prompt 3 — Cloud tiering router (Haiku → Sonnet → Opus)

```
Read CLAUDE.md and DESIGN_AND_ROADMAP.md (§4). Depends on Prompt 2.

Branch: feature/cost-router

Goal: route each turn to the cheapest capable cloud model, escalating only when the turn
earns it — where the real recurring cost drops.

Tasks:
1. covas/router.py — a pure, unit-testable decide(text, context) -> Route(model,
   max_tokens, reason). Rules (first cut, deterministic):
   - default model = Haiku (claude-haiku-4-5).
   - escalate to Sonnet if the turn needs current/web data, asks for depth/analysis, or
     matches a wake phrase ("think hard", "ask the big brain").
   - Opus only on an explicit override ("use opus").
   - raise max_tokens for an explicit "full breakdown"-style request.
   - a manual override (wake phrase / UI toggle) pins a tier.
2. Integrate into app.py so the chosen model + max_tokens apply for that turn. IMPORTANT:
   AnthropicLLM takes the model as a per-call parameter — do NOT create a separate provider
   class per model.
3. config [router]: enable flag, the tier model ids, wake phrases. Default OFF (fixed
   Sonnet) until I turn it on, so nothing changes unless I opt in.
4. Log each routing decision (chosen model + reason) to the session log + EventBus, so I
   can tune the rules from real transcripts.
5. Optional (same branch): allow [tts].provider = "piper" as the default with ElevenLabs
   as an override, so TTS cost can drop to zero while running next to the game.

Constraints: routing is policy only — no provider logic in the router; deterministic and
explainable. Leave a clean extension point for a future cheap-classifier pass, but don't
build it yet.

Acceptance: thorough unit tests on decide() covering each rule + the override; the usage
log (Prompt 1) shows most turns landing on Haiku; manual test steps. Stop for review.
```

---

## Prompt 4 — Elite Dangerous journal + status monitoring

```
Read CLAUDE.md and DESIGN_AND_ROADMAP.md (§5).

Branch: feature/ed-monitoring

Goal: publish real game state onto the EventBus and let the companion reference it.

Tasks:
1. covas/ed/journal.py — a JournalWatcher thread that tails the NEWEST
   `%USERPROFILE%\Saved Games\Frontier Developments\Elite Dangerous\Journal.*.log`,
   handles rollover to a new file, tolerates a half-written final line (retry parse),
   parses each NDJSON line, and publishes {"type":"ed_event","event":<name>,...} on the
   bus. Journal directory configurable ([elite] section) with the standard default.
2. covas/ed/status.py — a StatusWatcher that watches Status.json, decodes the Flags
   bitfield, and publishes semantic transitions (Docked/Undocked, LandingGear, LowFuel,
   Supercruise, etc.) rather than raw spam. Maintain a rolling "current context" object
   (system, station, ship, docked?, fuel%, cargo) updated from both watchers.
3. covas/capabilities/ed_context_capability.py — system_context() returning a short
   natural-language summary of current context for the (cached) system prompt, plus simple
   read tools (where_am_i, ship_status).
4. Config toggle to enable/disable monitoring; off by default until I opt in.

Constraints: watchers publish events only and must never block the voice loop. Unit-test
the pure parsing/bitfield logic with sample journal lines and Status.json fixtures.

Acceptance: unit tests green on fixtures (offline, default run); the live-journal tail is
an opt-in `integration`+`local` test/script, not part of the default run. Do NOT wire
proactive speech yet. Stop for review.
```

---

## Prompt 5 — Proactive callouts (opt-in)

```
Read CLAUDE.md and DESIGN_AND_ROADMAP.md (§5 "What it unlocks"). Depends on Prompt 4.

Branch: feature/proactive

Goal: let the companion initiate speech on notable ED events (arrival, mission complete,
low fuel, near-death) without a PTT press.

Tasks:
1. A ProactiveCapability subscribing to ed_event on the bus, with a per-event-type
   whitelist, cooldowns, and a global mute. On a qualifying event, generate a short line
   (via the router — these should prefer the cheap tier) and speak it through the existing
   speak/cancel path, never interrupting an in-progress user turn.
2. Config [proactive]: enabled flag (default off), per-event toggles, cooldown seconds.

Constraints: respect the cancel/interrupt model; never talk over the Commander; trivially
mutable; keep it cheap (cheap tier by default).

Acceptance: tests for the whitelist/cooldown/mute logic; manual test steps. Stop.
```

---

## Prompt 6 — Keybind automation (LATER; safety-first)

```
Read CLAUDE.md and DESIGN_AND_ROADMAP.md (§6). Do NOT start until prompts 2–4 are merged.

Branch: feature/keybinds-prototype

Goal: prove ONE reliable action end-to-end before generalizing. No broad automation yet.

Tasks:
1. covas/keybinds/binds.py — parse the ED bindings XML
   (%LOCALAPPDATA%\Frontier Developments\Elite Dangerous\Options\Bindings\*.binds),
   mapping an action name (e.g. LandingGearToggle) to the physically bound key. Unit-test
   the parser against a sample .binds fixture.
2. covas/keybinds/executor.py — send that key to Elite via scancode-level SendInput
   (ED often ignores plain virtual-key events). Support press / hold(duration) / release.
3. A KeybindCapability exposing exactly ONE action (toggle landing gear) behind a safety
   layer: explicit confirmation, a hard global abort, an allowlist, and a guard that
   refuses to act during combat/interdiction (read from ED Status flags).
4. The LLM only selects a named macro; the executor runs deterministic keystrokes. The
   model never synthesizes raw key sequences.

Constraints: safety layer is non-negotiable. Everything opt-in and off by default.

Acceptance: binds parser unit-tested; a manual test where I confirm landing gear toggles
in-game. Report reliability quirks. Stop for a go/no-go before adding more actions.
```

---

### Tips for running these
- Keep `CLAUDE.md` current — Claude Code reads it every session.
- If a step balloons, ask Claude Code to split it and update this file.
- Merge (or tag) each branch before the next, so you can always roll back to a known-good
  point without losing context.
