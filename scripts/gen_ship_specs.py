"""Regenerate `covas/nav/ship_spec_data.py` from the EDCD/coriolis-data ship JSON.

The bundled ship-specification table (issue #83) is baked from the maintained community
dataset at https://github.com/EDCD/coriolis-data (`ships/*.json`) — the same lineage
Coriolis/EDSY use, and the one place newer hulls (Panther Clipper Mk II, Python Mk II,
Type-8, Mandalay, Cobra Mk V, Corsair, …) get real, non-hallucinated numbers. Spansh has
NO ship-reference endpoint (see covas/nav/ship_index.py), so this community dataset is the
authoritative offline source.

Run to refresh after Frontier ships a new hull (needs network; stdlib only):

    C:/dev/COVAS++/.venv/Scripts/python.exe scripts/gen_ship_specs.py

Every value written is either a raw field from the source JSON or a deterministic derivation
of one (fuel/cargo tonnage = 2**slot-size; utility mounts = the zero entries coriolis stores
in the hardpoint array). Nothing is invented — that is the whole point of bundling it, so the
`ship_spec` tool answers from ground truth instead of the model's training cutoff.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
from covas.nav.ships import ROSTER  # noqa: E402 — after sys.path so the repo package imports
from scripts import dataset_manifest  # noqa: E402

_BASE = "https://raw.githubusercontent.com/EDCD/coriolis-data/master/ships/"
# GitHub API dir listing — the authoritative, self-updating set of coriolis ship files, so a
# brand-new hull's JSON is picked up without a hand-maintained file list.
_INDEX = "https://api.github.com/repos/EDCD/coriolis-data/contents/ships"
_OUT = _ROOT / "covas" / "nav" / "ship_spec_data.py"

# The canonical (Spansh) ship name per id, so the bundled spec's `name` is the SAME string the
# resolver/roster use everywhere else (coriolis spells a few differently — e.g. its "Viper" is
# the Viper MkIII). One canonical name across the app, no dual-naming.
_CANON_NAME = {s.id: s.name for s in ROSTER}

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _norm(text: str) -> str:
    """Fold a name to a comparison key: 'Cobra Mk III' / 'Cobra MkIII' -> 'cobramkiii'."""
    return _NON_ALNUM.sub("", str(text).lower())


# Auto-match: a coriolis ship JSON's `properties.name` normalizes to the SAME key as the
# roster's canonical name for almost every hull (Anaconda, Cobra MkIII, Type-6 Transporter, …),
# so the file->id map is DERIVED, not hand-typed (issue #101 killed the old `_FILE_TO_ID`). Only
# the genuinely irregular coriolis spellings need an explicit override, keyed by the coriolis
# name's normalized form. Everything else is matched by name; an unmatched file FAILS LOUD (the
# new-ship detection signal).
_NAME_EXCEPTIONS: dict[str, str] = {
    "viper": "viper_mk3",      # coriolis "Viper" is the Viper MkIII
    "asp": "asp_explorer",     # coriolis "Asp" is the Asp Explorer
}

# The coriolis `slots.standard` array order (from coriolis Ship.js). Baked here so the row
# stays a flat tuple while the reader still knows which size is which core module.
_CORE_ORDER = ("power_plant", "thrusters", "frame_shift_drive", "life_support",
               "power_distributor", "sensors", "fuel_tank")


def _fetch(name: str) -> dict:
    with urllib.request.urlopen(_BASE + name + ".json", timeout=60) as r:  # noqa: S310 (trusted host)
        return json.load(r)


def _list_ship_files() -> list[str]:
    """Every coriolis ship filename (no .json), from the repo's dir listing — so a hull added to
    coriolis-data is discovered with no hand-maintained file list. FAIL-LOUD on a fetch error
    (network required, like the whole script), matching `regen_engineering_data.py`."""
    req = urllib.request.Request(_INDEX, headers={"Accept": "application/vnd.github+json",
                                                  "User-Agent": "covas-gen-ship-specs"})
    with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310 — pinned trusted host
        entries = json.load(r)
    return sorted(e["name"][:-5] for e in entries
                  if isinstance(e, dict) and str(e.get("name", "")).endswith(".json"))


def _coriolis_name(data: dict) -> str:
    """The ship's display name from a coriolis ship JSON ('Cobra Mk III')."""
    key = next(iter(data))
    return str(data[key]["properties"]["name"])


