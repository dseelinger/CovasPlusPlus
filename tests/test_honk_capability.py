"""Unit tests for auto-honk (N5 + K2 detect-and-recover) — offline, free (DESIGN §6, §9).

No fire groups: the honk fires the CURRENT group's Primary/Secondary and reacts. Covers the
probe-then-honk, the Surface-Scanner-misfire recovery (exit + warn + disarm), the guards
(combat, supercruise, analysis mode), re-arm (verbal tool + auto on a discovery scan), the
fail-soft unbound-key path, and config parsing. A recording fake executor asserts the exact
key sequence; no journal, no audio, no real key injection.
"""
from __future__ import annotations

from covas.ed.status import GUI_FOCUS_SAA
from covas.keybinds.binds import KeyBinding
from covas.capabilities.honk_capability import HonkCapability, HonkConfig, _PROBE_SECONDS


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


_BINDS = {
    "PrimaryFire": KeyBinding(action="PrimaryFire", key="Key_1"),
    "SecondaryFire": KeyBinding(action="SecondaryFire", key="Key_2"),
    "ExplorationSAAExitThirdPerson": KeyBinding(action="ExplorationSAAExitThirdPerson", key="Key_X"),
}
# A "safe to honk" snapshot: not in danger, in supercruise, analysis (not combat) mode, no focus.
_SAFE = {"in_danger": False, "being_interdicted": False, "supercruise": True,
         "analysis_mode": True, "gui_focus": None}
_P = _PROBE_SECONDS


def _cap(*, cfg=None, binds=None, status=_SAFE, speak=None):
    ex = _FakeExecutor()
    cap = HonkCapability(
        cfg or HonkConfig(enabled=True),
        binds=_BINDS if binds is None else binds,
        executor=ex,
        status_snapshot=(lambda: status),
        spawn=lambda fn: fn(),            # synchronous: run the sequence inline for the test
        speak=speak,
        sleep=lambda _s: None,            # no real waiting in the detect-window poll
        log=lambda m: None)
    return cap, ex


def _jump(cap):
    cap.on_event({"type": "ed_event", "event": "FSDJump", "StarSystem": "Sol"})


# --- probe-then-honk -------------------------------------------------------

def test_probe_then_honks_when_clear():
    # Short probe, GuiFocus stays normal -> complete the honk on the current group.
    cap, ex = _cap(cfg=HonkConfig(enabled=True, hold_seconds=6.0))
    _jump(cap)
    assert ex.calls == [("hold", "Key_1", _P), ("hold", "Key_1", 6.0)]


def test_secondary_trigger_holds_secondary_fire():
    cap, ex = _cap(cfg=HonkConfig(enabled=True, trigger="secondary", hold_seconds=6.0))
    _jump(cap)
    assert ex.calls == [("hold", "Key_2", _P), ("hold", "Key_2", 6.0)]


def test_probe_detects_dss_and_recovers():
    # Probe opened the Surface Scanner (GuiFocus == SAA) -> exit, warn, disarm, NO full honk.
    spoken: list[str] = []
    cap, ex = _cap(status={**_SAFE, "gui_focus": GUI_FOCUS_SAA},
                   speak=lambda t: spoken.append(t))
    _jump(cap)
    assert ex.calls == [("hold", "Key_1", _P), ("press", "Key_X", 0.0)]
    assert cap._disarmed is True
    assert spoken and "Surface Scanner" in spoken[0]


def test_probe_detects_dss_even_when_snapshot_lags():
    # The ~1s-polled snapshot shows the SAA mode only after a couple reads -> the detect window
    # must still catch it and recover, NOT do the full honk in the Surface Scanner.
    reads = [{**_SAFE, "gui_focus": None}] * 3 + [{**_SAFE, "gui_focus": GUI_FOCUS_SAA}]
    i = {"n": 0}

    def status():
        snap = reads[min(i["n"], len(reads) - 1)]
        i["n"] += 1
        return snap

    ex = _FakeExecutor()
    cap = HonkCapability(HonkConfig(enabled=True), binds=_BINDS, executor=ex,
                         status_snapshot=status, spawn=lambda fn: fn(),
                         sleep=lambda _s: None, log=lambda m: None)
    _jump(cap)
    assert ex.calls == [("hold", "Key_1", _P), ("press", "Key_X", 0.0)]  # probe -> exit, no honk
    assert cap._disarmed is True


# --- disarm / re-arm -------------------------------------------------------

