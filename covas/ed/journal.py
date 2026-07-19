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
from . import currencies
from .context import EDContext
from .engineers import parse_engineer_progress
from .loadout import parse_loadout
from .materials import parse_materials
from .modes import MODE_SRV
from .owned_ships import SHIPYARD_EVENTS
from .status import describe_transition
from .stored import parse_stored_ships, parse_stored_modules
from .visit_ledger import ARRIVAL_EVENTS

# SRV hull integrity (0..1) below which a proactive "hull's getting low" callout is worth it
# (#54). Status.json has no SRV hull field, so it comes from the journal HullDamage event while
# driving; srv_hull_transitions() fires only on a downward crossing (see the journal watcher).
SRV_HULL_LOW = 0.30


def srv_hull_transitions(old: float | None, new: float | None) -> list[str]:
    """['SrvHullLow'] when SRV hull just crossed BELOW the alert threshold, else []. Fires only
    on a genuine downward crossing (prior known and at/above threshold), so a steady-low hull
    doesn't re-alert and a fresh SRV (unknown prior) establishes a baseline silently."""
    if (isinstance(new, (int, float)) and new < SRV_HULL_LOW
            and isinstance(old, (int, float)) and old >= SRV_HULL_LOW):
        return ["SrvHullLow"]
    return []

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
    # LoadGame also carries `Credits` — that balance is folded into the grounded wallet by the
    # registry-driven `currencies.extract_balances` in apply_journal_event (#101), not here, so
    # this stays a pure "current context" patch (ship/fuel).
    patch: dict = {}
    ship = e.get("Ship_Localised") or e.get("Ship")
    if ship:
        patch["ship"] = _title(ship)
    raw_symbol = e.get("Ship")
    if raw_symbol:
        # The RAW internal symbol (not localized/title-cased) — a stable lookup key for
        # `ed.ships.ship_pad_size` (#117), unlike the display name above.
        patch["ship_symbol"] = str(raw_symbol).strip()
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
    raw_symbol = e.get("Ship")
    if raw_symbol:
        # See _load_game — the raw symbol is the ship_pad_size lookup key (#117).
        patch["ship_symbol"] = str(raw_symbol).strip()
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
    # Carry the docked station's type + MarketID so "am I at my own carrier?" is answerable when
    # the Commander logs in already docked (issue #19). Cleared when not docked.
    patch["docked_station_type"] = e.get("StationType") if docked else None
    patch["docked_market_id"] = e.get("MarketID") if docked else None
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
    patch["docked_station_type"] = e.get("StationType") if docked else None
    patch["docked_market_id"] = e.get("MarketID") if docked else None
    patch["body"] = e.get("Body")
    return patch


def _docked(e: dict) -> dict:
    patch: dict = {"docked": True}
    if e.get("StationName"):
        patch["station"] = e["StationName"]
    if e.get("StarSystem"):
        patch["system"] = e["StarSystem"]
    # StationType ("FleetCarrier" for a carrier) + MarketID (== the carrier's CarrierID) let the
    # audio layer tell when you're docked at your OWN carrier (issue #19). Absent -> None.
    patch["docked_station_type"] = e.get("StationType")
    patch["docked_market_id"] = e.get("MarketID")
    return patch


def _undocked(_e: dict) -> dict:
    return {"docked": False, "station": None,
            "docked_station_type": None, "docked_market_id": None}


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


def _launch_srv(_e: dict) -> dict:
    # Deployed from the ship — a fresh SRV at full hull (#54). Actual damage arrives via
    # HullDamage while driving; DockSRV clears it back to None.
    return {"srv_hull": 1.0}


def _dock_srv(_e: dict) -> dict:
    return {"srv_hull": None}


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
    "LaunchSRV": _launch_srv,
    "DockSRV": _dock_srv,
}


