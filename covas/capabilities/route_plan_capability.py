"""Spansh route planners — trade loop, neutron/long-range, and Road to Riches (#41/#42/#43).

Three bespoke planner tools that deliberately stay OUTSIDE the spec-driven search family (issue
#111): they run over the ASYNC route client in `search/routes.py` (submit-and-poll a Spansh job,
then a galaxy-map plot handoff via `RoutePlotter`), NOT the synchronous `build_query`/
`execute_search` slot search the family is built on. They share that client + the plot seam + the
LLM-native "fill the numbers you know or ask" shape, so they're grouped in one module:

  * `plan_trade_route`  (#41) — a profitable multi-hop buy/sell loop from where you're docked,
    with per-hop and whole-loop price-freshness caveats.
  * `plot_neutron_route` (#43) — the neutron-highway long-range plotter (A -> B in far fewer
    jumps); no market data, so no freshness caveat.
  * `plan_riches_route`  (#42) — Road to Riches: nearby systems of high-value UNSCANNED bodies to
    First-Discovery-scan for exploration credits.

All are LLM-native and stateless: the model fills what it knows (or asks), the start defaults to
the current system/station, and the conversation is the memory. Everything I/O-bound is injected
(`http`, `get_current_system`, `get_current_station`, `plotter`, `sleep`) so the default `pytest`
run is offline (DESIGN §9). Fail soft throughout — a bad value or a failed plot is spoken, never
raised.

LIVE-VERIFY: the request/result shapes live in `search/routes.py` (`build_*_request` /
`parse_*_route`) and are confirmed on-hardware per the issues; these capabilities are unaffected by
a field-name correction there.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from ..nav import copy as _default_copy
from ..search import NavError, RequestsHttp
from ..search.routes import (RICHES_ROUTE_URL, ROUTE_URL, TRADE_ROUTE_URL, RoutePlotter,
                             RouteWaypoint, build_galaxy_request, build_riches_request,
                             build_trade_request, hop_age_days, parse_galaxy_route,
                             parse_riches_route, parse_trade_route, stale_age_caveat,
                             submit_and_poll)
from ..search.spansh import Http, _DEFAULT_UA
from ..i18n import fmt_int   # locale-aware number formatting for spoken callouts (#199)
from .base import HelpMeta, Slot


# ==========================================================================================
# Shared planner shell
# ==========================================================================================
class _PlannerBase:
    """The shell the three planners share: the injected seams (`http`, `get_current_system`,
    `plotter`, `clipboard`, `sleep`, `log` — all so the default test run is offline), the
    fail-soft `run_tool` wrapper, and the log helper. A subclass sets `_TOOL_NAME` /
    `_ERROR_LABEL` and brings its `_handle` (plus `tools()` / `help_meta()`)."""
    # Tiering group (issue #84): the token-budget cluster this capability's tools belong
    # to; the level filter (covas/tiering.py) keeps or drops the whole group as a unit.
    TIERING_GROUP = "search"

    _TOOL_NAME: str          # the one tool the subclass advertises
    _ERROR_LABEL: str        # "<label> error: {e}" for the fail-soft guard

    def __init__(self, config, *,
                 http: Http | None = None,
                 get_current_system: Callable[[], str | None] | None = None,
                 plotter: RoutePlotter | None = None,
                 clipboard: Callable[[str], None] = _default_copy,
                 sleep: Callable[[float], None] = time.sleep,
                 log: Callable[[str], None] | None = None) -> None:
        self._cfg = config
        self._http = http if http is not None else RequestsHttp()
        self._current_system = get_current_system
        self._plotter = plotter if plotter is not None else RoutePlotter(clipboard=clipboard, log=log)
        self._sleep = sleep
        self._log = log

    def run_tool(self, name: str, inp: dict) -> str:
        if name != self._TOOL_NAME:
            return f"Unknown tool: {name}"
        try:
            return self._handle(inp)
        except Exception as e:  # noqa: BLE001 — the voice loop must survive any tool error
            self._logline(f"error: {e}")
            return f"{self._ERROR_LABEL} error: {e}"

    def _handle(self, inp: dict) -> str:  # pragma: no cover — subclasses always override
        raise NotImplementedError

    @staticmethod
    def _coerce_numbers(inp: dict, specs) -> tuple[dict | None, str | None]:
        """Coerce numeric slots UP FRONT, with a friendly reprompt on the first unparseable one
        (the shape the neutron planner gets right). `specs` is an iterable of
        `(key, caster, phrase)`; an absent/blank slot is skipped (the caller supplies its
        default). Returns `(values, None)` on success, or `(None, reprompt)` naming the field so
        a mis-heard "twenty-ish" doesn't fall through to the fail-soft guard and speak a raw
        `ValueError`."""
        out: dict = {}
        for key, caster, phrase in specs:
            raw = inp.get(key)
            if raw in (None, ""):
                continue
            try:
                out[key] = caster(raw)
            except (TypeError, ValueError):
                return None, f"I didn't catch {phrase} — give me a number."
        return out, None

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)


# ==========================================================================================
# Trade route — plan_trade_route (#41)
# ==========================================================================================
_TRADE_TOOL = "plan_trade_route"


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


_TRADE_DESC = (
    "Plan a profitable multi-hop TRADE ROUTE (a buy/sell loop) for the Commander using Spansh, "
    "starting from the station they're currently docked at, and hand the first destination to the "
    "galaxy map. Use when they ask to plan trading, find a trade run, or 'where should I trade from "
    "here'.\n"
    "- Fill the numbers you know or can infer, and ASK for any you can't: `capital` (credits to "
    "spend), `max_cargo` (cargo capacity in tons), `jump_range` (laden jump range in ly).\n"
    "- Optional refinements, only when the Commander mentions them: `max_hops` (loop length), "
    "`requires_large_pad` (big ship), `max_arrival_distance` (avoid long supercruise to the station, "
    "ls), `allow_planetary` (include planetary ports), `avoid_loops` (don't revisit a station), "
    "`max_price_age_days` (only trust prices newer than this).\n"
    "- The start defaults to where they're docked. If they aren't docked, pass `from_system` + "
    "`from_station`, or ask where to start.\n"
    "- Relay the WHOLE loop the tool returns (each hop: what to buy where, where to sell, profit per "
    "ton) and the round-trip total; if the tool adds a freshness caveat or flags a leg's price age, "
    "pass it along. Tell them the next stop was copied to their clipboard for the galaxy map."
)

_TRADE_SCHEMA_PROPS = {
    "capital": {"type": "integer", "description": "Credits available to spend on cargo."},
    "max_cargo": {"type": "integer", "description": "Cargo capacity in tons."},
    "jump_range": {"type": "number", "description": "Laden jump range in light-years."},
    "max_hops": {"type": "integer",
                 "description": "Maximum number of hops in the loop. Omit for the default."},
    "requires_large_pad": {"type": "boolean",
                           "description": "True if the ship needs a large landing pad."},
    "max_arrival_distance": {"type": "integer",
                             "description": "Max supercruise distance from the star to the station, "
                                            "in light-seconds. Omit for no cap."},
    "allow_planetary": {"type": "boolean",
                        "description": "True to include planetary ports (surface markets)."},
    "avoid_loops": {"type": "boolean",
                    "description": "True (default) to never revisit the same station in the loop."},
    "max_price_age_days": {"type": "integer",
                           "description": "Only trust market prices newer than this many days. "
                                          "Omit for the configured default."},
    "from_system": {"type": "string",
                    "description": "Start system. Omit to use the current (docked) system."},
    "from_station": {"type": "string",
                     "description": "Start station. Omit to use the current docked station."},
}


class RoutePlanCapability(_PlannerBase):
    """Advertises `plan_trade_route` and runs it over the shared route client + plot seam.

    STANDALONE by design (issue #111): the async submit-and-poll route client + RoutePlotter
    handoff don't fit the synchronous slot-search family."""
    _TOOL_NAME = _TRADE_TOOL
    _ERROR_LABEL = "Trade-route"

    def __init__(self, config: RoutePlanConfig, *,
                 get_current_station: Callable[[], str | None] | None = None,
                 **seams) -> None:
        # The one seam only the trade planner needs (the start defaults to the docked
        # station); everything else is the shared planner shell.
        super().__init__(config, **seams)
        self._current_station = get_current_station

    # -- capability interface ---------------------------------------------------------
    def tools(self) -> list[dict]:
        return [{"name": _TRADE_TOOL, "description": _TRADE_DESC,
                 "input_schema": {"type": "object", "properties": dict(_TRADE_SCHEMA_PROPS),
                                  "required": []}}]

    def help_meta(self) -> HelpMeta:
        return HelpMeta(
            category="trade routes",
            group="navigation and search",
            one_liner=("I plan a profitable multi-hop trade loop from where you're docked — every "
                       "hop's buy, sell, and profit plus the round-trip total, with a heads-up when "
                       "prices are stale — and copy the next stop to your clipboard for the galaxy "
                       "map."),
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

        # Coerce every numeric slot up front so a non-numeric value gets a friendly reprompt,
        # not a raw ValueError from the fail-soft guard (the neutron planner's shape).
        nums, reprompt = self._coerce_numbers(inp, (
            ("capital", int, "your budget in credits"),
            ("max_cargo", int, "your cargo capacity"),
            ("jump_range", float, "your jump range"),
            ("max_hops", int, "the number of hops"),
            ("max_arrival_distance", int, "the distance from the star"),
            ("max_price_age_days", int, "the price age in days"),
        ))
        if reprompt:
            return reprompt

        age_window = nums.get("max_price_age_days") or self._cfg.max_price_age_days
        try:
            params = build_trade_request(
                from_system=system, from_station=station,
                capital=nums["capital"], max_cargo=nums["max_cargo"],
                jump_range=nums["jump_range"],
                max_hops=nums.get("max_hops") or self._cfg.default_max_hops,
                max_arrival_distance=nums.get("max_arrival_distance"),
                requires_large_pad=bool(inp.get("requires_large_pad", False)),
                allow_planetary=bool(inp.get("allow_planetary", False)),
                unique=bool(inp.get("avoid_loops", True)),
                max_price_age_days=age_window)
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
        return self._say_and_plot(hops, station, age_window)

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

    def _say_and_plot(self, hops, start_station: str, age_window: int) -> str:
        """Speak the WHOLE loop — every hop's buy/sell/profit plus the round-trip total — flag any
        leg resting on old prices, then hand the first destination to the galaxy map."""
        n = len(hops)
        round_trip = sum(h.profit_total for h in hops)
        legs = " ".join(self._leg_phrase(i, h, age_window) for i, h in enumerate(hops))
        header = (f"Trade loop from {start_station}: {n} hop{'s' if n != 1 else ''}, "
                  f"round-trip profit about {fmt_int(round_trip)} credits.")
        caveat = stale_age_caveat(hops, max_age_days=age_window)
        # Plot the first destination — that's where the Commander flies to sell the first haul.
        plot = self._plotter.plot_next([RouteWaypoint(hops[0].destination_system)])
        self._logline(f"trade route: {n} hops, round-trip {round_trip:,}cr, "
                      f"first -> {hops[0].destination_system}{' (stale)' if caveat else ''}")
        parts = [header, legs]
        if caveat:
            parts.append(f"({caveat})")
        parts.append(plot)
        return " ".join(parts)

    def _leg_phrase(self, i: int, hop, age_window: int) -> str:
        """One spoken hop, with a per-leg age tag when THAT leg's source price is stale (per-hop
        freshness — the summary caveat only covers a wholesale-old loop)."""
        verb = "Then buy" if i > 0 else "Buy"
        age = hop_age_days(hop)
        tag = f" (price ~{age:.0f} days old)" if age is not None and age > age_window else ""
        return (f"{verb} {hop.commodity} at {hop.source_station} and sell at "
                f"{hop.destination_station} in {hop.destination_system} for about "
                f"{fmt_int(hop.profit_per_unit)} a ton{tag}.")


# ==========================================================================================
# Neutron / long-range route — plot_neutron_route (#43)
# ==========================================================================================
_NEUTRON_TOOL = "plot_neutron_route"

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


_NEUTRON_DESC = (
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

_NEUTRON_SCHEMA_PROPS = {
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


class NeutronPlanCapability(_PlannerBase):
    """Advertises `plot_neutron_route` and runs it over the shared galaxy plotter + plot seam.

    STANDALONE by design (issue #111): the async galaxy plotter (submit-and-poll) + RoutePlotter
    handoff don't fit the synchronous slot-search family."""
    _TOOL_NAME = _NEUTRON_TOOL
    _ERROR_LABEL = "Neutron-route"

    # -- capability interface ---------------------------------------------------------
    def tools(self) -> list[dict]:
        return [{"name": _NEUTRON_TOOL, "description": _NEUTRON_DESC,
                 "input_schema": {"type": "object", "properties": dict(_NEUTRON_SCHEMA_PROPS),
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


# ==========================================================================================
# Road to Riches — plan_riches_route (#42)
# ==========================================================================================
_RICHES_TOOL = "plan_riches_route"


@dataclass(frozen=True)
class RichesPlanConfig:
    """Immutable snapshot of `[riches_plan]`. Off by default; not registered unless enabled."""
    enabled: bool = False
    user_agent: str = _DEFAULT_UA
    default_radius: float = 50.0
    default_max_results: int = 25
    default_min_value: int = 300_000
    use_mapping_value: bool = True

    @classmethod
    def from_cfg(cls, cfg: dict) -> "RichesPlanConfig":
        r = cfg.get("riches_plan", {}) or {}
        d = cls()
        return cls(
            enabled=bool(r.get("enabled", False)),
            user_agent=str(r.get("user_agent", d.user_agent) or d.user_agent),
            default_radius=float(r.get("default_radius", d.default_radius) or d.default_radius),
            default_max_results=int(r.get("default_max_results", d.default_max_results)
                                    or d.default_max_results),
            default_min_value=int(r.get("default_min_value", d.default_min_value)
                                  or d.default_min_value),
            use_mapping_value=bool(r.get("use_mapping_value", d.use_mapping_value)),
        )


_RICHES_DESC = (
    "Plan a ROAD TO RICHES route — a chain of nearby systems with high-value UNSCANNED bodies to "
    "First-Discovery-scan for exploration credits — using Spansh, starting from the Commander's "
    "current system, and hand the first system to the galaxy map. Use when they ask to 'plan a "
    "Road to Riches', find an exploration/credit-farming route, or 'where can I scan for money'.\n"
    "- You MUST have their `jump_range` (laden jump range in ly): fill it if known, otherwise ASK.\n"
    "- The start defaults to the Commander's current system; pass `from_system` only to start "
    "elsewhere.\n"
    "- Optional: `radius` (ly to search around the start), `max_results` (how many systems), "
    "`min_value` (minimum per-body scan value to bother with). Omit for sensible defaults.\n"
    "- Relay the summary: the first system, how many bodies to scan there, and the estimated "
    "values. Tell them the first system was copied to their clipboard for the galaxy map."
)

_RICHES_SCHEMA_PROPS = {
    "jump_range": {"type": "number", "description": "Laden jump range in light-years (required)."},
    "from_system": {"type": "string",
                    "description": "Start system. Omit to use the Commander's current system."},
    "radius": {"type": "number",
               "description": "Search radius in light-years around the start. Omit for the default."},
    "max_results": {"type": "integer",
                    "description": "Maximum number of systems in the route. Omit for the default."},
    "min_value": {"type": "integer",
                  "description": "Minimum per-body scan value (credits) worth including. Omit for "
                                 "the default."},
}


class RichesPlanCapability(_PlannerBase):
    """Advertises `plan_riches_route` and runs it over the shared route client + plot seam.

    STANDALONE by design (issue #111): the async Road-to-Riches route client + RoutePlotter
    handoff don't fit the synchronous slot-search family."""
    _TOOL_NAME = _RICHES_TOOL
    _ERROR_LABEL = "Road-to-Riches"

    # -- capability interface ---------------------------------------------------------
    def tools(self) -> list[dict]:
        return [{"name": _RICHES_TOOL, "description": _RICHES_DESC,
                 "input_schema": {"type": "object", "properties": dict(_RICHES_SCHEMA_PROPS),
                                  "required": ["jump_range"]}}]

    def help_meta(self) -> HelpMeta:
        return HelpMeta(
            category="Road to Riches",
            group="navigation and search",
            one_liner=("I plan a Road to Riches from where you are — nearby systems full of "
                       "high-value bodies to First-Discovery-scan for credits — and copy the first "
                       "system to your clipboard for the galaxy map."),
            example="plan me a Road to Riches route",
            slots=(
                Slot(param="jump_range",
                     phrasings=("your jump range", "how far you jump"),
                     example="a Road to Riches for a 40 light-year jump range",
                     help_text="Your laden jump range in light-years — it bounds each jump."),
                Slot(param="radius",
                     phrasings=("how far to search", "the search radius"),
                     example="a Road to Riches within 30 light-years",
                     help_text="How far around your start to look for scannable systems."),
                Slot(param="min_value",
                     phrasings=("the minimum scan value", "how valuable the bodies must be"),
                     example="a Road to Riches with bodies worth at least a million each",
                     help_text="Skip bodies whose estimated scan value is below this."),
            ),
            help_when_active=("Tell me your jump range and I'll plan a Road to Riches from your "
                              "current system — nearby systems with high-value bodies to scan."),
        )

    # -- dialog -----------------------------------------------------------------------
    def _handle(self, inp: dict) -> str:
        system = str(inp.get("from_system") or "").strip() or (
            self._current_system() if self._current_system else None)
        if not system:
            return ("I need a starting system — tell me where to start, or wait until I can read "
                    "your current system from the game.")

        if inp.get("jump_range") in (None, ""):
            return "To plan a Road to Riches I need your laden jump range. What is it?"

        # Coerce every numeric slot up front so a non-numeric value gets a friendly reprompt,
        # not a raw ValueError from the fail-soft guard (the neutron planner's shape).
        nums, reprompt = self._coerce_numbers(inp, (
            ("jump_range", float, "your jump range"),
            ("radius", float, "the search radius"),
            ("max_results", int, "the number of systems"),
            ("min_value", int, "the minimum scan value"),
        ))
        if reprompt:
            return reprompt

        try:
            params = build_riches_request(
                from_system=system, jump_range=nums["jump_range"],
                radius=nums.get("radius") or self._cfg.default_radius,
                max_results=nums.get("max_results") or self._cfg.default_max_results,
                min_value=nums.get("min_value") or self._cfg.default_min_value,
                use_mapping_value=self._cfg.use_mapping_value)
            result = submit_and_poll(self._http, RICHES_ROUTE_URL, params,
                                     user_agent=self._cfg.user_agent, sleep=self._sleep,
                                     subject="the Road-to-Riches planner",
                                     lookup_name="Road-to-Riches route")
        except NavError as e:
            self._logline(f"riches plan failed: {e}")
            return str(e)

        systems = parse_riches_route(result)
        if not systems:
            return ("Spansh didn't find a Road-to-Riches route from there with those limits — try a "
                    "bigger radius, a longer jump range, or a lower minimum scan value.")
        return self._say_and_plot(systems)

    def _say_and_plot(self, systems) -> str:
        first = systems[0]
        total = sum(s.total_value for s in systems)
        bodies = "body" if first.body_count == 1 else "bodies"
        line = (f"Road to Riches: start at {first.system} — {first.body_count} {bodies} to scan "
                f"worth about {fmt_int(first.total_value)} credits. Across {len(systems)} systems "
                f"that's roughly {fmt_int(total)} credits in first-discovery scans.")
        # Plot the first system — that's where the Commander flies to start scanning.
        plot = self._plotter.plot_next([RouteWaypoint(first.system)])
        self._logline(f"riches route: {len(systems)} systems, first -> {first.system} "
                      f"({first.body_count} bodies)")
        return f"{line} {plot}"
