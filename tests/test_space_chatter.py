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
    chatter_interval,
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


# ---- eligibility is a function of state: chatter is POPULATED-ONLY --------------------------

def test_chatter_is_populated_only():
    reg = CueRegistry(chatter_cues())
    populated = {c.name for c in reg.eligible({"populated"})}
    assert "station_traffic" in populated                 # chatter fires where there are people
    assert populated == {c.name for c in chatter_cues()}  # EVERY chatter cue needs populated
    # Out in empty/unpopulated space (or plain flight states) NOTHING chatters.
    assert reg.eligible({"unpopulated", "deep_space"}) == []
    assert reg.eligible({"supercruise", "normal_space"}) == []


def test_every_chatter_cue_requires_populated():
    for cue in chatter_cues():
        assert cue.eligible_states == frozenset({"populated"})


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
    eng.note_journal({"event": "FSDJump", "Population": 9000})   # populated -> chatter eligible
    eng.note_flags(FLAGS["Docked"])
    names = {c.name for c in chatter_cues()}
    played = drv.tick()
    assert played is not None and played.name in names      # a populated-system chatter line
    assert len(speak.said) == 1
    # immediately again: global min-interval blocks a second line
    assert drv.tick() is None
    assert len(speak.said) == 1


# ---- frequency: population-scaled interval + the ChatterPlayer gate -------------------------

def test_chatter_interval_scales_with_population():
    # Dense system (>= full_population) -> the fast min gap; unknown/unpopulated -> never.
    assert chatter_interval(45.0, 240.0, 1e9, 1e9) == 45.0
    assert chatter_interval(45.0, 240.0, 5e9, 1e9) == 45.0        # clamped at the ceiling
    assert chatter_interval(45.0, 240.0, None, 1e9) is None
    assert chatter_interval(45.0, 240.0, 0, 1e9) is None
    # A sparse system sits nearer the slow end; a mid system between the two.
    sparse = chatter_interval(45.0, 240.0, 1_000, 1e9)
    mid = chatter_interval(45.0, 240.0, 1_000_000, 1e9)
    assert 45.0 < mid < sparse <= 240.0
    # Busier system -> shorter gap (more frequent chatter), monotonically.
    assert chatter_interval(45.0, 240.0, 1e8, 1e9) < chatter_interval(45.0, 240.0, 1e4, 1e9)


def test_chatter_interval_tolerates_swapped_bounds():
    # min/max passed the wrong way round still yields a value within [45, 240].
    v = chatter_interval(240.0, 45.0, 1_000, 1e9)
    assert 45.0 <= v <= 240.0


def test_frequency_gate_suppresses_until_interval_elapses():
    cue = Cue("station_traffic", COMMS, {"populated"}, phrasings=("a", "b", "c"))
    speak = _Speak()
    now = {"t": 0.0}
    player = ChatterPlayer(speak, min_interval=lambda: 30.0, clock=lambda: now["t"])
    assert player(cue) is True                    # first line: nothing spoken yet
    now["t"] = 10.0
    assert player(cue) is False                   # only 10s < 30s -> suppressed
    assert len(speak.said) == 1
    now["t"] = 40.0
    assert player(cue) is True                    # 40s >= 30s since the last line
    assert [t for t, _ in speak.said] == ["a", "b"]


def test_frequency_gate_none_interval_means_never():
    cue = Cue("station_traffic", COMMS, {"populated"}, phrasings=("a",))
    speak = _Speak()
    # min_interval returns None (unknown/unpopulated) -> nothing plays, and the LLM is never asked.
    calls = []
    player = ChatterPlayer(speak, generate=lambda p: calls.append(p) or "x",
                           min_interval=lambda: None, clock=lambda: 0.0)
    assert player(cue) is False
    assert speak.said == [] and calls == []


def test_no_min_interval_preserves_ungated_behaviour():
    cue = Cue("station_traffic", COMMS, {"populated"}, phrasings=("a", "b"))
    speak = _Speak()
    player = ChatterPlayer(speak)                  # no min_interval -> today's behaviour
    assert player(cue) and player(cue)
    assert [t for t, _ in speak.said] == ["a", "b"]
