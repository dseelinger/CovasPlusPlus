"""Route-planning capability — the FOUNDATION proof for the Spansh route planners (#41).

The route foundation (`search/routes.py`) stands up the async submit+poll client, the freshness
discipline, and the galaxy-map plot-handoff seam. This capability is the thin surface that proves
them end to end with ONE tool — `plan_trade_route` — so the Commander can voice-plan a trade loop
from where they're docked, hear the best first hop (with an age caveat when prices are stale), and
have the next stop handed to the galaxy map (clipboard until the keybind galaxy-map automation, #32,
lands). The richer planners — Road to Riches (#42), the full trade planner (#44), mining (#45) —
add sibling tools on the SAME client; they don't touch this file.

LLM-native and stateless, like the search capabilities: the model fills the numbers it knows (or
asks the Commander), the start defaults to the current docked station, and the conversation is the
memory. Everything I/O-bound is injected (`http`, `get_current_system`, `get_current_station`,
`plotter`, `sleep`) so the default `pytest` run is offline (DESIGN §9). Fail soft throughout — a bad
value or a failed plot is spoken, never raised.

LIVE-VERIFY: the trade request/result shape lives in `search/routes.py` (`build_trade_request` /
`parse_trade_route`) and is confirmed on-hardware per the issue; this capability is unaffected by a
field-name correction there.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from ..nav import copy as _default_copy
from ..search import NavError, RequestsHttp
from ..search.routes import (TRADE_ROUTE_URL, RoutePlotter, RouteWaypoint, build_trade_request,
                             parse_trade_route, stale_age_caveat, submit_and_poll)
from ..search.spansh import Http, _DEFAULT_UA
from .base import HelpMeta, Slot

_TOOL_NAME = "plan_trade_route"


@dataclass(frozen=True)
class RoutePlanConfig:
    """Immutable snapshot of `[route_plan]`. Off by default; not registered unless enabled."""
    enabled: bool = False
    user_agent: str = _DEFAULT_UA
    default_max_hops: int = 4
    max_price_age_days: int = 2

    @classmethod
    def from_cfg(cls, cfg: dict) -> "RoutePlanConfig":
        r = cfg.get("route_plan", {}) or {}
        d = cls()
        return cls(
            enabled=bool(r.get("enabled", False)),
            user_agent=str(r.get("user_agent", d.user_agent) or d.user_agent),
            default_max_hops=int(r.get("default_max_hops", d.default_max_hops) or d.default_max_hops),
            max_price_age_days=int(r.get("max_price_age_days", d.max_price_age_days)
                                  or d.max_price_age_days),
        )


_DESC = (
    "Plan a profitable TRADE ROUTE (a buy/sell loop) for the Commander using Spansh, starting from "
    "the station they're currently docked at, and hand the first destination to the galaxy map. "
    "Use when they ask to plan trading, find a trade run, or 'where should I trade from here'.\n"
    "- Fill the numbers you know or can infer, and ASK for any you can't: `capital` (credits to "
    "spend), `max_cargo` (cargo capacity in tons), `jump_range` (laden jump range in ly). "
    "`max_hops` defaults sensibly; `requires_large_pad` if they fly a big ship.\n"
    "- The start defaults to where they're docked. If they aren't docked, pass `from_system` + "
    "`from_station`, or ask where to start.\n"
    "- Relay the top hop: what to buy, where to sell, and the profit per ton; if the tool adds a "
    "freshness caveat, pass it along. Tell them the next stop was copied to their clipboard for the "
    "galaxy map."
)

_SCHEMA_PROPS = {
    "capital": {"type": "integer", "description": "Credits available to spend on cargo."},
    "max_cargo": {"type": "integer", "description": "Cargo capacity in tons."},
    "jump_range": {"type": "number", "description": "Laden jump range in light-years."},
    "max_hops": {"type": "integer",
                 "description": "Maximum number of hops in the loop. Omit for the default."},
    "requires_large_pad": {"type": "boolean",
                           "description": "True if the ship needs a large landing pad."},
    "from_system": {"type": "string",
                    "description": "Start system. Omit to use the current (docked) system."},
    "from_station": {"type": "string",
                     "description": "Start station. Omit to use the current docked station."},
}


class RoutePlanCapability:
    """Advertises `plan_trade_route` and runs it over the shared route client + plot seam."""

    def __init__(
        self,
        config: RoutePlanConfig,
        *,
        http: Http | None = None,
        get_current_system: Callable[[], str | None] | None = None,
        get_current_station: Callable[[], str | None] | None = None,
        plotter: RoutePlotter | None = None,
        clipboard: Callable[[str], None] = _default_copy,
        sleep: Callable[[float], None] = time.sleep,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._cfg = config
        self._http = http if http is not None else RequestsHttp()
        self._current_system = get_current_system
        self._current_station = get_current_station
        self._plotter = plotter if plotter is not None else RoutePlotter(clipboard=clipboard, log=log)
        self._sleep = sleep
        self._log = log

    # -- capability interface ---------------------------------------------------------
    def tools(self) -> list[dict]:
        return [{"name": _TOOL_NAME, "description": _DESC,
                 "input_schema": {"type": "object", "properties": dict(_SCHEMA_PROPS),
                                  "required": []}}]

    def help_meta(self) -> HelpMeta:
        return HelpMeta(
            category="trade routes",
            group="navigation and search",
            one_liner=("I plan a profitable trade loop from where you're docked — what to buy, "
                       "where to sell, and the profit — and copy the next stop to your clipboard "
                       "for the galaxy map."),
            example="plan me a trade route from here",
            slots=(
                Slot(param="max_cargo",
                     phrasings=("your cargo capacity", "how much cargo"),
                     example="plan a trade route with 720 tons of cargo",
                     help_text="Your cargo capacity in tons — it sizes the profit per hop."),
                Slot(param="jump_range",
                     phrasings=("your jump range", "how far you jump"),
                     example="a trade route for a 30 light-year jump range",
                     help_text="Your laden jump range in light-years — it bounds each hop."),
                Slot(param="capital",
                     phrasings=("your budget", "credits to spend"),
                     example="a trade route with 100 million to spend",
                     help_text="Credits available to buy cargo."),
            ),
            help_when_active=("Tell me your cargo capacity, jump range, and budget, and I'll plan "
                              "a trade loop from the station you're docked at."),
        )

    def run_tool(self, name: str, inp: dict) -> str:
        if name != _TOOL_NAME:
            return f"Unknown tool: {name}"
        try:
            return self._handle(inp)
        except Exception as e:  # noqa: BLE001 — the voice loop must survive any tool error
            self._logline(f"error: {e}")
            return f"Trade-route error: {e}"

    # -- dialog -----------------------------------------------------------------------
    def _handle(self, inp: dict) -> str:
        system = str(inp.get("from_system") or "").strip() or (
            self._current_system() if self._current_system else None)
        station = str(inp.get("from_station") or "").strip() or (
            self._current_station() if self._current_station else None)
        if not (system and station):
            return ("I need a starting point — dock at a station, or tell me the system and "
                    "station to start the trade route from.")

        need = self._required_numbers(inp)
        if need:
            return f"To plan a trade route I need {need}. What should I use?"

        try:
            params = build_trade_request(
                from_system=system, from_station=station,
                capital=int(inp["capital"]), max_cargo=int(inp["max_cargo"]),
                jump_range=float(inp["jump_range"]),
                max_hops=int(inp.get("max_hops") or self._cfg.default_max_hops),
                requires_large_pad=bool(inp.get("requires_large_pad", False)),
                max_price_age_days=self._cfg.max_price_age_days)
            result = submit_and_poll(self._http, TRADE_ROUTE_URL, params,
                                     user_agent=self._cfg.user_agent, sleep=self._sleep,
                                     subject="the trade planner", lookup_name="trade route")
        except NavError as e:
            self._logline(f"trade plan failed: {e}")
            return str(e)

        hops = parse_trade_route(result)
        if not hops:
            return ("Spansh didn't find a profitable route from there with those limits — try more "
                    "cargo, a bigger budget, or a longer jump range.")
        return self._say_and_plot(hops, station)

    def _required_numbers(self, inp: dict) -> str | None:
        """Which of the three essential numbers are missing, as a spoken phrase (or None)."""
        missing = [label for key, label in
                   (("capital", "your budget in credits"), ("max_cargo", "your cargo capacity"),
                    ("jump_range", "your jump range"))
                   if inp.get(key) in (None, "")]
        if not missing:
            return None
        if len(missing) == 1:
            return missing[0]
        return ", ".join(missing[:-1]) + f", and {missing[-1]}"

    def _say_and_plot(self, hops, start_station: str) -> str:
        top = hops[0]
        line = (f"Best run from {start_station}: buy {top.commodity} and sell it at "
                f"{top.destination_station} in {top.destination_system} for about "
                f"{top.profit_per_unit:,} credits a ton.")
        caveat = stale_age_caveat(hops, max_age_days=self._cfg.max_price_age_days)
        if caveat:
            line += f" ({caveat})"
        # Plot the first destination — that's where the Commander flies to sell.
        plot = self._plotter.plot_next([RouteWaypoint(top.destination_system)])
        self._logline(f"trade route: {len(hops)} hops, first -> {top.destination_system}"
                      f"{' (stale)' if caveat else ''}")
        return f"{line} {plot}"

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)
