"""Unit tests for auto-honk (N5) — offline, free (DESIGN §6, §9).

Covers the pure fire-group cycle math, the arrival-triggered honk sequence (asserting the
exact key sequence + hold duration via a recording fake executor), the combat/interdiction
guard, the disabled/no-op path, and the unbound-key and unknown-fire-group fail-soft paths.
No journal, no audio, no real key injection.
"""
from __future__ import annotations

from covas.keybinds.binds import KeyBinding
from covas.capabilities.honk_capability import (CYCLE_NEXT, CYCLE_PREV, HonkCapability,
                                                HonkConfig, cycle_plan)


# --- recording fake executor -----------------------------------------------

class _FakeExecutor:
    """Records the ordered sequence of presses/holds so a test can assert the exact honk."""
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, float]] = []   # (kind, key, seconds)
        self.released_all = 0

    def press(self, binding) -> None:
        self.calls.append(("press", binding.key, 0.0))

    def hold(self, binding, seconds) -> None:
        self.calls.append(("hold", binding.key, seconds))

    def release_all(self) -> None:
        self.released_all += 1


# ED bindings the honk sequence uses, mapped to distinct keys so the sequence is legible.
_BINDS = {
    "PrimaryFire": KeyBinding(action="PrimaryFire", key="Key_1"),
    "SecondaryFire": KeyBinding(action="SecondaryFire", key="Key_2"),
    "CycleFireGroupNext": KeyBinding(action="CycleFireGroupNext", key="Key_N"),
    "CycleFireGroupPrevious": KeyBinding(action="CycleFireGroupPrevious", key="Key_B"),
}
_SAFE = {"in_danger": False, "being_interdicted": False, "supercruise": True, "fire_group": 0}


def _cap(*, cfg=None, binds=None, status=_SAFE):
    ex = _FakeExecutor()
    cap = HonkCapability(
        cfg or HonkConfig(enabled=True),
        binds=_BINDS if binds is None else binds,
        executor=ex,
        status_snapshot=(lambda: status),
        spawn=lambda fn: fn(),            # synchronous: run the sequence inline for the test
        log=lambda m: None)
    return cap, ex


def _jump(cap):
    cap.on_event({"type": "ed_event", "event": "FSDJump", "StarSystem": "Sol"})


# --- cycle_plan (pure) -----------------------------------------------------

def test_cycle_plan_forward():
    assert cycle_plan(0, 3) == (CYCLE_NEXT, 3)


def test_cycle_plan_backward():
    assert cycle_plan(4, 1) == (CYCLE_PREV, 3)


def test_cycle_plan_no_move():
    assert cycle_plan(2, 2) == ("", 0)


# --- configured honk: cycle -> hold -> cycle back --------------------------

def test_honk_cycles_holds_and_cycles_back():
    cfg = HonkConfig(enabled=True, fire_group=2, trigger="primary", hold_seconds=6.0)
    cap, ex = _cap(cfg=cfg, status={**_SAFE, "fire_group": 0})
    _jump(cap)
    assert ex.calls == [
        ("press", "Key_N", 0.0),          # cycle next 0 -> 1
        ("press", "Key_N", 0.0),          # cycle next 1 -> 2 (scanner group)
        ("hold", "Key_1", 6.0),           # hold primary fire for the honk
        ("press", "Key_B", 0.0),          # cycle back 2 -> 1
        ("press", "Key_B", 0.0),          # cycle back 1 -> 0 (restore original)
    ]


def test_honk_already_on_scanner_group_skips_cycling():
    cfg = HonkConfig(enabled=True, fire_group=1, hold_seconds=5.0)
    cap, ex = _cap(cfg=cfg, status={**_SAFE, "fire_group": 1})
    _jump(cap)
    assert ex.calls == [("hold", "Key_1", 5.0)]     # no cycle when already there


def test_honk_backward_cycle_when_current_above_target():
    cfg = HonkConfig(enabled=True, fire_group=1, hold_seconds=4.0)
    cap, ex = _cap(cfg=cfg, status={**_SAFE, "fire_group": 3})
    _jump(cap)
    assert ex.calls == [
        ("press", "Key_B", 0.0), ("press", "Key_B", 0.0),   # prev 3 -> 1
        ("hold", "Key_1", 4.0),
        ("press", "Key_N", 0.0), ("press", "Key_N", 0.0),   # next 1 -> 3 (restore)
    ]


def test_secondary_trigger_holds_secondary_fire():
    cfg = HonkConfig(enabled=True, fire_group=0, trigger="secondary", hold_seconds=6.0)
    cap, ex = _cap(cfg=cfg, status={**_SAFE, "fire_group": 0})
    _jump(cap)
    assert ex.calls == [("hold", "Key_2", 6.0)]


# --- fallback (no fire group configured) -----------------------------------

def test_fallback_holds_primary_without_cycling():
    cfg = HonkConfig(enabled=True, fire_group=-1, hold_seconds=6.0, allow_unmapped_fire=True)
    # even with a known current group, an unconfigured target means "just hold primary fire"
    cap, ex = _cap(cfg=cfg, status={**_SAFE, "fire_group": 2})
    _jump(cap)
    assert ex.calls == [("hold", "Key_1", 6.0)]


