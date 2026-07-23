"""Game-data freshness capability (issue #101) — the honest companion to "I don't have that yet".

COVAS++'s promise is grounded, offline reference data (ship specs, outfitting modules,
engineering recipes) refreshed from community datasets each release — never LLM training-cutoff
lore. This tool answers the natural follow-up: "how current IS your game data?" It reads the
bundled dataset manifest (`covas/nav/data/datasets_manifest.json`, emitted by the regen scripts)
and reports each dataset's source and when it was last generated — so "my data may predate that
hull" is backed by a real date, not a shrug.

Same offline, STATELESS, fail-soft pattern as `ship_spec_capability`: pure read of a bundled
JSON, no network, and any error is spoken rather than raised into the voice loop.
"""
from __future__ import annotations

from collections.abc import Callable

from ..nav.datasets import load_manifest
from .base import HelpMeta

_TOOL_NAME = "game_data_status"

_DESC = (
    "Report how CURRENT the bundled Elite Dangerous reference data is — the ship, outfitting and "
    "engineering datasets COVAS++ answers from. Call this when the Commander asks how fresh, "
    "how up-to-date, or when last updated your game data / ship data is ('how current is your "
    "ship data', 'when was your data last refreshed', 'do you know the newest ships'). It reads "
    "a bundled manifest and returns each dataset's source and generation date. Use the dates to "
    "be honest: if a hull or module is newer than the data, say your data may predate it and "
    "offer to web-search — never invent specs for content the datasets don't cover."
)


class GameDataStatusCapability:
    """Advertises `game_data_status` and reads the dataset manifest. Pure/offline; `manifest` is
    injected (defaults to the bundled reader) so tests pass their own rows, and any error degrades
    to a spoken message — the voice loop never sees an exception."""

    def __init__(self, *, manifest: Callable[[], tuple] = load_manifest,
                 log: Callable[[str], None] | None = None) -> None:
        self._manifest = manifest
        self._log = log

    def tools(self) -> list[dict]:
        return [{
            "name": _TOOL_NAME,
            "description": _DESC,
            "input_schema": {"type": "object", "properties": {}},
        }]

    def help_meta(self) -> HelpMeta:
        return HelpMeta(
            category="game data status",
            group="your ship",
            one_liner=("I tell you how current my bundled game data is — the ship, outfitting "
                       "and engineering datasets — and when each was last refreshed, so you know "
                       "whether I'd have the newest content."),
            example="how up to date is your ship data",
        )

    def run_tool(self, name: str, inp: dict) -> str:
        if name != _TOOL_NAME:
            return f"Unknown tool: {name}"
        try:
            return self._summary()
        except Exception as e:  # noqa: BLE001 — the voice loop must survive any tool error
            if self._log is not None:
                self._log(f"error: {e}")
            return f"Game data status error: {e}"

    def _summary(self) -> str:
        rows = self._manifest()
        if not rows:
            return ("I can't read my data manifest right now, so I can't tell you how current my "
                    "game data is — treat anything about very recent content with caution.")
        parts = []
        for d in rows:
            age = ("age unknown" if d.age_days is None
                   else "today" if d.age_days == 0
                   else f"{d.age_days} day{'s' if d.age_days != 1 else ''} ago")
            parts.append(f"{d.label} ({d.row_count} entries) from {d.source}, generated {age}")
        oldest = max((d.age_days for d in rows if d.age_days is not None), default=None)
        lead = ("My game reference data" if oldest is None
                else f"My newest game data was generated {oldest} days ago at most. It covers")
        return (f"{lead}: " + "; ".join(parts) +
                ". If you ask about content newer than these dates, I may not have it yet — "
                "I'll say so and offer a web search rather than guess.")
