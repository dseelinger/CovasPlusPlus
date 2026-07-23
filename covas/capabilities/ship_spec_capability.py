"""Ship-specification capability (issue #83) — grounded specs so newer hulls don't hallucinate.

The problem this solves: asked "how much can a Type-8 carry" or "what pad does the Mandalay
need", the model answers from its training cutoff, so hulls added after that cutoff (Panther
Clipper Mk II, Python Mk II, Type-8, Mandalay, Cobra Mk V, Corsair, …) are unknown or
confidently wrong. This tool grounds the answer in a bundled, refreshable dataset instead.

Same offline, STATELESS pattern as `find_closest_capability`: resolve the (maybe
misheard) name against the curated roster (`resolve_ship`), then — on a single resolved hull —
look its spec up in the bundled table (`nav/ship_specs.py`). Structured guidance the tool
DESCRIPTION steers the model through:
  * AMBIGUOUS (a family like Krait / Cobra / Viper / Asp / Type) -> ask which, don't guess.
  * UNKNOWN                                                      -> say so, offer suggestions.
  * RESOLVED with a bundled spec -> return the real numbers.
  * RESOLVED but no bundled spec (e.g. the Lynx Highliner, or a hull only just learned live)
    -> say the dataset doesn't cover it and to use web search — never invent numbers.

Pure/offline (no network, no clipboard): the only injected seam is an optional live
`ship_index` (fold in Spansh-learned hull NAMES so a brand-new ship still resolves), kept
fail-soft exactly as the find-closest-ship capability does. Default `pytest` stays hermetic.
Fail soft throughout — any error is spoken, never raised into the voice loop.
"""
from __future__ import annotations

from collections.abc import Callable

from ..nav import SHIP_NAMES, AmbiguousShip, ResolvedShip, UnknownShip, get_spec, ship_spec_summary
from ..nav import resolve_ship as _default_resolve
from . import _search_support as sup
from .base import HelpMeta, Slot

_TOOL_NAME = "ship_spec"

_DESC = (
    "Look up the real SPECIFICATIONS of any Elite Dangerous ship from a bundled, maintained "
    "dataset — manufacturer, landing-pad size, hull mass, weapon hardpoints and utility "
    "mounts, core and optional internal slots, maximum cargo capacity, crew seats, speed, "
    "shields and armour. Use THIS tool whenever the Commander asks what a ship IS or what it "
    "can do ('how much cargo can a Type-8 carry', 'what pad does a Mandalay need', 'how many "
    "hardpoints has the Corsair', 'is the Python Mk II big'). Your own training data is "
    "cutoff-limited and does NOT reliably know newer hulls — ALWAYS call this instead of "
    "answering ship specs from memory.\n"
    "1. Normalize the spoken ship name to a real ship (e.g. 'conda' -> Anaconda, 'fdl' -> "
    "Fer-de-Lance, 'panther' -> Panther Clipper Mk II) and call with `ship` set to your best "
    "interpretation.\n"
    "2. If it replies that the name is AMBIGUOUS (a family: Krait, Cobra, Viper, Asp, "
    "Diamondback, Type), ask which one it lists — NEVER guess. If UNKNOWN, say so and offer "
    "the suggestions.\n"
    "3. On a resolved ship it returns the full spec — relay just the part the Commander asked "
    "about, in your own voice. For the Commander's OWN ship's fitted values (their real cargo, "
    "jump range, engineering) prefer the loadout tools. Jump range is NOT in this dataset "
    "because it depends on the fitted drive and loadout; if asked, use the loadout tool for "
    "their own ship or web search otherwise — do not invent a figure. If the tool says it has "
    "no data for a resolved hull, tell the Commander and offer to web-search — never confabulate."
)

_SCHEMA_PROPS = {
    "ship": {
        "type": "string",
        "description": "Your best interpretation of the ship name (e.g. 'Anaconda', 'Type-8', "
                       "'Python Mk II', 'Mandalay', 'Corsair'). For an ambiguous family "
                       "(Krait, Cobra, Viper, Asp, Type), pass what you have and the tool asks "
                       "which model.",
    },
}


class ShipSpecCapability:
    """Advertises `ship_spec` and runs the resolve -> lookup dialog. `resolve` is injected
    (defaults to the real offline resolver) for tests; `ship_index` is an optional live roster
    of newly-released hull names, folded into resolution, kept fail-soft."""

    def __init__(
        self,
        *,
        resolve: Callable[..., object] = _default_resolve,
        ship_index: object | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._resolve = resolve
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
        return HelpMeta(
            category="ship specs",
            group="your ship",
            one_liner=("I look up any ship's real specs — pad size, hull mass, hardpoints, "
                       "slots, cargo capacity — from a bundled dataset, so even the newest "
                       "hulls are accurate."),
            example="how much cargo can a Type-8 carry",
            slots=(
                Slot(param="ship",
                     phrasings=("the ship name", "the ship"),
                     example="what are the specs on a Mandalay",
                     help_text="Name the ship you're curious about — a Python Mk II, a "
                               "Corsair, a Type-9, and so on. If it's a family like Krait or "
                               "Cobra, I'll ask which model."),
            ),
            help_when_active=("Tell me the ship — and which model if I ask — and I'll read out "
                              "its specs."),
        )

    def help_vocabulary(self) -> dict[str, list[str]]:
        """Canonical ship names help's failure-recovery mode matches an unresolved term
        against, so a suggested correction is always a real ship."""
        return {"ship": list(SHIP_NAMES)}

    def run_tool(self, name: str, inp: dict) -> str:
        if name != _TOOL_NAME:
            return f"Unknown tool: {name}"
        try:
            return self._handle(inp)
        except Exception as e:  # noqa: BLE001 — the voice loop must survive any tool error
            self._logline(f"error: {e}")
            return f"Ship spec error: {e}"

    # -- dialog -----------------------------------------------------------------------
    def _handle(self, inp: dict) -> str:
        ship = str(inp.get("ship") or "").strip()
        if not ship:
            return "Which ship's specifications would you like?"

        extra = self._extra_names()
        outcome = self._resolve(ship, extra_names=extra) if extra else self._resolve(ship)

        if isinstance(outcome, UnknownShip):
            if outcome.suggestions:
                return (f"I don't recognize '{outcome.query}' as a ship. Did you mean "
                        f"{sup.or_list(outcome.suggestions)}?")
            return (f"I don't recognize '{outcome.query}' as a ship. Tell me the ship name "
                    "another way.")
        if isinstance(outcome, AmbiguousShip):
            return (f"That could be a few ships — {sup.or_list(outcome.candidates)}. Which one?")
        if isinstance(outcome, ResolvedShip):
            return self._describe(outcome)
        return "I couldn't interpret that ship — try naming it another way."

    def _describe(self, resolved: ResolvedShip) -> str:
        spec = get_spec(resolved.id)
        if spec is None:
            # Resolved to a real hull, but the bundled table has no numbers for it (the Lynx
            # Highliner, or a name only just learned live). Say so — do NOT invent specs.
            self._logline(f"no bundled spec for {resolved.label} ({resolved.id})")
            return (f"I've got the {resolved.label} in my roster but no spec data bundled for "
                    "it yet — I'd rather web-search than guess the numbers. Want me to?")
        self._logline(f"spec for {resolved.label}: pad {spec.pad}, hull {spec.hull_mass:g}t, "
                      f"cargo {spec.max_cargo}t")
        return ship_spec_summary(spec)

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

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)
