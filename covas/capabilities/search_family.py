"""Spec-driven search/nav capability family (issue #111).

Six of the Spansh voice-search categories — star systems, stations, minor factions, signals,
faction states (misc), and bodies — were twelve-trenchcoats-on-one-feature: each a ~200-line
module repeating the same skeleton (`_TOOL_NAME`/`_DESC`/`_SCHEMA_PROPS`, a `tools()`, a
`help_meta()`, a `run_tool()` funnelling into the shared `_search_support` pipeline) around a
`CategorySpec` that already described the domain as data. This module collapses those six into
ONE generic `SpecSearchCapability` parameterised by a per-category `SearchDescriptor` (tool
name, description, schema props, the `CategorySpec` key, help metadata, and one `run` callable
carrying the parts that genuinely differ — slot validation and the spoken result). A declarative
table (`SEARCH_GROUP`) instantiates them; `bootstrap` loops over it.

The six are the "thin" tier of issue #111. The BESPOKE tier stays standalone by design — the two
find-closest tools (`find_closest_capability`, module + ship: `nav/closest.py`, a resolve/confirm
dialog, stateful turn-gate) and the four planners (`route_plan_capability`, the async
`search/routes.py` submit+poll + galaxy-map handoff; `mining_helper_capability`, hotspot + sell +
checklist) don't ride the synchronous `build_query`/`execute_search` slot-search pipeline this
class is built on, so forcing them through it would be dishonest, not a simplification.

The LLM- and help-facing surface is FROZEN byte-for-byte (`tests/test_search_family_snapshot.py`):
each category's exact `tools()` JSON, `help_meta()`, and `help_vocabulary()` are unchanged from the
pre-collapse modules — the descriptors below carry those strings verbatim.

Every seam (`http`, `get_current_system`, `clipboard`, `factions`) is injected so the default
`pytest` run is offline (DESIGN §9); fail soft throughout — a bad value or a failed lookup is
spoken, never raised.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from ..nav import copy as _default_copy
from ..search import NavError, RequestsHttp, category, parse_bodies, parse_stations, parse_systems
from ..search.bodies import (
    BIO_GENUS_NAMES,
    BODY_SUBTYPES,
    nearest_bio_signal,
    nearest_subtype,
    resolve_bio_signal,
    resolve_subtype,
)
from ..search.faction_index import FactionIndex
from ..search.factions import FACTION_STATES, nearest_state, resolve_state
from ..search.spansh import Http, data_age_days, pad_filter_key
from ..search.stations import (
    SERVICES,
    STATION_TYPES,
    nearest_service,
    nearest_type,
    resolve_service,
    resolve_type,
)
from ..search.systems import VOCAB, nearest_enum, resolve_enum
from . import _search_support as sup
from ._search_support import SearchConfig
from .base import HelpMeta, Slot


@dataclass(frozen=True)
class SearchDescriptor:
    """One search category rendered as data. `run(cap, inp)` is the per-category flow (slot
    validation + spoken result) — the genuinely-varying part; everything else (tools()/help/
    run_tool wrapper/reference-system/query/deliver) is shared by `SpecSearchCapability`.

      * `tool_name` / `description` / `schema_props` / `required` — the FROZEN tool schema.
      * `category_key` — the `CategorySpec` key (`categories.category(...)`).
      * `error_label` — "<label> error: {e}" for the fail-soft guard.
      * `help_meta` / `help_vocabulary` — the FROZEN help surface (help_vocabulary may be None).
    """
    tool_name: str
    description: str
    schema_props: Mapping[str, dict]
    required: tuple[str, ...]
    category_key: str
    error_label: str
    help_meta: HelpMeta
    help_vocabulary: Mapping[str, list] | None
    run: Callable[[SpecSearchCapability, dict], str]


class SpecSearchCapability:
    """A Spansh voice-search category, generic over its `SearchDescriptor`. Stateless slot-fill ->
    validate -> search, like the star-systems reference: the conversation IS the state, the model
    fills slots and refines by re-calling, no classifier / state machine.

    Injected seams keep the default test run offline: `http` (Spansh poster), `get_current_system`
    (Callable[[], str|None]), `clipboard`, and `factions` (the shared faction-name index, used only
    by the faction-taking categories)."""
    # Tiering group (issue #84): the token-budget cluster this capability's tools belong to; the
    # level filter (covas/tiering.py) keeps or drops the whole group as a unit.
    TIERING_GROUP = "search"

    # The reference-system miss is worded identically for every category.
    NO_SYSTEM = ("I don't know your current system yet — is Elite Dangerous running with "
                 "monitoring on? Jump somewhere, or tell me a system to search near.")

    def __init__(self, descriptor: SearchDescriptor, config: SearchConfig, *,
                 http: Http | None = None,
                 get_current_system: Callable[[], str | None] | None = None,
                 clipboard: Callable[[str], None] = _default_copy,
                 factions: FactionIndex | None = None,
                 log: Callable[[str], None] | None = None) -> None:
        self._d = descriptor
        self._cfg = config
        self._http = http if http is not None else RequestsHttp()
        self._current_system = get_current_system
        self._clipboard = clipboard
        self._factions = factions if factions is not None else FactionIndex()
        self._log = log
        self._spec = category(descriptor.category_key)
        # Built once: tools_for_level() re-reads this every LLM turn, and the schema is
        # immutable per instance (the find-closest pattern).
        self._tool = {"name": descriptor.tool_name, "description": descriptor.description,
                      "input_schema": {"type": "object",
                                       "properties": dict(descriptor.schema_props),
                                       "required": list(descriptor.required)}}

    # -- capability interface ---------------------------------------------------------
    def tools(self) -> list[dict]:
        return [self._tool]

    def help_meta(self) -> HelpMeta:
        return self._d.help_meta

    def help_vocabulary(self) -> dict[str, list[str]]:
        return {k: list(v) for k, v in (self._d.help_vocabulary or {}).items()}

    def run_tool(self, name: str, inp: dict) -> str:
        if name != self._d.tool_name:
            return f"Unknown tool: {name}"
        try:
            return self._d.run(self, inp)
        except Exception as e:  # noqa: BLE001 — the voice loop must survive any tool error
            self.logline(f"error: {e}")
            return f"{self._d.error_label} error: {e}"

    # -- shared pipeline steps the per-category run() functions call ------------------
    def reference(self, inp: dict) -> str | None:
        """The system to measure from: a spoken `near` override, else the current system."""
        return sup.reference_system(self._current_system, inp)

    def query(self, slots: dict, system: str) -> list[dict]:
        """Build + POST the category query (plain, no freshness). Raises `NavError`."""
        return sup.run_query(self._spec, slots, self._http, system,
                             user_agent=self._cfg.user_agent, size=self._cfg.search_size)

    def query_fresh(self, slots: dict, system: str, fresh_field: str) -> tuple[list[dict], float | None]:
        """`query` with the staleness policy for VOLATILE facts (BGS states tick daily): constrain
        `fresh_field` server-side, and fall back to stale data (with the age) when nothing fresh
        matches. Returns `(results, stale_age_days)`."""
        return sup.run_query_fresh(self._spec, slots, self._http, system,
                                   user_agent=self._cfg.user_agent, size=self._cfg.search_size,
                                   fresh_field=fresh_field)

    def deliver(self, name: str, distance_ly: float, *,
                reference_is_current: bool = True) -> tuple[bool, bool]:
        """Copy the result system to the clipboard unless the Commander is already at it (the N3
        already-here rule). `reference_is_current` is False when a `near X` override measured the
        search from X rather than from where the Commander is, so a distance-0 match is the
        override target (worth copying), not "you're already there". Returns
        `(copied, already_here)`."""
        return sup.deliver_system(self._clipboard, name, distance_ly, self._log,
                                  reference_is_current=reference_is_current)

    def deliver_logged(self, name: str, distance_ly: float, detail: str, *,
                       reference_is_current: bool = True) -> tuple[bool, bool]:
        """`deliver` plus the standard result log line — `<detail>, clipboard=<here|ok|failed>`.
        `detail` carries the per-category prefix (match name, distance, filters)."""
        copied, here = self.deliver(name, distance_ly,
                                    reference_is_current=reference_is_current)
        self.logline(f"{detail}, "
                     f"clipboard={'here' if here else ('ok' if copied else 'failed')}")
        return copied, here

    def run_search(self, inp: dict, slots: dict, parse: Callable[[list[dict]], list], *,
                   empty: str, fresh_field: str | None = None) -> str | tuple[list, float | None]:
        """The invariant tail every category's run() shares once its slots are validated:
        resolve the reference system (or speak the NO_SYSTEM line), run the query fail-soft
        (a `NavError` is spoken, never raised), parse, and speak `empty` when nothing matched.
        Returns the spoken early-exit string, or `(records, stale_age_days)` on a hit —
        `stale_age_days` is None unless a `fresh_field` search answered from the stale fallback."""
        system = self.reference(inp)
        if not system:
            return self.NO_SYSTEM
        try:
            if fresh_field is not None:
                results, stale_age = self.query_fresh(slots, system, fresh_field)
            else:
                results, stale_age = self.query(slots, system), None
        except NavError as e:
            self.logline(f"search failed: {e}")
            return str(e)
        records = parse(results)
        if not records:
            return empty
        return records, stale_age

    def logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)


def _fill_enum_slots(inp: dict, slots: dict, caught: list[str], rows) -> str | None:
    """Resolve a table of validated slots in order. Each row is `(arg, slot_key, resolve,
    nearest, spoken_kind, caught_fmt)`; the first unresolvable value returns the shared
    recovery line (echoing what WAS caught — no search on an unvalidated value), None means
    every provided value resolved. `caught_fmt` is a `{val}` format for the caught-so-far
    echo, or None to leave the slot out of it."""
    for arg, slot_key, resolve, nearest, kind, fmt in rows:
        raw = inp.get(arg)
        if raw in (None, ""):
            continue
        val = resolve(raw)
        if val is None:
            return sup.recovery(raw, kind, nearest(raw), caught=caught)
        slots[slot_key] = val
        if fmt is not None:
            caught.append(fmt.format(val=val))
    return None


# ======================================================================================
# Star systems — search_star_systems
# ======================================================================================
_SYS_TOOL = "search_star_systems"

# tool arg -> the canonical Spansh enum param it fills. Kept explicit so the arg the model fills,
# the value we validate, and the Spansh filter key are one mapping in one place.
_SYS_ENUM_ARGS = {
    "allegiance": "allegiance",
    "government": "government",
    "economy": "primary_economy",
    "security": "security",
    "power": "power",
    "power_state": "power_state",
}
_SYS_BOOL_ARGS = {
    "needs_permit": "needs_permit",
    "colonised": "is_colonised",
    "being_colonised": "is_being_colonised",
}
# Population is a range: Spansh wants integer-string min/max, so an unspoken bound gets a
# permissive default (0 .. a value comfortably above the most populous real system).
_POP_FLOOR, _POP_CEIL = 0, 1_000_000_000_000

_SYS_DESC = (
    "Find the nearest Elite Dangerous STAR SYSTEM matching what the Commander describes — by "
    "superpower allegiance, government, economy, security, population, Powerplay, or "
    "colonization state — measured from their current system, and copy the system's name to "
    "the clipboard. It is LLM-native and STATELESS; the conversation is the memory:\n"
    "1. Fill only the slots the Commander actually SAID; every unspoken slot means 'Any'. "
    "Normalize loosely (e.g. 'imperial' -> Empire allegiance, 'mining economy' -> Extraction) "
    "and pass your best interpretation.\n"
    "2. If a value is genuinely unclear, ASK — offering a couple of valid options — rather "
    "than guessing. If the tool replies that a value wasn't recognized, relay its suggested "
    "correction and try again.\n"
    "3. You do NOT need a separate confirmation turn: state your interpretation briefly and "
    "search. To REFINE, call again with the accumulated slots plus the new one; for a fresh "
    "search, just call with the new slots. 'Cancel'/'never mind' -> drop it, don't call.\n"
    "4. When the tool returns a match, relay the system, its distance, and the couple of "
    "traits that matter, and ALWAYS tell the Commander the system name was copied to their "
    "clipboard."
)

_SYS_SCHEMA_PROPS = {
    "allegiance": {"type": "string",
                   "description": "Superpower allegiance: Federation, Empire, Alliance, "
                                  "Independent, Guardian, or Thargoid."},
    "government": {"type": "string",
                   "description": "Government type, e.g. Democracy, Corporate, Anarchy, "
                                  "Dictatorship, Theocracy, Feudal."},
    "economy": {"type": "string",
                "description": "Main economy, e.g. Extraction (mining), High Tech, Agriculture, "
                               "Industrial, Refinery, Tourism."},
    "security": {"type": "string", "description": "Security level: High, Medium, Low, Anarchy."},
    "power": {"type": "string",
              "description": "Powerplay power in the system, e.g. Aisling Duval, Edmund Mahon, "
                             "Felicia Winters, Jerome Archer, Archon Delaine."},
    "power_state": {"type": "string",
                    "description": "Powerplay state: Stronghold, Fortified, Exploited, or "
                                   "Unoccupied."},
    "min_population": {"type": "integer",
                       "description": "Minimum system population (e.g. 1000000000 for 'over a "
                                      "billion'). Omit for no lower bound."},
    "max_population": {"type": "integer",
                       "description": "Maximum system population. Omit for no upper bound."},
    "needs_permit": {"type": "boolean",
                     "description": "True to require a permit-locked system, false to exclude "
                                    "one. Omit if the Commander didn't say."},
    "colonised": {"type": "boolean",
                  "description": "True for already-colonised systems, false for uncolonised. "
                                 "Omit if unspoken."},
    "being_colonised": {"type": "boolean",
                        "description": "True for systems currently OPEN for / undergoing "
                                       "colonization. Omit if unspoken."},
    "near": {"type": "string",
             "description": "Reference system to measure from. Omit to use the Commander's "
                            "current system."},
}

_SYS_HELP = HelpMeta(
    category="star systems",
    group="navigation and search",
    one_liner=("I find the nearest star system matching an allegiance, government, "
               "economy, security, population, Powerplay, or colonization state, and "
               "copy its name to your clipboard."),
    example="find the nearest Empire system with high security",
    slots=(
        Slot(param="allegiance",
             phrasings=("an allegiance", "aligned to a superpower"),
             example="the closest Alliance system",
             help_text="Restrict to a superpower — Federation, Empire, Alliance, "
                       "Independent, Guardian, or Thargoid."),
        Slot(param="government",
             phrasings=("a government", "a government type"),
             example="a nearby Democracy system",
             help_text="Restrict to a government type, like Democracy, Corporate, "
                       "Anarchy, or Theocracy."),
        Slot(param="primary_economy",
             phrasings=("an economy", "an economy type"),
             example="the nearest High Tech system",
             help_text="Restrict to a main economy, like Extraction, High Tech, "
                       "Agriculture, or Industrial."),
        Slot(param="security",
             phrasings=("a security level", "how secure"),
             example="a nearby high security system",
             help_text="Restrict by security: High, Medium, Low, or Anarchy."),
        Slot(param="power",
             phrasings=("a Powerplay power", "a power"),
             example="the closest system under Aisling Duval",
             help_text="Restrict to a Powerplay power present in the system, like "
                       "Aisling Duval or Edmund Mahon."),
        Slot(param="power_state",
             phrasings=("a Powerplay state", "a power state"),
             example="the nearest Stronghold system",
             help_text="Restrict by Powerplay state: Stronghold, Fortified, Exploited, "
                       "or Unoccupied."),
        Slot(param="population",
             phrasings=("a population", "how populated"),
             example="a nearby system with over a billion people",
             help_text="Restrict by population — say a minimum or a range, like over a "
                       "billion."),
        Slot(param="needs_permit",
             phrasings=("permit locked", "whether it needs a permit"),
             example="the nearest permit-locked system",
             help_text="Require, or exclude, permit-locked systems."),
        Slot(param="is_colonised",
             phrasings=("colonised or not", "whether it's colonised"),
             example="the closest uncolonised system",
             help_text="Restrict to already-colonised or uncolonised systems."),
        Slot(param="is_being_colonised",
             phrasings=("open for colonization", "being colonised"),
             example="the nearest system open for colonization",
             help_text="Restrict to systems currently open for or undergoing "
                       "colonization."),
    ),
    help_when_active=("Tell me the traits — allegiance, economy, security, and so on — "
                      "and I'll find the nearest system that matches."),
)

_SYS_VOCAB = {
    "allegiance": list(VOCAB["allegiance"]),
    "government": list(VOCAB["government"]),
    "economy": list(VOCAB["primary_economy"]),
    "security": list(VOCAB["security"]),
    "power": list(VOCAB["power"]),
    "power state": list(VOCAB["power_state"]),
}


def _sys_population(inp: dict) -> dict | None:
    lo, hi = inp.get("min_population"), inp.get("max_population")
    if lo is None and hi is None:
        return None
    try:
        lo_i = int(lo) if lo is not None else _POP_FLOOR
        hi_i = int(hi) if hi is not None else _POP_CEIL
    except (TypeError, ValueError):
        return None
    # Normalize the range: clamp to non-negative and swap an inverted min>max (a mis-said
    # "between a billion and a million"). Left unswapped it's an empty interval -> Spansh
    # matches nothing and the Commander hears a misleading "no results", not the real hit.
    lo_i, hi_i = max(lo_i, 0), max(hi_i, 0)
    if lo_i > hi_i:
        lo_i, hi_i = hi_i, lo_i
    return {"min": lo_i, "max": hi_i}


def _run_star_systems(cap: SpecSearchCapability, inp: dict) -> str:
    slots: dict[str, object] = {}
    caught: list[str] = []          # values understood so far, echoed on a later bad slot

    # Enum slots: resolve each spoken value to a canonical Spansh one, or speak a correction
    # and stop (no search on an unvalidated value).
    for arg, param in _SYS_ENUM_ARGS.items():
        raw = inp.get(arg)
        if raw is None or str(raw).strip() == "":
            continue
        value = resolve_enum(param, raw)
        if value is None:
            kind = arg.replace("_", " ")
            return sup.recovery(raw, kind, nearest_enum(param, raw), caught=caught)
        slots[param] = value
        caught.append(f"{value} {arg.replace('_', ' ')}")

    # Boolean slots: only set when actually provided.
    for arg, param in _SYS_BOOL_ARGS.items():
        if inp.get(arg) is not None:
            slots[param] = bool(inp.get(arg))

    # Population range from optional min/max.
    pop = _sys_population(inp)
    if pop is not None:
        slots["population"] = pop

    if not slots:
        return ("Tell me what kind of system to look for — an allegiance, economy, "
                "security level, Powerplay power, and so on.")

    got = cap.run_search(inp, slots, parse_systems,
                         empty=("I couldn't find a system matching that near you — try "
                                "relaxing one of the filters."))
    if isinstance(got, str):
        return got
    records, _ = got
    best = records[0]
    ref_here = not sup.near_override(inp)   # a `near X` search is measured from X, not from here
    copied, here = cap.deliver_logged(best.name, best.distance_ly,
                                      f"nearest match: {best.name} ({best.distance_ly:.1f} ly), "
                                      f"filters={sorted(slots)}", reference_is_current=ref_here)
    traits: list[str] = []
    if best.allegiance:
        traits.append(best.allegiance)
    if best.government and best.government != "None":
        traits.append(best.government)
    if best.security:
        traits.append(f"{best.security} security")
    trait_note = f" — {', '.join(traits)}" if traits else ""
    line = (f"Closest match: {best.name}, "
            f"{sup.distance_phrase(best.distance_ly, reference_is_current=ref_here)}{trait_note}.")
    return line + sup.clipboard_note(best.name, copied, here)


STAR_SYSTEMS = SearchDescriptor(
    tool_name=_SYS_TOOL, description=_SYS_DESC, schema_props=_SYS_SCHEMA_PROPS, required=(),
    category_key="star_systems", error_label="System search",
    help_meta=_SYS_HELP, help_vocabulary=_SYS_VOCAB, run=_run_star_systems)


# ======================================================================================
# Stations — search_stations
# ======================================================================================
_STATION_TOOL = "search_stations"

_STATION_DESC = (
    "Find the nearest Elite Dangerous STATION by its facilities — station type, services "
    "(shipyard, outfitting, market, material trader, interstellar factors, …), landing-pad "
    "size, distance from the star, or controlling faction — from the Commander's current "
    "system, and copy the station's SYSTEM name to the clipboard.\n"
    "ROUTING: if the Commander is really after a MODULE ('where can I buy a fuel scoop'), use "
    "find_closest_module instead; if they want a whole SHIP ('where can I buy a Python'), use "
    "find_closest_ship instead — those find the nearest station SELLING that, and return a "
    "station too. Use THIS tool for 'nearest station with <service/type/pad>'.\n"
    "It is LLM-native and STATELESS:\n"
    "1. Fill only the slots the Commander said; unspoken means Any. Surface stations and "
    "fleet carriers are INCLUDED by default — if they say 'no carriers', set no_carriers. "
    "'Close to the star' means within about a thousand light-seconds.\n"
    "2. If a service or station type is unclear, ask or relay the tool's suggested "
    "correction — don't invent one.\n"
    "3. Refine by re-calling with the accumulated slots (each call RE-QUERIES — a new "
    "constraint can change which station is nearest); a fresh search is just new slots. On "
    "'cancel' / 'never mind', drop it and do NOT call this tool. Report the station, its "
    "system, pad, and distance, and ALWAYS say the system name was copied to the clipboard."
)

_STATION_SCHEMA_PROPS = {
    "station_type": {"type": "string",
                     "description": "Station type, e.g. Coriolis Starport, Orbis Starport, "
                                    "Outpost, Planetary Port, Settlement, Mega ship."},
    "services": {"type": "array", "items": {"type": "string"},
                 "description": "Required services, e.g. Shipyard, Outfitting, Market, "
                                "Material Trader, Interstellar Factors Contact, Black Market."},
    "faction": {"type": "string",
                "description": "Controlling minor faction name (exact-ish). Free text."},
    "pad_size": {"type": "string",
                 "description": "Minimum landing-pad size the ship needs: S, M, or L."},
    "max_arrival_distance": {"type": "integer",
                             "description": "Maximum distance from the main star, in "
                                            "light-seconds (e.g. 1000 for 'close to the star')."},
    "no_carriers": {"type": "boolean",
                    "description": "Set true to EXCLUDE fleet carriers (they're included by "
                                   "default). Omit otherwise."},
    "near": {"type": "string",
             "description": "Reference system to measure from. Omit to use the current system."},
}

_STATION_HELP = HelpMeta(
    category="stations",
    group="navigation and search",
    one_liner=("I find the nearest station by its type, services, landing pad, "
               "distance from the star, or controlling faction, and copy its system to "
               "your clipboard."),
    example="find the nearest station with a shipyard and a large pad",
    slots=(
        Slot(param="type", phrasings=("a station type", "a kind of station"),
             example="the nearest Orbis Starport",
             help_text="Restrict to a station type, like Coriolis Starport, Outpost, "
                       "Planetary Port, or Mega ship."),
        Slot(param="services", phrasings=("a service", "services"),
             example="somewhere with a material trader",
             help_text="Require services, like Shipyard, Outfitting, Market, Material "
                       "Trader, or Interstellar Factors."),
        Slot(param="controlling_minor_faction",
             phrasings=("a controlling faction", "run by a faction"),
             example="a station controlled by the Dark Wheel",
             help_text="Restrict to stations controlled by a named minor faction."),
        Slot(param="has_large_pad", phrasings=("a landing pad size", "a pad size"),
             example="somewhere with a large pad",
             help_text="Require a landing-pad size — small, medium, or large."),
        Slot(param="distance_to_arrival",
             phrasings=("close to the star", "distance from the star"),
             example="a station close to the star",
             help_text="Restrict to stations within a distance of the main star, like "
                       "close to the star."),
    ),
    help_when_active=("Tell me the type, services, or pad you need — and say 'no "
                      "carriers' to leave out fleet carriers — and I'll find the "
                      "nearest station."),
)

_STATION_VOCAB = {"station type": list(STATION_TYPES), "service": list(SERVICES)}


def _run_stations(cap: SpecSearchCapability, inp: dict) -> str:
    slots: dict[str, object] = {}
    caught: list[str] = []          # values understood so far, echoed on a later bad slot

    stype = inp.get("station_type")
    if stype not in (None, ""):
        val = resolve_type(stype)
        if val is None:
            return sup.recovery(stype, "station type", nearest_type(stype), caught=caught)
        slots["type"] = val
        caught.append(val)

    raw_services = inp.get("services")
    if raw_services:
        wanted = raw_services if isinstance(raw_services, list) else [raw_services]
        resolved: list[str] = []
        for s in wanted:
            v = resolve_service(s)
            if v is None:
                return sup.recovery(s, "service", nearest_service(s), caught=caught)
            resolved.append(v)
        if resolved:
            slots["services"] = resolved
            caught.extend(resolved)

    faction = inp.get("faction")
    if faction and str(faction).strip():
        canon, recover_msg = sup.faction_or_recovery(cap._factions, faction)
        if recover_msg:
            return recover_msg
        slots["controlling_minor_faction"] = canon
        caught.append(canon)

    pad = inp.get("pad_size")
    if pad not in (None, ""):
        if pad_filter_key(pad) is None:
            return "I didn't catch the pad size — say small, medium, or large."
        slots["has_large_pad"] = pad

    max_ls = inp.get("max_arrival_distance")
    if max_ls is not None:
        try:
            slots["distance_to_arrival"] = {"max": int(max_ls)}
        except (TypeError, ValueError):
            return "I didn't catch the distance from the star — say it in light-seconds."

    if not slots:
        return ("Tell me what the station needs — a service like a shipyard, a station "
                "type, a landing-pad size, or how close to the star. (Say 'never mind' to "
                "drop it.)")

    include_carriers = not bool(inp.get("no_carriers"))
    got = cap.run_search(inp, slots,
                         lambda results: parse_stations(results, include_carriers=include_carriers),
                         empty=("I couldn't find a station matching that near you — try relaxing "
                                "a filter, or allowing fleet carriers."))
    if isinstance(got, str):
        return got
    stations, _ = got
    best = stations[0]
    ref_here = not sup.near_override(inp)   # a `near X` search is measured from X, not from here
    copied, here = cap.deliver_logged(best.system, best.distance_ly,
                                      f"nearest station: {best.station} in {best.system} "
                                      f"({best.distance_ly:.1f} ly), filters={sorted(slots)}, "
                                      f"carriers={'in' if include_carriers else 'out'}",
                                      reference_is_current=ref_here)
    line = (f"Closest station: {best.station} in {best.system}, "
            f"{sup.distance_phrase(best.distance_ly, reference_is_current=ref_here)}.")
    if best.pad:
        line += f" Largest pad {best.pad}."
    arrival = best.extra.get("distance_to_arrival")
    if isinstance(arrival, (int, float)) and arrival >= 1:
        from ..i18n import fmt_num
        line += f" About {fmt_num(arrival, 0)} light-seconds from the star."   # locale (#199)
    return line + sup.clipboard_note(best.system, copied, here)


STATIONS = SearchDescriptor(
    tool_name=_STATION_TOOL, description=_STATION_DESC, schema_props=_STATION_SCHEMA_PROPS,
    required=(), category_key="stations", error_label="Station search",
    help_meta=_STATION_HELP, help_vocabulary=_STATION_VOCAB, run=_run_stations)


# ======================================================================================
# Minor factions — search_minor_factions
# ======================================================================================
_FACTION_TOOL = "search_minor_factions"

_FACTION_DESC = (
    "Find the nearest Elite Dangerous STAR SYSTEM by MINOR FACTION — where a named faction is "
    "present or in control, and/or by faction allegiance, government, or active state (war, "
    "boom, election, …) — from the Commander's current system, and copy the system name to "
    "the clipboard. Stateless and LLM-native:\n"
    "1. A named faction defaults to 'is PRESENT in the system'. Only if the Commander says the "
    "faction CONTROLS / runs / owns the system, set controls=true. Pass the faction name as "
    "spoken (free text); don't invent one.\n"
    "2. Allegiance, government, and state are validated — if one isn't recognized, relay the "
    "tool's suggested correction. Fill only what was said; unspoken means Any.\n"
    "3. Refine by re-calling with the accumulated slots (each call RE-QUERIES). If the "
    "Commander says 'cancel' / 'never mind', drop it and do NOT call this tool. Report the "
    "system and why it matched, and ALWAYS say the system name was copied to the clipboard."
)

_FACTION_SCHEMA_PROPS = {
    "faction": {"type": "string",
                "description": "Minor faction name, as spoken (free text)."},
    "controls": {"type": "boolean",
                 "description": "True only if the faction must CONTROL the system; omit/false "
                                "means merely present (the default)."},
    "allegiance": {"type": "string",
                   "description": "Faction allegiance: Federation, Empire, Alliance, "
                                  "Independent."},
    "government": {"type": "string",
                   "description": "Faction government, e.g. Democracy, Corporate, Anarchy."},
    "state": {"type": "string",
              "description": "Active faction state, e.g. War, Civil War, Boom, Election, "
                             "Expansion, Infrastructure Failure."},
    "near": {"type": "string",
             "description": "Reference system to measure from. Omit to use the current system."},
}

_FACTION_HELP = HelpMeta(
    category="minor factions",
    group="navigation and search",
    one_liner=("I find the nearest system where a minor faction is present or in "
               "control, or by faction allegiance, government, or state, and copy the "
               "system to your clipboard."),
    example="find the nearest system where the Dark Wheel is present",
    slots=(
        Slot(param="minor_faction_presences",
             phrasings=("a faction is present", "where a faction is"),
             example="where the Dark Wheel is present",
             help_text="Find systems where a named minor faction is present."),
        Slot(param="controlling_minor_faction",
             phrasings=("a faction controls", "run by a faction"),
             example="controlled by the Dark Wheel",
             help_text="Find systems a named minor faction controls, rather than just "
                       "is present in."),
        Slot(param="allegiance", phrasings=("an allegiance", "aligned to"),
             example="a nearby Independent faction system",
             help_text="Restrict by faction allegiance — Federation, Empire, Alliance, "
                       "or Independent."),
        Slot(param="government", phrasings=("a government", "a government type"),
             example="a Cooperative faction nearby",
             help_text="Restrict by faction government, like Democracy, Corporate, or "
                       "Cooperative."),
        Slot(param="controlling_minor_faction_state",
             phrasings=("a faction state", "at war, in boom, in election"),
             example="the nearest faction at war",
             help_text="Restrict by active faction state, like War, Boom, Election, or "
                       "Expansion."),
    ),
    help_when_active=("Name the faction — and say whether it should control the system "
                      "or just be present — and I'll find the nearest match."),
)

_FACTION_VOCAB = {"allegiance": list(VOCAB["allegiance"]),
                  "government": list(VOCAB["government"]),
                  "faction state": list(FACTION_STATES)}


# The validated slots after the faction name, in spoken-echo order: allegiance and government
# are generic enums; the state resolves through the BGS vocabulary and is left out of the
# caught-so-far echo (it was never echoed pre-collapse).
_MF_ENUM_ROWS = (
    ("allegiance", "allegiance", lambda v: resolve_enum("allegiance", v),
     lambda v: nearest_enum("allegiance", v), "allegiance", "{val} allegiance"),
    ("government", "government", lambda v: resolve_enum("government", v),
     lambda v: nearest_enum("government", v), "government", "{val} government"),
    ("state", "controlling_minor_faction_state", resolve_state, nearest_state,
     "faction state", None),
)


def _run_minor_factions(cap: SpecSearchCapability, inp: dict) -> str:
    slots: dict[str, object] = {}
    caught: list[str] = []          # values understood so far, echoed on a later bad slot

    faction = inp.get("faction")
    if faction and str(faction).strip():
        # Resolve the spoken name to Spansh's exact string (mishears -> 0 systems otherwise);
        # an unresolved name offers real corrections rather than searching on nothing.
        canon, recover_msg = sup.faction_or_recovery(cap._factions, faction)
        if recover_msg:
            cap.logline(f"unresolved faction '{faction}'")
            return recover_msg
        # Polarity is a slot value, not a mode: controls -> controlling_minor_faction,
        # else the default 'is present' -> minor_faction_presences.
        controls = bool(inp.get("controls"))
        slots["controlling_minor_faction" if controls else "minor_faction_presences"] = canon
        caught.append(f"{canon} ({'controls' if controls else 'present'})")

    recover_msg = _fill_enum_slots(inp, slots, caught, _MF_ENUM_ROWS)
    if recover_msg:
        return recover_msg

    if not slots:
        return ("Name the minor faction, or give me an allegiance, government, or state to "
                "look for. (Say 'never mind' to drop it.)")

    # A faction's PRESENCE is stable, but its STATE ticks daily — so only a state-filtered
    # search constrains data freshness (with a stale fallback + spoken caveat).
    fresh = "updated_at" if "controlling_minor_faction_state" in slots else None
    got = cap.run_search(inp, slots, parse_systems, fresh_field=fresh,
                         empty=("I couldn't find a system matching that near you — try "
                                "relaxing one of the filters, or check the faction name."))
    if isinstance(got, str):
        return got
    systems, stale_age = got
    best = systems[0]
    ref_here = not sup.near_override(inp)   # a `near X` search is measured from X, not from here
    copied, here = cap.deliver_logged(best.name, best.distance_ly,
                                      f"nearest faction match: {best.name} "
                                      f"({best.distance_ly:.1f} ly), filters={sorted(slots)}, "
                                      f"stale_age={stale_age}", reference_is_current=ref_here)
    dist = sup.distance_phrase(best.distance_ly, reference_is_current=ref_here)
    want_faction = slots.get("controlling_minor_faction") or slots.get("minor_faction_presences")
    if want_faction:
        # Ground the answer in the faction the Commander ASKED about. A presence search can
        # land in a system a DIFFERENT faction controls; leading with that controller reads
        # as a miss, and the model then reports failure over a success (observed live).
        controls = "controlling_minor_faction" in slots
        line = f"{want_faction} {'controls' if controls else 'is present in'} {best.name}, {dist}."
        controller = best.controlling_minor_faction
        if not controls and controller and controller != want_faction:
            line += f" The system's controlled by {controller}."
    else:
        line = f"Closest match: {best.name}, {dist}."
        if best.controlling_minor_faction:
            line += f" Controlled by {best.controlling_minor_faction}."
    # Present only when a state-filtered search answered from the stale fallback.
    line += sup.stale_note(stale_age, what="that report",
                           risk="the faction state may have changed")
    return line + sup.clipboard_note(best.name, copied, here)


MINOR_FACTIONS = SearchDescriptor(
    tool_name=_FACTION_TOOL, description=_FACTION_DESC, schema_props=_FACTION_SCHEMA_PROPS,
    required=(), category_key="minor_factions", error_label="Minor-faction search",
    help_meta=_FACTION_HELP, help_vocabulary=_FACTION_VOCAB, run=_run_minor_factions)


# ======================================================================================
# Signals / structures — search_signals
# ======================================================================================
_SIGNAL_TOOL = "search_signals"

_SIGNAL_DESC = (
    "Find the nearest Elite Dangerous STRUCTURE by type — a megaship, settlement, outpost, "
    "starport, planetary port, or asteroid base — from the Commander's current system, and "
    "copy its SYSTEM name to the clipboard. Stateless and LLM-native: fill the type the "
    "Commander named. If they ask for a type that isn't one of these, offer the closest type "
    "you CAN find rather than inventing a result. On 'cancel' / 'never mind', drop it and do "
    "NOT call this tool. Report the structure, its system, and distance, and ALWAYS say the "
    "system name was copied to the clipboard."
)

_SIGNAL_SCHEMA_PROPS = {
    "signal_type": {"type": "string",
                    "description": "Structure type to find: Mega ship, Settlement, Outpost, "
                                   "Coriolis/Orbis/Ocellus Starport, Planetary Port, or "
                                   "Asteroid base."},
    "near": {"type": "string",
             "description": "Reference system to measure from. Omit to use the current system."},
}

_SIGNAL_HELP = HelpMeta(
    category="signals",
    group="navigation and search",
    one_liner=("I find the nearest structure — a megaship, settlement, outpost, or "
               "starport — and copy its system to your clipboard."),
    example="find the nearest megaship",
    slots=(
        Slot(param="type", phrasings=("a structure type", "a kind of structure"),
             example="the closest settlement",
             help_text="Name the structure — a megaship, settlement, outpost, "
                       "starport, planetary port, or asteroid base."),
    ),
    help_when_active=("Tell me the structure — a megaship, settlement, and so on — and "
                      "I'll find the nearest one."),
)

_SIGNAL_VOCAB = {"structure type": list(STATION_TYPES)}


def _run_signals(cap: SpecSearchCapability, inp: dict) -> str:
    raw = inp.get("signal_type")
    if raw in (None, ""):
        return ("What kind of structure should I find — a megaship, settlement, outpost, "
                "or starport?")
    stype = resolve_type(raw)
    if stype is None:
        sugg = nearest_type(raw)
        cap.logline(f"unresolved signal type '{raw}' -> {sugg or 'no match'}")
        if sugg:
            return f"I can't search for '{raw}', but I can find {sugg}s — want that?"
        return ("I can only find fixed structures — megaships, settlements, outposts, "
                "starports, planetary ports, or asteroid bases. Which one?")

    got = cap.run_search(inp, {"type": stype},
                         parse_stations,        # carriers dropped: not a signal source
                         empty=f"I couldn't find {stype} near you — try a different structure "
                               f"or system.")
    if isinstance(got, str):
        return got
    stations, _ = got
    best = stations[0]
    ref_here = not sup.near_override(inp)   # a `near X` search is measured from X, not from here
    copied, here = cap.deliver_logged(best.system, best.distance_ly,
                                      f"nearest {stype}: {best.station} in {best.system} "
                                      f"({best.distance_ly:.1f} ly)", reference_is_current=ref_here)
    line = (f"Closest {stype}: {best.station} in {best.system}, "
            f"{sup.distance_phrase(best.distance_ly, reference_is_current=ref_here)}.")
    return line + sup.clipboard_note(best.system, copied, here)


SIGNALS = SearchDescriptor(
    tool_name=_SIGNAL_TOOL, description=_SIGNAL_DESC, schema_props=_SIGNAL_SCHEMA_PROPS,
    required=(), category_key="signals", error_label="Signal search",
    help_meta=_SIGNAL_HELP, help_vocabulary=_SIGNAL_VOCAB, run=_run_signals)


# ======================================================================================
# Faction states (misc) — search_faction_states
# ======================================================================================
_MISC_TOOL = "search_faction_states"

_MISC_DESC = (
    "Find the nearest Elite Dangerous STAR SYSTEM by its CONTROLLING faction's state — for "
    "wars and civil wars, boom, election, infrastructure failure, and the missions those "
    "states generate — from the Commander's current system, and copy the system name to the "
    "clipboard. Map mission-speak to the underlying state: 'massacre missions' or 'combat "
    "zones' -> War or Civil War; 'restore / repair missions' -> Infrastructure Failure; "
    "'mining missions' aren't a state Spansh can filter, so say so rather than guessing. "
    "Stateless and LLM-native: fill only what was said (state, and optionally a faction name, "
    "allegiance, or Powerplay state), validate — relay any suggested correction — and search. "
    "Refine by re-calling with the accumulated slots (each call RE-QUERIES); on 'cancel' / "
    "'never mind', drop it and do NOT call this tool. Report the system and its state, and "
    "ALWAYS say the system name was copied to the clipboard."
)

_MISC_SCHEMA_PROPS = {
    "state": {"type": "string",
              "description": "Controlling faction state: War, Civil War, Boom, Election, "
                             "Expansion, Infrastructure Failure, Outbreak, Lockdown, …"},
    "faction": {"type": "string",
                "description": "Optional controlling minor faction name (free text)."},
    "allegiance": {"type": "string",
                   "description": "Optional allegiance: Federation, Empire, Alliance, "
                                  "Independent."},
    "power_state": {"type": "string",
                    "description": "Optional Powerplay state: Stronghold, Fortified, "
                                   "Exploited, Unoccupied."},
    "near": {"type": "string",
             "description": "Reference system to measure from. Omit to use the current system."},
}

_MISC_HELP = HelpMeta(
    category="faction states",
    group="navigation and search",
    one_liner=("I find the nearest system by its controlling faction's state — wars, "
               "civil wars, boom, election, infrastructure failure — and copy the "
               "system to your clipboard."),
    example="find the nearest system at war",
    slots=(
        Slot(param="controlling_minor_faction_state",
             phrasings=("a faction state", "at war, in boom, in election"),
             example="the nearest civil war",
             help_text="The controlling faction's state, like War, Civil War, Boom, "
                       "Election, or Infrastructure Failure."),
        Slot(param="controlling_minor_faction",
             phrasings=("a controlling faction", "run by a faction"),
             example="where the Dark Wheel is in control",
             help_text="Narrow to a named controlling minor faction."),
        Slot(param="allegiance", phrasings=("an allegiance", "aligned to"),
             example="a nearby Empire system at war",
             help_text="Narrow by allegiance — Federation, Empire, Alliance, or "
                       "Independent."),
        Slot(param="power_state", phrasings=("a Powerplay state", "a power state"),
             example="a Fortified system in boom",
             help_text="Narrow by Powerplay state — Stronghold, Fortified, Exploited, "
                       "or Unoccupied."),
    ),
    help_when_active=("Tell me the state — war, boom, election — or the kind of "
                      "missions you want, and I'll find the nearest system."),
)

_MISC_VOCAB = {"faction state": list(FACTION_STATES),
               "power state": list(VOCAB["power_state"]),
               "allegiance": list(VOCAB["allegiance"])}


# The validated slots before the faction name, in spoken-echo order: the state resolves
# through the BGS vocabulary and is echoed bare; the others are generic enums.
_STATE_ENUM_ROWS = (
    ("state", "controlling_minor_faction_state", resolve_state, nearest_state,
     "faction state", "{val}"),
    ("power_state", "power_state", lambda v: resolve_enum("power_state", v),
     lambda v: nearest_enum("power_state", v), "Powerplay state", "{val} power state"),
    ("allegiance", "allegiance", lambda v: resolve_enum("allegiance", v),
     lambda v: nearest_enum("allegiance", v), "allegiance", "{val} allegiance"),
)


def _run_faction_states(cap: SpecSearchCapability, inp: dict) -> str:
    slots: dict[str, object] = {}
    caught: list[str] = []          # values understood so far, echoed on a later bad slot

    recover_msg = _fill_enum_slots(inp, slots, caught, _STATE_ENUM_ROWS)
    if recover_msg:
        return recover_msg

    faction = inp.get("faction")
    if faction and str(faction).strip():
        canon, recover_msg = sup.faction_or_recovery(cap._factions, faction)
        if recover_msg:
            return recover_msg
        slots["controlling_minor_faction"] = canon

    if not slots:
        return ("Tell me the state to look for — war, civil war, boom, election, "
                "infrastructure failure — or the kind of missions you want. (Say 'never "
                "mind' to drop it.)")

    # Faction and Powerplay states tick daily, so a state-filtered search constrains data
    # freshness (with a stale fallback + spoken caveat). This category exists FOR states,
    # so in practice that's nearly every call.
    fresh = ("updated_at" if ("controlling_minor_faction_state" in slots
                              or "power_state" in slots) else None)
    got = cap.run_search(inp, slots, parse_systems, fresh_field=fresh,
                         empty=("I couldn't find a system in that state near you — try a "
                                "different state or relax the other filters."))
    if isinstance(got, str):
        return got
    systems, stale_age = got
    best = systems[0]
    ref_here = not sup.near_override(inp)   # a `near X` search is measured from X, not from here
    copied, here = cap.deliver_logged(best.name, best.distance_ly,
                                      f"nearest state match: {best.name} "
                                      f"({best.distance_ly:.1f} ly), filters={sorted(slots)}, "
                                      f"stale_age={stale_age}", reference_is_current=ref_here)
    state_note = f" — {best.extra['state']}" if best.extra.get("state") else ""
    line = (f"Closest match: {best.name}, "
            f"{sup.distance_phrase(best.distance_ly, reference_is_current=ref_here)}{state_note}.")
    if best.controlling_minor_faction:
        line += f" Controlled by {best.controlling_minor_faction}."
    # Present only when a state-filtered search answered from the stale fallback.
    line += sup.stale_note(stale_age, what="that report",
                           risk="the state may have changed")
    return line + sup.clipboard_note(best.name, copied, here)


FACTION_STATES_CATEGORY = SearchDescriptor(
    tool_name=_MISC_TOOL, description=_MISC_DESC, schema_props=_MISC_SCHEMA_PROPS,
    required=(), category_key="misc", error_label="Faction-state search",
    help_meta=_MISC_HELP, help_vocabulary=_MISC_VOCAB, run=_run_faction_states)


# ======================================================================================
# Bodies / bio-geo signals — search_bodies
# ======================================================================================
_BODY_TOOL = "search_bodies"

# A biological-signal record older than this gets a gentle "that scan is N days old" caveat —
# the signal/landmark data is the crowdsourced (so ageable) part of a body; the structure isn't.
_BIO_STALE_DAYS = 30

_BODY_DESC = (
    "Find the nearest Elite Dangerous BODY (planet or moon) by its type or its biological "
    "signals, from the Commander's current system, and copy the body's SYSTEM name to the "
    "clipboard for the galaxy map. Answers 'nearest Earth-like world', 'closest ammonia / water "
    "world', or 'nearest body with <biology> signals'.\n"
    "ROUTING: this is a SINGLE-body lookup. If they want a whole exploration/credit RUN of "
    "systems to scan, use plan_riches_route (Road to Riches) instead.\n"
    "It is LLM-native and STATELESS:\n"
    "1. Fill only the slots the Commander said; unspoken means Any. `body_type` is a body class "
    "(Earth-like world, Ammonia world, Water world, High metal content world, a gas-giant class, "
    "…). `biological_signal` is an exobiology genus ('Bacterium', 'Stratum', 'Tussock'), a "
    "specific species ('Bacterium Aurasus'), or 'any biological'. Optionally `landable`, or "
    "`max_arrival_distance` in light-seconds.\n"
    "2. If a type or biology is unclear, relay the tool's suggested correction — don't invent "
    "one.\n"
    "3. Refine by re-calling with the accumulated slots (each call RE-QUERIES). On 'cancel' / "
    "'never mind', drop it and do NOT call this tool. Report the body, its system, distance, and "
    "distance from the star, and ALWAYS say the system name was copied to the clipboard."
)

_BODY_SCHEMA_PROPS = {
    "body_type": {"type": "string",
                  "description": "Body class to find, e.g. Earth-like world, Ammonia world, "
                                 "Water world, High metal content world, Class II gas giant."},
    "biological_signal": {"type": "string",
                          "description": "Biological signal to require: an exobiology genus "
                                         "(Bacterium, Stratum, Tussock, Aleoida, …), a specific "
                                         "species (Bacterium Aurasus), or 'any biological'."},
    "landable": {"type": "boolean",
                 "description": "Set true to require a landable body (needed to scan surface "
                                "biology on foot). Omit for any."},
    "max_arrival_distance": {"type": "integer",
                             "description": "Maximum distance from the main star, in "
                                            "light-seconds. Omit for any."},
    "near": {"type": "string",
             "description": "Reference system to measure from. Omit to use the current system."},
}

_BODY_HELP = HelpMeta(
    category="bodies",
    group="navigation and search",
    one_liner=("I find the nearest body — an Earth-like world, ammonia or water world, or "
               "one with a given biological signal — and copy its system to your "
               "clipboard for the galaxy map."),
    example="find the nearest Earth-like world",
    slots=(
        Slot(param="subtype", phrasings=("a body type", "a kind of world"),
             example="the closest ammonia world",
             help_text="Name the body class — Earth-like world, ammonia world, water "
                       "world, high metal content world, or a gas-giant class."),
        Slot(param="landmark_subtype",
             phrasings=("a biological signal", "a type of biology"),
             example="the nearest body with Bacterium",
             help_text="Require a biological signal — an exobiology genus like Bacterium "
                       "or Stratum, a specific species, or 'any biological'."),
        Slot(param="is_landable", phrasings=("landable", "one I can land on"),
             example="a landable body with Aleoida",
             help_text="Require a landable body — needed to scan surface biology on foot."),
        Slot(param="distance_to_arrival",
             phrasings=("close to the star", "distance from the star"),
             example="an Earth-like world close to the star",
             help_text="Restrict to bodies within a distance of the main star."),
    ),
    help_when_active=("Tell me a body type — like an Earth-like world — or a biological "
                      "signal like Bacterium, and I'll find the nearest one."),
)

_BODY_VOCAB = {"body type": list(BODY_SUBTYPES), "biological signal": list(BIO_GENUS_NAMES)}


def _body_age_caveat(cap: SpecSearchCapability, rec, bio_search: bool) -> str:
    """A gentle caveat when a BIO search lands on an old record — the signal data is
    crowdsourced, so a very stale body may have been re-surveyed since. Structure searches
    (subtype etc.) don't age, so they get no caveat."""
    if not bio_search:
        return ""
    stamp = rec.extra.get("signals_updated_at") or rec.extra.get("updated_at")
    age = data_age_days({"_": stamp}, "_", now=cap._now())
    if age is None or age < _BIO_STALE_DAYS:
        return ""
    return sup.stale_note(age, what="that biology scan",
                          risk="the surface may have been re-surveyed since")


