"""Unit tests for KeybindCapability's safety layer (DESIGN §6, §9).

Offline and hermetic: a fake executor records what would be pressed, an injectable clock
and a mutable status snapshot drive the guards. Covers the four non-negotiable gates —
allowlist, explicit (turn-gated) confirmation, combat/interdiction guard, and hard abort.
"""
from __future__ import annotations

from covas.keybinds.binds import KeyBinding
from covas.capabilities.keybind_capability import (KeybindCapability, KeybindConfig,
                                                   Macro, combat_state)


class _FakeExecutor:
    def __init__(self) -> None:
        self.pressed: list[str] = []
        self.held: list[tuple[str, float]] = []
        self.released_all = 0

    def press(self, binding) -> None:
        self.pressed.append(binding.key)

    def hold(self, binding, seconds) -> None:
        self.held.append((binding.key, seconds))

    def release_all(self) -> None:
        self.released_all += 1


class _Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


_LG = {"LandingGearToggle": KeyBinding(action="LandingGearToggle", key="Key_L")}
_SAFE = {"in_danger": False, "being_interdicted": False}


def _safe(mode: str | None = None) -> dict:
    """A safe (no combat) status snapshot, optionally carrying a game_mode for mode-gating."""
    return {"in_danger": False, "being_interdicted": False, "game_mode": mode}


def _cap(*, binds=None, cfg=None, status=_SAFE, clock=None):
    ex = _FakeExecutor()
    clk = clock or _Clock()
    cap = KeybindCapability(
        binds=_LG if binds is None else binds,
        executor=ex,
        config=cfg or KeybindConfig(enabled=True),
        status_snapshot=(lambda: status),
        clock=clk,
    )
    return cap, ex, clk


# --- tools + allowlist -----------------------------------------------------

def test_tools_expose_arm_confirm_abort():
    cap, _, _ = _cap()
    names = {t["name"] for t in cap.tools()}
    assert names == {"toggle_landing_gear", "confirm_keybind", "abort_keybinds"}


def test_non_allowlisted_macro_not_advertised():
    cfg = KeybindConfig(enabled=True, allowlist=())     # nothing allowed
    cap, _, _ = _cap(cfg=cfg)
    names = {t["name"] for t in cap.tools()}
    assert "toggle_landing_gear" not in names
    # arming it is refused even if the model calls it directly
    assert "disallowed" in cap.run_tool("toggle_landing_gear", {}).lower()


# --- confirmation flow (turn-gated) ----------------------------------------

def test_arm_does_not_execute():
    cap, ex, _ = _cap()
    msg = cap.run_tool("toggle_landing_gear", {})
    assert ex.pressed == []                 # nothing fired on arm
    assert "confirm" in msg.lower()


def test_confirm_in_same_turn_is_refused():
    cap, ex, _ = _cap()
    cap.new_turn()                          # turn 1 (the arming utterance)
    cap.run_tool("toggle_landing_gear", {})
    # model tries to confirm without a new Commander command
    msg = cap.run_tool("confirm_keybind", {})
    assert ex.pressed == []
    assert "separate" in msg.lower() or "new command" in msg.lower()


def test_confirm_on_new_turn_executes():
    cap, ex, _ = _cap()
    cap.new_turn()                          # turn 1: "lower the gear"
    cap.run_tool("toggle_landing_gear", {})
    cap.new_turn()                          # turn 2: "confirm"
    msg = cap.run_tool("confirm_keybind", {})
    assert ex.pressed == ["Key_L"]
    assert "Key_L" in msg


def test_confirm_restates_the_armed_action_from_the_payload(monkeypatch):
    # issue #190 (confused-deputy defence-in-depth): the confirm output re-states the ACTUAL armed
    # action sourced from the pending payload — the deterministic macro — not the model's earlier
    # narration. Arm two DISTINCT macros' worth of wording and prove the confirm names the one that
    # was actually armed, so a bait-and-switch (arm B, narrate A) is audible when it fires.
    macros = {
        "gear": Macro(name="gear", tool="toggle_landing_gear", action="LandingGearToggle",
                      arm_phrase="toggle the landing gear", done_phrase="Landing gear toggled"),
        "silent": Macro(name="silent", tool="run_silent_running", action="SilentRunning",
                        arm_phrase="engage silent running", done_phrase="Silent running on"),
    }
    binds = {"LandingGearToggle": KeyBinding(action="LandingGearToggle", key="Key_L"),
             "SilentRunning": KeyBinding(action="SilentRunning", key="Key_Delete")}
    ex = _FakeExecutor()
    cap = KeybindCapability(
        binds=binds, executor=ex,
        config=KeybindConfig(enabled=True, combat_guard=False,
                             allowlist=("gear", "silent")),
        macros=macros, status_snapshot=(lambda: _SAFE), clock=_Clock())

    cap.new_turn()
    cap.run_tool("run_silent_running", {})   # ARM the silent-running macro (the "true" action)
    cap.new_turn()
    msg = cap.run_tool("confirm_keybind", {})

    # The confirm names the ARMED action's own arm_phrase (from the payload)...
    assert "engage silent running" in msg
    # ...and does NOT name the OTHER macro the model might have narrated instead.
    assert "landing gear" not in msg.lower()
    assert ex.pressed == ["Key_Delete"]      # and it fired the armed action, not the narrated one


