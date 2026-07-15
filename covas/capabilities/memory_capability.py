"""Memory-capture capability (issue #60) — populate persistent memory without being asked.

Two capture paths, both cheap and self-contained (capabilities over loop edits):

  * `on_event` — the bus hook. When ED monitoring publishes a journal `ed_event`, a curated
    DETERMINISTIC describer (`memory.capture.describe_highlight`) turns the milestone-worthy
    ones (first discoveries, deaths, rank-ups, big payouts, a new ship/carrier) into a durable
    memory. No LLM per event; deduped and capped by `MemoryCapture`.
  * `remember_this` tool — the conversation-fact path. The LLM calls it DURING a turn it's
    already producing when the Commander states a standing preference/instruction (or says
    "remember that…"), so there's NO extra model call. `MemoryCapture.remember` is the sink.

This capability is CAPTURE/STORE ONLY. Recall — injecting relevant memories into a turn and a
"what do you remember about…" tool — is issue #61, which extends this class. Nothing here reads
memory back into a turn.
"""
from __future__ import annotations

from typing import Callable, Optional

from ..memory.capture import MemoryCapture
from .base import HelpMeta

MEMORY_TOOLS = [
    {
        "name": "remember_this",
        "description": (
            "Save a DURABLE fact about the Commander to persistent memory so it survives "
            "across sessions. Call this when the Commander explicitly says 'remember that…' "
            "OR volunteers a standing preference or instruction worth keeping — how they like "
            "to be addressed, their main ship, a recurring routine, a lasting like/dislike. Do "
            "NOT use it for transient chit-chat, one-off task state (use the checklist for "
            "that), or facts about the galaxy rather than the Commander. A free local write — "
            "no cost, no network. Duplicates are ignored automatically."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": (
                        "The fact to remember, as a concise standalone statement in the third "
                        "person, e.g. 'Prefers to be addressed as Commander' or 'Main ship is "
                        "a Krait Mk II'."
                    ),
                },
                "type": {
                    "type": "string",
                    "enum": ["preference", "fact", "note"],
                    "description": "Kind of fact: a preference, a plain fact, or a loose note.",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional keywords for later recall, e.g. ['name'] or ['ship'].",
                },
            },
            "required": ["text"],
        },
    },
]


class MemoryCapability:
    """Capture side of persistent memory: journal-highlight `on_event` + the `remember_this`
    store tool. Holds a `MemoryCapture` (the dedup/cap sink). Store-only — issue #61 adds
    recall by extending this."""

    def __init__(self, capture: MemoryCapture,
                 *, log: Optional[Callable[[str], None]] = None) -> None:
        self.capture = capture
        self._log = log

    # -- capability interface ----------------------------------------------------------
    def tools(self) -> list[dict]:
        return MEMORY_TOOLS

    def run_tool(self, name: str, inp: dict) -> str:
        try:
            if name == "remember_this":
                return self._remember(inp)
            return f"Unknown tool: {name}"
        except Exception as e:  # noqa: BLE001 — the loop must survive any tool error
            return f"Tool error: {e}"

    def help_meta(self) -> HelpMeta:
        return HelpMeta(
            category="memory",
            group="your companion",
            one_liner=("I remember durable facts you tell me — how you like to be addressed, "
                       "your main ship, standing preferences — plus milestones from your "
                       "journal, in a plain file you control."),
            example="remember that I prefer the Krait Mk II",
        )

    # -- journal-highlight capture -----------------------------------------------------
    def on_event(self, event: dict) -> None:
        """Bus hook (dispatched by the app's event pump). Capture a curated journal milestone
        into memory. Must never raise — it runs on the shared pump thread, and a watcher event
        must not take that thread down."""
        try:
            if not isinstance(event, dict) or event.get("type") != "ed_event":
                return
            record = self.capture.capture_journal_event(event)
            if record is not None and self._log is not None:
                self._log(f"remembered milestone: {record.text}")
        except Exception:  # noqa: BLE001 — never crash the event pump on a bad event
            pass

    # -- remember_this tool ------------------------------------------------------------
    def _remember(self, inp: dict) -> str:
        text = str(inp.get("text") or "").strip()
        if not text:
            return "Nothing to remember — no fact was given."
        mtype = str(inp.get("type") or "note") or "note"
        tags = inp.get("tags") or ()
        record = self.capture.remember(text, type=mtype, tags=tags)
        if record is None:
            return f"Already knew that: {text}"
        if self._log is not None:
            self._log(f"remembered: {record.text}")
        return f"Noted — I'll remember that: {record.text}"
