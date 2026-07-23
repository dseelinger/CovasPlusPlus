"""Status watcher — decodes Status.json into semantic transitions (DESIGN §5).

Status.json is a single object ED rewrites frequently: a `Flags` bitfield (docked, gear,
hardpoints, low fuel, supercruise…), plus fuel and cargo. `StatusWatcher` polls it, diffs
the bitfield, and publishes only the *transitions* that matter (`Docked`, `Undocked`,
`LandingGearDeployed`, `LowFuel`…) rather than the raw snapshot on every rewrite. It also
folds fuel/cargo/flag state into the shared `EDContext`. The decode + diff logic is pure
and unit-tested; the thread is a thin polling shell.
"""
from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path

from ..events import EventBus
from .context import EDContext
from .modes import MODE_FIGHTER, MODE_MAINSHIP, MODE_ON_FOOT, MODE_SRV

STATUS_FILE = "Status.json"

# ED's Status.json Flags bitfield, per Frontier's Status File spec. Bit positions are
# ABSOLUTE and must match the spec exactly — an off-by-one silently mislabels flags. (It
# did: this table once omitted bit 14 "Srv Turret retracted", shifting bits 15-31 down one,
# so the FSD-cooldown bit that sets on every supercruise exit read as LowFuel and fired a
# bogus "fuel below 25%" callout. test_ed_status pins the key bits to guard against this.)
FLAGS: dict[str, int] = {
    "Docked": 1 << 0,
    "Landed": 1 << 1,               # on a planet surface
    "LandingGearDown": 1 << 2,
    "ShieldsUp": 1 << 3,
    "Supercruise": 1 << 4,
    "FlightAssistOff": 1 << 5,
    "HardpointsDeployed": 1 << 6,
    "InWing": 1 << 7,
    "LightsOn": 1 << 8,
    "CargoScoopDeployed": 1 << 9,
    "SilentRunning": 1 << 10,
    "ScoopingFuel": 1 << 11,
    "SrvHandbrake": 1 << 12,
    "SrvTurret": 1 << 13,           # SRV using turret view
    "SrvTurretRetracted": 1 << 14,  # SRV turret retracted (close to ship)
    "SrvDriveAssist": 1 << 15,
    "FsdMassLocked": 1 << 16,
    "FsdCharging": 1 << 17,
    "FsdCooldown": 1 << 18,         # set on dropping out of supercruise — NOT low fuel
    "LowFuel": 1 << 19,             # < 25%
    "Overheating": 1 << 20,         # > 100%
    "HasLatLong": 1 << 21,
    "IsInDanger": 1 << 22,
    "BeingInterdicted": 1 << 23,
    "InMainShip": 1 << 24,
    "InFighter": 1 << 25,
    "InSRV": 1 << 26,
    "HudAnalysisMode": 1 << 27,
    "NightVision": 1 << 28,
    "AltitudeFromAverageRadius": 1 << 29,
    "FsdJump": 1 << 30,
    "SrvHighBeam": 1 << 31,
}

# ED's Status.json *second* Flags field (Odyssey only; absent in Horizons). We use just the
# OnFoot bit: it's how on-foot is detected positively, since the main Flags InMainShip/
# InFighter/InSRV bits are ALL clear on foot (as they also are on the menu/loading screens).
# Kept as a table for the same off-by-one safety as FLAGS.
FLAGS2: dict[str, int] = {
    "OnFoot": 1 << 0,
}


def game_mode_from_flags(flags: int | None, flags2: int | None) -> str | None:
    """Derive the Commander's current mode from the two Status.json bitfields (#29).

    Ship modes come from `Flags` (InMainShip/InFighter/InSRV, bits 24-26); on-foot is a
    *separate* field, `Flags2` bit 0 (Odyssey), so it's detected positively rather than
    inferred from "no ship bits" — which is also true on the menu/loading screens. Returns
    None (unknown) when neither field pins a mode, so mode-gating can fall back safely."""
    if isinstance(flags, int):
        if flags & FLAGS["InMainShip"]:
            return MODE_MAINSHIP
        if flags & FLAGS["InFighter"]:
            return MODE_FIGHTER
        if flags & FLAGS["InSRV"]:
            return MODE_SRV
    if isinstance(flags2, int) and (flags2 & FLAGS2["OnFoot"]):
        return MODE_ON_FOOT
    return None

# Flags whose flip is worth announcing, mapped to (name-when-set, name-when-cleared).
# Everything else is decoded into context but not published as its own event.
TRANSITIONS: dict[str, tuple[str, str]] = {
    "Docked": ("Docked", "Undocked"),
    "Landed": ("Landed", "LiftedOff"),
    "LandingGearDown": ("LandingGearDeployed", "LandingGearRetracted"),
    "Supercruise": ("SupercruiseEntered", "SupercruiseExited"),
    "HardpointsDeployed": ("HardpointsDeployed", "HardpointsRetracted"),
    "ScoopingFuel": ("FuelScoopStarted", "FuelScoopStopped"),
    "LowFuel": ("LowFuel", "FuelRestored"),
    "Overheating": ("Overheating", "HeatNormal"),
    "BeingInterdicted": ("Interdicted", "InterdictionEnded"),
    "IsInDanger": ("EnteredDanger", "LeftDanger"),
}


