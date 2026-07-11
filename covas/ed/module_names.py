"""Journal symbol → spoken name mapping for ship loadouts (N9). Pure + offline.

The `Loadout` event names everything by internal symbol: modules like
"int_hyperdrive_size5_class5" / "hpt_multicannon_gimbal_medium", blueprints like
"FSD_LongRange", experimentals like "special_fsd_heavy". This module turns those into what
a Commander would say aloud ("5A Frame Shift Drive", "medium gimballed Multi-Cannon",
"Increased Range", "Mass Manager").

Deliberately SEPARATE from `nav/modules.py`: that file is the Spansh outfitting taxonomy
(what stations sell, exact Spansh filter names); this one decodes the journal's ed-symbols.
The two symbol spaces overlap but don't match, and mixing them would muddy both.

Approach: decompose the symbol structurally (int_<kind>_size<N>_class<M>,
hpt_<kind>_<mount>_<size>, <hull>_armour_<grade>) over curated kind tables baked from the
EDCD/FDevIDs naming (2026-07), with a readable title-cased fallback for anything unknown —
never a raw symbol in speech, and never an invented stat. For experimental effects the
journal usually carries `ExperimentalEffect_Localised`; callers should prefer that (see
`experimental_name(symbol, localised=...)`) so the curated table is only a fallback.
"""
from __future__ import annotations

import re

from .loadout import LoadoutSnapshot, ShipModule

# ---- internal (int_) module kinds ----------------------------------------------------------
# Key = the symbol between "int_" and any _sizeN/_classN suffix, longest-match first.
# Values are display names; {} entries that differ from plain title-casing earn their row.

_INT_KINDS: dict[str, str] = {
    "hyperdrive_overcharge": "Frame Shift Drive (SCO)",
    "hyperdrive": "Frame Shift Drive",
    "engine": "Thrusters",
    "powerplant": "Power Plant",
    "powerdistributor": "Power Distributor",
    "lifesupport": "Life Support",
    "sensors": "Sensors",
    "fueltank": "Fuel Tank",
    "shieldgenerator": "Shield Generator",
    "shieldcellbank": "Shield Cell Bank",
    "cargorack": "Cargo Rack",
    "corrosionproofcargorack": "Corrosion Resistant Cargo Rack",
    "fuelscoop": "Fuel Scoop",
    "hullreinforcement": "Hull Reinforcement Package",
    "modulereinforcement": "Module Reinforcement Package",
    "guardianshieldreinforcement": "Guardian Shield Reinforcement",
    "guardianhullreinforcement": "Guardian Hull Reinforcement",
    "guardianmodulereinforcement": "Guardian Module Reinforcement",
    "guardianfsdbooster": "Guardian FSD Booster",
    "guardianpowerplant": "Guardian Hybrid Power Plant",
    "guardianpowerdistributor": "Guardian Hybrid Power Distributor",
    "dronecontrol_collection": "Collector Limpet Controller",
    "dronecontrol_prospector": "Prospector Limpet Controller",
    "dronecontrol_fueltransfer": "Fuel Transfer Limpet Controller",
    "dronecontrol_repair": "Repair Limpet Controller",
    "dronecontrol_resourcesiphon": "Hatch Breaker Limpet Controller",
    "dronecontrol_recon": "Recon Limpet Controller",
    "dronecontrol_decontamination": "Decontamination Limpet Controller",
    "dronecontrol_unkvesselresearch": "Research Limpet Controller",
    "multidronecontrol_mining": "Mining Multi Limpet Controller",
    "multidronecontrol_operations": "Operations Multi Limpet Controller",
    "multidronecontrol_rescue": "Rescue Multi Limpet Controller",
    "multidronecontrol_xeno": "Xeno Multi Limpet Controller",
    "multidronecontrol_universal": "Universal Multi Limpet Controller",
    "detailedsurfacescanner": "Detailed Surface Scanner",
    "dockingcomputer_advanced": "Advanced Docking Computer",
    "dockingcomputer_standard": "Standard Docking Computer",
    "supercruiseassist": "Supercruise Assist",
    "planetapproachsuite_advanced": "Advanced Planetary Approach Suite",
    "planetapproachsuite": "Planetary Approach Suite",
    "buggybay": "Planetary Vehicle Hangar",
    "fighterbay": "Fighter Hangar",
    "passengercabin": "Passenger Cabin",
    "refinery": "Refinery",
    "repairer": "Auto Field-Maintenance Unit",
    "fsdinterdictor": "Frame Shift Drive Interdictor",
    "fuelscoop_sco": "Fuel Scoop",
    "expmodulestabiliser": "Experimental Weapon Stabiliser",
    "metaalloyhullreinforcement": "Meta Alloy Hull Reinforcement",
    "stellarbodydiscoveryscanner": "Discovery Scanner",
}

