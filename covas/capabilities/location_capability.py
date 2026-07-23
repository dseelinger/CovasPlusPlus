"""Location & carrier voice commands (N3).

Three quick, LOCAL commands:

  * `copy_current_system`     — put the Commander's current system on the clipboard.
  * `where_is_fleet_carrier`  — the PERSONAL (owned) carrier's system, tracked live from the
                                journal (pinned to the owned carrier's id, with a journal-scan
                                fallback for state set last session); speak it and copy it
                                (unless you're already there).
  * `where_is_squadron_carrier` — squadron carriers aren't in any queryable public database,
                                so this just points the Commander at the in-game Carrier
                                Management tab (optionally naming their squadron).

All results honour the N3 "already there -> don't copy" rule. All I/O is injected — the
current-system getter, the carrier state getter, the squadron-name getter, and the clipboard
— so the default `pytest` run is offline and free (DESIGN §9). Fail soft: any error is spoken.
"""
from __future__ import annotations

from collections.abc import Callable

from ..nav import CarrierInfo
from .base import HelpMeta

_COPY_TOOL = "copy_current_system"
_FLEET_TOOL = "where_is_fleet_carrier"
_SQUAD_TOOL = "where_is_squadron_carrier"

_COPY_DESC = (
    "Copy the Commander's CURRENT star system name to the clipboard (to paste into the galaxy "
    "map). Use for 'copy my current system' / 'copy where I am'. A free local read — ALWAYS "
    "call it rather than answering from memory."
)
_FLEET_DESC = (
    "Report where the Commander's PERSONAL fleet carrier is — its current star system, read "
    "LIVE from the journal — and copy that system to the clipboard (unless they're already "
    "there). ALWAYS call this for 'where's my fleet carrier' / 'where is my carrier'. Do NOT "
    "answer from the system prompt, personality, or checklist — those may be out of date about "
    "whether the Commander owns a carrier. This tool is the source of truth: if they own one "
    "it returns its location; if it genuinely finds none, it says so."
)
_SQUAD_DESC = (
    "Answer 'where's my squadron carrier'. A squadron carrier's location is NOT available in "
    "any database the companion can query, so this tool explains it can only be found in-game "
    "(on the Carrier Management tab of the Squadron menu). ALWAYS call it for that question "
    "rather than guessing or attempting a lookup; relay its reply."
)

_NO_ARGS = {"type": "object", "properties": {}, "required": []}


class LocationCarrierCapability:
    """Advertises the location/carrier tools and answers them from injected state."""
    # Tiering group (issue #84): the token-budget cluster this capability's tools belong
    # to; the level filter (covas/tiering.py) keeps or drops the whole group as a unit.
    TIERING_GROUP = "location"

    def __init__(
        self,
        *,
        get_current_system: Callable[[], str | None],
        clipboard: Callable[[str], None],
        get_fleet_carrier: Callable[[], CarrierInfo | None],
        get_squadron_name: Callable[[], str | None] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._current_system = get_current_system
        self._clipboard = clipboard
        self._get_fleet = get_fleet_carrier
        self._get_squadron = get_squadron_name
        self._log = log

    # -- capability interface ---------------------------------------------------------
    def tools(self) -> list[dict]:
        return [
            {"name": _COPY_TOOL, "description": _COPY_DESC, "input_schema": dict(_NO_ARGS)},
            {"name": _FLEET_TOOL, "description": _FLEET_DESC, "input_schema": dict(_NO_ARGS)},
            {"name": _SQUAD_TOOL, "description": _SQUAD_DESC, "input_schema": dict(_NO_ARGS)},
        ]

    def help_meta(self) -> HelpMeta:
        return HelpMeta(
            category="carriers",
            group="navigation and search",
            one_liner=("I copy your current system to the clipboard, and tell you where your "
                       "fleet carrier is."),
            example="where's my fleet carrier",
        )

    def run_tool(self, name: str, inp: dict) -> str:
        try:
            if name == _COPY_TOOL:
                return self._copy_current()
            if name == _FLEET_TOOL:
                return self._fleet_carrier()
            if name == _SQUAD_TOOL:
                return self._squadron_carrier()
            return f"Unknown tool: {name}"
        except Exception as e:  # noqa: BLE001 — the voice loop must survive any tool error
            self._logline(f"error: {e}")
            return f"Carrier lookup error: {e}"

    # -- copy current system ----------------------------------------------------------
    def _copy_current(self) -> str:
        system = self._current_system()
        if not system:
            return ("I don't know your current system yet — is Elite Dangerous running with "
                    "monitoring on?")
        copied = self._copy(system)
        self._logline(f"copy current system {system}: {'ok' if copied else 'failed'}")
        if copied:
            return f"You're in {system}. I've copied it to your clipboard."
        return f"You're in {system}. (I couldn't copy it to the clipboard.)"

    # -- fleet carrier ----------------------------------------------------------------
    def _fleet_carrier(self) -> str:
        info = self._get_fleet()
        if info is None or not info.known():
            return ("I haven't seen a fleet carrier in your journals. If you own one, jump it "
                    "or log in near it and I'll start tracking it.")
        label = info.name or "Your fleet carrier"
        callsign = f" ({info.callsign})" if info.callsign else ""
        if not info.system:
            return (f"I know about your carrier{callsign}, but I don't have its current system "
                    "yet — it should update after its next jump.")
        current = self._current_system()
        here = _same_system(info.system, current)
        line = f"{label}{callsign} is in {info.system}."
        if info.pending_system and not _same_system(info.pending_system, info.system):
            line += f" It's scheduled to jump to {info.pending_system}."
        return line + self._deliver(info.system, here)

    # -- squadron carrier -------------------------------------------------------------
    def _squadron_carrier(self) -> str:
        squad = self._get_squadron() if self._get_squadron is not None else None
        lead = (f"I can't track {squad}'s carrier remotely" if squad
                else "I can't track squadron carriers remotely")
        return (f"{lead} — that information isn't in any database I can search. You'll need to "
                "check in-game: open the Squadron menu and look at the Carrier Management tab.")

    # -- shared: copy-or-not + note ---------------------------------------------------
    def _deliver(self, system: str, here: bool) -> str:
        """Copy `system` and return the trailing clipboard sentence — unless the Commander is
        already there (N3: don't copy your own current system)."""
        if here:
            return " That's your current system, so I haven't copied anything."
        copied = self._copy(system)
        return (f" I've copied {system} to your clipboard." if copied
                else f" (Couldn't copy to the clipboard — the system is {system}.)")

    def _copy(self, text: str) -> bool:
        try:
            self._clipboard(text)
            return True
        except Exception as e:  # noqa: BLE001 — clipboard is a convenience, never fatal
            self._logline(f"clipboard copy failed: {e}")
            return False

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)


def _same_system(a: str | None, b: str | None) -> bool:
    """Case-insensitive system-name equality (both present)."""
    return bool(a and b and a.strip().lower() == b.strip().lower())
