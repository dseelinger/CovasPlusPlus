"""Journal watcher — tails the newest Elite Dangerous journal and publishes events.

ED writes `Journal.<timestamp>.<part>.log` files: newline-delimited JSON, one event per
line, append-only, a fresh file per session/part. `JournalWatcher` tails the newest one,
handles rollover to a new file, tolerates a half-written final line, parses each line to
a dict, updates the shared `EDContext`, and publishes `{"type":"ed_event", ...}` on the
bus. The pure helpers (`parse_journal_line`, `apply_journal_event`) hold all the logic
and are unit-tested offline; the thread is a thin I/O shell around them.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Callable

from ..events import EventBus
from .context import EDContext

# ED's journals live under the Windows user profile. Resolved at runtime (never a
# hardcoded C:\Users\... path — see the repo guardrails) so it's portable per machine.
_DEFAULT_SUBPATH = ("Saved Games", "Frontier Developments", "Elite Dangerous")
_JOURNAL_GLOB = "Journal.*.log"


def default_journal_dir() -> Path:
    """The standard journal directory: %USERPROFILE%\\Saved Games\\Frontier Developments
    \\Elite Dangerous. Home is resolved live so no username is baked into config."""
    return Path.home().joinpath(*_DEFAULT_SUBPATH)


def resolve_journal_dir(cfg: dict) -> Path:
    """The configured [elite].journal_dir, or the standard default when it's blank."""
    configured = str(cfg.get("elite", {}).get("journal_dir", "") or "").strip()
    return Path(configured) if configured else default_journal_dir()


def parse_journal_line(line: str) -> dict | None:
    """Parse one NDJSON journal line to a dict. Returns None for a blank line or one that
    doesn't parse (e.g. the final line was caught half-written) — the caller retries it on
    the next read once the rest has been flushed to disk."""
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


# --- journal event -> context update --------------------------------------------------
# Each handler takes the raw event dict and returns a {field: value} patch for EDContext.
# Only events that move "current context" are handled; everything else is published to
# the bus but leaves the rolling state untouched.

def _load_game(e: dict) -> dict:
    patch: dict = {}
    ship = e.get("Ship_Localised") or e.get("Ship")
    if ship:
        patch["ship"] = _title(ship)
    if e.get("ShipName"):
        patch["ship_name"] = e["ShipName"]
    if isinstance(e.get("FuelLevel"), (int, float)):
        patch["fuel_main"] = float(e["FuelLevel"])
    if isinstance(e.get("FuelCapacity"), (int, float)):
        patch["fuel_capacity"] = float(e["FuelCapacity"])
    return patch


def _loadout(e: dict) -> dict:
    patch: dict = {}
    ship = e.get("Ship_Localised") or e.get("Ship")
    if ship:
        patch["ship"] = _title(ship)
    if e.get("ShipName"):
        patch["ship_name"] = e["ShipName"]
    cap = e.get("FuelCapacity")
    if isinstance(cap, dict) and isinstance(cap.get("Main"), (int, float)):
        patch["fuel_capacity"] = float(cap["Main"])
    elif isinstance(cap, (int, float)):
        patch["fuel_capacity"] = float(cap)
    return patch


def _location(e: dict) -> dict:
    docked = bool(e.get("Docked"))
    patch: dict = {"docked": docked}
    if e.get("StarSystem"):
        patch["system"] = e["StarSystem"]
    patch["station"] = e.get("StationName") if docked else None
    patch["body"] = e.get("Body")
    return patch


def _fsd_jump(e: dict) -> dict:
    patch: dict = {"docked": False, "station": None, "body": None}
    if e.get("StarSystem"):
        patch["system"] = e["StarSystem"]
    if isinstance(e.get("FuelLevel"), (int, float)):
        patch["fuel_main"] = float(e["FuelLevel"])
    return patch


def _carrier_jump(e: dict) -> dict:
    docked = bool(e.get("Docked"))
    patch: dict = {"docked": docked}
    if e.get("StarSystem"):
        patch["system"] = e["StarSystem"]
    patch["station"] = e.get("StationName") if docked else None
    patch["body"] = e.get("Body")
    return patch


def _docked(e: dict) -> dict:
    patch: dict = {"docked": True}
    if e.get("StationName"):
        patch["station"] = e["StationName"]
    if e.get("StarSystem"):
        patch["system"] = e["StarSystem"]
    return patch


def _undocked(_e: dict) -> dict:
    return {"docked": False, "station": None}


def _supercruise_entry(e: dict) -> dict:
    patch: dict = {"supercruise": True}
    if e.get("StarSystem"):
        patch["system"] = e["StarSystem"]
    return patch


def _supercruise_exit(e: dict) -> dict:
    patch: dict = {"supercruise": False}
    if e.get("StarSystem"):
        patch["system"] = e["StarSystem"]
    if e.get("Body"):
        patch["body"] = e["Body"]
    return patch


def _fuel_scoop(e: dict) -> dict:
    return {"fuel_main": float(e["Total"])} if isinstance(e.get("Total"), (int, float)) else {}


