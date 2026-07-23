"""Unit tests for the flight/nav action batch (issue #30).

Offline and hermetic (DESIGN §9): assert the batch registers the expected macros with the
right modes + confirmation policy, then drive a representative benign macro and a representative
consequential one through KeybindCapability with a recording fake executor — following
test_keybind_capability.py's style, constructed with our macros + an allowlist including them.
"""
from __future__ import annotations

from covas.capabilities.keybind_capability import KeybindCapability, KeybindConfig
from covas.ed.modes import MODE_FIGHTER, MODE_MAINSHIP
from covas.keybinds.binds import KeyBinding
from covas.keybinds.registry import registered_macros


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


def _safe(mode: str | None = "mainship") -> dict:
    return {"in_danger": False, "being_interdicted": False, "game_mode": mode}


# --- registration ----------------------------------------------------------

# name -> (ED action token, expected modes, confirm_required)
_EXPECTED = {
    "throttle_zero": ("SetSpeedZero", {MODE_MAINSHIP, MODE_FIGHTER}, False),
    "throttle_50": ("SetSpeed50", {MODE_MAINSHIP, MODE_FIGHTER}, False),
    "throttle_100": ("SetSpeed100", {MODE_MAINSHIP, MODE_FIGHTER}, False),
    "frame_shift_drive": ("HyperSuperCombination", {MODE_MAINSHIP}, True),
    "supercruise": ("Supercruise", {MODE_MAINSHIP}, True),
    "hyperspace": ("Hyperspace", {MODE_MAINSHIP}, True),
    "flight_assist": ("ToggleFlightAssist", {MODE_MAINSHIP, MODE_FIGHTER}, True),
    "select_target_ahead": ("SelectTarget", {MODE_MAINSHIP, MODE_FIGHTER}, False),
    "cycle_next_target": ("CycleNextTarget", {MODE_MAINSHIP, MODE_FIGHTER}, False),
    "cycle_previous_target": ("CyclePreviousTarget", {MODE_MAINSHIP, MODE_FIGHTER}, False),
    "target_next_route_system": ("TargetNextRouteSystem", {MODE_MAINSHIP}, False),
    "nav_lock": ("WingNavLock", {MODE_MAINSHIP}, False),
}


def test_flight_macros_registered_with_expected_policy():
    reg = registered_macros()
    for name, (action, modes, confirm) in _EXPECTED.items():
        assert name in reg, f"{name} not registered"
        m = reg[name]
        assert m.action == action
        assert set(m.modes) == modes
        assert m.confirm_required is confirm


def test_consequential_flight_actions_confirm_benign_do_not():
    reg = registered_macros()
    # jump/supercruise/FA are consequential; throttle/target/nav-lock are benign.
    assert reg["supercruise"].confirm_required is True
    assert reg["hyperspace"].confirm_required is True
    assert reg["frame_shift_drive"].confirm_required is True
    assert reg["throttle_zero"].confirm_required is False
    assert reg["cycle_next_target"].confirm_required is False
    assert reg["nav_lock"].confirm_required is False


def test_fsd_actions_are_mainship_only():
    reg = registered_macros()
    for name in ("frame_shift_drive", "supercruise", "hyperspace",
                 "target_next_route_system", "nav_lock"):
        assert set(reg[name].modes) == {MODE_MAINSHIP}


# --- benign macro fires immediately (behind the guards) --------------------

def test_benign_throttle_fires_immediately():
    """A benign macro (confirm_required=False) fires on arm even with confirmation ON —
    still gated by allowlist + combat + mode guards."""
    binds = {"SetSpeedZero": KeyBinding(action="SetSpeedZero", key="Key_Backspace")}
    ex = _FakeExecutor()
    cap = KeybindCapability(
        binds=binds, executor=ex,
        config=KeybindConfig(enabled=True, require_confirmation=True, allowlist=("throttle_zero",)),
        status_snapshot=(lambda: _safe("mainship")),
    )
    # advertised in the ship
    assert "set_throttle_zero" in {t["name"] for t in cap.tools()}
    msg = cap.run_tool("set_throttle_zero", {})
    assert ex.pressed == ["Key_Backspace"]
    assert "Key_Backspace" in msg


# --- consequential macro arms-and-confirms ---------------------------------

def test_supercruise_arms_then_confirms_on_new_turn():
    binds = {"Supercruise": KeyBinding(action="Supercruise", key="Key_M")}
    ex = _FakeExecutor()
    cap = KeybindCapability(
        binds=binds, executor=ex,
        config=KeybindConfig(enabled=True, allowlist=("supercruise",)),
        status_snapshot=(lambda: _safe("mainship")),
    )
    cap.new_turn()                                  # turn 1: "engage supercruise"
    msg = cap.run_tool("engage_supercruise", {})
    assert ex.pressed == []                         # armed, not fired
    assert "confirm" in msg.lower()
    cap.new_turn()                                  # turn 2: "confirm"
    out = cap.run_tool("confirm_keybind", {})
    assert ex.pressed == ["Key_M"]
    assert "Key_M" in out


# --- mode gating: FSD not offered in a fighter -----------------------------

def test_fsd_not_advertised_in_fighter():
    binds = {"Supercruise": KeyBinding(action="Supercruise", key="Key_M")}
    ex = _FakeExecutor()
    cap = KeybindCapability(
        binds=binds, executor=ex,
        config=KeybindConfig(enabled=True, allowlist=("supercruise",)),
        status_snapshot=(lambda: _safe(MODE_FIGHTER)),
    )
    assert "engage_supercruise" not in {t["name"] for t in cap.tools()}
    # and arming it in a fighter is refused
    msg = cap.run_tool("engage_supercruise", {})
    assert ex.pressed == []
    assert "fighter" in msg.lower()


def test_throttle_advertised_in_fighter():
    binds = {"SetSpeedZero": KeyBinding(action="SetSpeedZero", key="Key_Backspace")}
    ex = _FakeExecutor()
    cap = KeybindCapability(
        binds=binds, executor=ex,
        config=KeybindConfig(enabled=True, require_confirmation=True, allowlist=("throttle_zero",)),
        status_snapshot=(lambda: _safe(MODE_FIGHTER)),
    )
    assert "set_throttle_zero" in {t["name"] for t in cap.tools()}


# --- unbound token degrades to a "bind it" message -------------------------

def test_unbound_token_degrades_gracefully():
    ex = _FakeExecutor()
    cap = KeybindCapability(
        binds={}, executor=ex,                      # nav lock not bound to a key
        config=KeybindConfig(enabled=True, allowlist=("nav_lock",)),
        status_snapshot=(lambda: _safe("mainship")),
    )
    msg = cap.run_tool("toggle_nav_lock", {})
    assert ex.pressed == []
    assert "bind" in msg.lower()
