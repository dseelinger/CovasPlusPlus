"""Unit tests for the long-hyperspace flavor remark (issue #149) — pure, offline, free.

Covers the flavor prompt (asserts no game facts), and the capability's on_event gating: fires on a
long plotted jump, stays silent on a short one / unplotted route / non-hyperspace jump, and honours
the shared proactive enable + mute and its own dedicated cooldown.
"""
from __future__ import annotations

from covas.capabilities.long_jump_capability import LongJumpCapability, build_long_jump_prompt
from covas.capabilities.proactive_capability import ProactiveConfig, ProactivePolicy

# --- flavor prompt --------------------------------------------------------------------

def test_prompt_is_flavor_and_asserts_no_facts():
    p = build_long_jump_prompt(120.0)
    assert "UNPROMPTED" in p
    assert "hyperspace" in p.lower()
    assert "Assert NO facts" in p or "assert no facts" in p.lower()
    # the distance is mood-only, never quoted as a figure
    assert "120" not in p


def test_prompt_without_distance():
    p = build_long_jump_prompt(None)
    assert "long jump" in p.lower() or "long haul" in p.lower() or "long" in p.lower()


# --- capability wiring ----------------------------------------------------------------

class _Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class _Speaker:
    def __init__(self, started=True):
        self.calls = []
        self.started = started

    def __call__(self, event, prompt):
        self.calls.append((event.get("StarSystem"), prompt))
        return self.started


# A plotted route: Sol -> Dest, exactly 60 ly apart (0,0,0)->(60,0,0).
_NAVROUTE = {"Route": [
    {"StarSystem": "Sol", "StarClass": "G", "StarPos": [0.0, 0.0, 0.0]},
    {"StarSystem": "Dest", "StarClass": "K", "StarPos": [60.0, 0.0, 0.0]},
]}

_SHORT_NAVROUTE = {"Route": [
    {"StarSystem": "Sol", "StarClass": "G", "StarPos": [0.0, 0.0, 0.0]},
    {"StarSystem": "Near", "StarClass": "K", "StarPos": [10.0, 0.0, 0.0]},
]}


def _policy(**over):
    cfg = {"enabled": True, "min_interval": 0, "long_jump_enabled": True,
           "long_jump_ly": 50.0, "long_jump_cooldown": 300}
    cfg.update(over)
    return ProactivePolicy(ProactiveConfig.from_cfg({"proactive": cfg}))


def _cap(policy, speaker, clock, navroute=_NAVROUTE, system="Sol"):
    return LongJumpCapability(
        policy, speaker,
        load_navroute=lambda: navroute,
        current_system=lambda: system,
        clock=clock)


def _start_jump(dest="Dest", jump_type="Hyperspace"):
    return {"type": "ed_event", "event": "StartJump", "JumpType": jump_type, "StarSystem": dest}


def test_fires_on_long_jump():
    spk = _Speaker()
    cap = _cap(_policy(), spk, _Clock())
    cap.on_event(_start_jump())
    assert len(spk.calls) == 1 and spk.calls[0][0] == "Dest"


def test_silent_on_short_jump():
    spk = _Speaker()
    cap = _cap(_policy(), spk, _Clock(), navroute=_SHORT_NAVROUTE, system="Sol")
    cap.on_event(_start_jump(dest="Near"))
    assert spk.calls == []


def test_silent_when_route_unplotted():
    """No coords for the systems -> can't gate -> stay silent (fail-soft)."""
    spk = _Speaker()
    cap = _cap(_policy(), spk, _Clock(), navroute={"Route": []})
    cap.on_event(_start_jump())
    assert spk.calls == []


def test_ignores_supercruise_jumps_and_non_startjump():
    spk = _Speaker()
    cap = _cap(_policy(), spk, _Clock())
    cap.on_event(_start_jump(jump_type="Supercruise"))     # not hyperspace
    cap.on_event({"type": "ed_event", "event": "FSDJump", "StarSystem": "Dest"})
    cap.on_event({"type": "log", "text": "hi"})
    assert spk.calls == []


def test_disabled_and_muted_stay_silent():
    spk = _Speaker()
    cap = _cap(_policy(enabled=False), spk, _Clock())
    cap.on_event(_start_jump())
    assert spk.calls == []

    spk2 = _Speaker()
    pol = _policy()
    pol.set_muted(True)
    cap2 = _cap(pol, spk2, _Clock())
    cap2.on_event(_start_jump())
    assert spk2.calls == []


def test_long_jump_off_toggle():
    spk = _Speaker()
    cap = _cap(_policy(long_jump_enabled=False), spk, _Clock())
    cap.on_event(_start_jump())
    assert spk.calls == []


def test_cooldown_gates_repeat_long_jumps():
    spk = _Speaker()
    clock = _Clock()
    cap = _cap(_policy(long_jump_cooldown=300), spk, clock)
    cap.on_event(_start_jump())
    assert len(spk.calls) == 1
    clock.t += 100                       # within the 300s cooldown
    cap.on_event(_start_jump())
    assert len(spk.calls) == 1           # still gated
    clock.t += 250                       # now past it
    cap.on_event(_start_jump())
    assert len(spk.calls) == 2


def test_busy_app_does_not_arm_cooldown():
    """When the app is busy (speak returns False), the cooldown is NOT armed, so the flavor line
    can fire once the Commander is done rather than being swallowed."""
    spk = _Speaker(started=False)
    clock = _Clock()
    cap = _cap(_policy(long_jump_cooldown=300), spk, clock)
    cap.on_event(_start_jump())
    assert len(spk.calls) == 1           # attempted...
    clock.t += 1                          # ...still within any cooldown
    cap.on_event(_start_jump())
    assert len(spk.calls) == 2           # retried because nothing was armed
