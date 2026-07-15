"""Unit tests for the engineers reference table + EngineerProgress grounding (#65; offline).

Covers the pure data layer: name/specialty matching, the two shapes of the EngineerProgress
journal event, EDContext merge semantics, and the journal handler wiring — all against a
committed fixture, no network, no journal directory, no threads.
"""
from __future__ import annotations

import json
from pathlib import Path

from covas.ed import EDContext
from covas.ed.engineers import (ENGINEERS, EngineerStatus, find_by_specialty, find_engineer,
                                parse_engineer_progress, status_for)
from covas.ed.journal import apply_journal_event

_FIXTURE = Path(__file__).parent / "fixtures" / "ed" / "engineer_progress.json"


def _event() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


# --- reference table integrity ----------------------------------------------------------

def test_every_engineer_is_completely_described():
    assert len(ENGINEERS) >= 20
    for e in ENGINEERS:
        assert e.name and e.system and e.station
        assert e.region in ("bubble", "colonia")
        assert e.specialties and e.access and e.unlock
    # Names are unique (they key the journal join).
    names = [e.name for e in ENGINEERS]
    assert len(names) == len(set(names))


# --- name matching ----------------------------------------------------------------------

def test_find_engineer_exact_and_partial():
    assert find_engineer("Felicity Farseer").name == "Felicity Farseer"
    assert find_engineer("farseer").name == "Felicity Farseer"
    assert find_engineer("the dweller").name == "The Dweller"


def test_find_engineer_last_name_and_quotes():
    # 'Tod 'The Blaster' McQuinn' is findable by a bare last name.
    assert find_engineer("mcquinn").name == "Tod 'The Blaster' McQuinn"


def test_find_engineer_unknown_is_none():
    assert find_engineer("nobody at all") is None
    assert find_engineer("") is None


# --- specialty matching -----------------------------------------------------------------

def test_find_by_specialty_synonym_fsd():
    names = {e.name for e in find_by_specialty("FSD")}
    assert "Felicity Farseer" in names
    assert "Professor Palin" in names  # Palin does FSD too


def test_find_by_specialty_shields_and_lasers():
    assert any(e.name == "Lei Cheung" for e in find_by_specialty("shields"))
    # 'laser' should reach the beam/burst/pulse specialists.
    assert any(e.name == "Broo Tarquin" for e in find_by_specialty("laser"))


def test_find_by_specialty_bubble_before_colonia():
    hits = find_by_specialty("thrusters")
    assert hits  # Palin / Chloe Sedesi etc.
    regions = [e.region for e in hits]
    # No colonia engineer appears before a bubble one.
    assert regions == sorted(regions, key=lambda r: 0 if r == "bubble" else 1)


def test_find_by_specialty_unknown_is_empty():
    assert find_by_specialty("warp core") == []


# --- EngineerProgress parsing (summary form) --------------------------------------------

def test_parse_progress_summary_form():
    prog = parse_engineer_progress(_event())
    assert prog["Felicity Farseer"] == EngineerStatus(progress="Unlocked", rank=5)
    assert prog["The Dweller"].progress == "Invited"
    assert prog["The Dweller"].rank is None
    assert prog["Tod 'The Blaster' McQuinn"].progress == "Known"


def test_parse_progress_single_update_form():
    ev = {"event": "EngineerProgress", "Engineer": "Selene Jean",
          "EngineerID": 300210, "Progress": "Unlocked", "Rank": 4}
    prog = parse_engineer_progress(ev)
    assert prog == {"Selene Jean": EngineerStatus(progress="Unlocked", rank=4)}


def test_parse_progress_skips_junk_rows():
    ev = {"event": "EngineerProgress", "Engineers": [
        {"Engineer": "", "Progress": "Known"},          # no name
        {"EngineerID": 1, "Progress": "Unlocked"},       # no name
        {"Engineer": "Bill Turner"},                     # no progress
        {"Engineer": "Ram Tah", "Progress": "Unlocked", "Rank": 5},
    ]}
    prog = parse_engineer_progress(ev)
    assert list(prog) == ["Ram Tah"]


# --- status_for -------------------------------------------------------------------------

def test_status_for_matches_by_name():
    prog = parse_engineer_progress(_event())
    farseer = find_engineer("farseer")
    assert status_for(farseer, prog).unlocked
    palin = find_engineer("palin")
    assert status_for(palin, prog) is None  # not in the fixture progress


# --- EDContext merge + journal handler --------------------------------------------------

def test_context_merges_progress_not_replaces():
    ctx = EDContext()
    apply_journal_event(ctx, _event())
    assert ctx.engineer_progress()["The Dweller"].progress == "Invited"
    # A later single-engineer update patches one, leaving the rest intact.
    apply_journal_event(ctx, {"event": "EngineerProgress", "Engineer": "The Dweller",
                              "Progress": "Unlocked", "Rank": 2})
    prog = ctx.engineer_progress()
    assert prog["The Dweller"] == EngineerStatus(progress="Unlocked", rank=2)
    assert prog["Felicity Farseer"].progress == "Unlocked"  # untouched


def test_context_progress_empty_until_seen():
    assert EDContext().engineer_progress() == {}
