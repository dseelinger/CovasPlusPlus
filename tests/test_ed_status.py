"""Unit tests for Status.json decode + transitions (DESIGN §5, §9).

Pure bitfield logic, context folding, and StatusWatcher's read/publish cycle driven
synchronously against a temp Status.json — all offline (no thread, no ED, no network).
"""
from __future__ import annotations

import json
from pathlib import Path

from covas.ed import EDContext, StatusWatcher
from covas.ed.status import (FLAGS, apply_status, decode_flags, describe_transition,
                            flag_transitions, status_path)
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
