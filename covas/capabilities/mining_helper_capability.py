"""Mining-helper capability (#45) — plan and run a mining session by voice.

One tool — `plan_mining_session` — ties three pieces together for a named material (Painite, Low
Temperature Diamonds, Tritium…):

  1. HOTSPOT FINDER — the nearest ring hotspot(s) for that material from the Commander's current
     system (or a named start), via Spansh `bodies/search` (`search/mining.find_hotspots`).
  2. BEST SELL PRICE — where to sell the mined commodity for the most credits, FRESHNESS-VERIFIED:
     transient fleet carriers dropped, a stale quote spoken only WITH an age caveat
     (`search/mining.find_best_sell`). This is the differentiator — mining prices swing hard, and the
     highest headline prices are usually years-stale carrier data.
  3. CHECKLIST LOOP — the mining loop dropped as trackable steps (go to the hotspot → mine → sell
     here) onto the Commander's existing checklist, reusing the `Checklist` model the checklist
     capability serves (not a parallel mechanism).

Plus an optional PLOT HANDOFF: the hotspot system handed to the galaxy map (clipboard until the #32
keybind course-set), exactly like the other planners.

LLM-native and stateless like the search/route capabilities: the model fills what it knows (or asks),
the start defaults to the current system, and the conversation is the memory. Everything I/O-bound is
injected (`http`, `get_current_system`, `plotter`, `checklist`) so the default `pytest` run is offline
(DESIGN §9). Fail soft throughout — a bad value, a failed sell lookup, or a failed plot is spoken,
never raised; a missing sell price still yields the hotspot and the loop.

LIVE-VERIFY: the hotspot + sell request/result shapes live in `search/mining.py` and are confirmed
on-hardware per the issue; this capability is unaffected by a field-name correction there.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..nav import copy as _default_copy
from ..search import NavError, RequestsHttp
from ..search.mining import (SELL_PRICE_MAX_AGE_DAYS, Hotspot, SellMarket, find_best_sell,
                             find_hotspots)
from ..search.routes import RoutePlotter, RouteWaypoint
from ..search.spansh import Http, _DEFAULT_UA
from .base import HelpMeta, Slot

_TOOL_NAME = "plan_mining_session"

# Spoken material -> canonical Spansh name (used for BOTH the ring-signal hotspot query and the
# market commodity query — verified live that the two vocabularies match for mineables). Only the
# common variants a Commander says aloud; an unrecognised material falls through title-cased and, if
# Spansh doesn't know it, simply yields no hotspots (fail soft).
_MATERIAL_ALIASES = {
    "painite": "Painite",
    "ltd": "Low Temperature Diamonds", "ltds": "Low Temperature Diamonds",
    "low temp diamonds": "Low Temperature Diamonds",
    "low temperature diamonds": "Low Temperature Diamonds", "diamonds": "Low Temperature Diamonds",
    "void opal": "Void Opal", "void opals": "Void Opal", "opals": "Void Opal", "opal": "Void Opal",
    "tritium": "Tritium", "platinum": "Platinum",
    "alexandrite": "Alexandrite", "benitoite": "Benitoite", "musgravite": "Musgravite",
    "grandidierite": "Grandidierite", "monazite": "Monazite", "serendibite": "Serendibite",
    "rhodplumsite": "Rhodplumsite", "bromellite": "Bromellite", "bertrandite": "Bertrandite",
    "osmium": "Osmium", "samarium": "Samarium",
}


def resolve_material(spoken: str) -> str:
    """A spoken material name mapped to its canonical Spansh name. Falls back to a title-cased
    passthrough for anything not in the alias table (Spansh silently ignores an unknown ring signal,
    so an unrecognised material just yields no hotspots — never an error)."""
    key = " ".join(str(spoken or "").strip().lower().split())
    if not key:
        return ""
    return _MATERIAL_ALIASES.get(key, str(spoken).strip().title())


@dataclass(frozen=True)
class MiningHelperConfig:
    """Immutable snapshot of `[mining_helper]`. Off by default; not registered unless enabled."""
    enabled: bool = False
    user_agent: str = _DEFAULT_UA
    max_price_age_days: int = SELL_PRICE_MAX_AGE_DAYS
    add_to_checklist: bool = True

    @classmethod
    def from_cfg(cls, cfg: dict) -> "MiningHelperConfig":
        m = cfg.get("mining_helper", {}) or {}
        d = cls()
        return cls(
            enabled=bool(m.get("enabled", False)),
            user_agent=str(m.get("user_agent", d.user_agent) or d.user_agent),
            max_price_age_days=int(m.get("max_price_age_days", d.max_price_age_days)
                                   or d.max_price_age_days),
            add_to_checklist=bool(m.get("add_to_checklist", d.add_to_checklist)),
        )


_DESC = (
    "Plan a MINING SESSION for the Commander: find the nearest ring HOTSPOT for a material, the best "
    "FRESH place to sell it, drop the mining loop onto their checklist, and hand the hotspot system "
    "to the galaxy map. Use when they ask where to mine something, to find a Painite / Low "
    "Temperature Diamonds / Void Opal / Tritium hotspot, or to plan a mining run.\n"
    "- `material` is REQUIRED — the thing to mine (e.g. 'Painite', 'Low Temperature Diamonds', "
    "'Tritium'). Ask if they don't say.\n"
    "- The start defaults to their current system; pass `from_system` only to start elsewhere.\n"
    "- `sell_commodity` defaults to the mined material; pass it only if they want to sell something "
    "different.\n"
    "- Optional: `requires_large_pad` (big ship — only sell where a large pad fits), "
    "`max_price_age_days` (only trust sell prices newer than this), `add_to_checklist` (default "
    "true — drop the go-to-hotspot / mine / sell-here loop as trackable steps), `plot` (default "
    "true — copy the hotspot system to the clipboard for the galaxy map).\n"
    "- Relay what the tool returns: the hotspot (system, ring, how many overlaps, how stale the "
    "hotspot data is), the best sell (station, system, price per ton) AND any freshness caveat it "
    "adds, and that the loop was added to their checklist / the system copied for the galaxy map."
)

_SCHEMA_PROPS = {
    "material": {"type": "string",
                 "description": "The material to mine (Painite, Low Temperature Diamonds, Void "
                                "Opal, Tritium, Platinum, Alexandrite, …). Required."},
    "from_system": {"type": "string",
                    "description": "Start system. Omit to use the Commander's current system."},
    "sell_commodity": {"type": "string",
                       "description": "Commodity to price for selling. Omit to use the mined "
                                      "material."},
    "requires_large_pad": {"type": "boolean",
                           "description": "True to only consider sell stations with a large pad."},
    "max_price_age_days": {"type": "integer",
                           "description": "Only trust sell prices newer than this many days. Omit "
                                          "for the configured default."},
    "add_to_checklist": {"type": "boolean",
                         "description": "True (default) to drop the mining loop onto the checklist "
                                        "as trackable steps."},
    "plot": {"type": "boolean",
             "description": "True (default) to copy the hotspot system to the clipboard for the "
                            "galaxy map."},
}


class MiningHelperCapability:
    """Advertises `plan_mining_session` and runs the hotspot + best-sell + checklist + plot flow."""

    def __init__(
        self,
        config: MiningHelperConfig,
        *,
        http: Http | None = None,
        get_current_system: Callable[[], str | None] | None = None,
        checklist=None,
        plotter: RoutePlotter | None = None,
        clipboard: Callable[[str], None] = _default_copy,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._cfg = config
        self._http = http if http is not None else RequestsHttp()
        self._current_system = get_current_system
        self._checklist = checklist
        self._plotter = plotter if plotter is not None else RoutePlotter(clipboard=clipboard, log=log)
        self._log = log

    # -- capability interface ---------------------------------------------------------
    def tools(self) -> list[dict]:
        return [{"name": _TOOL_NAME, "description": _DESC,
                 "input_schema": {"type": "object", "properties": dict(_SCHEMA_PROPS),
                                  "required": ["material"]}}]

    def help_meta(self) -> HelpMeta:
        return HelpMeta(
            category="mining",
            group="navigation and search",
            one_liner=("I plan a mining run — the nearest ring hotspot for a material, the best "
                       "FRESH place to sell it (stale prices flagged), and the go-mine-sell loop "
                       "dropped onto your checklist — and copy the hotspot system for the galaxy "
                       "map."),
            example="where's the nearest Painite hotspot",
            slots=(
                Slot(param="material",
                     phrasings=("what to mine", "the material"),
                     example="find me a Low Temperature Diamonds hotspot",
                     help_text="The material to mine — I find its nearest ring hotspot."),
                Slot(param="requires_large_pad",
                     phrasings=("large pad only", "big ship"),
                     example="a Painite hotspot and somewhere with a large pad to sell it",
                     help_text="Only price sell stations that can take a large pad."),
                Slot(param="max_price_age_days",
                     phrasings=("how fresh the price must be", "the price age"),
                     example="a Void Opal run with prices no older than a day",
                     help_text="Only trust a sell price newer than this many days."),
            ),
            help_when_active=("Tell me what you want to mine and I'll find the nearest hotspot, the "
                              "best fresh place to sell it, and drop the loop onto your checklist."),
        )

    def run_tool(self, name: str, inp: dict) -> str:
        if name != _TOOL_NAME:
            return f"Unknown tool: {name}"
        try:
            return self._handle(inp)
        except Exception as e:  # noqa: BLE001 — the voice loop must survive any tool error
            self._logline(f"error: {e}")
            return f"Mining-helper error: {e}"

    # -- dialog -----------------------------------------------------------------------
    def _handle(self, inp: dict) -> str:
        material = resolve_material(inp.get("material") or "")
        if not material:
            return "What do you want to mine? Tell me the material — Painite, Low Temperature Diamonds, Tritium…"

        system = str(inp.get("from_system") or "").strip() or (
            self._current_system() if self._current_system else None)
        if not system:
            return ("I need a starting system — tell me where to start, or wait until I can read "
                    "your current system from the game.")

        try:
            hotspots = find_hotspots(self._http, material=material, reference_system=system,
                                     user_agent=self._cfg.user_agent)
        except NavError as e:
            self._logline(f"hotspot lookup failed: {e}")
            return str(e)

        if not hotspots:
            return (f"I couldn't find a {material} hotspot near {system}. Double-check the material "
                    "name, or try from a system closer to a mining region.")

        hotspot = hotspots[0]                      # nearest ring (results come distance-sorted)
        commodity = resolve_material(inp.get("sell_commodity") or "") or material
        sell, stale = self._lookup_sell(inp, commodity, system)
        return self._say(hotspot, sell, stale, commodity, inp)

    def _lookup_sell(self, inp: dict, commodity: str, system: str):
        """Best FRESH sell market for `commodity` (or None). Fail soft — a sell-lookup failure must
        not sink the whole plan, so a NavError degrades to 'no price' rather than propagating."""
        age_window = int(inp.get("max_price_age_days") or self._cfg.max_price_age_days)
        try:
            return find_best_sell(self._http, commodity=commodity, reference_system=system,
                                  requires_large_pad=bool(inp.get("requires_large_pad", False)),
                                  max_age_days=age_window, user_agent=self._cfg.user_agent)
        except NavError as e:
            self._logline(f"sell lookup failed: {e}")
            return None, False

    # -- readout + side effects -------------------------------------------------------
    def _say(self, hotspot: Hotspot, sell: SellMarket | None, stale: bool, commodity: str,
             inp: dict) -> str:
        parts = [self._hotspot_phrase(hotspot)]
        parts.append(self._sell_phrase(sell, stale, commodity))
        if self._should(inp, "add_to_checklist", self._cfg.add_to_checklist):
            parts.append(self._add_checklist(hotspot, sell, commodity))
        if self._should(inp, "plot", True):
            parts.append(self._plotter.plot_next([RouteWaypoint(hotspot.system)]))
        self._logline(f"mining {hotspot.material}: hotspot {hotspot.system} ({hotspot.ring}, "
                      f"x{hotspot.count}), sell "
                      f"{(sell.station + ' @ ' + format(sell.sell_price, ',')) if sell else 'none'}"
                      f"{' (stale)' if stale else ''}")
        return " ".join(p for p in parts if p)

    def _hotspot_phrase(self, h: Hotspot) -> str:
        overlaps = f"{h.count} overlapping hotspots" if h.count > 1 else "a hotspot"
        arrival = f", {h.arrival_ls:,.0f} light-seconds in" if h.arrival_ls else ""
        age = h.age_days()
        age_note = f" That ring data is about {age:.0f} days old." if age is not None and age > 30 else ""
        return (f"Nearest {h.material}: the {h.ring} in {h.system}, {h.distance_ly:.1f} light-years "
                f"away{arrival} — {overlaps}.{age_note}")

    def _sell_phrase(self, sell: SellMarket | None, stale: bool, commodity: str) -> str:
        if sell is None:
            return (f"I couldn't find a fresh place to sell {commodity} nearby — sell prices swing, "
                    "so check the market before you commit.")
        where = f"{sell.station} in {sell.system}"
        base = f"Best sell for {commodity}: {sell.sell_price:,} a ton at {where}."
        if stale:
            age = sell.age_days()
            old = f" about {age:.0f} days old" if age is not None else " stale"
            return base + (f" Heads up — that's the freshest quote I found and it's{old}, so it may "
                           "have moved; check the board before you haul.")
        return base

    def _add_checklist(self, hotspot: Hotspot, sell: SellMarket | None, commodity: str) -> str:
        """Drop the mining loop onto the Commander's checklist as trackable steps, reusing the
        shared `Checklist` model. Fail soft — no checklist wired, or a write error, degrades to a
        spoken note rather than sinking the plan."""
        if self._checklist is None:
            return ""
        sell_step = (f"Sell {commodity} at {sell.station} in {sell.system} (~{sell.sell_price:,}/t)"
                     if sell else f"Sell {commodity} at the best market you can find")
        steps = [
            f"Fly to {hotspot.system} and drop into the {hotspot.ring} {hotspot.material} hotspot",
            f"Mine {hotspot.material}",
            sell_step,
        ]
        try:
            for step in steps:
                self._checklist.add(step)
        except Exception as e:  # noqa: BLE001 — a checklist write must never crash the loop
            self._logline(f"checklist add failed: {e}")
            return "I couldn't add the mining loop to your checklist just now."
        return f"I added the {len(steps)}-step mining loop to your checklist."

    @staticmethod
    def _should(inp: dict, key: str, default: bool) -> bool:
        """A boolean tool arg, defaulting to `default` when the LLM omits it (or passes blank)."""
        raw = inp.get(key)
        if raw in (None, ""):
            return default
        return bool(raw)

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)
