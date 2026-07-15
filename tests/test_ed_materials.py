"""Unit tests for the material-inventory snapshot + journal wiring (#66; offline, DESIGN §9).

Covers parsing a `Materials` event into a flat inventory, the immutable Collected/Discarded
delta, and the journal apply-path that keeps `EDContext` fresh — no journal directory, no network.
"""
from __future__ import annotations

import json
from pathlib import Path

from covas.ed.context import EDContext
from covas.ed.journal import apply_journal_event, apply_materials_event
from covas.ed.materials import MaterialsSnapshot, parse_materials

_FIXTURE = Path(__file__).parent / "fixtures" / "journal_materials.json"


def _event() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


# --- parsing ---------------------------------------------------------------------------------

def test_parse_materials_flattens_all_three_buckets():
    snap = parse_materials(_event())
    assert snap.count("iron") == 300               # Raw
    assert snap.count("chemicalprocessors") == 20  # Manufactured
    assert snap.count("disruptedwakeechoes") == 30  # Encoded
    assert snap.timestamp == "2026-07-15T09:30:00Z"


def test_parse_materials_unknown_is_zero_and_case_insensitive():
    snap = parse_materials(_event())
    assert snap.count("chemicalmanipulators") == 0   # genuinely not held
    assert snap.count("ARSENIC") == 12               # normalised to lower-case


def test_parse_materials_tolerates_missing_buckets_and_bad_rows():
    snap = parse_materials({"event": "Materials", "Raw": [
        {"Name": "iron", "Count": 5}, {"Name": "", "Count": 9}, {"Count": 3}, "junk"]})
    assert snap.count("iron") == 5
    assert dict(snap.counts) == {"iron": 5}


# --- deltas ----------------------------------------------------------------------------------

def test_with_delta_is_immutable_and_clamps_at_zero():
    snap = parse_materials(_event())
    plus = snap.with_delta("arsenic", 3)
    assert plus.count("arsenic") == 15
    assert snap.count("arsenic") == 12               # original untouched
    assert snap.with_delta("cadmium", -99).count("cadmium") == 0   # clamped, not negative


# --- journal wiring --------------------------------------------------------------------------

def test_apply_journal_event_stores_full_materials_snapshot():
    ctx = EDContext()
    apply_journal_event(ctx, _event())
    snap = ctx.materials_snapshot()
    assert isinstance(snap, MaterialsSnapshot)
    assert snap.count("phosphorus") == 40


def test_collected_and_discarded_nudge_the_inventory():
    ctx = EDContext()
    apply_materials_event(ctx, _event())
    apply_materials_event(ctx, {"event": "MaterialCollected", "Category": "Raw",
                                "Name": "arsenic", "Count": 4})
    assert ctx.materials_snapshot().count("arsenic") == 16
    apply_materials_event(ctx, {"event": "MaterialDiscarded", "Category": "Manufactured",
                                "Name": "chemicalprocessors", "Count": 5})
    assert ctx.materials_snapshot().count("chemicalprocessors") == 15


def test_delta_before_any_snapshot_is_ignored():
    ctx = EDContext()
    apply_materials_event(ctx, {"event": "MaterialCollected", "Name": "iron", "Count": 2})
    assert ctx.materials_snapshot() is None   # no baseline yet -> wait for a full Materials event
