"""Minor-faction search capability — "find the nearest system where <faction> is present".

Same stateless, LLM-native shape as the star-systems reference. Finds the nearest system by a
minor faction's presence, plus allegiance / government / active state. The "controls vs is
present" distinction is just a slot value, flipped by how the Commander speaks it (default: is
present) — no state machine.

Faction NAMES are free text (there are ~90k factions; the search validates them by returning
results or not, and the spoken answer is always a real system name from Spansh). Allegiance,
government, and faction state ARE validated against the canonical vocabulary before the query.
I/O is injected so the default test run is offline (DESIGN §9); fail soft throughout.
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

_TOOL_NAME = "search_minor_factions"

_DESC = (
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

_SCHEMA_PROPS = {
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


class MinorFactionSearchCapability:
    """Advertises `search_minor_factions`; stateless slot-fill -> validate -> search."""

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
        self._spec = category("minor_factions")

    def tools(self) -> list[dict]:
        return [{"name": _TOOL_NAME, "description": _DESC,
                 "input_schema": {"type": "object", "properties": dict(_SCHEMA_PROPS),
                                  "required": []}}]

    def help_meta(self) -> HelpMeta:
        return HelpMeta(
            category="minor factions",
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

    def help_vocabulary(self) -> dict[str, list[str]]:
        return {"allegiance": list(VOCAB["allegiance"]),
                "government": list(VOCAB["government"]),
                "faction state": list(FACTION_STATES)}

    def run_tool(self, name: str, inp: dict) -> str:
        if name != _TOOL_NAME:
            return f"Unknown tool: {name}"
        try:
            return self._handle(inp)
        except Exception as e:  # noqa: BLE001 — the voice loop must survive any tool error
            self._logline(f"error: {e}")
            return f"Minor-faction search error: {e}"

    def _handle(self, inp: dict) -> str:
        slots: dict[str, object] = {}
        caught: list[str] = []          # values understood so far, echoed on a later bad slot

        faction = inp.get("faction")
        if faction and str(faction).strip():
            # Resolve the spoken name to Spansh's exact string (mishears -> 0 systems otherwise);
            # an unresolved name offers real corrections rather than searching on nothing.
            canon, recover_msg = sup.faction_or_recovery(self._factions, faction)
            if recover_msg:
                self._logline(f"unresolved faction '{faction}'")
                return recover_msg
            # Polarity is a slot value, not a mode: controls -> controlling_minor_faction,
            # else the default 'is present' -> minor_faction_presences.
            controls = bool(inp.get("controls"))
            slots["controlling_minor_faction" if controls else "minor_faction_presences"] = canon
            caught.append(f"{canon} ({'controls' if controls else 'present'})")

        for arg in ("allegiance", "government"):
            raw = inp.get(arg)
            if raw not in (None, ""):
                val = resolve_enum(arg, raw)
                if val is None:
                    return sup.recovery(raw, arg, nearest_enum(arg, raw), caught=caught)
                slots[arg] = val
                caught.append(f"{val} {arg}")

        state = inp.get("state")
        if state not in (None, ""):
            val = resolve_state(state)
            if val is None:
                return sup.recovery(state, "faction state", nearest_state(state), caught=caught)
            slots["controlling_minor_faction_state"] = val

        if not slots:
            return ("Name the minor faction, or give me an allegiance, government, or state to "
                    "look for. (Say 'never mind' to drop it.)")

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
            return ("I couldn't find a system matching that near you — try relaxing one of the "
                    "filters, or check the faction name.")
        best = systems[0]
        copied, here = sup.deliver_system(self._clipboard, best.name, best.distance_ly, self._log)
        self._logline(f"nearest faction match: {best.name} ({best.distance_ly:.1f} ly), "
                      f"filters={sorted(slots)}, "
                      f"clipboard={'here' if here else ('ok' if copied else 'failed')}")
        return self._say(best, slots, copied, here)

    def _say(self, rec, slots: dict, copied: bool, here: bool = False) -> str:
        dist = sup.distance_phrase(rec.distance_ly)
        faction = slots.get("controlling_minor_faction") or slots.get("minor_faction_presences")
        if faction:
            # Ground the answer in the faction the Commander ASKED about. A presence search can
            # land in a system a DIFFERENT faction controls; leading with that controller reads
            # as a miss, and the model then reports failure over a success (observed live).
            controls = "controlling_minor_faction" in slots
            line = f"{faction} {'controls' if controls else 'is present in'} {rec.name}, {dist}."
            controller = rec.controlling_minor_faction
            if not controls and controller and controller != faction:
                line += f" The system's controlled by {controller}."
        else:
            line = f"Closest match: {rec.name}, {dist}."
            if rec.controlling_minor_faction:
                line += f" Controlled by {rec.controlling_minor_faction}."
        return line + sup.clipboard_note(rec.name, copied, here)

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)