def _run_bodies(cap: SpecSearchCapability, inp: dict) -> str:
    slots: dict[str, object] = {}
    caught: list[str] = []          # values understood so far, echoed on a later bad slot
    bio_search = False              # whether biology was requested (drives the age caveat)

    btype = inp.get("body_type")
    if btype not in (None, ""):
        val = resolve_subtype(btype)
        if val is None:
            return sup.recovery(btype, "body type", nearest_subtype(btype), caught=caught)
        slots["subtype"] = val
        caught.append(val)

    bio = inp.get("biological_signal")
    if bio not in (None, ""):
        species = resolve_bio_signal(bio)
        if species is None:
            return sup.recovery(bio, "biological signal", nearest_bio_signal(bio),
                                caught=caught)
        slots["landmark_subtype"] = species
        bio_search = True
        # Echo the spoken term, not the (possibly long) species list, so the caught-so-far
        # line stays readable when a LATER slot is the one that fails.
        caught.append(str(bio))

    if inp.get("landable") is True:
        slots["is_landable"] = True
        caught.append("landable")

    max_ls = inp.get("max_arrival_distance")
    if max_ls is not None:
        try:
            slots["distance_to_arrival"] = {"max": int(max_ls)}
        except (TypeError, ValueError):
            return "I didn't catch the distance from the star — say it in light-seconds."

    if not slots:
        return ("Tell me what to look for — a body type like an Earth-like world, or a "
                "biological signal like Bacterium. (Say 'never mind' to drop it.)")

    got = cap.run_search(inp, slots, parse_bodies,
                         empty=("I couldn't find a body matching that near you — try a "
                                "different type, a broader biology, or relaxing a filter."))
    if isinstance(got, str):
        return got
    bodies, _ = got
    best = bodies[0]
    ref_here = not sup.near_override(inp)   # a `near X` search is measured from X, not from here
    copied, here = cap.deliver_logged(best.system, best.distance_ly,
                                      f"nearest body: {best.name} ({best.subtype}) in "
                                      f"{best.system} ({best.distance_ly:.1f} ly), "
                                      f"filters={sorted(slots)}", reference_is_current=ref_here)
    what = best.subtype or "body"
    line = (f"Closest {what}: {best.name} in {best.system}, "
            f"{sup.distance_phrase(best.distance_ly, reference_is_current=ref_here)}.")
    if best.distance_to_arrival_ls is not None and best.distance_to_arrival_ls >= 1:
        from ..i18n import fmt_num
        line += f" About {fmt_num(best.distance_to_arrival_ls, 0)} light-seconds from the star."
    if best.is_landable:
        line += " It's landable."
    if bio_search:
        bio_count = best.signals.get("Biological")
        if isinstance(bio_count, int) and bio_count > 0:
            sig = "signal" if bio_count == 1 else "signals"
            line += f" {bio_count} biological {sig}."
        if best.landmarks:
            line += f" Confirmed: {sup.or_list(list(best.landmarks)[:3])}."
    return line + sup.clipboard_note(best.system, copied, here) + _body_age_caveat(cap, best, bio_search)


