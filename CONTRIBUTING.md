# Contributing to COVAS++

Thanks for your interest in COVAS++ — a local, voice-first ship's AI for Elite Dangerous. This
guide covers building from a **clean checkout**, running the tests, and submitting a change.

> Please also read the [Code of Conduct](CODE_OF_CONDUCT.md). By participating you agree to abide
> by it.

Before you start, a candid word on scope and pace — see
[Maintainer status & response expectations](README.md#maintainer-status--response-expectations) in
the README. COVAS++ is a solo, high-velocity project; a quick issue **before** a large PR saves
everyone time.

---

## What you'll need (host requirements)

COVAS++ is a **Windows** desktop app, and a few of its foundations are genuinely Windows-only. You
can develop and run the offline test suite on Windows without any special hardware; running the
full voice loop additionally needs a mic, speakers, and (for game features) Elite Dangerous.

| Requirement | Why | Notes |
|-------------|-----|-------|
| **Windows 10/11** | DPAPI key encryption (`covas/dpapi.py`), global PTT hotkeys (`keyboard`), scancode injection (`SendInput`), the PyWebView app window | The unit suite has only been verified on Windows; it is not guaranteed to import cleanly on macOS/Linux because some modules import Windows-oriented libraries at import time. |
| **Python 3.11** | The build target (`covas.spec`, the docs workflow, and the packaged app all pin 3.11) | 3.11.x is what CONTRIBUTING and CI assume. Newer 3.x may work for source runs but isn't the shipped target. |
| **git** | Clone + PR flow | — |
| A microphone + speakers | Only to run the **live voice loop** (not for tests) | The default `pytest` run needs neither. |
| A cloud LLM key (Anthropic) | Only to run the live app | Not needed to build or to run unit tests. |

There are **no absolute paths or machine-specific assumptions** in the tree — `config.toml` uses
relative paths resolved at load time, and every personal file (keys, `personality.txt`,
`overrides.json`, logs) is git-ignored. A clean checkout builds and tests with nothing from any
particular machine. (This was verified: a fresh `git archive` export + a new venv + the offline
suite, all green — see [Build from a clean checkout](#build-from-a-clean-checkout) below.)

---

## Build from a clean checkout

These are the exact steps used to verify a contributor can build without the author's machine.
Run them from the repo root in **PowerShell**:

```powershell
# 1. A fresh Python 3.11 virtual environment
py -3.11 -m venv .venv        # or: python -m venv .venv  (if 3.11 is your default)

# 2. Dev dependencies (runtime + pytest + ruff + mkdocs). A superset of requirements.txt.
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt

# 3. Sanity: every module byte-compiles
.venv\Scripts\python.exe -m compileall covas

# 4. The offline, free unit suite (no network, no API, no audio, no hardware)
.venv\Scripts\python.exe -m pytest
```

The project is **not** pip-installed — `pyproject.toml` sets `pythonpath = ["."]`, so
`from covas...` imports resolve directly. There is nothing to `pip install -e`.

To run the app itself (needs the personal files + a key — see the README
[Quick start](README.md#quick-start-from-a-fresh-clone)):

```powershell
.venv\Scripts\python.exe run_covas.py        # headless voice loop
.venv\Scripts\python.exe run_covas_ui.py     # + localhost control panel (http://127.0.0.1:8765)
.venv\Scripts\python.exe check_setup.py      # environment health report (change/cost-free)
```

### Building the installer (optional)

Packaging the frozen Windows app needs the build extras and a couple of libraries that must be
present **in the build env** so PyInstaller can bundle them (`openvr`, `pillow`):

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\build.ps1                                   # PyInstaller (covas.spec) -> Inno Setup (covas.iss)
```

If you redistribute a build, keep [`NOTICE.md`](NOTICE.md) — the third-party bill-of-materials —
alongside it; several bundled components carry attribution/copyleft obligations.

---

## Running the tests

The default run is **offline and free** by design — dependencies are injected, and tests pass
fakes (`tests/fakes.py`) instead of real providers. Anything that touches a real service or device
is opt-in behind a marker.

```powershell
python -m compileall covas                    # fast sanity check after edits
pytest                                        # UNIT tests only — offline, free, run often
pytest -m "integration and local"             # free integration (Piper / Whisper / audio devices)
pytest -m "integration and paid"              # deliberate — COSTS money (Anthropic / ElevenLabs)
ruff check covas                              # lint (config in pyproject.toml)
```

Bare `pytest` **must stay offline and free** — no network, API, ElevenLabs, or audio. If your
change adds logic that would need a real service, inject the dependency and pass a fake in the
test; mark any genuinely external test `@pytest.mark.integration` plus `local` (free) or `paid`
(costs money). See `DESIGN_AND_ROADMAP.md` §9.

Ship-critical paths (real audio devices, the running game, ElevenLabs) need on-hardware testing
and can't be fully exercised in CI — capture a manual check in `MANUAL_TESTS.md` when your change
touches one.

---

## Making a change

**Architecture in one breath:** the voice loop lives in `covas/app.py`; swappable LLM/TTS/STT
providers sit behind `covas/providers/`; features are self-registering modules in
`covas/capabilities/`. Prefer adding a **capability** over branching the loop. `CLAUDE.md` and
`DESIGN_AND_ROADMAP.md` carry the full conventions — skim them before a non-trivial change.

**House style:** Python 3.11, standard library first (add a dependency only when it earns its
place, and note it in the PR). Type hints, module docstrings, comments that explain *why* not
*what*. Keep diffs small and reviewable. Fail soft — the voice loop must survive any provider/tool
error and return to idle.

**Definition of done (from `CLAUDE.md`):** a feature isn't finished until it's reflected in all of
— the documentation site (`docs/`), a manual check in `MANUAL_TESTS.md`, the capability's in-app
help metadata, and `DESIGN_AND_ROADMAP.md` if the architecture changed. Update them in the same
change.

**Guardrails (public repo):** never commit secrets or personal data. All provider key files, plus
per-user data (`personality.txt`, `overrides.json`, `logs/`, voice models, `memory/`, …) are
git-ignored — keep it that way. Don't hardcode API keys, absolute `C:\Users\...` paths, a username,
or the Commander's identity anywhere tracked.

---

## Submitting a pull request

1. **Open (or find) an issue first** for anything non-trivial, so effort isn't wasted on a change
   that won't be merged. Bugs use the bug template (it asks for `check_setup.py` output — please
   paste it).
2. **Branch** off `main` — one focused change per branch. A short, descriptive name is fine
   (`fix/route-reprompt`, `docs/contributing`).
3. **Build & test before you push:** `python -m compileall covas` and the relevant `pytest`
   tier(s). Keep bare `pytest` green, offline, and free.
4. **Fill in the PR template** — what changed, why, how you tested, and the definition-of-done
   checklist.
5. **Expect asynchronous review.** See the maintainer-status note; a well-scoped, tested PR that
   matches an existing issue is the fastest path to a merge.

Small, well-tested, single-purpose PRs are merged fastest. Thanks for helping COVAS++ fly. o7
