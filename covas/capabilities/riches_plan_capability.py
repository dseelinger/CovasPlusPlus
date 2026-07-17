"""Road-to-Riches capability (#42) — the exploration-credit route planner.

Road to Riches is Spansh's route of nearby high-value UNSCANNED bodies to First-Discovery-scan for
exploration credits. This capability adds ONE tool — `plan_riches_route` — as a sibling on the #41
route foundation (`search/routes.py`): from the Commander's current system (or a given start) + their
jump range, it POSTs to `RICHES_ROUTE_URL`, parses the systems/bodies to scan with estimated values,
speaks a summary (first system, how many bodies, estimated total/first value), and hands the first
system to the galaxy map via `RoutePlotter.plot_next` (clipboard until the keybind galaxy-map
automation, #32, lands).

LLM-native and stateless like the search/route capabilities: the model fills what it knows (or asks),
the start defaults to the current system, and the conversation is the memory. Everything I/O-bound is
injected (`http`, `get_current_system`, `plotter`, `sleep`) so the default `pytest` run is offline
(DESIGN §9). Fail soft throughout — a bad value or a failed plot is spoken, never raised.

Beats the competitors: EDCoPilot/COVAS:NEXT read a Road-to-Riches list aloud; COVAS++ CLOSES THE LOOP
by handing the first system straight to the galaxy map (clipboard now, in-game course-set once #32
lands) off one spoken command.

LIVE-VERIFY: the riches request/result shape lives in `search/routes.py` (`build_riches_request` /
`parse_riches_route`) and is confirmed on-hardware per the issue; this capability is unaffected by a
field-name correction there.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from ..nav import copy as _default_copy
from ..search import NavError, RequestsHttp
from ..search.routes import (RICHES_ROUTE_URL, RoutePlotter, RouteWaypoint, build_riches_request,
                             parse_riches_route, submit_and_poll)
from ..search.spansh import Http, _DEFAULT_UA
from .base import HelpMeta, Slot

_TOOL_NAME = "plan_riches_route"


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


_DESC = (
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

_SCHEMA_PROPS = {
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


class RichesPlanCapability:
    """Advertises `plan_riches_route` and runs it over the shared route client + plot seam."""
    # Tiering group (issue #84): the token-budget cluster this capability's tools belong
    # to; the level filter (covas/tiering.py) keeps or drops the whole group as a unit.
    TIERING_GROUP = "search"

    def __init__(
        self,
        config: RichesPlanConfig,
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

    def run_tool(self, name: str, inp: dict) -> str:
        if name != _TOOL_NAME:
            return f"Unknown tool: {name}"
        try:
            return self._handle(inp)
        except Exception as e:  # noqa: BLE001 — the voice loop must survive any tool error
            self._logline(f"error: {e}")
            return f"Road-to-Riches error: {e}"

    # -- dialog -----------------------------------------------------------------------
    def _handle(self, inp: dict) -> str:
        system = str(inp.get("from_system") or "").strip() or (
            self._current_system() if self._current_system else None)
        if not system:
            return ("I need a starting system — tell me where to start, or wait until I can read "
                    "your current system from the game.")

        if inp.get("jump_range") in (None, ""):
            return "To plan a Road to Riches I need your laden jump range. What is it?"

        try:
            params = build_riches_request(
                from_system=system, jump_range=float(inp["jump_range"]),
                radius=float(inp.get("radius") or self._cfg.default_radius),
                max_results=int(inp.get("max_results") or self._cfg.default_max_results),
                min_value=int(inp.get("min_value") or self._cfg.default_min_value),
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
                f"worth about {first.total_value:,} credits. Across {len(systems)} systems that's "
                f"roughly {total:,} credits in first-discovery scans.")
        # Plot the first system — that's where the Commander flies to start scanning.
        plot = self._plotter.plot_next([RouteWaypoint(first.system)])
        self._logline(f"riches route: {len(systems)} systems, first -> {first.system} "
                      f"({first.body_count} bodies)")
        return f"{line} {plot}"

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)