def test_confirm_without_arm_is_noop():
    cap, ex, _ = _cap()
    msg = cap.run_tool("confirm_keybind", {})
    assert ex.pressed == []
    assert "nothing to confirm" in msg.lower()


def test_confirm_window_expiry():
    clk = _Clock()
    cap, ex, _ = _cap(cfg=KeybindConfig(enabled=True, confirm_window=30.0), clock=clk)
    cap.new_turn()
    cap.run_tool("toggle_landing_gear", {})
    cap.new_turn()
    clk.t += 60.0                            # past the 30s window
    msg = cap.run_tool("confirm_keybind", {})
    assert ex.pressed == []
    assert "expired" in msg.lower()


def test_confirmation_disabled_executes_immediately():
    cfg = KeybindConfig(enabled=True, require_confirmation=False)
    cap, ex, _ = _cap(cfg=cfg)
    cap.run_tool("toggle_landing_gear", {})
    assert ex.pressed == ["Key_L"]


# --- combat / interdiction guard -------------------------------------------

def test_combat_state_classification():
    assert combat_state(None) == "unknown"
    assert combat_state({"being_interdicted": True}) == "interdiction"
    assert combat_state({"in_danger": True}) == "combat"
    assert combat_state({"in_danger": False, "being_interdicted": False}) == "safe"


def test_guard_blocks_arming_during_interdiction():
    cap, ex, _ = _cap(status={"being_interdicted": True})
    msg = cap.run_tool("toggle_landing_gear", {})
    assert ex.pressed == []
    assert "interdict" in msg.lower()


def test_guard_blocks_when_status_unknown():
    ex = _FakeExecutor()
    cap = KeybindCapability(binds=_LG, executor=ex, config=KeybindConfig(enabled=True),
                            status_snapshot=None)      # no ED monitoring
    msg = cap.run_tool("toggle_landing_gear", {})
    assert ex.pressed == []
    assert "status isn't available" in msg.lower() or "holding off" in msg.lower()


def test_guard_rechecks_at_confirm_time():
    status = dict(_SAFE)
    cap, ex, _ = _cap(status=status)
    cap.new_turn()
    cap.run_tool("toggle_landing_gear", {})   # armed while safe
    status["in_danger"] = True                # combat starts before confirm
    cap.new_turn()
    msg = cap.run_tool("confirm_keybind", {})
    assert ex.pressed == []
    assert "danger" in msg.lower() or "combat" in msg.lower()


def test_guard_can_be_disabled():
    cfg = KeybindConfig(enabled=True, combat_guard=False, require_confirmation=False)
    ex = _FakeExecutor()
    cap = KeybindCapability(binds=_LG, executor=ex, config=cfg, status_snapshot=None)
    cap.run_tool("toggle_landing_gear", {})
    assert ex.pressed == ["Key_L"]


# --- unusable binding ------------------------------------------------------

def test_unusable_binding_reports_clear_message():
    binds = {"LandingGearToggle": KeyBinding(action="LandingGearToggle", key=None)}
    cap, ex, _ = _cap(binds=binds)
    msg = cap.run_tool("toggle_landing_gear", {})
    assert ex.pressed == []
    assert "no keyboard binding" in msg.lower()


def test_missing_binding_reports_bind_it_message():
    cap, ex, _ = _cap(binds={})               # action not in .binds at all
    msg = cap.run_tool("toggle_landing_gear", {})
    assert ex.pressed == []
    assert "bind" in msg.lower()


# --- hard abort ------------------------------------------------------------

def test_abort_clears_pending_and_releases_keys():
    cap, ex, _ = _cap()
    cap.new_turn()
    cap.run_tool("toggle_landing_gear", {})   # arm
    msg = cap.run_tool("abort_keybinds", {})
    assert ex.released_all == 1
    assert "abort" in msg.lower()
    # after abort, a confirm finds nothing pending
    cap.new_turn()
    assert "nothing to confirm" in cap.run_tool("confirm_keybind", {}).lower()


