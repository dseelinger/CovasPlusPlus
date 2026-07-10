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
1. covas/keybinds/binds.py — parse the active ED bindings file. Default to the newest
   Custom.*.binds in %LOCALAPPDATA%\Frontier Developments\Elite Dangerous\Options\Bindings\
   (currently Custom.4.2.binds — glob Custom.*.binds and pick the highest version so a
   future ED update, e.g. 4.3 / 5.0, doesn't break it). Allow a [keybinds].binds_file
   config override. Each control has a Primary and a Secondary binding, and either may be a
   key OR a joystick/HOTAS button; extract the entry with Device="Keyboard" (every control
   that matters has a keyboard bind). The executor injects keyboard scancodes, so if an
   action somehow lacks a keyboard binding, mark it unusable and say so. Unit-test the
   parser against a sample .binds fixture with both Primary and Secondary set and a mix of
   keyboard and joystick devices.
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

## Prompt 7 — Feature: find the closest station selling a module (voice, multi-turn)

```
Read CLAUDE.md and DESIGN_AND_ROADMAP.md (§3 capabilities, §9 testing). New feature.
Depends on ED monitoring for current system (fallback: latest journal FSDJump/Location).

Branch: feature/find-closest-module

Goal: by voice, "find the closest station that sells module X" — resolve the module
CONVERSATIONALLY over multiple turns, confirm, then speak the result and copy the SYSTEM
name to the clipboard.

Multi-turn flow (state lives in the conversation history; the tool stays STATELESS):
1. Commander asks for the closest <module>.
2. The LLM interprets the (possibly misheard) name against the module taxonomy, then states
   its interpretation and asks to CONFIRM — and if required attributes (size, mount) are
   missing, asks for those in the same breath. It NEVER guesses a missing attribute and
   NEVER searches yet.
3. Commander narrows, confirms, or cancels.
4. If confirmed AND complete -> step 5. If still incomplete/ambiguous -> the LLM asks again
   (loop back to step 2), re-calling the tool with the accumulated info. If the commander
   cancels ("cancel" / "never mind" / "forget it") -> acknowledge and drop the request;
   NO search runs.
5. With a RESOLVED + confirmed module, run the Spansh search (this fires exactly once).
6. Speak the system + station + distance; copy the SYSTEM name to the clipboard.
7. Done.

Notes on the flow:
- The tool is a pure function of its arguments. The dialogue state IS the message history,
  so each re-call just passes more-complete args — no pending-state object to manage.
- Verbal cancel is an LLM-recognized intent, separate from the existing hard PTT-cancel
  (which still aborts any in-flight turn).
- The real, rate-limited Spansh query must NEVER run during disambiguation — only after
  confirmation.

Disambiguation (the LLM drives it; the tool validates and guides):
- The LLM maps loose/misheard names to the taxonomy: "multiple cannon", "multicannon" ->
  Multi-Cannon. It does the fuzzy understanding; the tool validates.
- The tool returns structured guidance the LLM turns into speech: what's missing and the
  valid options (sizes, mounts), or candidates when ambiguous.
- Runs fine on the default Haiku tier — it's mapping plus a clarifying question.

Data-source split:
- Module TAXONOMY (names, sizes, mounts, ratings) — bundle a static table (EDCD outfitting
  data / Spansh module reference) so validation + the whole ask/confirm/cancel dialog are
  OFFLINE, fast, unit-testable. No network for disambiguation.
- Station LOCATION — Spansh station search only (filter by module, sort by distance from
  current system). Before coding, fetch current Spansh API docs + one real sample response
  and parse what you actually see (may be job/poll based). EDSM fallback. Proper
  User-Agent; respect rate limits.

Tasks:
1. covas/nav/modules.py — bundled taxonomy + resolve(query, size?, mount?) returning one of:
   RESOLVED(id, label) | NEED_ATTRS(module, missing=[...], options={...}) |
   AMBIGUOUS(candidates=[...]) | UNKNOWN(suggestions=[...]). Pure/offline. Maps ED size
   words (small/medium/large/huge -> 1-4) and mounts (fixed/gimballed/turreted).
2. covas/nav/closest.py — find_closest_module(resolved_module, current_system, http, *,
   pad_size=None) -> nearest (system, station, distance_ly, pad, extra) via Spansh. http
   injected.
3. covas/nav/clipboard.py — copy(text), injected (Windows: pyperclip or Set-Clipboard via
   subprocess; note the choice in the PR).
4. covas/capabilities/find_closest_capability.py — FindClosestCapability with tool
   find_closest_module(module, size?, mount?, pad_size?, confirmed?). The tool DESCRIPTION
   tells the LLM to normalize the name, ask for any missing size/mount, CONFIRM before
   searching, and treat "cancel/never mind" as an abort. On RESOLVED + confirmed: read
   current system (ED context; fallback journal), query Spansh, copy the SYSTEM name to the
   clipboard, return a short spoken line. On NEED_ATTRS/AMBIGUOUS/UNKNOWN: return the
   structured guidance so the LLM can ask; on a cancel intent: return without searching.
5. Config [nav]: base URL(s), default pad_size (my main ships need Large — configurable),
   enable flag.

Tests (§9):
- Unit (default, offline): modules.resolve for exact, misheard ("multiple cannon"),
  missing-attrs ("multi-cannon" -> NEED_ATTRS size+mount), ambiguous, unknown; Spansh
  parsing + nearest-by-distance from a RECORDED fixture; clipboard via fake copy().
- Unit — the multi-turn flow with a scripted FakeLLM + fake http + fake clipboard:
  (a) ask -> narrow -> confirm -> asserts the search fires exactly ONCE and the system is
  copied; (b) a CANCEL mid-dialog asserts NO search and NO clipboard write; (c) a second
  ambiguous answer loops (asks again) before resolving.
- Integration+local (opt-in): one live Spansh query behind @pytest.mark.integration+local.

Constraints: no real network or clipboard in the default pytest run (inject both). Fail
soft — unknown module or failed lookup: say so, don't crash the loop.

Acceptance: bare pytest green and offline; manual tests: (a) "find the closest multicannon"
-> asks size + mount, confirms, then finds it; (b) "find the closest multiple cannon" ->
resolves to Multi-Cannon and confirms; (c) cancelling mid-dialog runs no search; all land
the right system on the clipboard. Report Spansh quirks. Stop for review.
```

---

## Voice Search & Help Subsystem — LLM-native (Search Prompts 1–6)

Extends the working outfitting feature (`find_closest_capability.py`) to five more Spansh
search categories, plus a first-class, templated help subsystem.

**Direction: LLM-native.** Copy the outfitting pattern — a stateless tool whose description
steers the model through conversational slot-filling and disambiguation; conversation
history IS the state. Do NOT build an explicit intent-classifier or query-state machine.

**Kept from the design brief (architecture-independent):** help is first-class and
TEMPLATED — no LLM in help *generation* (the category dialogs are LLM-native, help output
is not); validate every user-facing capability/slot/module/system name against the registry
or Spansh before speaking; the registry structurally prevents drift; failure-recovery IS
help; never speak >3 options; answer with an example utterance, not a schema.

**Backend: Spansh** (its field names are canonical). **Bodies/planets: OUT OF SCOPE** —
leave a seam, don't implement.

**Reuse map:** `nav/modules.py` (module resolution — done, reuse); `nav/closest.py` (Spansh
plumbing to generalize); `capabilities/base.py` (registry to extend with help metadata);
`find_closest_capability.py` (the LLM-native pattern to replicate); `nav/clipboard.py`,
`nav/location.py`, and the ED context (current-system default). Depends on the capability
registry and ED monitoring, both already merged.

**Boilerplate — paste this block at the top of Search Prompts 2–6 when you run them:**

> **Help requirement.** This feature must register with the capability registry, providing:
> a one-line description, at least one example utterance, spoken phrasings for every slot it
> introduces, and help text for each refinement it supports. The registry test must pass.
> Help output for this feature is templated from registry data — do not generate help prose
> with an LLM.
>
> **Hallucination constraints.** Any capability, slot, module, or system name in user-facing
> output must resolve against the registry or a canonical data source. Validate before
> speaking; on validation failure, fall back to the templated error response rather than
> emitting unvalidated text. Never invent a filter, parameter, or capability that does not
> exist in the registry.

---

### Search Prompt 1 — Help registry + templated help (idle + error)

```
Read CLAUDE.md, DESIGN_AND_ROADMAP.md (§3), covas/capabilities/base.py, and
find_closest_capability.py. Build help FIRST — every later capability registers with it.

Branch: feature/help-subsystem

Direction: help is a first-class, TEMPLATED projection of the registry — NO LLM in the help
generation path. (Category search dialogs are LLM-native; help OUTPUT is not.)

Tasks:
1. Extend the existing Capability protocol / CapabilityRegistry (base.py) with help metadata:
   one_liner, example (a real utterance), slots [{param, phrasings[], example, help_text}],
   help_when_active. Additive — existing capabilities keep working.
2. A HelpCapability, templated string assembly only:
   - idle: categories + one example each. Rank by usage; speak at most 3, then "there are
     others — ask about X, Y, Z."
   - error / failure-recovery (the important mode): on an unresolved term, say the specific
     failure + nearest valid phrasing ("I didn't recognize 'power distributer' as a module —
     did you mean Power Distributor?"). Never recite the capability list.
   - Answer with an EXAMPLE utterance, not a schema. Three phrasing variants per template,
     rotated DETERMINISTICALLY (random makes tests flaky).
3. Help registers ITSELF as a capability so "what can you do" always has one honest answer.
   Invocation is an intent, not a command word: explicit ("help", "what can you do"), meta
   ("how do I…", "can you…"), implicit (anything that fails to resolve — echo what WAS caught
   + what's missing, never "I didn't understand").
4. Retrofit the outfitting (find-closest) capability with its help metadata, so the registry
   is exercised by a real capability from day one.

Tests (unit, offline): registry rejects entries missing required help fields; idle with 0
caps -> empty-state that reads correctly now AND once capabilities exist; idle with 5 -> 3 +
tail; a template referencing an unregistered name hits the fallback WITHOUT raising and
WITHOUT emitting the unresolved name; rotation deterministic across a fixed call sequence;
error mode produces the specific recovery line, validated against the registry.

Acceptance: bare pytest green and offline. Stop for review.
```

### Search Prompt 2 — Registry contract enforcement

```
[Prepend the Boilerplate block above.]
Read CLAUDE.md. Make the "every capability carries complete help metadata" policy enforce
itself structurally, not in prose a future author skims.

Branch: feature/registry-contract

Tasks:
1. Finalize the help-metadata contract on Capability; a registry test that FAILS when any
   registered capability is missing complete metadata (one_liner, example, and a phrasing +
   help_text for every slot it introduces). This test guards every future category.
2. Pure read helpers help consumes: enumerate categories, a category's slots (for "what can
   I add"), examples.
3. Spansh field mapping: slot.param is the canonical Spansh parameter name.

Tests: contract test flags an incomplete capability and passes a complete one; helpers return
correct data over a fixture registry.

Acceptance: bare pytest green; this test gates the later prompts. Stop.
```

### Search Prompt 3 — Generalize the Spansh client

```
[Prepend the Boilerplate block above.]
Read CLAUDE.md, DESIGN §9, and covas/nav/closest.py — its plumbing is the base.

Branch: feature/spansh-client

Goal: extract a shared, typed Spansh client the six in-scope categories use; refactor
outfitting onto it. Bodies: leave a seam, do NOT implement.

Tasks:
1. covas/search/spansh.py — lift the reusable transport from closest.py: the injected Http
   Protocol + RequestsHttp, POST/parse, error handling (400 / unreachable -> spoken NavError),
   distance-sort assumption, fleet-carrier exclusion, pad logic. Category-agnostic.
2. Per-category query builders/parsers: stations, outfitting, minor factions, star systems,
   signals, misc. Reuse closest.py's existing build_payload/parse as the OUTFITTING one.
   BEFORE coding each, fetch current Spansh docs + one real sample response and parse what
   you actually see.
3. Derive each category's slot schema from Spansh's ACTUALLY accepted params where possible
   so the registry can't drift; fail LOUD on unknown params.
4. Refactor find_closest_capability + nav/closest.py onto the shared client — outfitting
   behavior and its existing tests unchanged.
5. Bodies: a clearly-marked unimplemented seam.

Tests: unit (offline) parse + query-build per category from RECORDED fixtures; unknown param
raises; outfitting's existing tests still pass; @pytest.mark.integration+local, one live query
per category.

Acceptance: bare pytest green and offline; outfitting unaffected. Stop.
```

### Search Prompt 4 — First new category, LLM-native (the reference)

```
[Prepend the Boilerplate block above.]
Read CLAUDE.md and covas/capabilities/find_closest_capability.py — copy its SHAPE (stateless
tool + conversation history as state; the model drives disambiguation; no classifier, no
state machine).

Branch: feature/search-star-systems

Goal: build the STAR SYSTEMS category as the LLM-native reference the others will follow.

Tasks:
1. covas/capabilities/system_search_capability.py — a stateless tool "search_star_systems"
   whose DESCRIPTION guides the LLM to fill slots conversationally (allegiance, government,
   economy, population, security, powerplay, colonization), ask when unclear, and confirm
   loosely. Every slot defaults to Any unless spoken. Near-system defaults to the Commander's
   current system (ED context; journal fallback via location.py).
2. Slot values validated against the registry/Spansh vocab before the query; query via the
   shared client; results validated before speaking; copy the primary SYSTEM name to the
   clipboard (nav/clipboard.py). Register full help metadata.
3. Refinement is natural multi-turn: re-call with accumulated slots; "new search" is just a
   fresh call. Do NOT build a state machine.

Tests: unit (offline) with fake http + fake clipboard + stubbed current system: slot-filling
across turns; Any-defaults; a result copies the system; an invalid slot value is caught (not
spoken as-is).

Acceptance: bare pytest green and offline; manual voice test. Stop — this is the pattern;
I'll confirm before you replicate it.
```

### Search Prompt 5 — Remaining categories in the reference shape

```
[Prepend the Boilerplate block above.]
Read CLAUDE.md, the star-systems capability (the reference), and find_closest (outfitting).

Branch: feature/search-remaining-categories

Goal: build the rest as LLM-native capabilities in the SAME shape: stations, minor factions,
signals, misc (nearest wars/civil wars, restore/mining/massacre missions, multistate factions).

Tasks:
1. One capability per category — a stateless conversational tool + registered help:
   - stations: landing pad, services, station type, distance, faction. Routing rule: if the
     utterance names a MODULE or SHIP, use the OUTFITTING tool instead (outfitting returns
     stations anyway) — state this in BOTH tools' descriptions so the model routes correctly
     (no separate classifier).
   - minor factions: allegiance, government, faction state, player faction; "is present"
     default; tri/quad-state controls are just slot values, spoken polarity flips them.
   - signals: signal type (beacon, etc.).
   - misc: the mission/war finders above.
2. Hardcoded defaults across categories: surface stations = yes (incl. Odyssey settlements);
   carriers included but "no carriers" is a one-word toggle; "close to the star" -> max
   station distance 1000 Ls.
3. Validate-before-speak everywhere; each copies the primary system to the clipboard; each
   registers help.

Tests: unit (offline) per category — slot-filling, defaults, polarity flip, result copies
system, invalid value caught. Stations-vs-outfitting routing exercised.

Acceptance: bare pytest green and offline; manual voice tests per category. Stop.
```

### Search Prompt 6 — Voice polish, refinement, error-help wiring

```
[Prepend the Boilerplate block above.]
Read CLAUDE.md. Final wiring — much is already in place from outfitting.

Branch: feature/search-voice-polish

Goal: make the whole surface feel right by voice, and make failure-recovery help fire.

Tasks:
1. Natural-language refinement across categories: confirm the tools accept a GROWING set of
   constraints turn to turn (add/"with", replace/"actually", remove/"never mind the",
   reset/"new search" are just the model re-calling with updated args). A refinement must
   RE-QUERY — never filter a cached result set (a new constraint can change which result is
   nearest). Speak the prior result while the new query is in flight if there's latency.
2. Wire HelpCapability's error mode: any unresolved term (module, system, slot value) routes
   to failure-recovery help ("I didn't recognize X — did you mean Y?"), not a dead end.
   Implicit help on low confidence: echo what WAS caught, ask for what's missing.
3. Low-confidence confirmation reusing the outfitting pattern (state best guess, offer
   alternatives, accept/narrow/cancel; verbal cancel drops it — no query runs).

Tests: unit (offline) — a refinement RE-QUERIES not cache-filters (assert fake-http call
count); error-mode help produces the validated recovery line and never emits an unresolved
name; cancel mid-dialog runs no query and no clipboard write. Manual: end-to-end voice across
categories.

Acceptance: bare pytest green and offline; manual voice checklist. Stop for review.
```

---

## Navigation, Settings & Automation (Prompts N1–N7)

Features layered on the existing subsystems. Order matters where noted (N2 depends on N1's
schema). Same conventions throughout — LLM-native capabilities, dependency injection, and
unit-default / integration-opt-in tests (§9). These are NOT Spansh-search categories, so the
search-subsystem boilerplate doesn't apply, but validate-before-speak and fail-soft do.

### N1 — Settings schema + web settings page

```
Read CLAUDE.md, covas/config.py, covas/web.py, covas/templates/index.html.

Branch: feature/settings-schema-web

Goal: ONE settings schema as the source of truth, plus a genuinely nice web settings page that
renders from it and writes overrides.json. (N2's voice layer projects from the SAME schema, so
they can't drift.)

Tasks:
1. covas/settings_schema.py — declare every user-facing setting ONCE: path (into config.toml
   sections), type (bool/int/float/enum/string/path), label, group, help text, range/options,
   default, plus spoken phrasings + example (for the voice layer). Cover the existing sections
   (anthropic, whisper, elevenlabs, web_search, conversation, nav, router, proactive,
   elite/ed, keybinds, ui…).
2. Web settings page (Flask, web.py + template): grouped sections, the RIGHT control per type
   (toggle, dropdown, number/slider, text/path), inline help, per-setting reset-to-default,
   client-side validation against the schema, a search/filter box, and a save that writes
   overrides.json via the existing mechanism. Reload the running config where already
   supported (Whisper). Bar to clear: cleaner and calmer than EDCopilot's settings.
3. Server-side: validate every POSTed setting against the schema; reject unknown keys/values
   LOUDLY — never write unvalidated data into overrides.json.

Tests: unit (offline) — schema covers all overridable keys; validation accepts good values and
rejects out-of-range/unknown; a POST round-trips into overrides.json.

Acceptance: bare pytest green; manual: change a setting in the panel, confirm it persists and
takes effect. Stop for review.
```

### N2 — Voice-settable settings

```
Read CLAUDE.md and covas/settings_schema.py (the shared schema from N1).

Branch: feature/settings-voice

Goal: change any setting by voice, projecting from the SAME schema.

Tasks:
1. A SettingsCapability (LLM-native, like find-closest): tools to GET and SET a setting by its
   spoken phrasings, validated against the schema (type/range/options), writing overrides.json
   via config.py. Confirm the change back ("Whisper model set to small").
2. Accept natural values ("turn personality off", "use the George voice", "set thinking to
   high"). Reject invalid values WITH the valid options — never guess.
3. Register help metadata so "what can I change" works and the error mode fires on an unknown
   setting/value.

Tests: unit (offline) — set a bool/enum/number by phrasing round-trips to overrides.json;
invalid value refused with options; unknown setting routes to help.

Acceptance: bare pytest green; manual voice test. Stop.
```

### N3 — Location & carrier commands

```
Read CLAUDE.md, covas/ed (monitoring/context), covas/nav/location.py, covas/nav/clipboard.py,
find_closest_capability.py.

Branch: feature/location-carriers

Goal: quick location/carrier voice commands + the "already there -> don't copy" fix.

Tasks:
1. "Copy my current system" -> put the current system on the clipboard.
2. Personal fleet carrier: track its current system LIVE from the journal. PIN to the OWNED
   carrier's identity — CarrierStats gives CarrierID + name + callsign; CarrierLocation (id-
   matched) gives the system; CarrierJumpRequest (id-matched) gives a pending jump. IGNORE
   CarrierJump for tracking — it fires when the Commander is aboard ANY carrier (e.g. a
   squadron carrier), so trusting it reports the wrong carrier's location (observed live:
   returned a squadron carrier's system instead of the owned carrier's). "Where's my fleet
   carrier" -> speak its system + copy it. Local and reliable.
3. Squadron carrier: DO NOT look it up remotely. Verified against the live sites — no public
   database (Spansh/EDSM/Inara) resolves a carrier by callsign reliably (likely a deliberate
   BGS/PvP measure). "Where's my squadron carrier" -> explain the location is only available
   in-game, on the Squadron menu's Carrier Management tab (optionally naming the squadron,
   auto-discovered from SquadronStartup). No config, no galaxy-DB call.
4. Fix across ALL search/nav/carrier results: if the answer IS the current system, say so and
   do NOT copy to the clipboard (you're already there).

Tests: unit (offline) — owned carrier system from a fixture journal (and a different carrier's
events IGNORED); squadron name from a fixture; the squadron command returns the in-game pointer
and never copies; a "current system" result skips the clipboard (fake clipboard asserts no call).

Acceptance: bare pytest green and offline; manual. Stop.
```

### N4 — Route callouts (proactive)

```
Read CLAUDE.md, covas/ed (journal/status + the proactive capability), DESIGN §5.

Branch: feature/route-callouts

Goal: proactive callouts while flying a plotted route.

Tasks:
1. Read the plotted route from NavRoute.json (full jump list with star classes); track progress
   via FSDJump. Handle replot and route completion.
2. On approach to the next system (FSDTarget, or on jump) announce whether the star is
   SCOOPABLE — classes K, G, B, F, O, A, M are scoopable; anything else isn't. Keep it terse.
3. Every Nth jump (N configurable, default 5) announce jumps remaining to the destination; and
   announce arrival at the final system.
4. Route everything through the proactive path (respect cancel/mute/cooldown; never talk over
   the Commander). Config [route]: enable + N + per-callout toggles. Off by default.

Tests: unit (offline) — scoopable classification over KGBFOAM vs non-scoopable; jumps-remaining
math from a NavRoute fixture; every-Nth cadence; replot handled.

Acceptance: bare pytest green and offline; manual in-game. Stop.
```

### N5 — Auto-honk (keybind automation)

```
Read CLAUDE.md, DESIGN §6, covas/keybinds (binds parser + executor), covas/ed (Status/journal).

Branch: feature/auto-honk

Goal: auto-"honk" (fire the Discovery Scanner) shortly after arriving in a new system. OPT-IN,
off by default, safety-gated.

Tasks:
1. Config [honk]: enabled (default false), the scanner's fire group index + trigger
   (primary/secondary), hold duration (default ~6s).
2. On arrival in a NEW system (FSDJump into normal space), if enabled:
   - If the scanner group + trigger are configured: read the CURRENT fire group from
     Status.json ("FireGroup"), cycle to the scanner group via the CycleFireGroupNext/Prev
     keybinds (from the binds parser) — deterministic, no guessing — then HOLD the configured
     primary/secondary fire key for the duration to complete the honk, then cycle back.
   - If NOT configured: just hold the primary fire key for the duration (the accepted
     "hope for the best" fallback).
3. Safety (reuse the keybind safety layer): never act during combat/interdiction (Status
   flags), honor the hard global abort, strictly opt-in. Log every honk.

Tests: unit (offline) — fire-group delta math (current vs target -> N cycles + direction) from a
Status fixture; arrival triggers the honk sequence via a FAKE executor (assert key sequence +
hold duration); combat guard suppresses it; disabled = no-op.

Acceptance: binds + sequencing unit-tested; manual: arrive in a system and confirm it honks (and
does NOT fire weapons). Report reliability. Stop for go/no-go.
```

### N6 — Community Goals

```
Read CLAUDE.md, covas/ed (journal/context), covas/nav/clipboard.py, find_closest_capability.py.

Branch: feature/community-goals

Goal: voice Community-Goal (CG) queries. Journal-primary — the ED journal `CommunityGoal`
event is authoritative for the player's own standing and carries the current CG array.

Journal source: the most recent `CommunityGoal` event lists active CGs, each with CGID, Title,
SystemName, MarketName, Expiry, CurrentTotal, TierReached/TopTier, PlayerContribution,
PlayerPercentileBand, PlayerInTopRank, TopRankSize, IsComplete. It's written when the Commander
interacts with a CG board, so it's "as of last board visit."

Tasks:
1. Track the latest CommunityGoal event from the journal into a small CG state object.
2. LLM-native CGCapability with tools:
   - list current CGs (title + system + expiry), MERGING the external feed (complete current
     list) with the journal (which ones you're contributing to). Call OUT the ones NEW to you —
     active CGs not in your journal ("and there's one in <system> you haven't visited yet").
     LLM-workable: fuzzy phrasing is fine.
   - "what system is CG X in" -> resolve X to a CG by title (fuzzy), speak the system, and
     copy it to the clipboard — applying the N3 rule: if it's your current system, say so and
     DON'T copy.
   - "what's my standing in CG X" -> PlayerInTopRank -> "Top 10 Commanders"; else
     "top {PlayerPercentileBand}%" (map to 100/75/50/25). Flag it's as of your last board
     visit; if the CG isn't in your journal, say so ("I don't have your standing — visit the
     board").
3. External CG feed — the completeness source, so CGs you HAVEN'T visited surface (the point
   of this feature). Pull-based (on request), no polling. Default to EDSM's community-goals API
   (no key required); Inara's getCommunityGoalsRecent (needs the generic API key) is the richer
   alternative — verify the current endpoints at build time. It's the PRIMARY source for the
   list + systems; the journal supplements it with your engagement and is the ONLY source of
   your own standing. Fail soft: if the feed is unreachable, fall back to journal-known CGs and
   say you can't see ones you haven't visited right now. Config [cg]: source (edsm|inara),
   optional key. Validate every CG/system name before speaking.
4. Register help metadata.

Tests: unit (offline) — parse a CommunityGoal journal fixture; percentile/top-rank -> phrasing
mapping (incl. Top 10); fuzzy CG-title match incl. ambiguity; system lookup copies (fake
clipboard) and skips copy when it's the current system; external list from a recorded fixture
(fake http); "no standing" path when a CG is absent.

Acceptance: bare pytest green and offline; manual. Stop for review.
```

### N7 — Personality tab, voice speed & log filter

```
Read CLAUDE.md, covas/config.py, covas/web.py + templates/index.html, covas/llm.py
(build_system reads personality.txt), covas/tts.py, and personalities/presets.md.

Branch: feature/personality-voice-log

Goal: three web-panel additions — a Personality tab, a voice-speed slider, and a Log filter.

--- Personality tab (no voice editing of it) ---
Separate PERSONA (voice) from CAMPAIGN (the Commander's personal facts) so switching persona
never wipes the campaign:
- personalities/presets.md ships a shared "Base" block + 10 selectable "Persona" blocks (parse
  it). Presets are read-only + committed (no personal data).
- Campaign = the Commander's personal facts, persisted git-ignored (like personality.txt today).
- build_system() returns Base + selected Persona + Campaign, composed at load. Migrate the
  current personality.txt: its voice -> the "Classic" persona (or a "Custom (current)"), its
  campaign section -> the Campaign field.
Tab UI: a persona picker (list + preview + select), an editable box with "Save as custom"
(writes a git-ignored custom persona, also listed), and a separate Campaign editor. Save applies
immediately. Add personalities/custom/ to .gitignore.

--- Voice speed ---
A slider 1.0-1.2x wired to ElevenLabs' native voice `speed` setting (its supported range — clamp
to [1.0, 1.2], don't exceed). Add [elevenlabs].speed to config + the settings schema/page and
pass it in the TTS request.

--- Log filter ---
Add a filter to the live Log window: two modes, "Conversation" (DEFAULT) and "All". Conversation
shows only Commander utterances and COVAS replies; All shows everything (status, thinking, search,
system, usage/cost). Client-side filter over the existing event stream (by event type / who);
default to Conversation since that's the normal case, and persist the choice.

Tests: unit (offline) — build_system composes Base+Persona+Campaign; preset parsing; save-as-
custom round-trips to the git-ignored path; speed clamped to [1.0,1.2] and passed to the TTS
payload (fake). (Log filter is client-side; a light template/JS assertion is enough.)

Acceptance: bare pytest green; manual: switch persona (campaign persists), save a custom, nudge
speed and hear it, toggle the log to Conversation and confirm stats/thinking hide. Stop for review.
```

---

### Tips for running these
- Keep `CLAUDE.md` current — Claude Code reads it every session.
- If a step balloons, ask Claude Code to split it and update this file.
- Merge (or tag) each branch before the next, so you can always roll back to a known-good
  point without losing context.