def test_disarmed_skips():
    cap, ex = _cap()
    cap._disarmed = True
    _jump(cap)
    assert ex.calls == []


def test_rearm_tool_clears_disarm():
    cap, _ = _cap()
    cap._disarmed = True
    msg = cap.run_tool("rearm_auto_honk", {})
    assert "re-armed" in msg.lower() and cap._disarmed is False


def test_auto_rearm_on_discovery_scan():
    cap, ex = _cap()
    cap._disarmed = True
    cap.on_event({"type": "ed_event", "event": "FSSDiscoveryScan", "BodyCount": 5})
    assert cap._disarmed is False
    assert ex.calls == []              # a re-arm event never fires a honk


# --- guards ----------------------------------------------------------------

def test_guard_suppresses_during_danger():
    cap, ex = _cap(status={**_SAFE, "in_danger": True})
    _jump(cap)
    assert ex.calls == []


def test_guard_suppresses_during_interdiction():
    cap, ex = _cap(status={**_SAFE, "being_interdicted": True})
    _jump(cap)
    assert ex.calls == []


def test_guard_suppresses_when_status_unknown():
    ex = _FakeExecutor()
    cap = HonkCapability(HonkConfig(enabled=True), binds=_BINDS, executor=ex,
                         status_snapshot=None, spawn=lambda fn: fn())
    _jump(cap)
    assert ex.calls == []                 # can't prove it's safe -> no honk


def test_supercruise_required():
    cap, ex = _cap(status={**_SAFE, "supercruise": False})
    _jump(cap)
    assert ex.calls == []


def test_analysis_mode_required():
    cap, ex = _cap(status={**_SAFE, "analysis_mode": False})
    _jump(cap)
    assert ex.calls == []


def test_guard_can_be_disabled_with_status():
    cfg = HonkConfig(enabled=True, combat_guard=False, hold_seconds=6.0)
    cap, ex = _cap(cfg=cfg, status={**_SAFE, "in_danger": True})
    _jump(cap)
    assert ex.calls == [("hold", "Key_1", _P), ("hold", "Key_1", 6.0)]  # guard off -> honks


# --- fail-soft -------------------------------------------------------------

def test_unbound_fire_key_skips():
    binds = dict(_BINDS)
    binds["PrimaryFire"] = KeyBinding(action="PrimaryFire", key=None)   # joystick-only
    cap, ex = _cap(binds=binds)
    _jump(cap)
    assert ex.calls == []


def test_recover_without_exit_binding_still_disarms():
    binds = dict(_BINDS)
    del binds["ExplorationSAAExitThirdPerson"]     # can't auto-exit
    cap, ex = _cap(binds=binds, status={**_SAFE, "gui_focus": GUI_FOCUS_SAA})
    _jump(cap)
    assert ex.calls == [("hold", "Key_1", _P)]     # probed, no exit press, but still...
    assert cap._disarmed is True                   # ...disarms so it can't keep misfiring


# --- event gating ----------------------------------------------------------

def test_non_arrival_events_ignored():
    cap, ex = _cap()
    cap.on_event({"type": "ed_event", "event": "Docked"})
    cap.on_event({"type": "ed_event", "event": "SupercruiseExit"})
    cap.on_event({"type": "log", "who": "system", "text": "hi"})
    assert ex.calls == []


def test_exposes_rearm_tool():
    cap, _ = _cap()
    assert [t["name"] for t in cap.tools()] == ["rearm_auto_honk"]


def test_bad_event_does_not_raise():
    cap, ex = _cap()
    cap.on_event("not a dict")             # type: ignore[arg-type]
    cap.on_event({})
    assert ex.calls == []


# --- config parsing --------------------------------------------------------

def test_config_from_cfg_defaults():
    d = HonkConfig.from_cfg({})
    assert d.enabled is False and d.trigger == "primary"
    assert d.hold_seconds == 5.0 and d.combat_guard is True
    assert d.fire_action == "PrimaryFire"


def test_config_from_cfg_reads_and_normalizes():
    c = HonkConfig.from_cfg({"honk": {"enabled": True, "trigger": "SECONDARY",
                                      "hold_seconds": 4.5, "combat_guard": False}})
    assert c.enabled and c.trigger == "secondary" and c.hold_seconds == 4.5
    assert c.combat_guard is False and c.fire_action == "SecondaryFire"


def test_config_bad_values_fall_back():
    c = HonkConfig.from_cfg({"honk": {"hold_seconds": "y"}})
    assert c.hold_seconds == 5.0 and c.trigger == "primary"
