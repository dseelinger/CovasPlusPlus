"""Unit tests for the Tier-2 AMBIENT auto-reflex framework (issue #37) — offline, free (§6, §9).

The automatic layer fires the SAME Tier-2 reflexes (#36) off Status/journal thresholds instead of
a spoken command, through the SAME combat-permissive guard + shared executor. These pin the
behaviour the design promises:

  * a threshold crossing FIRES the reflex (heat sink on Overheating, chaff on danger/interdiction);
  * a below-threshold event does NOT fire;
  * a per-reflex cooldown (and the global min-interval) SUPPRESS a rapid second fire;
  * the combat-permissive guard still BLOCKS when Status is SAFE (a trigger can't bypass it), and
    a blocked fire does NOT burn the cooldown;
  * the hard abort (`release_all()`) lifts every held key on the shared executor;
  * everything defaults OFF (master + per-reflex), and config parses the nested [reflex.auto] tables.

A recording fake executor + fake Status feed + fake clock keep it hermetic — no real presses, no
real waiting.
"""
from __future__ import annotations

from covas.keybinds.binds import KeyBinding
from covas.keybinds.executor import KeyExecutor
from covas.capabilities.auto_reflex_capability import (
    AUTO_REFLEXES,
    AutoReflexCapability,
    AutoReflexConfig,
    AutoReflexPolicy,
    ReflexSetting,
)
from covas.capabilities.reflex_capability import ALWAYS_REFUSED, COMBAT_PERMISSIVE, REFLEX_ACTIONS


# --- recording fake executor + a fake, advanceable clock -------------------

class _FakeExecutor:
    """Records the ordered presses/holds so a test can assert the exact reflex dispatch."""
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, float]] = []   # (kind, key, seconds)
        self.released_all = 0

    def press(self, binding) -> None:
        self.calls.append(("press", binding.key, 0.0))

    def hold(self, binding, seconds) -> None:
        self.calls.append(("hold", binding.key, seconds))

    def release_all(self) -> None:
        self.released_all += 1


class _Clock:
    """A monotonic clock the test advances by hand, so cooldowns are deterministic."""
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def tick(self, dt: float) -> None:
        self.t += dt


_BINDS = {
    "FireChaffLauncher": KeyBinding(action="FireChaffLauncher", key="Key_H"),
    "DeployHeatSink": KeyBinding(action="DeployHeatSink", key="Key_V"),
}

# Snapshots for the danger classes the guard/triggers read.
_COMBAT = {"in_danger": True, "being_interdicted": False, "overheating": False}
_INTERDICTION = {"in_danger": False, "being_interdicted": True, "overheating": False}
_SAFE = {"in_danger": False, "being_interdicted": False, "overheating": False}
_OVERHEAT_COMBAT = {"in_danger": True, "being_interdicted": False, "overheating": True}
_OVERHEAT_SAFE = {"in_danger": False, "being_interdicted": False, "overheating": True}


def _cfg(*, enabled=True, heat=True, chaff=True, heat_threshold=100.0,
         heat_cd=10.0, chaff_cd=8.0, min_interval=3.0, combat_guard=True) -> AutoReflexConfig:
    return AutoReflexConfig(
        enabled=enabled,
        combat_guard=combat_guard,
        min_interval=min_interval,
        reflexes={
            "heat_sink": ReflexSetting(enabled=heat, threshold=heat_threshold, cooldown=heat_cd),
            "chaff": ReflexSetting(enabled=chaff, threshold=0.0, cooldown=chaff_cd),
        },
    )


def _cap(cfg: AutoReflexConfig, *, status, clock=None, binds=None):
    ex = _FakeExecutor()
    clk = clock or _Clock()
    cap = AutoReflexCapability(
        binds=_BINDS if binds is None else binds,
        executor=ex,
        config=cfg,
        status_snapshot=(None if status is None else (lambda: status)),
        clock=clk,
        log=lambda m: None,
    )
    return cap, ex, clk


def _event(name: str) -> dict:
    return {"type": "ed_event", "event": name}


# =========================================================================
# Wiring sanity — the auto reflexes map onto the shared, guarded action set
# =========================================================================

def test_auto_reflexes_are_wired_and_combat_permissive():
    # Every automatic reflex must be a real REFLEX_ACTIONS entry AND a guard-permitted one, so the
    # combat-permissive guard actually governs it (never an ALWAYS_REFUSED name).
    for name in AUTO_REFLEXES:
        assert name in REFLEX_ACTIONS
        assert name in COMBAT_PERMISSIVE
        assert name not in ALWAYS_REFUSED


# =========================================================================
# Threshold FIRES / below-threshold does NOT
# =========================================================================

def test_heat_sink_fires_on_overheat_in_combat():
    cap, ex, _ = _cap(_cfg(), status=_OVERHEAT_COMBAT)
    cap.on_event(_event("Overheating"))
    assert ex.calls == [("press", "Key_V", 0.0)]      # pressed the heat-sink key


def test_chaff_fires_on_danger():
    cap, ex, _ = _cap(_cfg(), status=_COMBAT)
    cap.on_event(_event("EnteredDanger"))
    assert ex.calls == [("press", "Key_H", 0.0)]