def _cargo(e: dict) -> dict:
    return {"cargo": float(e["Count"])} if isinstance(e.get("Count"), (int, float)) else {}


_HANDLERS: dict[str, Callable[[dict], dict]] = {
    "LoadGame": _load_game,
    "Loadout": _loadout,
    "Location": _location,
    "FSDJump": _fsd_jump,
    "CarrierJump": _carrier_jump,
    "Docked": _docked,
    "Undocked": _undocked,
    "SupercruiseEntry": _supercruise_entry,
    "SupercruiseExit": _supercruise_exit,
    "FuelScoop": _fuel_scoop,
    "Cargo": _cargo,
}


def apply_journal_event(ctx: EDContext, event: dict) -> dict:
    """Fold one parsed journal event into the rolling context. Returns the patch that was
    applied (empty when the event doesn't affect context) — handy for tests."""
    handler = _HANDLERS.get(event.get("event", ""))
    if handler is None:
        return {}
    patch = handler(event)
    if patch:
        ctx.update(**patch)
    return patch


# --- carrier event -> carrier state (N3) ----------------------------------------------
# The Commander's PERSONAL (owned) fleet carrier is tracked separately from "current
# context". CRUCIAL subtlety: a Commander's journal contains carrier events for carriers they
# DON'T own — most importantly `CarrierJump`, which fires whenever the Commander is aboard ANY
# carrier as it jumps (e.g. a squadron carrier). Trusting those would report the wrong
# carrier's location. So we PIN to the owned carrier's identity:
#
#   * `CarrierStats` (emitted only for the carrier you OWN) establishes carrier_id + name +
#     callsign — the identity everything else is matched against.
#   * `CarrierLocation` sets the current system, but ONLY when its CarrierID matches ours.
#   * `CarrierJumpRequest` sets a pending destination, again id-matched.
#   * `CarrierJump` is deliberately IGNORED for carrier tracking — it's a "Commander aboard a
#     carrier jump" event that may be someone else's carrier. (It still updates the
#     Commander's own location via the _FIELDS handler.)

_CARRIER_IDENTITY_EVENTS = ("CarrierStats", "CarrierBuy")
_CARRIER_LOCATED_EVENTS = ("CarrierLocation",)
_CARRIER_PENDING_EVENTS = ("CarrierJumpRequest",)


def _own_carrier_event(ctx: EDContext, event: dict) -> bool:
    """Whether a location/pending carrier event is about the Commander's OWN carrier: we must
    know our carrier_id (from CarrierStats) and the event's CarrierID must match it. Unknown
    id, missing event id, or a mismatch -> not ours, ignore (this is what stops a squadron
    carrier's events from hijacking the tracked location)."""
    own = ctx.carrier_snapshot().get("carrier_id")
    ev_id = event.get("CarrierID")
    return own is not None and ev_id is not None and ev_id == own


def apply_carrier_event(ctx: EDContext, event: dict) -> dict:
    """Fold one parsed journal event into the OWNED carrier's state. Returns the applied patch
    (empty when the event isn't a relevant carrier event, or is a different carrier's)."""
    name = event.get("event", "")

    if name in _CARRIER_IDENTITY_EVENTS:
        patch: dict = {}
        if event.get("CarrierID") is not None:
            patch["carrier_id"] = event["CarrierID"]
        if event.get("Name"):
            patch["carrier_name"] = event["Name"]
        if event.get("Callsign"):
            patch["carrier_callsign"] = event["Callsign"]
        if patch:
            ctx.update_carrier(**patch)
        return patch

    if name in _CARRIER_LOCATED_EVENTS:
        if not event.get("StarSystem") or not _own_carrier_event(ctx, event):
            return {}
        patch = {"carrier_system": event["StarSystem"], "carrier_pending_system": None}
        ctx.update_carrier(**patch)
        return patch

    if name in _CARRIER_PENDING_EVENTS:
        dest = event.get("SystemName") or event.get("StarSystem")
        if not dest or not _own_carrier_event(ctx, event):
            return {}
        patch = {"carrier_pending_system": dest}
        ctx.update_carrier(**patch)
        return patch

    return {}


# --- journal event -> recent-log description ------------------------------------------
# A curated whitelist of narrative events worth surfacing for 'what just happened / check
# my logs'. Deliberately excludes high-frequency spam (Scan auto-pings, FuelScoop ticks,
# Bounty, HullDamage) so the feed stays readable. Each entry -> a short spoken phrase.

def _mission_name(e: dict) -> str:
    return e.get("LocalisedName") or e.get("Name") or "a mission"


