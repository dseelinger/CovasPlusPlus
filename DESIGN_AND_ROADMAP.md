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
 PTT/VAD+mic ─► STT ──►  Conversation loop  ──► Router ──► LLMProvider  {Haiku|Sonnet|Opus}
 ED journal ─► Watcher       │  (app.py)              └─► TTSProvider  {elevenlabs, piper}
 timers ─────► Scheduler     │                            STTProvider  {faster-whisper}
 UI/web ─────────────────────┘
                             │
                     CapabilityRegistry
                     {checklist, ed_context, keybinds…}  ──► tools + event handlers
```

### 3.1 Provider interfaces
Three tiny protocols. Each has 1–2 methods; existing code becomes the first implementation of each.

- **`LLMProvider.stream_reply(messages, cfg, cancel, on_event, tool_handler) -> Iterator[(kind, chunk)]`** — exactly today's `llm.stream_reply` signature. `AnthropicLLM` wraps the current file; tiering is a *parameter* to it, chosen by the Router, not a separate provider. **Provider-agnostic (issue #11):** the Router picks a canonical **tier** (cheap/standard/premium) and each provider's own `[<provider>].tiers` map turns that into a model id, so the same policy drives any cloud LLM. Each provider normalizes to the same `("text"|"thinking"|"search"|"tool", data)` event stream (+ a provider-agnostic `usage` dict costed via `llm.estimate_cost`) the app already consumes — see `providers/base.py` for the full contract. **In-game policy:** any *cloud* LLM (Anthropic today; OpenAI #12 / Gemini #13 next) is fine in-game; only *local* models (`OllamaLLM`) stay off the in-game path, because a useful local model competes with ED for the GPU (not an API limitation) — it's for offline/out-of-game use.
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

The outfitting search (§5) generalizes into a seven-category Spansh voice-search surface — stations, outfitting, minor factions, star systems, signals, misc, and **bodies** (the body / bio-geo signal finder, **#68** — originally a seam, now implemented over `bodies/search`) — plus a first-class help subsystem. Three decisions shape it:

- **LLM-native, not an explicit state machine.** Each category is a *stateless* tool whose description steers the model through conversational slot-filling and disambiguation; conversation history *is* the state, and multi-turn refinement is just the model re-calling with accumulated constraints. We deliberately did **not** build a separate intent-classifier or query-state machine — natural language beats a rigid script for a voice-only UI, and it's already how the working outfitting capability behaves. The one stations-vs-outfitting routing rule lives in the tool descriptions (if a module/ship is named, use outfitting), not a classifier.
- **Help is a templated projection of the registry — no LLM in help *generation*.** The existing `CapabilityRegistry` is *extended* with help metadata (one_liner, example, `group`, per-slot phrasings + help_text) — ONE registry, not a parallel one, so the drift that kills help systems is prevented structurally (a registry test fails if a capability ships without complete help metadata). Help composes registered strings; it never generates prose. It's a **hierarchy so it scales as capabilities grow** (there are already ~13): `idle` ("what can you do") names the GROUPS (navigation & search, your ship, your checklist, community goals, settings…), not every capability; asking about a group lists its capabilities (≤3 + tail); asking about a capability gives its detail. A capability with no `group` is its own singleton group, so nothing is ever unreachable. Every invokable capability carries help metadata (checklist, ship status, ship controls included); ambient-only features (route/auto-honk callouts, the proactive mute) are deliberately out. The other mode — the important one — is *failure recovery* ("I didn't recognize 'power distributer' — did you mean Power Distributor?"). An unresolved utterance is a help request in disguise.
- **Anti-hallucination is structural.** Any capability/slot/module/system name in spoken output must resolve against the registry or a canonical source (Spansh, the journal) before it's spoken; on failure, fall back to a templated error. Never invent a filter or capability. This is why the module taxonomy is bundled and validated offline, and why the LLM is used to *understand* messy speech, not to assert facts.

The prompts are in `CLAUDE_CODE_PROMPTS.md` (Search Prompts 1–6). The shared Spansh client is extracted from the existing `nav/closest.py`; `nav/modules.py` is reused as the outfitting resolver.

**Implemented** (`covas/search/` + `help_capability.py` + the `*_search_capability.py` set): `HelpCapability` (templated idle + failure-recovery, deterministic phrasing rotation); the registry-contract test that fails when a capability ships without complete help metadata; the shared typed `search/spansh.py` client with per-category builders/parsers (`categories.py`, `stations.py`, `systems.py`, `bodies.py`, `factions.py`) and offline fuzzy `faction_index.py` / `vocab.py`; and six LLM-native category capabilities (star systems, stations, minor factions, signals, misc, and the **body / bio-geo signal finder** #68) built on the outfitting pattern. Outfitting is refactored onto the shared client.

The **body finder** (#68 — `BodySearchCapability`, `search_bodies`) is the seventh category and the first over Spansh's synchronous `bodies/search` endpoint. It reuses the generic `CategorySpec` machinery unchanged — the only body-specific code is `parse_bodies` (→ `BodyRecord`) and the `search/bodies.py` vocabulary — so `BODIES` moved from an unimplemented seam to a real category by filling in its verified filter params. Filters (all **live-verified** to NARROW results, 2026-07): `subtype` (Earth-like / ammonia / water world, gas-giant classes, …), `landmark_subtype` (the Odyssey exobiology species — "biological signals of type X"; a spoken **genus** expands to an OR over its species, a **species** pins one, "any biological" spans the catalogue), `atmosphere`, `terraforming_state`, `is_landable`, `distance_to_arrival`. NOTE: the signal-**category** count filter ("has any Biological signal") is **not honoured** by the API under any tested shape, so biology is served precisely via `landmark_subtype`. It's a **targeted single-body lookup** (copy the match's SYSTEM to the clipboard — there's no per-body plot) that complements the Road-to-Riches ROUTE planner (#42). Signal/landmark data is crowdsourced, so a bio match on a very old survey carries a gentle age caveat; body **structure** (subtype etc.) doesn't age and gets none. Config `[bodies]` (enabled default off, `user_agent`, `search_size`); hermetic (recorded `fixtures/spansh_bodies_earthlike_sol.json`) and fail-soft. **Beats the competitors:** EDCoPilot/COVAS:NEXT read a body's data once you're at it; COVAS++ FINDS the nearest body matching a type/biology from anywhere and hands its system to the galaxy map off one spoken command.

### 3.6 Packaging: frozen app + user-data dir (the installable Windows app)

COVAS++ ships as a **double-click Windows app**, not a "install Python, run the server, open a browser" project. Three independent layers wrap the *unchanged* voice loop + Flask UI (full rationale + locked decisions in `INSTALLER_DESIGN.md`):

- **Freeze — PyInstaller (one-folder, CPU-only).** Bundles the interpreter + all `requirements.txt` deps into a read-only install tree, so there's no Python prerequisite. One-folder (not one-file) for faster starts and fewer AV false-positives. Spec is `covas.spec`; build deps live in `requirements-build.txt`, not the base runtime. **Lazy-import guard (issue #20):** the swappable providers are imported *lazily* from `providers/factory.py`, and Edge (`edge-tts`) is the default voice — a bundle that missed it would silently degrade to text. So `covas.spec` `collect_all`s `edge_tts`+`aiohttp` (alongside the onnxruntime/av natives), and `run_covas_app.py --selftest` imports the third-party `edge_tts` **and every provider module**, so a missing bundle fails `build.ps1 -SelfTest` loudly instead of shipping.
- **Window — PyWebView.** `run_covas_app.py` runs Flask + the voice loop on a background thread and hosts the existing control-panel templates in a native OS webview (Edge WebView2, present on Win11). Real window + icon, no browser, no URL bar. **Closing the window quits** — no tray, no background loop. The headless (`run_covas.py`) and browser (`run_covas_ui.py`) entry points are unchanged for source/dev use.
- **Installer — Inno Setup (per-user, unsigned).** `COVAS++ Setup.exe` installs to `%LOCALAPPDATA%\Programs\COVAS++` with **no admin/UAC prompt**, plus Start-menu/desktop shortcuts and an uninstaller. Unsigned by decision → a documented SmartScreen "unknown publisher → Run anyway" step.

The one real architectural change this forced is the **writable user-data dir**. A read-only install tree can't hold config/keys/logs, so `config.py` now distinguishes two roots: **`app_dir()`** (read-only shipped assets — the bundle dir when frozen) and **`data_dir()`** (writable per-user state — `%APPDATA%\COVAS++` when frozen; downloaded models under `%LOCALAPPDATA%`). A **source run keeps both == the project root**, so dev behavior is byte-for-byte unchanged; a frozen build (`sys.frozen`) relocates writable state via the existing `_PATH_FIELDS` mechanism. Both roots are overridable by `COVAS_APP_DIR`/`COVAS_DATA_DIR` (test seam + parity with `[audio].content_root`). This split is *why* updates can replace only the payload and never clobber user settings.

Supporting pieces: **`covas/__version__.py`** is the single source of truth for the version — read by the update-check and (later) stamped into the build. **Updates are Tier 2** (`covas/updates.py` + a UI banner): on launch it compares against the latest GitHub Release, and on a newer one downloads the installer, launches it, and exits so the running exe can be replaced; the installer touches only the payload, so `%APPDATA%\COVAS++` (keys, `overrides.json`, personality, checklist) survives. A **first-run wizard** (in the Flask UI) builds config from nothing — keys, mic, STT-model download, and (when an ElevenLabs key is given) its default voice — since the installer ships no secrets. STT is **download-on-first-run** (faster-whisper `small.en`) to keep the installer small; TTS **speaks out of the box** — the default provider is the **free, bundled Edge (`edge-tts`) voice** (#15/#20, no key required). **ElevenLabs stays optional/premium** (the wizard offers the key and defaults to "George" when set), and **Piper** (local/offline) isn't shipped by the installer, so the offline floor needs a user-downloaded voice; only when the selected provider has no working backend does the loop degrade to text via the existing fail-soft. A tiny **`VersionCapability`** answers "what version are you?" by voice; checking *for* updates stays a UI action.

### 3.7 Secrets at rest — DPAPI key encryption (issue #21)

COVAS++ is a **publicly distributed** app whose users each paste their **own** provider keys (Anthropic / ElevenLabs / OpenAI / Gemini / Azure / Cartesia / Inara), and the audience streams Elite Dangerous. So every key is **encrypted at rest** rather than left as a plaintext file under `%APPDATA%`.

- **Mechanism — Windows DPAPI, `CurrentUser` scope.** `covas/dpapi.py` calls `CryptProtectData`/`CryptUnprotectData` via `ctypes`/`crypt32` (the Python equivalent of .NET `ProtectedData` / `DataProtectionScope.CurrentUser`), mirroring the existing `WinDLL` use in `single_instance.py` and `keybinds/executor.py`. **No new dependency.** Windows owns the key material; the app stores none.
- **Storage format & migration.** Each provider's existing `api_key_file` path is unchanged — the file just holds ciphertext as a sentinel line, `DPAPI:<base64(blob)>`. `is_encrypted()` is a cheap prefix check on that `DPAPI:` sentinel. Reads transparently **migrate** any legacy plaintext key (or a hand-dropped `*.txt`) to encrypted on first read (`firstrun._read_key` → `_migrate_plaintext_key`, best-effort so a failed migration never fails the read). The **Inara** key, once inline plaintext in `overrides.json`, is folded into an encrypted `InaraAPIKey.txt` on first run and the inline value blanked (issue #24) — for a clean "zero plaintext keys anywhere" guarantee.
- **Env-var reads removed (locked decision).** Environment-variable key reading is gone entirely (issue #22): it's a plaintext bypass, and it masked fresh-install testing. Keys come only from the encrypted files, written by the first-run wizard or the masked, write-only **API keys** Settings card (issue #23; keys are never rendered back).
- **A blob that won't decrypt here** (e.g. `%APPDATA%` copied to another PC or account) is treated as **"no key"** with a clear re-enter message, never a crash. DPAPI is Windows-only; on other platforms `protect`/`unprotect` raise and the cross-platform test suite fakes them.
- **Threat model — in scope:** casual disclosure (a copied/synced key file, another local account, a key flashing on a stream) and a stolen unencrypted key file being usable elsewhere. **Out of scope by design:** malware or an admin/root attacker already running *as* the user — DPAPI cannot stop that, and at that point the API key is the least of the user's problems. As defense-in-depth beyond DPAPI, docs recommend **spend-capped / restricted keys** per provider.

### 3.8 Companion HUD — 2D overlay + in-headset VR (spike decision, epic #40 / issue #46)

A glanceable companion HUD on two surfaces: **(a)** a transparent always-on-top **2D
window** (desktop players + a VR fallback), and **(b)** a true **in-headset VR overlay**.
Content is state COVAS++ already has — current system/station, fuel + status, the active
checklist item, the last proactive callout — a *glanceable panel, not an instrument suite*.
The rendering-approach spike (#46) landed these decisions (full writeup + PoCs in
`docs/spikes/hud-spike-46.md`):

- **2D surface — stdlib `tkinter`, transparent + always-on-top.** **Zero new dependency**
  (strongest fit with "standard library first" + the frozen PyInstaller build), and Windows'
  `-transparentcolor` gives color-key transparency *and* click-through for free;
  `overrideredirect` + `-topmost` do borderless always-on-top. Enough for a text/box panel.
  A richer **PyWebView** variant (already bundled — it is the app's main window) that reuses
  the Flask/HTML HUD markup is the documented follow-up, accepting it is opaque / not
  click-through. PySide/Qt (~100 MB frozen) and Dear PyGui are rejected as not earning their
  dependency weight for a companion panel.
- **VR surface — first-party **SteamVR overlay** via `pyopenvr` (`IVROverlay`).** The one new
  dependency **`openvr`** (BSD-3, on PyPI, bundles `openvr_api.dll`; add it *only when the VR
  sub-issue is built*, and `collect_all("openvr")` in `covas.spec` like `av`/`onnxruntime`).
  The enabler: `setOverlayRaw` uploads a **raw RGBA buffer from system memory — no
  DirectX/OpenGL context** — so it is pure Python. Init as `VRApplication_Overlay` (runs
  alongside the game); **fail soft** when SteamVR is not running. This matches how ED renders
  VR: **ED natively speaks OpenVR/SteamVR (and Oculus SDK) — it has no native OpenXR** — so a
  SteamVR overlay composites over the *native* ED render for the majority of PCVR players.
- **"True OpenXR overlay" (`XR_EXTX_overlay`) is rejected.** No shipping runtime (SteamVR,
  Oculus/Meta, WMR) implements it; it exists only as LunarG's provisional D3D11 API-layer
  reference. The runtime-agnostic *API-layer* approach (how OpenKneeboard works) is a
  separate C++/D3D/Vulkan product and out of scope to build.
- **Meta Quest reach.** The SteamVR overlay reaches Quest players **running ED through
  SteamVR** (Link/Air Link with SteamVR active, or Virtual Desktop in SteamVR mode). Quest
  players on the **native Oculus runtime** or **OpenComposite/OpenXR** are covered by the
  documented **2D-window + OpenKneeboard/OVR Toolkit** route (the same window-capture path
  EDCoPilot ships) — we do not build a bespoke OpenXR API layer for them.
- **Architecture — one renderer, two sinks.** A `HudCapability` (off by default,
  `[hud].enabled`) subscribes to the `EventBus`, keeps a tiny provider-agnostic `HudModel`
  snapshot, and a `HudRenderer` draws it once to an offscreen RGBA buffer. A `Flat2DOverlay`
  sink blits it (tkinter) and a `SteamVROverlay` sink uploads it (`setOverlayRaw`); either
  sink can be absent without affecting the other, same fail-soft discipline as the provider
  seam. Redraw only on state change (throttled) — no always-on GPU cost.
- **Beats-competitors thesis.** EDCoPilot/COVAS:NEXT reach VR only as a *dumb 2D-window
  mirror* the user brings in with third-party capture tooling (OpenKneeboard + OpenComposite).
  COVAS++ ships a **first-party, context-aware SteamVR overlay needing no third-party tool for
  the common SteamVR case** — because we own the renderer + game state, the HUD can *react*
  (highlight the active checklist item, flash on a callout, fade when idle) instead of
  mirroring a window, and it composites over ED's native SteamVR render with zero extra setup.
- **Rough effort:** 2D sub-issue ~2–3 days (no new dep); VR sub-issue ~3–5 days (most of it
  on-hardware placement/legibility iteration) + the `openvr` dep. **Spike only — no product
  commitment yet; the 2D and VR builds are separate sub-issues under epic #40.**

**Shipped — the 2D overlay (issue #47).** The flat-screen surface is now built as
`covas/capabilities/hud_capability.py`, following the spike verbatim (stdlib `tkinter`,
transparent + always-on-top + click-through) and the project's "pure core + injected I/O"
discipline:

- **`HudModel` — the pure data adapter (unit-tested, headless).** It folds EventBus events
  (`status` → voice-loop state; `log` "COVAS" lines prefixed `(proactive)`/`(route)` → last
  callout; `ed_event` `NavRoute`/`FSDTarget`/`FSDJump`/`NavRouteClear` → a `RouteTracker` for
  jumps-remaining + next-star-scoopable) plus two **injected getters** (the checklist line, the
  parsed NavRoute) into an immutable `HudSnapshot` of the four display fields. No tkinter, no
  file I/O of its own — the whole adapter is exercised offline with crafted events + fakes; the
  window is never opened in the default suite.
- **`HudView` — the thin tkinter sink, guarded.** Created only when the HUD is enabled AND a
  display is available (`make_view` returns `None` otherwise — headless/CI safe). Every Tk call
  runs on the view's own thread; the outside world only flips thread-safe `show`/`hide`/`close`
  flags that a periodic on-thread poll applies, sidestepping Tkinter's single-thread rule
  without cross-thread `after()`.
- **`HudCapability` — always registered so the toggle works live.** It advertises no LLM tools
  (a non-interactive view) and only feeds the model from the bus. Visibility is reconciled
  against `[hud].enabled` by a **direct** `App._reconcile_hud()` call from
  `_after_settings_change` — so the toggle (Settings page or the voice `set_setting` path,
  projecting from the same `hud.enabled` schema entry) does not depend on the event pump. The
  shared event pump is started only when the HUD is actually enabled (a disabled HUD adds no
  thread; existing "no ambient feature → no pump" behaviour is preserved).
- **Off by default, fail-soft.** `[hud].enabled = false`; a missing toolkit/display, or any Tk
  error, degrades to "no overlay" and logs — it never crosses into the voice loop.

**Shipped — the VR overlay (issue #48).** The in-headset surface is now built as
`covas/capabilities/vr_hud.py`, following the spike verbatim (SteamVR `IVROverlay` via the
`openvr` binding, `setOverlayRaw` from a raw RGBA buffer). It is the **second sink** the "one
renderer, two sinks" design called for — it **reuses the same `HudModel`/`HudSnapshot`**; only
the rendering surface differs:

- **`render_snapshot_rgba` — the pure rasterizer (unit-tested, no runtime).** It paints a
  `HudSnapshot` onto an HxWx4 uint8 **RGBA** buffer with a **built-in 5x7 bitmap font — zero new
  runtime dependency** (only numpy, already required); text folds to ASCII/caps so arbitrary
  game text degrades gracefully. Same four rows as the 2D panel, from the same snapshot.
- **`VrPlacement` / `resolve_transform` — pure placement math.** A placement mode
  (`world` cockpit-fixed / `head` view-locked) + a physical width become an OpenVR 3x4
  transform; the mode picks the binding (`setOverlayTransformAbsolute` vs
  `…TrackedDeviceRelative`). Clamped and unit-tested — a bad setting can't place it unusably.
- **`VrHudView` / `make_vr_view` — the guarded sink.** `openvr` is imported **lazily** and
  SteamVR is `init`'d as `VRApplication_Overlay` only when the VR HUD is enabled; **any**
  import/init/runtime failure returns `None` (so `make_vr_view` yields "no VR surface"),
  mirroring how `make_view` returns `None` with no tkinter/display. All OpenVR calls live on one
  daemon thread; the outside world flips thread-safe `show`/`hide`/`close` flags, and the RGBA
  buffer re-uploads only when the snapshot changes (no always-on cost).
- **One capability, two independent surfaces.** `HudCapability` gained a VR sink alongside the
  2D view; `App._reconcile_hud()` reconciles both against `[hud].enabled` / `[hud].vr_enabled`,
  each fail-soft and independent (a headless desktop doesn't stop the VR overlay, and no VR
  runtime doesn't stop the 2D window). The event pump starts when **either** is enabled.
- **`openvr` is an OPTIONAL dependency.** It stays commented in `requirements.txt` (`pip install
  openvr` to opt in); the app and the **default test suite run without it installed** — the
  rasterizer and placement are covered with numpy-only fakes, `openvr` is never imported in CI.
  `covas.spec` collects it (bundling `openvr_api.dll`) **only when present**, so a freeze without
  it still succeeds. Off by default (`[hud].vr_enabled = false`).

---

## 4. Cloud model tiering strategy

Local LLMs are off the table (see the decision note up top): a useful model competes with Elite Dangerous for the GPU. So instead of local-vs-cloud, tier across **cloud** models — answer routine turns on the cheapest capable one, escalate only when the turn earns it.

**Provider-agnostic tiers (issue #11).** The router now chooses one of three *canonical* tiers — **cheap / standard / premium** — and the concrete model comes from the ACTIVE `[llm].provider`'s tier map. For Anthropic that map is `[router].{default,escalate,premium}_model` (Haiku/Sonnet/Opus) and the router-off model is `[anthropic].model` — unchanged. Any other cloud provider advertises its own map via `[<provider>].tiers.{cheap,standard,premium}` (or a single `[<provider>].model`). So the *same* deterministic policy below drives OpenAI (#12) and Gemini (#13) the moment they're added — the router picks a tier; the provider supplies the id. (The tier names below read Haiku/Sonnet/Opus because Anthropic is the default provider.)

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

### Implemented — on-foot / SRV awareness (#54)
Awareness + callouts extend into the Odyssey **on-foot** and **SRV** modes COVAS++ was previously silent in, reusing the same `game_mode` signal (#29) and the existing proactive discipline — no new loop branches, no bypassed cooldowns.
- **Context folding.** `EDContext` gains on-foot vitals (`oxygen`, `health`, `temperature`, `gravity`, from Status.json's Odyssey fields — cleared to `None` on re-boarding so nothing lingers), SRV `srv_hull` (fed by the journal: `LaunchSRV` → 1.0, `HullDamage` **only while `game_mode == srv`**, `DockSRV` → cleared), and exobiology sampling progress (`ScanOrganic` genus + samples-logged, kept **off** the cached summary as structured/rare state). `summary()` voices the on-foot/SRV vitals only in the matching mode so a ship flight isn't cluttered.
- **Proactive callouts** ride the existing `ProactivePolicy` whitelist + cooldowns: `ScanOrganic` ("sample two of three logged — one more to analyse"), status-derived `OxygenLow`/`HealthLow` (a downward threshold crossing, since oxygen/health have no ED flag), and journal-derived `SrvHullLow` ("hull's getting low"). All default-on in the whitelist but still gated by `[proactive].enabled` (off by default) and both cooldowns.
- **Read tools** mirror the ship reads: `OnFootSrvCapability` serves `on_foot_status`, `srv_status`, and `bio_scan_progress` ("how many samples do I need") as free local reads.

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

### Implemented — generalized action model (Tier-1 foundation, #29)
Before any *new* action batch lands, the prototype is generalized along four axes — all
additive, all still behind the same safety layer — so growing the action set is new **modules**
and **data**, not new branches in the capability or the loop:

- **Game-mode signal on `EDContext`.** A `game_mode` field (`mainship` / `fighter` / `srv` /
  `on_foot` / `None`) folded from Status.json by a pure `game_mode_from_flags(flags, flags2)`:
  ship modes come from the `Flags` bits `InMainShip`/`InFighter`/`InSRV` (24–26); **on-foot is a
  *separate* field**, `Flags2` bit 0 (Odyssey), so it's detected *positively* rather than
  inferred from "no ship bits" — which is also true on the menu/loading screens. The mode clears
  to `None` (unknown) in the menu rather than sticking stale. Canonical mode strings live in a
  zero-dependency leaf `ed/modes.py` shared by the producer (status decode) and consumer
  (keybinds), so neither imports the other.
- **Mode-gated tool advertisement.** `KeybindCapability` advertises only actions valid for the
  *current* mode (each `Macro` declares a `modes` set; empty = any mode), so the model isn't
  offered on-foot actions while flying (mirrors COVAS:NEXT's mode filter). The gate is enforced
  again at arm **and** re-checked at confirm (the Commander may disembark between the two). When
  the mode is **unknown** (no ED telemetry) gating is skipped — no worse than before, since the
  combat guard already refuses in that case. Toggle with `[keybinds].mode_guard`.
- **Action registry (the Phase-1 lever).** `Macro` and the action definitions moved out of the
  capability into `keybinds/registry.py` + `keybinds/actions/*.py`. Each batch registers its
  macros from its own module (imported for the side effect); the capability reads the aggregated
  registry. A future action batch (nav/combat/…) is a **new `actions/` module + one import
  line**, not an edit to `KeybindCapability` — keeping the Phase-1 fan-out parallel.
- **Per-action confirmation policy.** Each `Macro` carries `confirm_required` (default `True`).
  Effective confirmation = `[keybinds].require_confirmation` **AND** the macro's policy, so a
  consequential action arms-and-confirms while a benign/read-only one may fire immediately —
  still behind the allowlist + combat + mode guards. This is the declarative scaffold every
  future batch reuses instead of a global on/off.
- **Binding-preference decision.** `binds.py` still **prefers Primary**, now configurable via
  `[keybinds].binding_preference` (`primary`/`secondary`, either way falling back to the other
  slot). Rationale: unlike COVAS:NEXT's "prefer Secondary" convention, in a normal ED setup the
  **keyboard key lives on Primary** (ED seeds the keyboard/mouse preset there) and a
  joystick/HOTAS custom bind goes on Secondary — so Primary is where the *pressable* key is.
  The `secondary` option is the escape hatch for a Commander who deliberately parks COVAS's
  keyboard binds on the Secondary slot.

### Implemented — Tier-1 ship-systems batch (`keybinds/actions/ship_systems.py`, #31)
The first action batch to *use* the registry lever: a new module registering eight benign,
repeatable main-ship toggles — cargo scoop (`ToggleCargoScoop`), night vision
(`NightVisionToggle`), ship lights (`ShipSpotLightToggle`), HUD combat/analysis
(`PlayerHUDModeToggle`), and the four power-pip taps (`IncreaseEngines/Weapons/SystemsPower`,
`ResetPowerDistribution`). Each is `modes={mainship}` and `confirm_required=False` (harmless and
repeatable → fires immediately, still behind allowlist + combat + mode guards). `KeybindCapability`
is untouched — the batch is a module + one import in `actions/__init__.py`, exactly the fan-out
the registry was built for. Docking request has **no** direct keybind (it's a panel action → a
later panel batch / #32), so it's deliberately excluded. Crucially, the **default allowlist is
unchanged** (`["landing_gear"]`): these macros ship registered but off, opt-in per name — so
default safety/behaviour is identical to the prototype.
### Implemented — Tier-1 flight/nav action batch (#30)
The first *new* batch on the #29 seam, proving the "action batch = module + data, not a
capability edit" claim: `keybinds/actions/flight.py` registers throttle (`SetSpeedZero/50/100`),
FSD/supercruise/hyperspace (`HyperSuperCombination`/`Supercruise`/`Hyperspace`), flight assist
(`ToggleFlightAssist`), targeting (`SelectTarget`, `CycleNext/PreviousTarget`), next-route-system
target (`TargetNextRouteSystem`), and nav-lock (`WingNavLock`). Throttle + targeting also carry
`MODE_FIGHTER` (a fighter flies and targets); FSD/route/nav-lock are main-ship only. Consequential
actions (jump/supercruise/flight-assist) keep `confirm_required=True`; benign, repeatable ones
(throttle set, target cycling, nav-lock) set it `False` to fire immediately — all still behind the
allowlist + combat + mode guards. **None are in the default allowlist** — each is opt-in via
`[keybinds].allowlist`, so default behavior is unchanged. `KeybindCapability` was not touched.
### Implemented — Odyssey on-foot action batch (Tier-1, #34)
The first **non-ship** action batch on the registry seam — and the payoff of mode-gating. A new
module `keybinds/actions/on_foot.py` registers a benign, utility-focused set of Odyssey on-foot
macros (flashlight + night-vision toggles, weapon SELECT + holster — draw/holster only, never
*fires* — the three suit-tool switches, crouch, and the galaxy map), each declaring
`modes={on_foot}`. That single field is the whole feature: `KeybindCapability` advertises them
**only** while the Commander is disembarked (Odyssey `Flags2` OnFoot), so the model is never
offered "toggle flashlight" in the cockpit and the ship's landing-gear toggle never appears on
foot — correct mode-aware actions, unlike a flat action list. Combat actions (fire, grenade,
melee) are deliberately excluded. All are benign (`confirm_required=False`) so they fire
immediately, still behind the allowlist + combat + mode guards; **none is in the default
allowlist** (`[keybinds].allowlist` stays `landing_gear` only) — a Commander opts each macro in
by name. Landing this batch was a new module + one import line in `actions/__init__.py`, no edit
to `KeybindCapability` — the Phase-1 lever working as designed. Action tokens were verified
against a real Odyssey `.binds` file (the night-vision token is `HumanoidToggleNightVisionButton`).
### Implemented — SRV / buggy action batch (Tier-1, #35)
The first *multi-macro* batch to land on the #29 registry seam, and the proof the lever works:
a **new `keybinds/actions/srv.py` module plus one import line** — no edit to `KeybindCapability`.
It registers the useful, non-combat SRV controls, every one `modes=frozenset({MODE_SRV})` so
they're advertised and runnable **only while driving the buggy** (never flying or on foot; the
gate is re-checked at confirm). The benign in-cockpit toggles (`drive_assist`, `srv_headlights`,
`srv_night_vision`, `srv_cargo_scoop`, `srv_auto_brake`) set `confirm_required=False` and fire on
a single command; `recall_ship` (summon/dismiss the mothership) is disruptive so it keeps
`confirm_required=True` and arms-and-confirms like landing gear. Action tokens are the ED `.binds`
buggy element names (including ED's own misspelling `AutoBreakBuggyButton`); **SRV weapons and the
turret are deliberately excluded**. None are in the default allowlist — the Commander opts each in
by name. *Beats-competitors:* EDCoPilot/COVAS:NEXT narrate the SRV but don't press its controls;
COVAS++ drives the buggy's convenience controls hands-free behind the full safety layer.
### Implemented — Tier-1 action batch: panels / maps / fire groups (#32)
The first *real* batch on the #29 registry seam (`covas/keybinds/actions/panels.py` + one import
line in `actions/__init__.py`; **no** `KeybindCapability` edit — the lever working as designed).
Twelve **benign, repeatable** cockpit actions: focus the four HUD panels (`FocusLeftPanel`,
`FocusRightPanel`, `FocusCommsPanel`, `FocusRadarPanel`) + `QuickCommsPanel`, open the galaxy /
system map (`GalaxyMapOpen` / `SystemMapOpen`, main-ship variants), cycle fire groups
(`CycleFireGroup{Next,Previous}`), UI back / focus (`UI_Back`, `UIFocus`), and `HeadLookToggle`.

- **Confirmation policy.** All set `confirm_required=False` — opening a panel/map or cycling a
  fire group changes no ship state and is instantly reversible, so it fires immediately. Still
  fully behind the allowlist + combat/interdiction guard + mode-gating (the #29 scaffold reused,
  not bypassed).
- **Modes.** Panels, maps, UI nav, and head-look are `MODE_MAINSHIP`; fire-group cycling is
  main-ship **or** fighter (a deployed fighter has its own fire groups; same control token).
  On-foot / SRV panel + map variants use *different* ED controls and are deferred to #34/#35.
- **Allowlist unchanged.** The default allowlist stays `["landing_gear"]` — these are opt-in per
  macro name (documented in the `[keybinds]` config comment + `docs/automation/keybinds.md`).
- **Route handoff building block (#41).** `open_galaxy_map` is the first half of an in-game "set
  course": `search/routes.py::RoutePlotter` already accepts an injected `set_course(system)->bool`
  (clipboard fallback today). A future `set_course` opens the galaxy map via this macro, types the
  destination into the map search, and selects it — closing the plot loop. That cross-cutting wire
  is intentionally NOT done here; only the building block is in place.

### Implemented — Tier-1 macro framework: status-checked timed sequences (#33)
Every batch above presses a **single** key. This adds the design's "macros over single keys":
a macro can be a small **scripted sequence** that mixes presses/holds with waits and — the point
— **Status.json checks between steps**, so it *verifies* game state instead of firing blind. It
sits **on** the existing executor and safety layer; it does not rewrite either.

- **Sequence model (`keybinds/sequence.py`).** A `Macro` is now EITHER a single key (`action` +
  `kind`) OR a `steps` tuple of `Step`s (empty = single-key, so every prior batch is unchanged).
  Six declarative step kinds cover the design's sketch: `press` / `hold(seconds)` / `release`
  (the executor primitives), `wait(seconds)`, `require_status(key, expect)` (a **precondition** —
  fail the sequence now if Status.json's flag isn't as expected) and `await_status(key, expect,
  timeout)` (**block until** the flag flips, or fail on timeout). The LLM still only *selects* the
  named macro — it never assembles the step list (the tool schema exposes the name, not the keys).
- **Deterministic runner.** `run_sequence(...)` walks the steps on the injected executor, reading
  an injected status snapshot between them. Every side effect is injected — executor, status
  source, `sleep`, `clock`, and an `abort` predicate — so it's unit-tested with a recording fake
  executor + fake status feed and **no real key presses or waiting** (the fake sleep advances the
  fake clock, so awaits/timeouts are instant and deterministic). It **never raises**: a bad step
  returns a `SequenceOutcome` the capability speaks.
- **Safety inherited, not bypassed.** A sequence macro goes through the *same* gates as any macro
  — allowlist, combat/interdiction guard, mode gate, and (for consequential ones) arm-and-confirm.
  The **hard abort** now also sets a flag the runner polls **between steps and during waits**, so
  "abort" stops a running sequence *and* `release_all()` lifts any key a mid-sequence `hold` left
  down. On any failure the runner also calls `release_all()` — a failed step never strands a key.
- **Worked macro (`keybinds/actions/macros.py`).** `launch` — the design's own launch example —
  lifts off the pad and departs: `require_status(landing_gear=down)` → throttle 50% → **hold**
  vertical thrust to clear the pad → boost → retract gear → **`await_status(landing_gear=up)`** to
  confirm from Status.json that the gear actually retracted. It's `confirm_required=True` and, like
  every batch macro, **NOT in the default allowlist** (`["landing_gear"]` unchanged) — opt-in by
  name. This is the pattern future Tier-2 tasks (dock/undock flows, jump-and-continue) reuse.

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

### The two-tier control model — Tier-1 (combat-refusing) vs Tier-2 (combat-permissive)
Everything above is **Tier-1**: control the ship when it's *safe* to. Its guard (`combat_state`
+ `_GUARD_MESSAGES`) permits an action only when ED Status proves you're clear of
danger/interdiction, and refuses when Status is unavailable (can't prove safe). That's correct
for landing gear, panels, jumps, the launch sequence — things you do when nothing is shooting.

But some ship actions only make sense *while* something is shooting. Chaff, heat sink, shield
cell, boost are **defensive/evasive reflexes** — firing them the moment you're safe would be
pointless, and Tier-1's guard would (rightly, for its purpose) refuse them mid-fight. So Tier-2
is a **separate, deliberately INVERTED policy**, not the Tier-1 guard relaxed:

| | Tier-1 (`KeybindCapability`) | Tier-2 (`ReflexCapability`) |
|---|---|---|
| Purpose | act when it's safe | act *because* it's dangerous |
| Guard verdict | permit only when Status = **SAFE** | permit only when Status = **COMBAT / INTERDICTION** |
| No telemetry | refuse (can't prove safe) | refuse (can't prove *in danger*) |
| Confirmation | consequential actions arm-and-confirm | fire immediately (speed is the point) |
| Allowlist | `[keybinds].allowlist`, default `["landing_gear"]` | `[reflex].allowlist`, default **empty** |

### Implemented — Tier-2 combat-permissive guard + validated chaff reflex (`[reflex]`, default off, #36)
A PROTOTYPE proving the inverted policy end-to-end on the *same* scancode executor as Tier-1
(`covas/capabilities/reflex_capability.py`):

- **Combat-permissive guard (a distinct policy object).** A pure `combat_permissive_verdict(
  action, snap)` returns `None` (permit) or a Commander-facing refusal. It **reuses** Tier-1's
  `combat_state` classification so both tiers read danger identically — only the verdict flips:
  Tier-2 permits a member of the small `COMBAT_PERMISSIVE` set (`chaff`, `heat_sink`, `shields`,
  `boost`) ONLY in COMBAT/INTERDICTION, refuses it when SAFE or when Status is UNKNOWN (never
  fire blind), and **hard-refuses** the `ALWAYS_REFUSED` set (`eject_cargo`, `self_destruct`,
  `landing_gear`) in combat or out — so relaxing the guard *for* combat can't become a backdoor
  to eject cargo or self-destruct. The two sets are asserted disjoint.
- **One validated reflex end-to-end — fire chaff.** `ReflexCapability` advertises a `fire_chaff`
  tool (dispatch is the simplest direct-call path — the LLM SELECTS the named reflex, never keys)
  and presses `FireChaffLauncher` through the shared executor, behind the combat-permissive guard.
  A reflex FIRES immediately: no arm-and-confirm, because a defensive reflex you have to confirm
  is useless — the guard, not a prompt, is the safety. An unbound chaff key degrades to a spoken
  "bind it in-game" (fail-soft).
- **Hard abort preserved.** `abort_reflex` calls the shared `KeyExecutor.release_all()`, lifting
  every held key (the executor is shared with keybinds/honk, so one abort covers all three), and
  the executor still clamps hold duration. Off by default and the allowlist ships **empty** — the
  Tier-1 default allowlist is untouched and this whole path is opt-in per reflex name.
- Everything (binds, executor, status snapshot) is injected, so the guard's three cases
  (permitted-in-combat / always-refused / permitted-but-not-in-combat), the chaff dispatch, and
  the hard abort are all unit-tested offline with a recording fake executor + a fake Status feed.
- **Built ON this guard (separate issues):** a local phrase-spotter + second-PTT fast path (#38,
  below) and an automatic threshold layer (#37, below) — both fire the same reflexes through the
  same guard/executor, adding only new *triggers*.

### Implemented — Tier-2 phrase-spotter + second-PTT reflex fast path (`covas/reflex_spotter.py`, #38)
The reflex path in #36 dispatches through the LLM (`fire_chaff` is a tool). That's fine for a
conversational "fire chaff", but a deliberate combat call wants latency ≈ STT time only, and it
must not queue behind the main conversation turn. #38 adds a **local fast path** that keeps the
#36 guard/executor unchanged:

- **Pure `PhraseSpotter`** (`covas/reflex_spotter.py`) — a function of *(fixed vocabulary,
  transcript)*, mirroring the pure-rules detectors (`wake.py`, `ed/detector.py`). It maps a small,
  FIXED combat vocabulary spotted in the LOCAL Whisper transcript straight to a Tier-2 reflex
  NAME: the `COMBAT_PERMISSIVE` set (`chaff`/`heat_sink`/`shields`/`boost`), each with spoken
  synonyms and multi-word phrases ("break their lock", "heat sink"), plus an `ABORT` sentinel
  ("abort"/"stop"/…). Matching is whole-word (so "chaff" fires but "chaffinch" can't) and
  leftmost-wins; **no keyword → `None`**, so the caller falls through to a normal LLM turn. It
  NEVER presses a key — it only returns a name.
- **Dispatch reuses the ONE guard.** The spotter feeds `ReflexCapability.fire_reflex(name)`, a thin
  by-name entry that routes through the *same* `_fire` path as the `fire_*` tool — same allowlist,
  same `combat_permissive_verdict`, same shared executor, same hard abort (the `ABORT` sentinel
  calls `release_all()`). There is deliberately **no second guard** and no LLM on this path.
- **Second push-to-talk (`[reflex].ptt`, default unbound).** `app.py`'s key hook installs a
  SECOND bind alongside `[keys].push_to_talk`; its scancodes are subtracted from the main PTT/cancel
  codes so a mis-config can't double-dispatch. A capture on it runs `_process_reflex`: local
  transcribe → `PhraseSpotter.match` → on a hit fire IMMEDIATELY via `fire_reflex` (no LLM, no
  conversation-turn dispatch, spoken result is after-the-fact feedback); on a miss hand the SAME
  capture to the usual `_dispatch_utterance`/`_process`. Default unbound = no second hook installs,
  so the main PTT and the whole conversation path are untouched. Fail soft throughout.
- Unit-tested offline: the spotter exhaustively (exact/synonym/multi-word match, whole-word
  no-false-fire, leftmost-wins, no-match→None, vocabulary integrity); `fire_reflex` reusing the
  allowlist+guard+abort; and the app path (a spotted keyword fires the reflex and never calls the
  LLM, a non-combat capture falls through to a normal turn, abort routes to release_all).

### Implemented — Tier-2 ambient auto-reflex framework (`[reflex.auto]`, default off, #37)
The AUTOMATIC (no-voice) trigger layer for the reflexes above — the only sub-100ms path, because a
threshold crossing fires the reflex directly instead of waiting on an LLM round-trip *or* a key
press. Like #38 it adds **nothing** to the safety model: it fires the SAME `REFLEX_ACTIONS` through
the SAME `fire_reflex_action` helper (bind check → combat-permissive guard → executor), so an
automatic reflex is exactly as safe as a spoken or hotword one. What's new is only the *trigger*.

- **Same shape as auto-honk.** `AutoReflexCapability` (`covas/capabilities/auto_reflex_capability.py`)
  is a self-contained capability with an `on_event` bus hook (no worker-loop edits — capabilities
  over loop edits). On a waking `ed_event` it re-reads the live Status snapshot, checks each enabled
  reflex's threshold, and — if met — fires through the shared guard/executor. `#36`'s
  `ReflexCapability._fire` (which #38's `fire_reflex` also routes through) was refactored onto the
  shared `fire_reflex_action` helper so all three reflex paths take one route.
- **Two reflexes shipped, both default OFF.** `heat_sink` — deploy a heat sink on ED's `Overheating`
  flag (>100% heat); the per-reflex `threshold` is the heat percent to react at (>100 disables it,
  since ED never signals hotter). `chaff` — fire chaff on the `EnteredDanger` / `Interdicted` /
  `UnderAttack` triggers, i.e. when a hostile has a lock. `heat_sink` is a threshold reflex, `chaff`
  a pure boolean trigger. (`overheating` was added to the decoded `EDContext` snapshot for the
  trigger; both reflexes were also wired into `REFLEX_ACTIONS`, so the verbal + hotword paths gained
  heat sink too.)
- **Cooldowns reuse the proactive governor's shape.** `AutoReflexPolicy` (mirroring `ProactivePolicy`)
  enforces a per-reflex `cooldown` plus a global `min_interval`, on an injectable clock, so a
  sustained overheat or a long fight can't machine-gun presses. A guard-refused attempt does **not**
  arm the cooldown (a real danger re-trigger can still fire).
- **Guard re-enforced at fire time.** Even a threshold that fired is refused if Status can't confirm
  danger (SAFE/UNKNOWN), and the `ALWAYS_REFUSED` set is unreachable (only `COMBAT_PERMISSIVE` names
  are wired). `[reflex].combat_guard` is shared — its escape-hatch `false` lets `heat_sink` fire on
  overheat regardless of danger (e.g. fuel scooping), but never a dangerous action. The shared hard
  abort (`release_all()`) still lifts any held key.
- **No LLM tools** (the "no voice" requirement) — the layer is silent and ambient; the verbal
  `ReflexCapability` still owns the spoken tools + help metadata. Everything (binds, executor, status
  snapshot, clock) is injected, so the threshold/below-threshold/cooldown/guard-blocks/abort cases
  are all unit-tested offline with a recording fake executor, a fake Status feed, and a fake clock.

### Implemented — send in-game comms text by voice (`[comms_send]`, default off, #49)
The first Tier-1 action that needs **character input**, not a single scancode — compose ED chat
by voice and send it (`covas/capabilities/comms_capability.py` + `covas/comms/`). The LLM composes
the words + picks the channel (local / wing / squadron / direct); a deterministic sequence does
the input. It reuses the keybind executor + `.binds` but introduces a **new text-injection
mechanism** and a **different safety model** to the rest of §6:

- **Text injection = clipboard-paste (the new mechanism).** Unlike every other action (one named
  scancode), a message is arbitrary text. Per-character `SendInput` is fragile (keyboard-layout /
  dead-key / IME dependent), so we take the more reliable path: put the composed string on the
  Windows clipboard (reusing `nav/clipboard.py`'s `clip.exe` writer — no new dependency) and
  **paste** it into ED's focused chat box with **Ctrl+V**, then press **Enter**. `Ctrl+V` and
  `Enter` are CONSTRUCTED `KeyBinding`s (`comms/injector.py`), not ED `.binds` tokens — they're
  OS/chat-field keystrokes the game doesn't rebind — pressed through the same shared scancode
  executor. The injector is an injected seam (clipboard writer + executor both faked in tests), so
  the whole path is unit-tested offline with a recording fake executor + fake clipboard.
- **Safety = mandatory read-back-before-send, NOT a combat guard.** This is **outward-facing** —
  other Commanders see the message — and the real risk is a garbled STT reaching strangers, not
  ship danger. So the gate is an **unconditional** read-back: `send_comms_message` composes + arms
  and returns the words for the model to read back, injecting nothing; `confirm_comms_send` fires
  only on a **separate** Commander turn (turn-gated via the same `new_turn()` counter as keybinds,
  with a `confirm_window` expiry) and `cancel_comms_send` discards. Unlike benign keybinds there is
  **no** `confirm_required=False` path — confirmation can't be switched off. The message is
  normalised to a single line first (whitespace/newlines collapsed, length capped) so it can't
  commit early or run away.
- **Channel routing + fail-soft.** The scripted sequence is *open comms box → (optional) select
  channel → paste → Enter*. `open_bind` (default `QuickCommsPanel`) and the per-channel select
  tokens resolve through the `.binds` like any action; an unbound/joystick-only key degrades to a
  spoken "bind it in-game". Elite has no universal per-channel *send* key, so `channel_*` default
  **blank** = "send on the currently-selected channel" (fine for local chat), and are Commander-
  configurable for anyone who's bound channel-switch keys.
- Off by default; no ED-monitoring dependency (the read-back is the safety). Shares the keybind
  executor so a hard "abort" releases any key it pressed. *Beats-competitors:* EDCoPilot/COVAS:NEXT
  narrate *incoming* comms; COVAS++ composes + sends *outgoing* chat hands-free, gated so nothing
  garbled reaches another Commander.

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

21. **Audio / Comms / Chatter subsystem** (C1–C8, `covas/mixer/`) — an atmospheric audio layer where the LLM only ever produces text that is validated then routed; it is never in the realtime audio path. Built bottom-up: a multi-bus mixer with pure per-bus DSP (C1: COVAS/Comms/Ambient/Music/Alert, biquad bandpass + static + compressor comms radio treatment, applied at runtime not pre-edited); a structurally-enforced cue registry (C2: a cue can't target a nonexistent bus, and an empty eligibility set is valid-but-silent); the game-state eligibility engine + rate governor + driver (C3: Status/journal → state tokens, per-cue cooldowns + a rolling global cap, deterministic rotation, off by default); the **fail-closed `ReceiveText` channel gate** (C4: the core safety contract — player DMs verbatim, NPC lines variant-eligible, the Open-play firehose and any CMDR-ambiguous line dropped; template-identity dedup keyed to the governor); the comms variant pipeline (C5: verbatim/paraphrase/riff with a validator that rejects invented proper nouns/numbers/threats and always falls back to the verbatim source; players never paraphrased); space chatter (C6: template pools with fact gating — the LLM only voices `fact_bearing=false` flavor that asserts nothing checkable); a context-crossfaded music library (C7: curated local tracks, equal-power crossfades, generation a deliberate non-runtime seam); and worked-example cues (C8: the layered pirate-interdiction cue across the alert/COVAS/comms buses, plus eligibility-gated ambient SFX). Everything opt-in and off by default; the DSP, mix, classifier, validators, and crossfades are all pure and unit-tested (no device in the default run).

22. **Audio layer integration** (C9, `covas/mixer/runtime.py` + `app.py`) — the closing step that wires C1–C8 into the running loop, behind a master `[audio].enabled` (default OFF → the shipped direct-playback path is byte-for-byte unchanged). When ON, ONE `BusMixer` owns the device: COVAS's reply STREAMS through it on the clean bus (new `SpeechStream` feed/finish/cancel/wait, barge-in preserved; a device-open failure falls back to the legacy path), cues route to the alert bus, and `AudioLayer` composes the cue registry/governor/driver, comms gate→voicer, music director, and interdiction cue, routing bus `ed_event`s to each (LLM off by default for cost). Adds voice controls (`control_ambient_audio` with help metadata) and an "Ambient audio" settings group (master/per-category enables, per-bus volumes, comms voice pickers) that apply live; fixed the dead `interdiction.enabled` flag and audited every audio flag to a live consumer.

23. **Voice cast** (C10, `covas/mixer/voices.py`) — everything the audio layer speaks gets a voice from a configurable pool, assigned DETERMINISTICALLY by a stable SHA-1 of an identity key (a ReceiveText sender / station / NPC name), so different speakers sound different and the same speaker stays consistent across a session. `for_record()` routes player DMs to the fixed player voice and NPCs to an identity-assigned voice (gendered honorific → matching voice); an empty pool degrades to the persona. Provider routing keeps COVAS on ElevenLabs (the persona) while the NPC/comms/chatter cast runs on local Piper by default (free, no EL burn), EL an opt-in override; `CastSynth` routes a voice to its provider (cached Piper models) and fails soft to silence. The exclusion hook drops any ElevenLabs 'famous'/™ voice (or a flagged one) from the pool as well as the picker. Config `[audio.voices]` + settings pickers.

24. **Drop-in content pipeline** (C11, `covas/mixer/content.py`) — audio + line content is drop-in: a startup scan of convention folders (`audio/sfx/<cue>/`, `audio/music/<context>/`, `content/chatter/<category>.txt`, `content/interdiction_threat.txt`) overlays discovered samples/tracks/lines onto the registered cues, music contexts, and pools, so adding content is dropping a file — no code/config edits. Files override the shipped defaults when present; a missing/empty folder or file leaves that cue simply silent (no error). `ensure_skeleton()` creates the folders + a README in each (idempotent); a content-status report shows per-cue counts and what's still silent; `audio/`+`content/` are git-ignored (assets are user-supplied).

25. **Installable Windows app** (I1–I9, §3.6) — the packaged double-click app. The one structural change is the **writable user-data dir** (I1): `config.py` splits `app_dir()` (read-only shipped assets) from `data_dir()` (`%APPDATA%\COVAS++`), frozen builds relocate writable state, a source run is byte-for-byte unchanged. On top of it: a baked-in **version string** + **Tier-2 update check** (I2, `covas/__version__.py` + `covas/updates.py` + a UI banner that downloads the installer and relaunches, never touching user data); a **first-run wizard** that builds config from nothing — keys, mic, STT-model download, default ElevenLabs "George" voice (I3/I9); a **PyWebView native window** whose close quits the app (I4); a **PyInstaller one-folder CPU-only freeze** (I5, `covas.spec` + `requirements-build.txt`); an **Inno Setup per-user unsigned installer** (I6, `covas.iss`); and a small **`VersionCapability`** answering "what version are you?" by voice while "check for updates" stays a UI action. Docs, the packaged-build manual-test checklist (`MANUAL_TESTS.md` §19), and voice help are in sync (I7). Finally, **shippable default UI cues** (I8): the four cue TYPES (`listen`/`processing`/`completed`/`failure`) are drop-in **folders** — `covas/audio.py` resolves each at load from `<data_dir>/sounds/<type>/` (user override, ≥1 file REPLACES the default) else the shipped originals in `covas/assets/cues/<type>/`, else silence; a random file plays, any count rotates. This replaces the old `[sound_cues]` config lists. Defaults are synthesized originals (`tools/cuegen/gen_defaults.py`, same voice as the LOCKED `listen_ea.wav`), including an interdiction sting fallback; the panel has an **Open cues folder** button (`/api/cues/open`), and `sounds/` stays git-ignored.

26. **Living-galaxy chatter + random persistent voice cast** (`covas/mixer/chatter.py`, `voices.py`, `voice_memory.py`, `runtime.py`) — a tuning pass that makes the ambient layer feel populated. **Space chatter is now populated-only** (every cue gated to the sticky `populated` state — nothing chatters out in empty/unpopulated space) and **population-scaled**: a new `chatter_interval(min_s, max_s, population, full_population)` slides the seconds-between-lines logarithmically between a fast `[audio.chatter].min_seconds` (dense hubs) and a slow `max_seconds` (sparse outposts); `ChatterPlayer` gates on it above the C3 governor (a suppressed turn never touches the LLM or burns the budget). **The cast defaults to ElevenLabs with RANDOM voices** (`cast_provider = "elevenlabs"`, `random_el = true` seeds the pool from the live EL library minus the persona) — Piper stays the documented free fallback. Determinism gives way to **random-but-sticky** assignment via `StickyVoicePool`: comms speakers keep a voice for the current system and are re-cast on a jump (`AudioLayer` tracks `StarSystem`/`Population` from arrival events and clears the comms memory on a change), players keep a voice for the session via a 25-entry LRU, and chatter picks a fresh random voice per line. `VoiceCast.assign` stays deterministic (still backs the interdiction cue); all four new knobs are on the Settings page. Reverses the two cost defaults in entry 23 at the user's request.

27. **Soft "thinking" bed — audio spinner** (issue #5, `covas/audio.py` + `app.py`) — fills the silent transcribe/think/search gap after PTT release so a slow turn doesn't read as "ignored." A new **looping** cue type `thinking` joins the drop-in I8 cue model (shipped soft default under `covas/assets/cues/thinking/`, generated seamless-looping by `tools/cuegen/gen_defaults.py`; `sounds/thinking/` overrides it). `CuePlayer` gains a loop-and-stop lifecycle (`start_loop`/`stop_loop`, one bed at a time, fail-soft on an empty type). The lifecycle is wired through the **single `set_state` chokepoint**: entering a WORKING state (`Transcribing`/`Thinking`/`Searching`) on an *armed* user turn starts the bed; entering any other state stops it — so reply-start (→Speaking), cancel/error (→Idle), and barge-in (→Listening) can never orphan it. Armed only for PTT turns (never proactive), a clean handoff stops the bed before the `completed` chime, and it's toggleable live (`[audio].thinking_bed`, a Settings row + voice: *"turn the thinking sound off"*). Non-verbal and local — never a spoken "let me look that up." Unit-tested lifecycle (loop repeat/stop with an injected clock; start-on-thinking / stop-on-speak-cancel-fail with fakes).

28. **Pluggable TTS/cast provider registry** (issue #14, `covas/providers/registry.py`, `mixer/voices.py`) — the foundation for adding voice providers (Edge/OpenAI/Azure/Cartesia, #15–#18). A `TTSProviderRegistry` maps a provider name → a PCM **backend** `(text, ref) -> (pcm, sr)` (the same shape as `TTSProvider.synth_pcm`); `CastSynth` now dispatches every cast voice through the registry instead of a hardcoded `if elevenlabs / elif piper`, so a new provider is castable the moment it registers a backend — no change to `CastSynth`. Behaviour-preserving: ElevenLabs + Piper register today (the legacy `el_synth`/`piper_loader` constructor is wrapped into a registry, Piper models still cached per `.onnx`), a voice on an unregistered/failing provider still fails soft to silence. `resolve_provider(cfg, role)` adds **per-role provider selection** — `[audio.voices.providers].{comms,chatter,player,interdiction}` override the umbrella `cast_provider`; the **persona/status** path is deliberately NOT a cast role (it keeps the full `TTSProvider` via `[tts].provider` because it needs streaming + cancellation, not just PCM). Establishes the voice ladder (free/local Piper → cheap cloud → premium) mirroring the LLM cost router. Pure/offline-tested (`tests/test_tts_registry.py`).

29. **Edge (edge-tts) free neural voice provider** (issue #15, `covas/providers/edge_tts.py`) — the first drop-in on the #14 registry: an `EdgeTTS` `TTSProvider` over `edge-tts` (Microsoft Edge's "Read Aloud" Azure Neural voices) giving hundreds of distinct voices with **no API key and no per-character cost** — ideal for the NPC/comms/chatter cast so ambient chatter never burns ElevenLabs credits. `edge-tts` returns MP3; `soundfile`/libsndfile (already a dep) decodes it to the same raw 16-bit mono PCM (24 kHz) the audio/cancel path already expects, so nothing downstream changes. Cancellation is honored while synthesizing (stop pulling stream chunks) and while playing (abort/drop buffered audio), so tap-cancel/barge-in stay snappy; the mixer path mirrors Piper's. Selectable as `[tts].provider = "edge"` (persona/status, via `make_tts`) and registered as a **cast-eligible** backend on the `CastSynth` registry (any `[audio.voices]` role). **Optional / not load-bearing:** the endpoint is undocumented, ToS-gray, and SLA-less — so the persona path **fails soft to Piper** (`fallback=`, when `[piper].model` is set, else degrades to text) and cast Edge voices fall silent on failure; **Piper remains the guaranteed free floor**, and official **Azure Neural TTS** (#17, same voices + API/SLA) is the dependable version. `[edge].voice` picks the persona ShortName; the voice catalog (`list_edge_voices`, gender-mapped) is exposed for cast assignment. Offline-unit-tested with a locally-built MP3 fixture + monkeypatched network (`tests/test_edge_tts.py`); one opt-in `integration`/`local` test hits the real endpoint.

30. **Azure Neural TTS provider** (issue #17, `covas/providers/azure_tts.py`) — the **reliable sibling** of the Edge provider (#29): the *same* Azure Neural voices, but over the official Speech service — a real API, an SLA, and a **free monthly tier (~0.5M chars)** — so it's the shippable low/zero-cost way to give the cast big voice variety with **no ToS/reliability asterisk**. Implemented over the Speech REST endpoint via `requests` (already a dep — **no Azure SDK / no new dependency**); requests `raw-24khz-16bit-mono-pcm`, so the response IS the raw 16-bit mono PCM the pipeline expects (no decode step, unlike Edge's MP3). `_build_ssml` XML-escapes the text and optionally wraps it in an `mstts:express-as` speaking **style** (`[azure].style`); cancellation is honored while synthesizing (stop pulling stream chunks) and playing, mirroring ElevenLabs/Piper; the mixer path mirrors Piper's. Selectable as `[tts].provider = "azure"` (persona, via `make_tts`) and registered as a **cast-eligible** backend on the `CastSynth` registry (`_register_azure_cast`). Key resolution mirrors the other providers: the git-ignored `[azure].api_key_file` (`firstrun.azure_key`, resolved under data_dir), **DPAPI-encrypted at rest** — env-var key reads were removed in #22 (§3.7); `[azure].region` must match the resource. **Fail soft:** no key / service error makes the persona degrade to text and cast voices fall silent — never crashes the loop. Voice catalog (`list_azure_voices`, gender-mapped) exposed for cast assignment. Offline-unit-tested with monkeypatched network (`tests/test_azure_tts.py`: SSML build/escape/style, catalog normalize, synth/speak/cancel, cast registry, fail-soft); one opt-in `integration`/`paid` live test (skips without a key).

31. **OpenAI-compatible TTS provider** (issue #16, `covas/providers/openai_tts.py`) — a **cheap cloud** voice over the OpenAI `audio/speech` API (REST via `requests`, **no new dependency / no OpenAI SDK**). Requests `response_format = "pcm"`, which OpenAI returns as raw 24 kHz 16-bit mono PCM — the pipeline's native shape, so no decode step (like Azure #30). `base_url` is configurable so any OpenAI-compatible endpoint (a proxy, a local server) works with the same code; the key comes from the git-ignored `[openai_tts].api_key_file`, **DPAPI-encrypted at rest** and **shared with a future OpenAI LLM provider** (#12) — env-var key reads were removed in #22 (§3.7). Config `[openai_tts]` carries `base_url`/`model`/`voice` plus an optional free-text `instructions` tone steer (honored by newer models like gpt-4o-mini-tts, ignored by tts-1). Streaming with prompt cancellation + the mixer path mirror Azure/Piper. Selectable as `[tts].provider = "openai"` (persona) and registered **cast-eligible** (`_register_openai_cast`). The voice set is small/fixed (no voices/list endpoint) → `list_voices` returns a static gender-neutral catalog; best as a **cheap persona or supplemental cast voice**, not a large diverse cast. **Fail soft:** no key / service error → persona degrades to text, cast voices fall silent — never crashes. Offline-unit-tested with monkeypatched network (`tests/test_openai_tts.py`); one opt-in `integration`/`paid` live test (skips without a key).

32. **Cartesia (Sonic) low-latency persona voice** (issue #18, `covas/providers/cartesia_tts.py`) — a **premium, low-latency** alternative to ElevenLabs for the COVAS **persona** (Cartesia's Sonic models are built for very low time-to-first-audio, which is what a live companion feels most). Unlike the cast providers (#15–#17, collect-then-play), this one's value is latency, so `speak` **streams chunks to the output as they arrive** (mirroring the ElevenLabs streaming path in `tts.py`, with the same odd-byte/whole-sample handling and barge-in). Over the Cartesia REST `/tts/bytes` endpoint via `requests` (**no new dependency / no SDK**), requesting a `raw`/`pcm_s16le` output format so the streamed bytes ARE the raw 16-bit mono PCM the pipeline expects (no decode). Registered **persona-eligible ONLY** — wired into `make_tts` (`[tts].provider = "cartesia"`) but deliberately NOT onto the `CastSynth` registry and NOT in `CAST_PROVIDERS`, since it's premium and its value is the live reply, not background chatter. Key via the git-ignored `[cartesia].api_key_file`, **DPAPI-encrypted at rest** — env-var key reads were removed in #22 (§3.7); `[cartesia].voice` is a required Cartesia voice id, `model`/`language` configurable. **Fail soft:** no key/voice or a service error → persona degrades to text, never crashes. (Deepgram **Aura** is the documented alternative; Sonic was picked to start — Aura is a later drop-in on the same seam.) Offline-unit-tested with a monkeypatched chunk stream, incl. streaming cancel + whole-sample reassembly (`tests/test_cartesia_tts.py`); one opt-in `integration`/`paid` live test (skips without a key + voice id).

33. **Provider-agnostic router foundation** (issue #11, `covas/router.py`, `covas/providers/base.py`) — the LLM-track foundation that unblocks the OpenAI (#12) and Gemini (#13) providers. The router now decides one of three **canonical tiers** (`cheap`/`standard`/`premium`, + `fixed` when off) instead of a hardcoded Anthropic model; a per-provider **tier map** (`router._provider_tiers`) turns the tier into a concrete model id from the ACTIVE `[llm].provider`. Anthropic keeps reading `[router].{default,escalate,premium}_model` + `[anthropic].model`, so **existing Anthropic users see zero change** (same models, caching, defaults — verified by the unchanged decision tests). Any other cloud provider advertises its map via `[<provider>].tiers.{cheap,standard,premium}` (or one `[<provider>].model`). `Route` gained a `tier` field (logged + on the `router` bus event); reason strings are now tier-named (not Anthropic-flavored). `providers/base.py` spells out the full **normalization contract** every LLM provider meets — tool-call translation, streaming text vs. `on_event("thinking")`, the provider-agnostic `usage` dict costed via `llm.estimate_cost`, cancellation, fail-soft. **In-game-LLM policy relaxed** (CLAUDE.md, DESIGN §4/§3.5): any *cloud* LLM is fine in-game (the router tiers it); only *local* models (Ollama) stay off the in-game path, because they fight ED for the GPU — not an API limitation. Pure/offline-tested (`tests/test_router.py`: tier reporting, the Anthropic map unchanged, a generic provider's own map, single-model fallback).

34. **OpenAI-compatible LLM provider** (issue #12, `covas/providers/openai_llm.py`) — the first LLM drop-in on the #11 foundation: ONE `LLMProvider` that unlocks **OpenAI, Groq, DeepSeek, and OpenRouter** (they all speak the OpenAI `chat/completions` API — only `[openai].base_url` + model ids differ). Streaming over `requests` (**no new dependency / no OpenAI SDK**), normalized to the shared `base.py` event contract so `app.py` consumes it identically to Anthropic. The hard part is **tool calling**: OpenAI streams `tool_calls` as *deltas* (id/name once, `arguments` assembled across chunks) and links results by `tool_call_id` — `stream_reply` assembles them (`_accumulate_tool_call`/`_finalize_tool_calls`), dispatches via the shared `tool_handler`, appends the assistant `tool_calls` + `role:"tool"` results, and loops (capped at 8 rounds) exactly like the Anthropic client-tool loop. Reasoning-model deltas (`reasoning_content`, DeepSeek-R1/o-series) route to `on_event("thinking")`, kept out of speech; usage is normalized (`_usage_event`) and costed via the shared `llm.estimate_cost` + `[pricing]`. **Tiering** comes from the router foundation — the per-turn model is `[openai].tiers.{cheap,standard,premium}`. No web-search on this path (chat/completions has none — Anthropic-only). Key is the git-ignored `[openai].api_key_file`, **DPAPI-encrypted at rest** and shared with the OpenAI TTS provider (#16) — env-var key reads were removed in #22 (§3.7). **Cloud, so in-game is fine** (per the #11 policy). Wired into `factory.make_llm`; fail soft (a request error degrades the turn to text, never crashes). Offline-unit-tested with monkeypatched SSE chunk streams — tool-call assembly, the tool loop, parallel calls, tool-error recovery, usage, reasoning→thinking, cancellation, body shaping, and SSE line parsing (`tests/test_openai_llm.py`); one opt-in `integration`/`paid` live test.

35. **Gemini LLM provider** (issue #13, `covas/providers/gemini_llm.py`) — the second LLM drop-in on the #11 foundation, on Gemini's **native** `generateContent` API (not the OpenAI-compat shim), for the richer surface: strong **function calling**, Google-Search **grounding**, and a cheap/fast **Flash** default tier. Streaming SSE over `requests` (**no new dependency / no google SDK**), normalized to the shared `base.py` event contract. Handles Gemini's shape: `contents` with `user`/`model` roles + `parts` (system → `systemInstruction`); a whole `functionCall` part (args already a dict, not delta-assembled) run via the shared `tool_handler` and answered with a `functionResponse` part, looping (capped 8) like the other client-tool loops; **grounding** (added as the `googleSearch` tool when `[web_search].enabled`) with the queries from `groundingMetadata.webSearchQueries` surfaced via `on_event("search", …)` — the same side-channel as Anthropic web_search; and 2.5 **thought** parts routed to `on_event("thinking")`. Tiering from #11 is `[gemini].tiers` (Flash cheap/default, Pro depth); usage costed via `llm.estimate_cost`. The key rides the `x-goog-api-key` **header** (never the URL — privacy guardrail); resolved from the git-ignored `[gemini].api_key_file`, **DPAPI-encrypted at rest** — env-var key reads were removed in #22 (§3.7). Cloud, so in-game is fine; wired into `factory.make_llm`; fail soft. Offline-unit-tested with monkeypatched SSE chunk streams — function-call loop, parallel calls, tool-error recovery, grounding side-channel, thought→thinking, usage, cancellation, header-not-URL key, SSE parsing (`tests/test_gemini_llm.py`); one opt-in `integration`/`paid` live test. **This closes the multi-provider epic (#10): both provider tracks are complete.**

36. **API keys encrypted at rest — Windows DPAPI** (epic #21, subs #22–#25, `covas/dpapi.py`, `covas/firstrun.py`, `covas/templates/settings.html`) — every provider key (Anthropic, ElevenLabs, OpenAI, Gemini, Azure, Cartesia, Inara) is now **encrypted at rest** instead of a plaintext file, and **environment-variable key reads are removed** — the full rationale, mechanism, storage format, and threat model live in §3.7. **#22** added the DPAPI core (`CryptProtectData`/`CryptUnprotectData` via `ctypes`/`crypt32`, `CurrentUser` scope, no new dependency), the `DPAPI:<base64(blob)>` sentinel + transparent plaintext→encrypted migration on read, and stripped all env-var key lookups. **#23** added the masked, write-only **API keys** card to the Settings page — set/rotate/clear any provider's key without touching files, keys never rendered back. **#24** folded the **Inara** key (previously inline plaintext in `overrides.json`) into an encrypted `InaraAPIKey.txt`, blanking the legacy inline value on first run — closing the "zero plaintext keys anywhere" gap. **#25** is this docs/config sweep: every config comment, `settings_schema` help string, docs-site page, `MANUAL_TESTS.md` step, and this design doc now tell one consistent story (keys entered in the wizard / Settings card, DPAPI-encrypted, env vars not read), plus a least-privilege (spend-capped keys) defense-in-depth tip. A blob that won't decrypt on this machine/account is treated as "no key" with a re-enter message, never a crash; DPAPI is Windows-only and the cross-platform test suite fakes it (`tests/test_dpapi.py`, `tests/test_firstrun.py`).

37. **Fleet-carrier context voices** (issue #19, `covas/mixer/carrier.py`) — captain / tower / carrier-chatter as a **context** voice: the role is chosen because of *where the Commander is* (at, or in the same system as, the carrier they **own**), not just who's speaking — the gap the per-speaker cast (C10) and per-honorific comms (C4) couldn't fill. Two new eligibility tokens (`at_own_carrier` / `near_own_carrier`) are folded into the C3 `EligibilityEngine` by `AudioLayer` from **EDContext** carrier tracking — pinned to the owned carrier's identity (`Docked`/`Location`/`CarrierJump` now capture `StationType` + `MarketID`; `EDContext.at_own_carrier()` matches `docked_market_id == carrier_id`, so a **squadron/other** carrier never triggers it; `near_own_carrier()` = current system == carrier system). Seven carrier cues (captain welcome/status + in-system greeting, tower traffic/departure, deck + services chatter) ride the radio-treated **comms** bus, gated on those tokens (tower is docked-only), each tagged with a new optional `Cue.voice_role`. A `CarrierPlayer` (mirroring C6's `ChatterPlayer`, but with a **fixed per-role voice** instead of a random one) selects a curated pooled line — **fact_bearing, so the LLM is never in this path** — weaves in the role's configurable display **name**, and speaks it in the role's own voice: `[audio.carrier.<role>]` `voice_ref`/`voice_provider` resolve through the same #14 provider registry (new `captain`/`tower` cast roles), or fall back to a distinct **stable cast-pool voice** so the three sound like different people with zero config. Literal docking messages still flow through the C4/C5 comms gate — this layer is pure atmosphere on top, never a re-implementation of it. Independently toggleable (`[audio.carrier].enabled`, default on but naturally silent unless you own a carrier and are there; `control_ambient_audio` gains a `carrier` target + help). Pure/offline-tested end-to-end — context predicates, journal capture, token folding, cue contract, name templating, player routing, and the AudioLayer wiring (`tests/test_carrier_voices.py`); the synth/provider paths stay integration-marked. **Extends the C-series audio layer (entries 21–35) with a location-aware voice; event-reactive tower one-shots (pad numbers off `DockingGranted`) are a noted future extension.**

38. **Filterable voice lists** (issue #26, `covas/templates/index.html`, `covas/templates/settings.html`) — every ElevenLabs voice dropdown gains a **type-to-filter** box, mirroring the existing settings filter (issue #7): 3+ characters narrows the `<select>` to options whose visible label (voice **name + category**) contains the text as a case-insensitive substring, so "any word" matches; **fewer than 3 chars (or empty) clears the filter** and restores the full list. Native `<select>`s can't be filtered directly, so a small text input toggles each option's `hidden` flag — the **currently-selected** option always stays visible so the active pick can't disappear behind the filter. Applied to the control-panel **ElevenLabs voice** picker (stacked filter below the dropdown) and the schema-driven `@elevenlabs_voices` picker on the Settings page (compact filter beside it, wired generically to any future `*_voices` source so persona / voice-cast pickers inherit it). Pure client-side UI on top of the existing option lists — no provider, route, or schema change; verified by driving the filter fn over a synthetic voice list (name-match, category-match, <3-char passthrough, empty-restore, selected-stays-visible).

39. **Context-aware voice quality — variety + correct perspective** (issue #57, `covas/mixer/voice_memory.py`, `cues.py`, `chatter.py`, `runtime.py`) — two fixes that make the atmospheric/context voices feel intentional instead of a shuffled soundboard. **(1) Variety.** `StickyVoicePool` gains an optional **anti-repeat window** (`anti_repeat=N`): on top of the existing "prefer a voice not currently assigned" rule, `_pick` also avoids the last N voices it *handed out*, relaxing step-by-step (drop the recent constraint, then the in-use one) so it never deadlocks on a small pool. `AudioLayer` sets a window of 5 on all three cast memories (comms/player/chatter), so per-line chatter and freshly-cast speakers **spread across the whole pool** rather than clustering on a few voices. Off by default (`anti_repeat=0`) preserves the prior behaviour, and the injected `rng` keeps it deterministic in tests. **(2) Attribution / perspective.** A new `voice_role=PERSONA` value (in `cues.py`, alongside the #19 carrier roles) tags a cue as an **"our"-perspective** line — something the ship/companion itself notices — so the audio layer routes it to COVAS's **own persona voice on the clean COVAS bus** (via the app's real TTS provider, mirroring the interdiction COVAS line), never an anonymous radioed cast voice. Everything untagged stays an **ambient** line on a random radioed cast voice on the comms bus. The classification lives on the cue (a `voice_role` attribute), not scattered conditionals: `_dispatch_play` branches PERSONA → the persona chatter player, other roles → the carrier player, else the ambient path. The shipped `populated_musing` chatter cue ("nice to have some company out here") was reclassified from an anonymous COMMS cast voice to `voice_role=PERSONA` on the COVAS bus — the representative "our" cue. PERSONA is also the documented **seam** for a crew member's voice once interactive crew lands. Pure/offline unit-tested: the anti-repeat window (no reuse within the window, wider effective variety, small-pool relaxation, off-by-default) and the routing (an "our" cue → the persona voice on the clean bus, an ambient cue → a radioed cast voice on comms). **Improvement thesis vs EDCoPilot/COVAS:NEXT: a coherent, correctly-attributed soundscape — the companion speaks in its own voice for what it notices while the anonymous radio cast handles ambient traffic, and the cast doesn't visibly repeat — rather than a single shuffled voice pool narrating everything.**

40. **Stored ships & modules finder** (issue #67, `covas/ed/stored.py`, `covas/capabilities/stored_capability.py`) — "where's my Cutter / where did I leave that module / how much to transfer it here?" answered by voice from PURE your-state journal data (no CAPI, no network — always accurate to the last dock). The journal's `StoredShips` / `StoredModules` events are full inventories written when you dock somewhere with a shipyard / outfitting; `stored.py` parses each into a frozen snapshot (`StoredShipsSnapshot` / `StoredModulesSnapshot`, symbol → spoken name deferred to speak time like the N9 loadout capture) classifying each entry **here** (parked where the snapshot was taken) vs. **remote** vs. **in transit**. `apply_journal_event` stashes each snapshot on `EDContext` (`set_stored_ships`/`set_stored_modules`, replaced wholesale, kept OUT of `_FIELDS`/summary like the loadout) — this needed lifting the `_HANDLERS`-only early return so a snapshot-carrying event with no "current context" patch still stores. **Transfer time & cost are surfaced VERBATIM from the game**: Frontier writes the exact `TransferPrice`/`TransferCost` and `TransferTime` (seconds) into every remote entry — computed from the distance between where you're docked and where the item sits (time grows with distance; cost with distance + item value) — so the `StoredCapability` speaks those numbers rather than re-deriving them (they match the in-game transfer screen exactly), always tagged "as of your last dock at ⟨station⟩". Two LLM-native tools (`find_stored_ship`/`find_stored_module`) resolve a spoken hull/module/custom-name (substring + difflib for ships, an Item-symbol-fragment alias table + `module_names.module_name` for modules), answer the here/remote/in-transit/unknown cases honestly (an unknown query lists what IS stored, never invents a location), and — for a single remote hit — copy the destination system to the clipboard for the galaxy-map handoff, honouring the N3 "already there → don't copy" rule. Injected snapshot getters + current-system getter + clipboard keep the default `pytest` run offline (fixtures `journal_stored_ships.json` / `journal_stored_modules.json`; `tests/test_stored.py` + `tests/test_stored_capability.py`), and the capability fails soft (any error spoken, never crashes the loop). Docs (`docs/elite/stored-ships-modules.md`), `MANUAL_TESTS.md` §9a, and in-app help metadata are in sync. **Improvement thesis vs EDCoPilot/COVAS:NEXT: an exact, always-correct "where is it / what's the transfer" answer from your own journal — game-quoted cost/time, no external DB to be stale or wrong — with a one-paste galaxy-map handoff.**
40. **Engineers finder** (issue #65, `covas/ed/engineers.py`, `covas/capabilities/engineers_capability.py`) — "which engineer unlocks X / where is engineer Y / what do I still need to unlock them", answered from the Commander's OWN journal, not a generic wiki. Two halves kept apart: a **bundled offline reference table** (`ENGINEERS` — every bubble + Colonia ship engineer's system/base, module specialties, invitation requirement, and unlock gift/task; regenerable, sources + refresh steps in the module docstring; no network at runtime) JOINED with **live `EngineerProgress` grounding** — a new journal handler folds the event (both the startup `Engineers`-array summary and single-engineer updates) into a MERGED `{name: EngineerStatus}` map on `EDContext` (`update_engineer_progress`/`engineer_progress`, kept out of `_FIELDS`/summary like the loadout snapshot), matched onto the table by the exact journal name. The `EngineersCapability` advertises `find_engineer` (by name → location + specialties + your Known/Invited/Unlocked status + what's left, and copies the system to the clipboard for plotting unless you're already there; or by `module` → every engineer for that module tagged with whether YOU'VE unlocked them, bubble-before-Colonia) and `engineer_unlock_status` (a journal-grounded rundown: count unlocked, in-progress, still-locked). Registered with ED monitoring (its only data source); all I/O injected (progress getter, current-system, clipboard) so the default `pytest` run is offline and free; fail soft — any error is spoken, a bad getter degrades to the table's generic requirement text, never raises. Docs (`docs/elite/engineers.md`), `MANUAL_TESTS.md` §9a, and voice help metadata in sync. Pure/offline-tested end-to-end — name/specialty matching, both EngineerProgress shapes, the EDContext merge, journal wiring, and the spoken tool shapes (`tests/test_engineers.py`, `tests/test_engineers_capability.py`). **Improvement thesis vs EDCoPilot/COVAS:NEXT: unlock answers are grounded in the Commander's real journal progress ("you've been invited, you still need 500,000 credits") with a one-command plot handoff — not a static wiki recital of every engineer's requirements.**
41. **Blueprint / material sourcing** (issue #66, `covas/ed/materials.py`, `covas/ed/blueprints.py`, `covas/ed/data/`, `covas/capabilities/blueprint_capability.py`) — "what do I need for a grade-5 FSD, and where do I farm what I'm short on?" Journal-grounded like the loadout capability (entry 18): the journal **`Materials`** event (the full Raw/Manufactured/Encoded inventory) is parsed to a frozen `MaterialsSnapshot` kept on `EDContext` — replaced wholesale on each snapshot, nudged by `MaterialCollected`/`MaterialDiscarded` deltas between them (single-writer, so the read-modify-write is race-free) — and read on demand, never injected into the cached system prompt. The new architectural piece is a **bundled, regenerable data pattern**: two static JSON tables under `covas/ed/data/` (`blueprints.json` recipes, `materials.json` catalogue + sourcing hints) are **derived, never hand-authored** — `regen_engineering_data.py` (a dev tool, never imported at runtime) rebuilds them from EDCD/coriolis-data + EDCD/FDevIDs, so runtime stays fully offline while the data can be refreshed when Frontier changes a recipe (PyInstaller ships them automatically via the spec's `collect_data_files("covas")`). `BlueprintLibrary` (pure) fuzzy-resolves a spoken request to the real blueprint(s) — honestly returning several for a module-only request ("FSD") so the model disambiguates rather than guessing — and crosses a chosen grade's recipe with the live inventory to compute what's **MISSING** (never the full list dumped blind), each short material carrying a trader-group + evergreen-farm hint. The **differentiator** is the checklist hand-off: the tool descriptions invite the model to drop the shortfall onto the checklist as trackable steps via the **existing `add_objective` tool** (the same cross-capability seam loadout uses for upgrade ideas) — no parallel mechanism. Registered with ED monitoring (its only live data source); everything spoken is derived from the tables + the journal's own counts, so a material, count, or source is reported, never invented. Offline-unit-tested with a materials fixture (`tests/test_ed_materials.py`, `test_blueprints.py`, `test_blueprint_capability.py`). **Improvement thesis vs EDCoPilot/COVAS:NEXT: not a static recipe read-out but a journal-grounded gap analysis — what YOU specifically lack — that lands as trackable farm steps on the checklist you already keep.**

42. **Hands-free continuous listening — VAD activation mode** (issue #63, `covas/listen.py`, `app.py`, `[listen]`) — a second **activation mode** beside push-to-talk: with `[listen].mode = "continuous"` a local voice-activity gate opens a capture window on speech onset and closes it after trailing silence, then runs the turn — **no key press**. PTT stays the DEFAULT; continuous is opt-in and switchable **live** (by voice — *"switch to continuous listening"* — or the Settings **Activation mode** row). The design SPLITS pure logic from the mic thread, mirroring the watcher pattern: a **pure `VadGate`** state machine is fed one frame *energy* at a time and decides SILENCE→SPEECH (onset above `energy_threshold`, debounced by `start_ms`) and SPEECH→SILENCE (a `hangover_ms` trailing-silence timeout ends the utterance), rejecting captures shorter than `min_speech_ms` as noise — it has **no audio device, no threads, and no wall clock** (time is counted in `frame_ms` frames), so a synthetic energy sequence exercises every branch offline. A thin **`VadListener`** daemon reads the real mic, slices it into frames, computes each frame's RMS, feeds the gate, and buffers the audio with a little **pre-roll** so the first phoneme isn't clipped; on a confirmed onset it calls the app's barge-in path (`_on_vad_speech_start` → `_interrupt` + Listening, under the proactive lock, exactly like `on_ptt_down`), and on utterance-end it hands the captured audio to the **SAME dispatch** PTT uses (`_dispatch_utterance`, extracted from `on_ptt_up`: arm the thinking bed → `active_cancel` → `_process` worker), so transcription (local Whisper), cancellation, and barge-in are all reused unchanged — **local-only, zero added cloud cost to listen**. A physical PTT hold WINS while held (the VAD callbacks no-op on `ptt_held`) so the two inputs never double-fire. The live switch reconciles via `_after_settings_change` → `_reconcile_listener()` (start/stop the listener to match the mode), **mirroring `_reconcile_hud`**. **Stdlib + numpy energy VAD — no new dependency** (an energy gate is enough for "did a human start talking"; `webrtcvad` was considered and deliberately skipped to keep the default path dependency-free). **Fail soft:** a mic that won't open logs and falls back to PTT; a bad frame or callback error is swallowed so continuous mode can never crash the loop. Unit-tested exhaustively where it's pure — the `VadGate` decisions and the `VadListener` capture/pre-roll logic driven synchronously with synthetic frames, no mic/thread/real-time (`tests/test_listen.py`); the on-hardware mic thread is covered by `MANUAL_TESTS.md` §3a. Docs (`docs/getting-started/hands-free.md`), the config reference, and voice/Settings help (schema `[listen]` rows) are in sync. **Improvement thesis vs EDCoPilot/COVAS:NEXT: a genuinely hands-free mode that reuses the exact same local transcribe→LLM→TTS + barge-in path (no separate always-on cloud pipeline, no extra cost), switchable live by voice, with PTT still first-class.**

43. **Interactive crew — multi-character voicing on the conversation path** (issue #69, `covas/crew.py`, `mixer/runtime.py`, `llm.py`, `[crew]`) — the payoff on the attribution seam entry 39 left open: an ordinary reply can now voice a **named crew member**, each line attributed and spoken in its **own** deterministic, radio-filtered voice, while the ship **persona stays the DEFAULT speaker** for every line it isn't told to hand off. Two **pure** pieces keep the reply path thin and offline-testable: `parse_segments()` splits a reply into ordered `(speaker, text)` `Segment`s from `[Name]` **line** prefixes (`speaker is None` = persona) — total and fail-safe: no prefix anywhere returns the whole reply verbatim as one persona segment (the common case is byte-identical to the direct speak path), malformed / empty-name / over-long brackets (`[unclosed`, `[]`, `[   ]`, a 41-char name) are left as ordinary persona text and never crash, the name is trimmed so `[Nyx]`/`[ Nyx ]` share a voice key, consecutive same-speaker lines merge into one synth call, and empty crew text is dropped; and `speak_segments()` walks them in order routing persona→`persona_speak` and crew→`crew_speak(name, text) -> bool`, honoring barge-in (`cancel`) **between** segments and **degrading a failed crew line to the persona voice** (fail soft). The voice routing REUSES the C10 cast, not a new TTS path: `AudioLayer.speak_crew` calls `VoiceCast.assign(name)` (same name → same pool voice, distinct names → distinct, empty pool → persona) → `CastSynth` → the radio-treated **comms** bus via `mixer.submit`, then blocks for the clip's duration with `cancel.wait()` so a tap-cancel drops the rest of the line — the persona keeps its own direct `tts.speak` path unchanged (`app.py::_speak_persona`). Enablement is `[crew].enabled` (**DEFAULT OFF**): when on, a **STATIC** instruction (`crew.system_instruction`, folded into `llm.build_system`) tells the model it may prefix a line with `[Name]` and that unprefixed lines are the ship — constant for a given config (the only variable, the optional `[crew].roster`, is itself static) so it rides the **cached** system prefix and never busts the prompt cache turn-to-turn; when off the reply is spoken exactly as before (the parser isn't even invoked). Exhaustively unit-tested — the parser's every edge case, the dispatcher's persona-default / fail-soft-degrade / barge-in-midway routing with recording fakes, the static-instruction/cache-safety, and `speak_crew`'s deterministic comms-bus routing over a device-free mixer (`tests/test_crew.py`, 31 tests); on-hardware crew voicing + barge-in is `MANUAL_TESTS.md` §18.5b. Docs (`docs/using/crew.md` + config reference), the Settings/voice help (schema `crew.enabled` row), and this doc are in sync. **Improvement thesis vs EDCoPilot/COVAS:NEXT: crew is a live, LLM-directed conversational cast — the model decides turn-by-turn when a named character adds something and voices just that line in a consistent, distinct voice — not a set of pre-scripted role soundboards, and it correctly keeps the companion as the default narrator.**

44. **Wake-word gating — hands-free arming phrase** (issue #64, `covas/wake.py`, `app.py`, `[listen]`) — an optional guard IN FRONT of the continuous path (entry 42) so hands-free mode isn't triggered by every stray utterance: with `[listen].wake_word` set (e.g. `COVAS`), an ambient capture only becomes a turn if its **transcript** carries the phrase, else the turn is **dropped before the LLM** — and the phrase is stripped before the words reach the model. **OFF by default** (empty phrase); PTT is **never** gated (a deliberate press always runs). Keyword spotting on the **local Whisper transcript** is the simplest reliable wake path — no extra model, no new dependency, and a false trigger can't burn cloud tokens because the drop happens before any API call. The core is a **pure `WakeWordGate`** (mirrors the ED/memory detectors): a function of *(config, text)* → `WakeResult(armed, text, reason)` with no audio, network, LLM, or threads, so match / no-match / strip / fuzzy / empty=disabled are all exercised offline with plain strings. Rules, all case-insensitive and whitespace/punctuation-robust: empty phrase → always armed, unchanged (continuous behaves exactly as #63 shipped); the phrase is matched on **word tokens** (so `cove` can't arm on `discover`) anywhere in the utterance (typically leading); only the phrase itself is excised (both sides preserved — leading, trailing, and embedded call signs all handled, `COVAS, what's my fuel` → `what's my fuel`); a capture that is *only* the wake word cleans to empty and the caller returns to Idle; and **fuzzy** tolerance (on by default, `wake_word_fuzzy`) forgives the one-letter STT slips a short call sign attracts (`Kovas`/`Covis`) via a per-word `difflib` similarity ratio, while unrelated short words stay below threshold. Wiring is a **small, surgical** app.py diff: `_dispatch_utterance` gained a `wake_gated` flag that ONLY the VAD utterance callback sets (`_on_vad_utterance`), threaded to `_process`, which consults `WakeWordGate.from_cfg(self.cfg)` right after local transcription and before anything is printed/logged/sent — PTT's dispatch never sets the flag, so it bypasses the gate entirely. **Fail soft** and reuses the exact transcribe→LLM→TTS path unchanged. Unit-tested where it's pure — the gate's full contract (`tests/test_wake.py`) plus app-level proof that the continuous path consults it while PTT bypasses it and that empty=disabled passes through (`tests/test_app_turn.py`); the on-hardware mic behaviour is `MANUAL_TESTS.md` §3b. Docs (`docs/getting-started/hands-free.md` + config reference), voice/Settings help (schema `listen.wake_word` / `listen.wake_word_fuzzy` rows), and this doc are in sync. **Improvement thesis vs EDCoPilot/COVAS:NEXT: a local, zero-cost, fuzzy-tolerant wake gate that makes hands-free actually usable in a shared room — arming on the transcript reuses the existing local pipeline (no separate always-on wake-word engine or cloud call), and PTT stays first-class and ungated.**

### Backlog
**Multi-provider support (issue #10) — COMPLETE.** TTS track: #14 registry → #15 Edge → #16 OpenAI TTS → #17 Azure Neural → #18 Cartesia (all done). LLM track: #11 provider-agnostic router → #12 OpenAI-compatible → #13 Gemini (all done). The provider seam now spans free/local, free-tier, cheap-cloud, and premium across both LLM and TTS, all on the router/registry foundations. Otherwise every prompt in `CLAUDE_CODE_PROMPTS.md` (Prompts 1–7, Search 1–6, N1–N11, C1–C11, I1–I9) is built and merged. **The prompt pack / GitHub issues carry the live worklist; this doc carries the architecture.**

---

## 8. Watch-items / risks

- **Tier quality feel.** Haiku-by-default changes the companion's voice/reasoning feel vs. Sonnet/Opus. A/B on real sessions and tune the escalation rules; keep the manual override so you can force a tier when it matters.
- **Reply truncation.** A low `max_tokens` can cut off a genuinely long answer mid-sentence — bad over TTS. Let the Router raise the cap for explicit "full breakdown" turns rather than setting it too low globally.
- **Journal rollover & partial lines.** Tailing must handle new-file rollover and the occasional half-written final line (retry-on-parse-fail).
- **Key injection into ED.** Expect scancode/`SendInput` work; validate with the one-action prototype.
- **Secret hygiene.** All provider key files are git-ignored **and DPAPI-encrypted at rest** (§3.7, issue #21); env-var key reads were removed. Keep the files git-ignored and out of synced folders. The residual risk is malware/admin already running as the user — explicitly out of the threat model.
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

---

## 10. Persistent memory (epic #58 — foundation #59, capture #60)

A companion that forgets your name between sessions doesn't feel like a companion. Persistent
memory lets COVAS++ carry a handful of small facts about you — how you like to be addressed, your
main ship, standing preferences — across restarts. Issue #59 is the **foundation** (store + recall
API); issue #60 is **automatic capture** (populating the store without being asked); issue #61 wires
*recall* into the live turn (a cache-safe memory block) plus a "what do you remember about…" tool.

### The store — transparent by design (`covas/memory/store.py`)
Facts live in a plain **JSON Lines** file, `memory.jsonl`, under the user's writable data dir
(`[memory].dir`, default `memory/`, **git-ignored** and private). One fact per line:

```json
{"id":"…","text":"prefers the Krait Mk II for combat","type":"preference","tags":["ship"],"when":"2026-07-15T12:00:00Z"}
```

`text` is the fact; `type`/`tags`/`when` are light metadata for recall and UX. Only `text` is
required — a user can hand-write a bare `{"text":"…"}` line. **Why per-line JSON, not one big
array:** a single malformed line (a hand-edit typo, a half-written line from a crash) is *skipped*
with a warning, and the rest of the memory survives — the store parses and fails soft line by line.
`add` appends a single line (cheap to grow); `save` rewrites atomically via a temp file (for
edits/pruning). A missing file is simply an empty memory. Nothing here ever raises into the caller.

### Recall — keyword by default, embeddings opt-in (`retrieval.py`, `embedding.py`)
`Retriever(store, embedder=None).recall(query, tags=…, limit=…)` returns the most relevant facts.
The **default** scorer is bag-of-words token overlap with a **tag bonus** (a query word matching a
curated tag outweighs a body-text match) — pure standard library, deterministic, **offline, free,
zero new dependency**. An optional `tags=` hard filter gives cheap structured recall; an empty
query returns the most recent facts.

Semantic recall (matching by *meaning*, not shared words) is an **optional embedding seam** that is
**OFF by default**. It mirrors the provider pattern: a tiny one-method `EmbeddingProvider` Protocol,
built only when `[memory.embedding].enabled = true` names a backend, with the heavy import kept
**lazy** inside `build_embedder` so the disabled path imports nothing extra. No backend ships in the
foundation; enabling it without one falls back to keyword recall. A dead/failing embedder at runtime
also degrades to keyword rather than crashing recall. Dependencies are **injected** (the store takes
a path; the retriever takes an optional embedder), so the default `pytest` run stays hermetic — tests
point the store at a tmp file and pass a deterministic fake embedder to exercise the similarity path.

### Automatic capture — journal milestones + conversation facts (#60, `memory/capture.py`)
The store fills itself, from two **cost-free** sources, wired as a self-contained
`MemoryCapability` (capabilities over loop edits — it subscribes to the bus, no worker-loop
branch) gated on `[memory].enabled`:

- **Journal milestones (deterministic).** A curated `describe_highlight` table turns a *small,
  high-signal* set of journal events — first discovery / full mapping of a body, death, rank
  promotion, buying a carrier or a new ship, and *notable* (≥ `NOTABLE_CREDITS`, 10 M) missions /
  exploration payouts / voucher redemptions — into a durable one-line `milestone` memory. This
  mirrors the recent-events feed's curated-describer style (§5) but writes a **permanent** fact
  rather than a rolling line. **No LLM per event** — a table lookup. It rides the live bus via the
  event pump (`replay=False`), so relaunching never re-captures an existing journal (the watcher
  primes context *without* republishing).
- **Conversation facts (piggybacked).** A `remember_this` tool lets the LLM store a standing
  preference/instruction ("remember that…", "call me Commander") **during a turn it's already
  producing** — the tool-use rides the existing reply, so there is **no extra model call** and no
  extra cost. This one tool serves both the explicit "remember that X" command and the model
  proactively noting a durable fact it just heard.

Both paths **dedup** against `store.all()` before writing — a verbatim (normalized) match, or a
`keyword_score` at/over `DEDUP_THRESHOLD` (0.9), is skipped, so a repeated milestone or a reworded-
but-identical fact isn't stored twice (pure/offline — **no embedding**). A **cap** (`[memory].cap`,
default 500) bounds the file: over cap, the oldest auto `milestone` records are evicted first
(reproducible from the journal), and facts the Commander explicitly asked to keep are evicted only
if they alone exceed the cap. Every capture method is fail-soft — a bad event or store error is
logged, never raised, so capture can't take down the event pump or the voice loop.

### Recall in conversation — cache-safe injection + explicit tool (#61, `memory/detector.py`)
Recall extends the same `MemoryCapability` (still capabilities-over-loop-edits). Two paths, both
keyword/tag by default (free, offline):

- **Automatic injection (cache-safe).** A pure-rules `MemoryDetector` — the exact twin of the ED
  `ContextDetector` (§5): `from_cfg` → `.decide(text) -> MemoryRef(.matched, .reason)` + `.strip()`
  — classifies whether a turn reaches into the past (`[memory].recall_phrases` like "do you
  remember", "what's my…", plus a `recall_wake` override, both config-tunable). On a match, the
  worker loop asks `MemoryCapability.recall_block(query)` for a **compact** parenthesized block of
  the top facts and **prepends it to THAT turn's user message only** — composing with the ED
  telemetry block identically (both prepend to the per-call `llm_text` while `self.history` keeps
  the clean `user_text`). It rides the **uncached** user message, never the cached system prefix,
  so recall **cannot bust the prompt cache** — the crux of the issue, and asserted by a test
  (`test_memory_recall_is_cache_safe`) that checks the block lands in the per-turn tail while
  stored history and the cacheable prefix stay clean.
- **Explicit tool.** A `recall_memory` tool lets the LLM look memory up mid-reply ("what do you
  remember about my ship") and answer from stored facts instead of guessing.

Recall is fail-soft: a miss (or any retriever error) injects nothing and never crashes the turn.
Gated on `[memory].enabled`; the embedding seam stays OFF, so the default path is free and offline.

### Memory browser — the user-editable ship's log (#62, `web.py` + `templates/memory.html`)
A `/memory` tab in the control panel makes the store's transparency **actionable**: read, search,
edit, delete, and add memories from the panel — the differentiator EDCoPilot / COVAS:NEXT don't
offer. It deliberately mirrors the checklist editor (N10): the web routes (`memory_page`,
`memory_state`, and `memory_add` / `memory_edit` / `memory_delete`) reuse the same content-hash
`_file_version` **stale-write guard** — every read hands the client a `version`, and any mutation
whose `base_version` no longer matches the file on disk (a voice append/prune landed underneath) is
refused with **409** carrying the current state, so the panel reloads instead of clobbering. Web and
voice share the **one physical `memory.jsonl`**: the routes edit the app's live `MemoryStore`
instance (exposed as `MemoryCapability.store`) when memory is on — so mutating it keeps the
in-memory list authoritative and a later voice prune can't lose a web edit — falling back to
`store_from_config(cfg)` (same file) when capture is off. Every write is a whole-file atomic
`store.save(records)`; edits target a record **by `id`**, preserving its `id` and original `when` so
they round-trip losslessly. The template is pure vanilla JS (no CDN), so the tab works fully offline.

### Cost & privacy stance — and how this beats the competitors
Two deliberate defaults, both cost- and privacy-forward:
- **Transparent, not opaque.** Memory is a human-readable file you own, can read, edit line by line,
  or delete — and it's git-ignored so it never leaves your machine. EDCoPilot / COVAS:NEXT keep memory
  in an opaque, vendor-controlled store you can't inspect or hand-correct. Ours is a text file.
- **Free by default, paid only on request.** Recall works fully offline with no API and no extra
  dependency; embeddings (which cost money and phone home) are strictly opt-in, consistent with the
  project's cloud-tiering cost philosophy (§4) — spend only where a turn earns it.

---

## 11. Vision / screenshot awareness — spike (issue #55)

*Research spike, not shipped. Full findings + cost model + throwaway capture PoC: `docs/spikes/vision-spike-55.md` (`docs/spikes/screenshot_poc.py`).*

**Question:** can COVAS++ usefully "see" ED via screenshots + a vision-LLM, worth the cost? **Recommendation: GO (narrow) — build an opt-in, ON-DEMAND, router-tiered "read a value off the HUD on request" prototype; NO-GO on any always-on / polling / closed-loop-control use of vision.**

- **Capture is the easy part.** `mss` (pure ctypes, PyInstaller-clean) is the PoC path; **Windows Graphics Capture (WGC)** is the shipping path for fullscreen-exclusive + VR-mirror robustness (pulls WinRT, needs a `covas.spec` `collect_all` + `--selftest` guard like `edge_tts`). GDI grabs (`mss`/`PIL.ImageGrab`) return **black frames** under true fullscreen-exclusive DirectX → run ED **borderless** for the PoC, WGC for ship. In **VR**, ED renders to the HMD and the monitor shows a capturable **mirror window** — we grab what the desktop shows (WGC can target the mirror window by handle). Grab is 5–50 ms; the cost/latency live in the LLM call.
- **No new provider.** Anthropic / OpenAI / Gemini all accept image inputs; the image rides as a content block in the user message (`messages` is already `list[dict]`), so `LLMProvider.stream_reply(...)` is unchanged — the only work is teaching each provider's translator to emit its native image block (Anthropic `image` / OpenAI `image_url` / Gemini `inline_data`), exactly like the tool-call translation already in `openai_llm.py` / `gemini_llm.py`.
- **Cost (rough, on-demand):** downscale to ~1568px long edge (~1,600 Anthropic image tokens; **downscaling is the main cost lever** — hi-res 2576px is up to ~4,784 tok/image). Per vision call ≈ **$0.003 Haiku (cheap) / ~$0.009 Sonnet (standard) / ~$0.014 Opus / <$0.001 Gemini Flash / ~$0.005 GPT-4o**. A session that looks 5–10× costs **1.5–15¢ — negligible.** OpenAI/Gemini per-image token counts are estimates **needing confirmation**; Anthropic `(w×h)/750` is documented. Everything costs out of the existing `[pricing]` table via `llm.estimate_cost`.
- **Cost stance (NON-NEGOTIABLE):** **on-demand ONLY, never a polling loop.** A 1 fps vision loop is ~$5–$50/hour and adds 2–4 s latency/frame — unaffordable and useless for control. Vision is **router-tiered** exactly like text (default the look to the **cheap** tier; escalate for ambiguous scenes), and the image goes on that turn's user message **after** the cached system+tools prefix (uncached, never stored in history) so it can't bust the prompt cache — the same discipline as the inline ED `context_block()` (§5).
- **Approach:** a `VisionCapability` (opt-in, default off) that mirrors the inline-context pattern — a wake-phrase/`VisionDetector` **or** an LLM `look_at_screen(question)` tool triggers a single grab → downscale → attach-as-image on that turn → router tiers it. Fail soft: a black/failed frame degrades to a spoken "couldn't read the screen," never crashes the loop. It's a capability plugin (§3.3), not a loop edit.
- **What it unlocks (ranked):** (1) **reading on-screen values the journal does NOT expose** — distance-to-target/station, sub-target module, throttle %, exact heading, GUI panel text — the highest-value use and the **#50 tier-2 enabler**; (2) target ID / visual confirmation (cheap add-on); (3) generative HUD content — **not worth it**, epic #40's HUD is deliberately companion-state-centric (voice-loop state + checklist + route) and needs no vision.
- **Ties:** **#50 (aim/distance)** — ED does not stream live distance-to-target, so an on-demand vision **read** ("look now — within 7.5 km?") is the only way to answer the tier-2 ceiling today, but as a *check*, **not a continuous trigger** (that's polling); tier-3 analog aiming stays out (2–4 s latency makes closed-loop aiming infeasible/unsafe). **#40 (HUD/VR overlay)** — #55 is **not** required for #40 and must not block/bloat it; note the relationship, keep them decoupled.
- **Beats-competitors thesis:** EDCoPilot / COVAS:NEXT are **blind to anything ED doesn't write to disk** (journal / `Status.json` / EDDN only). On-demand vision lets COVAS++ answer from **what's literally on screen** (distance-to-target, sub-target, panel contents) — a capability axis competitors *structurally* cannot reach — while staying cost-disciplined (on-demand, cheapest capable tier, pennies/session), not a dollars/hour vision loop.
- **Unverified (needs on-hardware validation):** whether a **cheap-tier** model (Haiku/Flash) reliably reads small orange-on-black ED HUD text at 1080p (the single most important prototype question); exact OpenAI/Gemini per-image token counts; fullscreen-exclusive capture on Doug's exact GPU/ED install; VR mirror-window legibility. The throwaway PoC is byte-compiled but **un-run against a live game**.

---

## 12. Route & activity planners (epic #39 — foundation #41)

The activity planners (Road to Riches #42, neutron/galaxy #43, trade #44) sit on one piece of shared plumbing built here (`covas/search/routes.py`); the mining helper (#45) reuses the same two differentiators from a separate synchronous-search module (`search/mining.py`, see below). Two things make COVAS++'s planners better than the competitors': **closed-loop plotting** (a computed route is handed to the galaxy map, not just read aloud) and **freshness** (volatile market data is answered honestly).

### The async route client (`search/routes.py`)
Spansh's ROUTE endpoints are **asynchronous**, unlike the synchronous `/search` transport (§ outfitting/search): a `POST` enqueues a job and returns **HTTP 202** with `{"job": "<id>"}`, then you **GET `/api/results/<id>`** until it completes. `submit_and_poll(http, url, params, …)` runs that whole flow over the SAME injected `Http` seam (`get_json` added alongside `post_json`), with a bounded poll (mirrors the frontend's ~20×@1 s), injected `sleep` (tests run instantly), and spoken-friendly `NavError`s on any failure. Route params ride on the **query string** (Spansh's route convention — booleans as `true`/`false`), not the structured JSON the `/search` filters use.

- **Galaxy/neutron plotter** (`build_galaxy_request` / `parse_galaxy_route`) — `POST /api/route?efficiency&range&from&to` → `result.system_jumps[]` of `{system, jumps}`. **Confirmed from a live client.**
- **Trade planner** (`build_trade_request` / `parse_trade_route`) — `POST /api/trade/route`, exposed by `RoutePlanCapability` (`plan_trade_route`). Enriched into the full feature in **#44** (from the #41 proof): it speaks the **whole multi-hop loop** — every leg's buy-here / sell-there / profit-per-ton in sequence ("Buy X at A and sell at B… Then buy Y at B and sell at C…") plus the **round-trip total** (sum of per-hop `total_profit`) — not just the top hop, and plots the first destination via `RoutePlotter`. The request exposes the useful trade-form knobs as tool args: `jump_range` (max hop distance), `max_hops`, `max_arrival_distance` (star→station supercruise cap), `requires_large_pad`, `allow_planetary`, `avoid_loops` (→ `unique`), and a per-run `max_price_age_days` override. Freshness is the headline (see below): each leg carries a per-hop age tag when its source price is stale, and a wholesale-old loop adds the summary caveat. The request params + result hop fields are built to the observed trade planner (its form fields + the shared job/poll pattern) and remain **LIVE-VERIFY** — isolated in those two functions so an on-hardware field-name correction is a one-function change. Road to Riches (#42) adds a sibling builder on the same async client; mining (#45) is a synchronous-search sibling (`search/mining.py`) that reuses only the `RoutePlotter` + freshness helpers. **Beats the competitors:** EDCoPilot/COVAS:NEXT read a single crowdsourced hop; COVAS++ speaks the whole verified-fresh loop with round-trip profit off one command and hands the next stop to the galaxy map.
- **Road to Riches** (`build_riches_request` / `parse_riches_route`) — `POST /api/riches/route`, exposed by `RichesPlanCapability` (`plan_riches_route`, #42): from the Commander's current system + laden jump range (plus optional radius / max results / min scan value), it plans a chain of nearby systems with high-value **UNSCANNED** bodies to First-Discovery-scan for exploration credits, speaks a summary (first system, body count, estimated first/total value), and plots the first system via the same `RoutePlotter`. The request params (`reference_system`, `range`, `radius`, `min_value`, `max_results`, `use_mapping_value`) + result fields (`systems[]` of `{system, jumps, bodies[]}`, each body `{name, subtype, value}`) are built to the observed Road-to-Riches form and are **LIVE-VERIFY** — isolated in those two functions so an on-hardware correction is one edit. No freshness caveat here: scan values are stable, unlike volatile market prices. **Beats the competitors:** EDCoPilot/COVAS:NEXT read a Road-to-Riches list aloud; COVAS++ closes the loop by handing the first system straight to the galaxy map off one spoken command.

### Neutron / long-range galaxy planner (#43 — `NeutronPlanCapability`, `plot_neutron_route`)
The second capability on the foundation, and Elite's long-range travel workhorse: the galaxy plotter with `efficiency > 0` rides the neutron highway to get from A to B in **far fewer jumps** than a straight route. It reuses the confirmed-live galaxy machinery unchanged — `build_galaxy_request` (`efficiency`/`range`/`from`/`to`) + `submit_and_poll(ROUTE_URL, …)` + `parse_galaxy_route` — so it needed a **capability, not new client code**. The Commander gives a **destination** system (required) + their **laden jump range** (required — asked for if missing); the start defaults to their current system (`_current_system`), and `efficiency` (1–100, clamped; `[neutron_plan].default_efficiency`, default 60) is an optional nudge toward fewer jumps or a more direct route. It speaks the summary (**total jumps** = the last waypoint's cumulative `jumps`, **number of waypoints**, the **first waypoint**) and hands the first waypoint to the galaxy map via `RoutePlotter.plot_next` (clipboard until #32). No market data, so **no freshness caveat** — the plotted systems are static. Config `[neutron_plan]` (enabled default off, `user_agent`, `default_efficiency`); fail-soft and hermetic like its sibling.

### Mining helper (#45 — `MiningHelperCapability`, `plan_mining_session`; `search/mining.py`)
The one activity planner that does **not** ride the async route client: a mining session needs two ordinary **synchronous** Spansh `/search` queries, so its builders/parsers live in a separate module (`covas/search/mining.py`) over `spansh.execute_search`, leaving the shared `routes.py` untouched. It still reuses the route stack's two differentiators — `RoutePlotter` (plot handoff) and the `spansh.data_age_days` freshness parser. One tool ties three pieces together for a named `material` (Painite, LTDs, Void Opal, Tritium…): (1) **hotspot finder** — `build_hotspot_request` / `parse_hotspots` `POST /api/bodies/search` with a `ring_signals` filter (`[{name, value:[min,max]}]`), distance-sorted, returning the nearest ring's `{system, body, ring, count, signals_updated_at}`; (2) **best sell** — `build_sell_request` / `parse_sell_markets` `POST /api/stations/search` filtered to stations trading the commodity, **sorted by that commodity's sell price DESC** (`market_sell_price:[{name,direction}]`), then `best_sell` picks the best **fresh** quote; (3) **checklist loop** — the go-to-hotspot / mine / sell-here steps dropped onto the Commander's checklist via the shared `Checklist` model (not a parallel mechanism), plus the optional `RoutePlotter` handoff of the hotspot system. **Freshness is the headline and the differentiator here** (mining prices swing hardest): confirmed live that the very highest sell prices are almost all **fleet carriers with years-stale market data**, so `parse_sell_markets` drops transient carriers (`is_fleet_carrier`) and `best_sell` returns `(market, is_stale)` — the best fresh quote, or the best available flagged stale so the readout speaks an age caveat rather than quoting a dead price. Both request/result shapes are **LIVE-VERIFIED** against the real API (2026-07) and isolated in `mining.py`. Config `[mining_helper]` (enabled default off, `user_agent`, `max_price_age_days`, `add_to_checklist`); fail-soft and hermetic — a failed sell lookup still yields the hotspot + loop. **Beats the competitors:** EDCoPilot/COVAS:NEXT read a hotspot/price aloud; COVAS++ verifies the sell price is *fresh* (not a phantom carrier quote), drops a trackable loop, and plots the hotspot — one command.

### Freshness discipline (the differentiator, built once)
Market prices rotate constantly, so a trade route carries a per-hop `updated_at`. The client-side backstop reuses the search layer's day-window age helper (`spansh.data_age_days`) at two granularities: `hop_age_days(hop)` gives one leg's source-price age, so the readout tags **individual** stale legs inline (*"…for about 7,200 a ton (price ~4 days old)"*); `stale_age_caveat(hops)` returns a spoken **summary caveat** only when even the *freshest* hop is older than `max_price_age_days` (default 2) — i.e. the whole loop rests on stale data. A stale-but-only answer is **spoken WITH the caveat**, never silently dropped — the "answer stale, honestly" fallback. The request also carries the server-side max-age param (Maximum Market Age in the trade form; unit LIVE-VERIFY, overridable per run), but the client backstop is authoritative regardless.

### Plot handoff — closed loop (`RoutePlotter`)
`RoutePlotter.plot_next(waypoints)` hands the next stop to the galaxy map. Until the Tier-1 galaxy-map keybind automation (**#32**) lands, it degrades to **copying the next waypoint's system name to the clipboard** so the Commander pastes it into the galaxy-map search; when a `set_course` callable is later injected (the keybind path), it's tried first and the clipboard is the fallback — so planners call `plot_next` unchanged across that transition. Both seams are injected and fail soft.

### How this beats the competitors
Competitors surface raw crowdsourced route data and (EDCoPilot) set course via a VoiceAttack layer. This foundation makes every COVAS++ planner **verified-fresh** (an old price is flagged, not passed off as current) and **voice-plotted end-to-end** (clipboard now, in-game course-set once #32 lands) — using machinery only COVAS++ has.
