# INSTALLER_DESIGN.md — packaging COVAS++ as an installable Windows app

Design of record for turning COVAS++ from a "install Python, run the server, open a
browser" project into a double-click Windows app. **No build code exists yet** — this
captures the decisions so the eventual build prompt(s) have a spec to follow. Companion
to `DESIGN_AND_ROADMAP.md`; when the build lands, fold the architecture-affecting parts
back into that doc.

## Goal
A user downloads one `COVAS++ Setup.exe`, double-clicks, and gets a Start-menu entry and
desktop icon. Launching opens a **native window** (not a browser tab) into the existing
Flask control-panel UI. No separate Python install, no manual server start, no browser
step. The voice loop and web UI are unchanged underneath — we're only re-wrapping and
re-homing them.

## The stack (three independent layers)

| Layer | Choice | Why |
|---|---|---|
| **Freeze** (kill the "install Python" step) | **PyInstaller**, one-folder mode | One-folder starts faster and trips antivirus far less than one-file (which unpacks to temp every launch). Freezes interpreter + all `requirements.txt` deps into a clean install tree. |
| **Window** ("appify") | **PyWebView** | Wraps the local Flask server in a native OS webview (Edge WebView2, already on Win11). Real window + our icon, no URL bar. Flask + voice loop run on a background thread; webview owns the main thread. Existing templates unchanged. |
| **Installer** | **Inno Setup**, **unsigned** | Free, produces the expected `Setup.exe` with shortcuts + uninstaller, installs over previous versions cleanly. Signing skipped by decision (see below). |

Rejected: Electron/Tauri (would bundle a second runtime in front of Python for no gain),
one-file PyInstaller (temp-unpack + AV), MSIX (forces signing; its container would fight
the global PTT hotkey and ED-journal/bindings file access — see below).

## Decisions (locked)

1. **CPU only. No GPU, ever, in the packaged app.** faster-whisper STT already defaults to
   `device="cpu"`, `compute_type="int8"` — plenty fast for short push-to-talk clips. The
   installer ships CPU-only, no CUDA libraries, **no opt-in GPU download for power users**.
   (The `whisper.device` setting can stay in the schema for source-run users, but the app
   experience and docs treat CPU as the only path. Nothing in the installer references a GPU.)
   - Note: nothing else touches the GPU — the LLM (Anthropic) and premium TTS (ElevenLabs)
     are cloud; Piper TTS is CPU. This is deliberate: the in-game LLM stays in the cloud so
     it never competes with Elite Dangerous for the GPU.

2. **Download-on-first-run for the STT model.** Installer stays small (no bundled weights).
   First launch downloads the STT model into the user-data dir. Needs network on first run;
   documented as such.
   - **STT default: faster-whisper `small`** (~250 MB; English-only `small.en` unless
     multilingual is needed) — best accuracy/speed balance on CPU int8 for voice commands.
   - **TTS: ElevenLabs only (cloud) for now.** Local **Piper is not offered** in the
     packaged app — no default voice download. TTS therefore requires an ElevenLabs key;
     with no key the app degrades to **text-only** (the existing fail-soft "dead TTS → text"
     path) until a key is entered.
   - *Future (not this build):* additional model options are planned — OpenAI for the LLM,
     Edge natural voices for TTS, etc. The first-run flow should be built so adding
     providers later is easy, but ship with the two defaults above only.

3. **First-run setup flow** (new screen in the existing Flask UI): enter API keys
   (Anthropic required for the LLM; ElevenLabs for voice), download the STT model, confirm
   mic. Keys are always user-supplied — the installer **never** contains secrets
   (public-repo guardrail). No local-voice download step (Piper not offered).

4. **Closing the window quits the app.** No system tray, no background voice loop after
   close. Simple mental model.

5. **Updates: Tier 2 (download + relaunch installer).** On launch the app checks the GitHub
   Releases API, compares to its baked-in version string, and on a newer release shows an
   "update available" banner. The app **downloads the new installer itself**, launches it,
   and exits so the installer can replace the running files (a running `.exe` can't
   overwrite itself — hence the hand-off). GitHub Releases *is* the update server; no
   infrastructure to run. Requires a version string baked into the build (see below).