# ScanType progression for exobiology sampling (#54): three scans complete one organism.
# "Log" registers the first sample, "Sample" the second, "Analyse" the third (which awards
# the credits). We map ScanType -> samples-logged so "how many more do I need" is answerable.
_SCAN_ORGANIC_SAMPLES = {"Log": 1, "Sample": 2, "Analyse": 3}
_BIO_REQUIRED = 3


def apply_scan_organic(ctx: EDContext, event: dict) -> None:
    """Fold a `ScanOrganic` event into the exobiology-sampling state on `ctx` (#54). Tracks the
    genus and how many of the three samples are logged (derived from ScanType). The completing
    'Analyse' leaves samples == required so the read tool can report 'complete'."""
    if event.get("event") != "ScanOrganic":
        return
    scan_type = str(event.get("ScanType") or "")
    samples = _SCAN_ORGANIC_SAMPLES.get(scan_type)
    if samples is None:
        return
    genus = event.get("Genus_Localised") or event.get("Genus")
    species = event.get("Species_Localised") or event.get("Species")
    ctx.set_bio_scan(genus, samples, required=_BIO_REQUIRED, species=species)


def apply_journal_event(ctx: EDContext, event: dict) -> dict:
    """Fold one parsed journal event into the rolling context. Returns the patch that was
    applied (empty when the event doesn't affect context) — handy for tests."""
    name = event.get("event", "")
    # Material-inventory events (#66) don't touch "current context" _FIELDS, so they run
    # independent of the handler table: the full `Materials` snapshot replaces the inventory;
    # Collected/Discarded nudge counts between snapshots. Single-writer (this thread), so the
    # read-modify-write on a delta is race-free.
    apply_materials_event(ctx, event)
    # Exobiology sampling (#54) is structured state off the flat _FIELDS patch — fold it apart,
    # like materials, so ScanOrganic tracks genus + sample count without a context field.
    apply_scan_organic(ctx, event)
    # Grounded wallet (#101): fold any KNOWN currency balance this event carries into the wallet.
    # Registry-driven, so LoadGame's `Credits` and CarrierStats' `Finance.CarrierBalance` are read
    # without an event-specific handler — and a NEW currency (no registry row) yields nothing here,
    # which is the honest-degradation contract (the model's guardrail handles the rest).
    ctx.update_wallet(**currencies.extract_balances(event))
    handler = _HANDLERS.get(name)
    patch = handler(event) if handler is not None else {}
    # HullDamage carries the hull of whatever the Commander is piloting (ship/fighter/SRV) with
    # no discriminator, so only treat it as SRV hull (#54) when game_mode says we're driving the
    # SRV — otherwise a main-ship hit would masquerade as SRV damage. Merged into the returned
    # patch so the watcher's crossing detection sees the new value.
    if name == "HullDamage" and isinstance(event.get("Health"), (int, float)):
        if ctx.snapshot().get("game_mode") == MODE_SRV:
            patch = {**patch, "srv_hull": float(event["Health"])}
    if patch:
        ctx.update(**patch)
    # A few events carry structured state beyond the flat "current context" patch and have no
    # _HANDLERS entry of their own — fold those in regardless of whether a field-patch was
    # produced (each event is complete -> replace/merge wholesale).
    if name == "Loadout":                                   # per-module engineering (N9)
        loadout = parse_loadout(event)
        ctx.set_loadout(loadout)
        # Owned-ships identity (#134): the active ship is owned — reconcile it in (mark active,
        # fill type/name/ident) without clobbering a manual correction.
        ctx.reconcile_owned_from_loadout(loadout)
        # Per-ship config memory (#135): remember THIS ship's full build (modules + engineering)
        # under its ShipID, so switching ships doesn't lose the prior one's config. Same identity
        # spine as the owned-ships reconcile above; fail-soft no-op when no store is installed.
        ctx.capture_loadout(loadout)
    elif name == "StoredShips":                             # stored-ships inventory (#67)
        stored = parse_stored_ships(event)
        ctx.set_stored_ships(stored)
        # Owned-ships identity (#134): every stored ship is owned — upsert + refresh locations
        # (never removes, so a snapshot predating a manual add can't delete it).
        ctx.reconcile_owned_from_stored(stored)
    elif name == "StoredModules":                           # stored-modules inventory (#67)
        ctx.set_stored_modules(parse_stored_modules(event))
    # EngineerProgress grounds the engineers finder (#65): merge the {name: status} map so
    # "what have I unlocked / what's left" is answered from the Commander's own journal.
    elif name == "EngineerProgress":
        ctx.update_engineer_progress(parse_engineer_progress(event))
    # Hired NPC crew (#125): fold the five crew events into the persisted registry so the Crew
    # editor can offer the Commander's ACTUAL fighter pilots to adopt. NPC crew only — the fold
    # ignores multicrew human events. Runs regardless of a field-patch (no _HANDLERS entry).
    if name in _NPC_CREW_EVENTS:
        ctx.apply_npc_crew_event(event)
    # Owned-ships ownership changes (#134): fold buy/new/sell/swap into the persisted registry so
    # the fleet identity survives restarts. Runs regardless of a field-patch (no _HANDLERS entry).
    if name in _SHIPYARD_EVENTS:
        ctx.apply_shipyard_event(event)
    # Visit ledger (#138): record system/station arrivals so proactive callouts can ground a
    # history remark ("first time here", "10 times today"). Fail-soft no-op when no ledger installed.
    if name in _ARRIVAL_EVENTS:
        ctx.record_arrival(event)
    return patch


