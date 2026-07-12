"""Unit tests for the C3 governor — cooldowns, global rate cap, deterministic rotation. Offline."""
from __future__ import annotations

from covas.mixer import COMMS, Cue, CueGovernor, GovernorConfig


def _cue(name: str, cooldown_s: float = 0.0) -> Cue:
    return Cue(name, COMMS, {"docked"}, cooldown_s=cooldown_s)


def _gov(**kw) -> CueGovernor:
    base = dict(enabled=True, min_interval=0.0, max_per_minute=1000, default_cooldown=0.0)
    base.update(kw)
    return CueGovernor(GovernorConfig(**base))


def test_disabled_blocks_everything():
    gov = CueGovernor(GovernorConfig(enabled=False))
    ok, reason = gov.allow(_cue("a"), 0.0)
    assert not ok and "disabled" in reason


def test_global_min_interval_blocks_second_cue():
    gov = _gov(min_interval=8.0)
    a, b = _cue("a"), _cue("b")
    assert gov.allow(a, 0.0)[0]
    gov.mark_fired(a, 0.0)
    assert not gov.allow(b, 5.0)[0]        # within the 8 s global gap
    assert gov.allow(b, 8.0)[0]            # gap elapsed


def test_per_cue_cooldown():
    gov = _gov(default_cooldown=10.0)
    a, b = _cue("a"), _cue("b")
    gov.mark_fired(a, 0.0)
    assert not gov.allow(a, 5.0)[0]        # a still cooling down
    assert gov.allow(b, 5.0)[0]            # a different cue is fine
    assert gov.allow(a, 10.0)[0]           # cooldown elapsed


def test_cue_declares_its_own_cooldown():
    gov = _gov(default_cooldown=5.0)
    slow = _cue("slow", cooldown_s=100.0)
    gov.mark_fired(slow, 0.0)
    assert not gov.allow(slow, 50.0)[0]    # its own 100 s cooldown, not the 5 s default


def test_global_rate_cap_over_rolling_window():
    gov = _gov(max_per_minute=2)
    a, b, c = _cue("a"), _cue("b"), _cue("c")
    gov.mark_fired(a, 0.0)
    gov.mark_fired(b, 1.0)
    assert not gov.allow(c, 2.0)[0]        # third within 60 s -> capped
    assert "rate cap" in gov.allow(c, 2.0)[1]
    assert gov.allow(c, 61.0)[0]           # a's timestamp slid out of the window


def test_select_is_deterministic_rotation():
    gov = _gov(min_interval=1.0, default_cooldown=5.0)
    a, b = _cue("a"), _cue("b")
    first = gov.select([b, a], 0.0)        # order of the list shouldn't matter (sorted by name)
    assert first is a
    gov.mark_fired(first, 0.0)
    second = gov.select([a, b], 2.0)       # rotor advanced -> starts at b, and a is cooling down
    assert second is b
    gov.mark_fired(second, 2.0)
    assert gov.select([a, b], 3.0) is None  # both cooling down -> nothing


def test_select_empty_returns_none():
    assert _gov().select([], 0.0) is None
