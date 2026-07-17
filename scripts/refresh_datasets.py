"""One umbrella to refresh every bundled FDev-content dataset, then show what changed (#101).

Per FDev patch the workflow becomes: run this, review the diff, run `pytest`, commit. It drives
the individual regen scripts (ship roster + specs, module taxonomy, engineering data), each of
which is FETCH (network) -> GENERATE (pure function of a committed snapshot), and prints a diff
summary (new hulls / new modules / changed recipes / orphaned overlay rows) so a new-content
review is a glance, not an archaeology dig.

Failure contract (matches each script):
  * Spansh ship harvest  — FAIL-SOFT: an outage keeps the committed snapshot (a release never
    blocks on Spansh); noted in the summary.
  * coriolis-data / FDevIDs — FAIL-LOUD: a bad fetch aborts rather than shipping stale data.
    An unmatched coriolis ship file is the NEW-HULL signal (`gen_ship_specs.py` raises).

The hand-curated engineer tables (`covas/ed/engineers.py`, `covas/ed/odyssey_engineering.py`)
are wiki-shaped, not machine-generated — this runner only NAGS their "last refreshed" dates.

    C:/dev/COVAS++/.venv/Scripts/python.exe scripts/refresh_datasets.py            # fetch + generate
    C:/dev/COVAS++/.venv/Scripts/python.exe scripts/refresh_datasets.py --no-fetch # regen from snapshots
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from scripts import dataset_manifest, gen_module_taxonomy, gen_ship_roster, gen_ship_specs  # noqa: E402

_NAV_DATA = _ROOT / "covas" / "nav" / "data"
_ROSTER_JSON = _NAV_DATA / "ship_roster.json"
_SPEC_PY = _ROOT / "covas" / "nav" / "ship_spec_data.py"
_MODULE_PY = _ROOT / "covas" / "nav" / "module_data.py"
_BLUEPRINTS = _ROOT / "covas" / "ed" / "data" / "blueprints.json"

_SPEC_ID_RE = re.compile(r"^    '([a-z0-9_]+)': \{$", re.M)
_MODULE_NAME_RE = re.compile(r"^    \('[^']+', '([^']+)'", re.M)
_LAST_REFRESHED_RE = re.compile(r"last refreshed (\d{4}-\d{2})")


# ---- snapshotting (read from disk so import caching never lies) ----------------------------

def _roster_ids() -> set[str]:
    if not _ROSTER_JSON.exists():
        return set()
    return {r["id"] for r in json.loads(_ROSTER_JSON.read_text(encoding="utf-8"))}


def _spec_ids() -> set[str]:
    return set(_SPEC_ID_RE.findall(_SPEC_PY.read_text(encoding="utf-8"))) if _SPEC_PY.exists() else set()


def _module_names() -> set[str]:
    return set(_MODULE_NAME_RE.findall(_MODULE_PY.read_text(encoding="utf-8"))) if _MODULE_PY.exists() else set()


def _blueprint_keys() -> set[str]:
    return set(json.loads(_BLUEPRINTS.read_text(encoding="utf-8"))) if _BLUEPRINTS.exists() else set()


def _snapshot() -> dict[str, set[str]]:
    return {"hulls": _roster_ids(), "specs": _spec_ids(),
            "modules": _module_names(), "blueprints": _blueprint_keys()}


def _orphaned_overlay() -> list[str]:
    """Alias-overlay ids the generated roster no longer defines (the fail-loud contract, surfaced
    here as a summary line too). Reads the overlay directly to avoid importing a roster that would
    itself raise on the orphan."""
    from covas.nav.ships import _ALIASES  # noqa: PLC0415 — local: after a fresh generate on disk
    return sorted(set(_ALIASES) - _roster_ids())


# ---- stages --------------------------------------------------------------------------------

def _fetch(name: str, fn) -> None:
    print(f"\n--- {name} ---")
    fn()


def run(*, fetch: bool) -> None:
    before = _snapshot()

    args = ["--fetch"] if fetch else []
    _fetch("ship roster", lambda: gen_ship_roster.main(args))
    _fetch("outfitting modules", lambda: gen_module_taxonomy.main(args))
    if fetch:
        _fetch("ship specs (coriolis)", gen_ship_specs.main)
        _fetch("engineering data", _regen_engineering)
    else:
        print("\n--- ship specs / engineering: skipped (need network; use without --no-fetch) ---")

    after = _snapshot()
    _print_diff(before, after)
    _print_nag()


def _regen_engineering() -> None:
    from covas.ed.data import regen_engineering_data
    regen_engineering_data.main()


# ---- reporting -----------------------------------------------------------------------------

def _line(label: str, added: set[str], removed: set[str]) -> None:
    if not added and not removed:
        print(f"  {label}: no change")
        return
    if added:
        print(f"  {label}: +{len(added)} new -> {', '.join(sorted(added))}")
    if removed:
        print(f"  {label}: -{len(removed)} gone -> {', '.join(sorted(removed))}")


def _print_diff(before: dict, after: dict) -> None:
    print("\n=== dataset diff summary ===")
    _line("new hulls", after["hulls"] - before["hulls"], before["hulls"] - after["hulls"])
    _line("hull specs", after["specs"] - before["specs"], before["specs"] - after["specs"])
    _line("modules", after["modules"] - before["modules"], before["modules"] - after["modules"])
    _line("blueprints", after["blueprints"] - before["blueprints"],
          before["blueprints"] - after["blueprints"])
    orphans = _orphaned_overlay()
    if orphans:
        print(f"  ORPHANED overlay rows (alias ids with no roster hull): {', '.join(orphans)} "
              "— fix _ALIASES or the roster harvest.")
    # A hull in the roster but not the spec table = resolved-but-no-spec (honest gap, not a bug).
    no_spec = after["hulls"] - after["specs"]
    if no_spec:
        print(f"  hulls with no bundled spec (say-so + web-search path): {', '.join(sorted(no_spec))}")


def _print_nag() -> None:
    print("\n=== hand-curated tables (refresh these MANUALLY when they drift) ===")
    for rel in ("covas/ed/engineers.py", "covas/ed/odyssey_engineering.py"):
        text = (_ROOT / rel).read_text(encoding="utf-8")
        m = _LAST_REFRESHED_RE.search(text)
        print(f"  {rel}: last refreshed {m.group(1) if m else 'UNKNOWN'}")
    print("\n=== manifest ===")
    for d in dataset_manifest.load().items():
        name, info = d
        print(f"  {name}: {info['row_count']} rows, generated {info['generated_at']}")
    print("\nReview the diff, run `pytest`, then commit.")


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    run(fetch="--no-fetch" not in argv)


if __name__ == "__main__":
    main()
