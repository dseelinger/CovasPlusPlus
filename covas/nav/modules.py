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

from .module_data import MODULE_ROWS

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
# The COMPLETE module set, baked offline from EDCD/FDevIDs outfitting.csv into
# `module_data.MODULE_ROWS` (regenerate with `scripts/gen_module_taxonomy.py`). Every
# purchasable module is here with its real sizes/mounts/ratings and the EXACT Spansh
# filter name — so `resolve()` recognises anything the Commander can actually buy, not a
# hand-picked subset. We only hand-maintain the friendly *aliases* below (mishears +
# shorthand for the modules people ask for by voice); the rows themselves are generated.

# Friendly spoken aliases / common mishears, keyed by the exact module name. Everything the
# game sells resolves by its real name regardless; these just make the frequent asks robust
# to Whisper's spelling and to shorthand ("mc", "fsd", "scoop"). Names not listed simply
# have no extra aliases — that's fine.
_ALIASES: dict[str, tuple[str, ...]] = {
    "Multi-Cannon": ("multicannon", "multi cannon", "multiple cannon", "mc"),
    "Beam Laser": ("beam", "beamlaser"),
    "Burst Laser": ("burst",),
    "Pulse Laser": ("pulse",),
    "Cannon": ("cannons",),
    "Fragment Cannon": ("frag cannon", "fragcannon", "frag", "shotgun"),
    "Plasma Accelerator": ("plasma", "pa"),
    "Rail Gun": ("railgun", "rail"),
    "Missile Rack": ("missiles", "missile", "dumbfire", "dumbfire missiles"),
    "Seeker Missile Rack": ("seeker missiles", "seekers", "seeker missile"),
    "Torpedo Pylon": ("torpedoes", "torpedo"),
    "Mine Launcher": ("mines", "mine"),
    "Mining Laser": ("mining lasers", "extractor"),
    "AX Multi-Cannon": ("anti xeno multi-cannon", "ax multicannon", "anti-xeno multicannon"),
    "AX Missile Rack": ("ax missiles", "anti xeno missile rack"),
    "Abrasion Blaster": ("abrasion",),
    "Frame Shift Drive": ("fsd", "hyperdrive", "jump drive", "frameshift drive"),
    "Frame Shift Drive (SCO)": ("sco drive", "sco fsd", "supercruise overcharge", "fsd sco"),
    "Thrusters": ("thruster", "engines"),
    "Power Plant": ("powerplant", "reactor"),
    "Power Distributor": ("distributor", "distro"),
    "Life Support": ("lifesupport",),
    "Sensors": ("sensor",),
    "Fuel Tank": ("fueltank", "tank"),
    "Fuel Scoop": ("fuelscoop", "scoop"),
    "Shield Generator": ("shields", "shield gen", "sg"),
    "Bi-Weave Shield Generator": ("biweave", "bi weave", "bi-weave shields", "biweave shields"),
    "Prismatic Shield Generator": ("prismatics", "prismatic shields", "prismatic shield"),
    "Shield Cell Bank": ("scb", "shield cell", "cell bank"),
    "Cargo Rack": ("cargo", "cargo hold"),
    "Hull Reinforcement Package": ("hrp", "hull reinforcement"),
    "Module Reinforcement Package": ("mrp", "module reinforcement"),
    "Auto Field-Maintenance Unit": ("afmu", "auto field maintenance unit",
                                    "field maintenance unit", "repair unit"),
    "Frame Shift Drive Interdictor": ("interdictor", "fsd interdictor", "interdiction"),
    "Detailed Surface Scanner": ("dss", "surface scanner"),
    "Collector Limpet Controller": ("collector limpets", "collector controller", "collector limpet"),
    "Prospector Limpet Controller": ("prospector limpets", "prospector controller",
                                     "prospector limpet"),
    "Repair Limpet Controller": ("repair limpets", "repair controller", "repair limpet"),
    "Fuel Transfer Limpet Controller": ("fuel transfer limpets", "fuel limpet"),
    "Fighter Hangar": ("hangar", "fighter bay", "scb hangar"),
    "Shield Booster": ("shield boosters", "booster"),
    "Heat Sink Launcher": ("heatsink", "heat sink", "heatsinks"),
    "Chaff Launcher": ("chaff",),
    "Point Defence": ("point defense", "pd turret"),
    "Kill Warrant Scanner": ("kws", "kill warrant"),
    "Cargo Scanner": ("manifest scanner",),
    "Frame Shift Wake Scanner": ("wake scanner", "fsd wake scanner"),
    "Electronic Countermeasure": ("ecm",),
    "Shutdown Field Neutraliser": ("shutdown neutralizer", "caustic neutraliser"),
    "Pulse Wave Analyser": ("pulse wave analyzer", "pwa"),
    # newly-covered modules people ask for by a shorter name. Bare "docking computer" maps to
    # the Standard one (the sensible default); "advanced docking computer" exact-matches its
    # own name and needs no alias.
    "Standard Docking Computer": ("docking computer", "std docking computer"),
    "Supercruise Assist": ("sc assist", "supercruise"),
}


