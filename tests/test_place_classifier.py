"""Unit tests for the special-place classifier + grounded facts (issue #138) — pure, offline.

Covers engineer-base / own-carrier / landmark / first-visit classification from faked locations,
the unknown -> None default, and the `place_facts` gate (notable places/patterns carry grounded
facts; ordinary arrivals carry none).
"""
from __future__ import annotations

from covas.ed.place_classifier import (KIND_ENGINEER, KIND_FIRST_SYSTEM, KIND_LANDMARK,
                                       KIND_OWN_CARRIER, classify_station, classify_system,
                                       place_facts, render_facts)
from covas.ed.visit_ledger import VisitStats


# --- classify_station -----------------------------------------------------------------

def test_engineer_base_recognised_from_table():
    place = classify_station("Deciat", "Farseer Inc")
    assert place is not None and place.kind == KIND_ENGINEER
    assert "Felicity Farseer" in place.label
    assert "Frame Shift Drive" in place.detail


def test_engineer_match_is_case_insensitive():
    place = classify_station("deciat", "farseer inc")
    assert place is not None and place.kind == KIND_ENGINEER


def test_own_carrier_recognised_when_flagged():
    place = classify_station("SomeSystem", "My Carrier XYZ-123", at_own_carrier=True)
    assert place is not None and place.kind == KIND_OWN_CARRIER


def test_engineer_base_wins_over_carrier_flag():
    # Even if the carrier flag were somehow set, a real engineer base classifies as such.
    place = classify_station("Deciat", "Farseer Inc", at_own_carrier=True)
    assert place.kind == KIND_ENGINEER


def test_station_landmark_recognised():
    place = classify_station("Alpha Centauri", "Hutton Orbital")
    assert place is not None and place.kind == KIND_LANDMARK
    assert "Hutton" in place.label


def test_ordinary_station_is_none():
    assert classify_station("Random", "Generic Outpost") is None


def test_no_station_is_none():
    assert classify_station("Deciat", None) is None


# --- classify_system ------------------------------------------------------------------

def test_first_visit_system_classified():
    place = classify_system("Wolf 359", first_visit=True)
    assert place is not None and place.kind == KIND_FIRST_SYSTEM
    assert place.label == "Wolf 359"


def test_repeat_ordinary_system_is_none():
    assert classify_system("Wolf 359", first_visit=False) is None


def test_system_landmark_wins_over_first_visit():
    place = classify_system("Sol", first_visit=True)
    assert place.kind == KIND_LANDMARK and place.label == "Sol"


# --- place_facts gate -----------------------------------------------------------------

def _stats(total=1, v24=1, v7=1, first=True):
    return VisitStats(total=total, visits_24h=v24, visits_7d=v7, first_visit=first)


def test_ordinary_place_repeat_visit_carries_no_facts():
    """An ordinary station on an unremarkable repeat visit -> None (callout stays generic)."""
    place = classify_station("Random", "Generic Outpost")
    stats = _stats(total=3, v24=1, v7=1, first=False)
    assert place_facts(place, stats) is None


def test_engineer_base_carries_grounded_facts():
    place = classify_station("Deciat", "Farseer Inc")
    stats = _stats(total=10, v24=10, v7=10, first=False)
    facts = place_facts(place, stats)
    assert facts is not None
    assert facts["place"] == KIND_ENGINEER
    assert "Felicity Farseer" in facts["label"]
    assert facts["visits_24h"] == 10


def test_first_visit_alone_is_notable():
    facts = place_facts(None, _stats(total=1, v24=1, v7=1, first=True))
    assert facts is not None and facts["first_visit"] is True


def test_milestone_total_is_notable():
    facts = place_facts(None, _stats(total=50, v24=1, v7=3, first=False))
    assert facts is not None and facts["visit_number"] == 50


def test_high_frequency_alone_is_notable():
    facts = place_facts(None, _stats(total=8, v24=6, v7=8, first=False))
    assert facts is not None and facts["visits_24h"] == 6


def test_low_frequency_non_milestone_ordinary_place_is_none():
    facts = place_facts(None, _stats(total=3, v24=2, v7=3, first=False))
    assert facts is None


# --- render_facts (what the LLM is handed) --------------------------------------------

def test_render_facts_is_grounded_and_readable():
    facts = {"place": KIND_ENGINEER, "label": "Farseer Inc, Felicity Farseer's workshop",
             "detail": "engineers Frame Shift Drive, Thrusters", "visits_24h": 10}
    rendered = render_facts(facts)
    assert "Farseer Inc" in rendered
    assert "Frame Shift Drive" in rendered
    assert "10 times in the last 24 hours" in rendered


def test_render_facts_ordinals():
    assert "50th visit" in render_facts({"visit_number": 50})
    assert "first ever visit" in render_facts({"first_visit": True})
