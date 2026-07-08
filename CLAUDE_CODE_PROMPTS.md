# Claude Code prompt pack — COVAS++

Sequenced, copy-paste prompts for building COVAS++ with Claude Code. Do them **in
order**; each is a self-contained, independently shippable increment on its own branch.
Paste one prompt per Claude Code session (or `/clear` between them).

Ground rules baked into every prompt (also in `CLAUDE.md`):
- Read `CLAUDE.md` and `DESIGN_AND_ROADMAP.md` first.
- Public repo — never commit secrets or personal data.
- Keep provider interfaces tiny; add features as capabilities, not `app.py` branches.
- Byte-compile + unit-test pure logic; call out what needs manual on-hardware testing.
- Small commits, one increment per branch.

How to work each step: create a branch, implement, self-review the diff, tell me exactly
what to test by hand, and wait for me to confirm before you consider it done.

---

## Prompt 1 — Validate and harden the local POC

```
Read CLAUDE.md and DESIGN_AND_ROADMAP.md (§4 hybrid).

Goal: make the offline proof of concept (poc_local_loop.py + covas/providers/
ollama_llm.py, piper_tts.py, whisper_stt.py, factory.py) reliably runnable, and add
tests for the pure logic.

Branch: poc/local-validate

Tasks:
1. Review the four provider files and poc_local_loop.py for correctness against the
   Ollama /api/chat streaming API and the piper-tts 1.4.x API (PiperVoice.load,
   voice.synthesize -> AudioChunk.audio_int16_bytes, voice.config.sample_rate).
2. Add unit tests (pytest) for the pure logic that does NOT need hardware or servers:
   - covas/providers/ollama_llm._split_think(): <think> tag splitting, including tags
     split across streamed chunks (carry state), no-tag text, and nested/edge cases.
   - factory.make_llm / make_tts: correct class for each provider name; clear error on
     unknown names; lazy import doesn't require the other stack.
   - config path resolution (_resolve_paths): relative -> absolute under project root,
     absolute left untouched.
3. Add a pyproject.toml (or setup.cfg) configuring pytest + ruff. Add ruff and pytest to
   a new requirements-dev.txt. Do not change runtime deps.
4. Do NOT try to run Ollama/Piper/mic in CI. In the PR notes, give me the exact manual
   commands to test on my Windows machine (I have Ollama + Qwen; I'll download a Piper
   voice).

Constraints: no new runtime dependencies; keep the ollama client on `requests`.

Acceptance: `python -m py_compile` clean; `pytest` green; `ruff check` clean; a clear
manual test checklist for me. Then stop and tell me what to run.
```

---

## Prompt 2 — Adopt the provider seam + capability registry in app.py

```
Read CLAUDE.md and DESIGN_AND_ROADMAP.md (§3). This is the "safe checkpoint": pure
structure, zero behavior change on the default (cloud) config.

Branch: refactor/provider-seam

Goal: route app.py through covas/providers/factory instead of constructing Anthropic /
ElevenLabs / Whisper directly, and move the checklist tools into a Capability.

Tasks:
1. In covas/app.py, replace direct construction with factory.make_llm/make_tts/make_stt.
   The Anthropic and ElevenLabs providers already wrap existing code, so default behavior
   must be identical. Update _process() to call llm_provider.stream_reply(...) and
   tts_provider.speak(...).
2. Create covas/capabilities/base.py with a Capability protocol: tools(), run_tool(name,
   input), optional on_event(event), optional system_context(). Create a
   CapabilityRegistry that aggregates tools() and dispatches run_tool().
3. Move the checklist tool schemas (currently CHECKLIST_TOOLS in llm.py) and the
   _run_tool body (currently in app.py) into covas/capabilities/checklist_capability.py.
   llm.py's _build_kwargs should accept tools from the registry rather than hardcoding.
4. Wire the registry into app.py and the Anthropic tool loop. Keep prompt caching intact
   (cache_control still on the last tool + system).

Constraints: no functional change with the shipped config.toml. Keep fail-soft guards.

Acceptance: with [llm].provider=anthropic and [tts].provider=elevenlabs, the app behaves
exactly as before (verify checklist add/find/complete via voice still works — tell me how
to test). py_compile + existing/added tests green. Then stop for my review.
```

---

## Prompt 3 — Elite Dangerous journal + status monitoring

```
Read CLAUDE.md and DESIGN_AND_ROADMAP.md (§5).

Branch: feature/ed-monitoring

Goal: publish real game state onto the EventBus and let the companion reference it.

Tasks:
1. covas/ed/journal.py — a JournalWatcher thread that tails the NEWEST
   `%USERPROFILE%\Saved Games\Frontier Developments\Elite Dangerous\Journal.*.log`,
   handles rollover to a new file, tolerates a half-written final line (retry parse),
   parses each NDJSON line, and publishes {"type":"ed_event","event":<name>,...} on the
   bus. Make the Journal directory configurable ([elite] section) with the standard
   default.
2. covas/ed/status.py — a StatusWatcher that watches Status.json, decodes the Flags
   bitfield, and publishes semantic transitions (Docked/Undocked, LandingGear, LowFuel,
   Supercruise, etc.) rather than raw spam. Maintain a rolling "current context" object
   (system, station, ship, docked?, fuel%, cargo) updated from both watchers.
3. covas/capabilities/ed_context_capability.py — exposes system_context() returning a
   short natural-language summary of current context for the system prompt (so even local
   Qwen sounds situationally aware), and simple read tools (where_am_i, ship_status).
4. Add a config toggle to enable/disable monitoring. Everything off by default until I
   opt in.

Constraints: watchers publish events only; they must never block the voice loop. No
polling tighter than needed. Pure parsing/bitfield logic must be unit-tested with sample
journal lines and Status.json payloads (include fixtures).

Acceptance: unit tests green on fixtures; a manual test script that tails my live journal
and prints published events. Do NOT wire proactive speech yet. Stop for review.
```

