"""Bundled Elite Dangerous outfitting taxonomy + a pure `resolve()` (offline).

The whole point of this module is that understanding *what the Commander asked for* — the
ask/confirm/cancel disambiguation dialog — never touches the network. The taxonomy below
is baked from the real Spansh/EDCD outfitting data (names, valid sizes, mounts, ratings),
so `resolve()` is a pure function: given a loose/possibly-misheard name (+ optional size &
mount), it returns exactly one of four structured outcomes the LLM turns into speech:

    Resolved   — a single module, fully pinned down (ready to search)
    NeedAttrs  — identified the module but a required size/mount is missing (ask for it)
    Ambiguous  — the name matches several modules (ask which)
    Unknown    — no confident match (offer suggestions)

The LLM does the fuzzy *understanding* ("multiple cannon" → Multi-Cannon); this module
*validates* it against the taxonomy and guides the next question. It NEVER guesses a
missing attribute — but when a module comes in exactly one size or one mount, that value
is determined (not guessed), so it's filled in silently.

`name` on each spec is the EXACT string Spansh's station-search module filter expects — the
station lookup (`closest.py`) filters on it verbatim, so don't "tidy" these.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

# ED size vocabulary. Hardpoints (weapons) are sold in small/medium/large/huge = class
# 1/2/3/4; core/optional internals use class numbers 1-8; utilities are size-less (class 0).
_SIZE_WORDS: dict[str, int] = {
    "small": 1, "medium": 2, "large": 3, "huge": 4,
    # a few things the Commander (or Whisper) might say
    "tiny": 1, "s": 1, "m": 2, "l": 3, "h": 4,
}
_WORD_FOR_SIZE = {1: "small", 2: "medium", 3: "large", 4: "huge"}

# Mount vocabulary → Spansh's `weapon_mode` value (Fixed / Gimbal / Turret). Note Spansh
# uses the short forms "Gimbal"/"Turret", not "Gimballed"/"Turreted" — the station lookup
# post-filters on these exact strings (Spansh's request filter can't narrow by mount).
_MOUNTS: dict[str, str] = {
    "fixed": "Fixed",
    # gimballed + common spelling/mishear variants (Whisper renders it loosely)
    "gimballed": "Gimbal", "gimbal": "Gimbal", "gimballed mount": "Gimbal",
    "gimbaled": "Gimbal", "gimbled": "Gimbal", "gimballd": "Gimbal", "gimballe": "Gimbal",
    "turreted": "Turret", "turret": "Turret", "turretted": "Turret", "turre ted": "Turret",
}
_WORD_FOR_MOUNT = {"Fixed": "fixed", "Gimbal": "gimballed", "Turret": "turreted"}


@dataclass(frozen=True)
class ModuleSpec:
    """One outfitting module in the taxonomy. `sizes`/`mounts` list every valid option;
    an empty tuple means that attribute doesn't apply (utilities have no size; non-weapons
    have no mount). `name` is the exact Spansh filter string."""
    id: str                      # stable slug (spoken-independent identity)
    name: str                    # EXACT Spansh module-filter name
    category: str                # weapon | internal | standard | utility (for labels)
    sizes: tuple[int, ...] = ()
    mounts: tuple[str, ...] = ()
    ratings: str = ""            # informational only (e.g. "ABCDE")
    aliases: tuple[str, ...] = ()


# ---- resolve() outcomes -------------------------------------------------------------------


@dataclass(frozen=True)
class Resolved:
    """A single module, fully pinned down. `size` is the Spansh class (None when size-less);
    `mount` is the Spansh weapon_mode (None when not a weapon). Ready to hand to the station
    search."""
    id: str
    label: str                   # natural spoken label, e.g. "medium gimballed Multi-Cannon"
    name: str                    # exact Spansh filter name
    category: str
    size: int | None = None
    mount: str | None = None
    kind: str = "resolved"


@dataclass(frozen=True)
class NeedAttrs:
    """Identified the module, but a required size and/or mount is missing (or invalid).
    `options` maps each missing attr to the valid choices, phrased for speech."""
    module: str                  # the module's display name
    missing: list[str]           # subset of ["size", "mount"], in ask order
    options: dict[str, list[str]]
    kind: str = "need_attrs"


@dataclass(frozen=True)
class Ambiguous:
    """The loose name matched several modules — the LLM should ask which one."""
    candidates: list[str]
    kind: str = "ambiguous"


@dataclass(frozen=True)
class Unknown:
    """No confident match. `suggestions` are the closest taxonomy names, if any."""
    query: str
    suggestions: list[str] = field(default_factory=list)
    kind: str = "unknown"


# ---- the taxonomy -------------------------------------------------------------------------
# Baked from real Spansh station outfitting data (names/sizes/mounts/ratings verified live).
# Not every module in the game — a broad, useful core that a Commander is likely to ask for
# by voice. Extend by adding rows; `resolve()` picks them up automatically. `M`/`W`/`FGT`
# helpers keep the rows terse.

_FIXED = ("Fixed",)
_FGT = ("Fixed", "Gimbal", "Turret")
_FT = ("Fixed", "Turret")


def _w(id, name, sizes, mounts, ratings="", aliases=()) -> ModuleSpec:
    return ModuleSpec(id, name, "weapon", tuple(sizes), tuple(mounts), ratings, tuple(aliases))


def _i(id, name, sizes, ratings="", aliases=()) -> ModuleSpec:
    return ModuleSpec(id, name, "internal", tuple(sizes), (), ratings, tuple(aliases))


def _c(id, name, sizes, ratings="", aliases=()) -> ModuleSpec:  # core/standard internal
    return ModuleSpec(id, name, "standard", tuple(sizes), (), ratings, tuple(aliases))


def _u(id, name, ratings="", aliases=(), mounts=()) -> ModuleSpec:  # utility (class 0)
    return ModuleSpec(id, name, "utility", (), tuple(mounts), ratings, tuple(aliases))


TAXONOMY: tuple[ModuleSpec, ...] = (
    # --- weapons (hardpoints): small/medium/large/huge + Fixed/Gimbal/Turret --------------
    _w("multi_cannon", "Multi-Cannon", (1, 2, 3, 4), _FGT, "ACEFG",
       ("multicannon", "multi cannon", "multiple cannon", "multi-cannon", "mc")),
    _w("beam_laser", "Beam Laser", (1, 2, 3, 4), _FGT, "ACDEF",
       ("beam", "beam laser", "beamlaser")),
    _w("burst_laser", "Burst Laser", (1, 2, 3, 4), _FGT, "DEFG",
       ("burst", "burst laser")),
    _w("pulse_laser", "Pulse Laser", (1, 2, 3, 4), _FGT, "ADEFG",
       ("pulse", "pulse laser")),
    _w("cannon", "Cannon", (1, 2, 3, 4), _FGT, "BCDEF", ("cannons",)),
    _w("fragment_cannon", "Fragment Cannon", (1, 2, 3), _FGT, "ACDE",
       ("frag cannon", "fragcannon", "frag", "shotgun")),
    _w("plasma_accelerator", "Plasma Accelerator", (2, 3, 4), _FIXED, "ABC",
       ("plasma", "plasma accelerator", "pa")),
    _w("rail_gun", "Rail Gun", (1, 2), _FIXED, "BD",
       ("railgun", "rail gun", "rail")),
    _w("missile_rack", "Missile Rack", (1, 2, 3), _FIXED, "AB",
       ("missiles", "missile", "dumbfire", "dumbfire missiles")),
    _w("seeker_missile", "Seeker Missile Rack", (1, 2, 3), _FIXED, "AB",
       ("seeker missiles", "seekers", "seeker missile")),
    _w("torpedo", "Torpedo Pylon", (1, 2, 3), _FIXED, "I",
       ("torpedoes", "torpedo", "torpedo pylon")),
    _w("mine_launcher", "Mine Launcher", (1, 2), _FIXED, "I", ("mines", "mine")),
    _w("mining_laser", "Mining Laser", (1, 2), _FT, "D",
       ("mining laser", "mining lasers", "extractor")),
    _w("ax_multi_cannon", "AX Multi-Cannon", (2, 3), _FT, "CEF",
       ("anti xeno multi-cannon", "ax multicannon", "anti-xeno multicannon")),
    _w("ax_missile_rack", "AX Missile Rack", (2, 3), _FT, "AB",
       ("ax missiles", "anti xeno missile rack")),
    _w("abrasion_blaster", "Abrasion Blaster", (1,), _FT, "D", ("abrasion",)),
    # --- core internals (standard): class-numbered, no mount ------------------------------
    _c("frame_shift_drive", "Frame Shift Drive", (2, 3, 4, 5, 6, 7, 8), "ABCDE",
       ("fsd", "frame shift drive", "hyperdrive", "jump drive", "frameshift drive")),
    _c("fsd_sco", "Frame Shift Drive (SCO)", (2, 3, 4, 5, 6, 7, 8), "ABCD",
       ("sco drive", "sco fsd", "supercruise overcharge", "fsd sco")),
    _c("thrusters", "Thrusters", (2, 3, 4, 5, 6, 7, 8), "ABCDE",
       ("thruster", "engines")),
    _c("power_plant", "Power Plant", (2, 3, 4, 5, 6, 7, 8), "ABCDE",
       ("powerplant", "power plant", "reactor")),
    _c("power_distributor", "Power Distributor", (1, 2, 3, 4, 5, 6, 7, 8), "ABCDE",
       ("distributor", "power distributor", "distro")),
    _c("life_support", "Life Support", (1, 2, 3, 4, 5, 6, 7, 8), "ABCDE",
       ("lifesupport", "life support")),
    _c("sensors", "Sensors", (1, 2, 3, 4, 5, 6, 7, 8), "ABCDE",
       ("sensor", "sensors")),
    _c("fuel_tank", "Fuel Tank", (1, 2, 3, 4, 5, 6, 7, 8), "C",
       ("fueltank", "fuel tank", "tank")),
    # --- optional internals: class-numbered, no mount ------------------------------------
    _i("fuel_scoop", "Fuel Scoop", (1, 2, 3, 4, 5, 6, 7, 8), "ABCDE",
       ("fuelscoop", "fuel scoop", "scoop")),
    _i("shield_generator", "Shield Generator", (2, 3, 4, 5, 6, 7, 8), "ABCDE",
       ("shields", "shield generator", "shield gen", "sg")),
    _i("bi_weave", "Bi-Weave Shield Generator", (1, 2, 3, 4, 5, 6, 7, 8), "C",
       ("biweave", "bi weave", "bi-weave shields", "biweave shields")),
    _i("prismatic", "Prismatic Shield Generator", (1, 2, 3, 4, 5, 6, 7, 8), "A",
       ("prismatics", "prismatic shields", "prismatic shield")),
    _i("shield_cell_bank", "Shield Cell Bank", (1, 2, 3, 4, 5, 6, 7, 8), "ABCDE",
       ("scb", "shield cell", "cell bank", "shield cell bank")),
    _i("cargo_rack", "Cargo Rack", (1, 2, 3, 4, 5, 6, 7, 8), "E",
       ("cargo", "cargo rack", "cargo hold")),
    _i("hull_reinforcement", "Hull Reinforcement Package", (1, 2, 3, 4, 5), "DE",
       ("hrp", "hull reinforcement", "hull reinforcement package")),
    _i("module_reinforcement", "Module Reinforcement Package", (1, 2, 3, 4, 5), "DE",
       ("mrp", "module reinforcement", "module reinforcement package")),
    _i("afmu", "Auto Field-Maintenance Unit", (1, 2, 3, 4, 5, 6, 7, 8), "ABCDE",
       ("afmu", "auto field maintenance unit", "field maintenance unit", "repair unit")),
    _i("fsd_interdictor", "Frame Shift Drive Interdictor", (1, 2, 3, 4), "ABCDE",
       ("interdictor", "fsd interdictor", "interdiction")),
    _i("refinery", "Refinery", (1, 2, 3, 4), "ABCDE", ("refinery",)),
    _i("detailed_surface_scanner", "Detailed Surface Scanner", (1,), "CI",
       ("dss", "surface scanner", "detailed surface scanner")),
    _i("collector_limpet", "Collector Limpet Controller", (1, 3, 5, 7), "ABCDE",
       ("collector limpets", "collector controller", "collector limpet")),
    _i("prospector_limpet", "Prospector Limpet Controller", (1, 3, 5, 7), "ABCDE",
       ("prospector limpets", "prospector controller", "prospector limpet")),
    _i("repair_limpet", "Repair Limpet Controller", (1, 3, 5, 7), "ABCDE",
       ("repair limpets", "repair controller", "repair limpet")),
    _i("fuel_transfer_limpet", "Fuel Transfer Limpet Controller", (1, 3, 5, 7), "ABCDE",
       ("fuel transfer limpets", "fuel limpet")),
    _i("fighter_hangar", "Fighter Hangar", (5, 6, 7), "D",
       ("hangar", "fighter hangar", "fighter bay", "scb hangar")),
    # --- utilities (class 0): no size, (almost) no mount ----------------------------------
    _u("shield_booster", "Shield Booster", "ABCDE",
       ("shield boosters", "booster", "shield booster")),
    _u("heat_sink", "Heat Sink Launcher", "I",
       ("heatsink", "heat sink", "heat sink launcher", "heatsinks")),
    _u("chaff", "Chaff Launcher", "I", ("chaff", "chaff launcher")),
    _u("point_defence", "Point Defence", "I",
       ("point defense", "point defence", "pd turret")),
    _u("kill_warrant_scanner", "Kill Warrant Scanner", "ABCDE",
       ("kws", "kill warrant scanner", "kill warrant")),
    _u("cargo_scanner", "Cargo Scanner", "ABCDE",
       ("manifest scanner", "cargo scanner")),
    _u("frame_shift_wake_scanner", "Frame Shift Wake Scanner", "ABCDE",
       ("wake scanner", "fsd wake scanner", "frame shift wake scanner")),
    _u("ecm", "Electronic Countermeasure", "F",
       ("ecm", "electronic countermeasure")),
    _u("shutdown_neutraliser", "Shutdown Field Neutraliser", "F",
       ("shutdown field neutraliser", "shutdown neutralizer", "caustic neutraliser")),
    _u("pulse_wave_analyser", "Pulse Wave Analyser", "ABCDE",
       ("pulse wave analyzer", "pulse wave analyser", "pwa")),
)


# ---- normalization + lookup ---------------------------------------------------------------

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _norm(text: str) -> str:
    """Fold a spoken/typed name to a comparison key: lowercase, drop punctuation/spaces so
    'Multi-Cannon', 'multi cannon' and 'multicannon' all collapse to 'multicannon'."""
    return _NON_ALNUM.sub("", str(text).lower())


# Normalized key -> spec, for exact + fuzzy matching. Names first so a name always wins over
# an alias collision; aliases fill in with setdefault.
def _build_lookup() -> dict[str, ModuleSpec]:
    lut: dict[str, ModuleSpec] = {}
    for spec in TAXONOMY:
        lut[_norm(spec.name)] = spec
    for spec in TAXONOMY:
        for alias in spec.aliases:
            lut.setdefault(_norm(alias), spec)
    return lut


_LOOKUP = _build_lookup()

# A short, friendly starter list for the Unknown case (common asks).
_COMMON = ("Multi-Cannon", "Beam Laser", "Frame Shift Drive", "Fuel Scoop",
           "Shield Generator", "Cargo Rack")


# ---- size / mount parsing -----------------------------------------------------------------

def _parse_size(spec: ModuleSpec, raw) -> int | None:
    """Turn a size arg (a word like 'large' or a class number like 5 / '5') into a valid
    class for `spec`, or None if it isn't one of the module's sizes."""
    if raw is None:
        return None
    s = str(raw).strip().lower()
    n: int | None = None
    if s.isdigit():
        n = int(s)
    elif s in _SIZE_WORDS:
        n = _SIZE_WORDS[s]
    else:
        # tolerate "class 5", "size 5", "5a"
        m = re.search(r"\d+", s)
        if m:
            n = int(m.group())
    return n if n in spec.sizes else None


def _parse_mount(spec: ModuleSpec, raw) -> str | None:
    """Map a mount word to `spec`'s valid Spansh weapon_mode, or None if not valid."""
    if raw is None:
        return None
    m = _MOUNTS.get(str(raw).strip().lower())
    return m if m in spec.mounts else None


