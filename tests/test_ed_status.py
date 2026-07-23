"""Unit tests for Status.json decode + transitions (DESIGN §5, §9).

Pure bitfield logic, context folding, and StatusWatcher's read/publish cycle driven
synchronously against a temp Status.json — all offline (no thread, no ED, no network).
"""
from __future__ import annotations

import json
from pathlib import Path

from covas.ed import EDContext, StatusWatcher
from covas.ed.modes import MODE_FIGHTER, MODE_MAINSHIP, MODE_ON_FOOT, MODE_SRV
from covas.ed.status import (
    FLAGS,
    FLAGS2,
    HEALTH_LOW,
    OXYGEN_LOW,
    apply_status,
    decode_flags,
    describe_transition,
    flag_transitions,
    game_mode_from_flags,
    low_vital_transitions,
    status_path,
)
from covas.events import EventBus

FIXTURES = Path(__file__).parent / "fixtures" / "ed"


def _flags(*names: str) -> int:
    v = 0
    for n in names:
        v |= FLAGS[n]
    return v


# --- bit positions (regression guard) --------------------------------------

def test_flag_bit_positions_match_ed_spec():
    """Absolute bit positions per Frontier's Status File spec. Pinned because the symbolic
    _flags() helper is self-consistent with whatever the table says, so it can't catch an
    off-by-one. Regression: FsdCooldown (bit 18, set on every supercruise exit) was once
    mislabeled LowFuel, firing a bogus 'fuel below 25%' callout."""
    assert FLAGS["Supercruise"] == 1 << 4
    assert FLAGS["ScoopingFuel"] == 1 << 11
    assert FLAGS["FsdCooldown"] == 1 << 18
    assert FLAGS["LowFuel"] == 1 << 19
    assert FLAGS["Overheating"] == 1 << 20
    assert FLAGS["IsInDanger"] == 1 << 22
    assert FLAGS["BeingInterdicted"] == 1 << 23
    assert FLAGS["SrvHighBeam"] == 1 << 31


def test_supercruise_exit_cooldown_is_not_low_fuel():
    """Dropping out of supercruise clears the Supercruise bit and sets FSD-cooldown; that
    must read as SupercruiseExited, never LowFuel (the false-callout regression)."""
    events = flag_transitions(_flags("Supercruise"), _flags("FsdCooldown"))
    assert "LowFuel" not in events
    assert events == ["SupercruiseExited"]   # FsdCooldown isn't a published transition


# --- decode_flags ----------------------------------------------------------

def test_decode_flags_zero_is_all_false():
    d = decode_flags(0)
    assert set(d) == set(FLAGS)
    assert not any(d.values())


def test_decode_flags_sets_named_bits():
    d = decode_flags(_flags("Docked", "LandingGearDown", "LowFuel"))
    assert d["Docked"] and d["LandingGearDown"] and d["LowFuel"]
    assert not d["Supercruise"] and not d["HardpointsDeployed"]


def test_decode_fixture_flags():
    status = json.loads((FIXTURES / "status_docked.json").read_text(encoding="utf-8"))
    d = decode_flags(status["Flags"])
    assert d["Docked"] and d["LandingGearDown"]
    assert not d["Supercruise"] and not d["LowFuel"] and not d["HardpointsDeployed"]


# --- flag_transitions ------------------------------------------------------

def test_first_read_has_no_transitions():
    # old is None -> establish a baseline silently, no burst of events.
    assert flag_transitions(None, _flags("Docked", "Supercruise")) == []


def test_docked_transition():
    assert flag_transitions(0, _flags("Docked")) == ["Docked"]


def test_undocked_transition():
    assert flag_transitions(_flags("Docked"), 0) == ["Undocked"]


def test_gear_and_supercruise_transitions():
    old = _flags("Docked")
    new = _flags("LandingGearDown", "Supercruise")
    got = set(flag_transitions(old, new))
    assert got == {"Undocked", "LandingGearDeployed", "SupercruiseEntered"}


