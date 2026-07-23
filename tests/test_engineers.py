"""Unit tests for the engineers reference table + EngineerProgress grounding (#65; offline).

Covers the pure data layer: name/specialty matching, the two shapes of the EngineerProgress
journal event, EDContext merge semantics, and the journal handler wiring — all against a
committed fixture, no network, no journal directory, no threads.
"""
from __future__ import annotations

import json
from pathlib import Path

from covas.ed import EDContext
from covas.ed.engineers import (
    ENGINEERS,
    EngineerStatus,
    engineer_dashboard,
    find_by_specialty,
    find_engineer,
    parse_engineer_progress,
    status_for,
)
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


# --- dashboard view-model (issue #133) --------------------------------------------------

def test_dashboard_covers_every_engineer_and_is_serializable():
    import json
    dash = engineer_dashboard(parse_engineer_progress(_event()))
    assert dash["total"] == len(ENGINEERS)
    assert len(dash["engineers"]) == len(ENGINEERS)
    # Bucket counts partition the whole fleet.
    c = dash["counts"]
    assert c["unlocked"] + c["in_progress"] + c["locked"] == len(ENGINEERS)
    # JSON-serializable end to end (the route jsonify's it directly).
    json.dumps(dash)


def test_dashboard_unlocked_row_carries_grade_and_no_outstanding():
    dash = engineer_dashboard(parse_engineer_progress(_event()))
    row = next(r for r in dash["engineers"] if r["name"] == "Felicity Farseer")
    assert row["group"] == "unlocked"
    assert row["progress"] == "Unlocked"
    assert row["grade"] == 5
    assert row["outstanding"] == ""            # nothing left once unlocked


def test_dashboard_invited_row_needs_only_the_unlock_gift():
    dash = engineer_dashboard(parse_engineer_progress(_event()))
    row = next(r for r in dash["engineers"] if r["name"] == "The Dweller")
    assert row["group"] == "in_progress" and row["progress"] == "Invited"
    dweller = find_engineer("the dweller")
    assert row["outstanding"] == dweller.unlock   # just the gift, not the access task


def test_dashboard_discovered_row_needs_access_then_gift():
    dash = engineer_dashboard(parse_engineer_progress(_event()))
    row = next(r for r in dash["engineers"] if r["name"] == "Tod 'The Blaster' McQuinn")
    assert row["group"] == "in_progress" and row["progress"] == "Known"
    # Known = discovered but not invited: the access task remains, then the gift.
    assert row["access"] in row["outstanding"]
    assert "Then" in row["outstanding"]


def test_dashboard_locked_row_has_full_requirement():
    dash = engineer_dashboard(parse_engineer_progress(_event()))
    # Palin isn't in the fixture progress at all -> locked, full requirement shown.
    row = next(r for r in dash["engineers"] if r["name"] == "Professor Palin")
    assert row["group"] == "locked" and row["progress"] == ""
    assert row["grade"] is None
    assert row["access"] in row["outstanding"] and row["unlock"][1:] in row["outstanding"]


def test_dashboard_empty_progress_is_fail_soft():
    # Before any EngineerProgress event: has_progress False, every engineer locked, still
    # carries its requirement so the page is useful with zero journal data.
    for empty in (None, {}):
        dash = engineer_dashboard(empty)
        assert dash["has_progress"] is False
        assert dash["counts"]["locked"] == len(ENGINEERS)
        assert dash["counts"]["unlocked"] == 0
        assert all(r["outstanding"] for r in dash["engineers"])


def test_dashboard_barred_row_is_locked_bucket():
    dash = engineer_dashboard({"The Dweller": EngineerStatus(progress="Barred")})
    row = next(r for r in dash["engineers"] if r["name"] == "The Dweller")
    assert row["group"] == "locked" and row["progress"] == "Barred"
    assert "barred" in row["outstanding"].lower()
