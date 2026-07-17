"""Bundled Elite Dangerous ship roster + a pure `resolve_ship()` (offline).

The sibling of `modules.py`, for "find the closest station that sells SHIP X". As with the
outfitting taxonomy, the whole point is that understanding *which ship the Commander asked
for* — the disambiguation dialog — never touches the network.

The roster is two layers merged at import (issue #101), so keeping up with FDev content is a
data update, not a code edit:

  * a GENERATED base — `data/ship_roster.json` (`{id, name, ed_symbol}` per hull), baked from a
    Spansh shipyard harvest by `scripts/gen_ship_roster.py`; the same Spansh-live lineage this
    roster always trusted, but now regenerated instead of hand-typed.
  * a CURATED overlay below — aliases (mishears, "fdl"), `_FAMILIES` disambiguation, `_COMMON` —
    kept hand-maintained because it is genuinely editorial. Overlay rows key on the base `id`;
    an alias row for an id the base doesn't have is a build error (`_build_roster` fails loud).

`resolve_ship()` is a pure function returning one of three structured outcomes the LLM turns
into speech:

    ResolvedShip   — a single ship, pinned to its exact Spansh name (ready to search)
    AmbiguousShip  — the loose name matches a genuine FAMILY (ask which; never guess)
    UnknownShip    — no confident match (offer suggestions)

Ships have no size/mount to fill in (unlike modules), so there is no NeedAttrs equivalent —
a ship is either resolved, one of a family to ask about, or unknown.

`name` on each spec is the EXACT string Spansh's station-search `ships` filter expects, and
that filter is CASE-SENSITIVE exact-match (verified live: "Krait Mk II" and "anaconda" both
return zero — only "Krait MkII" / "Anaconda" work), and an unrecognised name returns zero
rather than everything. That is precisely why resolution must map messy speech to the exact
canonical name offline before the search fires — so don't "tidy" these strings.

The genuinely ambiguous families the model must ASK about (never guess): Krait (MkII vs
Phantom), Cobra (MkIII / MkIV / MkV), Viper (MkIII vs MkIV), Asp (Explorer vs Scout),
Diamondback (Explorer vs Scout), and Type (Type-6/7/8/9/10/11). Bare "krait"/"cobra"/"asp"/
"type"/… resolve to AmbiguousShip; a discriminator ("krait phantom", "type 9") resolves.
"""
from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

_DATA = Path(__file__).resolve().parent / "data"


@dataclass(frozen=True)
class ShipSpec:
    """One ship in the roster. `name` is the EXACT Spansh `ships` filter string; `symbol` is
    Spansh's internal ed_symbol (informational — handy when cross-checking the data)."""
    id: str                      # stable slug (spoken-independent identity)
    name: str                    # EXACT Spansh ships-filter name (case-sensitive)
    symbol: str = ""             # Spansh ed_symbol (informational only)
    aliases: tuple[str, ...] = ()


# ---- resolve_ship() outcomes --------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedShip:
    """A single ship, pinned to its exact Spansh name. Ready to hand to the station search."""
    id: str
    name: str                    # exact Spansh filter name == natural spoken label
    symbol: str = ""
    kind: str = "resolved"

    @property
    def label(self) -> str:
        """Spoken label — the canonical name reads fine aloud ('Krait MkII', 'Asp Explorer')."""
        return self.name


@dataclass(frozen=True)
class AmbiguousShip:
    """The loose name matched a genuine ship family — the LLM should ask which one."""
    candidates: list[str]
    kind: str = "ambiguous"


@dataclass(frozen=True)
class UnknownShip:
    """No confident match. `suggestions` are the closest roster names, if any."""
    query: str
    suggestions: list[str] = field(default_factory=list)
    kind: str = "unknown"


# ---- curated overlay: aliases per canonical id --------------------------------------------
# Aliases cover common short names and Whisper mishears; they stay hand-maintained (editorial)
# next to the resolution logic, keyed by the GENERATED base's id. FAMILY roots (bare "krait",
# "cobra", …) are handled separately (see _FAMILIES) so they ASK rather than resolve, so they
# must NOT appear as a single ship's alias here. An alias row whose id is absent from the base
# is caught by `_build_roster` (fail loud) — the "orphaned overlay" contract from issue #101.

