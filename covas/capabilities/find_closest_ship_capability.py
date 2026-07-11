"""Find-closest-ship capability — "find the closest station that sells SHIP X".

The shipyard sibling of `find_closest_capability.py` (outfitting), built on the exact same
LLM-native, STATELESS pattern: the conversation history *is* the state, so each re-call just
passes a more-specific ship name (loose family -> a picked variant). The tool is a pure
function of its arguments; there is no pending-request object.

Flow the tool DESCRIPTION steers the LLM through:
  1. Commander asks for the closest <ship>.
  2. The LLM normalizes the (maybe misheard) name and calls the tool with its best guess.
     The tool validates against the offline roster and returns structured guidance:
       - AMBIGUOUS  → ask which family member they meant (Krait MkII vs Phantom, Cobra
                      MkIII/IV/V, Viper III/IV, Asp Explorer/Scout, Type-6…11), don't search.
       - UNKNOWN    → say so, offer suggestions, don't search.
       - RESOLVED   → the ship is pinned to its exact Spansh name, so search immediately.
  3. On "cancel / never mind" the LLM simply drops the request (it doesn't call the tool) —
     verbal cancel is an LLM-recognized intent, separate from the hard PTT-cancel.

Ships have no size/mount to fill in (unlike modules), so there is no NEED_ATTRS step and no
confirmation gate: a resolved ship is a single, unambiguous decision, so it searches at once
(read-only lookup). The one networked step reads the current system (ED context; journal
fallback), finds the nearest station selling the ship, copies the SYSTEM name to the clipboard
(unless it's the current system — the N3 rule), and returns a short spoken line.

Everything I/O-bound is injected (`http`, `get_current_system`, `clipboard`) so the whole
capability is unit-testable offline and the default `pytest` never hits the network or the
real clipboard (DESIGN §9). Fail soft throughout — an unknown ship or a failed lookup is
spoken, never raised into the loop.
"""
from __future__ import annotations

from typing import Callable

from ..nav import (AmbiguousShip, NavError, ResolvedShip, UnknownShip, SHIP_NAMES,
                   copy as _default_copy, find_closest_ship, resolve_ship as _default_resolve)
from ..nav.closest import RequestsHttp
from ..search.spansh import Http
from . import _search_support as sup
from .base import HelpMeta, Slot
# Reuse the outfitting capability's [nav] config snapshot verbatim — pad default, base URL,
# user-agent, search size, enable toggle are shared (the prompt: reuse [nav]).
from .find_closest_capability import NavConfig

_TOOL_NAME = "find_closest_ship"

_DESC = (
    "Find the closest Elite Dangerous station that SELLS a given SHIP (from a shipyard), by "
    "distance from the Commander's current system, and copy that system's name to the "
    "clipboard. Use THIS tool whenever the Commander wants to buy/find a whole SHIP ('where "
    "can I buy an Anaconda', 'find the closest Krait', 'nearest Python'). For an outfitting "
    "MODULE (multi-cannon, fuel scoop, shield generator) use find_closest_module instead; for "
    "the nearest station by SERVICE/TYPE/PAD use search_stations. Resolve the ship "
    "CONVERSATIONALLY, then search:\n"
    "1. Normalize the spoken ship name to a real ship (e.g. 'conda' -> Anaconda, 'fdl' -> "
    "Fer-de-Lance, 'clipper' -> Imperial Clipper) and call this tool with `ship` set to your "
    "best interpretation.\n"
    "2. The tool replies with structured guidance. If it says the name is AMBIGUOUS (a "
    "family like Krait, Cobra, Viper, Asp, Diamondback, or Type), ask which one it lists — "
    "NEVER guess. If UNKNOWN, say so and offer the suggestions.\n"
    "3. As soon as the ship is resolved to a single model, the tool searches immediately and "
    "returns the nearest station — there's no size/mount to ask about and no separate confirm "
    "step. If the Commander says 'cancel' / 'never mind', DROP the request and acknowledge — "
    "do NOT call this tool.\n"
    "It is stateless — re-call it each turn with the most specific name known so far (family "
    "-> picked model). When the tool returns a result, relay the station, system, and "
    "distance, and ALWAYS tell the Commander that the system name has been copied to their "
    "clipboard."
)

