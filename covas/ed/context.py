"""EDContext — the rolling "what's happening right now" snapshot (DESIGN §5).

Both watchers (journal + status) write into one shared EDContext; the ED-context
capability reads it for `system_context()` and its read tools. It is therefore touched
from several threads (two watcher threads write, the voice-loop thread reads), so every
read/write goes through a lock. Updates are keyed and validated so a mis-typed field
fails loudly (DESIGN §2.5) rather than silently creating junk state.
"""
from __future__ import annotations

import threading
from collections import deque

# How many recent notable events to retain by default (a rolling "what just happened"
# feed for 'check my logs'-style questions). Overridable per EDContext.
DEFAULT_RECENT_KEPT = 25

# The fields that make up "current context". Kept deliberately small — the design lists
# system, station, ship, docked?, fuel%, cargo; a few semantic flags (gear/supercruise/
# low-fuel) ride along because the status watcher already decodes them and they make the
# spoken context richer. fuel_pct is DERIVED (see fuel_pct()), not stored.
_FIELDS: tuple[str, ...] = (
    "system",         # current star system name
    "station",        # docked station name (None when not docked)
    "body",           # nearest body / planet name
    "ship",           # ship type, display name (e.g. "Anaconda")
    "ship_name",      # Commander's custom ship name (e.g. "Void Runner")
    "docked",         # on a landing pad
    "landing_gear",   # gear down
    "supercruise",    # in supercruise
    "hardpoints",     # hardpoints deployed
    "in_danger",      # ED's IsInDanger flag (under fire / hostiles near) — combat guard
    "being_interdicted",  # ED's BeingInterdicted flag — combat guard (keybinds §6)
    "low_fuel",       # ED's LowFuel flag (< 25%)
    "fuel_main",      # main tank fuel, tons
    "fuel_capacity",  # main tank capacity, tons (from the journal, not Status.json)
    "cargo",          # cargo aboard, tons
    "fire_group",     # currently-selected fire group index (Status.json, 0-based) — auto-honk (N5)
    "analysis_mode",  # HudAnalysisMode flag — analysis (scanners) vs combat HUD; auto-honk (K2)
    "gui_focus",      # Status.json GuiFocus: 9=FSS, 10=SAA probe view — honk detect-recover (K2)
)

# The Commander's PERSONAL fleet carrier, tracked live from journal carrier events. Kept
# separate from _FIELDS (and out of the "current context" summary) because it's about the
# carrier's whereabouts, not the Commander's — "where's my carrier" is its own question.
_CARRIER_FIELDS: tuple[str, ...] = (
    "carrier_id",         # the OWNED carrier's id (CarrierStats) — the identity we pin to, so
                          # a squadron / other carrier's events (a different id) can't hijack it
    "carrier_name",       # the carrier's given name (CarrierStats)
    "carrier_callsign",   # its callsign, e.g. "K7X-B0X" (CarrierStats)
    "carrier_system",     # its current star system (CarrierLocation, id-matched)
    "carrier_pending_system",  # a scheduled-but-not-yet-made jump destination (CarrierJumpRequest)
)