# On-foot / SRV low-vital thresholds (#54). These aren't ED flags — Status.json reports the
# raw fractions (Oxygen/Health, 0..1) and the journal reports SRV hull — so we derive the
# "getting low" alert ourselves by watching for a downward crossing (see low_vital_transitions
# / the journal's srv_hull_transitions). Tuned to give real warning, not nag.
OXYGEN_LOW = 0.25          # on-foot suit oxygen fraction
HEALTH_LOW = 0.25          # on-foot Commander health fraction

# Transitions worth adding to the recent-events feed. Kept to alerts the *journal* doesn't
# cleanly event (fuel/heat are status-flag derived); dock/gear/hardpoint transitions are
# omitted here because the journal already narrates docks and gear/hardpoints are noisy. The
# on-foot/SRV entries (#54) also give event_phrase() a clean spoken form for those callouts.
_LOGGED_TRANSITIONS: dict[str, str] = {
    "LowFuel": "Fuel dropped below 25%",
    "Overheating": "Ship overheating",
    "OxygenLow": "Oxygen running low",
    "HealthLow": "Health critical",
    "SrvHullLow": "SRV hull getting low",
}


def low_vital_transitions(old: dict, new: dict) -> list[str]:
    """Event names for on-foot vitals that just crossed BELOW their alert threshold, given the
    previous and current {oxygen, health} readings. Fires only on a genuine downward crossing —
    a prior reading at/above the threshold to one below it — so a steady-low value doesn't
    re-alert every poll, and an unknown prior (first read after embarking) establishes a
    baseline silently (mirrors flag_transitions' first-read rule)."""
    out: list[str] = []
    for key, event, thresh in (("oxygen", "OxygenLow", OXYGEN_LOW),
                               ("health", "HealthLow", HEALTH_LOW)):
        o, n = old.get(key), new.get(key)
        if isinstance(n, (int, float)) and n < thresh and isinstance(o, (int, float)) and o >= thresh:
            out.append(event)
    return out


def describe_transition(name: str) -> str | None:
    """Short spoken phrase for a status transition worth logging, or None."""
    return _LOGGED_TRANSITIONS.get(name)


def status_path(journal_dir: str | Path) -> Path:
    """Status.json sits in the journal directory alongside the Journal.*.log files."""
    return Path(journal_dir) / STATUS_FILE


def decode_flags(flags: int) -> dict[str, bool]:
    """Decode the Flags bitfield into {name: bool} for every bit in FLAGS."""
    return {name: bool(flags & bit) for name, bit in FLAGS.items()}


def flag_transitions(old: int | None, new: int) -> list[str]:
    """Semantic event names for bits that changed between two Flags values. On the first
    read (`old is None`) there's no prior state to diff, so return [] — we establish a
    baseline silently rather than firing a burst of events for the current state."""
    if old is None:
        return []
    out: list[str] = []
    for key, (on_name, off_name) in TRANSITIONS.items():
        bit = FLAGS[key]
        was, now = bool(old & bit), bool(new & bit)
        if now and not was:
            out.append(on_name)
        elif was and not now:
            out.append(off_name)
    return out


# Status.json GuiFocus values (the UI mode the Commander is in). Full set is 0..11; we name
# the scanner modes auto-honk cares about.
GUI_FOCUS_NONE = 0
GUI_FOCUS_FSS = 9    # Full Spectrum System Scanner
GUI_FOCUS_SAA = 10   # Surface Analysis Scanner — the Detailed Surface Scanner probe view


