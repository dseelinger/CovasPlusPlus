"""Unit tests for C8 worked-example cues — layered interdiction + ambient SFX. Offline."""
from __future__ import annotations

from covas.mixer import (
    ALERT,
    AMBIENT,
    COMMS,
    COVAS,
    Cue,
    CueGovernor,
    CueRegistry,
    GovernorConfig,
    InterdictionCue,
    SfxPlayer,
    register_sfx,
    sfx_cues,
)


class _Emit:
    def __init__(self, ok=True):
        self.ok = ok
        self.layers = []

    def __call__(self, layer):
        self.layers.append(layer)
        return self.ok


# ---- the layered interdiction cue ----------------------------------------------------------

def test_interdiction_emits_three_layers_in_order():
    emit = _Emit()
    cue = InterdictionCue(emit)
    out = cue.on_event({"event": "Interdiction", "IsPlayer": False})
    assert len(out) == 3
    assert [layer.bus for layer in emit.layers] == [ALERT, COVAS, COMMS]
    assert [layer.kind for layer in emit.layers] == ["sfx", "line", "line"]
    assert emit.layers[2].voice == "male"          # pirate line on the comms bus, voiced


def test_interdiction_fires_on_underattack_too_and_ignores_others():
    emit = _Emit()
    cue = InterdictionCue(emit)
    assert cue.on_event({"event": "UnderAttack"})
    assert cue.on_event({"event": "FSDJump"}) == []
    assert cue.on_event("garbage") == []


def test_interdiction_pool_rotation_is_deterministic():
    emit = _Emit()
    cue = InterdictionCue(emit)
    cue.on_event({"event": "Interdiction"})
    cue.on_event({"event": "Interdiction"})
    threat_lines = [layer.payload for layer in emit.layers if layer.bus == COVAS]
    pirate_lines = [layer.payload for layer in emit.layers if layer.bus == COMMS]
    from covas.mixer import DEFAULT_PIRATE_LINES, DEFAULT_THREAT_LINES
    assert threat_lines == [DEFAULT_THREAT_LINES[0], DEFAULT_THREAT_LINES[1]]
    assert pirate_lines == [DEFAULT_PIRATE_LINES[0], DEFAULT_PIRATE_LINES[1]]


def test_interdiction_is_governed_by_cooldown():
    clock = [0.0]
    gov = CueGovernor(GovernorConfig(enabled=True, min_interval=0.0), clock=lambda: clock[0])
    emit = _Emit()
    cue = InterdictionCue(emit, governor=gov, cooldown_s=45.0, clock=lambda: clock[0])
    assert len(cue.on_event({"event": "Interdiction"})) == 3
    clock[0] = 10.0
    assert cue.on_event({"event": "UnderAttack"}) == []     # within the 45s cooldown -> suppressed
    clock[0] = 50.0
    assert len(cue.on_event({"event": "Interdiction"})) == 3


def test_interdiction_sting_falls_back_to_builtin_when_blank():
    from covas.mixer.example_cues import DEFAULT_STING
    emit = _Emit()
    InterdictionCue(emit, sting="").on_event({"event": "Interdiction"})
    assert emit.layers[0].payload == DEFAULT_STING


def test_interdiction_from_cfg_reads_sting():
    emit = _Emit()
    cfg = {"audio": {"interdiction": {"sting": "sounds/my_sting.wav"}}}
    InterdictionCue.from_cfg(cfg, emit).on_event({"event": "Interdiction"})
    assert emit.layers[0].payload == "sounds/my_sting.wav"


# ---- ambient SFX cues ----------------------------------------------------------------------

def test_sfx_cues_register_cleanly_and_use_real_states():
    from covas.mixer.eligibility import STATES, unknown_states
    reg = CueRegistry()
    register_sfx(reg, {"audio": {"sfx": {"thargoid_voices": ["sfx/t1.wav", "sfx/t2.wav"]}}})
    assert reg.contract_violations() == []
    for cue in sfx_cues():
        assert unknown_states(cue.eligible_states) == set()
        assert cue.eligible_states <= STATES


def test_sfx_eligibility_gating():
    reg = CueRegistry(sfx_cues({"audio": {"sfx": {"thargoid_voices": ["a.wav"]}}}))
    in_jump = {c.name for c in reg.eligible({"hyperspace"})}
    assert {"thargoid_voices", "hyperspace_weirdness"} <= in_jump
    assert "space_radiation" not in in_jump
    deep = {c.name for c in reg.eligible({"deep_space"})}
    assert deep == {"space_radiation"}
    assert reg.eligible({"docked"}) == []          # none of these play while docked


def test_sfx_player_rotates_samples_deterministically():
    cue = Cue("thargoid_voices", AMBIENT, {"hyperspace"}, samples=("t1.wav", "t2.wav", "t3.wav"))
    played = []
    player = SfxPlayer(lambda sample, bus: played.append((sample, bus)) or True)
    for _ in range(4):
        player(cue)
    assert played == [("t1.wav", AMBIENT), ("t2.wav", AMBIENT),
                      ("t3.wav", AMBIENT), ("t1.wav", AMBIENT)]


def test_sfx_player_empty_samples_is_silent():
    cue = Cue("empty", AMBIENT, {"hyperspace"}, samples=())
    player = SfxPlayer(lambda s, b: True)
    assert player(cue) is False


def test_sfx_player_failed_play_does_not_advance():
    cue = Cue("thargoid_voices", AMBIENT, {"hyperspace"}, samples=("t1.wav", "t2.wav"))
    played = []
    player = SfxPlayer(lambda sample, bus: played.append(sample) or False)   # never starts
    player(cue)
    player(cue)
    assert played == ["t1.wav", "t1.wav"]          # rotation didn't advance
