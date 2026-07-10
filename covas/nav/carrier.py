"""Fleet-carrier + squadron helpers (N3).

`carrier_from_journals` / `squadron_name_from_journals` reconstruct the Commander's PERSONAL
(owned) fleet-carrier state (name, callsign, current system) and squadron name by replaying
recent journals. This is the FALLBACK for the live `EDContext` (which only knows what this
session's watcher has seen) — it recovers state set in an earlier session. Pure file reads,
no threads.

Note: there is deliberately NO remote squadron-carrier lookup here. A squadron carrier's
position is not exposed by any public database (Spansh/Inara/EDSM don't index carriers by
callsign in a way that resolves reliably — verified against the live sites), so that command
just points the Commander at the in-game Carrier Management tab (see location_capability).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..ed.context import EDContext
from ..ed.journal import _JOURNAL_GLOB, apply_carrier_event, parse_journal_line

# How many recent journal files to scan for carrier/squadron state. The relevant events
# (CarrierStats, SquadronStartup) fire at session start and on carrier actions, so a handful
# of recent sessions comfortably covers "where's my carrier" even if you haven't touched it
# this session.
_MAX_FILES = 12


def _recent_journals(journal_dir: str | Path, limit: int = _MAX_FILES) -> list[Path]:
    """The `limit` most recent journal files, oldest-first (so a replay ends on newest state)."""
    try:
        files = list(Path(journal_dir).glob(_JOURNAL_GLOB))
    except OSError:
        return []
    files.sort(key=lambda p: (p.stat().st_mtime, p.name))
    return files[-limit:]


def _iter_events(files: list[Path]):
    """Yield every parsed event across `files`, in file then line order (best-effort)."""
    for f in files:
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            ev = parse_journal_line(line)
            if ev:
                yield ev


@dataclass(frozen=True)
class CarrierInfo:
    """The Commander's fleet carrier as known from the journal."""
    name: str | None
    callsign: str | None
    system: str | None
    pending_system: str | None

    def known(self) -> bool:
        """Whether we know anything at all about a carrier (else the Commander likely has none)."""
        return bool(self.name or self.callsign or self.system)


def carrier_from_journals(journal_dir: str | Path) -> CarrierInfo | None:
    """Reconstruct fleet-carrier state by replaying recent journals into a throwaway context
    (reusing the same handlers the live watcher uses, so there's one source of truth). Returns
    None when no carrier events are found — the Commander probably doesn't own one."""
    ctx = EDContext()
    for ev in _iter_events(_recent_journals(journal_dir)):
        apply_carrier_event(ctx, ev)
    snap = ctx.carrier_snapshot()
    info = CarrierInfo(snap["carrier_name"], snap["carrier_callsign"],
                       snap["carrier_system"], snap["carrier_pending_system"])
    return info if info.known() else None


def squadron_name_from_journals(journal_dir: str | Path) -> str | None:
    """The Commander's squadron name from the most recent `SquadronStartup`, or None."""
    name: str | None = None
    for ev in _iter_events(_recent_journals(journal_dir)):
        if ev.get("event") == "SquadronStartup" and ev.get("SquadronName"):
            name = ev["SquadronName"]        # keep scanning; the newest wins
    return name
