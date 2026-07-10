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

Bodies/planets are OUT OF SCOPE for now — `BODIES` is a clearly-marked, unimplemented seam.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .spansh import (BODIES_URL, STATIONS_URL, SYSTEMS_URL, distance_sort, is_fleet_carrier,
                     largest_pad, pad_filter_key)

# Spansh filter "kinds" — how a slot value is rendered into the request. Each maps a plain
# Python value (what a capability fills a slot with) to the structured shape Spansh accepts.
_KINDS = frozenset({"enum", "range", "bool", "pad"})


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
      * `result_kind` — "station" | "system", selects the parser.
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
        lo, hi = _range_bounds(value)
        return {param.name: {"min": str(lo), "max": str(hi)}}
    if param.kind == "pad":
        key = pad_filter_key(value)
        return {key: {"value": True}} if key else {}
    raise ValueError(f"unrenderable param kind {param.kind!r}")  # unreachable (ParamSpec guards)


def _range_bounds(value):
    """Accept a range as {"min","max"} or a (min, max)/[min, max] pair — else raise."""
    if isinstance(value, dict):
        return value.get("min"), value.get("max")
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return value[0], value[1]
    raise ValueError(f"range value must be a 'min/max' dict or a (min, max) pair, got {value!r}")


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


def parse_stations(results: list[dict]) -> list[StationRecord]:
    """Map raw Spansh station results to `StationRecord`s, dropping transient fleet carriers
    (they jump, so they're a poor 'nearest station' answer)."""
    out: list[StationRecord] = []
    for r in results:
        if is_fleet_carrier(r):
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


def parse_results(spec: CategorySpec, results: list[dict]):
    """Parse raw results per the category's `result_kind`."""
    if spec.result_kind == "system":
        return parse_systems(results)
    if spec.result_kind == "station":
        return parse_stations(results)
    raise ValueError(f"category '{spec.key}' has no parser for result_kind {spec.result_kind!r}")


# ---- the six categories (+ bodies seam) ---------------------------------------------------
# Every `ParamSpec.name` below is a Spansh filter key confirmed accepted against the live API
# (2026-07): an unknown key is silently ignored by Spansh, so these were verified by observing
# a filter actually narrow results, not by trusting the docs.

_STATION_SUBJECT = ("the station database", "station lookup")
_SYSTEM_SUBJECT = ("the systems database", "system lookup")


def _stations_spec() -> CategorySpec:
    """Nearest station by services / type / pad / faction (NOT module — that's outfitting)."""
    return CategorySpec(
        key="stations", endpoint=STATIONS_URL, result_kind="station",
        subject=_STATION_SUBJECT[0], lookup_name=_STATION_SUBJECT[1],
        params=(
            ParamSpec("type", "enum"),                       # Coriolis Starport, Outpost, …
            ParamSpec("services", "enum"),                   # Outfitting, Shipyard, …
            ParamSpec("economy", "enum"),
            ParamSpec("allegiance", "enum"),
            ParamSpec("government", "enum"),
            ParamSpec("controlling_minor_faction", "enum"),
            ParamSpec("has_large_pad", "pad"),               # rendered from a pad size (S/M/L)
            ParamSpec("distance_to_arrival", "range"),
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
        ),
    )


def _minor_factions_spec() -> CategorySpec:
    """Nearest system by minor-faction presence: controls vs is-present, allegiance,
    government, and the faction's active state."""
    return CategorySpec(
        key="minor_factions", endpoint=SYSTEMS_URL, result_kind="system",
        subject=_SYSTEM_SUBJECT[0], lookup_name=_SYSTEM_SUBJECT[1],
        params=(
            ParamSpec("controlling_minor_faction", "enum"),  # "controls"
            ParamSpec("minor_faction_presences", "enum"),    # "is present"
            ParamSpec("allegiance", "enum"),
            ParamSpec("government", "enum"),
            ParamSpec("state", "enum"),
        ),
    )


def _signals_spec() -> CategorySpec:
    """Nearest signal source (beacon / installation / …) — mapped to the station `type`
    filter, the Spansh key that carries these signal-source structures."""
    return CategorySpec(
        key="signals", endpoint=STATIONS_URL, result_kind="station",
        subject=_STATION_SUBJECT[0], lookup_name=_STATION_SUBJECT[1],
        params=(
            ParamSpec("type", "enum"),                       # Nav Beacon, Installation, …
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
            ParamSpec("state", "enum"),
            ParamSpec("controlling_minor_faction", "enum"),
            ParamSpec("minor_faction_presences", "enum"),
            ParamSpec("power_state", "enum"),
            ParamSpec("allegiance", "enum"),
        ),
    )


# Bodies/planets: intentionally UNIMPLEMENTED (out of scope, Search Prompt 3). Left as a seam
# so the endpoint + result_kind are recorded and a future prompt fills in params + a parser;
# `build_query`/`build_filters` refuse it until then.
BODIES = CategorySpec(
    key="bodies", endpoint=BODIES_URL, result_kind="body", implemented=False,
    subject="the bodies database", lookup_name="body lookup",
)


CATEGORIES: dict[str, CategorySpec] = {
    spec.key: spec for spec in (
        _stations_spec(),
        _outfitting_spec(),
        _star_systems_spec(),
        _minor_factions_spec(),
        _signals_spec(),
        _misc_spec(),
    )
}


def category(key: str) -> CategorySpec:
    """The `CategorySpec` for a category key. Raises KeyError for the bodies seam / unknowns —
    callers must go through `CATEGORIES` or `BODIES` deliberately."""
    return CATEGORIES[str(key).strip().lower()]