_ALIASES: dict[str, tuple[str, ...]] = {
    "sidewinder": ("sidey", "side winder"),
    "eagle": ("eagle mk2", "eagle mkii"),
    "imperial_eagle": ("i eagle", "imp eagle"),
    "viper_mk3": ("viper mk3", "viper mark 3", "viper mark three", "viper three", "viper 3"),
    "cobra_mk3": ("cobra mk3", "cobra mark 3", "cobra mark three", "cobra three", "cobra 3"),
    "viper_mk4": ("viper mk4", "viper mark 4", "viper mark four", "viper four", "viper 4"),
    "diamondback_scout": ("dbs", "diamond back scout"),
    "cobra_mk4": ("cobra mk4", "cobra mark 4", "cobra mark four", "cobra four", "cobra 4"),
    "cobra_mk5": ("cobra mk5", "cobra mark 5", "cobra mark five", "cobra five", "cobra 5"),
    "type_6": ("type 6", "type six", "t6", "type-6", "type 6 transporter"),
    # bare "diamondback" is an ambiguous family (see _FAMILIES)
    "diamondback_explorer": ("dbx", "diamond back explorer"),
    "imperial_courier": ("courier", "imp courier"),
    "asp_scout": ("asp s",),
    "asp_explorer": ("aspx", "asp x", "asp e"),
    "federal_dropship": ("dropship",),
    "imperial_clipper": ("clipper", "imp clipper"),  # "clipper" in ED parlance == Imperial Clipper
    "federal_assault_ship": ("fas", "assault ship"),
    "type_7": ("type 7", "type seven", "t7", "type-7", "type 7 transporter"),
    "type_8": ("type 8", "type eight", "t8", "type-8", "type 8 transporter"),
    "federal_gunship": ("gunship",),
    "krait_mk2": ("krait mk2", "krait mark 2", "krait mark two", "krait two", "krait 2"),
    "krait_phantom": ("phantom", "krait light"),
    "fer_de_lance": ("fdl", "fer de lance", "ferdelance", "fur de lance"),
    "python_mk2": ("python mk2", "python mark 2", "python mark two", "python two", "python 2"),
    "type_9": ("type 9", "type nine", "t9", "type-9", "type 9 heavy", "type 9 transporter"),
    "type_10": ("type 10", "type ten", "t10", "type-10", "type 10 defender"),
    "type_11": ("type 11", "type eleven", "t11", "type-11", "type 11 prospector"),
    "beluga": ("beluga",),
    "alliance_chieftain": ("chieftain", "chief"),
    "alliance_crusader": ("crusader",),
    "alliance_challenger": ("challenger",),
    "federal_corvette": ("corvette",),
    "imperial_cutter": ("cutter", "imp cutter"),
    "anaconda": ("conda",),
    "panther_clipper": ("panther", "panther clipper"),
    "kestrel": ("kestrel", "kestrel mk2", "kestrel mark 2"),
    "caspian": ("caspian",),
    "lynx": ("lynx", "highliner"),
}


def _build_roster(base: list[dict], aliases: dict[str, tuple[str, ...]]) -> tuple[ShipSpec, ...]:
    """Merge the generated base (`{id, name, ed_symbol}` rows) with the curated alias overlay.
    FAIL LOUD on an orphaned overlay row (an alias id the base doesn't define) — the same
    regen-time contract `regen_engineering_data.py` uses, so drift is a build error not a silent
    gap. Base order is preserved so resolution behaviour is stable across regens."""
    ids = {row["id"] for row in base}
    orphans = sorted(set(aliases) - ids)
    if orphans:
        raise ValueError(f"ship alias overlay references unknown id(s): {orphans} — regenerate "
                         "covas/nav/data/ship_roster.json or fix _ALIASES")
    return tuple(
        ShipSpec(row["id"], row["name"], row.get("ed_symbol", ""), tuple(aliases.get(row["id"], ())))
        for row in base
    )


def _load_base() -> list[dict]:
    """The generated roster base (offline; read once at import — the blueprints.py pattern)."""
    return json.loads((_DATA / "ship_roster.json").read_text(encoding="utf-8"))


ROSTER: tuple[ShipSpec, ...] = _build_roster(_load_base(), _ALIASES)


# ---- genuine ship families (ask, don't guess) ---------------------------------------------
# A bare family word maps to several ships that differ in a way the Commander must pick. Keyed
# by the normalized family root (and its mishears); the value is the ordered ship ids to offer.

_FAMILIES: dict[str, tuple[str, ...]] = {
    "krait": ("krait_mk2", "krait_phantom"),
    "kraite": ("krait_mk2", "krait_phantom"),          # common mishear
    "crate": ("krait_mk2", "krait_phantom"),           # Whisper renders "krait" as "crate"
    "cobra": ("cobra_mk3", "cobra_mk4", "cobra_mk5"),
    "kobra": ("cobra_mk3", "cobra_mk4", "cobra_mk5"),
    "viper": ("viper_mk3", "viper_mk4"),
    "asp": ("asp_explorer", "asp_scout"),
    "diamondback": ("diamondback_explorer", "diamondback_scout"),
    "diamond back": ("diamondback_explorer", "diamondback_scout"),
    "type": ("type_6", "type_7", "type_8", "type_9", "type_10", "type_11"),
}


# ---- normalization + lookup ---------------------------------------------------------------

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _norm(text: str) -> str:
    """Fold a spoken/typed name to a comparison key: lowercase, drop punctuation/spaces so
    'Krait MkII', 'krait mk2' and 'kraitmkii' all collapse to 'kraitmkii'."""
    return _NON_ALNUM.sub("", str(text).lower())


def _build_lookup() -> dict[str, ShipSpec]:
    """Normalized key -> spec. Names first so a name always wins an alias collision; aliases
    fill in with setdefault."""
    lut: dict[str, ShipSpec] = {}
    for spec in ROSTER:
        lut[_norm(spec.name)] = spec
    for spec in ROSTER:
        for alias in spec.aliases:
            lut.setdefault(_norm(alias), spec)
    return lut


