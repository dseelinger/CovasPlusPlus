"""Per-category Spansh query builders + result parsers, over the shared transport.

Each of the six in-scope voice-search categories is one `CategorySpec`: which Spansh
endpoint it POSTs to and the exact set of Spansh filter parameters it ACCEPTS. A category's
accepted-param set is the single source of truth the help registry's slots must line up
with (Search Prompt 2: `slot.param` IS the canonical Spansh param name), which is what keeps
the two from drifting — and `build_query` FAILS LOUD (`UnknownParamError`) on any param a
category doesn't accept. That loudness matters because Spansh itself silently ignores an
unknown filter key (see `spansh.py`), so a typo or a drifted slot would otherwise widen a
search with no signal at all.

Param values are validated for STRUCTURE, not vocabulary, here — turning a slot into the
`{"value": [...]}` / `{"min","max"}` / `{"value": bool}` shape Spansh requires. Validating a
value against the real Spansh vocabulary ("is 'Feudal' a government?") belongs to the
capability, before it ever calls this (the LLM-native categories in Search Prompts 4–5).

Outfitting is the ONE category with a bespoke builder/parser: it reuses the module sub-filter
+ mount post-filter already proven in `nav/closest.py` (that file's `build_payload` /
`find_closest_module`), so its `CategorySpec` records the endpoint + params for the registry
but delegates the actual request to closest.py.

Bodies/planets are the SEVENTH category (issue #68, the body / bio-geo signal finder): the
`BODIES` spec below binds the `bodies/search` endpoint with its verified filter params, and
`parse_bodies` turns a raw body into a `BodyRecord`. Its enum/bool/range filters render through
the same generic machinery as every other category — the only body-specific piece is the parser.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .spansh import (BODIES_URL, STATIONS_URL, SYSTEMS_URL, distance_sort, is_fleet_carrier,
                     largest_pad, pad_filter_key)

# Spansh filter "kinds" — how a slot value is rendered into the request. Each maps a plain
# Python value (what a capability fills a slot with) to the structured shape Spansh accepts.
#   enum     -> {"value": [strings]}
#   bool     -> {"value": true|false}
#   range    -> {"value": N | [lo, hi], "comparison": op}   (Spansh's numeric shape; see below)
#   services -> [{"name": s}, ...]                           (station services list)
#   pad      -> the has_<size>_pad boolean, from a pad size (S/M/L)
_KINDS = frozenset({"enum", "range", "bool", "pad", "services"})


class UnknownParamError(ValueError):
    """A param not in a category's accepted Spansh set was passed to `build_query`. Raised
    LOUD on purpose: Spansh silently ignores unknown filter keys, so this is the only place a
    drifted/typo'd param surfaces. Keeps the help registry and the real query in lockstep."""


@dataclass(frozen=True)
class ParamSpec:
    """One accepted Spansh filter parameter for a category.

      * `name` — the EXACT Spansh filter key (and the canonical registry `slot.param`).
      * `kind` — how a slot value renders into the request (see `_KINDS`).
    """
    name: str
    kind: str

    def __post_init__(self) -> None:
        if self.kind not in _KINDS:
            raise ValueError(f"unknown param kind {self.kind!r} for {self.name!r}")


@dataclass(frozen=True)
class CategorySpec:
    """A voice-search category's Spansh binding: endpoint, accepted params, result shape.

      * `key`         — our internal category key ("star_systems", "outfitting", …).
      * `endpoint`    — the Spansh /search URL it POSTs to.
      * `params`      — the accepted filter params; `build_query` rejects anything else.
      * `result_kind` — "station" | "system" | "body", selects the parser.
      * `subject` / `lookup_name` — spoken wording for `execute_search`'s error lines.
      * `bespoke`     — True for outfitting: request/parse live in `nav/closest.py`, so
                        `build_query` here refuses (call closest.py instead).
      * `implemented` — False for the bodies seam.
    """
    key: str
    endpoint: str
    result_kind: str
    params: tuple[ParamSpec, ...] = ()
    subject: str = "the galaxy database"
    lookup_name: str = "search"
    bespoke: bool = False
    implemented: bool = True

    def param_names(self) -> tuple[str, ...]:
        return tuple(p.name for p in self.params)

    def param(self, name: str) -> ParamSpec | None:
        want = str(name).strip().lower()
        for p in self.params:
            if p.name.lower() == want:
                return p
        return None

    def validate_params(self, names) -> None:
        """Raise `UnknownParamError` if any of `names` isn't an accepted Spansh param for this
        category. The loud guard that keeps a slot from silently widening a Spansh search."""
        accepted = {p.name.lower() for p in self.params}
        unknown = [str(n) for n in names if str(n).strip().lower() not in accepted]
        if unknown:
            raise UnknownParamError(
                f"category '{self.key}' does not accept param(s) {sorted(unknown)}; "
                f"accepted: {sorted(self.param_names())}")