def test_low_fuel_transition_both_ways():
    assert flag_transitions(0, _flags("LowFuel")) == ["LowFuel"]
    assert flag_transitions(_flags("LowFuel"), 0) == ["FuelRestored"]


def test_no_change_no_transitions():
    same = _flags("Docked", "ShieldsUp")
    assert flag_transitions(same, same) == []


# --- apply_status ----------------------------------------------------------

def test_apply_status_folds_flags_and_fuel_and_cargo():
    ctx = EDContext()
    ctx.update(fuel_capacity=32.0)          # capacity comes from the journal
    status = json.loads((FIXTURES / "status_docked.json").read_text(encoding="utf-8"))
    apply_status(ctx, status)
    s = ctx.snapshot()
    assert s["docked"] is True and s["landing_gear"] is True
    assert s["supercruise"] is False and s["low_fuel"] is False
    assert s["fuel_main"] == 24.0 and s["cargo"] == 12.0
    assert s["fuel_pct"] == 75.0


def test_apply_status_ignores_missing_fields():
    ctx = EDContext()
    assert apply_status(ctx, {"event": "Status"}) == {}   # no Flags/Fuel/Cargo -> no-op


def test_apply_status_folds_fire_group():
    ctx = EDContext()
    patch = apply_status(ctx, {"Flags": 0, "FireGroup": 2})   # auto-honk (N5) reads this
    assert patch["fire_group"] == 2 and ctx.snapshot()["fire_group"] == 2


def test_apply_status_folds_analysis_mode_and_gui_focus():
    # auto-honk (K2) reads HudAnalysisMode (bit 27) + GuiFocus (10 = SAA/DSS probe view).
    ctx = EDContext()
    patch = apply_status(ctx, {"Flags": (1 << 27), "GuiFocus": 10})
    assert patch["analysis_mode"] is True
    assert patch["gui_focus"] == 10
    snap = ctx.snapshot()
    assert snap["analysis_mode"] is True and snap["gui_focus"] == 10


def test_apply_status_gui_focus_absent_leaves_default():
    ctx = EDContext()
    apply_status(ctx, {"Flags": 0})              # combat HUD, no GuiFocus key
    snap = ctx.snapshot()
    assert snap["analysis_mode"] is False and snap["gui_focus"] is None


# --- game mode (#29) -------------------------------------------------------

def test_flags_include_mode_bits():
    """The three ship-mode bits are at their spec positions; on-foot lives in Flags2 bit 0."""
    assert FLAGS["InMainShip"] == 1 << 24
    assert FLAGS["InFighter"] == 1 << 25
    assert FLAGS["InSRV"] == 1 << 26
    assert FLAGS2["OnFoot"] == 1 << 0


def test_game_mode_from_ship_flags():
    assert game_mode_from_flags(_flags("InMainShip"), None) == MODE_MAINSHIP
    assert game_mode_from_flags(_flags("InFighter"), None) == MODE_FIGHTER
    assert game_mode_from_flags(_flags("InSRV"), None) == MODE_SRV


def test_game_mode_on_foot_from_flags2():
    # On foot: no ship bit in Flags, OnFoot set in Flags2.
    assert game_mode_from_flags(0, FLAGS2["OnFoot"]) == MODE_ON_FOOT
    # A ship bit outranks a stray Flags2 (shouldn't co-occur, but ship-first is the safe rule).
    assert game_mode_from_flags(_flags("InMainShip"), FLAGS2["OnFoot"]) == MODE_MAINSHIP


def test_game_mode_unknown_when_nothing_pins_it():
    # Menu / loading: no ship bits and no OnFoot -> unknown (None), and both fields absent too.
    assert game_mode_from_flags(0, 0) is None
    assert game_mode_from_flags(None, None) is None


