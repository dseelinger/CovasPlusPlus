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


def test_interdiction_all_failed_emit_does_not_burn_governor_or_rotation():
    """If EVERY layer's emit() fails (a transient TTS/SFX outage), the cue must NOT arm the 45s
    cooldown or advance its rotation — the next tick gets a fresh shot at the same lines. Issue
    #160: previously a total failure still burned the budget and skipped a rotation position."""
    clock = [0.0]
    gov = CueGovernor(GovernorConfig(enabled=True, min_interval=0.0), clock=lambda: clock[0])
    dead = _Emit(ok=False)                                  # every layer fails to play
    cue = InterdictionCue(dead, governor=gov, cooldown_s=45.0, clock=lambda: clock[0])
    assert cue.on_event({"event": "Interdiction"}) == []   # nothing emitted
    # A moment later (well inside the 45s cooldown) a working emit MUST get through — the failed
    # attempt didn't arm the governor.
    clock[0] = 1.0
    live = _Emit(ok=True)
    cue._emit = live                                        # noqa: SLF001 — swap in a working sink
    out = cue.on_event({"event": "UnderAttack"})
    assert len(out) == 3
    # And the rotation didn't advance during the failure: the first line played is line 0.
    from covas.mixer import DEFAULT_THREAT_LINES
    assert live.layers[1].payload == DEFAULT_THREAT_LINES[0]


def test_interdiction_partial_emit_still_advances():
    """At least one layer playing counts as a real moment: rotation advances and the governor arms
    (contrast the all-failed case)."""
    clock = [0.0]
    gov = CueGovernor(GovernorConfig(enabled=True, min_interval=0.0), clock=lambda: clock[0])

    class _OnlyFirst:
        def __init__(self):
            self.n = 0
        def __call__(self, layer):
            self.n += 1
            return self.n == 1                              # only the first layer plays

    cue = InterdictionCue(_OnlyFirst(), governor=gov, cooldown_s=45.0, clock=lambda: clock[0])
    assert len(cue.on_event({"event": "Interdiction"})) == 1
    clock[0] = 10.0
    # governor armed -> a follow-up inside the cooldown is suppressed
    assert cue.on_event({"event": "UnderAttack"}) == []


def test_interdiction_layers_survives_sting_samples_emptied_mid_call():
    """layers() must snapshot _sting_samples once: a concurrent set_content() reload emptying it
    right after the truthiness check must not raise ZeroDivisionError (issue #160). Reproduces the
    race deterministically by emptying the live field on the truthiness read — the fix survives it
    (it indexes the local snapshot); the pre-fix re-read of self._sting_samples would divide by 0."""
    cue = InterdictionCue(_Emit(), sting_samples=("s1.wav", "s2.wav"))

    class _Evil(tuple):
        """Truthy, but the truthiness check triggers a set_content() reload that empties the cue's
        live field — modelling a concurrent reload landing between the check and the index."""
        _fired = False
        def __bool__(self):
            if not _Evil._fired:
                _Evil._fired = True
                cue.set_content(sting_samples=())          # empties self._sting_samples mid-call
            return True

    cue._sting_samples = _Evil(("s1.wav", "s2.wav"))       # noqa: SLF001
    layers = cue.layers()                                  # must not raise ZeroDivisionError
    assert layers[0].payload in ("s1.wav", "s2.wav")       # used the snapshot, not the emptied field
    assert cue._sting_samples == ()                        # noqa: SLF001 — the reload did land


def test_interdiction_layers_empty_samples_uses_default_sting():
    """With no sting samples, layers() falls back to the single default sting (no index into empty)."""
    from covas.mixer.example_cues import DEFAULT_STING
    cue = InterdictionCue(_Emit(), sting_samples=())
    assert cue.layers()[0].payload == DEFAULT_STING


def test_interdiction_sting_falls_back_to_builtin_when_blank():
    from covas.mixer.example_cues import DEFAULT_STING
    emit = _Emit()
    InterdictionCue(emit, sting="").on_event({"event": "Interdiction"})
    assert emit.layers[0].payload == DEFAULT_STING


def test_interdiction_from_cfg_reads_sting_and_enabled():
    emit = _Emit()
    cfg = {"audio": {"interdiction": {"enabled": True, "sting": "sounds/my_sting.wav"}}}
    InterdictionCue.from_cfg(cfg, emit).on_event({"event": "Interdiction"})
    assert emit.layers[0].payload == "sounds/my_sting.wav"


def test_interdiction_from_cfg_disabled_by_default():
    emit = _Emit()
    # No enabled key -> the cue is off (the C9 dead-flag fix), so nothing fires.
    InterdictionCue.from_cfg({"audio": {"interdiction": {"sting": "s.wav"}}}, emit).on_event(
        {"event": "Interdiction"})
    assert emit.layers == []


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