# The NPC-crew events harvested into the seen-set registry (issue #125). Kept as a module constant
# so the dispatch above is a cheap membership test, and the parser owns the authoritative list.
_NPC_CREW_EVENTS = frozenset(
    {"CrewHire", "CrewAssign", "NpcCrewPaidWage", "NpcCrewRank", "CrewFire"}
)

# The ownership-change events folded into the owned-ships registry (issue #134). The
# owned_ships module owns the authoritative set; re-exported here for the cheap dispatch test.
_SHIPYARD_EVENTS = SHIPYARD_EVENTS

# The arrival events folded into the visit ledger (issue #138). The visit_ledger module owns the
# authoritative set; re-exported here so the dispatch above is a cheap membership test.
_ARRIVAL_EVENTS = ARRIVAL_EVENTS


# The three material buckets each carry a Category on the incremental events.
_MATERIAL_DELTAS = {"MaterialCollected": 1, "MaterialDiscarded": -1}


def apply_materials_event(ctx: EDContext, event: dict) -> None:
    """Fold a material-inventory event into `ctx`: a full `Materials` snapshot replaces the
    inventory; `MaterialCollected` / `MaterialDiscarded` adjust one count. Anything else is a
    no-op. Kept separate from the context _FIELDS patch — materials aren't 'current context'."""
    name = event.get("event", "")
    if name == "Materials":
        ctx.set_materials(parse_materials(event))
        return
    sign = _MATERIAL_DELTAS.get(name)
    if sign is None:
        return
    symbol = str(event.get("Name") or "").strip().lower()
    count = event.get("Count")
    snap = ctx.materials_snapshot()
    if not symbol or not isinstance(count, (int, float)) or snap is None:
        return  # no baseline inventory yet, or a malformed delta — wait for the next Materials
    ctx.set_materials(snap.with_delta(symbol, sign * int(count)))


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


def _scan_organic_phrase(e: dict) -> str | None:
    """Spoken phrase for a `ScanOrganic` sample (#54), keyed on ScanType so the count is
    self-contained: Log = 1st sample, Sample = 2nd, Analyse = final. None for an unrecognised
    ScanType (so it isn't logged as a bare 'ScanOrganic')."""
    samples = _SCAN_ORGANIC_SAMPLES.get(str(e.get("ScanType") or ""))
    if samples is None:
        return None
    genus = e.get("Genus_Localised") or e.get("Genus") or "an organism"
    if samples >= _BIO_REQUIRED:
        return f"Analysed {genus} — exobiology sample complete"
    remaining = _BIO_REQUIRED - samples
    tail = "one more to analyse" if remaining == 1 else f"{remaining} more to go"
    return f"Sample {samples} of {_BIO_REQUIRED} of {genus} logged — {tail}"