_SCHEMA_PROPS = {
    "ship": {
        "type": "string",
        "description": "Your best interpretation of the ship name (e.g. 'Anaconda', 'Krait "
                       "Phantom', 'Type-9 Heavy', 'Fer-de-Lance'). For an ambiguous family "
                       "(Krait, Cobra, Viper, Asp, Type), pass what you have and the tool will "
                       "ask which model.",
    },
    "pad_size": {
        "type": "string",
        "description": "Required landing-pad size (S/M/L) for the Commander's ship. Omit to "
                       "use the configured default.",
    },
}


class FindClosestShipCapability:
    """Advertises `find_closest_ship` and runs the resolve → search dialog.

    Injected seams (all so the default test run is offline):
      * `http` — the Http poster for Spansh (RequestsHttp in the app; a fake in tests).
      * `get_current_system` — Callable[[], str|None] returning the Commander's current
        system (ED context, with a journal fallback the app wires up), or None.
      * `resolve` / `search` / `clipboard` — pure/offline deps, defaulted to the real ones
        but overridable in tests.
    """

    def __init__(
        self,
        config: NavConfig,
        *,
        http: Http | None = None,
        get_current_system: Callable[[], str | None] | None = None,
        resolve: Callable[..., object] = _default_resolve,
        search: Callable[..., object] = find_closest_ship,
        clipboard: Callable[[str], None] = _default_copy,
        ship_index: object | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._cfg = config
        self._http = http if http is not None else RequestsHttp()
        self._current_system = get_current_system
        self._resolve = resolve
        self._search = search
        self._clipboard = clipboard
        # Optional live roster: surfaces ship names Spansh knows that the bundle is missing
        # (newly-released hulls), folded into resolution so a new ship is findable with no code
        # change. None -> bundled roster only (the default; keeps tests offline).
        self._ship_index = ship_index
        self._log = log

    # -- capability interface ---------------------------------------------------------
    def tools(self) -> list[dict]:
        return [{
            "name": _TOOL_NAME,
            "description": _DESC,
            "input_schema": {"type": "object", "properties": dict(_SCHEMA_PROPS),
                             "required": ["ship"]},
        }]

    def help_meta(self) -> HelpMeta:
        """Describe this capability for the help subsystem. Templated help is projected from
        this — nothing here is generated by an LLM."""
        return HelpMeta(
            category="shipyards",
            group="navigation and search",
            one_liner=("I find the closest station selling a given ship and copy that system "
                       "to your clipboard."),
            example="find the closest Anaconda",
            slots=(
                Slot(param="ship",
                     phrasings=("the ship name", "the ship"),
                     example="where can I buy a Python",
                     help_text="Name the ship you want — an Anaconda, a Python, a Krait, a "
                               "Type-9, and so on. If it's a family like Krait or Cobra, I'll "
                               "ask which model."),
                Slot(param="pad_size",
                     phrasings=("a landing pad size", "a pad size"),
                     example="somewhere with a large pad",
                     help_text="Restrict to stations with a given landing-pad size — small, "
                               "medium, or large."),
            ),
            help_when_active=("Tell me the ship — and which model if I ask — and I'll find the "
                              "nearest station that sells it."),
        )

    def help_vocabulary(self) -> dict[str, list[str]]:
        """The canonical ship names help's failure-recovery mode matches an unresolved term
        against, so a suggested correction is always a real ship (never invented)."""
        return {"ship": list(SHIP_NAMES)}

    def run_tool(self, name: str, inp: dict) -> str:
        if name != _TOOL_NAME:
            return f"Unknown tool: {name}"
        try:
            return self._handle(inp)
        except Exception as e:  # noqa: BLE001 — the voice loop must survive any tool error
            self._logline(f"error: {e}")
            return f"Ship lookup error: {e}"

    # -- dialog -----------------------------------------------------------------------
    def _handle(self, inp: dict) -> str:
        ship = str(inp.get("ship") or "").strip()
        if not ship:
            return "Which ship should I find the closest station for?"

        # Fold in any hulls the live index learned Spansh knows but the bundle doesn't. Only
        # pass the kwarg when there ARE extras, so the injected resolve seam stays simple.
        extra = self._extra_names()
        outcome = self._resolve(ship, extra_names=extra) if extra else self._resolve(ship)

        if isinstance(outcome, UnknownShip):
            return self._say_unknown(outcome)
        if isinstance(outcome, AmbiguousShip):
            return self._say_ambiguous(outcome)
        if isinstance(outcome, ResolvedShip):
            return self._do_search(outcome, inp)
        return "I couldn't interpret that ship — try naming it another way."

    def _say_unknown(self, o: UnknownShip) -> str:
        if o.suggestions:
            return (f"I don't recognize '{o.query}' as a ship. Did you mean "
                    f"{sup.or_list(o.suggestions)}?")
        return (f"I don't recognize '{o.query}' as a ship. Tell me the ship name another way.")

    def _say_ambiguous(self, o: AmbiguousShip) -> str:
        return f"That could be a few ships — {sup.or_list(o.candidates)}. Which one?"

    def _do_search(self, resolved: ResolvedShip, inp: dict) -> str:
        """The one networked step — fires as soon as a ship resolves to a single model."""
        system = self._current_system() if self._current_system is not None else None
        pad = self._pad_size(inp)
        try:
            result = self._search(
                resolved, system, self._http,
                pad_size=pad,
                base_url=self._cfg.base_url,
                user_agent=self._cfg.user_agent,
                search_size=self._cfg.search_size,
            )
        except NavError as e:
            self._logline(f"search failed for {resolved.label}: {e}")
            return str(e)

        # Copy the SYSTEM name unless the station is in the Commander's current system (N3
        # rule): you're already there, so copying your own system just clobbers the clipboard.
        copied, here = sup.deliver_system(self._clipboard, result.system, result.distance_ly,
                                          self._log)
        self._logline(f"nearest {resolved.label}: {result.station} in {result.system} "
                      f"({result.distance_ly:.1f} ly), pad {result.pad}, "
                      f"clipboard={'here' if here else ('ok' if copied else 'failed')}")
        return self._say_result(resolved, result, copied, here)

    def _say_result(self, resolved: ResolvedShip, result, copied: bool, here: bool) -> str:
        line = (f"Closest {resolved.label}: {result.station} in {result.system}, "
                f"{sup.distance_phrase(result.distance_ly)}. Largest pad {result.pad}.")
        arrival = result.extra.get("distance_to_arrival")
        if isinstance(arrival, (int, float)) and arrival >= 1:
            line += f" About {arrival:,.0f} light-seconds from the star."
        price = result.extra.get("ship_price")
        if isinstance(price, (int, float)) and price >= 1:
            line += f" It runs about {price:,.0f} credits."
        return line + sup.clipboard_note(result.system, copied, here)

    # -- helpers ----------------------------------------------------------------------
    def _extra_names(self) -> tuple[str, ...]:
        """Newly-released ship names from the live index (empty until its background fetch
        lands, or if it's absent/unreachable). Fail-soft — a broken index never blocks a
        lookup; resolution just uses the bundled roster."""
        idx = self._ship_index
        if idx is None:
            return ()
        try:
            return tuple(idx.extra_names())
        except Exception as e:  # noqa: BLE001 — the live roster is a bonus, never fatal
            self._logline(f"ship index unavailable: {e}")
            return ()

    def _pad_size(self, inp: dict) -> str | None:
        """The pad constraint for this search: the tool arg if given, else the config default.
        'any' / 'none' / '' disables it."""
        raw = inp.get("pad_size")
        pad = str(raw).strip() if raw is not None else self._cfg.default_pad_size
        if not pad or pad.lower() in ("any", "none", "n/a"):
            return None
        return pad

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)