BODIES = SearchDescriptor(
    tool_name=_BODY_TOOL, description=_BODY_DESC, schema_props=_BODY_SCHEMA_PROPS,
    required=(), category_key="bodies", error_label="Body search",
    help_meta=_BODY_HELP, help_vocabulary=_BODY_VOCAB, run=_run_bodies)


# ======================================================================================
# The declarative family + the two standalone-section constructors
# ======================================================================================
# The four categories that share the single `[search]` toggle, in registration order (the
# order bootstrap's build_searches registers them — part of the frozen tools() order). All
# take the shared faction-name index unconditionally: `FactionIndex` is lazy, so a category
# without a faction slot (signals) simply never touches it.
SEARCH_GROUP: tuple[SearchDescriptor, ...] = (
    STATIONS,
    MINOR_FACTIONS,
    SIGNALS,
    FACTION_STATES_CATEGORY,
)


def SystemSearchCapability(config: SearchConfig, **kw) -> SpecSearchCapability:
    """The star-systems category — its own `[star_systems]` section, so it's constructed by
    name (bootstrap's build_system_search) rather than via the `SEARCH_GROUP` loop."""
    return SpecSearchCapability(STAR_SYSTEMS, config, **kw)


class BodySearchCapability(SpecSearchCapability):
    """The bodies category — its own `[bodies]` section, constructed by name (bootstrap's
    build_bodies). A real subclass because it alone carries the `now` seam: the bio-scan age
    caveat is the only place the family reads the clock, so the seam lives here instead of
    widening every category's constructor."""

    def __init__(self, config: SearchConfig, *,
                 now: Callable[[], datetime] | None = None, **kw) -> None:
        super().__init__(BODIES, config, **kw)
        self._now = now if now is not None else (lambda: datetime.now(UTC))