# ---- hardpoint (hpt_) weapon / utility kinds ------------------------------------------------

_HPT_KINDS: dict[str, str] = {
    "multicannon": "Multi-Cannon",
    "atmulticannon": "AX Multi-Cannon",
    "atmulticannon_v2": "Enhanced AX Multi-Cannon",
    "pulselaser": "Pulse Laser",
    "pulselaserburst": "Burst Laser",
    "beamlaser": "Beam Laser",
    "cannon": "Cannon",
    "slugshot": "Fragment Cannon",
    "railgun": "Rail Gun",
    "plasmaaccelerator": "Plasma Accelerator",
    "basicmissilerack": "Seeker Missile Rack",
    "dumbfiremissilerack": "Missile Rack",
    "atdumbfiremissile": "AX Missile Rack",
    "atdumbfiremissile_v2": "Enhanced AX Missile Rack",
    "atventdisruptorpylon": "Torpedo Pylon",
    "torpedopylon": "Torpedo Pylon",
    "minelauncher": "Mine Launcher",
    "mining_abrblstr": "Abrasion Blaster",
    "mining_seismchrgwarhd": "Seismic Charge Launcher",
    "mining_subsurfdispmisle": "Sub-Surface Displacement Missile",
    "mininglaser": "Mining Laser",
    "flakmortar": "Remote Release Flak Launcher",
    "flechettelauncher": "Remote Release Flechette Launcher",
    "guardian_gausscannon": "Guardian Gauss Cannon",
    "guardian_plasmalauncher": "Guardian Plasma Charger",
    "guardian_shardcannon": "Guardian Shard Cannon",
    "shieldbooster": "Shield Booster",
    "chafflauncher": "Chaff Launcher",
    "heatsinklauncher": "Heat Sink Launcher",
    "causticsinklauncher": "Caustic Sink Launcher",
    "crimescanner": "Kill Warrant Scanner",
    "cloudscanner": "Frame Shift Wake Scanner",
    "cargoscanner": "Manifest Scanner",
    "plasmapointdefence": "Point Defence",
    "electroniccountermeasure": "Electronic Countermeasure",
    "xenoscanner": "Xeno Scanner",
    "mrascanner": "Pulse Wave Analyser",
    "shutdownfieldneutraliser": "Shutdown Field Neutraliser",
    "antiunknownshutdown": "Shutdown Field Neutraliser",
}

_MOUNTS = {"fixed": "fixed", "gimbal": "gimballed", "turret": "turreted"}
_WEAPON_SIZES = ("small", "medium", "large", "huge", "tiny")

# ---- armour grades --------------------------------------------------------------------------

_ARMOUR_GRADES = {
    "grade1": "Lightweight Alloy",
    "grade2": "Reinforced Alloy",
    "grade3": "Military Grade Composite",
    "mirrored": "Mirrored Surface Composite",
    "reactive": "Reactive Surface Composite",
}

# ---- engineering blueprints (journal BlueprintName -> in-game blueprint name) ---------------
# The journal carries no localised blueprint string, so this table matters most. Curated for
# the blueprints Commanders actually run (EDCD names, 2026-07); the fallback camel-splits.

