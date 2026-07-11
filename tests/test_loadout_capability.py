"""Unit tests for the LoadoutCapability (N9; offline, DESIGN §9).

Drives the three tools against the recorded Corsair snapshot (engineered power distributor,
stock SCO FSD) through a stubbed `get_loadout` — no journal directory, no network. Locks the
spoken shapes: per-module engineering with key modifiers, the experimental-effects list, the
grouped rundown, the validated unknown-module fallback, and the no-loadout-yet path.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from covas.capabilities.base import CapabilityRegistry, help_meta_problems
from covas.capabilities.loadout_capability import LoadoutCapability
from covas.ed.loadout import parse_loadout

_FIXTURE = Path(__file__).parent / "fixtures" / "journal_loadout_corsair.json"


def _snapshot():
    return parse_loadout(json.loads(_FIXTURE.read_text(encoding="utf-8")))


def _cap(snapshot="fixture"):
    snap = _snapshot() if snapshot == "fixture" else snapshot
    return LoadoutCapability(get_loadout=lambda: snap)


# --- no loadout yet ---------------------------------------------------------------------------

@pytest.mark.parametrize("tool,inp", [
    ("ship_engineering", {}),
    ("list_experimental_effects", {}),
    ("ship_modules", {}),
])
def test_no_loadout_yet_says_so(tool, inp):
    cap = _cap(snapshot=None)
    out = cap.run_tool(tool, inp)
    assert "board your ship" in out.lower()


# --- engineering ------------------------------------------------------------------------------

def test_engineering_overview_names_blueprint_grade_and_experimental():
    out = _cap().run_tool("ship_engineering", {})
    assert "Power Distributor" in out
    assert "Charge Enhanced" in out and "grade 5" in out
    assert "Super Conduits" in out


def test_engineering_for_a_stock_module_is_honest():
    out = _cap().run_tool("ship_engineering", {"module": "FSD"})
    assert "no engineering" in out.lower()
    assert "Frame Shift Drive" in out


def test_engineering_detail_speaks_key_modifiers():
    out = _cap().run_tool("ship_engineering", {"module": "distributor"})
    assert "Charge Enhanced grade 5, with Super Conduits" in out
    assert "The Dweller" in out
    assert "Key changes:" in out and "+51% weapons recharge" in out


# --- experimental effects ----------------------------------------------------------------------

def test_experimental_effects_listed_per_module():
    out = _cap().run_tool("list_experimental_effects", {})
    assert "Super Conduits on the 7A Power Distributor" in out


def test_no_experimentals_is_honest():
    # Strip the engineering: a snapshot whose only engineered module is gone.
    snap = _snapshot()
    bare = type(snap)(ship=snap.ship, modules=tuple(
        type(m)(slot=m.slot, item=m.item) for m in snap.modules))
    out = _cap(snapshot=bare).run_tool("list_experimental_effects", {})
    assert "no experimental effects" in out.lower()


# --- modules ------------------------------------------------------------------------------------

def test_module_rundown_groups_and_collapses():
    out = _cap().run_tool("ship_modules", {})
    assert out.startswith("Your Corsair:")
    assert "Hardpoints: 3 medium gimballed Multi-Cannon" in out
    assert "Utilities:" in out and "Core:" in out and "Optional internals:" in out
    assert "(engineered)" in out                       # the distributor is marked
    assert "Max jump range 33.0 light-years." in out
    assert "cockpit" not in out.lower()                # cosmetic slots stay out of the list


def test_module_detail_names_module_and_slot():
    out = _cap().run_tool("ship_modules", {"module": "fuel scoop"})
    assert "5A Fuel Scoop in the optional slot 6, size 5." in out


def test_unknown_module_fallback_offers_real_names():
    out = _cap().run_tool("ship_modules", {"module": "flux capacitor"})
    assert "I don't see 'flux capacitor'" in out
    assert "Fitted modules include:" in out
    assert "Multi-Cannon" in out                       # only genuinely fitted names offered


# --- registry contract --------------------------------------------------------------------------

def test_help_metadata_is_complete_and_registers():
    cap = _cap()
    assert help_meta_problems(cap.help_meta()) == []
    reg = CapabilityRegistry()
    reg.register(cap)
    assert "ship loadout" in reg.categories()
    assert "Charge Enhanced" in reg.run_tool("ship_engineering", {"module": "distributor"})
