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

## Navigation, Settings & Automation (Prompts N1–N11)

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
   supported (Whisper). Bar to clear: a clean, calm, uncluttered settings page.
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
   of this feature). Pull-based (on request), no polling. BUILD-TIME FINDING: EDSM has no
   public community-goals API anymore (every candidate endpoint 404s), so the supported feed
   is Inara's getCommunityGoalsRecent (POST inara.cz/inapi/v1/), which needs a free generic
   Inara API key. It's the source for the complete list + systems; the journal supplements it
   with your engagement and is the ONLY source of your own standing. Fail soft: no key ->
   journal-only with a note to add one; feed unreachable -> journal-only, say you can't see
   unvisited ones right now. Config [cg]: source (inara|none) + inara_api_key. The key is a
   restart-level setting and NOT exposed in the settings panel (it's a credential). Validate
   every CG/system name before speaking.
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

### N8 — Find the closest station selling a SHIP

```
Read CLAUDE.md, DESIGN_AND_ROADMAP.md (§3.5), and the capability it mirrors:
covas/capabilities/find_closest_capability.py, covas/nav/modules.py, covas/nav/closest.py,
covas/search/spansh.py.

Branch: feature/find-closest-ship

Goal: "find the closest station that sells SHIP X" by voice — resolve the ship conversationally,
find the nearest station selling it, speak it, copy the SYSTEM name to the clipboard. Mirror the
find-closest-module LLM-native pattern exactly.

Data source: Spansh station search supports a SHIPS filter (like the modules filter). BEFORE
coding, fetch current Spansh docs + one real sample ship-query response and parse what you
actually see. REUSE the shared search/spansh.py client + closest.py plumbing (Http seam, distance
sort, carrier exclusion, pad filter, error handling) — don't duplicate it.

Tasks:
1. covas/nav/ships.py — a bundled canonical SHIP list (exact Spansh ship names) + pure
   resolve(query) -> Resolved / Ambiguous(candidates) / Unknown(suggestions), with aliases +
   fuzzy match for STT mishears ("conda" -> Anaconda, "fdl" -> Fer-de-Lance, "clipper" ->
   Imperial Clipper). Handle the genuinely AMBIGUOUS families — "Krait" -> Mk II or Phantom;
   "Cobra" -> Mk III or Mk IV; "Viper" -> Mk III or Mk IV; "Asp" -> Explorer or Scout; "Type" ->
   Type-6/7/9/10 — ASK which, never guess. Offline + unit-testable.
2. FindClosestShipCapability (mirror FindClosestCapability): stateless tool
   find_closest_ship(ship, pad_size?) whose description drives the LLM to normalize the name, ask
   on ambiguity, and search once resolved. Reads current system (ED context; journal fallback),
   queries Spansh via the shared client with the ships filter, copies the SYSTEM name (skip the
   copy when it's the current system — the N3 rule), returns a short spoken line. Register full
   help metadata.
3. Routing: update the outfitting + station-search tool descriptions so "where can I buy the
   <ship>" goes to THIS tool instead of being punted to generic station search.
4. Config: reuse [nav] (pad default, base URL, enable).

Tests (§9): unit (offline) — resolve for exact / mishear / ambiguous family / unknown; Spansh
ship-query build + nearest-by-distance from a RECORDED fixture; clipboard via fake copy (and
skipped when current system); capability end-to-end with fake http + fake clipboard + stubbed
current system. Integration+local: one live Spansh ship query.

Constraints: no real network/clipboard in the default pytest run; fail soft; LLM-native (no
classifier/state machine — mirror the outfitting pattern).

Acceptance: bare pytest green and offline; manual: "find the closest Krait" -> asks Mk II or
Phantom, then finds it and copies the system; "find the closest Anaconda" -> resolves and finds.
Stop for review.
```

### N9 — Ship loadout & engineering (read + reason)

```
Read CLAUDE.md, covas/ed/journal.py (the existing _loadout handler), covas/ed/context.py,
covas/capabilities/ed_context_capability.py, covas/capabilities/checklist_capability.py, and
covas/nav/modules.py (for friendly names).

Branch: feature/ship-loadout

Goal: capture the full current ship loadout + engineering from the journal Loadout event and
answer questions about it by voice — and let the model reason over it (suggest improvements, add
to the checklist).

Tasks:
1. Extend _loadout in ed/journal.py to capture the FULL loadout, not just ship name: per module —
   slot, item (internal symbol), on/health/priority, and its Engineering block if present:
   BlueprintName, Level (grade), Quality, ExperimentalEffect, and the Modifiers array (label,
   value, original value). Store a structured loadout snapshot on EDContext (replace on each new
   Loadout — it's a full snapshot each time).
2. Friendly names: Loadout uses internal symbols ("int_hyperdrive_size5_class5", "FSD_LongRange",
   "special_fsd_heavy"). Map module, blueprint, and experimental-effect symbols to spoken names
   (reuse/extend nav/modules.py; a symbol->name table baked from EDCD outfitting/FDevIDs data —
   offline). Fall back to the event's *_Localised fields when present.
3. A LoadoutCapability (LLM-native) with tools answering from the snapshot: engineering on a named
   module ("what's on my FSD / power plant / thrusters") -> blueprint, grade, experimental,
   spoken naturally; list all experimental effects; list modules / a slot's module (with key
   modifiers). Validate names before speaking; if no Loadout seen yet, say so ("board your ship or
   open outfitting and I'll read it"). Register full help metadata.
4. Config toggle if useful; reads only local journal data (no CAPI/auth).
5. Cross-capability behavior (no new plumbing — the checklist tools are already available every
   turn): the loadout tool descriptions should invite the model, when asked or clearly useful, to
   reason over the loadout, suggest improvements conversationally, and OFFER to add specific
   upgrades to the checklist via the existing add_objective tool. For "what's best" engineering
   advice, lean on web search for current meta and flag uncertainty — never invent module stats or
   blueprint effects.

Tests (§9): unit (offline) — parse a recorded Loadout fixture (with engineering + an experimental
effect) into the snapshot; symbol->friendly-name mapping incl. blueprint + experimental; the
capability answers "engineering on my FSD" and "experimental effects" from a stubbed snapshot; the
"no loadout yet" path. No network.

Constraints: fail soft; LLM-native; local journal only.

Acceptance: bare pytest green and offline; manual: board ship, ask "what's the engineering on my
FSD" and "what experimental effects do I have", and try "suggest upgrades and add them to my
checklist". Stop for review.
```

### N10 — Web checklist editor (WYSIWYG markdown)

```
Read CLAUDE.md, covas/web.py + templates/, covas/checklist.py (its "- [ ]"/"- [x]" + indentation
format), and config.toml ([checklist].file).

Branch: feature/web-checklist-editor

Goal: a "Checklist" tab in the web panel that RENDERS and edits the checklist as markdown
(WYSIWYG, Obsidian-style — NOT a plain textarea), with first-class checkbox/task-list support,
editing the same file the voice loop uses.

Tasks:
1. Add a Checklist tab with a WYSIWYG markdown editor that renders and edits rendered markdown and
   supports task lists (click a checkbox to toggle; edit text inline; add / remove / nest items).
   Use a proven library from CDN — Tiptap (ProseMirror) + task-list extension, Milkdown, or TOAST
   UI Editor's WYSIWYG mode — your pick; checkbox/task-list support is REQUIRED.
2. Load [checklist].file, edit in the tab, and SAVE back to the same file. The serialized markdown
   MUST round-trip cleanly through covas/checklist.py's parser: preserve the exact "- [ ]"/"- [x]"
   task syntax AND the indentation/nesting the model relies on.
3. Keep voice and web in sync: on save, have the running app reload its Checklist model from disk
   (both edit the same file). Handle "file changed underneath" gracefully — if the file changed
   since the tab loaded it (voice edited it), warn before overwriting rather than clobbering.
4. The file stays git-ignored (personal). No behavior change to the voice checklist tools.

Tests (§9): unit (offline) — round-trip through checklist.py is lossless (checkbox state + nesting
preserved); the save endpoint writes the file and triggers a model reload; the stale-write guard
fires when the on-disk file changed since load. (Editor is client-side; a light template check is
enough.)

Acceptance: bare pytest green; manual: toggle a checkbox and edit an item in the tab, save, confirm
the file and the voice model reflect it, and that voice-made edits appear on reload. Stop.
```

### N11 — "Copy that to my clipboard" (general, conversational)

```
Read CLAUDE.md, covas/nav/clipboard.py (copy()), covas/capabilities/_search_support.py
(copy_system helper), and find_closest_capability.py for the LLM-native pattern.

Branch: feature/copy-to-clipboard

Goal: a general "copy that to my clipboard" voice command that works on anything from the
conversation — "copy that system onto my clipboard" after the AI names Elvira Martuuk's system,
"copy that station", "copy those coordinates", etc.

Design: LLM-native. Expose ONE tool the model calls with the exact text to copy; the model
resolves what "that" refers to from the recent conversation. No parsing/heuristics on our side.

Tasks:
1. A ClipboardCapability with a tool copy_to_clipboard(text, label?): puts `text` on the Windows
   clipboard via the existing nav/clipboard.py copy() (INJECTED so tests use a fake). Returns a
   short confirmation of what was copied. `label` is optional flavor for the spoken reply
   ("the system", "the station").
2. Tool description: copy the SPECIFIC value the Commander refers to (usually just a name — a
   system, station, or coordinates — not a whole sentence), resolved from recent conversation,
   and confirm back ("Copied Khun to your clipboard."). This is an EXPLICIT copy request, so copy
   regardless of current location — do NOT apply the search "skip if it's your current system"
   rule here.
3. Fail soft: a clipboard error is spoken, never raised into the loop. Register help metadata.
4. (Optional) if the text is clearly an ED system name you MAY validate it against EDSM/Spansh and
   note if unrecognized — but default to copying what the Commander referred to; never block on a
   network call.

Tests (§9): unit (offline) — copy_to_clipboard routes text to a fake clipboard and confirms it; a
clipboard failure is caught and spoken, not raised; help metadata present. No network.

Constraints: no real clipboard in the default pytest run (inject copy); LLM-native; fail soft.

Acceptance: bare pytest green and offline; manual: ask "where is Elvira Martuuk's system", then
"copy that system to my clipboard" and confirm the right system name lands on the clipboard. Stop.
```

---

## Audio / Comms / Chatter Subsystem (Prompts C1–C11)

A separate subsystem: an atmospheric audio layer — a multi-bus mixer with per-bus DSP,
context-driven "space chatter," and comms voicing of ED `ReceiveText` events. AI-native, but the
**LLM is never in the realtime audio path** — it only produces text that is validated and routed.

**Direction:** mirror the search subsystem's discipline — a **cue registry** (modeled on the help
registry), structural (not prompt-based) safety, determinism over randomness where it aids
testing, and fail-closed behavior with graceful cockpit fallback. Over-talking is the primary
failure mode; hard rate limits + cooldowns everywhere.

