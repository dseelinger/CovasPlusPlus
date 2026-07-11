"""Miscellaneous BGS search capability — "find the nearest system at war".

The grab-bag category (Search Prompt 5): find the nearest system by the CONTROLLING faction's
state — wars and civil wars (also where combat / massacre missions come from), infrastructure
failure (restore/repair missions), boom, election, and so on — optionally narrowed by faction,
allegiance, or Powerplay state. Same stateless, LLM-native shape as the reference.

Honesty note: Spansh has no "mission type" or "multistate faction" filter, so mission-speak is
mapped to the underlying faction STATE in the tool description (massacre -> War/Civil War,
restore -> Infrastructure Failure) rather than pretending a mission filter exists. States,
allegiance, and Powerplay state are validated against the canonical vocabulary; the faction
name is free text. I/O is injected so the default test run is offline (DESIGN §9).
"""
from __future__ import annotations

from typing import Callable

from ..nav import copy as _default_copy
from ..search import RequestsHttp, parse_systems
from ..search.categories import category
from ..search.faction_index import FactionIndex
from ..search.factions import FACTION_STATES, nearest_state, resolve_state
from ..search.spansh import Http
from ..search.systems import VOCAB, nearest_enum, resolve_enum
from . import _search_support as sup
from ._search_support import SearchConfig
from .base import HelpMeta, Slot

_TOOL_NAME = "search_faction_states"

_DESC = (
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

_SCHEMA_PROPS = {
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


class MiscSearchCapability:
    """Advertises `search_faction_states`; stateless slot-fill -> validate -> search."""

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
        self._spec = category("misc")

    def tools(self) -> list[dict]:
        return [{"name": _TOOL_NAME, "description": _DESC,
                 "input_schema": {"type": "object", "properties": dict(_SCHEMA_PROPS),
                                  "required": []}}]

    def help_meta(self) -> HelpMeta:
        return HelpMeta(
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

    def help_vocabulary(self) -> dict[str, list[str]]:
        return {"faction state": list(FACTION_STATES),
                "power state": list(VOCAB["power_state"]),
                "allegiance": list(VOCAB["allegiance"])}

    def run_tool(self, name: str, inp: dict) -> str:
        if name != _TOOL_NAME:
            return f"Unknown tool: {name}"
        try:
            return self._handle(inp)
        except Exception as e:  # noqa: BLE001 — the voice loop must survive any tool error
            self._logline(f"error: {e}")
            return f"Faction-state search error: {e}"

    def _handle(self, inp: dict) -> str:
        slots: dict[str, object] = {}
        caught: list[str] = []          # values understood so far, echoed on a later bad slot

        state = inp.get("state")
        if state not in (None, ""):
            val = resolve_state(state)
            if val is None:
                return sup.recovery(state, "faction state", nearest_state(state), caught=caught)
            slots["controlling_minor_faction_state"] = val
            caught.append(val)

        power_state = inp.get("power_state")
        if power_state not in (None, ""):
            val = resolve_enum("power_state", power_state)
            if val is None:
                return sup.recovery(power_state, "Powerplay state",
                                    nearest_enum("power_state", power_state), caught=caught)
            slots["power_state"] = val
            caught.append(f"{val} power state")

        alleg = inp.get("allegiance")
        if alleg not in (None, ""):
            val = resolve_enum("allegiance", alleg)
            if val is None:
                return sup.recovery(alleg, "allegiance", nearest_enum("allegiance", alleg),
                                    caught=caught)
            slots["allegiance"] = val
            caught.append(f"{val} allegiance")

        faction = inp.get("faction")
        if faction and str(faction).strip():
            canon, recover_msg = sup.faction_or_recovery(self._factions, faction)
            if recover_msg:
                return recover_msg
            slots["controlling_minor_faction"] = canon

        if not slots:
            return ("Tell me the state to look for — war, civil war, boom, election, "
                    "infrastructure failure — or the kind of missions you want. (Say 'never "
                    "mind' to drop it.)")

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

        systems = parse_systems(results)
        if not systems:
            return ("I couldn't find a system in that state near you — try a different state "
                    "or relax the other filters.")
        best = systems[0]
        copied, here = sup.deliver_system(self._clipboard, best.name, best.distance_ly, self._log)
        self._logline(f"nearest state match: {best.name} ({best.distance_ly:.1f} ly), "
                      f"filters={sorted(slots)}, "
                      f"clipboard={'here' if here else ('ok' if copied else 'failed')}")
        state_note = f" — {best.extra['state']}" if best.extra.get("state") else ""
        line = f"Closest match: {best.name}, {sup.distance_phrase(best.distance_ly)}{state_note}."
        if best.controlling_minor_faction:
            line += f" Controlled by {best.controlling_minor_faction}."
        return line + sup.clipboard_note(best.name, copied, here)

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)
