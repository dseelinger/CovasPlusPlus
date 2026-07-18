"""Visit ledger (issue #138) — a persisted per-location arrival log for grounded history remarks.

Nothing in Elite Dangerous tells you "you've docked here ten times today"; the journal only
records each arrival in isolation. So we keep a small git-ignored ledger, keyed by location, that
survives restarts and accumulates arrivals as they happen. Proactive callouts (DESIGN §5) read
its PURE stats to ground a history remark ("first time here", "tenth visit today") — the LLM only
ever *voices* these numbers, it never invents them.

Two location grains, tracked apart so each answers its own question:

  * SYSTEM visits (folded on `FSDJump` / `CarrierJump`) — "first time in this system", "back in
    Sol again".
  * STATION visits (folded on `Docked`) — "you practically live at Farseer Inc".

Everything here is PURE + fail-soft, mirroring `ed/npc_crew.py`:

  * recording is a total fold (raw event in, updated map out, never raises);
  * the on-disk store is atomic temp-then-replace, swallowing I/O errors — a corrupt file
    degrades to an empty ledger rather than crashing the journal thread;
  * the CLOCK is injectable (like `ProactivePolicy`) so tests advance time deterministically,
    and arrival times come from the event's own `timestamp` field (NOT wall-clock) so a fold is
    reproducible.

Bounding (so the file can't grow without limit over a long career):

  * per location we keep at most `max_recent` recent arrival timestamps and drop any older than
    `retention_days` — those feed only the rolling 24h / 7d windows;
  * lifetime `total` and `first_seen` / `last_seen` are retained (a milestone like "50th visit"
    must survive a rolloff);
  * the ledger holds at most `max_locations` locations, evicting the least-recently-visited.
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

# The arrival events we fold. System grain vs station grain — see the module docstring.
_SYSTEM_ARRIVALS = frozenset({"FSDJump", "CarrierJump"})
_STATION_ARRIVALS = frozenset({"Docked"})
ARRIVAL_EVENTS = _SYSTEM_ARRIVALS | _STATION_ARRIVALS

# Defaults for bounding. A month of recency covers the 24h/7d windows with margin; 64 recent
# stamps per location is plenty for those windows; 4000 locations caps the file at a few hundred KB
# even for a Commander who's docked everywhere.
DEFAULT_RETENTION_DAYS = 30
DEFAULT_MAX_RECENT = 64
DEFAULT_MAX_LOCATIONS = 4000

_DAY_S = 86400.0


@dataclass(frozen=True)
class VisitStats:
    """Pure, grounded visit stats for one location, as of a query time `now`.

      * `total`        — lifetime arrivals recorded here (never rolled off; drives milestones).
      * `visits_24h`   — arrivals within the last 24 hours (INCLUDING the one just recorded).
      * `visits_7d`    — arrivals within the last 7 days.
      * `first_visit`  — True when this is the only arrival ever recorded here (`total == 1`).
      * `first_seen`   — epoch seconds of the earliest recorded arrival, or None.
      * `last_seen`    — epoch seconds of the most recent arrival, or None.
    """
    total: int = 0
    visits_24h: int = 0
    visits_7d: int = 0
    first_visit: bool = False
    first_seen: Optional[float] = None
    last_seen: Optional[float] = None


def _norm(text: object) -> str:
    """Lowercase + collapse whitespace for tolerant location keying. Pure, total."""
    return " ".join(str(text or "").strip().lower().split())


def _sys_key(system: object) -> str:
    return f"sys::{_norm(system)}"


def _stn_key(system: object, station: object) -> str:
    return f"stn::{_norm(system)}::{_norm(station)}"


def _parse_ts(ts: object) -> Optional[float]:
    """Best-effort epoch seconds from an ED ISO timestamp ('2026-07-08T12:05:00Z'), or None.
    Kept dependency-free (stdlib `time.strptime`) and fail-soft — a bad stamp just means the
    caller falls back to the injected clock."""
    if not isinstance(ts, str) or "T" not in ts:
        return None
    try:
        return time.mktime(time.strptime(ts.strip().rstrip("Z"), "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, OverflowError):
        return None


class VisitLedger:
    """The persisted arrival log: `{location_key: {system, station, total, first, last, recent[]}}`,
    backed by a JSON file. Single-writer (the journal thread, via the EDContext accessor under its
    lock) so no internal lock — mirrors `NpcCrewRegistry`. Fail-soft throughout."""

    def __init__(
        self,
        entries: Optional[dict] = None,
        path: Optional[Path | str] = None,
        *,
        clock: Callable[[], float] = time.time,
        retention_days: float = DEFAULT_RETENTION_DAYS,
        max_recent: int = DEFAULT_MAX_RECENT,
        max_locations: int = DEFAULT_MAX_LOCATIONS,
    ) -> None:
        self._entries: dict = dict(entries or {})
        self._path: Optional[Path] = Path(path) if path else None
        self._clock = clock
        self._retention_s = max(1.0, float(retention_days) * _DAY_S)
        self._max_recent = max(1, int(max_recent))
        self._max_locations = max(1, int(max_locations))

    # -- persistence (mirrors ed/npc_crew.py) ------------------------------------------
    @classmethod
    def load(cls, path: Optional[Path | str], **kw) -> "VisitLedger":
        """Read the ledger from disk, fail-soft. A missing/corrupt/non-dict file yields an EMPTY
        ledger (never raises) so a bad file can't wedge the journal watcher."""
        p = Path(path) if path else None
        entries: dict = {}
        if p is not None and p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8") or "{}")
            except (OSError, json.JSONDecodeError, ValueError) as e:
                _warn(f"could not read visit ledger {p} ({e}); starting empty")
            else:
                if isinstance(data, dict):
                    entries = {str(k): dict(v) for k, v in data.items()
                               if isinstance(v, dict) and isinstance(v.get("total"), int)}
                else:
                    _warn(f"visit ledger {p} is not a JSON object; starting empty")
        return cls(entries=entries, path=p, **kw)

    def save(self) -> None:
        """Persist the whole ledger atomically (temp-then-replace), fail-soft. No-op with no path."""
        if self._path is None:
            return
        p = self._path
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            body = json.dumps(self._entries, ensure_ascii=False, indent=2)
            tmp = p.with_suffix(p.suffix + ".tmp")
            tmp.write_text(body + "\n", encoding="utf-8")
            tmp.replace(p)  # atomic on the same filesystem
        except OSError as e:
            _warn(f"could not save visit ledger {p} ({e})")

    # -- recording ---------------------------------------------------------------------
    def record_arrival(self, event: dict, *, when: Optional[float] = None) -> bool:
        """Fold one arrival event into the ledger (and persist on change). Records a SYSTEM visit
        for FSDJump/CarrierJump and a STATION visit for Docked. `when` (epoch seconds) defaults to
        the event's own `timestamp`, then the injected clock. Returns True if anything changed.
        PURE-ish + fail-soft: an event we don't recognise, or one missing its location, is a no-op."""
        if not isinstance(event, dict):
            return False
        name = event.get("event")
        if name not in ARRIVAL_EVENTS:
            return False
        if when is None:
            when = _parse_ts(event.get("timestamp"))
        if when is None:
            when = self._clock()
        changed = False
        system = event.get("StarSystem")
        if name in _SYSTEM_ARRIVALS and system:
            changed |= self._bump(_sys_key(system), system, None, float(when))
        if name in _STATION_ARRIVALS:
            station = event.get("StationName")
            # A Docked event may omit StarSystem; fall back is not possible purely, so we key the
            # station under whatever system name it carries (usually present on Docked).
            if station:
                changed |= self._bump(_stn_key(system, station), system, station, float(when))
        if changed:
            self._evict_if_needed()
            self.save()
        return changed

    def _bump(self, key: str, system: object, station: object, when: float) -> bool:
        """Record one arrival at `key` at time `when`. Increments the lifetime total, refreshes
        first/last-seen, appends to the bounded recent-timestamp list (rolling off anything older
        than the retention window), and caps the list length. Always a change -> returns True."""
        rec = self._entries.get(key)
        if rec is None:
            rec = {"system": (str(system) if system else None),
                   "station": (str(station) if station else None),
                   "total": 0, "first": when, "last": when, "recent": []}
            self._entries[key] = rec
        rec["total"] = int(rec.get("total", 0)) + 1
        rec["last"] = when
        if not isinstance(rec.get("first"), (int, float)):
            rec["first"] = when
        recent = [t for t in rec.get("recent", []) if isinstance(t, (int, float))]
        recent.append(when)
        cutoff = when - self._retention_s
        recent = [t for t in recent if t >= cutoff][-self._max_recent:]
        rec["recent"] = recent
        return True

    def _evict_if_needed(self) -> None:
        """Cap the number of tracked locations, evicting the least-recently-visited (smallest
        `last`). Keeps the file bounded on a long career. Fail-soft."""
        over = len(self._entries) - self._max_locations
        if over <= 0:
            return
        victims = sorted(self._entries.items(),
                         key=lambda kv: float(kv[1].get("last", 0.0)))[:over]
        for k, _ in victims:
            self._entries.pop(k, None)

    # -- pure stats --------------------------------------------------------------------
    def stats_for_station(self, system: object, station: object,
                          *, now: Optional[float] = None) -> VisitStats:
        """Visit stats for a station (system+station), as of `now` (default: injected clock)."""
        return self._stats(_stn_key(system, station), now)

    def stats_for_system(self, system: object, *, now: Optional[float] = None) -> VisitStats:
        """Visit stats for a system, as of `now` (default: injected clock)."""
        return self._stats(_sys_key(system), now)

    def _stats(self, key: str, now: Optional[float]) -> VisitStats:
        rec = self._entries.get(key)
        if not rec:
            return VisitStats()
        if now is None:
            now = self._clock()
        total = int(rec.get("total", 0))
        recent = [t for t in rec.get("recent", []) if isinstance(t, (int, float))]
        v24 = sum(1 for t in recent if t >= now - _DAY_S)
        v7 = sum(1 for t in recent if t >= now - 7 * _DAY_S)
        first = rec.get("first") if isinstance(rec.get("first"), (int, float)) else None
        last = rec.get("last") if isinstance(rec.get("last"), (int, float)) else None
        return VisitStats(total=total, visits_24h=v24, visits_7d=v7,
                          first_visit=(total == 1), first_seen=first, last_seen=last)

    def entries(self) -> dict:
        """A deep-ish copy of the raw store (safe to snapshot)."""
        return {k: dict(v) for k, v in self._entries.items()}


def _warn(msg: str) -> None:
    """Fail-soft diagnostic to stderr (matches ed/npc_crew.py) — never an exception upward."""
    print(f"!! [visit_ledger] {msg}", file=sys.stderr, flush=True)