**Reuse map:** `covas/tts.py` + `covas/audio.py` (current PCM playback), `covas/providers/` (TTS
voices), `covas/ed/` (journal + Status + `EDContext`), and `help_capability.py` + `base.py` (the
registry pattern the cue registry mirrors). Music/SFX assets are local + git-ignored (supply your
own rights), like `sounds/`.

**Ordering:** infra → registry → driver → the safety-critical channel gate (C4, fully specified,
highest safety value) → variants → chatter → music → example cues.

**Boilerplate — paste at the top of Prompts C2–C8 when you run them:**

> **Cue-registry requirement.** Every SFX, chatter category, music context, and comms cue MUST
> self-register with the cue registry (C2): its eligible game states, target bus, cooldown/rate
> limit, and phrasing pool or sample set. A cue that registers without a bus or without eligibility
> states must FAIL a test. A cue with no valid trigger states is silent — it must not error. Where
> a capability adds a voice-commandable control (mute, volume, toggle), also register help metadata
> per the search subsystem's help requirement.
>
> **Structural safety.** Hallucination prevention is structural, not prompt-based — validated
> output spaces, fail-closed tests, loud in CI but graceful fallback in the cockpit. The LLM is
> NEVER in the realtime audio path; it only produces text that is then validated and routed.
> Determinism over randomness where it aids testability (phrasing rotation, not random selection).
> Enforce hard rate limits + cooldowns.