_DESCRIBERS: dict[str, Callable[[dict], str]] = {
    "FSDJump": lambda e: f"Jumped to {e.get('StarSystem', 'a new system')}",
    "CarrierJump": lambda e: f"Carrier-jumped to {e.get('StarSystem', 'a new system')}",
    "Docked": lambda e: f"Docked at {e.get('StationName', 'a station')}",
    "Undocked": lambda e: f"Undocked from {e.get('StationName', 'the station')}",
    "Touchdown": lambda e: "Touched down on a surface",
    "Liftoff": lambda e: "Lifted off",
    "LaunchSRV": lambda e: "Deployed the SRV",
    "DockSRV": lambda e: "Docked the SRV",
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
    Scan/ScanOrganic are special-cased so only meaningful ones surface (a deliberate detailed
    scan, or a recognised exobiology sample — not auto-scan spam)."""
    name = event.get("event", "")
    if name == "Scan" and event.get("ScanType") == "Detailed" and event.get("BodyName"):
        return f"Scanned {event['BodyName']}"
    if name == "ScanOrganic":
        return _scan_organic_phrase(event)
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
        # Previous SRV hull, for downward-crossing detection of the SrvHullLow callout (#54).
        # None until a LaunchSRV / HullDamage sets it, so the first reading is a silent baseline.
        self._last_srv_hull: float | None = None

    def stop(self) -> None:
        self._stop.set()

    # -- file selection ----------------------------------------------------------------
    def _newest(self) -> Path | None:
        # Guard glob AND the mtime read together: a file can vanish between the glob and
        # the stat() (log rollover, cleanup), and an escaping OSError would otherwise
        # propagate up to run() and kill the watcher for the session (#152).
        try:
            files = list(self.dir.glob(_JOURNAL_GLOB))
            if not files:
                return None
            return max(files, key=lambda p: (p.stat().st_mtime, p.name))
        except OSError:
            return None

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
            # Guard EACH line: parse+apply+publish for one event must not take down the
            # tail loop. A single malformed/unexpected event (e.g. a fail-loud ctx.update
            # KeyError) is logged via on_error and skipped so tailing continues (#152).
            # Mirrors StatusWatcher.run()'s per-poll guard.
            try:
                self._apply_line(line)
            except Exception as e:  # noqa: BLE001 — one bad event can't stop monitoring
                if self._on_error is not None:
                    self._on_error(e)

    def _apply_line(self, line: str) -> None:
        """Parse a single journal line and fold+publish it. Raises are the caller's to
        catch (see _drain) so one faulty event is skipped, not fatal."""
        ev = parse_journal_line(line)
        if ev is None:
            return
        patch = apply_journal_event(self.ctx, ev)
        apply_carrier_event(self.ctx, ev)
        self._record(ev)
        self._publish(ev)
        self._srv_hull_alert(ev, patch)

    def _srv_hull_alert(self, event: dict, patch: dict) -> None:
        """Publish a derived `SrvHullLow` ed_event when SRV hull crossed below the threshold on
        this event (#54). Kept in the watcher (not apply_journal_event) because it needs the
        prior value; the raw HullDamage event carries no low-hull signal of its own."""
        if "srv_hull" not in patch:
            return
        new = patch["srv_hull"]
        for name in srv_hull_transitions(self._last_srv_hull, new):
            self.bus.publish({"type": "ed_event", "event": name})
            desc = describe_transition(name)
            if desc:
                self.ctx.record(name, desc, event.get("timestamp"))
        self._last_srv_hull = new

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
