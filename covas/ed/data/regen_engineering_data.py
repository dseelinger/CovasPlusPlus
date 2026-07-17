"""Regenerate the bundled engineering reference tables (issue #66). DEV TOOL — not
imported at runtime.

Two static, offline tables ship next to this script and power the blueprint / material
sourcing capability (`covas/capabilities/blueprint_capability.py`):

  * `materials.json`  — every engineering material keyed by its JOURNAL name (the lower-case
                        `Name` the `Materials` event uses), with display name, category
                        (Raw / Manufactured / Encoded), grade (1-5), trader group, and a
                        sourcing hint.
  * `blueprints.json` — every engineering blueprint keyed by its FDev symbol, with its short
                        name, module aliases, and the per-grade material recipe (each entry a
                        `{"m": <journal name>, "n": <count>}` pair).

Both are DERIVED, never hand-authored, so they stay faithful to the game and can be
refreshed when Frontier changes a recipe. Sources (public, community-maintained, the same
data Coriolis/EDEngineer use):

  * Recipes   — EDCD/coriolis-data  `modifications/blueprints.json`
  * Materials — EDCD/FDevIDs        `material.csv`

To regenerate (needs network; run from anywhere with the repo's Python):

    C:/dev/COVAS++/.venv/Scripts/python.exe -m covas.ed.data.regen_engineering_data

It rewrites the two JSON files in place and prints a summary. Runtime code NEVER calls this —
it only reads the committed JSON (see `covas/ed/blueprints.py`), so the app stays fully
offline. Fail loud: an unresolved component name aborts rather than silently dropping a mat.
"""
from __future__ import annotations

import csv
import io
import json
import urllib.request
from pathlib import Path

_BLUEPRINTS_URL = ("https://raw.githubusercontent.com/EDCD/coriolis-data/master/"
                   "modifications/blueprints.json")
_MATERIALS_URL = "https://raw.githubusercontent.com/EDCD/FDevIDs/master/material.csv"

_HERE = Path(__file__).resolve().parent

# Material trader per category (the trader swaps materials WITHIN a group — always-valid advice).
_TRADER = {
    "Raw": "Raw Material Trader",
    "Manufactured": "Manufactured Material Trader",
    "Encoded": "Encoded Material Trader",
}
# Evergreen farm hints per category — deliberately community-stable landmarks + generic methods,
# NOT volatile "best spot this patch" claims, so the bundled advice doesn't rot.
_FARM = {
    "Raw": "surface-prospect metallic/rocky bodies (crystalline-shard sites yield grade 4-5)",
    "Manufactured": ("salvage High-Grade Emission signal sources and combat/mission rewards "
                     "(Dav's Hope in Hyades Sector DR-V c2-23 is a reliable hand-farm)"),
    "Encoded": ("scan ship wakes, data points and megaships (the Jameson crash site at "
                "HIP 12099 is a classic data farm)"),
}


def _fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 — pinned trusted host
        return resp.read()


def _build_materials(csv_bytes: bytes) -> tuple[dict, dict]:
    """materials.json content, plus a display-name -> journal-name index for recipe mapping."""
    materials: dict[str, dict] = {}
    by_display: dict[str, str] = {}
    reader = csv.DictReader(io.StringIO(csv_bytes.decode("utf-8")))
    for row in reader:
        symbol = row["symbol"].strip().lower()   # == the journal `Materials` event Name
        category = row["type"].strip()           # Raw / Manufactured / Encoded
        group = row["category"].strip()          # trader sub-group (name for Man/Enc; grid # for Raw)
        display = row["name"].strip()
        trader = _TRADER.get(category, "a Material Trader")
        farm = _FARM.get(category, "a Material Trader")
        # Raw's `group` is a numeric grid cell, not a spoken name — phrase it generically.
        swap = "swaps within its raw grid group" if category == "Raw" \
            else f"swaps within the {group} group"
        article = "an" if trader[:1].upper() in "AEIOU" else "a"
        source = f"Trade at {article} {trader} ({swap}), or {farm}."
        materials[symbol] = {
            "name": display,
            "category": category,
            "grade": int(row["rarity"]),
            "group": group,
            "source": source,
        }
        by_display[display.lower()] = symbol
    return materials, by_display


def _build_blueprints(bp_bytes: bytes, by_display: dict[str, str]) -> dict:
    raw = json.loads(bp_bytes.decode("utf-8"))
    unresolved: set[str] = set()
    out: dict[str, dict] = {}
    for key, b in raw.items():
        grades: dict[str, list] = {}
        for grade, gd in b.get("grades", {}).items():
            recipe = []
            for cname, count in (gd.get("components") or {}).items():
                sym = by_display.get(cname.strip().lower())
                if sym is None:
                    unresolved.add(cname)
                    continue
                recipe.append({"m": sym, "n": int(count)})
            if recipe:
                grades[grade] = recipe
        if not grades:
            continue  # experimental-only / no-material blueprints carry nothing to source
        module = b.get("modulename")
        aliases = [str(m).strip() for m in module] if isinstance(module, list) \
            else [str(module).strip()] if module else []
        out[key] = {
            "name": str(b.get("name") or key).strip(),
            "module": aliases[0] if aliases else "",
            "aliases": aliases,
            "grades": grades,
        }
    if unresolved:
        raise SystemExit(f"Unresolved component names (fix the material map): {sorted(unresolved)}")
    return out


def main() -> None:
    materials, by_display = _build_materials(_fetch(_MATERIALS_URL))
    blueprints = _build_blueprints(_fetch(_BLUEPRINTS_URL), by_display)

    (_HERE / "materials.json").write_text(
        json.dumps(materials, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8")
    (_HERE / "blueprints.json").write_text(
        json.dumps(blueprints, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8")
    # Record provenance in the shared dataset manifest (issue #101) so `check_setup.py` / the
    # `game_data_status` capability can report how fresh the engineering tables are. Best-effort:
    # a manifest write must never fail the regen (the JSON is already written above).
    try:
        from scripts import dataset_manifest
        dataset_manifest.update("engineering_materials", source="EDCD/FDevIDs material.csv",
                                source_ref="github.com/EDCD/FDevIDs@master", row_count=len(materials))
        dataset_manifest.update("engineering_blueprints",
                                source="EDCD/coriolis-data modifications/blueprints.json",
                                source_ref="github.com/EDCD/coriolis-data@master",
                                row_count=len(blueprints))
    except Exception as e:  # noqa: BLE001 — manifest is a nicety; never block the regen on it
        print(f"  [warn] manifest not updated: {e}")
    print(f"Wrote {len(materials)} materials and {len(blueprints)} blueprints.")


if __name__ == "__main__":
    main()
