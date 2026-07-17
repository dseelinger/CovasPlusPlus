"""Generate `covas/nav/module_data.py` from EDCD's FDevIDs outfitting.csv.

Why: `covas/nav/modules.py` used to hand-curate a *subset* of modules, so real ones the
Commander could ask for (e.g. "Advanced Docking Computer") resolved to Unknown. This bakes
the COMPLETE, canonical module set instead — every purchasable module, with its valid
sizes / mounts / ratings — into an offline table. No runtime fetch: the table is generated
here and committed.

Source of truth: EDCD/FDevIDs `outfitting.csv` (the same IDs Spansh/EDDN use), committed at
`tests/fixtures/fdevids_outfitting.csv`. Its `name` column is the EXACT string Spansh's
station-search module filter expects — every previously hand-verified name matched it
verbatim, so we trust `name` as the Spansh filter key.

Grouping: one taxonomy row per distinct `name`, aggregating the per-variant CSV rows:
  * category   hardpoint→weapon, else standard/internal/utility (drives size wording + mount)
  * sizes      sorted distinct `class` (utilities are class 0 → size-less: sizes=())
  * mounts     weapons only: Fixed / Gimballed→Gimbal / Turreted→Turret (Spansh short forms)
  * ratings    distinct rating letters, informational only (Spansh filters on name+class)

Two stages (issue #101), so runtime stays offline while the snapshot keeps up with FDev:
  * FETCH   — `--fetch`: download EDCD/FDevIDs outfitting.csv and overwrite the committed
              fixture. Network; FAIL-LOUD on error (a bad fetch must not silently ship stale
              data), matching `regen_engineering_data.py`.
  * GENERATE — default: a PURE function of the committed CSV, so regen is deterministic/offline.

Run from the repo root:  python scripts/gen_module_taxonomy.py            # generate
                         python scripts/gen_module_taxonomy.py --fetch    # + refresh the CSV
Then run the tests — `tests/test_nav_modules.py` guards resolve()'s behaviour and
`tests/test_nav_module_completeness.py` guards that every CSV symbol stays represented.
"""
from __future__ import annotations

import csv
import re
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
from scripts import dataset_manifest  # noqa: E402 — after sys.path so the repo package imports

_CSV = _ROOT / "tests" / "fixtures" / "fdevids_outfitting.csv"
_OUT = _ROOT / "covas" / "nav" / "module_data.py"
_URL = "https://raw.githubusercontent.com/EDCD/FDevIDs/master/outfitting.csv"

# EDCD category → ModuleSpec.category. "hardpoint" is what the game calls weapons.
_CATEGORY = {"hardpoint": "weapon", "standard": "standard",
             "internal": "internal", "utility": "utility"}
# EDCD mount word → Spansh weapon_mode short form (matches covas.nav.modules._MOUNTS).
_MOUNT = {"Fixed": "Fixed", "Gimballed": "Gimbal", "Turreted": "Turret"}
_MOUNT_ORDER = ("Fixed", "Gimbal", "Turret")

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _slug(name: str) -> str:
    """Stable id from a module name: 'Advanced Docking Computer' → 'advanced_docking_computer'.
    Names are unique in the CSV, so slugs are unique (asserted below)."""
    return _NON_ALNUM.sub("_", name.lower()).strip("_")


def _aggregate(rows: list[dict]) -> dict[str, dict]:
    by_name: dict[str, dict] = defaultdict(
        lambda: {"category": set(), "sizes": set(), "mounts": set(), "ratings": set()})
    for r in rows:
        g = by_name[r["name"]]
        g["category"].add(_CATEGORY.get(r["category"], r["category"]))
        cls = int(r["class"])
        if cls > 0:                       # class 0 = utilities (size-less)
            g["sizes"].add(cls)
        mount = _MOUNT.get(r["mount"])    # ignores '' and the lone 'mount' CSV glitch row
        if mount:
            g["mounts"].add(mount)
        if r["rating"]:
            g["ratings"].add(r["rating"])
    return by_name


def _rows_out(by_name: dict[str, dict]) -> list[tuple]:
    out: list[tuple] = []
    seen_ids: set[str] = set()
    for name in sorted(by_name):
        g = by_name[name]
        assert len(g["category"]) == 1, f"{name!r} spans categories {g['category']}"
        category = next(iter(g["category"]))
        sizes = tuple(sorted(g["sizes"]))
        # Mounts only meaningful for weapons; keep Spansh's canonical order.
        mounts = tuple(m for m in _MOUNT_ORDER if m in g["mounts"]) if category == "weapon" else ()
        ratings = "".join(sorted(g["ratings"]))
        sid = _slug(name)
        assert sid and sid not in seen_ids, f"duplicate/empty slug for {name!r}: {sid!r}"
        seen_ids.add(sid)
        out.append((sid, name, category, sizes, mounts, ratings))
    return out


def _render(rows: list[tuple]) -> str:
    lines = [
        '"""GENERATED — do not edit by hand.',
        "",
        "The complete Elite Dangerous outfitting module table, baked from EDCD/FDevIDs",
        "outfitting.csv (`tests/fixtures/fdevids_outfitting.csv`). Regenerate with:",
        "",
        "    python scripts/gen_module_taxonomy.py",
        "",
        "Each row: (id, name, category, sizes, mounts, ratings). `name` is the EXACT Spansh",
        "station-search module-filter string. `covas/nav/modules.py` turns these into",
        "ModuleSpec objects (adding friendly mishear aliases) and resolves against them.",
        '"""',
        "from __future__ import annotations",
        "",
        f"# {len(rows)} modules across weapon / standard / internal / utility.",
        "MODULE_ROWS: tuple[tuple, ...] = (",
    ]
    for sid, name, category, sizes, mounts, ratings in rows:
        lines.append(f"    ({sid!r}, {name!r}, {category!r}, {sizes!r}, {mounts!r}, {ratings!r}),")
    lines.append(")")
    return "\n".join(lines) + "\n"


def _fetch_csv() -> None:
    """FETCH stage — refresh the committed outfitting.csv from EDCD/FDevIDs. FAIL-LOUD: a fetch
    error raises rather than shipping a stale/partial table (the `regen_engineering_data.py`
    contract). The CSV stays committed so the GENERATE stage and tests run offline."""
    with urllib.request.urlopen(_URL, timeout=30) as r:  # noqa: S310 — pinned trusted host
        _CSV.write_bytes(r.read())
    print(f"fetched outfitting.csv -> {_CSV.relative_to(_ROOT)}")


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if "--fetch" in argv:
        _fetch_csv()
    with _CSV.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    out_rows = _rows_out(_aggregate(rows))
    _OUT.write_text(_render(out_rows), encoding="utf-8")
    dataset_manifest.update("module_taxonomy", source="EDCD/FDevIDs outfitting.csv",
                            source_ref="tests/fixtures/fdevids_outfitting.csv", row_count=len(out_rows))
    print(f"wrote {len(out_rows)} modules to {_OUT.relative_to(_ROOT)}")


if __name__ == "__main__":
    main()
