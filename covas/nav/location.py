"""Current-system resolution for the find-closest search.

The station lookup needs a reference system. When ED monitoring is on, that's just the live
`EDContext.system`. This module is the FALLBACK for when it isn't (monitoring off, or the
context hasn't seen a location event yet): read the newest journal file and take the most
recent system from a `FSDJump` / `CarrierJump` / `Location` / `Docked` event. Pure file
read, no watcher/threads — a one-shot best-effort lookup.
"""
from __future__ import annotations

from pathlib import Path

from ..ed.journal import _JOURNAL_GLOB, parse_journal_line

# Journal events that carry the Commander's current StarSystem, newest wins.
_LOCATION_EVENTS = {"FSDJump", "CarrierJump", "Location", "Docked", "SupercruiseExit",
                    "SupercruiseEntry"}


def current_system_from_journal(journal_dir: str | Path) -> str | None:
    """The Commander's current system from the newest journal, or None if it can't be found
    (no journals, unreadable, no location event yet). Best-effort and fail-soft."""
    try:
        d = Path(journal_dir)
        files = list(d.glob(_JOURNAL_GLOB))
    except OSError:
        return None
    if not files:
        return None
    newest = max(files, key=lambda p: (p.stat().st_mtime, p.name))
    try:
        lines = newest.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    # Scan newest-first so the first StarSystem we hit is the current one.
    for line in reversed(lines):
        ev = parse_journal_line(line)
        if ev and ev.get("event") in _LOCATION_EVENTS and ev.get("StarSystem"):
            return str(ev["StarSystem"])
    return None