def _size_options(spec: ModuleSpec) -> list[str]:
    """Valid sizes phrased for speech: size words for weapons, 'class N' for internals."""
    if spec.category == "weapon":
        return [_WORD_FOR_SIZE.get(s, str(s)) for s in spec.sizes]
    return [f"class {s}" for s in spec.sizes]


def _mount_options(spec: ModuleSpec) -> list[str]:
    return [_WORD_FOR_MOUNT[m] for m in spec.mounts]


def _label(spec: ModuleSpec, size: int | None, mount: str | None) -> str:
    """A natural spoken label for a fully-resolved module."""
    parts: list[str] = []
    if size is not None:
        parts.append(_WORD_FOR_SIZE[size] if spec.category == "weapon" and size in _WORD_FOR_SIZE
                     else f"class {size}")
    if mount is not None:
        parts.append(_WORD_FOR_MOUNT[mount])
    parts.append(spec.name)
    return " ".join(parts)


# ---- resolve ------------------------------------------------------------------------------

def _finish(spec: ModuleSpec, size, mount) -> Resolved | NeedAttrs:
    """Pin down size + mount for a matched module. A single valid option is filled in
    silently (determined, not guessed); >1 with nothing/invalid given → NeedAttrs."""
    missing: list[str] = []
    options: dict[str, list[str]] = {}

    rsize: int | None = None
    if len(spec.sizes) == 1:
        rsize = spec.sizes[0]
    elif len(spec.sizes) > 1:
        rsize = _parse_size(spec, size)
        if rsize is None:
            missing.append("size")
            options["size"] = _size_options(spec)

    rmount: str | None = None
    if len(spec.mounts) == 1:
        rmount = spec.mounts[0]
    elif len(spec.mounts) > 1:
        rmount = _parse_mount(spec, mount)
        if rmount is None:
            missing.append("mount")
            options["mount"] = _mount_options(spec)

    if missing:
        return NeedAttrs(module=spec.name, missing=missing, options=options)
    return Resolved(id=spec.id, label=_label(spec, rsize, rmount), name=spec.name,
                    category=spec.category, size=rsize, mount=rmount)


