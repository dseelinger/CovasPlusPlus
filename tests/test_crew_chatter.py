"""Unit tests for ambient CREW chatter + crew addressing (issue #126).

All pure/offline: the CrewChatterPlayer takes injected seams (roster / speak_crew / generate /
context / min_interval / clock), so no audio device, no LLM, no network. Mirrors the fake-injection
style of test_space_chatter.py.
"""
from __future__ import annotations

from covas import crew
from covas.crew import CrewMember
from covas.mixer import (
    COMMS,
    CREW,
    CrewChatterPlayer,
    CueRegistry,
    build_crew_chatter_prompt,
    chatter_cues,
    crew_chatter_cue,
)
from covas.mixer.chatter import _CREW_CHATTER_PREFIX
from covas.mixer.eligibility import IN_SHIP, STATES, unknown_states


class _SpeakCrew:
    """Records (name, text) fire-and-forget calls; `ok` toggles success."""

    def __init__(self, ok=True):
        self.ok = ok
        self.said: list[tuple[str, str]] = []

    def __call__(self, name, text):
        self.said.append((name, text))
        return self.ok


def _roster(*members):
    """A stable roster callable over CrewMembers built from (name, role, persona) triples."""
    crew_members = [CrewMember(name=n, role=r, persona=p) for n, r, p in members]
    return lambda: list(crew_members)


# ---- the crew_chatter cue: contract-clean, in-ship (NOT population) gated -------------------

def test_crew_chatter_cue_is_contract_clean_alongside_chatter():
    reg = CueRegistry(list(chatter_cues()) + [crew_chatter_cue()])
    assert reg.contract_violations() == []
    cue = crew_chatter_cue()
    assert cue.voice_role == CREW
    assert cue.bus == COMMS
    assert cue.fact_bearing is False
    assert cue.phrasings == ()                      # LLM-or-nothing: no curated pool


def test_crew_chatter_eligible_states_are_real_and_in_ship():
    cue = crew_chatter_cue()
    assert unknown_states(cue.eligible_states) == set()
    assert cue.eligible_states <= STATES
    assert cue.eligible_states == frozenset({IN_SHIP})


def test_crew_chatter_is_gated_on_in_ship_not_population():
    reg = CueRegistry([crew_chatter_cue()])
    # In the ship, it's eligible whatever the population — crew are aboard.
    assert [c.name for c in reg.eligible({IN_SHIP})] == ["crew_chatter"]
    assert [c.name for c in reg.eligible({IN_SHIP, "unpopulated", "deep_space"})] == ["crew_chatter"]
    # Population WITHOUT being in the ship does NOT make it eligible (unlike station chatter).
    assert reg.eligible({"populated"}) == []
    assert reg.eligible({"on_foot"}) == []


# ---- the prompt: byte-stable prefix + role/persona/situation --------------------------------

def test_crew_chatter_prompt_prefix_is_byte_stable_and_carries_role_persona_situation():
    m = CrewMember(name="Nyx", role="Sensor officer", persona="terse and dry")
    prompt = build_crew_chatter_prompt(m, context="just yanked out of the sky by an interdiction")
    assert prompt.startswith(_CREW_CHATTER_PREFIX)   # cacheable head first, byte-stable
    assert "Nyx" in prompt
    assert "Sensor officer" in prompt
    assert "terse and dry" in prompt
    assert "interdiction" in prompt                  # the live situation slice is woven in
    # The prefix itself never changes with the member/situation (prompt-cache guarantee).
    other = build_crew_chatter_prompt(CrewMember(name="Vela", role="Cook"), context="docked")
    assert other.startswith(_CREW_CHATTER_PREFIX)


def test_crew_chatter_prompt_tolerates_role_and_persona_absent():
    prompt = build_crew_chatter_prompt(CrewMember(name="Nyx"))
    assert prompt.startswith(_CREW_CHATTER_PREFIX) and "Nyx" in prompt


# ---- speaker rotation: deterministic, stable order -----------------------------------------

def test_speaker_rotation_is_deterministic_over_the_roster():
    speak = _SpeakCrew()
    # Genuinely distinct lines (low token overlap) so the near-repeat guard doesn't suppress any
    # (that's covered separately) — this test isolates the SPEAKER rotation.
    lines = iter([
        "the dark presses close tonight",
        "warm mug, cold void, familiar hum",
        "she rides steady under my hands",
        "another watch, another empty scope",
    ])
    player = CrewChatterPlayer(
        _roster(("Nyx", "Sensors", ""), ("Vela", "Cook", ""), ("Rho", "Gunner", "")),
        speak, generate=lambda p: next(lines))
    for _ in range(4):
        player(crew_chatter_cue())
    assert [n for n, _ in speak.said] == ["Nyx", "Vela", "Rho", "Nyx"]


def test_empty_roster_speaks_nothing():
    speak = _SpeakCrew()
    player = CrewChatterPlayer(lambda: [], speak, generate=lambda p: "a line")
    assert player(crew_chatter_cue()) is False
    assert speak.said == []


def test_roster_read_error_is_fail_soft_no_line():
    speak = _SpeakCrew()
    player = CrewChatterPlayer(lambda: (_ for _ in ()).throw(RuntimeError("x")),
                               speak, generate=lambda p: "a line")
    assert player(crew_chatter_cue()) is False
    assert speak.said == []


# ---- honesty + hygiene: validated routed, rejected -> silence (no pool) ---------------------

