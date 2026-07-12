"""Unit tests for the C3 eligibility engine — game state -> state tokens. Offline."""
from __future__ import annotations

from covas.ed.status import FLAGS
from covas.mixer import EligibilityEngine, flag_states, fuel_states, journal_states
from covas.mixer.eligibility import STATES, unknown_states


def _flags(*names: str) -> int:
    v = 0
    for n in names:
        v |= FLAGS[n]
    return v


def test_flag_states_docked():
    s = flag_states(_flags("Docked", "InMainShip"))
    assert "docked" in s and "in_ship" in s
    assert "normal_space" not in s        # docked is not "out in space"


def test_flag_states_normal_space_and_supercruise():
    assert "normal_space" in flag_states(_flags("InMainShip"))
    sc = flag_states(_flags("InMainShip", "Supercruise"))
    assert "supercruise" in sc and "normal_space" not in sc


def test_flag_states_scooping_implies_near_star():
    s = flag_states(_flags("InMainShip", "ScoopingFuel"))
    assert {"scooping_fuel", "near_star"} <= s


def test_flag_states_danger_interdiction_hardpoints_hyperspace():
    assert "in_danger" in flag_states(_flags("IsInDanger"))
    assert "interdicted" in flag_states(_flags("BeingInterdicted"))
    assert "hardpoints" in flag_states(_flags("HardpointsDeployed"))
    assert "hyperspace" in flag_states(_flags("FsdJump"))
    assert "low_fuel" in flag_states(_flags("LowFuel"))


def test_fuel_states_thresholds():
    assert fuel_states(8.0) == {"low_fuel", "fuel_critical"}
    assert fuel_states(20.0) == {"low_fuel"}
    assert fuel_states(50.0) == set()
    assert fuel_states(None) == set()


def test_journal_states_population():
    assert journal_states({"event": "FSDJump", "Population": 12345}) == {"populated"}
    assert journal_states({"event": "FSDJump", "Population": 0}) == {"unpopulated", "deep_space"}
    assert journal_states({"event": "FSDJump"}) is None        # no Population -> no change
    assert journal_states({"event": "Loadout", "Population": 0}) is None  # not an arrival event


def test_engine_merges_flags_journal_and_fuel():
    eng = EligibilityEngine()
    eng.note_event({"type": "ed_event", "event": "FSDJump", "Population": 500,
                    "flags": _flags("InMainShip", "Supercruise")})
    states = eng.states(fuel_pct=8.0)
    assert {"populated", "supercruise", "in_ship", "low_fuel", "fuel_critical"} <= states


def test_engine_population_is_sticky_across_status_updates():
    eng = EligibilityEngine()
    eng.note_journal({"event": "FSDJump", "Population": 0})     # arrived unpopulated
    eng.note_flags(_flags("InMainShip"))                       # later status-only update
    states = eng.states()
    assert "unpopulated" in states and "deep_space" in states  # population persisted
    assert "normal_space" in states


def test_engine_empty_until_told_anything():
    assert EligibilityEngine().states() == frozenset()


def test_all_emitted_tokens_are_in_the_vocabulary():
    # Every token the engine can emit must be declared in STATES (no drift).
    emitted = flag_states(_flags(*FLAGS.keys())) | fuel_states(5.0)
    emitted |= journal_states({"event": "FSDJump", "Population": 0}) or set()
    emitted |= journal_states({"event": "FSDJump", "Population": 9}) or set()
    assert unknown_states(emitted) == set()
    assert emitted <= STATES