# ---- filter rendering (pure) --------------------------------------------------------------

def _render(param: ParamSpec, value) -> dict:
    """Turn one (accepted) slot value into its Spansh filter fragment. Structure only — the
    caller has already validated the value against the real vocabulary."""
    if param.kind == "enum":
        vals = value if isinstance(value, (list, tuple)) else [value]
        return {param.name: {"value": [str(v) for v in vals]}}
    if param.kind == "bool":
        return {param.name: {"value": bool(value)}}
    if param.kind == "range":
        frag = _numeric_filter(*_range_bounds(value))
        return {param.name: frag} if frag else {}
    if param.kind == "services":
        vals = value if isinstance(value, (list, tuple)) else [value]
        return {param.name: [{"name": str(v)} for v in vals]}
    if param.kind == "pad":
        key = pad_filter_key(value)
        return {key: {"value": True}} if key else {}
    raise ValueError(f"unrenderable param kind {param.kind!r}")  # unreachable (ParamSpec guards)


def _range_bounds(value):
    """Accept a range as {"min","max"}, a (min, max)/[min, max] pair, or a bare scalar (a
    lower bound). Either bound may be None (a one-sided range)."""
    if isinstance(value, dict):
        return value.get("min"), value.get("max")
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return value[0], value[1]
    if isinstance(value, (int, float)):
        return value, None
    raise ValueError(f"range value must be a 'min/max' dict, a (min, max) pair, or a number, "
                     f"got {value!r}")


def _numeric_filter(lo, hi) -> dict:
    """Spansh's numeric-filter shape — `{"value": N, "comparison": op}`, NOT `{min,max}`
    (that form is silently ignored). Both bounds -> an inclusive range ('<=>'); one bound -> a
    one-sided comparison; neither -> empty (the slot renders to nothing)."""
    if lo is not None and hi is not None:
        return {"value": [_num(lo), _num(hi)], "comparison": "<=>"}
    if lo is not None:
        return {"value": _num(lo), "comparison": ">="}
    if hi is not None:
        return {"value": _num(hi), "comparison": "<="}
    return {}


def _num(x):
    """A JSON number for Spansh: an int when integral (populations, light-seconds), else float."""
    f = float(x)
    return int(f) if f.is_integer() else f


def build_filters(spec: CategorySpec, slots: dict) -> dict:
    """The Spansh `filters` object for `slots` under `spec`. Rejects unknown params LOUD;
    skips slots whose value is None (unset)."""
    if not spec.implemented:
        raise NotImplementedError(f"category '{spec.key}' is a seam and not implemented yet")
    if spec.bespoke:
        raise NotImplementedError(
            f"category '{spec.key}' builds its request in nav/closest.py, not here")
    spec.validate_params(slots.keys())
    filters: dict = {}
    for name, value in slots.items():
        if value is None:
            continue
        param = spec.param(name)
        assert param is not None  # validate_params guarantees it
        filters.update(_render(param, value))
    return filters


def build_query(spec: CategorySpec, slots: dict, reference_system: str, *,
                size: int = 50, page: int = 0) -> dict:
    """A complete Spansh request body for a category: validated filters, nearest-first sort,
    and the reference system results are measured from. Fails LOUD on an unknown param."""
    return {
        "filters": build_filters(spec, slots),
        "sort": distance_sort(),
        "size": int(size),
        "page": int(page),
        "reference_system": reference_system,
    }


# ---- typed result records + parsers -------------------------------------------------------