def _spec_from_row(row: tuple) -> ModuleSpec:
    """Build a ModuleSpec from a generated (id, name, category, sizes, mounts, ratings) row,
    attaching any hand-curated aliases for that name."""
    sid, name, category, sizes, mounts, ratings = row
    return ModuleSpec(sid, name, category, tuple(sizes), tuple(mounts), ratings,
                      _ALIASES.get(name, ()))


TAXONOMY: tuple[ModuleSpec, ...] = tuple(_spec_from_row(r) for r in MODULE_ROWS)


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

# Every canonical module name, for the help subsystem's failure-recovery vocabulary and as the
# bundled baseline the live ModuleIndex reconciles against (mirrors ships.SHIP_NAMES).
MODULE_NAMES: tuple[str, ...] = tuple(spec.name for spec in TAXONOMY)

# A short, friendly starter list for the Unknown case (common asks).
_COMMON = ("Multi-Cannon", "Beam Laser", "Frame Shift Drive", "Fuel Scoop",
           "Shield Generator", "Cargo Rack")


def _lookup_with_extras(extra_names) -> dict[str, ModuleSpec]:
    """The bundled name/alias lookup, plus a synthetic spec for each extra canonical name (a
    module Spansh knows that the bundle doesn't yet — see `module_index.py`). Extras contribute
    their exact name only, with NO sizes/mounts: a live-only module resolves by name and searches
    unqualified (no "which size?" guidance) until the next EDCD refresh fills its attributes in.
    Bundled names always win a collision. Returns `_LOOKUP` unchanged (no copy) when there are no
    genuinely-new extras. Mirrors ships._lookup_with_extras."""
    fresh: dict[str, ModuleSpec] = {}
    for name in extra_names or ():
        key = _norm(name)
        if key and key not in _LOOKUP and key not in fresh:
            # category "" so no size-word/mount logic engages; sizes/mounts empty -> Resolved.
            fresh[key] = ModuleSpec(id=f"live:{key}", name=str(name), category="")
    return {**_LOOKUP, **fresh} if fresh else _LOOKUP


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


def resolve(query: str, size=None, mount=None,
            *, extra_names=()) -> Resolved | NeedAttrs | Ambiguous | Unknown:
    """Map a loose module name (+ optional size & mount) to a structured outcome. Pure and
    offline — the LLM drives the dialog and re-calls this with more-complete args each turn.

    `extra_names` are canonical module names Spansh knows that the bundled taxonomy is missing
    (newly-released modules, from the live `ModuleIndex`); they're folded into the lookup so a
    brand-new module resolves by exact/containment/fuzzy match. Such a live-only module has no
    known sizes/mounts, so it resolves straight to a name-only search. Default empty -> pure
    bundled behaviour. Mirrors ships.resolve_ship.

    Matching, most-confident first:
      1. exact normalized match on a name or alias,
      2. substring containment (query in a name, or a name in the query),
      3. fuzzy (difflib) against names + aliases.
    A single hit → Resolved/NeedAttrs; several → Ambiguous; none → Unknown(suggestions)."""
    q = _norm(query)
    if not q:
        return Unknown(query=str(query), suggestions=list(_COMMON))

    lookup = _lookup_with_extras(extra_names)

    # 1. exact
    if q in lookup:
        return _finish(lookup[q], size, mount)

    # 2. containment — the query is inside a name/alias, or a name sits inside the query
    #    ("closest multi cannon please" contains "multicannon"). Dedupe to distinct specs.
    hits: list[ModuleSpec] = []
    seen: set[str] = set()
    for key, spec in lookup.items():
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
    close = difflib.get_close_matches(q, list(lookup), n=6, cutoff=0.72)
    specs: list[ModuleSpec] = []
    seen.clear()
    for key in close:
        spec = lookup[key]
        if spec.id not in seen:
            seen.add(spec.id)
            specs.append(spec)
    if len(specs) == 1:
        return _finish(specs[0], size, mount)
    if len(specs) > 1:
        return Ambiguous(candidates=_unique_names(specs))

    return Unknown(query=str(query), suggestions=_suggest(q, lookup))


def _unique_names(specs: list[ModuleSpec]) -> list[str]:
    """Distinct module names, capped so a spoken "did you mean…" stays short."""
    return [s.name for s in specs][:8]


def _suggest(q: str, lookup: dict[str, ModuleSpec] | None = None) -> list[str]:
    """Best-effort near names for an Unknown, else a few common modules. Suggests over the given
    lookup (bundled + any live extras) so a near-miss on a new module can still be offered."""
    lut = lookup if lookup is not None else _LOOKUP
    by_name = {_norm(s.name): s.name for s in lut.values()}
    close = difflib.get_close_matches(q, list(by_name), n=3, cutoff=0.5)
    names = [by_name[c] for c in close]
    return names or list(_COMMON[:4])
