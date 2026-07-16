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
import sys
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
from covas.nav.ships import ROSTER  # noqa: E402 — after sys.path so the repo package imports

_BASE = "https://raw.githubusercontent.com/EDCD/coriolis-data/master/ships/"
_OUT = _ROOT / "covas" / "nav" / "ship_spec_data.py"

# The canonical (Spansh) ship name per id, so the bundled spec's `name` is the SAME string the
# resolver/roster use everywhere else (coriolis spells a few differently — e.g. its "Viper" is
# the Viper MkIII). One canonical name across the app, no dual-naming.
_CANON_NAME = {s.id: s.name for s in ROSTER}

# coriolis-data filename (no .json) -> the canonical ship id in covas/nav/ships.py. Keying to
# the SAME slug the resolver returns is what lets `ship_spec` look a resolved hull straight up.
# `lynx` (Lynx Highliner) has no coriolis entry, so it simply carries no bundled spec.
_FILE_TO_ID: dict[str, str] = {
    "sidewinder": "sidewinder", "eagle": "eagle", "hauler": "hauler", "adder": "adder",
    "imperial_eagle": "imperial_eagle", "viper": "viper_mk3", "cobra_mk_iii": "cobra_mk3",
    "viper_mk_iv": "viper_mk4", "diamondback_scout": "diamondback_scout",
    "cobra_mk_iv": "cobra_mk4", "cobra_mk_v": "cobra_mk5", "type_6_transporter": "type_6",
    "dolphin": "dolphin", "diamondback_explorer": "diamondback_explorer",
    "imperial_courier": "imperial_courier", "keelback": "keelback", "asp_scout": "asp_scout",
    "asp": "asp_explorer", "vulture": "vulture", "federal_dropship": "federal_dropship",
    "imperial_clipper": "imperial_clipper", "federal_assault_ship": "federal_assault_ship",
    "type_7_transport": "type_7", "type_8_transport": "type_8",
    "federal_gunship": "federal_gunship", "krait_mkii": "krait_mk2",
    "krait_phantom": "krait_phantom", "orca": "orca", "mamba": "mamba",
    "fer_de_lance": "fer_de_lance", "python": "python", "python_nx": "python_mk2",
    "mandalay": "mandalay", "imperial_corsair": "corsair", "type_9_heavy": "type_9",
    "type_10_defender": "type_10", "type_11_prospector": "type_11", "beluga": "beluga",
    "alliance_chieftain": "alliance_chieftain", "alliance_crusader": "alliance_crusader",
    "alliance_challenger": "alliance_challenger", "federal_corvette": "federal_corvette",
    "imperial_cutter": "imperial_cutter", "anaconda": "anaconda",
    "panther_clipper": "panther_clipper", "kestrel": "kestrel", "explorer_nx": "caspian",
}

# The coriolis `slots.standard` array order (from coriolis Ship.js). Baked here so the row
# stays a flat tuple while the reader still knows which size is which core module.
_CORE_ORDER = ("power_plant", "thrusters", "frame_shift_drive", "life_support",
               "power_distributor", "sensors", "fuel_tank")


def _fetch(name: str) -> dict:
    with urllib.request.urlopen(_BASE + name + ".json", timeout=60) as r:  # noqa: S310 (trusted host)
        return json.load(r)


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
    rows: list[dict] = []
    for fname, ship_id in _FILE_TO_ID.items():
        rows.append(_row(ship_id, _fetch(fname)))
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
    print(f"wrote {len(rows)} ships -> {_OUT}")
    # spot-check a few well-known figures so a bad refresh is caught immediately.
    by_id = {r["id"]: r for r in rows}
    for sid, mc in (("type_9", 790), ("type_6", 114), ("hauler", 26)):
        got = by_id[sid]["max_cargo"]
        print(f"  {sid}: max_cargo={got} (expected {mc}) {'OK' if got == mc else 'CHECK'}")


if __name__ == "__main__":
    main()
