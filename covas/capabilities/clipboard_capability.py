"""General "copy that to my clipboard" capability (N11).

One LLM-native tool: the MODEL resolves what "that" refers to from the recent conversation —
a system the companion just named, a station, coordinates — and calls `copy_to_clipboard`
with the exact text; this side just copies and confirms. No parsing or reference-resolution
heuristics here — the conversation history is the state, same as every other LLM-native
capability (§3.5).

Deliberately different from the search capabilities' N3 rule: an EXPLICIT copy request copies
even when the value is the Commander's current system (they asked for it, so honoring it
beats second-guessing). The clipboard callable is injected (`nav/clipboard.py` `copy()` in
the app; a fake in tests) so the default `pytest` run never touches the real clipboard
(DESIGN §9). Fail soft: a clipboard error is spoken, never raised into the loop.
"""
from __future__ import annotations

from typing import Callable

from ..nav import copy as _default_copy
from .base import HelpMeta, Slot

_TOOL_NAME = "copy_to_clipboard"

_DESC = (
    "Copy a SPECIFIC value to the Commander's Windows clipboard. Use whenever the Commander "
    "explicitly asks to copy something — 'copy that to my clipboard', 'copy that system', "
    "'copy the station name', 'copy those coordinates'. YOU resolve what 'that' refers to "
    "from the recent conversation and pass the EXACT text: usually just a name (a star "
    "system, a station, a ship, coordinates), not a whole sentence. This is an EXPLICIT "
    "request, so copy it even when it's the Commander's current system — the search tools' "
    "skip-when-already-there rule does NOT apply here. Pass `label` with a short kind ('the "
    "system', 'the station') when you know it, for a nicer spoken confirmation. Relay the "
    "tool's confirmation of exactly what was copied."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {
            "type": "string",
            "description": "The exact text to place on the clipboard — the specific value "
                           "the Commander referred to (e.g. 'Khun'), not a sentence.",
        },
        "label": {
            "type": "string",
            "description": "Optional short kind of thing being copied, for the spoken "
                           "confirmation: 'the system', 'the station', 'the coordinates'.",
        },
    },
    "required": ["text"],
}


class ClipboardCapability:
    """Advertises `copy_to_clipboard` and routes the text to the injected clipboard."""

    def __init__(self, *, clipboard: Callable[[str], None] = _default_copy,
                 log: Callable[[str], None] | None = None) -> None:
        self._clipboard = clipboard
        self._log = log

    # -- capability interface ---------------------------------------------------------
    def tools(self) -> list[dict]:
        return [{"name": _TOOL_NAME, "description": _DESC, "input_schema": dict(_SCHEMA)}]

    def help_meta(self) -> HelpMeta:
        # Ungrouped on purpose: "clipboard" stands as its own group in "what can you do"
        # (base.py's group_of falls back to the category), since it isn't a search.
        return HelpMeta(
            category="clipboard",
            one_liner=("I copy anything we just talked about — a system, a station, "
                       "coordinates — to your clipboard."),
            example="copy that system to my clipboard",
            slots=(
                Slot(param="text",
                     phrasings=("that system", "that station", "the name", "the coordinates"),
                     example="copy that station name to my clipboard",
                     help_text="Say what to copy — the system, station, or name we just "
                               "discussed — and I'll put it on your clipboard."),
            ),
        )

    def run_tool(self, name: str, inp: dict) -> str:
        if name != _TOOL_NAME:
            return f"Unknown tool: {name}"
        try:
            return self._handle(inp)
        except Exception as e:  # noqa: BLE001 — the voice loop must survive any tool error
            self._logline(f"error: {e}")
            return f"Clipboard error: {e}"

    # -- the copy -----------------------------------------------------------------------
    def _handle(self, inp: dict) -> str:
        text = str(inp.get("text") or "").strip()
        if not text:
            return "What should I copy? Name the system, station, or text and I'll copy it."
        label = str(inp.get("label") or "").strip()
        try:
            self._clipboard(text)
        except Exception as e:  # noqa: BLE001 — the clipboard is a convenience, never fatal
            self._logline(f"copy failed for '{text}': {e}")
            return f"I couldn't reach the clipboard just now — the text was {text}."
        self._logline(f"copied '{text}'" + (f" ({label})" if label else ""))
        return f"Copied {label + ' ' if label else ''}{text} to your clipboard."

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)
