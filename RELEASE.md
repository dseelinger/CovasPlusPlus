# Releasing COVAS++

The checklist for cutting a release. COVAS++ follows [SemVer](https://semver.org/): while pre-1.0,
**minor** versions add features and **patch** versions are fixes.

> Why this file exists: the steps below are **developer/release rituals** that need the repo and a
> `.venv`. They deliberately live here rather than in `MANUAL_TESTS.md`, which is scoped to
> installed-app acceptance only (see its scope preamble). Correctness that a hosted runner can prove
> lives in `pytest`/CI; this file is the human release runbook.

## 1. Refresh the bundled game data — *before* the version bump
The app is **offline at runtime**, so cutting a release is the only moment its bundled
ship/module/engineering data converges on live community data. Regenerate it first so a downloaded
build ships current data:

```powershell
.venv\Scripts\python.exe scripts\refresh_datasets.py
```

- Review the printed **diff summary** (new hulls / modules / blueprints / orphaned overlay rows) and
  the *last refreshed* nag for the hand-curated engineer tables (refresh those by hand if they've drifted).
- **New FDev hull?** `scripts\gen_ship_specs.py` **fails loudly naming the ship** rather than silently
  dropping it — run `scripts\gen_ship_roster.py --fetch` first to harvest its name/symbol, then
  regenerate so the match needs no hand edits. (Covered by `tests/test_ship_roster_pipeline.py`.)
- **Determinism check:** `scripts\refresh_datasets.py --no-fetch` regenerates byte-identical from the
  committed snapshots with no network — `git status` clean apart from the manifest date.
- **Confirm freshness:** the panel's **Settings → Test my setup → Game data** section shows no
  `[warn]` (nothing older than ~6 months). This report rendering is covered by
  `tests/test_health.py::test_datasets_freshness_*`.
- Commit the regenerated data + manifest.

## 2. Bump the version
- Edit **`covas/__version__.py`** — the single source of truth. The runtime update-check and the
  frozen installer both read it; bumping it is the only code change a release needs.
- Add a **`CHANGELOG.md`** entry (Keep a Changelog format) and its `[#NNN]` link reference at the
  bottom. A test-only or CI-only release should say so ("the shipped app is unchanged").

## 3. Verify locally
CI (`.github/workflows/tests.yml`) runs these on every push, but run them before tagging:

```powershell
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m compileall covas
.venv\Scripts\python.exe -m pytest
```

## 4. Cut the release
Commit and push to `main`, then publish a GitHub Release whose tag is `vX.Y.Z` (matching
`__version__`):

```bash
gh release create vX.Y.Z --target main --title "vX.Y.Z" --notes-file <notes.md>
```

Publishing triggers **`.github/workflows/release.yml`**, which on a **published** release:

1. creates the `.venv` and installs the build + dev deps,
2. runs the **unit suite as a gate** — a red suite fails the job *before* any installer is built,
3. runs `build.ps1 -Installer -SelfTest` (PyInstaller freeze → frozen `--selftest` import check →
   Inno Setup compile), and
4. `gh release upload`s **`COVAS++ Setup.exe`** onto the release.

It uses the built-in `github.token` (no secrets). To (re)build an installer for an **existing** tag,
use the workflow's `workflow_dispatch` (Actions → **release** → *Run workflow* → enter the tag).

## 5. Verify the release
- The **release** workflow runs green (Actions tab) and the installer appears as a **release asset**
  within a few minutes (GitHub stores the space in the name as a dot: `COVAS++.Setup.exe`).
- Install it → the Setup wizard / **Apps & features** entry shows the **release tag's version**.
- The build is **unsigned by design** — SmartScreen warns on first run (expected; see
  `MANUAL_TESTS.md` §19.1).