_BLUEPRINTS: dict[str, str] = {
    # FSD
    "fsd_longrange": "Increased Range",
    "fsd_shielded": "Shielded",
    "fsd_fastboot": "Faster Boot Sequence",
    # Thrusters
    "engine_dirty": "Dirty Drive Tuning",
    "engine_tuned": "Clean Drive Tuning",
    "engine_reinforced": "Drive Strengthening",
    # Power plant
    "powerplant_boosted": "Overcharged",
    "powerplant_stealth": "Low Emissions",
    "powerplant_armoured": "Armoured",
    # Power distributor
    "powerdistributor_highfrequency": "Charge Enhanced",
    "powerdistributor_highcapacity": "High Charge Capacity",
    "powerdistributor_priorityengines": "Engine Focused",
    "powerdistributor_priorityweapons": "Weapon Focused",
    "powerdistributor_prioritysystems": "System Focused",
    "powerdistributor_shielded": "Shielded",
    # Shield generator
    "shieldgenerator_reinforced": "Reinforced",
    "shieldgenerator_thermic": "Thermal Resistant",
    "shieldgenerator_kinetic": "Kinetic Resistant",
    "shieldgenerator_optimised": "Enhanced Low Power",
    # Armour / hull reinforcement
    "armour_heavyduty": "Heavy Duty",
    "armour_advanced": "Lightweight",
    "armour_explosive": "Blast Resistant",
    "armour_kinetic": "Kinetic Resistant",
    "armour_thermic": "Thermal Resistant",
    "hullreinforcement_heavyduty": "Heavy Duty",
    "hullreinforcement_advanced": "Lightweight",
    "hullreinforcement_explosive": "Blast Resistant",
    "hullreinforcement_kinetic": "Kinetic Resistant",
    "hullreinforcement_thermic": "Thermal Resistant",
    # Weapons
    "weapon_overcharged": "Overcharged",
    "weapon_efficient": "Efficient",
    "weapon_longrange": "Long Range",
    "weapon_shortrange": "Short Range Blaster",
    "weapon_rapidfire": "Rapid Fire",
    "weapon_highcapacity": "High Capacity Magazine",
    "weapon_lightweight": "Lightweight",
    "weapon_sturdy": "Sturdy Mount",
    "weapon_doubleshot": "Double Shot",
    "weapon_focused": "Focused",
    # Utilities & misc
    "shieldbooster_heavyduty": "Heavy Duty",
    "shieldbooster_resistive": "Resistance Augmented",
    "shieldbooster_thermic": "Thermal Resistant",
    "shieldbooster_kinetic": "Kinetic Resistant",
    "shieldbooster_explosive": "Blast Resistant",
    "shieldcellbank_rapid": "Rapid Charge",
    "shieldcellbank_specialised": "Specialised",
    "sensor_longrange": "Long Range",
    "sensor_wideangle": "Wide Angle",
    "sensor_lightweight": "Lightweight",
    "misc_lightweight": "Lightweight",
    "misc_reinforced": "Reinforced",
    "misc_shielded": "Shielded",
}

# ---- experimental effects (journal ExperimentalEffect -> in-game name) -----------------------
# Fallback only — the journal's ExperimentalEffect_Localised is preferred when present.

_EXPERIMENTALS: dict[str, str] = {
    "special_fsd_heavy": "Mass Manager",
    "special_fsd_cooled": "Thermal Spread",
    "special_fsd_lightweight": "Stripped Down",
    "special_fsd_fuelcapacity": "Deep Charge",
    "special_engine_dirty": "Drag Drives",
    "special_engine_cooled": "Thermal Spread",
    "special_engine_haulage": "Drive Distributors",
    "special_engine_lightweight": "Stripped Down",
    "special_engine_toughened": "Double Braced",
    "special_powerdistributor_fast": "Super Conduits",
    "special_powerdistributor_capacity": "Cluster Capacitors",
    "special_powerdistributor_lightweight": "Stripped Down",
    "special_powerdistributor_toughened": "Double Braced",
    "special_powerplant_highcharge": "Monstered",
    "special_powerplant_lowemissions": "Thermal Spread",
    "special_powerplant_toughened": "Double Braced",
}


# ---- symbol decomposition --------------------------------------------------------------------

_CLASS_RATING = {1: "E", 2: "D", 3: "C", 4: "B", 5: "A"}
_SIZE_CLASS_RE = re.compile(r"^(?P<kind>.+?)(?:_size(?P<size>\d+))?(?:_class(?P<cls>\d+))?$")


def _prettify(symbol: str) -> str:
    """Readable fallback for an unmapped symbol: strip prefixes, underscores -> spaces,
    title case. Never speaks a raw internal symbol."""
    s = re.sub(r"^(int_|hpt_|special_)", "", str(symbol).lower())
    return s.replace("_", " ").strip().title()


