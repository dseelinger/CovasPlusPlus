"""Unit tests for the C3 driver — state -> eligible -> governed -> played. Offline, no device."""
from __future__ import annotations

from covas.ed.status import FLAGS
from covas.mixer import (
    COMMS,
    MUSIC,
    Cue,
    CueDriver,
    CueGovernor,
    CueRegistry,
    EligibilityEngine,
    GovernorConfig,
)


class _Clock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class _Play:
    """Records cues handed to it; `ok` controls whether it reports a successful start."""

    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.played: list[str] = []

    def __call__(self, cue: Cue) -> bool:
        self.played.append(cue.name)
        return self.ok


def _driver(cues, *, enabled=True, ok=True, context=None, clock=None):
    reg = CueRegistry(cues)
    eng = EligibilityEngine()
    gov = CueGovernor(GovernorConfig(enabled=enabled, min_interval=8.0, default_cooldown=30.0),
                      clock=clock or (lambda: 0.0))
    play = _Play(ok=ok)
    drv = CueDriver(reg, eng, gov, play, context=context, clock=clock or (lambda: 0.0))
    return drv, play, eng


def _flags(*names: str) -> int:
    v = 0
    for n in names:
        v |= FLAGS[n]
    return v


def test_disabled_driver_plays_nothing():
    drv, play, eng = _driver([Cue("dock", COMMS, {"docked"})], enabled=False)
    eng.note_flags(_flags("Docked"))
    assert drv.tick() is None
    assert play.played == []


def test_eligible_cue_plays_and_arms_cooldown():
    clock = _Clock()
    drv, play, eng = _driver([Cue("dock", COMMS, {"docked"})], clock=clock)
    eng.note_flags(_flags("Docked", "InMainShip"))
    cue = drv.tick()
    assert cue is not None and cue.name == "dock"
    assert play.played == ["dock"]
    # Immediately again: same cue is inside its cooldown AND the global interval -> nothing.
    assert drv.tick() is None
    assert play.played == ["dock"]


def test_no_eligible_cue_for_current_state():
    drv, play, eng = _driver([Cue("deep", MUSIC, {"deep_space"})])
    eng.note_flags(_flags("Docked"))          # docked, not deep space
    assert drv.tick() is None
    assert play.played == []


def test_on_event_ignores_non_ed_events():
    drv, play, eng = _driver([Cue("dock", COMMS, {"docked"})])
    drv.on_event({"type": "log", "msg": "hello"})
    assert play.played == []


def test_on_event_folds_journal_population_and_plays():
    drv, play, _eng = _driver([Cue("in_pop", COMMS, {"populated"})])
    drv.on_event({"type": "ed_event", "event": "FSDJump", "Population": 4200,
                  "flags": _flags("InMainShip", "Supercruise")})
    assert play.played == ["in_pop"]


def test_failed_play_does_not_arm_cooldown():
    clock = _Clock()
    drv, play, eng = _driver([Cue("dock", COMMS, {"docked"})], ok=False, clock=clock)
    eng.note_flags(_flags("Docked"))
    assert drv.tick() is None                 # play reported it didn't start
    assert play.played == ["dock"]
    clock.advance(10.0)                        # past the global interval
    assert drv.tick() is None                 # tried again (cooldown was NOT armed)
    assert play.played == ["dock", "dock"]


def test_context_fuel_pct_drives_fuel_cue():
    class _Ctx:
        def fuel_pct(self):
            return 6.0                         # critical

    drv, play, eng = _driver([Cue("bingo", COMMS, {"fuel_critical"})], context=_Ctx())
    eng.note_flags(_flags("InMainShip"))       # no fuel flag set; fuel comes from context
    cue = drv.tick()
    assert cue is not None and cue.name == "bingo"