def test_apply_status_folds_game_mode():
    ctx = EDContext()
    patch = apply_status(ctx, {"Flags": _flags("InMainShip")})
    assert patch["game_mode"] == MODE_MAINSHIP
    assert ctx.snapshot()["game_mode"] == MODE_MAINSHIP
    # On foot via Flags2, ship bits clear.
    patch = apply_status(ctx, {"Flags": 0, "Flags2": FLAGS2["OnFoot"]})
    assert patch["game_mode"] == MODE_ON_FOOT
    assert ctx.snapshot()["game_mode"] == MODE_ON_FOOT


def test_apply_status_clears_game_mode_to_none_in_menu():
    # Flags present but no mode bits (main menu) clears a previously-known mode rather than
    # leaving it stale.
    ctx = EDContext()
    apply_status(ctx, {"Flags": _flags("InSRV")})
    assert ctx.snapshot()["game_mode"] == MODE_SRV
    apply_status(ctx, {"Flags": 0})
    assert ctx.snapshot()["game_mode"] is None


# --- on-foot vitals (#54) --------------------------------------------------

def test_apply_status_folds_on_foot_vitals():
    ctx = EDContext()
    patch = apply_status(ctx, {"Flags": FLAGS["Landed"], "Flags2": FLAGS2["OnFoot"],
                               "Oxygen": 0.85, "Health": 1.0, "Temperature": 293.0,
                               "Gravity": 0.17})
    assert patch["oxygen"] == 0.85 and patch["health"] == 1.0
    assert patch["temperature"] == 293.0 and patch["gravity"] == 0.17
    s = ctx.snapshot()
    assert s["oxygen"] == 0.85 and s["gravity"] == 0.17


def test_on_foot_vitals_clear_when_back_in_ship():
    # Vitals present on foot, then a ship snapshot (no Oxygen/Health keys) clears them to None
    # so a stale reading can't linger once re-boarded.
    ctx = EDContext()
    apply_status(ctx, {"Flags": FLAGS["Landed"], "Flags2": FLAGS2["OnFoot"],
                       "Oxygen": 0.5, "Health": 0.9})
    apply_status(ctx, {"Flags": _flags("InMainShip")})   # back in the ship
    s = ctx.snapshot()
    assert s["oxygen"] is None and s["health"] is None


def test_apply_status_ignores_vitals_without_flags():
    # A partial write with no Flags must NOT wipe good vital state.
    ctx = EDContext()
    ctx.update(oxygen=0.5, health=0.5)
    apply_status(ctx, {"event": "Status"})               # no Flags
    s = ctx.snapshot()
    assert s["oxygen"] == 0.5 and s["health"] == 0.5


def test_low_vital_transitions_downward_crossing_only():
    # Fires only on a real crossing from at/above the threshold to below it.
    assert low_vital_transitions({"oxygen": 0.5}, {"oxygen": 0.2}) == ["OxygenLow"]
    # Already low -> already low = no re-alert.
    assert low_vital_transitions({"oxygen": 0.1}, {"oxygen": 0.05}) == []
    # Unknown prior (just embarked) establishes a baseline silently.
    assert low_vital_transitions({}, {"oxygen": 0.1}) == []
    # Health crosses too; both can fire together.
    got = set(low_vital_transitions({"oxygen": 0.5, "health": 0.5},
                                    {"oxygen": 0.1, "health": 0.1}))
    assert got == {"OxygenLow", "HealthLow"}


def test_low_vital_thresholds_are_sane():
    assert 0.0 < OXYGEN_LOW <= 0.5 and 0.0 < HEALTH_LOW <= 0.5


def test_describe_transition_covers_on_foot_srv_alerts():
    assert describe_transition("OxygenLow") == "Oxygen running low"
    assert describe_transition("HealthLow") == "Health critical"
    assert describe_transition("SrvHullLow") == "SRV hull getting low"


def test_watcher_fires_oxygen_low_callout(tmp_path):
    sp = status_path(tmp_path)
    w, ctx, q = _watcher(tmp_path)
    _write_status(sp, FLAGS["Landed"], Flags2=FLAGS2["OnFoot"], Oxygen=0.8,
                  timestamp="2026-07-08T12:00:00Z")
    w.poll_once()                            # baseline vitals, no alert
    assert _events(q) == []
    _write_status(sp, FLAGS["Landed"], Flags2=FLAGS2["OnFoot"], Oxygen=0.15,
                  timestamp="2026-07-08T12:01:00Z")
    w.poll_once()
    assert {e["event"] for e in _events(q)} == {"OxygenLow"}
    assert [e["desc"] for e in ctx.recent()] == ["Oxygen running low"]


