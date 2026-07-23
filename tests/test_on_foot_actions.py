"""Unit tests for the Odyssey on-foot action batch (issue #34).

Two things to prove, both offline:
  1. Registration policy — every on-foot macro is gated to exactly {on_foot} and is benign
     (confirm_required=False), and no combat action (fire/grenade) sneaked into the batch.
  2. The mode gate END-TO-END — driving a real KeybindCapability with these macros allowlisted,
     they must NOT be advertised or armable while in the main ship, but MUST be on foot. This is
     the whole point of the issue: correctly mode-aware on-foot actions.
"""
from __future__ import annotations

from covas.capabilities.keybind_capability import KeybindCapability, KeybindConfig
from covas.ed.modes import MODE_ON_FOOT
from covas.keybinds import actions as _actions  # noqa: F401 — populates the registry
from covas.keybinds.binds import KeyBinding
from covas.keybinds.registry import registered_macros

# The macros this batch is expected to register: name -> ED action token.
_EXPECTED = {
    "on_foot_flashlight": "HumanoidToggleFlashlightButton",
    "on_foot_night_vision": "HumanoidToggleNightVisionButton",
    "on_foot_select_primary": "HumanoidSelectPrimaryWeaponButton",
    "on_foot_select_secondary": "HumanoidSelectSecondaryWeaponButton",
    "on_foot_select_utility": "HumanoidSelectUtilityWeaponButton",
    "on_foot_holster": "HumanoidHideWeaponButton",
    "on_foot_energylink": "HumanoidSwitchToRechargeTool",
    "on_foot_profile_analyser": "HumanoidSwitchToCompAnalyser",
    "on_foot_suit_tool": "HumanoidSwitchToSuitTool",
    "on_foot_crouch": "HumanoidCrouchButton",
    "on_foot_galaxy_map": "GalaxyMapOpen_Humanoid",
}


class _FakeExecutor:
    def __init__(self) -> None:
        self.pressed: list[str] = []

    def press(self, binding) -> None:
        self.pressed.append(binding.key)

    def hold(self, binding, seconds) -> None:  # pragma: no cover - unused here
        self.pressed.append(binding.key)

    def release_all(self) -> None:  # pragma: no cover - unused here
        pass


def _safe(mode: str | None) -> dict:
    return {"in_danger": False, "being_interdicted": False, "game_mode": mode}


# --- registration policy ---------------------------------------------------

def test_all_expected_macros_registered():
    reg = registered_macros()
    for name, action in _EXPECTED.items():
        assert name in reg, f"{name} not registered"
        assert reg[name].action == action


def test_every_on_foot_macro_gated_to_on_foot_only():
    reg = registered_macros()
    for name in _EXPECTED:
        assert reg[name].modes == frozenset({MODE_ON_FOOT}), name


def test_every_on_foot_macro_is_benign_no_confirm():
    reg = registered_macros()
    for name in _EXPECTED:
        assert reg[name].confirm_required is False, name


def test_no_firing_or_grenade_action_in_batch():
    # Combat actions are explicitly out of scope; guard against one sneaking in.
    reg = registered_macros()
    for name in _EXPECTED:
        token = reg[name].action.lower()
        assert "fire" not in token and "grenade" not in token and "melee" not in token, name


def test_tool_names_are_natural_and_unique():
    reg = registered_macros()
    tools = [reg[name].tool for name in _EXPECTED]
    assert len(set(tools)) == len(tools)          # no collisions
    assert "toggle_flashlight" in tools and "holster_weapon" in tools


# --- mode gate END-TO-END (the issue's core proof) -------------------------

def _cap(status: dict) -> tuple[KeybindCapability, _FakeExecutor]:
    """A capability with EVERY on-foot macro allowlisted, driven by `status`."""
    ex = _FakeExecutor()
    binds = {a: KeyBinding(action=a, key="Key_X") for a in _EXPECTED.values()}
    cfg = KeybindConfig(enabled=True, require_confirmation=True, combat_guard=True,
                        mode_guard=True, allowlist=tuple(_EXPECTED))
    cap = KeybindCapability(binds=binds, executor=ex, config=cfg,
                            status_snapshot=(lambda: status))
    return cap, ex


def test_on_foot_actions_hidden_while_in_main_ship():
    cap, _ = _cap(_safe("mainship"))
    advertised = {t["name"] for t in cap.tools()}
    for name, _action in _EXPECTED.items():
        tool = registered_macros()[name].tool
        assert tool not in advertised, f"{tool} must not be offered while flying"
    # confirm/abort are always present regardless of mode.
    assert {"confirm_keybind", "abort_keybinds"} <= advertised


def test_on_foot_actions_advertised_on_foot():
    cap, _ = _cap(_safe("on_foot"))
    advertised = {t["name"] for t in cap.tools()}
    for name in _EXPECTED:
        tool = registered_macros()[name].tool
        assert tool in advertised, f"{tool} should be offered on foot"


def test_arm_refused_in_main_ship():
    cap, ex = _cap(_safe("mainship"))
    msg = cap.run_tool("toggle_flashlight", {})
    assert ex.pressed == []                       # nothing fired out of mode
    assert "in your ship" in msg.lower() or "only works" in msg.lower()


def test_benign_action_fires_immediately_on_foot():
    # confirm_required=False -> a single call fires (no separate confirmation), on foot + safe.
    cap, ex = _cap(_safe("on_foot"))
    msg = cap.run_tool("toggle_flashlight", {})
    assert ex.pressed == ["Key_X"]
    assert "flashlight toggled" in msg.lower()


def test_combat_guard_still_blocks_on_foot():
    # Benign doesn't mean unguarded: danger on foot still refuses.
    cap, ex = _cap({"in_danger": True, "being_interdicted": False, "game_mode": "on_foot"})
    msg = cap.run_tool("toggle_night_vision", {})
    assert ex.pressed == []
    assert "danger" in msg.lower() or "combat" in msg.lower()
