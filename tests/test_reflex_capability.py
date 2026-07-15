"""Unit tests for Tier-2 combat reflexes (issue #36) — offline, free (DESIGN §6, §9).

The Tier-2 model INVERTS Tier-1: the combat-permissive guard permits a small defensive set
(chaff / heat sink / shields / boost) ONLY while under fire, and HARD-REFUSES a dangerous set
(eject cargo, self-destruct, landing gear) at all times. These tests pin:

  * the guard's three cases — permitted-in-combat, always-refused, permitted-but-not-in-combat;
  * the chaff reflex end-to-end on the shared executor (dispatch presses the chaff key);
  * the allowlist + fail-soft (unbound key) + guard re-enforcement at run time;
  * the hard abort (`release_all()`), both via the capability's abort tool and on the real
    KeyExecutor with a fake backend (a held key is lifted).

A recording fake executor + a fake Status feed keep it hermetic — no real presses, no waiting.
"""
from __future__ import annotations

from covas.keybinds.binds import KeyBinding
from covas.keybinds.executor import KeyExecutor
from covas.capabilities.reflex_capability import (
    ALWAYS_REFUSED,
    COMBAT_PERMISSIVE,
    ReflexCapability,
    ReflexConfig,
    combat_permissive_verdict,
)


# --- recording fake executor + fake Status feed ----------------------------

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


_BINDS = {"FireChaffLauncher": KeyBinding(action="FireChaffLauncher", key="Key_H")}

# Snapshots for the three danger classes the guard reads.
_COMBAT = {"in_danger": True, "being_interdicted": False}
_INTERDICTION = {"in_danger": False, "being_interdicted": True}
_SAFE = {"in_danger": False, "being_interdicted": False}


def _cap(*, allowlist=("chaff",), binds=None, status=_COMBAT, combat_guard=True):
    ex = _FakeExecutor()
    cap = ReflexCapability(
        binds=_BINDS if binds is None else binds,
        executor=ex,
        config=ReflexConfig(enabled=True, combat_guard=combat_guard, allowlist=tuple(allowlist)),
        status_snapshot=(None if status is None else (lambda: status)),
        log=lambda m: None,
    )
    return cap, ex


# =========================================================================
# The combat-permissive guard (pure) — the three required cases + edges
# =========================================================================

def test_guard_permits_permitted_action_in_combat():
    # 1. permitted-in-combat: chaff while in danger -> permitted (None).
    assert combat_permissive_verdict("chaff", _COMBAT) is None


def test_guard_permits_permitted_action_during_interdiction():
    assert combat_permissive_verdict("chaff", _INTERDICTION) is None


def test_guard_always_refuses_dangerous_action_even_in_combat():
    # 2. always-refused: dangerous actions are refused even while in danger.
    for act in ALWAYS_REFUSED:
        assert combat_permissive_verdict(act, _COMBAT) is not None
    # self-destruct is refused in combat AND when safe.
    assert combat_permissive_verdict("self_destruct", _SAFE) is not None


def test_guard_refuses_permitted_action_when_not_in_combat():
    # 3. permitted-action-but-not-in-combat: chaff while safe -> refused (reflexes are FOR combat).
    verdict = combat_permissive_verdict("chaff", _SAFE)
    assert verdict is not None and "not in combat" in verdict.lower()


def test_guard_refuses_permitted_action_when_status_unknown():
    # No telemetry can't prove danger -> refuse (never fire a reflex blind).
    verdict = combat_permissive_verdict("chaff", None)
    assert verdict is not None and "status" in verdict.lower()


def test_guard_refuses_unknown_action_name():
    assert combat_permissive_verdict("wibble", _COMBAT) is not None


def test_policy_sets_are_disjoint():
    # The permitted and always-refused sets must never overlap — a name can't be both.
    assert not (COMBAT_PERMISSIVE & ALWAYS_REFUSED)


# =========================================================================
# The chaff reflex — end-to-end on the shared executor
# =========================================================================

def test_chaff_fires_in_combat():
    cap, ex = _cap(status=_COMBAT)
    msg = cap.run_tool("fire_chaff", {})
    assert ex.calls == [("press", "Key_H", 0.0)]      # pressed the chaff key
    assert "chaff away" in msg.lower()


def test_chaff_fires_during_interdiction():
    cap, ex = _cap(status=_INTERDICTION)
    cap.run_tool("fire_chaff", {})
    assert ex.calls == [("press", "Key_H", 0.0)]


def test_chaff_refused_when_safe():
    cap, ex = _cap(status=_SAFE)
    msg = cap.run_tool("fire_chaff", {})
    assert ex.calls == []                              # guard blocked the press
    assert "not in combat" in msg.lower()


def test_chaff_refused_when_status_unavailable():
    cap, ex = _cap(status=None)
    msg = cap.run_tool("fire_chaff", {})
    assert ex.calls == []
    assert "status" in msg.lower()


def test_chaff_not_in_allowlist_is_refused():
    # Default allowlist is empty (opt-in) — an un-allowlisted reflex isn't even advertised or run.
    cap, ex = _cap(allowlist=(), status=_COMBAT)
    msg = cap.run_tool("fire_chaff", {})
    assert ex.calls == []
    assert "disallowed" in msg.lower() or "unknown" in msg.lower()
    assert [t["name"] for t in cap.tools()] == ["abort_reflex"]   # not advertised


