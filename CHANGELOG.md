# Changelog

All notable changes to COVAS++ are recorded here so you can decide whether to update. The format
is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and COVAS++ follows
[Semantic Versioning](https://semver.org/): while the app is pre-1.0, **minor** versions add
features and **patch** versions are fixes.

The **authoritative, full notes for every release** — including versions older than those listed
here — live on the [GitHub Releases page](https://github.com/dseelinger/CovasPlusPlus/releases).
The in-app update notifier also points you there when a newer build exists.

## [Unreleased]

_Nothing released yet._

## [0.18.1] — 2026-07-19

A focused **security-hardening sweep** — four fixes that shrink the prompt-injection blast radius
(COVAS++ reads untrusted text: web results, in-game chatter, remembered facts).

### Changed
- **Keybind automation now ships OFF by default.** A fresh install no longer offers the model the
  ship-control tool until you opt in. ([#188])
- **Safety toggles are no longer voice-writable.** The keybind/macro/comms safety gates (combat
  guard, confirmations, master switches) can't be changed by the `set_setting` voice tool — only
  in the web control panel or config files. ([#183])

### Security
- **Recalled memory is fenced as reference data.** A stored fact phrased like an instruction is now
  presented to the model as passive context, not a directive. ([#189])
- **The confirm gate re-states the actual armed action** from the pending action (not the model's
  narration), so a bait-and-switch is audible before it fires. ([#190])

## [0.18.0] — 2026-07-19

Completed the full 2026-07-19 codebase audit (35 confirmed findings across four releases).

### Fixed
- Orchestrator concurrency: closed proactive-lock / turn-claim races and made the event-pump start
  idempotent. ([#177])
- Search & capability correctness: "already there" under a near-override, inverted population
  ranges, route reprompts, engineer bucketing, guarded delivery, fail-soft proactive config.
  ([#180], [#179])
- Mixer: reachable music contexts, no cooldown burn on an all-failed cue, race-safe `layers()`.
  ([#176])
- Jump-range estimate no longer overstates range 5–30× when hull mass is unknown (reports
  "unknown" instead). ([#179])
- First run resolves a real default microphone so a fresh install isn't silently deaf. ([#178])

## [0.17.3] — 2026-07-19

### Fixed
- OpenAI o-series reasoning models: send `max_completion_tokens` (every o-series turn used to
  `400`). ([#171])
- Event bus: ordered backlog replay under lock + bounded subscriber queues (drop-oldest on a
  stalled log consumer). ([#172])

## [0.17.2] — 2026-07-19

### Fixed
- Journal monitoring is fail-soft: one bad event can no longer permanently kill it. ([#167])
- OpenAI/Gemini re-evaluate the system prompt per turn so the per-ship crew roster follows a ship
  swap. ([#168])

## [0.17.1] — 2026-07-19

### Security
- Control-panel origin/CSRF hardening (advisory **GHSA-3mxj-5926-rqmr**).

## [0.17.0] — 2026-07-18

### Added
- Engineering-assistant epic — per-ship build memory + a conversational engineering planner bridged
  to the checklist. ([#132]–[#135], [#139])
- Route hazard / scoopable-star callouts. ([#147], [#148])
- Proactive place/visit lines and long-hyperspace flavor. ([#138], [#149])
- Persona speech arbiter, mis-voice fix, and Carrier Captain UI. ([#131], [#137], [#146])
- VR-HUD placement model and fixes. ([#140]–[#145])

## [0.16.0] — 2026-07-18

Feature and polish wave. See the
[GitHub Release](https://github.com/dseelinger/CovasPlusPlus/releases/tag/v0.16.0) for full notes.

## [0.15.1] — 2026-07-18

### Fixed
- Four parked polish findings from the search-family review, with spoken output verified unchanged.

## [0.15.0] — 2026-07-18

### Changed
- Consolidation wave: capability wiring extracted from `app.py` into `covas/bootstrap.py`; the 12
  search/nav modules collapsed into one spec-driven family (LLM surface byte-frozen); product
  pillars + admission rule documented. ([#111]–[#115])

---

Releases before 0.15.0 are listed on the
[GitHub Releases page](https://github.com/dseelinger/CovasPlusPlus/releases).

[Unreleased]: https://github.com/dseelinger/CovasPlusPlus/compare/v0.18.1...HEAD
[0.18.1]: https://github.com/dseelinger/CovasPlusPlus/releases/tag/v0.18.1
[0.18.0]: https://github.com/dseelinger/CovasPlusPlus/releases/tag/v0.18.0
[0.17.3]: https://github.com/dseelinger/CovasPlusPlus/releases/tag/v0.17.3
[0.17.2]: https://github.com/dseelinger/CovasPlusPlus/releases/tag/v0.17.2
[0.17.1]: https://github.com/dseelinger/CovasPlusPlus/releases/tag/v0.17.1
[0.17.0]: https://github.com/dseelinger/CovasPlusPlus/releases/tag/v0.17.0
[0.16.0]: https://github.com/dseelinger/CovasPlusPlus/releases/tag/v0.16.0
[0.15.1]: https://github.com/dseelinger/CovasPlusPlus/releases/tag/v0.15.1
[0.15.0]: https://github.com/dseelinger/CovasPlusPlus/releases/tag/v0.15.0

[#111]: https://github.com/dseelinger/CovasPlusPlus/issues/111
[#112]: https://github.com/dseelinger/CovasPlusPlus/issues/112
[#113]: https://github.com/dseelinger/CovasPlusPlus/issues/113
[#114]: https://github.com/dseelinger/CovasPlusPlus/issues/114
[#115]: https://github.com/dseelinger/CovasPlusPlus/issues/115
[#131]: https://github.com/dseelinger/CovasPlusPlus/issues/131
[#132]: https://github.com/dseelinger/CovasPlusPlus/issues/132
[#133]: https://github.com/dseelinger/CovasPlusPlus/issues/133
[#134]: https://github.com/dseelinger/CovasPlusPlus/issues/134
[#135]: https://github.com/dseelinger/CovasPlusPlus/issues/135
[#137]: https://github.com/dseelinger/CovasPlusPlus/issues/137
[#138]: https://github.com/dseelinger/CovasPlusPlus/issues/138
[#139]: https://github.com/dseelinger/CovasPlusPlus/issues/139
[#140]: https://github.com/dseelinger/CovasPlusPlus/issues/140
[#141]: https://github.com/dseelinger/CovasPlusPlus/issues/141
[#142]: https://github.com/dseelinger/CovasPlusPlus/issues/142
[#143]: https://github.com/dseelinger/CovasPlusPlus/issues/143
[#144]: https://github.com/dseelinger/CovasPlusPlus/issues/144
[#145]: https://github.com/dseelinger/CovasPlusPlus/issues/145
[#146]: https://github.com/dseelinger/CovasPlusPlus/issues/146
[#147]: https://github.com/dseelinger/CovasPlusPlus/issues/147
[#148]: https://github.com/dseelinger/CovasPlusPlus/issues/148
[#149]: https://github.com/dseelinger/CovasPlusPlus/issues/149
[#167]: https://github.com/dseelinger/CovasPlusPlus/issues/167
[#168]: https://github.com/dseelinger/CovasPlusPlus/issues/168
[#171]: https://github.com/dseelinger/CovasPlusPlus/issues/171
[#172]: https://github.com/dseelinger/CovasPlusPlus/issues/172
[#176]: https://github.com/dseelinger/CovasPlusPlus/issues/176
[#177]: https://github.com/dseelinger/CovasPlusPlus/issues/177
[#178]: https://github.com/dseelinger/CovasPlusPlus/issues/178
[#179]: https://github.com/dseelinger/CovasPlusPlus/issues/179
[#180]: https://github.com/dseelinger/CovasPlusPlus/issues/180
[#183]: https://github.com/dseelinger/CovasPlusPlus/issues/183
[#188]: https://github.com/dseelinger/CovasPlusPlus/issues/188
[#189]: https://github.com/dseelinger/CovasPlusPlus/issues/189
[#190]: https://github.com/dseelinger/CovasPlusPlus/issues/190
