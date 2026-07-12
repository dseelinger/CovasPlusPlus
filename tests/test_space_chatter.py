"""Unit tests for C6 space chatter — eligibility gating, fact gating, deterministic rotation."""
from __future__ import annotations

from covas.mixer import (
    COMMS,
    ChatterPlayer,
    Cue,
    CueDriver,
    CueGovernor,
    CueRegistry,
    EligibilityEngine,
    GovernorConfig,
    chatter_cues,
    is_flavor_safe,
    register_chatter,
)
from covas.mixer.eligibility import STATES, unknown_states


class _Speak:
    def __init__(self, ok=True):
        self.ok = ok
        self.said: list[tuple[str, str]] = []

    def __call__(self, text, bus):
        self.said.append((text, bus))
        return self.ok


# ---- the shipped cues are valid and use real states ----------------------------------------

def test_chatter_cues_register_cleanly():
    reg = CueRegistry()
    register_chatter(reg)
    assert reg.contract_violations() == []
    assert len(reg.cues()) == len(chatter_cues())


def test_chatter_eligible_states_are_all_in_the_vocabulary():
    for cue in chatter_cues():
        assert unknown_states(cue.eligible_states) == set()
        assert cue.eligible_states <= STATES


# ---- eligibility is a function of state ----------------------------------------------------

def test_station_traffic_only_where_populated():
    reg = CueRegistry(chatter_cues())
    populated = {c.name for c in reg.eligible({"populated"})}
    assert "station_traffic" in populated
    unpop = {c.name for c in reg.eligible({"unpopulated", "deep_space"})}
    assert "station_traffic" not in unpop
    assert "deep_space_musing" in unpop        # deep-space musing MORE eligible out there


# ---- fact gating: fact_bearing NEVER routes to the LLM -------------------------------------

def test_fact_bearing_cue_never_calls_the_generator():
    calls = []

    def spy_gen(prompt):
        calls.append(prompt)
        return "generated fact-bearing line"

    cue = Cue("facty", COMMS, {"populated"}, fact_bearing=True,
              phrasings=("Pool line one.", "Pool line two."))
    player = ChatterPlayer(_Speak(), generate=spy_gen)
    text, source = player.line_for(cue)
    assert source == "pool" and text == "Pool line one."
    assert calls == []                          # the generator was never touched


def test_flavor_cue_uses_validated_llm_then_falls_back_to_pool():
    cue = Cue("muse", COMMS, {"deep_space"}, fact_bearing=False,
              phrasings=("Pool A.", "Pool B."))

    # A safe flavor line is used verbatim.
    safe = ChatterPlayer(_Speak(), generate=lambda p: "just the void and me")
    assert safe.line_for(cue) == ("just the void and me", "flavor")

    # An unsafe flavor line (proper noun / number) is rejected -> pool.
    unsafe = ChatterPlayer(_Speak(), generate=lambda p: "Traffic near Sol is heavy, 3 ships")
    assert unsafe.line_for(cue) == ("Pool A.", "pool")

    # A generator error -> pool, never raises.
    boom = ChatterPlayer(_Speak(), generate=lambda p: (_ for _ in ()).throw(RuntimeError("x")))
    assert boom.line_for(cue)[1] == "pool"


def test_is_flavor_safe():
    assert is_flavor_safe("Quiet out here, just the dark.")
    assert not is_flavor_safe("Docking at Jameson Ring.")   # proper noun -> checkable
    assert not is_flavor_safe("3 ships inbound.")           # number -> checkable
    assert not is_flavor_safe("   ")


# ---- deterministic pool rotation ------------------------------------------------------------

def test_pool_rotation_is_deterministic():
    cue = Cue("rot", COMMS, {"populated"}, phrasings=("a", "b", "c"))
    speak = _Speak()
    player = ChatterPlayer(speak)
    for _ in range(4):
        player(cue)
    assert [t for t, _ in speak.said] == ["a", "b", "c", "a"]


def test_failed_speak_does_not_advance_rotation():
    cue = Cue("rot", COMMS, {"populated"}, phrasings=("a", "b"))
    speak = _Speak(ok=False)
    player = ChatterPlayer(speak)
    assert player(cue) is False
    assert player(cue) is False
    assert [t for t, _ in speak.said] == ["a", "a"]   # never advanced past 'a'


def test_flavor_use_does_not_consume_a_pool_slot():
    cue = Cue("muse", COMMS, {"deep_space"}, fact_bearing=False, phrasings=("A.", "B."))
    speak = _Speak()
    # alternate: unsafe (pool A), safe flavor, unsafe (pool B)
    scripted = iter(["Sol 7 ahead", "peaceful drift", "Achenar 9 near"])
    player = ChatterPlayer(speak, generate=lambda p: next(scripted))
    player(cue)   # unsafe -> pool 'A.'
    player(cue)   # safe flavor -> 'peaceful drift' (pool pointer unchanged)
    player(cue)   # unsafe -> pool 'B.'
    assert [t for t, _ in speak.said] == ["A.", "peaceful drift", "B."]


# ---- governed by C3 -------------------------------------------------------------------------

def test_chatter_plays_through_the_driver_and_is_governed():
    reg = CueRegistry(chatter_cues())
    eng = EligibilityEngine()
    gov = CueGovernor(GovernorConfig(enabled=True, min_interval=8.0, default_cooldown=90.0),
                      clock=lambda: 0.0)
    speak = _Speak()
    drv = CueDriver(reg, eng, gov, ChatterPlayer(speak), clock=lambda: 0.0)

    from covas.ed.status import FLAGS
    eng.note_journal({"event": "FSDJump", "Population": 9000})   # populated
    eng.note_flags(FLAGS["Docked"])            # docked (populated) -> only station_traffic eligible
    played = drv.tick()
    assert played is not None and played.name == "station_traffic"
    assert len(speak.said) == 1
    # immediately again: global min-interval blocks a second line
    assert drv.tick() is None
    assert len(speak.said) == 1