_DESCRIBERS: dict[str, Callable[[dict], str]] = {
    "FSDJump": lambda e: f"Jumped to {e.get('StarSystem', 'a new system')}",
    "CarrierJump": lambda e: f"Carrier-jumped to {e.get('StarSystem', 'a new system')}",
    "Docked": lambda e: f"Docked at {e.get('StationName', 'a station')}",
    "Undocked": lambda e: f"Undocked from {e.get('StationName', 'the station')}",
    "Touchdown": lambda e: "Touched down on a surface",
    "Liftoff": lambda e: "Lifted off",
    "MissionAccepted": lambda e: f"Accepted mission: {_mission_name(e)}",
    "MissionCompleted": lambda e: f"Completed mission: {_mission_name(e)}",
    "MissionFailed": lambda e: f"Failed mission: {_mission_name(e)}",
    "MissionAbandoned": lambda e: f"Abandoned mission: {_mission_name(e)}",
    "Died": lambda e: "Died",
    "Resurrect": lambda e: "Respawned",
    "Interdicted": lambda e: f"Interdicted by {e.get('Interdictor') or 'an attacker'}",
    "EscapeInterdiction": lambda e: "Escaped an interdiction",
}


def describe_journal_event(event: dict) -> str | None:
    """Short spoken phrase for a notable journal event, or None if it's not log-worthy.
    Scan is special-cased to only surface a deliberate detailed scan (not auto-scan spam)."""
    name = event.get("event", "")
    if name == "Scan" and event.get("ScanType") == "Detailed" and event.get("BodyName"):
        return f"Scanned {event['BodyName']}"
    describer = _DESCRIBERS.get(name)
    return describer(event) if describer else None


def _title(name: str) -> str:
    """Journals sometimes give an internal ship id ('anaconda') and sometimes a display
    name ('Anaconda'). Title-case the internal form so the summary reads naturally."""
    return name if any(c.isupper() for c in name) else name.replace("_", " ").title()


class JournalWatcher(threading.Thread):
    """Daemon thread that tails the newest journal file. Publishes events ONLY (plus the
    context update) — it never blocks or drives the voice loop."""

    def __init__(
        self,
        journal_dir: str | Path,
        bus: EventBus,
        ctx: EDContext,
        *,
        poll_interval: float = 0.5,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        super().__init__(name="ed-journal", daemon=True)
        self.dir = Path(journal_dir)
        self.bus = bus
        self.ctx = ctx
        self.poll = poll_interval
        self._on_error = on_error
        self._stop = threading.Event()
        self._f = None
        self._path: Path | None = None
        self._buf = ""

    def stop(self) -> None:
        self._stop.set()

    # -- file selection ----------------------------------------------------------------
    def _newest(self) -> Path | None:
        try:
            files = list(self.dir.glob(_JOURNAL_GLOB))
        except OSError:
            return None
        if not files:
            return None
        return max(files, key=lambda p: (p.stat().st_mtime, p.name))

    def _open(self, path: Path, *, prime: bool) -> None:
        """Open `path` for tailing. When `prime` (the file that was already mid-session at
        startup), replay its existing lines into context WITHOUT publishing them — that
        warms 'where am I' immediately without spamming the bus with stale events — then
        tail from the end. On rollover (`prime=False`) the file is fresh, so read it from
        the top and publish every line."""
        self._close()
        self._f = open(path, "r", encoding="utf-8", errors="replace")
        self._path = path
        self._buf = ""
        if prime:
            for line in self._f:
                ev = parse_journal_line(line)
                if ev:
                    apply_journal_event(self.ctx, ev)
                    apply_carrier_event(self.ctx, ev)   # warm carrier state too
                    self._record(ev)   # warm the recent feed too, but don't publish stale

    def _close(self) -> None:
        if self._f is not None:
            try:
                self._f.close()
            except Exception:  # noqa: BLE001
                pass
            self._f = None

    # -- tailing -----------------------------------------------------------------------
    def _drain(self) -> None:
        """Read whatever has been appended and process complete lines. A trailing partial
        line (no newline yet) is buffered and completed on a later drain."""
        if self._f is None:
            return
        chunk = self._f.read()
        if not chunk:
            return
        self._buf += chunk
        *lines, self._buf = self._buf.split("\n")
        for line in lines:
            ev = parse_journal_line(line)
            if ev is None:
                continue
            apply_journal_event(self.ctx, ev)
            apply_carrier_event(self.ctx, ev)
            self._record(ev)
            self._publish(ev)

    def _record(self, event: dict) -> None:
        """Add a notable event to the shared recent-events feed (no-op for spammy ones)."""
        desc = describe_journal_event(event)
        if desc:
            self.ctx.record(event.get("event", ""), desc, event.get("timestamp"))

    def _publish(self, event: dict) -> None:
        # Flat {"type":"ed_event","event":<name>, ...raw fields}. Journal events carry an
        # "event" key (not "type"), so the type stamp can't collide.
        self.bus.publish({**event, "type": "ed_event"})

    def run(self) -> None:
        try:
            # Wait for a journal to exist (ED may not be running yet).
            while not self._stop.is_set():
                newest = self._newest()
                if newest is not None:
                    self._open(newest, prime=True)
                    break
                self._stop.wait(self.poll)

            while not self._stop.is_set():
                self._drain()
                newest = self._newest()
                if newest is not None and newest != self._path:
                    self._drain()               # flush the tail of the old file first
                    self._open(newest, prime=False)
                    continue
                self._stop.wait(self.poll)
        except Exception as e:  # noqa: BLE001 — a watcher must never take down the app
            if self._on_error is not None:
                self._on_error(e)
        finally:
            self._close()