def resolve(query: str, size=None, mount=None) -> Resolved | NeedAttrs | Ambiguous | Unknown:
    """Map a loose module name (+ optional size & mount) to a structured outcome. Pure and
    offline — the LLM drives the dialog and re-calls this with more-complete args each turn.

    Matching, most-confident first:
      1. exact normalized match on a name or alias,
      2. substring containment (query in a name, or a name in the query),
      3. fuzzy (difflib) against names + aliases.
    A single hit → Resolved/NeedAttrs; several → Ambiguous; none → Unknown(suggestions)."""
    q = _norm(query)
    if not q:
        return Unknown(query=str(query), suggestions=list(_COMMON))

    # 1. exact
    if q in _LOOKUP:
        return _finish(_LOOKUP[q], size, mount)

    # 2. containment — the query is inside a name/alias, or a name sits inside the query
    #    ("closest multi cannon please" contains "multicannon"). Dedupe to distinct specs.
    hits: list[ModuleSpec] = []
    seen: set[str] = set()
    for key, spec in _LOOKUP.items():
        if len(key) < 3:
            continue
        if (q in key or key in q) and spec.id not in seen:
            seen.add(spec.id)
            hits.append(spec)
    if len(hits) == 1:
        return _finish(hits[0], size, mount)
    if len(hits) > 1:
        return Ambiguous(candidates=_unique_names(hits))

    # 3. fuzzy — catches Whisper mishears the aliases don't cover
    close = difflib.get_close_matches(q, list(_LOOKUP), n=6, cutoff=0.72)
    specs: list[ModuleSpec] = []
    seen.clear()
    for key in close:
        spec = _LOOKUP[key]
        if spec.id not in seen:
            seen.add(spec.id)
            specs.append(spec)
    if len(specs) == 1:
        return _finish(specs[0], size, mount)
    if len(specs) > 1:
        return Ambiguous(candidates=_unique_names(specs))

    return Unknown(query=str(query), suggestions=_suggest(q))


def _unique_names(specs: list[ModuleSpec]) -> list[str]:
    """Distinct module names, capped so a spoken "did you mean…" stays short."""
    return [s.name for s in specs][:8]


def _suggest(q: str) -> list[str]:
    """Best-effort near names for an Unknown, else a few common modules."""
    close = difflib.get_close_matches(q, [_norm(s.name) for s in TAXONOMY], n=3, cutoff=0.5)
    names = [_LOOKUP[c].name for c in close if c in _LOOKUP]
    return names or list(_COMMON[:4])