def _size_class_prefix(size: str | None, cls: str | None) -> str:
    """'5A ' from _size5_class5; '5 ' when only a size is known (e.g. Guardian FSD Booster)."""
    if size is None:
        return ""
    rating = _CLASS_RATING.get(int(cls)) if cls is not None else None
    return f"{size}{rating} " if rating else f"{size} "


def _int_module_name(body: str) -> str:
    m = _SIZE_CLASS_RE.match(body)
    kind, size, cls = m.group("kind"), m.group("size"), m.group("cls")
    name = _INT_KINDS.get(kind)
    if name is None:
        # Passenger cabins encode class differently (int_passengercabin_size6_class3 =
        # luxury tiers) — the generic path still reads fine for anything unmapped.
        return _size_class_prefix(size, cls) + _prettify(kind)
    return _size_class_prefix(size, cls) + name


def _hpt_module_name(body: str) -> str:
    parts = body.split("_")
    # Utility form: hpt_<kind>_size0_class5 (shield boosters, scanners).
    m = _SIZE_CLASS_RE.match(body)
    if m.group("size") is not None or m.group("cls") is not None:
        kind = m.group("kind")
        name = _HPT_KINDS.get(kind, _prettify(kind))
        return _size_class_prefix(m.group("size"), m.group("cls")) + name
    # Weapon form: hpt_<kind...>_<mount>_<size>[_variant...]; mount/size may be absent
    # (hpt_chafflauncher_tiny). Find the mount + size wherever they sit.
    mount = next((p for p in parts if p in _MOUNTS), None)
    size = next((p for p in parts if p in _WEAPON_SIZES), None)
    kind_parts = [p for p in parts if p not in _MOUNTS and p != size]
    kind = "_".join(kind_parts)
    name = _HPT_KINDS.get(kind, _prettify(kind))
    bits = []
    if size and size != "tiny":                 # "tiny" = utility mount; size adds nothing
        bits.append(size)
    if mount:
        bits.append(_MOUNTS[mount])
    bits.append(name)
    return " ".join(bits)


def module_name(item: str) -> str:
    """The spoken name for a Loadout `Item` symbol. Curated where naming matters, structural
    (size/class -> '5A') where it decomposes, readable title-case for the rest."""
    sym = str(item or "").strip().lower()
    if not sym:
        return "unknown module"
    if sym.startswith("int_"):
        return _int_module_name(sym[4:])
    if sym.startswith("hpt_"):
        return _hpt_module_name(sym[4:])
    if "_armour_" in sym:
        grade = sym.rsplit("_", 1)[-1]
        return _ARMOUR_GRADES.get(grade, _prettify(grade) + " armour")
    if sym == "modularcargobaydoor":
        return "cargo hatch"
    if sym.endswith("_cockpit"):
        return "cockpit"
    if sym.startswith("voicepack_"):
        return "COVAS voice pack"
    return _prettify(sym)


def blueprint_name(symbol: str) -> str:
    """The in-game blueprint name for a journal BlueprintName ('FSD_LongRange' ->
    'Increased Range'). Falls back to camel/underscore splitting, never the raw symbol."""
    sym = str(symbol or "").strip()
    if not sym:
        return "an unknown blueprint"
    hit = _BLUEPRINTS.get(sym.lower())
    if hit:
        return hit
    tail = sym.split("_", 1)[-1]                     # drop the module family prefix
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", tail).replace("_", " ").strip().title()


def experimental_name(symbol: str | None, localised: str | None = None) -> str | None:
    """The spoken experimental-effect name: the journal's own localised string first, then
    the curated table, then a prettified symbol. None when the module has no experimental."""
    if localised and str(localised).strip():
        return str(localised).strip()
    if not symbol or not str(symbol).strip():
        return None
    return _EXPERIMENTALS.get(str(symbol).strip().lower()) or _prettify(symbol)


def modifier_label(label: str) -> str:
    """'WeaponsRecharge' -> 'weapons recharge' — the journal's Modifier labels, speakable."""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(label or "")).replace("_", " ").lower()


# ---- slots ------------------------------------------------------------------------------------

