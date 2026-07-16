"""Unit tests for the OnFootEngineeringCapability (#73; offline, DESIGN §9).

Drives the single read tool against a stubbed progress getter + fake clipboard — no journal
directory, no network. Locks the four selector paths (suit / weapon / modification / engineer),
the overview, the journal-grounded status join, the clipboard/plot handoff, and fail-soft.
"""
from __future__ import annotations

from covas.capabilities.base import help_meta_problems
from covas.capabilities.on_foot_engineering_capability import OnFootEngineeringCapability
from covas.ed.engineers import EngineerStatus

_TOOL = "on_foot_engineering"


def _progress() -> dict:
    # A small live-progress snapshot keyed by the exact on-foot engineer journal names.
    return {
        "Domino Green": EngineerStatus(progress="Unlocked", rank=5),
        "Hero Ferrari": EngineerStatus(progress="Invited"),
        "Wellington Beck": EngineerStatus(progress="Known"),
    }


def _cap(progress=None, *, system=None, clip=None):
    return OnFootEngineeringCapability(
        get_progress=(lambda: progress) if progress is not None else None,
        get_current_system=(lambda: system),
        clipboard=clip)


# --- help metadata contract -------------------------------------------------------------

def test_help_meta_is_complete():
    assert help_meta_problems(_cap().help_meta()) == []


# --- suit / weapon grade upgrades -------------------------------------------------------

def test_suit_reports_recipe_and_sourcing():
    out = _cap().run_tool(_TOOL, {"suit": "Maverick"})
    assert "Maverick Suit" in out
    assert "12x Carbon Fibre Plating" in out and "12x Graphene" in out
    assert "Pioneer Supplies" in out
    assert "Suit Schematic:" in out            # a sourcing hint is attached


def test_suit_respects_explicit_grade():
    out = _cap().run_tool(_TOOL, {"suit": "Dominator", "grade": 3})
    assert "grade 3" in out
    assert "5x Titanium Plating" in out         # grade-3 component count


def test_weapon_reports_family_damage_and_class_materials():
    out = _cap().run_tool(_TOOL, {"weapon": "Manticore Oppressor"})
    assert "Manticore" in out and "Plasma" in out
    assert "Chemical Superbase" in out


def test_unknown_suit_and_weapon_never_guess():
    assert "don't recognise" in _cap().run_tool(_TOOL, {"suit": "warp suit"}).lower()
    assert "don't recognise" in _cap().run_tool(_TOOL, {"weapon": "ray gun"}).lower()


# --- modification -> engineers ----------------------------------------------------------

def test_modification_lists_offering_engineers_with_status():
    out = _cap(progress=_progress()).run_tool(_TOOL, {"modification": "Greater Range"})
    assert "Greater Range" in out
    assert "Domino Green" in out and "UNLOCKED" in out          # from the progress stub
    assert "Wellington Beck" in out and "known" in out.lower()


def test_unknown_modification_offers_real_examples():
    out = _cap().run_tool(_TOOL, {"modification": "flux capacitor"})
    assert "don't have" in out.lower()
    assert "Greater Range" in out or "Night Vision" in out


# --- engineer ---------------------------------------------------------------------------

def test_engineer_reports_location_unlock_referral_and_copies_system():
    clips: list[str] = []
    out = _cap(clip=clips.append).run_tool(_TOOL, {"engineer": "Domino Green"})
    assert "The Jackrabbit" in out and "Orishis" in out
    assert "100 light-years" in out            # unlock task from the table
    assert "Kit Fowler" in out                 # referral target
    assert clips == ["Orishis"]                # copied for plotting


def test_engineer_grounded_status_when_progress_known():
    out = _cap(progress=_progress()).run_tool(_TOOL, {"engineer": "Hero Ferrari"})
    assert "INVITED" in out


def test_engineer_already_in_system_does_not_copy():
    clips: list[str] = []
    out = _cap(system="Orishis", clip=clips.append).run_tool(
        _TOOL, {"engineer": "Domino Green"})
    assert "already in that system" in out.lower()
    assert clips == []


def test_unknown_engineer_lists_some():
    out = _cap().run_tool(_TOOL, {"engineer": "Zorbo the Great"})
    assert "don't recognise" in out.lower()


# --- overview + dispatch ----------------------------------------------------------------

def test_overview_when_no_selector():
    out = _cap().run_tool(_TOOL, {})
    assert "grade 1 to 5" in out
    assert "13" not in out or "bubble" in out   # names the engineer split
    assert "Maverick" in out and "Manticore" in out


def test_unknown_tool_name():
    assert "Unknown tool" in _cap().run_tool("not_a_tool", {})


# --- fail soft --------------------------------------------------------------------------

def test_tool_never_raises_on_bad_getter():
    def boom() -> dict:
        raise RuntimeError("journal exploded")
    cap = OnFootEngineeringCapability(get_progress=boom)
    out = cap.run_tool(_TOOL, {"engineer": "Domino Green"})
    # Falls back to the no-progress path (requirements from the table), never raises.
    assert "100 light-years" in out
