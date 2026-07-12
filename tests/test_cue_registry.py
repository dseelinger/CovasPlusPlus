"""Unit tests for the cue registry contract + eligibility (C2). Offline, no device."""
from __future__ import annotations

import pytest

from covas.mixer import COMMS, COVAS, MUSIC, Cue, CueRegistry, cue_problems, validate_cue


def _complete(**kw) -> Cue:
    base = dict(name="test_cue", bus=COMMS, eligible_states={"docked"}, cooldown_s=5.0)
    base.update(kw)
    return Cue(**base)


# ---- contract: incomplete cues are flagged, complete ones pass -----------------------------

def test_complete_cue_has_no_problems():
    assert cue_problems(_complete()) == []


def test_cue_without_bus_fails():
    problems = cue_problems(_complete(bus=""))
    assert problems and "no target bus" in problems[0]


def test_cue_with_unknown_bus_fails():
    # A cue can't target a bus the mixer doesn't define — structural drift prevention.
    problems = cue_problems(_complete(bus="teleprompter"))
    assert problems and "unknown bus" in problems[0]


def test_cue_without_eligibility_set_fails():
    # "declared no eligibility set" (None) is a contract failure...
    problems = cue_problems(_complete(eligible_states=None))
    assert problems and "eligibility set" in problems[0]


def test_cue_with_non_set_eligibility_fails():
    problems = cue_problems(_complete(eligible_states="docked"))  # a bare string, not a set
    assert problems and "eligibility set" in problems[0]


def test_negative_cooldown_fails():
    problems = cue_problems(_complete(cooldown_s=-1.0))
    assert problems and "cooldown" in problems[0]


def test_non_cue_object_is_flagged():
    assert cue_problems({"name": "x"})  # a dict is not a Cue


def test_validate_cue_raises_on_incomplete_and_returns_complete():
    good = _complete()
    assert validate_cue(good) is good
    with pytest.raises(ValueError):
        validate_cue(_complete(bus=""))


# ---- empty trigger set is VALID but silent -------------------------------------------------

def test_empty_eligibility_is_valid_but_never_eligible():
    cue = _complete(name="gated_off", eligible_states=set())
    assert cue_problems(cue) == []                     # valid — no error
    reg = CueRegistry([cue])                            # registers fine
    assert reg.eligible({"docked", "supercruise"}) == []   # never eligible (silent)


# ---- registry behavior ---------------------------------------------------------------------

def test_register_refuses_incomplete_cue():
    reg = CueRegistry()
    with pytest.raises(ValueError):
        reg.register(_complete(bus=""))
    assert reg.cues() == []


def test_register_rejects_duplicate_name():
    reg = CueRegistry([_complete(name="dup")])
    with pytest.raises(ValueError):
        reg.register(_complete(name="dup", bus=COVAS))


def test_contract_violations_empty_on_built_registry():
    reg = CueRegistry([
        _complete(name="a"),
        _complete(name="b", bus=MUSIC, eligible_states={"deep_space"}, context_tag="deep"),
    ])
    assert reg.contract_violations() == []


# ---- eligibility query returns the right cues per state / bus ------------------------------

def _registry() -> CueRegistry:
    return CueRegistry([
        Cue("dock_chatter", COMMS, {"docked"}, phrasings=("cleared to dock",)),
        Cue("deep_music", MUSIC, {"deep_space"}, context_tag="deep"),
        Cue("either", COMMS, {"docked", "supercruise"}),
        Cue("gated", COVAS, set()),   # empty -> never eligible
    ])


def test_eligible_matches_on_state_intersection():
    reg = _registry()
    names = {c.name for c in reg.eligible({"docked"})}
    assert names == {"dock_chatter", "either"}          # not deep_music, not gated

    assert {c.name for c in reg.eligible({"supercruise"})} == {"either"}
    assert {c.name for c in reg.eligible({"deep_space"})} == {"deep_music"}
    assert reg.eligible(set()) == []                    # nothing active -> nothing eligible


def test_eligible_by_bus_groups_results():
    reg = _registry()
    by_bus = reg.eligible_by_bus({"docked", "deep_space"})
    assert set(by_bus) == {COMMS, MUSIC}                # only buses with an eligible cue
    assert {c.name for c in by_bus[COMMS]} == {"dock_chatter", "either"}
    assert {c.name for c in by_bus[MUSIC]} == {"deep_music"}


def test_phrasing_rotation_is_deterministic():
    cue = Cue("c", COMMS, {"docked"}, phrasings=("a", "b", "c"))
    assert [cue.phrasing_at(i) for i in range(4)] == ["a", "b", "c", "a"]
    assert Cue("empty", COMMS, {"docked"}).phrasing_at(3) == ""