---

### C1 — Audio bus mixer + per-bus DSP (infrastructure)

```
Read CLAUDE.md, DESIGN_AND_ROADMAP.md, covas/tts.py + covas/audio.py (current PCM playback), and
covas/providers/ (TTS).

Branch: feature/audio-bus-mixer

Goal: a multi-bus audio mixer with per-bus DSP and independent volume — the shared foundation for
comms, chatter, SFX, music, and alerts.

Tasks:
1. Named buses, each with its own processing chain + independent volume:
   - COVAS  — clean, full volume, no processing (the ship's own assistant).
   - Comms  — bandpass ~300–3000 Hz + light static bed + compression, ducked a few dB under COVAS.
   - Ambient — SFX layers.  Music — ambient music.  Alert — warning stings.
2. A mixer that mixes concurrent sources across buses to the output device (sounddevice callback
   pulling from active per-bus sources). Existing COVAS speech routes through the COVAS bus with no
   change in character.
3. Per-bus DSP as PURE functions on PCM buffers (unit-testable, no device): biquad bandpass,
   additive noise bed, simple compressor, gain/duck. The comms radio treatment is applied by DSP
   at mix time — NOT per-file pre-editing.
4. Extend the TTS path so a spoken line can target a chosen BUS and a chosen VOICE (comms uses a
   different voice + the comms bus; COVAS uses the clean bus + the COVAS voice). Keep the injectable
   seam so the default test run never opens an audio device.
5. Config [audio.buses]: per-bus volume + enable, sensible defaults.

Tests (§9): unit (offline) — DSP functions transform a known buffer correctly (bandpass attenuates
out-of-band tones, gain scales, compressor limits peaks); the mixer sums sources with per-bus gain
deterministically on in-memory buffers. No device in the default run; integration+local for real
playback.

Acceptance: bare pytest green and offline; manual: COVAS speech unchanged; a test tone through the
comms bus sounds radio-filtered. Stop for review.
```