def test_unconfigured_is_inert_by_default():
    # On by default but no scanner fire group mapped -> must NOT fire (no blind hold), even
    # with the fire button bound and a safe status. allow_unmapped_fire defaults False.
    cfg = HonkConfig(enabled=True, fire_group=-1)
    cap, ex = _cap(cfg=cfg, status={**_SAFE, "fire_group": 2})
    _jump(cap)
    assert ex.calls == []


def test_no_status_means_no_honk_even_with_guard_off():
    # Without live ED status we can't confirm supercruise, so we don't fire — even with the
    # combat guard off and the blind fallback opted in.
    cfg = HonkConfig(enabled=True, fire_group=-1, combat_guard=False, hold_seconds=6.0, allow_unmapped_fire=True)
    ex = _FakeExecutor()
    cap = HonkCapability(cfg, binds=_BINDS, executor=ex,
                         status_snapshot=None, spawn=lambda fn: fn())
    _jump(cap)
    assert ex.calls == []


def test_only_honks_in_supercruise():
    # Configured + safe, but NOT in supercruise (dropped to normal space) -> no fire, so it
    # can't send you into the Surface Scanner.
    cfg = HonkConfig(enabled=True, fire_group=0)
    cap, ex = _cap(cfg=cfg, status={**_SAFE, "supercruise": False})
    _jump(cap)
    assert ex.calls == []


# --- combat / interdiction guard -------------------------------------------

def test_guard_suppresses_during_danger():
    cap, ex = _cap(status={"in_danger": True, "being_interdicted": False, "fire_group": 0})
    _jump(cap)
    assert ex.calls == []


def test_guard_suppresses_during_interdiction():
    cap, ex = _cap(status={"in_danger": False, "being_interdicted": True, "fire_group": 0})
    _jump(cap)
    assert ex.calls == []


def test_guard_suppresses_when_status_unknown():
    ex = _FakeExecutor()
    cap = HonkCapability(HonkConfig(enabled=True, fire_group=0),
                         binds=_BINDS, executor=ex,
                         status_snapshot=None, spawn=lambda fn: fn())
    _jump(cap)
    assert ex.calls == []                 # can't prove it's safe -> no honk


def test_guard_can_be_disabled_for_configured_honk_with_status():
    cfg = HonkConfig(enabled=True, fire_group=0, combat_guard=False)
    cap, ex = _cap(cfg=cfg, status={"in_danger": True, "supercruise": True, "fire_group": 0})
    _jump(cap)
    assert ex.calls == [("hold", "Key_1", 6.0)]   # guard off -> honks despite danger


# --- fail-soft paths -------------------------------------------------------

def test_unknown_fire_group_does_not_fire():
    # configured to cycle, but the current group is unreadable -> must NOT fire (could hold
    # fire in the wrong group and shoot weapons)
    cfg = HonkConfig(enabled=True, fire_group=2)
    cap, ex = _cap(cfg=cfg, status={**_SAFE, "fire_group": None})
    _jump(cap)
    assert ex.calls == []


def test_unbound_fire_key_skips():
    binds = dict(_BINDS)
    binds["PrimaryFire"] = KeyBinding(action="PrimaryFire", key=None)   # joystick-only
    cap, ex = _cap(cfg=HonkConfig(enabled=True, fire_group=0), binds=binds)
    _jump(cap)
    assert ex.calls == []


def test_missing_cycle_binding_does_not_fire():
    binds = dict(_BINDS)
    del binds["CycleFireGroupNext"]        # can't reach a higher group
    cap, ex = _cap(cfg=HonkConfig(enabled=True, fire_group=2), binds=binds,
                   status={**_SAFE, "fire_group": 0})
    _jump(cap)
    assert ex.calls == []                  # refuse rather than fire in the wrong group


# --- event gating ----------------------------------------------------------

def test_non_arrival_events_ignored():
    cap, ex = _cap()
    cap.on_event({"type": "ed_event", "event": "Docked"})
    cap.on_event({"type": "ed_event", "event": "SupercruiseExit"})
    cap.on_event({"type": "log", "who": "system", "text": "hi"})
    assert ex.calls == []


def test_ambient_no_tools():
    cap, _ = _cap()
    assert cap.tools() == []
    assert not hasattr(cap, "help_meta")   # ambient: not advertised to the model


def test_bad_event_does_not_raise():
    cap, ex = _cap()
    cap.on_event("not a dict")             # type: ignore[arg-type]
    cap.on_event({})
    assert ex.calls == []


# --- config parsing --------------------------------------------------------

def test_config_from_cfg_defaults():
    d = HonkConfig.from_cfg({})
    assert d.enabled is False and d.fire_group == -1 and d.trigger == "primary"
    assert d.hold_seconds == 6.0 and d.combat_guard is True and d.configured is False


def test_config_from_cfg_reads_and_normalizes():
    c = HonkConfig.from_cfg({"honk": {"enabled": True, "fire_group": 3,
                                      "trigger": "SECONDARY", "hold_seconds": 4.5,
                                      "combat_guard": False}})
    assert c.enabled and c.fire_group == 3 and c.trigger == "secondary"
    assert c.hold_seconds == 4.5 and c.combat_guard is False and c.configured is True
    assert c.fire_action == "SecondaryFire"


def test_config_bad_values_fall_back():
    c = HonkConfig.from_cfg({"honk": {"fire_group": "x", "hold_seconds": "y"}})
    assert c.fire_group == -1 and c.hold_seconds == 6.0
