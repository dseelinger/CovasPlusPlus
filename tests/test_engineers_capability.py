"""Unit tests for the EngineersCapability (#65; offline, DESIGN §9).

Drives the two tools against a stubbed progress getter + fake clipboard — no journal
directory, no network. Locks the journal-grounded spoken shapes: per-engineer location +
status + what's-left, the by-module list with unlock tags, the clipboard/plot handoff, the
unlock-status overview, and the no-progress-yet path.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from covas.capabilities.base import help_meta_problems
from covas.capabilities.engineers_capability import EngineersCapability
from covas.ed.engineers import parse_engineer_progress

_FIXTURE = Path(__file__).parent / "fixtures" / "ed" / "engineer_progress.json"


def _progress() -> dict:
    return parse_engineer_progress(json.loads(_FIXTURE.read_text(encoding="utf-8")))


def _cap(progress="fixture", *, system=None, clip=None):
    prog = _progress() if progress == "fixture" else (progress or {})
    return EngineersCapability(
        get_progress=lambda: prog,
        get_current_system=(lambda: system),
        clipboard=clip)


# --- help metadata contract -------------------------------------------------------------

def test_help_meta_is_complete():
    assert help_meta_problems(_cap().help_meta()) == []


# --- find by name: location + grounded status -------------------------------------------

def test_find_by_name_unlocked_reports_grade_and_location():
    clips: list[str] = []
    out = _cap(clip=clips.append).run_tool("find_engineer", {"engineer": "Farseer"})
    assert "Deciat" in out and "Farseer Inc" in out
    assert "UNLOCKED" in out and "grade 5" in out
    assert "Frame Shift Drive" in out
    assert clips == ["Deciat"]  # copied for plotting


def test_find_by_name_known_reports_whats_left():
    out = _cap().run_tool("find_engineer", {"engineer": "Tod McQuinn"})
    assert "DISCOVERED" in out
    # The remaining requirement text comes from the reference table.
    assert "Bounty Vouchers" in out


def test_find_by_name_invited_reports_unlock_task():
    out = _cap().run_tool("find_engineer", {"engineer": "The Dweller"})
    assert "INVITED" in out
    assert "500,000 credits" in out


def test_find_by_name_not_started_gives_access_then_unlock():
    out = _cap().run_tool("find_engineer", {"engineer": "Selene Jean"})
    assert "haven't started" in out.lower()
    assert "Painite" in out  # the unlock gift


def test_find_by_name_unknown_lists_some():
    out = _cap().run_tool("find_engineer", {"engineer": "Zorbo the Great"})
    assert "don't recognise" in out.lower()


def test_find_by_name_already_there_does_not_copy():
    clips: list[str] = []
    out = _cap(system="Deciat", clip=clips.append).run_tool(
        "find_engineer", {"engineer": "Farseer"})
    assert "already in that system" in out.lower()
    assert clips == []


def test_permit_note_surfaced():
    out = _cap().run_tool("find_engineer", {"engineer": "Colonel Bris Dekker"})
    assert "permit" in out.lower()


# --- find by module ---------------------------------------------------------------------

def test_find_by_module_lists_engineers_with_status_tags():
    out = _cap().run_tool("find_engineer", {"module": "FSD"})
    assert "Felicity Farseer" in out
    assert "UNLOCKED" in out           # Farseer is unlocked in the fixture
    assert "not yet unlocked" in out   # e.g. Palin/Dekker are not


def test_find_by_module_unknown():
    out = _cap().run_tool("find_engineer", {"module": "warp core"})
    assert "don't have" in out.lower()


# --- unlock status overview -------------------------------------------------------------

def test_status_overview_counts_and_buckets():
    out = _cap().run_tool("engineer_unlock_status", {})
    assert "unlocked 2 of" in out.lower()
    assert "In progress:" in out
    assert "The Dweller (invited)" in out
    assert "Tod 'The Blaster' McQuinn (known)" in out


def test_status_overview_barred_not_bucketed_as_not_started():
    # A "Barred" engineer is a real journal state, not "not yet started": it must be reported
    # distinctly (matching _status_sentence/_short_status), never lumped into "Not yet started".
    from covas.ed.engineers import ENGINEERS, EngineerStatus
    barred = ENGINEERS[0]
    out = _cap(progress={barred.name: EngineerStatus("Barred")}).run_tool(
        "engineer_unlock_status", {})
    assert f"{barred.name} (barred)" in out       # surfaced under In progress, tagged
    assert "In progress:" in out
    not_started = out.split("Not yet started:", 1)[1] if "Not yet started:" in out else ""
    assert barred.name not in not_started         # NOT in the locked / not-started bucket


def test_deliver_survives_a_raising_current_system_getter():
    # A raising current-system getter must not turn a good answer (location + status) into a
    # generic error — _deliver guards it like _progress. (Regression for the unguarded call.)
    clips: list[str] = []

    def boom() -> str:
        raise RuntimeError("ed context exploded")

    cap = EngineersCapability(get_progress=_progress, get_current_system=boom,
                              clipboard=clips.append)
    out = cap.run_tool("find_engineer", {"engineer": "Farseer"})
    assert "Deciat" in out and "Frame Shift Drive" in out   # the good answer is preserved
    assert "error" not in out.lower()                        # no generic error leaked
    assert clips == ["Deciat"]                               # copy still happens (getter -> None)


def test_status_overview_no_progress_yet():
    out = _cap(progress={}).run_tool("engineer_unlock_status", {})
    assert "haven't read your engineer progress" in out.lower()


def test_find_by_name_no_progress_yet_still_gives_requirements():
    out = _cap(progress={}).run_tool("find_engineer", {"engineer": "Farseer"})
    assert "Meta-Alloys" in out  # requirement still spoken from the table


# --- fail soft --------------------------------------------------------------------------

def test_tool_never_raises_on_bad_getter():
    def boom() -> dict:
        raise RuntimeError("journal exploded")
    cap = EngineersCapability(get_progress=boom)
    out = cap.run_tool("find_engineer", {"engineer": "Farseer"})
    # Falls back to the no-progress path (requirements from the table), never raises.
    assert "Meta-Alloys" in out


@pytest.mark.parametrize("inp", [{}, {"engineer": "", "module": ""}])
def test_find_with_no_args_prompts(inp):
    out = _cap().run_tool("find_engineer", inp)
    assert "engineer" in out.lower() and "module" in out.lower()
