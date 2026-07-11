"""Star-system search capability — "find the nearest <kind of> star system".

The LLM-native REFERENCE the other search categories follow (Search Prompt 4). Like the
outfitting feature it copies, the tool is STATELESS: the conversation history IS the state, so
refinement is just re-calling with more slots ("...and Empire-aligned" -> another call with
allegiance added), and "new search" is a fresh call. There is NO intent classifier and NO
query state machine — the tool's DESCRIPTION steers the model to fill slots conversationally,
ask when a value is unclear, confirm loosely in words, and search.

Every slot defaults to Any: an unspoken filter is simply absent from the query. The reference
point defaults to the Commander's current system (live ED context, journal fallback), or a
spoken `near` override.

Two guards keep it honest (the hallucination constraints):
  * every spoken enum slot is resolved to a CANONICAL Spansh value (`search/systems.py`)
    before the query is built; an unresolvable value is spoken back as a correction
    ("I didn't recognize 'Klingon' as an allegiance — did you mean Alliance?") and NO search
    runs. Spansh silently ignores an unknown value, so validating here is what stops a
    hallucinated filter from quietly returning the wrong systems.
  * the shared client's `build_query` FAILS LOUD on any param not in the star-systems schema,
    so a slot can't drift away from what the registry advertises.

Everything I/O-bound is injected (`http`, `get_current_system`, `clipboard`) so the capability
is unit-testable offline and the default `pytest` never touches the network or real clipboard
(DESIGN §9). Fail soft throughout — a bad value or a failed lookup is spoken, never raised.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..nav import copy as _default_copy
from ..search import NavError, RequestsHttp, build_query, category, execute_search, parse_systems
from ..search.spansh import Http, _DEFAULT_UA
from ..search.systems import VOCAB, nearest_enum, resolve_enum
from . import _search_support as sup
from .base import HelpMeta, Slot

_TOOL_NAME = "search_star_systems"
_CATEGORY = "star_systems"

# tool arg -> the canonical Spansh enum param it fills. Kept explicit so the arg the model
# fills, the value we validate, and the Spansh filter key are one mapping in one place.
_ENUM_ARGS = {
    "allegiance": "allegiance",
    "government": "government",
    "economy": "primary_economy",
    "security": "security",
    "power": "power",
    "power_state": "power_state",
}
_BOOL_ARGS = {
    "needs_permit": "needs_permit",
    "colonised": "is_colonised",
    "being_colonised": "is_being_colonised",
}
# Population is a range: Spansh wants integer-string min/max, so an unspoken bound gets a
# permissive default (0 .. a value comfortably above the most populous real system).
_POP_FLOOR, _POP_CEIL = 0, 1_000_000_000_000


@dataclass(frozen=True)
class SystemSearchConfig:
    """Immutable snapshot of `[star_systems]`. Off by default; not registered unless enabled."""
    enabled: bool = False
    user_agent: str = _DEFAULT_UA
    search_size: int = 50

    @classmethod
    def from_cfg(cls, cfg: dict) -> "SystemSearchConfig":
        s = cfg.get("star_systems", {}) or {}
        d = cls()
        return cls(
            enabled=bool(s.get("enabled", False)),
            user_agent=str(s.get("user_agent", d.user_agent) or d.user_agent),
            search_size=int(s.get("search_size", d.search_size) or d.search_size),
        )


_DESC = (
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

_SCHEMA_PROPS = {
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


def _build_tool() -> dict:
    return {
        "name": _TOOL_NAME,
        "description": _DESC,
        "input_schema": {"type": "object", "properties": dict(_SCHEMA_PROPS), "required": []},
    }


class SystemSearchCapability:
    """Advertises `search_star_systems` and runs the stateless slot-fill -> validate -> search
    flow. Injected seams (so the default test run is offline): `http` (Spansh poster),
    `get_current_system` (Callable[[], str|None]), `clipboard`."""

    def __init__(
        self,
        config: SystemSearchConfig,
        *,
        http: Http | None = None,
        get_current_system: Callable[[], str | None] | None = None,
        clipboard: Callable[[str], None] = _default_copy,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._cfg = config
        self._http = http if http is not None else RequestsHttp()
        self._current_system = get_current_system
        self._clipboard = clipboard
        self._log = log
        self._spec = category(_CATEGORY)
        self._tool = _build_tool()

    # -- capability interface ---------------------------------------------------------
    def tools(self) -> list[dict]:
        return [self._tool]

    def help_meta(self) -> HelpMeta:
        """Templated help projects from this — nothing here is LLM-generated. One slot per
        refinement, each with spoken phrasings + help text (the registry contract)."""
        return HelpMeta(
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

    def help_vocabulary(self) -> dict[str, list[str]]:
        """Canonical enum values help's failure-recovery matches an unresolved term against, so
        a suggested correction is always a real value (never invented). Keyed by the spoken
        kind a Commander/model would name."""
        return {
            "allegiance": list(VOCAB["allegiance"]),
            "government": list(VOCAB["government"]),
            "economy": list(VOCAB["primary_economy"]),
            "security": list(VOCAB["security"]),
            "power": list(VOCAB["power"]),
            "power state": list(VOCAB["power_state"]),
        }

    def run_tool(self, name: str, inp: dict) -> str:
        if name != _TOOL_NAME:
            return f"Unknown tool: {name}"
        try:
            return self._handle(inp)
        except Exception as e:  # noqa: BLE001 — the voice loop must survive any tool error
            self._logline(f"error: {e}")
            return f"System search error: {e}"

    # -- dialog -----------------------------------------------------------------------
    def _handle(self, inp: dict) -> str:
        slots: dict[str, object] = {}
        caught: list[str] = []          # values understood so far, echoed on a later bad slot

        # Enum slots: resolve each spoken value to a canonical Spansh one, or speak a
        # correction and stop (no search on an unvalidated value).
        for arg, param in _ENUM_ARGS.items():
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
        for arg, param in _BOOL_ARGS.items():
            if inp.get(arg) is not None:
                slots[param] = bool(inp.get(arg))

        # Population range from optional min/max.
        pop = self._population(inp)
        if pop is not None:
            slots["population"] = pop

        if not slots:
            return ("Tell me what kind of system to look for — an allegiance, economy, "
                    "security level, Powerplay power, and so on.")

        return self._do_search(slots, inp)

    def _population(self, inp: dict) -> dict | None:
        lo, hi = inp.get("min_population"), inp.get("max_population")
        if lo is None and hi is None:
            return None
        try:
            lo_i = int(lo) if lo is not None else _POP_FLOOR
            hi_i = int(hi) if hi is not None else _POP_CEIL
        except (TypeError, ValueError):
            return None
        return {"min": max(lo_i, 0), "max": hi_i}

    def _do_search(self, slots: dict, inp: dict) -> str:
        system = self._reference_system(inp)
        if not system:
            return ("I don't know your current system yet — is Elite Dangerous running with "
                    "monitoring on? Jump somewhere, or tell me a system to search near.")
        try:
            payload = build_query(self._spec, slots, system, size=self._cfg.search_size)
            results = execute_search(
                self._spec.endpoint, payload, self._http,
                user_agent=self._cfg.user_agent, reference_system=system,
                subject=self._spec.subject, lookup_name=self._spec.lookup_name)
        except NavError as e:
            self._logline(f"search failed: {e}")
            return str(e)

        records = parse_systems(results)
        if not records:
            return ("I couldn't find a system matching that near you — try relaxing one of the "
                    "filters.")
        best = records[0]
        # Don't copy when the nearest match IS the reference/current system (distance ~0) —
        # you're already there, so copying your own system just clobbers the clipboard.
        here = best.distance_ly < 0.05
        copied = False if here else self._copy(best.name)
        self._logline(f"nearest match: {best.name} ({best.distance_ly:.1f} ly), "
                      f"filters={sorted(slots)}, "
                      f"clipboard={'here' if here else ('ok' if copied else 'failed')}")
        return self._say_result(best, copied, here)

    def _say_result(self, rec, copied: bool, here: bool = False) -> str:
        dist = ("your current system" if rec.distance_ly < 0.05
                else f"{rec.distance_ly:.1f} light-years away")
        traits: list[str] = []
        if rec.allegiance:
            traits.append(rec.allegiance)
        if rec.government and rec.government != "None":
            traits.append(rec.government)
        if rec.security:
            traits.append(f"{rec.security} security")
        trait_note = f" — {', '.join(traits)}" if traits else ""
        line = f"Closest match: {rec.name}, {dist}{trait_note}."
        if here:
            line += " You're already there, so I haven't copied anything."
        else:
            line += (f" I've copied {rec.name} to your clipboard." if copied
                     else f" (Couldn't copy to the clipboard — the system is {rec.name}.)")
        return line

    # -- helpers ----------------------------------------------------------------------
    def _reference_system(self, inp: dict) -> str | None:
        near = inp.get("near")
        if near and str(near).strip():
            return str(near).strip()
        return self._current_system() if self._current_system is not None else None

    def _copy(self, text: str) -> bool:
        try:
            self._clipboard(text)
            return True
        except Exception as e:  # noqa: BLE001 — clipboard is a convenience, never fatal
            self._logline(f"clipboard copy failed: {e}")
            return False

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)