def match_id(coriolis_name: str, name_index: dict[str, str]) -> str | None:
    """The canonical roster id for a coriolis ship name, or None if unmatched. `name_index` is
    `{normalized canonical name: id}`. Irregular coriolis spellings resolve via `_NAME_EXCEPTIONS`;
    everything else matches on the normalized name. A None here is a NEW-HULL signal (fail loud)."""
    n = _norm(coriolis_name)
    if n in _NAME_EXCEPTIONS:
        return _NAME_EXCEPTIONS[n]
    return name_index.get(n)


def _optional(internal: list) -> list[tuple[int, str]]:
    """(size, kind) per optional-internal slot. `kind`: '' normal, 'military' (no cargo),
    'cargo' (cargo/fuel only). The PlanetaryApproachSuite pseudo-slot is dropped — it isn't an
    optional internal a Commander outfits."""
    out: list[tuple[int, str]] = []
    for slot in internal:
        if isinstance(slot, int):
            out.append((slot, ""))
        elif isinstance(slot, dict):
            nm = str(slot.get("name") or "")
            if nm == "PlanetaryApproachSuite":
                continue
            kind = "military" if nm == "Military" else "cargo" if nm == "Cargo" else nm.lower()
            out.append((int(slot.get("class", 0)), kind))
    return out


def _row(ship_id: str, data: dict) -> dict:
    key = next(iter(data))
    s = data[key]
    p = s["properties"]
    slots = s["slots"]
    # coriolis packs utility mounts as the zero entries in the hardpoint array; non-zero
    # entries are weapon hardpoint sizes (1=S 2=M 3=L 4=H). A few hulls (e.g. Type-11) store a
    # class-restricted hardpoint as a dict {class, name} — still a hardpoint of that size.
    hard = [int(x if isinstance(x, int) else x.get("class", 0)) for x in slots["hardpoints"]]
    core = tuple(int(x) for x in slots["standard"])          # 7 sizes, _CORE_ORDER
    optional = _optional(slots["internal"])
    weapons = tuple(sorted((x for x in hard if x > 0), reverse=True))
    utilities = sum(1 for x in hard if x == 0)
    fuel_t = 2 ** core[6]                                     # main-tank tonnage
    max_cargo = sum(2 ** sz for sz, kind in optional if kind in ("", "cargo"))
    return {
        "id": ship_id,
        "name": _CANON_NAME.get(ship_id, str(p["name"])),   # canonical roster name, not coriolis's
        "manufacturer": str(p.get("manufacturer") or ""),
        "pad_size": int(p.get("class") or 0),                # 1=S 2=M 3=L
        "hull_mass": float(p.get("hullMass") or 0),
        "fuel_capacity": int(fuel_t),
        "max_cargo": int(max_cargo),
        "crew": int(p.get("crew") or 1),
        "top_speed": int(p.get("speed") or 0),
        "boost_speed": int(p.get("boost") or 0),
        "base_shield": int(p.get("baseShieldStrength") or 0),
        "base_armour": int(p.get("baseArmour") or 0),
        "masslock": int(p.get("masslock") or 0),
        "core": core,
        "hardpoints": weapons,
        "utilities": int(utilities),
        "optional": tuple(optional),
    }