def test_safe_flavor_line_is_routed_to_the_members_voice():
    speak = _SpeakCrew()
    player = CrewChatterPlayer(_roster(("Nyx", "Sensors", "")), speak,
                               generate=lambda p: "the dark presses close tonight")
    assert player(crew_chatter_cue()) is True
    assert speak.said == [("Nyx", "the dark presses close tonight")]


def test_unsafe_flavor_line_is_rejected_to_silence_no_pool():
    speak = _SpeakCrew()
    # A number / proper noun makes the line checkable -> rejected -> SILENCE (no fallback pool).
    player = CrewChatterPlayer(_roster(("Nyx", "Sensors", "")), speak,
                               generate=lambda p: "3 contacts near Sol")
    assert player(crew_chatter_cue()) is False
    assert speak.said == []


def test_generator_failure_is_silence():
    speak = _SpeakCrew()
    player = CrewChatterPlayer(_roster(("Nyx", "Sensors", "")), speak,
                               generate=lambda p: (_ for _ in ()).throw(RuntimeError("boom")))
    assert player(crew_chatter_cue()) is False
    assert speak.said == []


def test_no_generator_is_silence():
    speak = _SpeakCrew()
    player = CrewChatterPlayer(_roster(("Nyx", "Sensors", "")), speak, generate=None)
    assert player(crew_chatter_cue()) is False
    assert speak.said == []


def test_near_repeat_line_is_rejected_to_silence():
    speak = _SpeakCrew()
    lines = iter(["the dark presses close tonight", "the dark presses close tonight again"])
    player = CrewChatterPlayer(_roster(("Nyx", "Sensors", ""), ("Vela", "Cook", "")),
                               speak, generate=lambda p: next(lines))
    assert player(crew_chatter_cue()) is True            # first line spoken + remembered
    assert player(crew_chatter_cue()) is False           # near-repeat -> silence
    assert speak.said == [("Nyx", "the dark presses close tonight")]


# ---- pacing: CREW interval gate (NOT population-scaled) -------------------------------------

def test_frequency_gate_suppresses_until_interval_elapses():
    speak = _SpeakCrew()
    now = {"t": 0.0}
    lines = iter(["the dark presses close tonight", "warm mug, cold void, familiar hum"])
    player = CrewChatterPlayer(_roster(("Nyx", "Sensors", "")), speak,
                               generate=lambda p: next(lines), min_interval=lambda: 300.0,
                               clock=lambda: now["t"])
    assert player(crew_chatter_cue()) is True            # first line: nothing spoken yet
    now["t"] = 100.0
    assert player(crew_chatter_cue()) is False           # 100s < 300s -> suppressed
    now["t"] = 320.0
    assert player(crew_chatter_cue()) is True            # 320s >= 300s
    assert len(speak.said) == 2


def test_none_interval_skips_and_never_calls_the_generator():
    speak = _SpeakCrew()
    calls = []
    player = CrewChatterPlayer(_roster(("Nyx", "Sensors", "")), speak,
                               generate=lambda p: calls.append(p) or "x",
                               min_interval=lambda: None)
    assert player(crew_chatter_cue()) is False
    assert speak.said == [] and calls == []


def test_rejected_line_still_advances_throttle_so_generator_is_not_re_hit_immediately():
    speak = _SpeakCrew()
    calls = []
    now = {"t": 0.0}

    def gen(p):
        calls.append(p)
        return "42 ships"                                 # always unsafe -> rejected

    player = CrewChatterPlayer(_roster(("Nyx", "Sensors", "")), speak, generate=gen,
                               min_interval=lambda: 300.0, clock=lambda: now["t"])
    assert player(crew_chatter_cue()) is False            # attempted (1 generator call), rejected
    assert player(crew_chatter_cue()) is False            # too soon -> NOT re-generated
    assert len(calls) == 1                                # throttle held despite the rejection


# ============================================================================================
# Part B — addressing clause in the crew system instruction (prompt-level only)
# ============================================================================================

def _on(**crew_extra) -> dict:
    """Crew-ON config: gated behind [crew].enabled AND [experimental.crew] (issue #123)."""
    return {"crew": {"enabled": True, **crew_extra},
            "experimental": {"crew": {"enabled": True}}}


def test_addressing_clause_present_only_when_crew_enabled():
    assert crew.system_instruction({"crew": {"enabled": False}}) is None
    assert crew.system_instruction({"crew": {"enabled": True}}) is None   # flag off -> still None (#123)
    inst = crew.system_instruction(_on())
    assert inst is not None
    assert "addresses a crew member by name" in inst
    assert "sound off" in inst                            # the everyone/roll-call case


def test_addressing_instruction_is_static_for_a_fixed_roster():
    cfg = _on(roster=["Nyx", "Vela"])
    a = crew.system_instruction(cfg)
    b = crew.system_instruction(cfg)
    assert a == b                                         # byte-identical -> prompt-cache safe


def test_addressed_multi_member_reply_voices_each_name_segment():
    # Part B needs NO parser change: a reply with two [Name] lines already splits + routes each to
    # crew_speak. This asserts the existing segment machinery voices both members in order.
    reply = "[Nyx] Contacts, clear.\n[Vela] Galley's a mess."
    segs = crew.parse_segments(reply, enabled=True)
    spoken: list[tuple[str, str]] = []
    crew.speak_segments(
        segs,
        persona_speak=lambda t: spoken.append(("persona", t)),
        crew_speak=lambda n, t: (spoken.append((n, t)) or True),
        cancel=_NeverCancel(),
    )
    assert spoken == [("Nyx", "Contacts, clear."), ("Vela", "Galley's a mess.")]


class _NeverCancel:
    def is_set(self):
        return False
