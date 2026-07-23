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

## [0.25.2] — 2026-07-23

A **Foundation** patch: automated tests now guard every change, not just releases.

### Changed
- **CI runs lint + unit tests on every PR and every push to `main`.** Until now the test suite
  only ran when an installer was cut, so `main` could move with no test run at all and `ruff` —
  though configured — was never actually enforced. A new workflow makes **`ruff check .` →
  byte-compile → unit `pytest`** a required merge gate, with a separate best-effort job for the
  free integration set. The ruff rule set is pinned so a future ruff release can't silently
  change what the gate enforces. ([#215])

### Fixed
- **Corrected a latent bug in the system RAM probe** surfaced by the newly-enforced linter: the
  non-Windows fallback referenced an unimported name and would have quietly failed. Windows users
  were never affected. ([#215])

### Migration
- Nothing to do — this release changes only the project's own CI and developer tooling; the app
  itself is unchanged.

## [0.25.1] — 2026-07-22

A **Foundation** patch: the Releases page finally hands you an installer.

### Fixed
- **The Releases page now actually carries the Windows installer.** The install docs pointed you to
  a `COVAS++ Setup.exe` download that never existed — every release was source-only. A new CI
  workflow builds the frozen app and Inno Setup installer on each **published** release and attaches
  it (gated on the unit suite, so a broken commit never ships), so the download the docs promise is
  really there. The build stays unsigned by design — SmartScreen still warns on first run. ([#213])

### Migration
- Nothing to do. If you've been running from source, you're unaffected; installer users can now
  update straight from the Releases page.

## [0.25.0] — 2026-07-20

An **Immerse & reach** release: the control panel now speaks your language.

### Added
- **The control panel and first-run wizard are translated into German, French, Russian, Spanish,
  and Portuguese.** Set your reply language and the whole web UI — headings, buttons, navigation,
  hints and the wizard — switches to that language. English is unchanged. These translations are
  **machine-generated and awaiting native-speaker review**, so wording may still be refined; a
  language only activates once its catalog is complete (no half-translated panels). See the
  "Translating the UI" docs to review or improve a catalog. ([#196])

### Migration
- Nothing to do. English is byte-for-byte unchanged; a non-English panel follows your reply language.

## [0.24.0] — 2026-07-20

A **Foundation** release for reach: the control panel is now ready to be translated.

### Changed
- **Control-panel text is extracted for translation.** Every visible string in the web panel and
  first-run wizard is now wired through a lightweight translation helper, with English as the
  baseline. Nothing changes for you today — the panel reads exactly as before — but a translator
  can now add a language by contributing one catalog, and a language only switches on once its
  translation is complete (no half-translated panels). Completes the extraction groundwork for
  localizing the UI; see the new "Translating the UI" docs. ([#196])

### Migration
- Nothing to do. The panel is unchanged in English.

## [0.23.0] — 2026-07-20

An **Immerse & reach** release: numbers and dates now read in your locale — completing the
localization round trip (reply → speech-to-text → voice → formatting).

### Changed
- **Numbers and dates in callouts are formatted for your reply language.** With a non-English
  reply language, credits, distances and short dates in spoken and on-screen callouts use your
  locale's separators — a balance reads `2.000.000` in German (or `2 000 000` in French) where
  English says `2,000,000`, and a Community Goal ends `15. Juli` instead of `Jul 15`. Automatic
  (driven off your reply language); nothing to set. English output is unchanged. ([#199])

### Migration
- Nothing to do. English (and any unmapped language) formats exactly as before.

## [0.22.0] — 2026-07-20

An **Immerse & reach** release: your TTS voice now speaks your reply language too.

### Changed
- **The voice follows your reply language.** Set a non-English **Reply language** and COVAS steers
  an Edge or Azure voice that can't pronounce it to a locale-matched one (e.g. a `de-DE-…` voice for
  German) — so the reply is *read* in that language, not mangled by an English voice. A voice you
  explicitly picked is kept (COVAS flags a mismatch rather than overriding you); ElevenLabs/OpenAI
  voices are multilingual and left alone; English is unaffected. New `[language].match_voice`
  (default on) is the opt-out, and the Edge/Azure voice pickers now list voices for the active
  language. With STT (#197) already following the reply language, one setting now localizes the
  whole round trip. ([#198])

### Migration
- Nothing to do. On-by-default, and it only ever steers an Edge/Azure voice that would otherwise
  mispronounce a non-English reply; set `[language].match_voice = false` to keep your voice fixed.

## [0.21.0] — 2026-07-20

An **Immerse & reach** release: speak to COVAS in your language and it now *hears* you in that
language too — no extra setup.

### Changed
- **Speech-to-text follows your reply language automatically.** `[whisper].language` now ships as
  `"follow"`, so setting **Reply language** to German (or French, Russian, Spanish, Portuguese)
  also transcribes your speech in that language — no separate Whisper setting to change. English
  installs are unchanged. If you set a non-English language, use a **multilingual** Whisper model
  (e.g. `small`): a `.en` model is English-only and COVAS now warns you to switch rather than
  transcribing poorly in silence. ([#197])

### Migration
- Nothing to do. Existing configs with an explicit `[whisper].language` (e.g. `en`) keep that
  forced value; the new default only applies where the key is unset.

## [0.20.0] — 2026-07-20

A **Foundation** release: a fully permissive speech-to-text stack. Nothing changes in how you talk
to COVAS — this removes redistributed GPL code from the installer and makes it smaller.

### Changed
- **Local speech-to-text now runs on whisper.cpp** (via `pywhispercpp`) instead of faster-whisper.
  It reads your microphone audio directly, so the installer no longer pulls in FFmpeg/PyAV — and
  the **GPL-licensed `libx264`/`libx265` codec DLLs that came with it are gone entirely**. The app
  never used them, but they were unavoidable dead weight before. The Windows install is now **100%
  permissively licensed** and **~157 MB smaller** (a 264 → 107 MB app folder). Same model sizes
  (`tiny`…`large-v3`), still CPU-only, still nothing leaves your machine. ([#206])

### Migration
- The `[whisper]` config swapped the faster-whisper-only `device`/`compute_type` keys for a single
  `n_threads` (CPU threads); `model` now defaults to `small.en`. Existing configs keep working —
  the old keys are simply ignored. On first run after updating, COVAS re-downloads the STT model in
  the new whisper.cpp `ggml` format (~465 MB) into your per-user models dir; the old ctranslate2
  weights can be deleted.

## [0.19.0] — 2026-07-20

A **Foundation & reach** release: talk to COVAS in your language, an easier front door, real
accessibility, operational maturity, and the standard open-source project files.

### Added
- **Reply in your language.** A new reply-language setting makes COVAS respond in German, French,
  Russian, Spanish, or Portuguese (English default) — say *"reply in German"* or set it on the
  Settings page. Layer 1 of the localization epic. ([#182])
- **Text mode is now first-class + an accessible control panel.** Type to COVAS and read replies in
  the live log without a mic; the panel gained keyboard navigation, screen-reader live captions,
  labelled controls, keyboard-operable switches, colorblind-safe status, and `prefers-reduced-motion`.
  ([#184])
- **One-click "Test my setup."** A button on the Settings page runs the full health check (keys,
  providers, game data, audio, RAM, updates) and shows a readable, screenshot-able report — no
  terminal, no tracebacks. ([#181])
- **Opt-in crash reports.** Off by default; when enabled, a crash is written to a **redacted** local
  file (keys/username scrubbed) you can attach to a bug — nothing is ever transmitted. ([#186])
- **Documented system requirements** with low-end guidance (smaller Whisper model, CPU-only), and
  the update check now also surfaces in "Test my setup." ([#186])
- **Project files:** `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, issue/PR templates, this changelog, a
  third-party `NOTICE.md`, README maintainer-status + Discord/community links. ([#185], [#187])

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

[Unreleased]: https://github.com/dseelinger/CovasPlusPlus/compare/v0.25.0...HEAD
[0.25.0]: https://github.com/dseelinger/CovasPlusPlus/releases/tag/v0.25.0
[0.24.0]: https://github.com/dseelinger/CovasPlusPlus/releases/tag/v0.24.0
[0.23.0]: https://github.com/dseelinger/CovasPlusPlus/releases/tag/v0.23.0
[0.22.0]: https://github.com/dseelinger/CovasPlusPlus/releases/tag/v0.22.0
[0.21.0]: https://github.com/dseelinger/CovasPlusPlus/releases/tag/v0.21.0
[0.20.0]: https://github.com/dseelinger/CovasPlusPlus/releases/tag/v0.20.0
[0.19.0]: https://github.com/dseelinger/CovasPlusPlus/releases/tag/v0.19.0
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
[#181]: https://github.com/dseelinger/CovasPlusPlus/issues/181
[#182]: https://github.com/dseelinger/CovasPlusPlus/issues/182
[#183]: https://github.com/dseelinger/CovasPlusPlus/issues/183
[#184]: https://github.com/dseelinger/CovasPlusPlus/issues/184
[#185]: https://github.com/dseelinger/CovasPlusPlus/issues/185
[#186]: https://github.com/dseelinger/CovasPlusPlus/issues/186
[#187]: https://github.com/dseelinger/CovasPlusPlus/issues/187
[#188]: https://github.com/dseelinger/CovasPlusPlus/issues/188
[#189]: https://github.com/dseelinger/CovasPlusPlus/issues/189
[#190]: https://github.com/dseelinger/CovasPlusPlus/issues/190
[#197]: https://github.com/dseelinger/CovasPlusPlus/issues/197
[#198]: https://github.com/dseelinger/CovasPlusPlus/issues/198
[#196]: https://github.com/dseelinger/CovasPlusPlus/issues/196
[#199]: https://github.com/dseelinger/CovasPlusPlus/issues/199
[#206]: https://github.com/dseelinger/CovasPlusPlus/issues/206
[#213]: https://github.com/dseelinger/CovasPlusPlus/issues/213
[#215]: https://github.com/dseelinger/CovasPlusPlus/issues/215