### C2 — Cue registry contract

```
[Prepend the C-series boilerplate above.]
Read CLAUDE.md, covas/capabilities/help_capability.py + base.py (the registry pattern to mirror),
and C1.

Branch: feature/cue-registry

Goal: a cue registry (mirroring the help registry) that every SFX, chatter category, music context,
and comms cue registers with — structurally enforced.

Tasks:
1. A CueRegistry + cue definition: eligible game states, target bus, cooldown/rate limit, and a
   phrasing pool (chatter) OR sample set (SFX) OR context tag (music).
2. A registration-contract test that FAILS if a cue registers without a bus or without eligibility
   states. A cue with an empty trigger-state set is valid but never eligible (silent, no error).
3. Pure query helpers: given a game-state eligibility set, return eligible cues per bus (for C3).

Tests: unit (offline) — contract test flags an incomplete cue, passes a complete one; eligibility
query returns the right cues for a state; an empty-trigger cue stays silent.

Acceptance: bare pytest green; this contract test gates the later C-prompts. Stop.
```

### C3 — Game-state driver + eligibility engine + rate governor

```
[Prepend the C-series boilerplate above.]
Read CLAUDE.md, covas/ed/ (journal + Status + EDContext), C2, C1.

Branch: feature/cue-driver

Goal: drive the mixer from live game state — compute eligibility, let the registry decide which
cues may play, and enforce the anti-over-talking budget.

Tasks:
1. From journal + Status + EDContext, compute a current eligibility set (population, docked,
   supercruise, conflict zone, deep space, hyperspace, fuel state, near-star, …).
2. A governor enforcing per-cue cooldowns + a global rate cap so the system never over-talks (the
   primary failure mode). Deterministic selection among eligible cues (rotation, not random).
3. On state-change/tick, pick allowed cues and hand them to the mixer/bus. Opt-in via config, off
   by default.

Tests: unit (offline) — eligibility from Status/journal fixtures; the governor blocks a cue in
cooldown and enforces the global cap; rotation is deterministic. No device.

Acceptance: bare pytest green and offline. Stop.
```

### C4 — ReceiveText capture + channel gate + fail-closed classifier  [CORE SAFETY CONTRACT]

