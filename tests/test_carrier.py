"""Unit tests for fleet-carrier tracking (N3) — offline, free.

Covers: carrier journal events folding into EDContext, PINNED to the owned carrier's id so
another carrier's events (e.g. a squadron carrier the Commander is aboard) can't hijack the
tracked location; and reconstructing carrier state + squadron name from fixture journals.
No network, no real journals.
"""
from __future__ import annotations

import json

from covas.ed.context import EDContext
from covas.ed.journal import apply_carrier_event
from covas.nav.carrier import (CarrierInfo, carrier_from_journals,
                               squadron_name_from_journals)

_OWN = 3700005632          # the owned carrier's CarrierID
_OTHER = 3999999999        # a different (e.g. squadron) carrier's id


# --- carrier events -> EDContext (id-pinned) -------------------------------

def test_carrier_stats_sets_identity():
    ctx = EDContext()
    apply_carrier_event(ctx, {"event": "CarrierStats", "CarrierID": _OWN,
                              "Name": "Sacred Fire", "Callsign": "BNH-T2F"})
    snap = ctx.carrier_snapshot()
    assert snap["carrier_id"] == _OWN
    assert snap["carrier_name"] == "Sacred Fire"
    assert snap["carrier_callsign"] == "BNH-T2F"


def test_location_updates_system_when_id_matches():
    ctx = EDContext()
    apply_carrier_event(ctx, {"event": "CarrierStats", "CarrierID": _OWN, "Callsign": "BNH-T2F"})
    apply_carrier_event(ctx, {"event": "CarrierLocation", "CarrierID": _OWN,
                              "StarSystem": "Wolf 397"})
    assert ctx.carrier_snapshot()["carrier_system"] == "Wolf 397"


def test_location_for_a_different_carrier_is_ignored():
    # THE squadron-carrier bug: a CarrierLocation for a carrier we don't own must NOT
    # overwrite our tracked system.
    ctx = EDContext()
    apply_carrier_event(ctx, {"event": "CarrierStats", "CarrierID": _OWN, "Callsign": "BNH-T2F"})
    apply_carrier_event(ctx, {"event": "CarrierLocation", "CarrierID": _OWN,
                              "StarSystem": "Wolf 397"})
    # Commander is aboard the squadron carrier (a different id) elsewhere:
    applied = apply_carrier_event(ctx, {"event": "CarrierLocation", "CarrierID": _OTHER,
                                        "StarSystem": "Col 285 Sector RE-Q d5-132"})
    assert applied == {}                                   # ignored
    assert ctx.carrier_snapshot()["carrier_system"] == "Wolf 397"   # unchanged


def test_location_ignored_when_owner_id_unknown():
    # Without CarrierStats we don't know which carrier is ours -> don't guess from a location.
    ctx = EDContext()
    applied = apply_carrier_event(ctx, {"event": "CarrierLocation", "CarrierID": _OWN,
                                        "StarSystem": "Wolf 397"})
    assert applied == {} and ctx.carrier_snapshot()["carrier_system"] is None


def test_carrier_jump_is_ignored_for_tracking():
    # CarrierJump = "Commander aboard a carrier jump" (maybe someone else's) -> never used to
    # set the OWNED carrier's location.
    ctx = EDContext()
    apply_carrier_event(ctx, {"event": "CarrierStats", "CarrierID": _OWN, "Callsign": "BNH-T2F"})
    applied = apply_carrier_event(ctx, {"event": "CarrierJump", "CarrierID": _OTHER,
                                        "StarSystem": "Col 285 Sector RE-Q d5-132"})
    assert applied == {} and ctx.carrier_snapshot()["carrier_system"] is None


def test_jump_request_sets_pending_and_location_clears_it():
    ctx = EDContext()
    apply_carrier_event(ctx, {"event": "CarrierStats", "CarrierID": _OWN, "Callsign": "BNH-T2F"})
    apply_carrier_event(ctx, {"event": "CarrierJumpRequest", "CarrierID": _OWN,
                              "SystemName": "Colonia"})
    assert ctx.carrier_snapshot()["carrier_pending_system"] == "Colonia"
    apply_carrier_event(ctx, {"event": "CarrierLocation", "CarrierID": _OWN,
                              "StarSystem": "Colonia"})
    snap = ctx.carrier_snapshot()
    assert snap["carrier_system"] == "Colonia" and snap["carrier_pending_system"] is None


def test_non_carrier_event_is_ignored():
    ctx = EDContext()
    assert apply_carrier_event(ctx, {"event": "FSDJump", "StarSystem": "Sol"}) == {}
    assert ctx.carrier_snapshot()["carrier_system"] is None


# --- journal-scan fallback -------------------------------------------------

def _write_journal(tmp_path, name, events):
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return p


def test_carrier_from_journals_reconstructs_latest_owned_state(tmp_path):
    # Older session establishes the owned carrier + its system; a squadron carrier's events
    # (different id) must be ignored; newer session relocates the owned carrier.
    _write_journal(tmp_path, "Journal.2026-07-01T100000.01.log", [
        {"event": "CarrierStats", "CarrierID": _OWN, "Name": "Sacred Fire", "Callsign": "BNH-T2F"},
        {"event": "CarrierLocation", "CarrierID": _OWN, "StarSystem": "Sol"},
        {"event": "CarrierJump", "CarrierID": _OTHER, "StarSystem": "Col 285 Sector RE-Q d5-132"},
        {"event": "CarrierLocation", "CarrierID": _OTHER, "StarSystem": "Col 285 Sector RE-Q d5-132"},
    ])
    _write_journal(tmp_path, "Journal.2026-07-05T100000.01.log", [
        {"event": "CarrierStats", "CarrierID": _OWN, "Name": "Sacred Fire", "Callsign": "BNH-T2F"},
        {"event": "CarrierLocation", "CarrierID": _OWN, "StarSystem": "Wolf 397"},
    ])
    info = carrier_from_journals(tmp_path)
    assert info == CarrierInfo(name="Sacred Fire", callsign="BNH-T2F",
                               system="Wolf 397", pending_system=None)


def test_carrier_from_journals_none_when_no_carrier(tmp_path):
    _write_journal(tmp_path, "Journal.2026-07-01T100000.01.log", [
        {"event": "FSDJump", "StarSystem": "Sol"},
    ])
    assert carrier_from_journals(tmp_path) is None


def test_squadron_name_from_journals(tmp_path):
    _write_journal(tmp_path, "Journal.2026-07-01T100000.01.log", [
        {"event": "SquadronStartup", "SquadronName": "The Dark Wheel", "CurrentRank": 3},
    ])
    assert squadron_name_from_journals(tmp_path) == "The Dark Wheel"


def test_squadron_name_none_when_absent(tmp_path):
    _write_journal(tmp_path, "Journal.2026-07-01T100000.01.log", [
        {"event": "FSDJump", "StarSystem": "Sol"},
    ])
    assert squadron_name_from_journals(tmp_path) is None
