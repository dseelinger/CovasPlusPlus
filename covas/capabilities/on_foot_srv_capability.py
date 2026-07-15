"""On-foot / SRV read tools — awareness in the modes ED context used to be silent in (#54).

Mirrors `EDContextCapability`'s ship read-tools for the Odyssey on-foot and SRV modes: cheap,
zero-LLM-round-trip answers to "how's my oxygen", "SRV status", and "how many bio samples do I
need", served straight from the shared `EDContext` the watchers keep. Read-only — it never
initiates speech (that's the proactive path); it just answers when asked.
"""
from __future__ import annotations

from ..ed.context import EDContext
from .base import HelpMeta

ON_FOOT_SRV_TOOLS = [
    {
        "name": "on_foot_status",
        "description": (
            "Return the Commander's Odyssey ON-FOOT status from live game telemetry: suit "
            "oxygen remaining, health, external temperature, and local gravity. Use for 'how's "
            "my oxygen' / 'am I okay out here' / 'what's my health' while disembarked. A free "
            "local read — only meaningful while on foot."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "srv_status",
        "description": (
            "Return the Commander's SRV (surface vehicle) status from live game telemetry: hull "
            "integrity and cargo aboard. Use for 'SRV status' / 'how's the buggy' / 'what's my "
            "SRV hull'. A free local read — only meaningful while driving the SRV."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "bio_scan_progress",
        "description": (
            "Return exobiology sampling progress from live game telemetry: the organism being "
            "sampled and how many of the three Genetic Sampler samples are logged (so how many "
            "more are needed to analyse it). Use for 'how many samples do I need' / 'bio scan "
            "progress' / 'what am I scanning'. A free local read."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
]


class OnFootSrvCapability:
    """Serves live on-foot / SRV / exobiology context to the LLM via read tools (#54)."""

    def __init__(self, ctx: EDContext) -> None:
        self.ctx = ctx

    def tools(self) -> list[dict]:
        return ON_FOOT_SRV_TOOLS

    def help_meta(self) -> HelpMeta:
        return HelpMeta(
            category="on-foot & SRV",
            group="your ship",
            one_liner=("I answer from live telemetry when you're on foot or in the SRV — oxygen "
                       "and health, SRV hull, and how many bio samples you still need."),
            example="how many samples do I need",
        )

    def run_tool(self, name: str, inp: dict) -> str:
        try:
            if name == "on_foot_status":
                return self._on_foot_status()
            if name == "srv_status":
                return self._srv_status()
            if name == "bio_scan_progress":
                return self._bio_scan_progress()
            return f"Unknown tool: {name}"
        except Exception as e:  # noqa: BLE001 — the loop must survive any tool error
            return f"Tool error: {e}"

    # -- read helpers ------------------------------------------------------------------
    def _on_foot_status(self) -> str:
        s = self.ctx.snapshot()
        parts = []
        if s["oxygen"] is not None:
            oxy = f"Oxygen: {s['oxygen'] * 100:.0f}%"
            if s["oxygen"] < 0.25:
                oxy += " — LOW"
            parts.append(oxy)
        if s["health"] is not None:
            hp = f"Health: {s['health'] * 100:.0f}%"
            if s["health"] < 0.25:
                hp += " — CRITICAL"
            parts.append(hp)
        if s["temperature"] is not None:
            parts.append(f"Temperature: {s['temperature']:.0f}K")
        if s["gravity"] is not None:
            parts.append(f"Gravity: {s['gravity']:.2f}g")
        if not parts:
            return ("On-foot status unknown — no on-foot telemetry yet (are you disembarked "
                    "with Elite Dangerous running?).")
        return ". ".join(parts) + "."

    def _srv_status(self) -> str:
        s = self.ctx.snapshot()
        parts = []
        if s["srv_hull"] is not None:
            hull = f"SRV hull: {s['srv_hull'] * 100:.0f}%"
            if s["srv_hull"] < 0.30:
                hull += " — LOW"
            parts.append(hull)
        if s["cargo"] is not None:
            parts.append(f"Cargo: {s['cargo']:.0f}t")
        if not parts:
            return ("SRV status unknown — no SRV telemetry yet (deploy the SRV with Elite "
                    "Dangerous running).")
        return ". ".join(parts) + "."

    def _bio_scan_progress(self) -> str:
        bio = self.ctx.bio_scan()
        if not bio:
            return ("No exobiology sample in progress — scan an organism with the Genetic "
                    "Sampler to start.")
        genus = bio.get("genus") or "the organism"
        samples, required = int(bio.get("samples", 0)), int(bio.get("required", 3))
        if samples >= required:
            return f"{genus}: all {required} samples logged — analysis complete."
        remaining = required - samples
        unit = "sample" if remaining == 1 else "samples"
        return (f"{genus}: {samples} of {required} samples logged — {remaining} more "
                f"{unit} needed to analyse.")