```
[Prepend the C-series boilerplate above.]
Read CLAUDE.md, covas/ed/journal.py, and C1–C3. THIS PROMPT CARRIES THE CORE SAFETY CONTRACT —
build it fully and test it exhaustively.

Branch: feature/comms-channel-gate

Goal: capture ED `ReceiveText` events and decide — FAIL-CLOSED — which may ever be voiced, and how.
ReceiveText is comms-panel TEXT the game never voices itself; voicing it at all is our feature, so
this gate is safety-critical.

Facts (verified against the journal schema): ReceiveText fields = From, From_Localised, Message,
Message_Localised, Channel. Channel enum includes player, npc, local, wing, friend, voicechat (also
squadron, starsystem). Real players carry a "CMDR"/$cmdr_decorate-prefixed From_Localised; NPCs do
not.

Tasks:
1. A PURE, deterministic classifier classify(event) -> Decision, exhaustively tested:
   - channel == 'player' -> VOICE VERBATIM (a real human DM'ing the Commander directly); fixed MALE
                            voice (journal has no gender data — do NOT randomize; male is right more
                            often than a coin flip).
   - channel == 'npc'    -> ELIGIBLE for the variant pipeline (C5); voice deterministic from the NPC
                            name if gendered, else default.
   - channel in {local, wing, friend, voicechat, squadron, starsystem} -> DROP, never TTS (the
                            Open-population firehose of real players).
   - anything else / unknown / missing channel -> AMBIGUOUS: voice ONLY if From_Localised does NOT
                            start with "CMDR" (treat as npc-like); if CMDR-prefixed (a real
                            commander), DROP.
   Net gate = player OR npc OR (ambiguous AND NOT CMDR-prefixed). If a line cannot be confidently
   classified as an NPC line OR the Commander's own direct player DM, DO NOT voice it. Silence beats
   voicing a random stranger.
2. Template-identity dedup: normalize Message (strip numbers + proper names) into a template hash;
   cooldown by SOURCE TEMPLATE, not exact string, so station spam / repeated announcements aren't
   re-voiced (even reworded) per jump. Wire into the C3 governor.
3. Emit a structured VoiceableComms record (channel, decision, source text, chosen voice, allowed
   variant tier) for C5. Route NOTHING to TTS here — this prompt only classifies + dedups.

Tests (§9): unit (offline), EXHAUSTIVE — one case per channel value; player+CMDR = verbatim/male;
npc = variant-eligible; each firehose channel dropped; ambiguous+CMDR dropped; ambiguous+non-CMDR
allowed as npc; missing/empty channel handled per the rule; dedup collapses two numerically-
different instances of the same template; and an explicit test that an unclassifiable line is NOT
voiced (the fail-closed default). The classifier must NEVER return "voice" for a real-player
broadcast.

Constraints: pure + deterministic; fail-closed; no TTS in this prompt.

Acceptance: bare pytest green; the exhaustive channel matrix passes; show me the decision table.
Stop for review — I'll validate this one carefully before C5.
```

### C5 — Comms variant generator + validator + verbatim fallback

```
[Prepend the C-series boilerplate above.]
Read CLAUDE.md, C4 (VoiceableComms records), C1 (comms bus + voice selection), covas/llm.py.

Branch: feature/comms-variants

Goal: voice the gated comms lines — player DMs verbatim; NPC lines at their allowed variant tier,
with a validator and a guaranteed verbatim fallback. The LLM produces text only; never in the
realtime audio path.

Tasks:
1. Variant tiers for npc lines (the cue category picks the allowed tier): verbatim (exact,
   fact-bearing/mission lines); paraphrase (same meaning, reworded, fact-neutral); riff (same
   intent, embellished with tone, asserts nothing checkable).
2. A validator on any generated variant: reject if it introduces a proper noun not in the source,
   changes a number, or invents a threat/instruction. On ANY failure -> fall back to voicing the
   verbatim SOURCE line (always the guaranteed-safe fallback).
3. Route final text to TTS on the comms bus (radio treatment) with the chosen voice; player DMs go
   verbatim on the fixed male voice. Respect the C3 governor + C4 dedup.

Tests (§9): unit (offline) — validator catches an added proper noun / changed number / invented
threat and triggers verbatim fallback; a clean paraphrase passes; player lines are never
paraphrased; LLM calls faked (no network in the default run; a paid-integration test may exercise
the real generator).

Acceptance: bare pytest green and offline; manual: an NPC line gets a safe riff, a tampered variant
falls back to verbatim, a player DM is read verbatim on the comms bus. Stop.
```