def apply_status(ctx: EDContext, status: dict) -> dict:
    """Fold a Status.json snapshot into the rolling context (flag booleans, fuel, cargo).
    Returns the patch applied — handy for tests. Station/system come from the journal;
    Status.json has no station name, only the docked *bit*."""
    patch: dict = {}
    flags = status.get("Flags")
    if isinstance(flags, int):
        d = decode_flags(flags)
        patch["docked"] = d["Docked"]
        patch["landing_gear"] = d["LandingGearDown"]
        patch["supercruise"] = d["Supercruise"]
        patch["hardpoints"] = d["HardpointsDeployed"]
        patch["in_danger"] = d["IsInDanger"]
        patch["being_interdicted"] = d["BeingInterdicted"]
        patch["overheating"] = d["Overheating"]        # auto-reflex heat-sink trigger (#37)
        patch["low_fuel"] = d["LowFuel"]
        patch["analysis_mode"] = d["HudAnalysisMode"]

    # Game mode (#29): mainship/fighter/srv from Flags, on-foot from Flags2. Mode-gated
    # keybind advertisement reads this so on-foot actions aren't offered while flying (and
    # vice-versa). Set whenever either field is present so it clears to None (unknown) in the
    # menu rather than sticking at a stale mode.
    flags2 = status.get("Flags2")
    if isinstance(flags, int) or isinstance(flags2, int):
        patch["game_mode"] = game_mode_from_flags(
            flags if isinstance(flags, int) else None,
            flags2 if isinstance(flags2, int) else None)

    # Odyssey on-foot vitals (#54): Oxygen/Health/Temperature are present ONLY on foot, Gravity
    # on foot or landed. Fold each when present and CLEAR it to None otherwise, so a value can't
    # linger and mislead once the Commander re-boards a ship. Only do this on a real snapshot
    # (Flags present) so a stray/partial write doesn't wipe good state. (SRV hull is journal-fed.)
    if isinstance(flags, int):
        for src, field in (("Oxygen", "oxygen"), ("Health", "health"),
                           ("Temperature", "temperature"), ("Gravity", "gravity")):
            val = status.get(src)
            patch[field] = float(val) if isinstance(val, (int, float)) else None

    fuel = status.get("Fuel")
    if isinstance(fuel, dict) and isinstance(fuel.get("FuelMain"), (int, float)):
        patch["fuel_main"] = float(fuel["FuelMain"])

    if isinstance(status.get("Cargo"), (int, float)):
        patch["cargo"] = float(status["Cargo"])

    # Currently-selected fire group (0-based). Only present in Status.json when in a ship
    # with hardpoints; auto-honk (N5) reads it to cycle to the Discovery Scanner's group.
    fg = status.get("FireGroup")
    if isinstance(fg, int) and not isinstance(fg, bool):
        patch["fire_group"] = fg

    # UI focus mode (GuiFocus): 0=none, 9=FSS, 10=SAA (the Detailed Surface Scanner probe view).
    # Auto-honk (K2) watches this to catch a honk that actually opened the DSS in the wrong group.
    gf = status.get("GuiFocus")
    if isinstance(gf, int) and not isinstance(gf, bool):
        patch["gui_focus"] = gf

    if patch:
        ctx.update(**patch)
    return patch


class StatusWatcher(threading.Thread):
    """Daemon thread that polls Status.json and publishes flag transitions. Publishes
    events ONLY (plus the context update) — it never blocks or drives the voice loop."""

    def __init__(
        self,
        path: str | Path,
        bus: EventBus,
        ctx: EDContext,
        *,
        poll_interval: float = 1.0,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        super().__init__(name="ed-status", daemon=True)
        self.path = Path(path)
        self.bus = bus
        self.ctx = ctx
        self.poll = poll_interval
        self._on_error = on_error
        self._stop = threading.Event()
        self._last_flags: int | None = None
        self._last_text: str | None = None
        # Previous on-foot vitals, for downward-crossing detection (#54). Seeded empty so the
        # first reading only establishes a baseline (no alert on connect).
        self._last_vitals: dict[str, float | None] = {}

    def stop(self) -> None:
        self._stop.set()

    def _read(self) -> dict | None:
        """Read + parse Status.json, tolerating the frequent case where we catch it
        mid-rewrite (empty or truncated) — return None and retry on the next poll. Skips
        by comparing file *content* (not mtime): the file is tiny, and content comparison
        catches a rewrite even when the filesystem's mtime resolution is too coarse to."""
        try:
            if not self.path.exists():
                return None
            text = self.path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        if text == self._last_text:             # byte-identical to the last read -> skip
            return None
        if not text.strip():
            return None
        try:
            obj = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return None                          # half-written; don't cache, retry later
        if not isinstance(obj, dict):
            return None
        self._last_text = text
        return obj

    def poll_once(self) -> None:
        """One read/decode/publish cycle. Split out so tests can drive it synchronously."""
        status = self._read()
        if status is None:
            return
        apply_status(self.ctx, status)
        flags = status.get("Flags")
        if isinstance(flags, int):
            ts = status.get("timestamp")
            for name in flag_transitions(self._last_flags, flags):
                self.bus.publish({"type": "ed_event", "event": name, "flags": flags})
                desc = describe_transition(name)
                if desc:
                    self.ctx.record(name, desc, ts)
            self._last_flags = flags

            # On-foot vital alerts (#54): oxygen/health have no ED flag, so diff the raw
            # readings for a downward crossing and publish OxygenLow/HealthLow like a
            # transition. Compared against a snapshot (apply_status already folded them),
            # so this reflects the value we actually stored.
            snap = self.ctx.snapshot()
            vitals = {"oxygen": snap["oxygen"], "health": snap["health"]}
            for name in low_vital_transitions(self._last_vitals, vitals):
                self.bus.publish({"type": "ed_event", "event": name, "flags": flags})
                desc = describe_transition(name)
                if desc:
                    self.ctx.record(name, desc, ts)
            self._last_vitals = vitals

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception as e:  # noqa: BLE001 — a watcher must never crash the app
                if self._on_error is not None:
                    self._on_error(e)
            self._stop.wait(self.poll)