def main() -> None:
    name_index = {_norm(s.name): s.id for s in ROSTER}
    rows: list[dict] = []
    unmatched: list[str] = []
    for fname in _list_ship_files():
        data = _fetch(fname)
        cname = _coriolis_name(data)
        ship_id = match_id(cname, name_index)
        if ship_id is None:
            unmatched.append(f"{fname}.json ({cname!r})")
            continue
        rows.append(_row(ship_id, data))
    if unmatched:
        # This loud failure IS the new-ship detector: a coriolis hull with no roster id means
        # Frontier shipped a ship the roster hasn't harvested yet — run gen_ship_roster.py first.
        raise SystemExit(
            "Unmatched coriolis ship file(s) — new FDev hull(s)? Regenerate the roster "
            "(scripts/gen_ship_roster.py --fetch) so these get a canonical id, or add a "
            f"_NAME_EXCEPTIONS override:\n  " + "\n  ".join(unmatched))
    rows.sort(key=lambda r: r["id"])

    lines = [
        '"""GENERATED — do not edit by hand.',
        "",
        "Elite Dangerous ship-specification table (issue #83), baked from the maintained",
        "EDCD/coriolis-data ship JSON (https://github.com/EDCD/coriolis-data, ships/*.json) —",
        "the same lineage Coriolis/EDSY use, and where newer hulls get real numbers instead of",
        "the model's training-cutoff guesses. Regenerate (needs network) with:",
        "",
        "    C:/dev/COVAS++/.venv/Scripts/python.exe scripts/gen_ship_specs.py",
        "",
        "Keyed by the canonical ship id in `covas/nav/ships.py`, so a resolved hull looks its",
        "spec straight up. `covas/nav/ship_specs.py` wraps these dicts and does the lookup.",
        "",
        "Fields: manufacturer; pad_size (1=S 2=M 3=L); hull_mass (t); fuel_capacity (t, main",
        "tank); max_cargo (t, every cargo-capable optional slot as a cargo rack); crew seats;",
        "top_speed / boost_speed (m/s); base_shield / base_armour; masslock factor; core (7",
        "internal sizes: power plant, thrusters, FSD, life support, power distributor, sensors,",
        "fuel tank); hardpoints (weapon mount sizes, 1=S 2=M 3=L 4=H); utilities (utility mount",
        "count); optional ((size, kind) per optional internal — kind '' normal / 'military' /",
        "'cargo'). Jump range is deliberately absent: it is fit-dependent, not a hull constant —",
        "the tool defers to the live loadout / web search for it.",
        '"""',
        "from __future__ import annotations",
        "",
        f"# {len(rows)} hulls. CORE_ORDER = power plant, thrusters, FSD, life support, power",
        "# distributor, sensors, fuel tank.",
        "SHIP_SPECS: dict[str, dict] = {",
    ]
    for r in rows:
        opt = ", ".join(f"({sz}, {kind!r})" for sz, kind in r["optional"])
        lines.append(f"    {r['id']!r}: {{")
        lines.append(f"        'name': {r['name']!r}, 'manufacturer': {r['manufacturer']!r},")
        lines.append(f"        'pad_size': {r['pad_size']}, 'hull_mass': {r['hull_mass']!r}, "
                     f"'fuel_capacity': {r['fuel_capacity']}, 'max_cargo': {r['max_cargo']},")
        lines.append(f"        'crew': {r['crew']}, 'top_speed': {r['top_speed']}, "
                     f"'boost_speed': {r['boost_speed']}, 'base_shield': {r['base_shield']}, "
                     f"'base_armour': {r['base_armour']}, 'masslock': {r['masslock']},")
        lines.append(f"        'core': {r['core']!r},")
        lines.append(f"        'hardpoints': {r['hardpoints']!r}, 'utilities': {r['utilities']},")
        lines.append(f"        'optional': ({opt}{',' if len(r['optional']) == 1 else ''}),")
        lines.append("    },")
    lines.append("}")
    _OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    dataset_manifest.update("ship_specs", source="EDCD/coriolis-data ships/*.json",
                            source_ref="github.com/EDCD/coriolis-data@master", row_count=len(rows))
    print(f"wrote {len(rows)} ships -> {_OUT}")
    # spot-check a few well-known figures so a bad refresh is caught immediately.
    by_id = {r["id"]: r for r in rows}
    for sid, mc in (("type_9", 790), ("type_6", 114), ("hauler", 26)):
        got = by_id[sid]["max_cargo"]
        print(f"  {sid}: max_cargo={got} (expected {mc}) {'OK' if got == mc else 'CHECK'}")


if __name__ == "__main__":
    main()