### C6 — Space chatter generator

```
[Prepend the C-series boilerplate above.]
Read CLAUDE.md, C2 (cue registry), C3 (driver), C1 (buses).

Branch: feature/space-chatter

Goal: context-driven "space chatter" — invented ambient lines from game state, template-pool
driven, with fact-bearing gating.

Tasks:
1. Chatter categories register with the cue registry (eligibility states, bus, cooldown, phrasing
   pool). Eligibility is a function of state — station-traffic chatter ineligible in unpopulated
   systems; deep-space musings MORE eligible there.
2. Template pools drive fact-bearing lines (fact_bearing:true) — no LLM asserting game facts (help
   discipline). LLM generation permitted ONLY for low-stakes flavor musings flagged
   fact_bearing:false (nothing checkable). Deterministic rotation within a pool.
3. Governed by C3 (cooldowns/rate) so chatter never over-talks.

Tests: unit (offline) — eligibility gating per state; fact_bearing:true never routes to the LLM;
fact_bearing:false flavor asserts no checkable fact; rotation deterministic.

Acceptance: bare pytest green and offline. Stop.
```

### C7 — Music library + context crossfade

```
[Prepend the C-series boilerplate above.]
Read CLAUDE.md, C1 (music bus), C3 (driver).

Branch: feature/music-library

Goal: ambient music, context-crossfaded — a curated PRE-GENERATED library, NOT a live API.

Tasks:
1. A curated music library tagged by context (deep space, populated system, nebula, near-star,
   combat-adjacent), played via the music bus with crossfades keyed to game-state transitions.
2. Music files are local assets (git-ignored, supply your own rights, like sounds/); the registry
   maps context tags -> track sets. Suno has no official public API (mid-2026), so generation is
   OUT of the runtime path — leave a clearly-marked seam for a future "generate fresh track" if an
   official API lands, but it must NOT be a runtime dependency.
3. Crossfade logic is pure/testable (gain envelopes over buffers); device playback is
   integration/manual.

Tests: unit (offline) — context->track selection; crossfade gain-envelope math; no runtime
generation dependency.

Acceptance: bare pytest green and offline. Stop.
```

### C8 — Worked-example cues (pirate interdiction + SFX layers)

```
[Prepend the C-series boilerplate above.]
Read CLAUDE.md, C1–C6.

Branch: feature/example-cues

Goal: prove the layered-cue pattern with real cues.

Tasks:
1. Pirate-interdiction layered cue off journal Interdiction / UnderAttack: (a) warning sting ->
   alert bus; (b) assistant threat-assessment line -> COVAS bus, clean, DETERMINISTIC phrasing
   rotation from a pool; (c) the pirate's own line -> comms bus, radio/static, ducked. All via the
   cue registry + governor.
2. Ambient SFX layers registered as cues: Thargoid voices, hyperspace weirdness, space-radiation
   bed — eligibility-gated (e.g. hyperspace, deep space) on the ambient bus.
3. Everything opt-in, off by default; governed by cooldowns/rate.

Tests: unit (offline) — the interdiction cue emits the three layers to the right buses in order;
SFX eligibility gating; rotation deterministic.

Acceptance: bare pytest green and offline; manual: trigger an interdiction and confirm the layered
audio. Stop for review.
```

### C9 — Integration: wire the audio layer into the running app (closing step)

