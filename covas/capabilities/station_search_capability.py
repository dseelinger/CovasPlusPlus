"""Station search capability — "find the nearest station with a shipyard and a large pad".

Same LLM-native, stateless shape as the star-systems reference: the conversation is the state,
the model fills slots and refines by re-calling, no classifier / state machine. Distinct from
OUTFITTING: this finds a station by its facilities (type, services, pad, distance, faction),
whereas outfitting finds one SELLING a given module. The two tool descriptions cross-reference
each other so the model routes a "where can I buy an X module / Y ship" ask to outfitting and a
"nearest station with Z service" ask here — no separate intent classifier.

Category defaults (Search Prompt 5): surface stations are included (no type filter unless
asked), and fleet carriers are included but a spoken "no carriers" drops them. "Close to the
star" maps to a max arrival distance of 1000 Ls.

Enum slots (type, services) are validated against the canonical Spansh vocabulary before the
query; the faction name is free text (validated by the search returning results). I/O is
injected so the default test run is offline (DESIGN §9); fail soft throughout.
"""
from __future__ import annotations

from typing import Callable

from ..nav import copy as _default_copy
from ..search import RequestsHttp, parse_stations
from ..search.categories import category
from ..search.faction_index import FactionIndex
from ..search.spansh import Http, pad_filter_key
from ..search.stations import (SERVICES, STATION_TYPES, nearest_service, nearest_type,
                               resolve_service, resolve_type)
from . import _search_support as sup
from ._search_support import SearchConfig
from .base import HelpMeta, Slot

_TOOL_NAME = "search_stations"
_CLOSE_TO_STAR_LS = 1000        # "close to the star" -> max arrival distance, in light-seconds

_DESC = (
    "Find the nearest Elite Dangerous STATION by its facilities — station type, services "
    "(shipyard, outfitting, market, material trader, interstellar factors, …), landing-pad "
    "size, distance from the star, or controlling faction — from the Commander's current "
    "system, and copy the station's SYSTEM name to the clipboard.\n"
    "ROUTING: if the Commander is really after a MODULE or a SHIP ('where can I buy a "
    "fuel scoop / a Python'), use the find_closest_module (outfitting) tool instead — it "
    "finds the nearest station SELLING that, and returns a station too. Use THIS tool for "
    "'nearest station with <service/type/pad>'.\n"
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

_SCHEMA_PROPS = {
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


class StationSearchCapability:
    """Advertises `search_stations`; stateless slot-fill -> validate -> search. Injected seams
    (`http`, `get_current_system`, `clipboard`) keep the default test run offline."""

    def __init__(self, config: SearchConfig, *, http: Http | None = None,
                 get_current_system: Callable[[], str | None] | None = None,
                 clipboard: Callable[[str], None] = _default_copy,
                 factions: FactionIndex | None = None,
                 log: Callable[[str], None] | None = None) -> None:
        self._cfg = config
        self._http = http if http is not None else RequestsHttp()
        self._current_system = get_current_system
        self._clipboard = clipboard
        self._factions = factions if factions is not None else FactionIndex()
        self._log = log
        self._spec = category("stations")

    def tools(self) -> list[dict]:
        return [{"name": _TOOL_NAME, "description": _DESC,
                 "input_schema": {"type": "object", "properties": dict(_SCHEMA_PROPS),
                                  "required": []}}]

    def help_meta(self) -> HelpMeta:
        return HelpMeta(
            category="stations",
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

    def help_vocabulary(self) -> dict[str, list[str]]:
        return {"station type": list(STATION_TYPES), "service": list(SERVICES)}

    def run_tool(self, name: str, inp: dict) -> str:
        if name != _TOOL_NAME:
            return f"Unknown tool: {name}"
        try:
            return self._handle(inp)
        except Exception as e:  # noqa: BLE001 — the voice loop must survive any tool error
            self._logline(f"error: {e}")
            return f"Station search error: {e}"

    def _handle(self, inp: dict) -> str:
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
            canon, recover_msg = sup.faction_or_recovery(self._factions, faction)
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

        return self._search(slots, inp)

    def _search(self, slots: dict, inp: dict) -> str:
        system = sup.reference_system(self._current_system, inp)
        if not system:
            return ("I don't know your current system yet — is Elite Dangerous running with "
                    "monitoring on? Jump somewhere, or tell me a system to search near.")
        try:
            results = sup.run_query(self._spec, slots, self._http, system,
                                    user_agent=self._cfg.user_agent, size=self._cfg.search_size)
        except sup.NavError as e:
            self._logline(f"search failed: {e}")
            return str(e)

        include_carriers = not bool(inp.get("no_carriers"))
        stations = parse_stations(results, include_carriers=include_carriers)
        if not stations:
            return ("I couldn't find a station matching that near you — try relaxing a "
                    "filter, or allowing fleet carriers.")
        best = stations[0]
        copied = sup.copy_system(self._clipboard, best.system, self._log)
        self._logline(f"nearest station: {best.station} in {best.system} "
                      f"({best.distance_ly:.1f} ly), filters={sorted(slots)}, "
                      f"carriers={'in' if include_carriers else 'out'}, "
                      f"clipboard={'ok' if copied else 'failed'}")
        return self._say(best, copied)

    def _say(self, rec, copied: bool) -> str:
        line = f"Closest station: {rec.station} in {rec.system}, {sup.distance_phrase(rec.distance_ly)}."
        if rec.pad:
            line += f" Largest pad {rec.pad}."
        arrival = rec.extra.get("distance_to_arrival")
        if isinstance(arrival, (int, float)) and arrival >= 1:
            line += f" About {arrival:,.0f} light-seconds from the star."
        return line + sup.clipboard_note(rec.system, copied)

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)