# --- StatusWatcher read/publish cycle (synchronous, offline) ---------------

def _watcher(tmp_path):
    bus = EventBus()
    q = bus.subscribe()
    ctx = EDContext()
    return StatusWatcher(status_path(tmp_path), bus, ctx, poll_interval=0.01), ctx, q


def _events(q) -> list[dict]:
    out = []
    while not q.empty():
        e = q.get_nowait()
        if e.get("type") == "ed_event":
            out.append(e)
    return out


def _write_status(path: Path, flags: int, **extra) -> None:
    path.write_text(json.dumps({"event": "Status", "Flags": flags, **extra}),
                    encoding="utf-8")


def test_watcher_baseline_then_transition(tmp_path):
    sp = status_path(tmp_path)
    w, ctx, q = _watcher(tmp_path)

    _write_status(sp, _flags("Docked"))
    w.poll_once()                            # first read = baseline, no events
    assert _events(q) == []
    assert ctx.snapshot()["docked"] is True

    _write_status(sp, _flags("Supercruise"))
    w.poll_once()
    got = {e["event"] for e in _events(q)}
    assert got == {"Undocked", "SupercruiseEntered"}
    assert ctx.snapshot()["supercruise"] is True


def test_watcher_tolerates_half_written_file(tmp_path):
    sp = status_path(tmp_path)
    w, ctx, q = _watcher(tmp_path)
    sp.write_text('{"event":"Status","Flags":', encoding="utf-8")   # truncated mid-write
    w.poll_once()                            # must not raise; nothing published
    assert _events(q) == []
    # A later complete write is picked up (mtime advances; parse succeeds).
    _write_status(sp, _flags("Docked"))
    w.poll_once()
    assert ctx.snapshot()["docked"] is True


def test_describe_transition_only_logs_alerts():
    assert describe_transition("LowFuel") == "Fuel dropped below 25%"
    assert describe_transition("Overheating") == "Ship overheating"
    # Docks/gear are narrated by the journal / too noisy -> not logged from status.
    assert describe_transition("Docked") is None
    assert describe_transition("LandingGearDeployed") is None


def test_watcher_records_low_fuel_alert_to_feed(tmp_path):
    sp = status_path(tmp_path)
    w, ctx, q = _watcher(tmp_path)
    _write_status(sp, 0, timestamp="2026-07-08T12:09:00Z")
    w.poll_once()                            # baseline
    _write_status(sp, _flags("LowFuel"), timestamp="2026-07-08T12:09:30Z")
    w.poll_once()
    assert {e["event"] for e in _events(q)} == {"LowFuel"}    # drains the queue
    assert [e["desc"] for e in ctx.recent()] == ["Fuel dropped below 25%"]
    # Docked transition fires an event but is NOT added to the feed.
    _write_status(sp, _flags("LowFuel", "Docked"), timestamp="2026-07-08T12:10:00Z")
    w.poll_once()
    assert {e["event"] for e in _events(q)} == {"Docked"}
    assert [e["desc"] for e in ctx.recent()] == ["Fuel dropped below 25%"]


def test_watcher_skips_unchanged_file(tmp_path):
    sp = status_path(tmp_path)
    w, ctx, q = _watcher(tmp_path)
    _write_status(sp, _flags("Docked"))
    w.poll_once()
    # Same file, same mtime -> the second poll short-circuits (no re-read/publish).
    w.poll_once()
    assert _events(q) == []


def test_status_path_is_in_journal_dir():
    assert status_path(r"D:\ed").name == "Status.json"
    assert status_path(r"D:\ed") == Path(r"D:\ed") / "Status.json"
