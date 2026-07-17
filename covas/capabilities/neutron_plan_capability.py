"""Neutron / long-range galaxy route capability (#43) — the second planner on the #41 foundation.

The trade planner (`route_plan_capability.py`) proved the async Spansh route client end to end.
This capability adds the neutron-highway workhorse: given a DESTINATION system (and optionally a
start, defaulting to the Commander's current system) + their laden jump range (+ optional
efficiency), it plots the long-range route via the SAME galaxy plotter — `build_galaxy_request` +
`submit_and_poll(ROUTE_URL, ...)` + `parse_galaxy_route` — speaks the summary (total jumps, number
of waypoints, the FIRST waypoint), and hands that first waypoint to the galaxy map via
`RoutePlotter.plot_next` (clipboard until the #32 keybind course-set lands).

This is Elite's long-range travel workhorse: the galaxy plotter with efficiency > 0 rides the
neutron highway to get from A to B in FAR fewer jumps than a straight route. Unlike the trade
planner there's no market data, so there's no freshness caveat — the plotted systems are static.

LLM-native and stateless like its sibling: the model fills the numbers it knows (or asks), the
start defaults to the current system, and the conversation is the memory. Everything I/O-bound is
injected (`http`, `get_current_system`, `plotter`, `sleep`) so the default `pytest` run is offline
(DESIGN §9). Fail soft throughout — a bad value or a failed plot is spoken, never raised.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from ..nav import copy as _default_copy
from ..search import NavError, RequestsHttp
from ..search.routes import (ROUTE_URL, RoutePlotter, build_galaxy_request, parse_galaxy_route,
                             submit_and_poll)
from ..search.spansh import Http, _DEFAULT_UA
from .base import HelpMeta, Slot

_TOOL_NAME = "plot_neutron_route"

# Spansh efficiency: 1–100, higher = fewer jumps by taking more neutron detours. 60 is the site's
# sensible middle ground (fewer jumps without wildly long neutron hops).
_MIN_EFFICIENCY = 1
_MAX_EFFICIENCY = 100


@dataclass(frozen=True)
class NeutronPlanConfig:
    """Immutable snapshot of `[neutron_plan]`. Off by default; not registered unless enabled."""
    enabled: bool = False
    user_agent: str = _DEFAULT_UA
    default_efficiency: int = 60

    @classmethod
    def from_cfg(cls, cfg: dict) -> "NeutronPlanConfig":
        n = cfg.get("neutron_plan", {}) or {}
        d = cls()
        eff = int(n.get("default_efficiency", d.default_efficiency) or d.default_efficiency)
        return cls(
            enabled=bool(n.get("enabled", False)),
            user_agent=str(n.get("user_agent", d.user_agent) or d.user_agent),
            default_efficiency=min(_MAX_EFFICIENCY, max(_MIN_EFFICIENCY, eff)),
        )


_DESC = (
    "Plot a NEUTRON / long-range GALAXY route for the Commander using Spansh's neutron-highway "
    "plotter, then hand the first waypoint to the galaxy map. Use when they ask to plot a route to "
    "a distant system, a neutron route, a long-range or galaxy route, or 'get me to <system> in as "
    "few jumps as possible'.\n"
    "- `to_system` (destination) is REQUIRED — ask for it if they don't say where.\n"
    "- `jump_range` (their LADEN jump range in ly) is REQUIRED — ask for it if missing; it bounds "
    "each jump.\n"
    "- The start defaults to their current system; pass `from_system` only to start elsewhere.\n"
    "- `efficiency` (1-100, higher = fewer jumps via more neutron boosts) defaults sensibly; omit "
    "unless they ask for a more efficient or more direct route.\n"
    "- Relay the summary: total jumps, how many waypoints, and the first one. Tell them the first "
    "waypoint was copied to their clipboard for the galaxy map."
)

_SCHEMA_PROPS = {
    "to_system": {"type": "string",
                  "description": "Destination system to plot a route to. Required."},
    "jump_range": {"type": "number",
                   "description": "Laden jump range in light-years. Required — ask if unknown."},
    "from_system": {"type": "string",
                    "description": "Start system. Omit to use the Commander's current system."},
    "efficiency": {"type": "integer",
                   "description": "Neutron efficiency 1-100 (higher = fewer jumps). Omit for the "
                                  "default."},
}


class NeutronPlanCapability:
    """Advertises `plot_neutron_route` and runs it over the shared galaxy plotter + plot seam."""
    # Tiering group (issue #84): the token-budget cluster this capability's tools belong
    # to; the level filter (covas/tiering.py) keeps or drops the whole group as a unit.
    TIERING_GROUP = "search"

    def __init__(
        self,
        config: NeutronPlanConfig,
        *,
        http: Http | None = None,
        get_current_system: Callable[[], str | None] | None = None,
        plotter: RoutePlotter | None = None,
        clipboard: Callable[[str], None] = _default_copy,
        sleep: Callable[[float], None] = time.sleep,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._cfg = config
        self._http = http if http is not None else RequestsHttp()
        self._current_system = get_current_system
        self._plotter = plotter if plotter is not None else RoutePlotter(clipboard=clipboard, log=log)
        self._sleep = sleep
        self._log = log

    # -- capability interface ---------------------------------------------------------
    def tools(self) -> list[dict]:
        return [{"name": _TOOL_NAME, "description": _DESC,
                 "input_schema": {"type": "object", "properties": dict(_SCHEMA_PROPS),
                                  "required": ["to_system"]}}]

    def help_meta(self) -> HelpMeta:
        return HelpMeta(
            category="neutron routes",
            group="navigation and search",
            one_liner=("I plot a long-range neutron-highway route to a distant system — total "
                       "jumps and the first waypoint — and copy that waypoint to your clipboard "
                       "for the galaxy map."),
            example="plot a neutron route to Colonia",
            slots=(
                Slot(param="to_system",
                     phrasings=("your destination", "where to"),
                     example="plot a neutron route to Sagittarius A*",
                     help_text="The system you want to reach — the route's destination."),
                Slot(param="jump_range",
                     phrasings=("your jump range", "how far you jump"),
                     example="a neutron route to Colonia for a 55 light-year jump range",
                     help_text="Your laden jump range in light-years — it bounds each jump."),
                Slot(param="efficiency",
                     phrasings=("how efficient", "fewer jumps or more direct"),
                     example="a more efficient neutron route to Colonia",
                     help_text="1-100; higher trades longer detours for fewer total jumps."),
            ),
            help_when_active=("Tell me your destination and your laden jump range, and I'll plot a "
                              "long-range neutron route and copy the first waypoint for the galaxy "
                              "map."),
        )

    def run_tool(self, name: str, inp: dict) -> str:
        if name != _TOOL_NAME:
            return f"Unknown tool: {name}"
        try:
            return self._handle(inp)
        except Exception as e:  # noqa: BLE001 — the voice loop must survive any tool error
            self._logline(f"error: {e}")
            return f"Neutron-route error: {e}"

    # -- dialog -----------------------------------------------------------------------
    def _handle(self, inp: dict) -> str:
        to_system = str(inp.get("to_system") or "").strip()
        if not to_system:
            return "Where do you want to go? Tell me the destination system to plot a route to."

        if inp.get("jump_range") in (None, ""):
            return ("What's your laden jump range in light-years? I need it to plot the neutron "
                    "route.")

        from_system = str(inp.get("from_system") or "").strip() or (
            self._current_system() if self._current_system else None)
        if not from_system:
            return (f"I don't know where you are — tell me the system to start from, or the route "
                    f"to {to_system} can't be plotted.")

        efficiency = self._efficiency(inp)
        try:
            params = build_galaxy_request(from_system, to_system,
                                          jump_range=float(inp["jump_range"]), efficiency=efficiency)
            result = submit_and_poll(self._http, ROUTE_URL, params,
                                     user_agent=self._cfg.user_agent, sleep=self._sleep,
                                     subject="the neutron plotter", lookup_name="neutron route")
        except (NavError, TypeError, ValueError) as e:
            self._logline(f"neutron plot failed: {e}")
            return str(e) if isinstance(e, NavError) else (
                f"I couldn't read that jump range — give me a number in light-years.")

        waypoints = parse_galaxy_route(result)
        if not waypoints:
            return (f"Spansh couldn't plot a neutron route from {from_system} to {to_system} — "
                    "double-check the system names, or try a longer jump range.")
        return self._say_and_plot(from_system, to_system, waypoints)

    def _efficiency(self, inp: dict) -> int:
        """The efficiency to use — the tool arg (clamped) or the configured default."""
        raw = inp.get("efficiency")
        if raw in (None, ""):
            return self._cfg.default_efficiency
        try:
            return min(_MAX_EFFICIENCY, max(_MIN_EFFICIENCY, int(raw)))
        except (TypeError, ValueError):
            return self._cfg.default_efficiency

    def _say_and_plot(self, from_system: str, to_system: str, waypoints) -> str:
        # The last waypoint's `jumps` is the cumulative total for the whole route (galaxy plotter).
        total_jumps = max((w.jumps for w in waypoints), default=len(waypoints))
        first = waypoints[0].system
        count = len(waypoints)
        stops = "waypoint" if count == 1 else "waypoints"
        line = (f"Neutron route to {to_system}: {total_jumps} jumps across {count} {stops}. "
                f"First hop is {first}.")
        plot = self._plotter.plot_next([waypoints[0]])
        self._logline(f"neutron route {from_system} -> {to_system}: {total_jumps} jumps, "
                      f"{count} waypoints, first -> {first}")
        return f"{line} {plot}"

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)