_LOOKUP = _build_lookup()
_BY_ID = {spec.id: spec for spec in ROSTER}
_FAMILY_LOOKUP = {_norm(k): v for k, v in _FAMILIES.items()}

# Every canonical ship name, for the help subsystem's failure-recovery vocabulary.
SHIP_NAMES: tuple[str, ...] = tuple(spec.name for spec in ROSTER)

# A short, friendly starter list for the Unknown case (common asks across roles).
_COMMON = ("Anaconda", "Python", "Krait MkII", "Asp Explorer", "Cobra MkIII", "Vulture")


# ---- resolve ------------------------------------------------------------------------------

def _resolved(spec: ShipSpec) -> ResolvedShip:
    return ResolvedShip(id=spec.id, name=spec.name, symbol=spec.symbol)


def _family(ids: tuple[str, ...]) -> AmbiguousShip:
    return AmbiguousShip(candidates=[_BY_ID[i].name for i in ids if i in _BY_ID])


def _lookup_with_extras(extra_names) -> dict[str, ShipSpec]:
    """The bundled name/alias lookup, plus a synthetic spec for each extra canonical name (a
    hull Spansh knows that the bundle doesn't yet — see `ship_index.py`). Extras contribute
    their exact name only; aliases/families stay curated. Bundled names always win a collision.
    Returns `_LOOKUP` unchanged (no copy) when there are no genuinely-new extras."""
    fresh = {}
    for name in extra_names or ():
        key = _norm(name)
        if key and key not in _LOOKUP and key not in fresh:
            fresh[key] = ShipSpec(id=f"live:{key}", name=str(name))
    return {**_LOOKUP, **fresh} if fresh else _LOOKUP


def resolve_ship(query: str, *, extra_names=()) -> ResolvedShip | AmbiguousShip | UnknownShip:
    """Map a loose ship name to a structured outcome. Pure and offline — the LLM drives the
    dialog and re-calls this with a more-specific name after asking about a family.

    `extra_names` are canonical ship names Spansh knows that the bundled roster is missing
    (newly-released hulls, from the live `ShipIndex`); they're folded into the lookup so a brand
    -new ship resolves by exact/containment/fuzzy match. Default empty -> pure bundled behavior.

    Matching, most-confident first:
      1. exact normalized match on a name or alias,
      2. a genuine FAMILY root (bare 'krait'/'cobra'/'asp'/'type'/…) -> Ambiguous (ask),
      3. substring containment (query in a name, or a name in the query),
      4. fuzzy (difflib) against names + aliases.
    A single hit -> Resolved; several distinct -> Ambiguous; none -> Unknown(suggestions)."""
    q = _norm(query)
    if not q:
        return UnknownShip(query=str(query), suggestions=list(_COMMON))

    lookup = _lookup_with_extras(extra_names)

    # 1. exact — a full name or a discriminating alias ("krait phantom", "type9", "fdl").
    if q in lookup:
        return _resolved(lookup[q])

    # 2. family — a bare root the Commander must narrow. Checked before containment so
    #    "krait" asks (MkII vs Phantom) instead of accidentally resolving.
    if q in _FAMILY_LOOKUP:
        return _family(_FAMILY_LOOKUP[q])

    # 3. containment — the query sits inside a name/alias, or a name inside the query
    #    ("the closest anaconda please" contains "anaconda"). Dedupe to distinct specs.
    hits: list[ShipSpec] = []
    seen: set[str] = set()
    for key, spec in lookup.items():
        if len(key) < 3:
            continue
        if (q in key or key in q) and spec.id not in seen:
            seen.add(spec.id)
            hits.append(spec)
    if len(hits) == 1:
        return _resolved(hits[0])
    if len(hits) > 1:
        return AmbiguousShip(candidates=_unique_names(hits))

    # 4. fuzzy — catches Whisper mishears the aliases don't cover.
    close = difflib.get_close_matches(q, list(lookup), n=6, cutoff=0.72)
    specs: list[ShipSpec] = []
    seen.clear()
    for key in close:
        spec = lookup[key]
        if spec.id not in seen:
            seen.add(spec.id)
            specs.append(spec)
    if len(specs) == 1:
        return _resolved(specs[0])
    if len(specs) > 1:
        return AmbiguousShip(candidates=_unique_names(specs))

    return UnknownShip(query=str(query), suggestions=_suggest(q, lookup))


def _unique_names(specs: list[ShipSpec]) -> list[str]:
    """Distinct ship names, capped so a spoken "did you mean…" stays short."""
    return [s.name for s in specs][:8]


def _suggest(q: str, lookup: dict[str, ShipSpec] | None = None) -> list[str]:
    """Best-effort near names for an Unknown, else a few common ships. Suggests over the given
    lookup (bundled + any live extras) so a near-miss on a new hull can still be offered."""
    lut = lookup if lookup is not None else _LOOKUP
    by_name = {_norm(s.name): s.name for s in lut.values()}
    close = difflib.get_close_matches(q, list(by_name), n=3, cutoff=0.5)
    names = [by_name[c] for c in close]
    return names or list(_COMMON[:4])