def test_chaff_fires_on_interdiction():
    cap, ex, _ = _cap(_cfg(), status=_INTERDICTION)
    cap.on_event(_event("Interdicted"))
    assert ex.calls == [("press", "Key_H", 0.0)]


def test_heat_sink_below_threshold_does_not_fire():
    # Overheating event but the snapshot flag isn't set (heat hasn't actually crossed) -> no fire.
    cap, ex, _ = _cap(_cfg(), status=_COMBAT)          # overheating=False
    cap.on_event(_event("Overheating"))
    assert ex.calls == []


def test_heat_sink_threshold_above_100_disables_it():
    # ED never signals hotter than the overheat flag, so a threshold > 100 can never be met.
    cap, ex, _ = _cap(_cfg(heat_threshold=150.0), status=_OVERHEAT_COMBAT)
    cap.on_event(_event("Overheating"))
    assert ex.calls == []


def test_unrelated_event_is_ignored():
    cap, ex, _ = _cap(_cfg(), status=_COMBAT)
    cap.on_event(_event("FSDJump"))                    # not a wake event for any reflex
    assert ex.calls == []


def test_disabled_reflex_does_not_fire():
    cap, ex, _ = _cap(_cfg(chaff=False), status=_COMBAT)
    cap.on_event(_event("EnteredDanger"))
    assert ex.calls == []


def test_master_switch_off_fires_nothing():
    cap, ex, _ = _cap(_cfg(enabled=False), status=_COMBAT)
    cap.on_event(_event("EnteredDanger"))
    assert ex.calls == []


# =========================================================================
# Combat-permissive guard still blocks (a trigger can't bypass safety)
# =========================================================================

def test_guard_blocks_when_safe_even_if_triggered():
    # An Overheating event while Status is SAFE: the trigger condition needs danger too (chaff) —
    # but heat_sink's condition is met (overheating), yet the guard refuses because not in combat.
    cap, ex, _ = _cap(_cfg(), status=_OVERHEAT_SAFE)
    cap.on_event(_event("Overheating"))
    assert ex.calls == []                              # guard blocked the press


def test_guard_blocks_when_status_unavailable():
    cap, ex, _ = _cap(_cfg(), status=None)             # no telemetry -> can't confirm danger
    cap.on_event(_event("EnteredDanger"))
    assert ex.calls == []


def test_guard_off_lets_heat_sink_fire_when_safe():
    # Escape hatch: with the combat guard off, an overheat fires a heat sink even when not in
    # danger (e.g. fuel scooping). The always-refused set is unreachable here anyway.
    cap, ex, _ = _cap(_cfg(combat_guard=False), status=_OVERHEAT_SAFE)
    cap.on_event(_event("Overheating"))
    assert ex.calls == [("press", "Key_V", 0.0)]


def test_blocked_fire_does_not_arm_cooldown():
    # A guard-blocked attempt must NOT burn the cooldown, so a real danger re-trigger still fires.
    clk = _Clock()
    cap, ex, _ = _cap(_cfg(), status=_OVERHEAT_SAFE, clock=clk)
    cap.on_event(_event("Overheating"))                # blocked (safe)
    assert ex.calls == []
    # Now genuinely in danger + overheating; a second event should fire (cooldown wasn't armed).
    cap._status = lambda: _OVERHEAT_COMBAT
    cap.on_event(_event("Overheating"))
    assert ex.calls == [("press", "Key_V", 0.0)]


# =========================================================================
# Cooldown / global governor suppress rapid re-fires
# =========================================================================

def test_cooldown_suppresses_rapid_second_fire():
    clk = _Clock()
    cap, ex, _ = _cap(_cfg(chaff_cd=8.0), status=_COMBAT, clock=clk)
    cap.on_event(_event("EnteredDanger"))
    assert len(ex.calls) == 1
    clk.tick(2.0)                                      # well within the 8s cooldown
    cap.on_event(_event("EnteredDanger"))
    assert len(ex.calls) == 1                          # suppressed


def test_cooldown_allows_fire_after_it_elapses():
    clk = _Clock()
    cap, ex, _ = _cap(_cfg(chaff_cd=8.0, min_interval=0.0), status=_COMBAT, clock=clk)
    cap.on_event(_event("EnteredDanger"))
    clk.tick(9.0)                                      # past the cooldown
    cap.on_event(_event("EnteredDanger"))
    assert len(ex.calls) == 2


def test_global_min_interval_suppresses_cross_reflex_burst():
    # Two DIFFERENT reflexes in quick succession: the global governor holds the second even though
    # each has its own cooldown untouched.
    clk = _Clock()
    cap, ex, _ = _cap(_cfg(min_interval=3.0), status=_OVERHEAT_COMBAT, clock=clk)
    cap.on_event(_event("Overheating"))               # heat sink fires
    assert len(ex.calls) == 1
    clk.tick(1.0)                                      # within min_interval
    cap.on_event(_event("EnteredDanger"))             # chaff blocked by global governor
    assert len(ex.calls) == 1


# =========================================================================
# Fail-soft (unbound key) + hard abort
# =========================================================================

