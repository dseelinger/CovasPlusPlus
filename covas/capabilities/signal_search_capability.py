"""Structure search capability — "find the nearest megaship".

Same stateless, LLM-native shape as the reference. Finds the nearest DOCKABLE structure by
type — megaship, settlement, asteroid base, outpost, starport, planetary port — over the
shared Spansh client. The vocabulary is the canonical set of Spansh station types (see
`search/stations.py`): a type Spansh doesn't track is corrected to what it can actually
locate, never invented.

Fleet carriers are dropped (they jump, so they're a poor 'nearest structure' answer). I/O is
injected so the default test run is offline (DESIGN §9); fail soft throughout.
"""
from __future__ import annotations

from typing import Callable

from ..nav import copy as _default_copy
from ..search import RequestsHttp, parse_stations
from ..search.categories import category
from ..search.spansh import Http
from ..search.stations import STATION_TYPES, nearest_type, resolve_type
from . import _search_support as sup
from ._search_support import SearchConfig
from .base import HelpMeta, Slot

_TOOL_NAME = "search_signals"

_DESC = (
    "Find the nearest Elite Dangerous STRUCTURE by type — a megaship, settlement, outpost, "
    "starport, planetary port, or asteroid base — from the Commander's current system, and "
    "copy its SYSTEM name to the clipboard. Stateless and LLM-native: fill the type the "
    "Commander named. If they ask for a type that isn't one of these, offer the closest type "
    "you CAN find rather than inventing a result. On 'cancel' / 'never mind', drop it and do "
    "NOT call this tool. Report the structure, its system, and distance, and ALWAYS say the "
    "system name was copied to the clipboard."
)

_SCHEMA_PROPS = {
    "signal_type": {"type": "string",
                    "description": "Structure type to find: Mega ship, Settlement, Outpost, "
                                   "Coriolis/Orbis/Ocellus Starport, Planetary Port, or "
                                   "Asteroid base."},
    "near": {"type": "string",
             "description": "Reference system to measure from. Omit to use the current system."},
}


class SignalSearchCapability:
    """Advertises `search_signals`; stateless slot-fill -> validate -> search."""

    def __init__(self, config: SearchConfig, *, http: Http | None = None,
                 get_current_system: Callable[[], str | None] | None = None,
                 clipboard: Callable[[str], None] = _default_copy,
                 log: Callable[[str], None] | None = None) -> None:
        self._cfg = config
        self._http = http if http is not None else RequestsHttp()
        self._current_system = get_current_system
        self._clipboard = clipboard
        self._log = log
        self._spec = category("signals")

    def tools(self) -> list[dict]:
        return [{"name": _TOOL_NAME, "description": _DESC,
                 "input_schema": {"type": "object", "properties": dict(_SCHEMA_PROPS),
                                  "required": []}}]

    def help_meta(self) -> HelpMeta:
        return HelpMeta(
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

    def help_vocabulary(self) -> dict[str, list[str]]:
        return {"structure type": list(STATION_TYPES)}

    def run_tool(self, name: str, inp: dict) -> str:
        if name != _TOOL_NAME:
            return f"Unknown tool: {name}"
        try:
            return self._handle(inp)
        except Exception as e:  # noqa: BLE001 — the voice loop must survive any tool error
            self._logline(f"error: {e}")
            return f"Signal search error: {e}"

    def _handle(self, inp: dict) -> str:
        raw = inp.get("signal_type")
        if raw in (None, ""):
            return ("What kind of structure should I find — a megaship, settlement, outpost, "
                    "or starport?")
        stype = resolve_type(raw)
        if stype is None:
            sugg = nearest_type(raw)
            self._logline(f"unresolved signal type '{raw}' -> {sugg or 'no match'}")
            if sugg:
                return f"I can't search for '{raw}', but I can find {sugg}s — want that?"
            return ("I can only find fixed structures — megaships, settlements, outposts, "
                    "starports, planetary ports, or asteroid bases. Which one?")

        system = sup.reference_system(self._current_system, inp)
        if not system:
            return ("I don't know your current system yet — is Elite Dangerous running with "
                    "monitoring on? Jump somewhere, or tell me a system to search near.")
        try:
            results = sup.run_query(self._spec, {"type": stype}, self._http, system,
                                    user_agent=self._cfg.user_agent, size=self._cfg.search_size)
        except sup.NavError as e:
            self._logline(f"search failed: {e}")
            return str(e)

        stations = parse_stations(results)          # carriers dropped: not a signal source
        if not stations:
            return f"I couldn't find {stype} near you — try a different structure or system."
        best = stations[0]
        copied, here = sup.deliver_system(self._clipboard, best.system, best.distance_ly, self._log)
        self._logline(f"nearest {stype}: {best.station} in {best.system} "
                      f"({best.distance_ly:.1f} ly), "
                      f"clipboard={'here' if here else ('ok' if copied else 'failed')}")
        line = (f"Closest {stype}: {best.station} in {best.system}, "
                f"{sup.distance_phrase(best.distance_ly)}.")
        return line + sup.clipboard_note(best.system, copied, here)

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)
