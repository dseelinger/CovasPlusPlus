"""Generate the ship-roster BASE (`covas/nav/data/ship_roster.json`) from a Spansh harvest.

Issue #101: the roster used to be a hand-authored `ROSTER` tuple in `covas/nav/ships.py`.
It is now two layers merged at import:

  * a GENERATED base — `{id, name, ed_symbol}` per hull, written here from a committed Spansh
    shipyard-name+symbol harvest (`tests/fixtures/spansh_ship_harvest.json`). Spansh's shipyard
    `ships` arrays carry both the exact case-sensitive filter `name` and the FDev `symbol`
    (verified live — see `tests/fixtures/spansh_stations_ship_anaconda.json`), so this is the
    authoritative offline source for names + symbols.
  * a CURATED overlay that stays hand-maintained in `ships.py` because it is genuinely
    editorial: aliases (mishears, "fdl"), `_FAMILIES` disambiguation, `_COMMON` starter list.

Two stages (like `refresh_datasets.py`'s contract):
  * FETCH   — `--fetch`: harvest live Spansh, overwrite the committed fixture. FAIL-SOFT (a
              Spansh outage keeps the stale snapshot), so a release never blocks on Spansh.
  * GENERATE — default: a PURE function of the committed fixture, so regen is deterministic and
              testable offline.

Stable ids: the existing hulls keep their editorial slug (the whole app — `ship_spec_data.py`
keys, `resolve_ship().id` — depends on them) via `_STABLE_IDS`. A BRAND-NEW hull the harvest
turns up gets a mechanical id from its ed_symbol with ZERO hand edits, so a new ship flows all
the way into the roster (and its spec, via `gen_ship_specs.py`) on one refresh.

    C:/dev/COVAS++/.venv/Scripts/python.exe scripts/gen_ship_roster.py            # generate
    C:/dev/COVAS++/.venv/Scripts/python.exe scripts/gen_ship_roster.py --fetch    # + refresh snapshot
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
from scripts import dataset_manifest  # noqa: E402 — after sys.path so the repo package imports

_HARVEST = _ROOT / "tests" / "fixtures" / "spansh_ship_harvest.json"
_OUT = _ROOT / "covas" / "nav" / "data" / "ship_roster.json"

_SOURCE = "Spansh shipyard harvest (name + FDev ed_symbol)"
_SOURCE_REF = "tests/fixtures/spansh_ship_harvest.json"

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _slug(text: str) -> str:
    """Mechanical id for a brand-new ed_symbol: 'CobraMkVI' -> 'cobramkvi'. Only used for hulls
    not in `_STABLE_IDS` — i.e. genuinely new ships — so existing ids never shift."""
    return _NON_ALNUM.sub("_", str(text).lower()).strip("_")


# The stable id per FDev ed_symbol for every hull the roster already knew (issue #83 lineage).
# These slugs are editorial (Empire_Eagle -> imperial_eagle, TypeX -> alliance_chieftain) and
# can't be mechanically derived, so they are pinned here — NOT re-invented per regen. A symbol
# absent from this map is a new FDev ship; it gets `_slug(symbol)` automatically.
_STABLE_IDS: dict[str, str] = {
    "SideWinder": "sidewinder", "Eagle": "eagle", "Hauler": "hauler", "Adder": "adder",
    "Empire_Eagle": "imperial_eagle", "Viper": "viper_mk3", "CobraMkIII": "cobra_mk3",
    "Viper_MkIV": "viper_mk4", "DiamondBack": "diamondback_scout", "CobraMkIV": "cobra_mk4",
    "CobraMkV": "cobra_mk5", "Type6": "type_6", "Dolphin": "dolphin",
    "DiamondBackXL": "diamondback_explorer", "Empire_Courier": "imperial_courier",
    "Independant_Trader": "keelback", "Asp_Scout": "asp_scout", "Asp": "asp_explorer",
    "Vulture": "vulture", "Federation_Dropship": "federal_dropship",
    "Empire_Trader": "imperial_clipper", "Federation_Dropship_MkII": "federal_assault_ship",
    "Type7": "type_7", "Type8": "type_8", "Federation_Gunship": "federal_gunship",
    "Krait_MkII": "krait_mk2", "Krait_Light": "krait_phantom", "Orca": "orca", "Mamba": "mamba",
    "FerDeLance": "fer_de_lance", "Python": "python", "Python_NX": "python_mk2",
    "Mandalay": "mandalay", "Corsair": "corsair", "Type9": "type_9",
    "Type9_Military": "type_10", "LakonMiner": "type_11", "BelugaLiner": "beluga",
    "TypeX": "alliance_chieftain", "TypeX_2": "alliance_crusader", "TypeX_3": "alliance_challenger",
    "Federation_Corvette": "federal_corvette", "Cutter": "imperial_cutter", "Anaconda": "anaconda",
    "PantherMkII": "panther_clipper", "SmallCombat01_NX": "kestrel", "Explorer_NX": "caspian",
    "MediumTransport01": "lynx",
}


def ship_id_for(symbol: str, name: str) -> str:
    """The stable canonical id for a hull. Pinned for known symbols; a mechanical slug of the
    ed_symbol (else the name) for a genuinely new one — the zero-hand-edit path for new FDev ships."""
    if symbol in _STABLE_IDS:
        return _STABLE_IDS[symbol]
    return _slug(symbol) or _slug(name)


def build_rows(harvest: list[dict]) -> list[dict]:
    """PURE: harvest `[{name, symbol}, …]` -> roster base `[{id, name, ed_symbol}, …]`, input
    order preserved. Raises on a duplicate id/name so a bad harvest fails loudly at regen time."""
    rows: list[dict] = []
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    for entry in harvest:
        name = str(entry["name"]).strip()
        symbol = str(entry.get("symbol") or "").strip()
        sid = ship_id_for(symbol, name)
        if sid in seen_ids:
            raise SystemExit(f"duplicate ship id {sid!r} (symbol {symbol!r}) — fix _STABLE_IDS")
        if name in seen_names:
            raise SystemExit(f"duplicate ship name {name!r} in harvest")
        seen_ids.add(sid)
        seen_names.add(name)
        rows.append({"id": sid, "name": name, "ed_symbol": symbol})
    return rows


def fetch_harvest(*, timeout: float = 30.0) -> list[dict] | None:
    """FETCH stage — live Spansh shipyard harvest of `{name, symbol}` pairs. FAIL-SOFT: returns
    None on any error so the committed snapshot is kept (Spansh outages never block a release)."""
    try:
        import requests  # local import: keeps the offline stack importable without hitting it
        from covas.search.spansh import STATIONS_URL, _DEFAULT_UA, distance_sort
        body = {"filters": {"has_shipyard": {"value": True}}, "sort": distance_sort(),
                "size": 250, "page": 0, "reference_system": "Shinrarta Dezhra"}
        resp = requests.post(STATIONS_URL, json=body, timeout=timeout,
                             headers={"Content-Type": "application/json", "User-Agent": _DEFAULT_UA})
        resp.raise_for_status()
        data = resp.json()
        by_symbol: dict[str, str] = {}
        for station in (data.get("results") if isinstance(data, dict) else None) or []:
            for ship in station.get("ships") or []:
                name, symbol = ship.get("name"), ship.get("symbol")
                if name and symbol:
                    by_symbol.setdefault(symbol, name)
        if not by_symbol:
            return None
        return [{"name": n, "symbol": s} for s, n in sorted(by_symbol.items(), key=lambda kv: kv[1])]
    except Exception as e:  # noqa: BLE001 — fetch is best-effort; the committed snapshot stands in
        print(f"  [warn] Spansh ship harvest failed ({e}); keeping the committed snapshot.")
        return None


def load_harvest() -> list[dict]:
    return json.loads(_HARVEST.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if "--fetch" in argv:
        fresh = fetch_harvest()
        if fresh is not None:
            _HARVEST.write_text(json.dumps(fresh, indent=1, ensure_ascii=False) + "\n",
                                encoding="utf-8")
            print(f"fetched {len(fresh)} ships -> {_HARVEST.relative_to(_ROOT)}")

    rows = build_rows(load_harvest())
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    dataset_manifest.update("ship_roster", source=_SOURCE, source_ref=_SOURCE_REF,
                            row_count=len(rows))
    print(f"wrote {len(rows)} roster rows -> {_OUT.relative_to(_ROOT)}")


if __name__ == "__main__":
    main()