def test_unbound_heat_sink_key_fails_soft():
    binds = {"DeployHeatSink": KeyBinding(action="DeployHeatSink", key=None)}  # joystick-only
    cap, ex, _ = _cap(_cfg(), status=_OVERHEAT_COMBAT, binds=binds)
    cap.on_event(_event("Overheating"))
    assert ex.calls == []                              # nothing pressed, no crash


def test_missing_binding_fails_soft():
    cap, ex, _ = _cap(_cfg(), status=_OVERHEAT_COMBAT, binds={})
    cap.on_event(_event("Overheating"))
    assert ex.calls == []


def test_bad_event_never_raises():
    cap, ex, _ = _cap(_cfg(), status=_COMBAT)
    for bad in (None, {}, {"type": "log"}, {"type": "ed_event"}, {"type": "ed_event", "event": None}):
        cap.on_event(bad)                              # must not raise
    assert ex.calls == []


def test_hard_abort_lifts_held_key_on_real_executor():
    # The shared hard-abort primitive still works with the auto path's executor: a stuck key lifts.
    events: list[tuple[str, int]] = []

    class _Backend:
        def key_down(self, sc, ext): events.append(("down", sc))
        def key_up(self, sc, ext): events.append(("up", sc))

    ex = KeyExecutor(backend=_Backend(), sleep=lambda _s: None)
    ex.backend.key_down(0x2F, False)
    ex._mark(0x2F, False, down=True)                   # mark it held as hold() would
    events.clear()
    ex.release_all()
    assert ("up", 0x2F) in events


# =========================================================================
# Readiness reporting
# =========================================================================

def test_enabled_reflexes_lists_only_opted_in():
    cap, _, _ = _cap(_cfg(heat=True, chaff=False), status=_COMBAT)
    names = {t.name for t in cap.enabled_reflexes()}
    assert names == {"heat_sink"}


def test_enabled_reflexes_empty_when_master_off():
    cap, _, _ = _cap(_cfg(enabled=False), status=_COMBAT)
    assert cap.enabled_reflexes() == []


# =========================================================================
# Policy (pure) + config parsing
# =========================================================================

def test_policy_gate_reasons():
    p = AutoReflexPolicy(_cfg(chaff_cd=5.0, min_interval=2.0))
    ok, _ = p.should_fire("chaff", 100.0)
    assert ok
    p.mark_fired("chaff", 100.0)
    ok, reason = p.should_fire("chaff", 101.0)         # inside global interval
    assert not ok and "governor" in reason
    ok, reason = p.should_fire("chaff", 103.0)         # past interval, inside cooldown
    assert not ok and "cooldown" in reason
    ok, _ = p.should_fire("chaff", 106.0)              # past both
    assert ok


def test_config_defaults_off():
    d = AutoReflexConfig.from_cfg({})
    assert d.enabled is False
    assert d.combat_guard is True
    for name in AUTO_REFLEXES:
        assert d.setting(name).enabled is False


def test_config_reads_nested_tables():
    c = AutoReflexConfig.from_cfg({"reflex": {"combat_guard": False, "auto": {
        "enabled": True, "min_interval": 5.0,
        "heat_sink": {"enabled": True, "threshold": 90.0, "cooldown": 12.0},
        "chaff": {"enabled": True, "cooldown": 6.0},
    }}})
    assert c.enabled and c.combat_guard is False and c.min_interval == 5.0
    hs = c.setting("heat_sink")
    assert hs.enabled and hs.threshold == 90.0 and hs.cooldown == 12.0
    assert c.setting("chaff").enabled and c.setting("chaff").cooldown == 6.0


def test_config_missing_reflex_table_uses_trigger_defaults():
    # [reflex.auto] on but no per-reflex tables: reflexes disabled, defaults from the triggers.
    c = AutoReflexConfig.from_cfg({"reflex": {"auto": {"enabled": True}}})
    hs = c.setting("heat_sink")
    assert hs.enabled is False
    assert hs.threshold == AUTO_REFLEXES["heat_sink"].default_threshold
    assert hs.cooldown == AUTO_REFLEXES["heat_sink"].default_cooldown
    chaff = c.setting("chaff")
    assert chaff.enabled is False
    assert chaff.cooldown == AUTO_REFLEXES["chaff"].default_cooldown


def test_chaff_default_cooldown_is_20s():
    # Issue #118 — an 8s cooldown let auto-chaff re-fire while the previous burst was still
    # masking the signature, wasting limited launcher ammo. 20s ≈ one burst's effective duration
    # plus margin. Pin the trigger default so config.toml/settings_schema.py can't silently drift.
    assert AUTO_REFLEXES["chaff"].default_cooldown == 20.0


def test_config_bad_numbers_fall_back_to_defaults():
    c = AutoReflexConfig.from_cfg({"reflex": {"auto": {
        "enabled": True, "min_interval": "soon",
        "heat_sink": {"enabled": True, "threshold": "hot", "cooldown": None},
    }}})
    assert c.min_interval == 3.0
    hs = c.setting("heat_sink")
    assert hs.threshold == 100.0 and hs.cooldown == 10.0
