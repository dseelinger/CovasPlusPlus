"""Unit tests for the BlueprintCapability (#66; offline, DESIGN §9).

Drives the two tools through a stubbed materials getter over the recorded inventory fixture —
no journal directory, no network. Locks the spoken shapes: the grade recipe, the computed
shortfall with per-material sourcing, the "have everything" and "no inventory yet" paths, the
module-only disambiguation, the validated unknown-blueprint fallback, and the registry contract.
"""
from __future__ import annotations

import json
from pathlib import Path

from covas.capabilities.base import CapabilityRegistry, help_meta_problems
from covas.capabilities.blueprint_capability import BlueprintCapability
from covas.ed.materials import parse_materials

_FIXTURE = Path(__file__).parent / "fixtures" / "journal_materials.json"


def _snapshot():
    return parse_materials(json.loads(_FIXTURE.read_text(encoding="utf-8")))


def _cap(snapshot="fixture"):
    snap = _snapshot() if snapshot == "fixture" else snapshot
    return BlueprintCapability(get_materials=lambda: snap)


# --- the headline: grade recipe + computed shortfall + sourcing ------------------------------

def test_grade5_fsd_reports_recipe_and_what_is_missing():
    out = _cap().run_tool("blueprint_materials", {"blueprint": "increased range", "grade": 5})
    assert "grade 5 Increased Range on the Frame Shift Drive" in out
    assert "1x Arsenic" in out and "1x Chemical Manipulators" in out
    # inventory has arsenic but none of the other two -> shortfall names those, not arsenic
    assert "SHORT on 2" in out
    assert "Chemical Manipulators (you have 0)" in out
    assert "Datamined Wake Exceptions (you have 0)" in out
    assert "Manufactured Material Trader" in out          # sourcing hint per short material
    assert "Arsenic (you have" not in out                 # held material isn't in the shortfall
    assert "checklist" in out.lower()                     # invites the farm-plan hand-off


def test_grade_parsed_from_phrase_when_arg_omitted():
    out = _cap().run_tool("blueprint_materials", {"blueprint": "grade 1 increased range"})
    assert "grade 1 Increased Range" in out
    assert "Atypical Disrupted Wake Echoes" in out         # G1 recipe
    assert "nothing to farm" in out.lower()                # fixture holds 30 of them


# --- have-everything + no-inventory paths ----------------------------------------------------

def test_no_materials_snapshot_still_gives_recipe_but_says_it_is_blind():
    out = _cap(snapshot=None).run_tool("blueprint_materials", {"blueprint": "increased range"})
    assert "grade 5 Increased Range" in out                # recipe still spoken
    assert "haven't read your materials" in out.lower()    # honest about no inventory


# --- disambiguation + validated fallback -----------------------------------------------------

def test_module_only_request_asks_which_blueprint():
    out = _cap().run_tool("blueprint_materials", {"blueprint": "grade 5 FSD"})
    assert "Which blueprint" in out
    assert "Increased Range" in out and "Faster Boot Sequence" in out
    assert "you have" not in out.lower()                   # no recipe until disambiguated


def test_unknown_blueprint_offers_real_names():
    out = _cap().run_tool("blueprint_materials", {"blueprint": "flux capacitor"})
    assert "don't have a blueprint matching 'flux capacitor'" in out
    assert "Increased Range" in out                        # only real blueprint names offered


def test_list_blueprints_for_a_module():
    out = _cap().run_tool("list_engineering_blueprints", {"module": "FSD"})
    assert out.startswith("Blueprints for the Frame Shift Drive:")
    assert "Increased Range" in out and "Shielded" in out


def test_list_blueprints_unknown_module_is_honest():
    out = _cap().run_tool("list_engineering_blueprints", {"module": "flux capacitor"})
    assert "don't have blueprints listed for 'flux capacitor'" in out


# --- fail soft + registry contract -----------------------------------------------------------

def test_unknown_tool_and_bad_input_fail_soft():
    assert "Unknown tool" in _cap().run_tool("nope", {})
    # a blank blueprint asks for one rather than raising
    assert "Which blueprint" in _cap().run_tool("blueprint_materials", {"blueprint": ""})


def test_help_metadata_is_complete_and_registers():
    cap = _cap()
    assert help_meta_problems(cap.help_meta()) == []
    reg = CapabilityRegistry()
    reg.register(cap)
    assert "engineering blueprints" in reg.categories()
    assert "SHORT" in reg.run_tool("blueprint_materials", {"blueprint": "increased range"})