# --- mode gating (#29) -----------------------------------------------------

def test_mode_gate_advertises_action_in_matching_mode():
    cap, _, _ = _cap(status=_safe("mainship"))
    names = {t["name"] for t in cap.tools()}
    assert "toggle_landing_gear" in names


def test_mode_gate_hides_action_out_of_mode():
    # landing_gear is a mainship action; on foot it must not be advertised.
    cap, _, _ = _cap(status=_safe("on_foot"))
    names = {t["name"] for t in cap.tools()}
    assert "toggle_landing_gear" not in names
    # confirm/abort are always available.
    assert {"confirm_keybind", "abort_keybinds"} <= names


def test_mode_unknown_advertises_all():
    # No game_mode in the snapshot (ED telemetry can't pin a mode) -> fall back to advertising.
    cap, _, _ = _cap(status=_safe(None))
    assert "toggle_landing_gear" in {t["name"] for t in cap.tools()}


def test_arm_refused_out_of_mode():
    cap, ex, _ = _cap(status=_safe("on_foot"))
    msg = cap.run_tool("toggle_landing_gear", {})
    assert ex.pressed == []
    assert "on foot" in msg.lower()


def test_arm_allowed_in_mode():
    cap, ex, _ = _cap(status=_safe("mainship"))
    msg = cap.run_tool("toggle_landing_gear", {})
    assert ex.pressed == []                  # armed, not fired (confirmation required)
    assert "confirm" in msg.lower()


def test_mode_guard_rechecked_at_confirm():
    status = _safe("mainship")
    cap, ex, _ = _cap(status=status)
    cap.new_turn()
    cap.run_tool("toggle_landing_gear", {})  # armed in the ship
    status["game_mode"] = "on_foot"          # Commander disembarks before confirming
    cap.new_turn()
    msg = cap.run_tool("confirm_keybind", {})
    assert ex.pressed == []
    assert "on foot" in msg.lower()


def test_mode_guard_can_be_disabled():
    cfg = KeybindConfig(enabled=True, mode_guard=False, require_confirmation=False)
    cap, ex, _ = _cap(cfg=cfg, status=_safe("on_foot"))
    # With mode-gating off, the out-of-mode action is advertised and fires.
    assert "toggle_landing_gear" in {t["name"] for t in cap.tools()}
    cap.run_tool("toggle_landing_gear", {})
    assert ex.pressed == ["Key_L"]


# --- per-action confirmation policy (#29) ----------------------------------

def test_macro_confirm_required_false_fires_immediately():
    # A benign macro (confirm_required=False) fires on arm even with global confirmation ON.
    macros = {"benign": Macro(name="benign", tool="do_benign", action="LandingGearToggle",
                              arm_phrase="do the benign thing", done_phrase="Done",
                              confirm_required=False)}
    ex = _FakeExecutor()
    cap = KeybindCapability(
        binds=_LG, executor=ex,
        config=KeybindConfig(enabled=True, require_confirmation=True, combat_guard=False,
                             mode_guard=False, allowlist=("benign",)),
        macros=macros, status_snapshot=None)
    msg = cap.run_tool("do_benign", {})
    assert ex.pressed == ["Key_L"]           # fired immediately, no confirmation step
    assert "Key_L" in msg


def test_macro_confirm_required_true_still_arms():
    # A consequential macro (default confirm_required=True) still arms-and-confirms.
    cap, ex, _ = _cap(status=_safe("mainship"))
    cap.run_tool("toggle_landing_gear", {})
    assert ex.pressed == []


# --- hold macro ------------------------------------------------------------

def test_hold_macro_uses_executor_hold():
    macros = {"charge": Macro(name="charge", tool="charge_fsd", action="HyperSuperCombination",
                              arm_phrase="charge the FSD", done_phrase="FSD charging",
                              kind="hold", hold_seconds=1.5)}
    binds = {"HyperSuperCombination": KeyBinding(action="HyperSuperCombination", key="Key_J")}
    ex = _FakeExecutor()
    cap = KeybindCapability(binds=binds, executor=ex,
                            config=KeybindConfig(enabled=True, require_confirmation=False,
                                                 combat_guard=False, allowlist=("charge",)),
                            macros=macros, status_snapshot=None)
    cap.run_tool("charge_fsd", {})
    assert ex.held == [("Key_J", 1.5)]