_SLOT_NAMES = {
    "frameshiftdrive": "frame shift drive",
    "mainengines": "thrusters",
    "powerplant": "power plant",
    "powerdistributor": "power distributor",
    "lifesupport": "life support",
    "radar": "sensors",
    "fueltank": "fuel tank",
    "armour": "armour",
    "planetaryapproachsuite": "planetary approach suite",
    "shipcockpit": "cockpit",
    "cargohatch": "cargo hatch",
    "vesselvoice": "ship's voice",
}
_HARDPOINT_RE = re.compile(r"^(tiny|small|medium|large|huge)hardpoint(\d+)$")
_OPTIONAL_RE = re.compile(r"^slot(\d+)_size(\d+)$")
_MILITARY_RE = re.compile(r"^military(\d+)$")


def slot_name(slot: str) -> str:
    """A spoken label for a Loadout `Slot` ('MainEngines' -> 'thrusters',
    'Slot04_Size5' -> 'optional slot 4, size 5', 'TinyHardpoint2' -> 'utility mount 2')."""
    s = str(slot or "").strip().lower()
    if s in _SLOT_NAMES:
        return _SLOT_NAMES[s]
    m = _HARDPOINT_RE.match(s)
    if m:
        size, n = m.group(1), int(m.group(2))
        return f"utility mount {n}" if size == "tiny" else f"{size} hardpoint {n}"
    m = _OPTIONAL_RE.match(s)
    if m:
        return f"optional slot {int(m.group(1))}, size {m.group(2)}"
    m = _MILITARY_RE.match(s)
    if m:
        return f"military slot {int(m.group(1))}"
    return _prettify(s)


# ---- finding a module by spoken name -----------------------------------------------------------
# "What's on my FSD / power plant / shields" — map loose speech to fitted modules. Aliases
# resolve to a SLOT or an ITEM-symbol fragment; matching then runs over the actual snapshot,
# so the answer can only ever be a module that is genuinely fitted.

_QUERY_ALIASES: dict[str, tuple[str, str]] = {
    # spoken form -> ("slot", slot key) or ("item", item-symbol fragment)
    "fsd": ("slot", "frameshiftdrive"),
    "frame shift drive": ("slot", "frameshiftdrive"),
    "drive": ("slot", "frameshiftdrive"),
    "jump drive": ("slot", "frameshiftdrive"),
    "thrusters": ("slot", "mainengines"),
    "engines": ("slot", "mainengines"),
    "engine": ("slot", "mainengines"),
    "power plant": ("slot", "powerplant"),
    "powerplant": ("slot", "powerplant"),
    "plant": ("slot", "powerplant"),
    "reactor": ("slot", "powerplant"),
    "power distributor": ("slot", "powerdistributor"),
    "distributor": ("slot", "powerdistributor"),
    "pd": ("slot", "powerdistributor"),
    "life support": ("slot", "lifesupport"),
    "sensors": ("slot", "radar"),
    "radar": ("slot", "radar"),
    "fuel tank": ("slot", "fueltank"),
    "armour": ("slot", "armour"),
    "armor": ("slot", "armour"),
    "hull": ("slot", "armour"),
    "shields": ("item", "shieldgenerator"),
    "shield": ("item", "shieldgenerator"),
    "shield generator": ("item", "shieldgenerator"),
    "fuel scoop": ("item", "fuelscoop"),
    "scoop": ("item", "fuelscoop"),
    "shield booster": ("item", "shieldbooster"),
    "boosters": ("item", "shieldbooster"),
    "fsd booster": ("item", "guardianfsdbooster"),
    "guardian booster": ("item", "guardianfsdbooster"),
}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", str(text).lower()).strip()


def find_modules(snapshot: LoadoutSnapshot, query: str) -> list[ShipModule]:
    """Every fitted module matching a spoken name, nearest-meaning first. Empty when nothing
    matches — the capability then answers with what IS fitted rather than inventing."""
    q = _norm(query)
    if not q or not snapshot.modules:
        return []

    alias = _QUERY_ALIASES.get(q)
    if alias:
        kind, key = alias
        if kind == "slot":
            return [m for m in snapshot.modules if m.slot.lower() == key]
        return [m for m in snapshot.modules if key in m.item.lower()]

    # Free matching: the query inside the module's friendly name, slot name, or raw symbol
    # (handles "multi-cannon", "cargo rack", "collector limpet", "docking computer"…).
    hits: list[ShipModule] = []
    for m in snapshot.modules:
        names = (_norm(module_name(m.item)), _norm(slot_name(m.slot)), _norm(m.item))
        if any(q in n or n in q for n in names if n):
            hits.append(m)
    return hits