class EDContext:
    """Thread-safe rolling snapshot of Elite Dangerous game state."""

    def __init__(self, recent_maxlen: int = DEFAULT_RECENT_KEPT) -> None:
        self._lock = threading.Lock()
        # Rolling feed of recent notable events (jumps, docks, missions, fuel alerts),
        # newest last. Bounded so it can't grow without limit. Fed by both watchers.
        self._recent: deque[dict] = deque(maxlen=max(1, int(recent_maxlen)))
        self.system: str | None = None
        self.station: str | None = None
        self.body: str | None = None
        self.ship: str | None = None
        self.ship_name: str | None = None
        self.docked: bool = False
        self.landing_gear: bool = False
        self.supercruise: bool = False
        self.hardpoints: bool = False
        self.in_danger: bool = False
        self.being_interdicted: bool = False
        self.low_fuel: bool = False
        self.fuel_main: float | None = None
        self.fuel_capacity: float | None = None
        self.cargo: float | None = None
        self.fire_group: int | None = None
        self.analysis_mode: bool = False
        self.gui_focus: int | None = None
        # Fleet-carrier state (see _CARRIER_FIELDS).
        self.carrier_id: int | None = None
        self.carrier_name: str | None = None
        self.carrier_callsign: str | None = None
        self.carrier_system: str | None = None
        self.carrier_pending_system: str | None = None
        # The current ship's full loadout (a frozen ed/loadout.LoadoutSnapshot), replaced
        # wholesale on every journal Loadout event. Kept OUT of _FIELDS/summary(): it's big,
        # structured, and read on demand by the LoadoutCapability's tools — never injected
        # into the (cached) system prompt.
        self._loadout = None

    def update(self, **changes) -> None:
        """Atomically set one or more fields. Unknown keys raise (fail loud) so a typo
        in a watcher surfaces in tests instead of silently doing nothing."""
        with self._lock:
            for key, val in changes.items():
                if key not in _FIELDS:
                    raise KeyError(f"EDContext has no field {key!r}")
                setattr(self, key, val)

    def snapshot(self) -> dict:
        """A plain-dict copy of all fields plus the derived fuel_pct, taken under lock."""
        with self._lock:
            snap = {k: getattr(self, k) for k in _FIELDS}
        snap["fuel_pct"] = _fuel_pct(snap["fuel_main"], snap["fuel_capacity"])
        return snap

    # -- fleet carrier -----------------------------------------------------------------
    def update_carrier(self, **changes) -> None:
        """Atomically set one or more carrier fields. Unknown keys raise (fail loud), same
        contract as update()."""
        with self._lock:
            for key, val in changes.items():
                if key not in _CARRIER_FIELDS:
                    raise KeyError(f"EDContext has no carrier field {key!r}")
                setattr(self, key, val)

    def carrier_snapshot(self) -> dict:
        """A plain-dict copy of the carrier fields, taken under lock."""
        with self._lock:
            return {k: getattr(self, k) for k in _CARRIER_FIELDS}

    # -- ship loadout (N9) ---------------------------------------------------------------
    def set_loadout(self, snapshot) -> None:
        """Replace the stored ship loadout (each journal Loadout event is a full snapshot).
        `snapshot` is a frozen `ed/loadout.LoadoutSnapshot` (or None to clear)."""
        with self._lock:
            self._loadout = snapshot

    def loadout_snapshot(self):
        """The current `LoadoutSnapshot`, or None when no Loadout event has been seen yet.
        The snapshot is immutable, so handing out the reference is thread-safe."""
        with self._lock:
            return self._loadout

    def fuel_pct(self) -> float | None:
        with self._lock:
            return _fuel_pct(self.fuel_main, self.fuel_capacity)

    # -- recent-events feed ------------------------------------------------------------
    def record(self, event: str, description: str, ts: str | None = None) -> None:
        """Append a notable event to the rolling feed. `description` is the short spoken
        form ('Jumped to Sol'); `ts` is the source ISO timestamp for the 'when'."""
        with self._lock:
            self._recent.append({"event": event, "desc": description, "ts": ts})

    def recent(self, n: int | None = None) -> list[dict]:
        """The last `n` recent events (all of them if n is None), oldest first."""
        with self._lock:
            items = list(self._recent)
        return items[-n:] if n else items

    def recent_summary(self, n: int = 8) -> str | None:
        """One-line 'Recent events: …' for injection / the recent_events tool, or None."""
        items = self.recent(n)
        if not items:
            return None
        parts = []
        for it in items:
            hhmm = _hhmm(it.get("ts"))
            parts.append(f"{it['desc']} ({hhmm})" if hhmm else it["desc"])
        return "Recent events: " + "; ".join(parts) + "."

    def context_block(self, include_log: bool = True) -> str | None:
        """The compact telemetry block prepended to a context-referencing turn (DESIGN
        §5). Goes in the user message, NOT the cached system prompt, so live state never
        busts the prompt cache. Returns None when nothing is known yet."""
        parts: list[str] = []
        summary = self.summary()
        if summary:
            parts.append(summary)
        if include_log:
            recent = self.recent_summary()
            if recent:
                parts.append(recent)
        if not parts:
            return None
        # Parenthesized + labelled so the model treats it as reference, not something to
        # read aloud, and grounds its answer in real state rather than guessing.
        return "(Live game telemetry for reference — " + " ".join(parts) + ")"

    def summary(self) -> str | None:
        """One short natural-language line for the system prompt, or None when nothing is
        known yet. Kept terse — it's spoken/cached, not a report."""
        s = self.snapshot()
        # "Nothing known" = every field still at its default (None / False).
        if not any(s[k] not in (None, False) for k in _FIELDS):
            return None

        parts: list[str] = []
        if s["system"]:
            loc = f"in the {s['system']} system"
            if s["docked"] and s["station"]:
                loc += f", docked at {s['station']}"
            elif s["body"]:
                loc += f", near {s['body']}"
            parts.append(loc)
        elif s["docked"] and s["station"]:
            parts.append(f"docked at {s['station']}")

        if s["ship"]:
            ship = f"flying a {s['ship']}"
            if s["ship_name"]:
                ship += f" named {s['ship_name']}"
            parts.append(ship)

        if s["fuel_pct"] is not None:
            fuel = f"fuel {s['fuel_pct']:.0f}%"
            if s["low_fuel"]:
                fuel += " (low)"
            parts.append(fuel)

        if s["cargo"] is not None:
            parts.append(f"cargo {s['cargo']:.0f}t")

        flags = []
        if s["supercruise"]:
            flags.append("in supercruise")
        if s["landing_gear"]:
            flags.append("landing gear down")
        if s["hardpoints"]:
            flags.append("hardpoints deployed")
        if flags:
            parts.append(", ".join(flags))

        if not parts:
            return None
        return "Elite Dangerous — the Commander is " + "; ".join(parts) + "."


def _fuel_pct(fuel_main: float | None, capacity: float | None) -> float | None:
    """Main-tank fuel as a percentage, or None if either value is unknown/zero."""
    if fuel_main is None or not capacity:
        return None
    return max(0.0, min(100.0, fuel_main / capacity * 100.0))


def _hhmm(ts: str | None) -> str | None:
    """Pull 'HH:MM' out of an ED ISO timestamp ('2026-07-08T12:05:00Z'), best-effort."""
    if not isinstance(ts, str) or "T" not in ts:
        return None
    clock = ts.split("T", 1)[1]
    return clock[:5] if len(clock) >= 5 and clock[2] == ":" else None
