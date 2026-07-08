"""ED-context capability — lets the companion reference real game state (DESIGN §5).

Wraps the shared `EDContext` (populated by the journal + status watchers) and exposes it
to the LLM two ways:

  * `system_context()` — a short natural-language line for the system prompt, so replies
    are grounded in where the Commander actually is ("You're low on fuel two jumps out").
  * read tools `where_am_i` / `ship_status` — cheap, zero-LLM-round-trip answers to
    "where am I / how's my fuel" that Claude can call on demand.

This capability only *reads* context and answers questions. It does NOT initiate speech —
proactive callouts are a later phase (DESIGN §5, §7).
"""
from __future__ import annotations

from ..ed.context import EDContext

ED_CONTEXT_TOOLS = [
    {
        "name": "where_am_i",
        "description": (
            "Return the Commander's current Elite Dangerous location from live game "
            "telemetry: star system, whether docked and at which station, and the "
            "nearest body. Use for 'where am I' / 'what system is this'. This is a free "
            "local read — prefer it over guessing or searching the web."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "ship_status",
        "description": (
            "Return the Commander's current ship state from live game telemetry: ship "
            "type and name, fuel level and percentage, cargo aboard, and flight flags "
            "(docked, landing gear, supercruise, hardpoints, low fuel). Use for 'how's "
            "my fuel' / 'what's my ship doing'. A free local read."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "recent_events",
        "description": (
            "Return recent notable Elite Dangerous events from the Commander's journal "
            "(jumps, docks, missions accepted/completed, deaths, fuel alerts), oldest "
            "first. Use for 'what just happened' / 'check my logs' / 'what have I been "
            "doing'. A free local read."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "count": {
                    "type": "integer",
                    "description": "How many recent events to return (default 8, max 25).",
                },
            },
            "required": [],
        },
    },
]


class EDContextCapability:
    """Serves live ED context to the LLM via system_context() + read tools."""

    def __init__(self, ctx: EDContext) -> None:
        self.ctx = ctx

    def tools(self) -> list[dict]:
        return ED_CONTEXT_TOOLS

    def system_context(self) -> str | None:
        """Short line injected into the (cached) system prompt, or None when nothing is
        known yet so we don't add an empty clause."""
        return self.ctx.summary()

    def run_tool(self, name: str, inp: dict) -> str:
        try:
            if name == "where_am_i":
                return self._where_am_i()
            if name == "ship_status":
                return self._ship_status()
            if name == "recent_events":
                return self._recent_events(inp)
            return f"Unknown tool: {name}"
        except Exception as e:  # noqa: BLE001 — the loop must survive any tool error
            return f"Tool error: {e}"

    # -- read helpers ------------------------------------------------------------------
    def _where_am_i(self) -> str:
        s = self.ctx.snapshot()
        if not s["system"] and not s["station"]:
            return "Location unknown — no game telemetry yet (is Elite Dangerous running?)."
        parts = []
        if s["system"]:
            parts.append(f"System: {s['system']}")
        if s["docked"] and s["station"]:
            parts.append(f"Docked at {s['station']}")
        elif s["body"]:
            parts.append(f"Near {s['body']}")
        else:
            parts.append("In open space")
        return ". ".join(parts) + "."

    def _ship_status(self) -> str:
        s = self.ctx.snapshot()
        parts = []
        if s["ship"]:
            ship = s["ship"]
            if s["ship_name"]:
                ship += f" ({s['ship_name']})"
            parts.append(f"Ship: {ship}")
        if s["fuel_pct"] is not None:
            fuel = f"Fuel: {s['fuel_pct']:.0f}%"
            if s["fuel_main"] is not None and s["fuel_capacity"]:
                fuel += f" ({s['fuel_main']:.1f}/{s['fuel_capacity']:.0f}t)"
            if s["low_fuel"]:
                fuel += " — LOW"
            parts.append(fuel)
        if s["cargo"] is not None:
            parts.append(f"Cargo: {s['cargo']:.0f}t")
        active = [label for flag, label in (
            ("docked", "docked"), ("landing_gear", "landing gear down"),
            ("supercruise", "supercruise"), ("hardpoints", "hardpoints deployed"),
        ) if s[flag]]
        if active:
            parts.append("Status: " + ", ".join(active))
        if not parts:
            return "Ship status unknown — no game telemetry yet (is Elite Dangerous running?)."
        return ". ".join(parts) + "."

    def _recent_events(self, inp: dict) -> str:
        count = max(1, min(25, int(inp.get("count") or 8)))
        summary = self.ctx.recent_summary(count)
        return summary or "No recent events recorded yet (is Elite Dangerous running?)."