def test_unbound_chaff_key_fails_soft():
    binds = {"FireChaffLauncher": KeyBinding(action="FireChaffLauncher", key=None)}  # joystick-only
    cap, ex = _cap(binds=binds, status=_COMBAT)
    msg = cap.run_tool("fire_chaff", {})
    assert ex.calls == []                              # nothing pressed
    assert "bind" in msg.lower()


def test_missing_chaff_binding_fails_soft():
    cap, ex = _cap(binds={}, status=_COMBAT)           # token absent entirely
    msg = cap.run_tool("fire_chaff", {})
    assert ex.calls == []
    assert "bindings" in msg.lower()


def test_guard_off_lets_chaff_fire_when_safe():
    # Escape hatch: combat_guard off permits the reflex regardless of Status...
    cap, ex = _cap(status=_SAFE, combat_guard=False)
    cap.run_tool("fire_chaff", {})
    assert ex.calls == [("press", "Key_H", 0.0)]


def test_advertises_allowlisted_reflex_plus_abort():
    cap, _ = _cap(allowlist=("chaff",))
    names = [t["name"] for t in cap.tools()]
    assert names == ["fire_chaff", "abort_reflex"]


def test_unknown_tool_is_soft_error():
    cap, _ = _cap()
    assert "unknown" in cap.run_tool("nope", {}).lower()


# =========================================================================
# fire_reflex — the phrase-spotter fast-path entry (issue #38)
# =========================================================================
# The local spotter dispatches BY NAME through fire_reflex, which must reuse the SAME allowlist +
# combat-permissive guard + executor as the LLM tool path (no second guard).

def test_fire_reflex_by_name_fires_in_combat():
    cap, ex = _cap(status=_COMBAT)
    msg = cap.fire_reflex("chaff")
    assert ex.calls == [("press", "Key_H", 0.0)]       # same executor press as the tool path
    assert "chaff away" in msg.lower()


def test_fire_reflex_honours_the_combat_guard():
    # Safe -> the same guard that blocks the tool path blocks the spotter path (no second guard).
    cap, ex = _cap(status=_SAFE)
    msg = cap.fire_reflex("chaff")
    assert ex.calls == []
    assert "not in combat" in msg.lower()


def test_fire_reflex_honours_the_allowlist():
    cap, ex = _cap(allowlist=(), status=_COMBAT)       # empty allowlist -> nothing fireable
    msg = cap.fire_reflex("chaff")
    assert ex.calls == []
    assert "disallowed" in msg.lower() or "unknown" in msg.lower()


def test_fire_reflex_abort_sentinel_hits_hard_abort():
    # The spotter's ABORT sentinel routes to the SHARED release_all(), just like the abort tool.
    cap, ex = _cap()
    msg = cap.fire_reflex("abort")
    assert ex.released_all == 1
    assert "released" in msg.lower()


def test_fire_reflex_unbound_key_fails_soft():
    binds = {"FireChaffLauncher": KeyBinding(action="FireChaffLauncher", key=None)}
    cap, ex = _cap(binds=binds, status=_COMBAT)
    msg = cap.fire_reflex("chaff")
    assert ex.calls == []
    assert "bind" in msg.lower()


# =========================================================================
# Hard abort — release_all()
# =========================================================================

def test_abort_tool_calls_release_all():
    cap, ex = _cap()
    msg = cap.run_tool("abort_reflex", {})
    assert ex.released_all == 1
    assert "released" in msg.lower()


def test_hard_abort_lifts_held_key_on_real_executor():
    # The hard-abort PRIMITIVE, exercised on the real KeyExecutor with a fake backend: a key left
    # held (a hold) is lifted by release_all(). This is the guarantee a reflex can never strand.
    events: list[tuple[str, int]] = []

    class _Backend:
        def key_down(self, sc, ext): events.append(("down", sc))
        def key_up(self, sc, ext): events.append(("up", sc))

    ex = KeyExecutor(backend=_Backend(), sleep=lambda _s: None)
    chaff = KeyBinding(action="FireChaffLauncher", key="Key_H")
    ex.hold(chaff, 0.05)          # hold + immediate release (sleep is a no-op)
    events.clear()
    ex.release_all()              # nothing still held -> no stray events
    assert events == []

    # Now simulate a key genuinely stuck down, then prove release_all lifts it.
    ex.backend.key_down(0x23, False)
    ex._mark(0x23, False, down=True)   # mark it held as hold() would
    events.clear()
    ex.release_all()
    assert ("up", 0x23) in events


# =========================================================================
# Config parsing
# =========================================================================

def test_config_defaults_off_and_empty():
    d = ReflexConfig.from_cfg({})
    assert d.enabled is False and d.combat_guard is True and d.allowlist == ()


def test_config_reads_allowlist_and_guard():
    c = ReflexConfig.from_cfg({"reflex": {"enabled": True, "combat_guard": False,
                                          "allowlist": ["chaff"]}})
    assert c.enabled and c.combat_guard is False and c.allowlist == ("chaff",)


def test_config_bad_allowlist_falls_back_to_empty():
    c = ReflexConfig.from_cfg({"reflex": {"allowlist": "chaff"}})   # not a list/tuple
    assert c.allowlist == ()