@dataclass(frozen=True)
class SystemRecord:
    """A star system result. `power` is a tuple because ED Powerplay 2.0 lists several powers
    contesting one system. `extra` carries softer fields a spoken line may mention."""
    name: str
    distance_ly: float
    allegiance: str | None = None
    government: str | None = None
    primary_economy: str | None = None
    security: str | None = None
    population: int | None = None
    power: tuple[str, ...] = ()
    power_state: str | None = None
    controlling_minor_faction: str | None = None
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class StationRecord:
    """A station result (the generic station/signals shape; outfitting keeps its own richer
    `ClosestResult`). `pad` is the largest landing pad the station has."""
    system: str
    station: str
    distance_ly: float
    pad: str | None = None
    type: str | None = None
    controlling_minor_faction: str | None = None
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class BodyRecord:
    """A body (planet/moon) result from `bodies/search`. `distance_ly` is from the reference
    system; `distance_to_arrival_ls` is in-system light-seconds from the main star. `signals`
    maps a signal category ("Biological", "Geological", "Human", …) to its count, and `landmarks`
    lists the distinct surface-feature subtypes present (the exobiology species live here), so a
    spoken line can confirm the biological signal the Commander asked for."""
    name: str
    system: str
    distance_ly: float
    subtype: str | None = None
    distance_to_arrival_ls: float | None = None
    is_landable: bool = False
    terraforming_state: str | None = None
    atmosphere: str | None = None
    signals: dict = field(default_factory=dict)
    landmarks: tuple[str, ...] = ()
    extra: dict = field(default_factory=dict)


