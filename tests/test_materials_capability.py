"""Unit tests for MaterialsCapability (#132; offline, DESIGN §9).

Drives the three tools — a single material's count, a per-bucket listing, and the cap/near-cap
scan — over the recorded inventory fixture (the same one BlueprintCapability's tests use) plus a
couple of hand-built snapshots for the near-cap boundary. Locks: exact counts, at/near-cap
wording, bucket listings excluding zero-count materials, fuzzy name matching, and fail-soft with
no snapshot yet.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import MappingProxyType

from covas.capabilities.base import CapabilityRegistry, help_meta_problems
from covas.capabilities.materials_capability import MaterialsCapability
from covas.ed.materials import MaterialsSnapshot, parse_materials

_FIXTURE = Path(__file__).parent / "fixtures" / "journal_materials.json"


def _snapshot():
    return parse_materials(json.loads(_FIXTURE.read_text(encoding="utf-8")))


def _cap(snapshot="fixture"):
    snap = _snapshot() if snapshot == "fixture" else snapshot
    return MaterialsCapability(get_materials=lambda: snap)


def _inv(**counts) -> MaterialsSnapshot:
    return MaterialsSnapshot(counts=MappingProxyType(dict(counts)))


# --- how many of X -----------------------------------------------------------------------------

def test_material_count_reports_held_count_grade_and_cap():
    out = _cap().run_tool("material_count", {"material": "arsenic"})
    assert "You have 12 Arsenic" in out
    assert "grade 2" in out and "cap 250" in out


def test_material_count_at_cap_says_capped():
    out = _cap().run_tool("material_count", {"material": "iron"})
    assert "You have 300 Iron" in out
    assert "capped at 300" in out.lower()


def test_material_count_near_cap_says_close_to_cap():
    # nickel is grade 1 (cap 300); 280 is >=90% of cap but not AT it.
    out = _cap(snapshot=_inv(nickel=280)).run_tool("material_count", {"material": "nickel"})
    assert "You have 280 Nickel" in out
    assert "close to the 300 cap" in out.lower()


def test_material_count_zero_held_is_honest_not_invented():
    # chemical manipulators aren't in the fixture at all -> reads as 0, never omitted/guessed.
    out = _cap().run_tool("material_count", {"material": "chemical manipulators"})
    assert "You have 0 Chemical Manipulators" in out


def test_material_count_fuzzy_partial_name_resolves():
    out = _cap().run_tool("material_count", {"material": "wake solutions"})
    assert "Strange Wake Solutions" in out
    assert "You have 10" in out


def test_material_count_unrecognized_name_is_honest():
    out = _cap().run_tool("material_count", {"material": "unobtainium"})
    assert "don't recognize" in out.lower()


def test_material_count_blank_input_asks_which():
    assert "Which material" in _cap().run_tool("material_count", {"material": ""})


def test_material_count_no_snapshot_fails_soft():
    out = _cap(snapshot=None).run_tool("material_count", {"material": "arsenic"})
    assert "haven't read your materials" in out.lower()
    assert "12" not in out                                  # no count invented


# --- list a bucket ------------------------------------------------------------------------------

def test_list_materials_bucket_lists_only_held_materials():
    out = _cap().run_tool("list_materials", {"bucket": "raw"})
    assert "Iron 300/300" in out
    assert "Nickel 250/300" in out
    assert "Arsenic 12/250" in out
    assert "Antimony" not in out                            # not held in the fixture -> not listed


def test_list_materials_near_cap_only_filters_to_iron():
    out = _cap().run_tool("list_materials", {"bucket": "raw", "near_cap_only": True})
    assert "Iron 300/300" in out
    assert "Nickel" not in out                              # 250/300 = 83% -> below the 90% bar


def test_list_materials_bucket_with_nothing_held_is_honest():
    # a snapshot with only raw materials -> manufactured/encoded read as empty, not invented.
    snap = parse_materials({"Raw": [{"Name": "iron", "Count": 5}]})
    out = _cap(snapshot=snap).run_tool("list_materials", {"bucket": "manufactured"})
    assert "not holding any manufactured materials" in out.lower()


def test_list_materials_unknown_bucket_asks():
    assert "Which bucket" in _cap().run_tool("list_materials", {"bucket": ""})
    assert "Which bucket" in _cap().run_tool("list_materials", {"bucket": "gubbins"})


def test_list_materials_no_snapshot_fails_soft():
    out = _cap(snapshot=None).run_tool("list_materials", {"bucket": "raw"})
    assert "haven't read your materials" in out.lower()


# --- what am I capped on -------------------------------------------------------------------------

def test_materials_capped_reports_at_cap_across_buckets():
    out = _cap().run_tool("materials_capped", {})
    assert "Capped:" in out
    assert "Iron (300/300)" in out
    assert "Close to capped" not in out                     # nothing else clears the 90% bar


def test_materials_capped_reports_near_cap_separately():
    snap = _inv(iron=300, nickel=280)                       # at-cap + near-cap together
    out = _cap(snapshot=snap).run_tool("materials_capped", {})
    assert "Capped:" in out and "Iron (300/300)" in out
    assert "Close to capped:" in out and "Nickel (280/300)" in out


def test_materials_capped_bucket_narrows_the_scan():
    out = _cap().run_tool("materials_capped", {"bucket": "manufactured"})
    assert "not capped or close to capped on manufactured" in out.lower()


def test_materials_capped_no_snapshot_fails_soft():
    out = _cap(snapshot=None).run_tool("materials_capped", {})
    assert "haven't read your materials" in out.lower()


# --- fail soft + registry contract -----------------------------------------------------------

def test_unknown_tool_fails_soft():
    assert "Unknown tool" in _cap().run_tool("nope", {})


def test_help_metadata_is_complete_and_registers():
    cap = _cap()
    assert help_meta_problems(cap.help_meta()) == []
    reg = CapabilityRegistry()
    reg.register(cap)
    assert "materials inventory" in reg.categories()
    assert "12" in reg.run_tool("material_count", {"material": "arsenic"})