```
[Prepend the C-series boilerplate above.]
Read CLAUDE.md, DESIGN_AND_ROADMAP.md, covas/app.py + run_covas.py + run_covas_ui.py + web.py, the
C1–C8 components (the bus mixer, cue registry/governor/driver, comms gate + voicer, chatter, music,
interdiction/SFX cues), covas/settings_schema.py, and MANUAL_TESTS.md.

Branch: feature/audio-integration

Goal: WIRE the C1–C8 audio layer into the running app so it actually plays in-game, and close the
three gaps that were blocked on it (live settings, voice controls + help, in-game manual tests).
C1–C8 built and unit-tested each component behind a play/emit/speak seam; nothing constructs or
connects them in the live loop. THIS is the assembly step. (It's genuinely new scope — the C1–C8
prompts were infra-only and stopped at their seams.)

Tasks:
1. UNIFY THE OUTPUT FIRST — this is the crux. Today covas/tts.py opens its OWN sounddevice stream
   straight to the device, and the C1 BusMixer opens a SEPARATE one — so speech currently bypasses
   the bus system entirely and two things would fight over the device. Make the BusMixer the SINGLE
   thing that opens the audio device, and route ALL playback through it: COVAS speech -> the COVAS
   bus, comms -> the Comms bus (radio DSP), cues/SFX/music/alerts -> their buses. Retire the direct
   device playback in tts.py / audio.py / piper_tts (submit their PCM to the mixer instead).
   Preserve barge-in + cancel semantics through the mixer. Nothing but the mixer touches the device.
2. Compose the layer at the app composition root (app.py): construct ONE shared BusMixer + cue
   governor; subscribe the comms bus to ReceiveText -> the channel gate (C4) -> CommsVoicer (C5) ->
   the Comms bus; drive the CueDriver (C3) from EDContext/state changes feeding chatter (C6) + SFX
   (C8); wire the InterdictionCue (C8) off Interdiction/UnderAttack; start the MusicDirector (C7)
   with context crossfades; resolve comms voice-ids. Everything OFF by default (honor the enable
   flags). FAIL SOFT — a dead audio component must never break the voice loop or block a watcher.
3. Voice controls as a small Capability (goes through the tool registry the loop already runs):
   tools for "mute/unmute the chatter", "turn the music up/down" / "stop the music", "quiet the
   comms", and a master "mute all ambient audio". Register FULL help metadata (this satisfies the
   help requirement). These flip the same runtime state the settings set.
4. Real settings: add the audio settings a running consumer now honors to settings_schema.py —
   master enables ([audio.cues], [music], [audio.interdiction], comms on/off), per-bus volume, and
   comms voice pickers (from the live ElevenLabs voice source) — and make them take EFFECT at
   runtime (apply/reload like other settings) so they aren't dead. Skip settings the schema can't
   model (track lists, DSP tables); leave those file-configured with a note.
5. Fix the dead flag + audit: InterdictionCue.from_cfg must honor [audio.interdiction].enabled
   (currently it reads only `sting`). Then audit ALL ~9 audio config groups so every advertised
   enable/volume flag is actually consumed by a live component — no more dead config.
6. Manual tests: add an in-game C-series section to MANUAL_TESTS.md — enable the layer; jump/dock
   to hear chatter; trigger an interdiction for the layered cue; receive an NPC/station line and a
   player DM to hear the comms bus + voices; toggle each voice control; change an audio setting and
   confirm it takes effect live.

Tests (§9, offline): unit — COVAS speech routes through the mixer's COVAS bus (assert it does NOT
open a second device stream); app composition constructs and wires the components with fakes (no
device, no network, no journal watcher); a ReceiveText fixture flows gate -> voicer -> fake TTS on
the comms bus; the voice-control tools flip runtime state; a settings change applies to the live
components; interdiction.enabled=false suppresses the cue; every audio enable flag has a consumer.
The existing C1–C8 tests still pass.

Acceptance: bare pytest green and offline; manual — the in-game section actually produces audio and
the settings/voice-controls take effect; no dead enable flags remain. Stop for review.
```

### C10 — Voice cast (voice pool + deterministic assignment + provider routing)