def _f(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_systems(results: list[dict]) -> list[SystemRecord]:
    """Map raw Spansh system results (already distance-sorted) to `SystemRecord`s."""
    out: list[SystemRecord] = []
    for r in results:
        power = r.get("power")
        powers = tuple(str(p) for p in power) if isinstance(power, list) else (
            (str(power),) if power else ())
        out.append(SystemRecord(
            name=r.get("name") or "an unknown system",
            distance_ly=_f(r.get("distance")),
            allegiance=r.get("allegiance"),
            government=r.get("government"),
            primary_economy=r.get("primary_economy"),
            security=r.get("security"),
            population=r.get("population"),
            power=powers,
            power_state=r.get("power_state"),
            controlling_minor_faction=r.get("controlling_minor_faction"),
            extra={"state": r.get("state")} if r.get("state") else {},
        ))
    return out


def parse_stations(results: list[dict], *, include_carriers: bool = False) -> list[StationRecord]:
    """Map raw Spansh station results to `StationRecord`s. Fleet carriers are dropped by
    default (they jump, so they're a poor 'nearest station' answer); a caller that genuinely
    wants them — the stations category includes them unless the Commander says 'no carriers' —
    passes `include_carriers=True`."""
    out: list[StationRecord] = []
    for r in results:
        if not include_carriers and is_fleet_carrier(r):
            continue
        extra = {k: r.get(k) for k in ("distance_to_arrival", "is_planetary") if r.get(k) is not None}
        out.append(StationRecord(
            system=r.get("system_name") or "an unknown system",
            station=r.get("name") or "an unknown station",
            distance_ly=_f(r.get("distance")),
            pad=largest_pad(r),
            type=r.get("type"),
            controlling_minor_faction=r.get("controlling_minor_faction"),
            extra=extra,
        ))
    return out


def _signals_map(raw) -> dict:
    """Spansh's `signals` list ([{"name": "Biological", "count": 3}, …]) as a name->count dict.
    An absent/odd shape yields {} (never raises — a body without signals is normal)."""
    out: dict = {}
    if isinstance(raw, list):
        for s in raw:
            if isinstance(s, dict) and s.get("name"):
                out[str(s["name"])] = s.get("count")
    return out


def _landmark_subtypes(raw) -> tuple[str, ...]:
    """The distinct `subtype`s in a body's `landmarks` list, order preserved. That's where the
    exobiology species show up (a body can list a genus's species many times, once per plotted
    location), so we de-duplicate to the set of feature TYPES present."""
    out: list[str] = []
    if isinstance(raw, list):
        for lm in raw:
            if isinstance(lm, dict):
                sub = lm.get("subtype")
                if sub and str(sub) not in out:
                    out.append(str(sub))
    return tuple(out)


def parse_bodies(results: list[dict]) -> list[BodyRecord]:
    """Map raw Spansh body results (already distance-sorted) to `BodyRecord`s."""
    out: list[BodyRecord] = []
    for r in results:
        arrival = r.get("distance_to_arrival")
        out.append(BodyRecord(
            name=r.get("name") or "an unnamed body",
            system=r.get("system_name") or "an unknown system",
            distance_ly=_f(r.get("distance")),
            subtype=r.get("subtype"),
            distance_to_arrival_ls=_f(arrival) if arrival is not None else None,
            is_landable=bool(r.get("is_landable")),
            terraforming_state=r.get("terraforming_state"),
            atmosphere=r.get("atmosphere"),
            signals=_signals_map(r.get("signals")),
            landmarks=_landmark_subtypes(r.get("landmarks")),
            # `signals_updated_at` dates the crowdsourced signal/landmark data (the volatile part
            # of a body record — its structure doesn't change); kept for a spoken age caveat on a
            # bio-signal search. `updated_at` is the record's last touch as a fallback.
            extra={k: r.get(k) for k in ("signals_updated_at", "updated_at")
                   if r.get(k) is not None},
        ))
    return out


def parse_results(spec: CategorySpec, results: list[dict]):
    """Parse raw results per the category's `result_kind`."""
    if spec.result_kind == "system":
        return parse_systems(results)
    if spec.result_kind == "station":
        return parse_stations(results)
    if spec.result_kind == "body":
        return parse_bodies(results)
    raise ValueError(f"category '{spec.key}' has no parser for result_kind {spec.result_kind!r}")


# ---- the six categories (+ bodies seam) ---------------------------------------------------
# Every `ParamSpec.name` below is a Spansh filter key confirmed accepted against the live API
# (2026-07): an unknown key is silently ignored by Spansh, so these were verified by observing
# a filter actually narrow results, not by trusting the docs.

_STATION_SUBJECT = ("the station database", "station lookup")
_SYSTEM_SUBJECT = ("the systems database", "system lookup")


def _stations_spec() -> CategorySpec:
    """Nearest station by services / type / pad / distance / faction (NOT module — that's
    outfitting). Params verified accepted against the live API; note `services` is a
    list-of-objects filter and `distance_to_arrival` a numeric comparison, not min/max."""
    return CategorySpec(
        key="stations", endpoint=STATIONS_URL, result_kind="station",
        subject=_STATION_SUBJECT[0], lookup_name=_STATION_SUBJECT[1],
        params=(
            ParamSpec("type", "enum"),                       # Coriolis Starport, Outpost, …
            ParamSpec("services", "services"),               # [{"name": "Shipyard"}, …]
            ParamSpec("controlling_minor_faction", "enum"),  # free-text faction name
            ParamSpec("has_large_pad", "pad"),               # rendered from a pad size (S/M/L)
            ParamSpec("distance_to_arrival", "range"),       # light-seconds from the star
        ),
    )


def _outfitting_spec() -> CategorySpec:
    """Nearest station SELLING a module — bespoke request/parse in nav/closest.py. The params
    here are the registry-facing conversational slots; `size`/`mount` fold into the module
    resolution + a client-side post-filter, not raw Spansh filter keys."""
    return CategorySpec(
        key="outfitting", endpoint=STATIONS_URL, result_kind="station", bespoke=True,
        subject=_STATION_SUBJECT[0], lookup_name=_STATION_SUBJECT[1],
        params=(
            ParamSpec("module", "enum"),
            ParamSpec("has_large_pad", "pad"),
        ),
    )


def _star_systems_spec() -> CategorySpec:
    """Nearest star system by allegiance / government / economy / security / population /
    Powerplay. The LLM-native reference category (Search Prompt 4)."""
    return CategorySpec(
        key="star_systems", endpoint=SYSTEMS_URL, result_kind="system",
        subject=_SYSTEM_SUBJECT[0], lookup_name=_SYSTEM_SUBJECT[1],
        params=(
            ParamSpec("allegiance", "enum"),
            ParamSpec("government", "enum"),
            ParamSpec("primary_economy", "enum"),
            ParamSpec("secondary_economy", "enum"),
            ParamSpec("security", "enum"),
            ParamSpec("population", "range"),
            ParamSpec("power", "enum"),
            ParamSpec("power_state", "enum"),
            ParamSpec("needs_permit", "bool"),
            ParamSpec("is_colonised", "bool"),        # colonization: already colonised
            ParamSpec("is_being_colonised", "bool"),  # colonization: open / in progress
        ),
    )


def _minor_factions_spec() -> CategorySpec:
    """Nearest system by minor-faction presence: controls vs is-present, allegiance,
    government, and the faction's active state."""
    return CategorySpec(
        key="minor_factions", endpoint=SYSTEMS_URL, result_kind="system",
        subject=_SYSTEM_SUBJECT[0], lookup_name=_SYSTEM_SUBJECT[1],
        params=(
            ParamSpec("controlling_minor_faction", "enum"),   # "controls" (free-text name)
            ParamSpec("minor_faction_presences", "enum"),     # "is present" (free-text name)
            ParamSpec("allegiance", "enum"),
            ParamSpec("government", "enum"),
            ParamSpec("controlling_minor_faction_state", "enum"),  # War, Boom, Election, …
        ),
    )


def _signals_spec() -> CategorySpec:
    """Nearest dockable STRUCTURE by type (megaship / settlement / …) — the station `type`
    filter. Keyed 'signals' for continuity with the category set; there is no separate
    signal-source data in Spansh, so this is structure-by-type."""
    return CategorySpec(
        key="signals", endpoint=STATIONS_URL, result_kind="station",
        subject=_STATION_SUBJECT[0], lookup_name=_STATION_SUBJECT[1],
        params=(
            ParamSpec("type", "enum"),                       # Mega ship, Settlement, Outpost, …
            ParamSpec("has_large_pad", "pad"),
        ),
    )


def _misc_spec() -> CategorySpec:
    """The grab-bag: nearest wars / civil wars (faction `state`), minor-faction presence, and
    Powerplay state — everything that doesn't warrant its own category yet."""
    return CategorySpec(
        key="misc", endpoint=SYSTEMS_URL, result_kind="system",
        subject=_SYSTEM_SUBJECT[0], lookup_name=_SYSTEM_SUBJECT[1],
        params=(
            ParamSpec("controlling_minor_faction_state", "enum"),  # War, Civil War, Boom, …
            ParamSpec("controlling_minor_faction", "enum"),
            ParamSpec("minor_faction_presences", "enum"),
            ParamSpec("power_state", "enum"),
            ParamSpec("allegiance", "enum"),
        ),
    )


# Bodies/planets (issue #68): nearest body by subtype (Earth-like / Ammonia / Water world, gas
# giants, …), biological signal of type X (`landmark_subtype` — the exobiology species), plus
# atmosphere / terraforming / landability / arrival distance. Every param below was verified
# accepted against the live `bodies/search` API (2026-07) by watching the filter NARROW results
# — an unknown key is silently ignored, so this was confirmed empirically, not from docs. NOTE:
# the signal-CATEGORY count filter ("has any Biological signal") is NOT honoured by the API under
# any shape tried, so "biological signals of type X" is served precisely via `landmark_subtype`
# (a genus expands to an OR over its species; see `search/bodies.py`).
_BODY_SUBJECT = ("the bodies database", "body lookup")

BODIES = CategorySpec(
    key="bodies", endpoint=BODIES_URL, result_kind="body",
    subject=_BODY_SUBJECT[0], lookup_name=_BODY_SUBJECT[1],
    params=(
        ParamSpec("subtype", "enum"),             # Earth-like world, Ammonia world, Water world, …
        ParamSpec("landmark_subtype", "enum"),    # exobiology species (bio signals of type X)
        ParamSpec("atmosphere", "enum"),          # Nitrogen, Ammonia, No atmosphere, …
        ParamSpec("terraforming_state", "enum"),  # Terraformable, Terraformed, Not terraformable
        ParamSpec("is_landable", "bool"),
        ParamSpec("distance_to_arrival", "range"),  # light-seconds from the main star
    ),
)


CATEGORIES: dict[str, CategorySpec] = {
    spec.key: spec for spec in (
        _stations_spec(),
        _outfitting_spec(),
        _star_systems_spec(),
        _minor_factions_spec(),
        _signals_spec(),
        _misc_spec(),
        BODIES,
    )
}


def category(key: str) -> CategorySpec:
    """The `CategorySpec` for a category key ("stations", "bodies", …). Raises KeyError for an
    unknown key — callers name a real category deliberately."""
    return CATEGORIES[str(key).strip().lower()]