6. **Updates preserve user settings whenever possible.** An update replaces only the
   read-only app payload; it **must not clobber user state** in `%APPDATA%\COVAS++\`
   (keys, `overrides.json`, personality, checklist, changed settings). Concretely:
   - The installer writes/overwrites only the payload dir; it never touches the user-data
     dirs (this is another reason those live outside the install tree).
   - **Defaults are applied once, at first run — never re-applied on update.** If a user
     changed the TTS voice away from "George", picked a different mic, edited any setting,
     the update leaves those exactly as-is.
   - When an update *adds* a new setting, it fills in the new setting's default without
     resetting existing ones (additive migration, not overwrite). If a stored value ever
     genuinely can't be carried forward, prefer preserving user intent and log it rather
     than silently reverting to default.

7. **No code-signing cert.** OV/EV certificates are an annual cost + business-identity
   vetting we're skipping. Consequence: **SmartScreen "unknown publisher" and possible AV
   false-positives** on first download. Mitigations: one-folder PyInstaller (reduces AV
   hits), and **documentation** that tells users what to expect and how to proceed ("More
   info → Run anyway"). If AV friction ever becomes a real support burden, the highest-
   leverage fix is buying an OV cert and signing the *Inno* installer — revisit then. This
   is also *why* MSIX is out: MSIX can't install unsigned at all.

## Shippable default assets vs. user overrides (sound cues, and the pattern generally)

The Phase-0 GUI test exposed this: the PTT cue that played was a **personal, copyrighted
clip** (a TNG sample) from the git-ignored `sounds/`. The repo deliberately ships *no*
cue files, so a packaged app would either ship nothing (silent) or — worse — ship
copyrighted audio. Neither is acceptable. Decision:

- **Folder-per-cue-type, random pick, arbitrary count.** Each cue type is a *folder*, and
  the app plays a **random file** from whatever is in it — so a type can have 1 file or 55,
  and users add variety just by dropping in more files (no config edit, no fixed count).
  Cue types: **`listen`, `processing`, `completed`, `failure`** (extensible).
- **Two-tier resolution, per cue type** (checked at play time):
  1. **User override folder** — `%APPDATA%\COVAS++\sounds\<type>\` — if it holds ≥1 file,
     pick randomly from **there** (user set *replaces* the default set; predictable).
  2. **Bundled default folder** — `covas/assets/cues/<type>\` (shipped, **original**,
     read-only) — random pick from here otherwise.
  3. **Neither** — silence (preserve today's fail-soft).
- This **replaces the current `[sound_cues]` explicit-path lists** in `config.toml` with
  folder discovery — a config-model change to make in the Phase 1 refactor. (Migration:
  the shipped defaults move into `covas/assets/cues/<type>/`; the old per-file arrays go
  away.)
- **We ship only originals we own.** Defaults are **synthesized procedurally** (numpy +
  soundfile, already deps) — short original UI blips, a few KB each, brandable, zero
  copyright exposure. CC0 packs were considered and rejected (attribution/licensing overhead).
  - **LOCKED default `listen` cue: `covas/assets/cues/listen/listen_ea.wav`** — an original
    E4→A4 rising fourth (E held ~2× the A), re-voiced from a guitar phrase Doug played (see
    workflow note below). This is the shipped default; the `processing`/`completed`/`failure`
    families are still to be generated in the same voice.
  - Generators live in **`tools/cuegen/`**: `gen_cues.py` (procedural UI cues), `gen_scifi.py`
    (re-voice a melody as synth tones), `analyze_phrase.py` (transcribe a recording's
    pitches/timing). Workflow proven: record a phrase → `analyze_phrase.py` extracts notes →
    `gen_scifi.py` re-voices it. These are the *source* of the original default cues.
- **Users supply their own cues locally.** Dropping a TNG clip (or anything) into their
  `%APPDATA%\COVAS++\sounds\` folder overrides the default. The app **never redistributes**
  it — it's the user's personal copy on their own machine. A "Open cues folder" button in
  the UI makes the location discoverable.
- **This is the general pattern for any rights-uncertain asset** (music tracks in
  `voicelines/`/track dirs, etc.): ship an original/empty default, resolve user overrides
  from the writable user-data dir, never bundle third-party media.

Ties directly into the refactor below — the override folder *is* a user-data dir. `sounds/`
stays git-ignored (user assets); the new `covas/assets/cues/` originals are tracked/shipped.

## Structural prerequisite — writable user-data dir (the one real refactor)

This gates everything and is the biggest code change. Today, config/secrets/logs are files
relative to the project root (`ElevenLabsAPIKey.txt`, `personality.txt`, `overrides.json`,
`logs/`, `ultimate_checklist.md`, downloaded models, etc.). Inside `Program Files` that tree
is **read-only**. All writable/user state must move to a per-user location:

- **`%APPDATA%\COVAS++\`** — config, keys, `overrides.json`, personality, checklist, logs.
- **`%LOCALAPPDATA%\COVAS++\models\`** — downloaded STT/TTS weights (large, machine-local).

Touches `config.py` (path resolution — the `_PATH_FIELDS` mechanism already centralizes
this) and anything that writes beside the project root. Design rule: **app payload is
read-only (installed to `%LOCALAPPDATA%\Programs\COVAS++`, per-user); all writes go to the
user-data dirs above.** A packaged build detects it's frozen
(`sys.frozen`) and switches roots; a source run keeps today's project-root behavior so dev
workflow is unchanged.

## Version string

Single source of truth: **`covas/__version__.py`** (`__version__ = "x.y.z"`). Read by:
(a) the PyInstaller/Inno build to stamp the installer, and (b) the runtime update-check to
compare against the latest GitHub Release tag. One string, two consumers.

## Definition-of-done ripple (docs + tests + voice help)

Per CLAUDE.md, a feature isn't done until docs, manual tests, in-app help, and the design
doc are in sync. Packaging is a big one because it rewrites the install story. When the
build lands, these must change together:

- **`docs/getting-started/install.md`** — near-total rewrite: "download Setup.exe → run →
  first-run wizard." Drop the manual Python/venv steps (or move them under a "run from
  source" advanced section). Add the SmartScreen/AV "unknown publisher" note up front.
- **`docs/getting-started/running.md`** — launch is now the desktop/Start-menu icon, not a
  `.venv` command. Note that closing the window quits.
- **`docs/using/settings.md`, `docs/using/help.md`** — where config/keys now live
  (`%APPDATA%\COVAS++\`), and the updater banner behavior.
- **New `docs/getting-started/updating.md`** — how updates work (banner → downloads →
  installer relaunch).
- **Voice help** (`covas/capabilities/help_capability.py` + settings-schema help text) —
  add a **"what version are you?"** voice query (reads `covas/__version__.py`; natural for a
  companion). **"Check for updates" stays UI-only** — it triggers a network call + the
  download/relaunch flow, which shouldn't be fireable by a stray voice command mid-game.
  Also make sure existing help text no longer describes the old server/browser launch.
- **`MANUAL_TESTS.md`** — on-hardware checklist for the packaged build (fresh-machine
  install, first-run wizard, key entry, model download, PTT works, ED journal/bindings
  still readable from the sandboxless install, update banner → relaunch).
- **`DESIGN_AND_ROADMAP.md`** — fold in the frozen-app architecture + user-data-dir change.

## Phase 0 spike results (2026-07-12) — freeze half PROVEN

A throwaway PyInstaller + pywebview spike (`--collect-all` sledgehammer, scratchpad only,
nothing committed) confirmed the toolchain is viable on this machine:

- **Python 3.14.2 is supported.** PyInstaller 6.21.0 + pywebview 6.2.1 install and run;
  pythonnet ships a cp314 wheel. The "bleeding-edge Python" worry is cleared.
- **The whole native-heavy dep set freezes and runs.** A frozen self-test imported and
  loaded the native libs of every risk: **ctranslate2** (native), **sounddevice/PortAudio**
  (enumerated 69 devices), **soundfile/libsndfile**, **faster_whisper**, **onnxruntime**,
  plus flask/flask_sock/anthropic/pywebview and the **entire `covas` app graph**
  (`config`, `app.App`, `web.create_app`). Exit 0. This was the single biggest risk and
  it's gone.
- **Flask templates** must be bundled with `--collect-data covas` (they live at
  `covas/templates`, package-relative; a code-only collect misses them).
- **Size: 257 MB frozen (one-folder), untrimmed.** With Inno LZMA that's roughly a
  ~120–150 MB download — comfortably within "small installer, models on first run."
- **Trim targets for the real spec (verify at runtime before cutting):**
  - `av.libs` (PyAV/ffmpeg) = **63 MB**, incl. video *encoders* libx265 / SvtAv1Enc that
    an STT audio path never needs. faster_whisper uses PyAV only in `audio.decode_audio`
    (file decode). If COVAS feeds Whisper raw mic PCM (numpy), PyAV may be excludable
    entirely — biggest single win.
  - `onnxruntime` = **34 MB**, pulled by faster_whisper's Silero **VAD** (`vad.py`) and by
    Piper. Piper is dropped from the packaged app; if VAD is unused, onnxruntime may go too.
  - No ROCm/AMD-GPU libs actually landed in `dist/` despite a search-path mention — good,
    ctranslate2's 56 MB DLL is the CPU build.
- **Still to verify interactively (needs the real machine — the GUI half):** pywebview
  window renders the control panel (WebView2), global PTT hotkey fires from the frozen
  windowed app, mic capture + TTS playback work frozen, ED journal/bindings readable, and
  one real `small.en` load+transcribe. Config-path resolution will misbehave until the
  Phase-1 user-data-dir refactor (the spike must be run with cwd = project root).

Implication for the sequence below: **Step 5 (PyInstaller) is de-risked** and becomes
mostly spec-authoring + trimming, not a viability question. Build deps (`pyinstaller`,
`pywebview`) were installed into `.venv` for the spike; formalize them in a separate
`requirements-build.txt` when Step 5 lands, not in `requirements.txt`.

## Clean-install / fresh-machine testing

The installer, the first-run download/wizard, and the updater can only be *proven* on a
machine that has none of the dev state. Constraints and plan:

- **Windows Sandbox is NOT available** — this machine is Win11 **Home** (Sandbox needs
  Pro/Enterprise). Don't rely on it.
- What a bare Win11 target actually needs, and where it comes from: Python + all libs and
  the VC++ runtime are **bundled** by PyInstaller (not required on target); the **Edge
  WebView2 Runtime ships with Win11 by default** (so the pywebview window works out of the
  box). What's deliberately absent — **STT model, API keys, config** — is exactly what the
  first-run flow builds. That absence *is* the test.
- **Tool: a free VM with a clean Win11 snapshot** (VirtualBox or VMware Workstation Player;
  both run on Home). Snapshot clean → run installer → test → revert. This is also the
  **only** way to test the Tier-2 updater end-to-end (install v1 → snapshot → update to
  v1.1 → confirm user settings survived, per decision #6). A cloud Windows VM works too.
- **Fast iteration shortcut (partial):** on the dev machine, delete the HF model cache +
  `%APPDATA%\COVAS++\` to re-exercise the first-run download/wizard without a full VM —
  good for wizard iteration, but it does **not** prove "no Python/runtimes preinstalled,"
  so it's not a substitute for the VM clean-install pass.
- Belongs in `MANUAL_TESTS.md` as the packaged-build acceptance checklist.

## Sequencing (suggested build prompts, each independently shippable)

1. **User-data dir refactor** — frozen-vs-source root detection, move writes to
   `%APPDATA%`/`%LOCALAPPDATA%`, keep source-run behavior. (Prerequisite; no packaging yet.)
2. **Version string + update-check** — `covas/__version__.py`, GitHub Releases check, UI
   banner, Tier-2 download-and-relaunch. (Works from source too; testable before freezing.)
3. **First-run setup flow** — key entry + model download screen in the Flask UI.
4. **PyWebView window** — wrap the UI as a native window; closing quits.
5. **PyInstaller one-folder freeze** — spec file, hidden-import shakeout, CPU-only.
6. **Inno Setup installer** — shortcuts, uninstaller, install-over-previous; unsigned.
7. **Docs + voice-help + manual-tests sync** — the ripple above.

## Resolved (were open questions)
- **STT default = faster-whisper `small`** (`.en` unless multilingual needed). **TTS =
  ElevenLabs only; Piper not offered.** Other providers (OpenAI LLM, Edge TTS) come later.
- **Voice help gains "what version are you?" only**; "check for updates" stays a UI action.
- **Install per-user to `%LOCALAPPDATA%\Programs\COVAS++`** — no UAC/admin prompt, which
  matters more for an unsigned app, and pairs cleanly with the writable-state-under-user-
  profile design. (Chrome/VS Code/Discord default to this.)

## Resolved (build-time details)
- **STT = faster-whisper `small.en`** (English-only: smaller and more accurate than the
  multilingual `small` at the same size; this is an English voice companion). Exact
  source/revision is whatever faster-whisper resolves by default for that model name.
- **Default TTS voice = ElevenLabs "George"** (pre-made voice), resolved by **name**, not a
  hardcoded id — ElevenLabs rotates the voices it presents, so at first run:
  1. Fetch the account's voice list; pick the voice named **"George"** if present.
  2. If "George" isn't in the list, fall back to the **first valid voice** the API returns.
  This is the *initial* default only — set once at first run, and per the update principle
  below, **never re-applied over a voice the user later changed.** (Resolve-by-name +
  first-valid fallback also means first run never dead-ends on an empty/changed catalog.)