```
[Prepend the C-series boilerplate above.]
Read CLAUDE.md, covas/mixer/comms.py + variants.py (comms voicing), C1 (per-line voice on the
mixer), covas/providers/ (ElevenLabs + Piper TTS), covas/elevenlabs.py (voice list). Run AFTER C9.

Branch: feature/voice-cast

Goal: a "voice cast" for everything the audio layer speaks — a configurable voice POOL with
DETERMINISTIC identity->voice assignment, and provider routing so COVAS stays on ElevenLabs while
the NPC/comms/chatter cast uses local Piper (free, runs alongside the game, no ElevenLabs burn).

Tasks:
1. A voice-cast module: a pool of voices (per provider) and assign(identity) -> voice that is
   DETERMINISTIC — hash a stable identity key (the ReceiveText From name, a station/carrier name,
   an NPC id) to a voice, so different speakers sound different and the SAME speaker stays
   consistent across a session. Player DMs -> the fixed male voice (from C4); COVAS -> the persona
   voice.
2. Provider routing: COVAS speech -> ElevenLabs (the persona voice); the comms/NPC/chatter cast ->
   Piper by default (multiple local voice models = the pool), with ElevenLabs as an opt-in override
   per bus/category. Wire through the C1 per-line voice + bus selection.
3. Config [audio.voices]: the ElevenLabs male-DM voice, the persona voice, and the Piper voice pool
   (a list of installed Piper models) + which provider each bus/category uses. Surface the pickers
   in settings_schema.py (build on C9's settings).
4. EXCLUSION HOOK: the pool builder must skip voices unusable for TTS on the account — leave a clean
   filter point the ™-voice bug fix plugs into (exclude from BOTH the picker and the
   random/atmospheric pool). Never select an unusable voice.

Tests (§9, offline): unit — assign() is deterministic (same identity -> same voice; different
identities spread across the pool); player DM -> male voice; routing picks Piper for the cast and
ElevenLabs for COVAS; the exclusion hook drops a flagged voice. No network; TTS calls faked.

Acceptance: bare pytest green and offline; manual — two different NPCs sound different and stable;
COVAS on your ElevenLabs voice, the cast on Piper. Stop for review.
```

### C11 — Drop-in content pipeline (auto-discover assets + line pools)

```
[Prepend the C-series boilerplate above.]
Read CLAUDE.md, covas/mixer/cues.py + example_cues.py + chatter.py + music.py, and the C2 registry.
Run AFTER C9 (needs the wired cues).

Branch: feature/audio-content-dropin

Goal: make audio + line content DROP-IN — scan convention folders at load and auto-populate cue
sample sets, music tracks, and chatter/threat line pools, so adding content is dropping a file in
the right place (or editing a text file) with NO code/config edits. A missing folder/file leaves
that cue simply silent (no error), per the fail-closed-silent rule.

Conventions (create the folder skeleton + a short README in each; add all to .gitignore):
- SFX samples: audio/sfx/<cue>/*.{wav,ogg,flac} — <cue> in {thargoid_voices, space_radiation,
  hyperspace_weirdness, interdiction_sting}. ANY filenames; every file in the folder joins that
  cue's sample set (deterministic rotation). Migrate the hardcoded sounds/interdiction_sting.wav
  to audio/sfx/interdiction_sting/.
- Music tracks: audio/music/<context>/*.{wav,ogg,flac,mp3} — <context> in {deep_space, populated,
  unpopulated, nebula, near_star, combat_adjacent, scooping_fuel, default}. ANY filenames.
- Line pools: content/chatter/<category>.txt — <category> in {deep_space_musing, open_space_idle,
  station_traffic, supercruise_ambient}; one spoken line per non-blank line, '#' lines ignored.
  And content/interdiction_threat.txt for the assistant threat-assessment pool.

Tasks:
1. A content loader that at startup scans these folders and attaches samples/tracks/phrasings to
   the matching registered cues/music contexts. Unknown/extra folders ignored; empty/missing ones
   leave that cue silent (no error).
2. Create the folder skeleton with a README-in-each (the drop rule + accepted formats); add audio/
   and content/ to .gitignore (keep any committed example line file if useful).
3. A "content status" report (a log line + a settings/web readout): per cue/context, how many
   files/lines were found and which are empty/silent — so it's obvious what still needs content.
4. Decode via soundfile (wav/flac/ogg; note mp3 depends on libsndfile). Validate chatter/threat
   lines before speaking (help/anti-hallucination rules).

Tests (§9, offline): unit — the loader maps a temp folder tree to the right cues/contexts; a
missing folder yields a silent (not errored) cue; chatter .txt parses lines + ignores '#' comments;
the content-status counts are correct. No device/network.

Acceptance: bare pytest green and offline; manual — drop a wav into audio/sfx/thargoid_voices/ and
hear it on the ambient bus; add lines to content/chatter/station_traffic.txt and hear them; the
content-status report reflects what's present. Stop for review.
```

---

### Tips for running these
- Keep `CLAUDE.md` current — Claude Code reads it every session.
- If a step balloons, ask Claude Code to split it and update this file.
- Merge (or tag) each branch before the next, so you can always roll back to a known-good
  point without losing context.