---

## Prompt 4 — Hybrid router (local-first with escalation)

```
Read CLAUDE.md and DESIGN_AND_ROADMAP.md (§4).

Branch: feature/router

Goal: decide per-turn whether a request is handled locally (Ollama+Piper) or escalated to
cloud (Claude+ElevenLabs), so routine turns cost nothing.

Tasks:
1. covas/router.py — a Router with a pure, unit-testable decide(text, context) -> Route
   (which LLM + which TTS + reason). First cut = deterministic rules:
   - escalate to cloud if the request needs web/current data (keywords + explicit asks),
     asks for depth/analysis, or matches a wake phrase ("ask the big brain"); else local.
   - always allow a manual override (UI toggle / wake phrase) to force a tier.
2. Integrate into app.py so the chosen providers are used for that turn. Keep both stacks
   constructed lazily / cached.
3. Config: [router] with the wake phrases and an enable flag; default to cloud-only until
   I turn it on, so nothing changes for me unless I opt in.
4. Log every routing decision (to the session log + EventBus) with its reason, so I can
   tune the rules from real transcripts.

Constraints: routing is policy only — no provider logic leaks into the router. Keep it
deterministic and explainable for now (no LLM classifier yet; leave a clean extension
point for one).

Acceptance: thorough unit tests on decide() covering each rule and the override; manual
test instructions. Stop for review.
```

---

## Prompt 5 — Local checklist tool-calling via Ollama

```
Read CLAUDE.md and DESIGN_AND_ROADMAP.md (§4 "practical notes").

Branch: feature/local-tools

Goal: let the local (Ollama/Qwen) path use the checklist tools reliably, matching the
cloud path's capability.

Tasks:
1. Extend covas/providers/ollama_llm.py to pass the registry's tool schemas to Ollama
   (/api/chat "tools"), handle non-streaming tool-call rounds, execute via the
   CapabilityRegistry, and continue until a final answer — normalizing to the same event
   contract (emit on_event("tool", name)).
2. Add a validate/repair step for tool-call JSON (Qwen formats less reliably): validate
   against the schema, and on malformed output, reprompt once with a terse correction
   before giving up gracefully.
3. Unit-test the parse/validate/repair logic with recorded-shape payloads (mock the HTTP
   layer; no live Ollama in CI).

Constraints: reuse the SAME CapabilityRegistry as the cloud path — no duplicate tool
logic. Fail soft: a botched tool call must not crash the turn.

Acceptance: tests green; manual test steps for me to try checklist edits by voice in
local mode. Stop for review.
```

---

## Prompt 6 — Proactive callouts (opt-in)

```
Read CLAUDE.md and DESIGN_AND_ROADMAP.md (§5 "What it unlocks").

Branch: feature/proactive

Goal: let the companion initiate speech on notable ED events (arrival, mission complete,
low fuel, near-death) without a PTT press.

Tasks:
1. A ProactiveCapability subscribing to ed_event on the bus, with a per-event-type
   whitelist, cooldowns, and a global mute. On a qualifying event, generate a short line
   (via the router's chosen LLM) and speak it through TTS — reusing the existing
   speak/cancel path, never interrupting an in-progress user turn.
2. Config [proactive]: enabled flag (default off), per-event toggles, cooldown seconds.

Constraints: must respect the cancel/interrupt model; must never talk over the Commander;
must be trivially mutable. Keep it cheap (prefer local LLM for these).

Acceptance: tests for the whitelist/cooldown/mute logic; manual test steps. Stop.
```

---

## Prompt 7 — Keybind automation (LATER; safety-first)

```
Read CLAUDE.md and DESIGN_AND_ROADMAP.md (§6). Do NOT start until prompts 2–5 are merged.

Branch: feature/keybinds-prototype

Goal: prove ONE reliable action end-to-end before generalizing. No broad automation yet.

Tasks:
1. covas/keybinds/binds.py — parse the ED bindings XML
   (%LOCALAPPDATA%\Frontier Developments\Elite Dangerous\Options\Bindings\*.binds),
   mapping an action name (e.g. LandingGearToggle) to the physically bound key. Unit-test
   the parser against a sample .binds fixture.
2. covas/keybinds/executor.py — send that key to Elite using scancode-level SendInput
   (ED often ignores plain virtual-key events). Support press / hold(duration) / release.
3. A KeybindCapability exposing exactly ONE action (toggle landing gear) behind a safety
   layer: explicit confirmation, a hard global abort, an allowlist, and a guard that
   refuses to act during combat/interdiction (read from ED Status flags).
4. The LLM only selects a named macro; the executor runs deterministic keystrokes. The
   model never synthesizes raw key sequences.

Constraints: safety layer is non-negotiable. Everything opt-in and off by default.

Acceptance: binds parser unit-tested; a manual test where I confirm landing gear toggles
in-game. Explicitly report reliability quirks. Stop for a go/no-go before adding more
actions.
```

---

### Tips for running these
- Keep `CLAUDE.md` current — Claude Code reads it every session.
- If a step balloons, ask Claude Code to split it and update this file.
- Prefer merging each branch (or tagging it) before the next, so you can always roll back
  to a known-good point without losing context.
