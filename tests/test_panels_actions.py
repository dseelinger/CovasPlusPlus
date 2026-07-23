"""Unit tests for the Tier-1 panels/maps/fire-group action batch (issue #32).

Offline and hermetic (DESIGN §9): asserts the batch REGISTERS with the right tokens, modes,
and confirm policy, plus a representative arm/execute through `KeybindCapability` behind the
same fake-executor + injected-status style as `test_keybind_capability.py`. Because these
macros are benign (`confirm_required=False`) they fire immediately once allowlisted and in the
right mode — but are still refused out of mode / during combat.
"""
from __future__ import annotations

import covas.keybinds.actions.panels  # noqa: F401 — ensure the batch is imported/registered
from covas.capabilities.keybind_capability import KeybindCapability, KeybindConfig
from covas.ed.modes import MODE_FIGHTER, MODE_MAINSHIP
from covas.keybinds.binds import KeyBinding
from covas.keybinds.registry import registered_macros

# Expected (macro name -> ED action token) for the whole batch.
_EXPECTED_TOKENS = {
    "focus_left_panel": "FocusLeftPanel",
    "focus_right_panel": "FocusRightPanel",
    "focus_comms_panel": "FocusCommsPanel",
    "focus_role_panel": "FocusRadarPanel",
    "quick_comms": "QuickCommsPanel",
    "open_galaxy_map": "GalaxyMapOpen",
    "open_system_map": "SystemMapOpen",
    "cycle_fire_group_next": "CycleFireGroupNext",
    "cycle_fire_group_previous": "CycleFireGroupPrevious",
    "ui_back": "UI_Back",
    "ui_focus": "UIFocus",
    "toggle_headlook": "HeadLookToggle",
}
_FIGHTER_TOO = {"cycle_fire_group_next", "cycle_fire_group_previous"}


class _FakeExecutor:
    def __init__(self) -> None:
        self.pressed: list[str] = []
        self.held: list[tuple[str, float]] = []

    def press(self, binding) -> None:
        self.pressed.append(binding.key)

    def hold(self, binding, seconds) -> None:
        self.held.append((binding.key, seconds))

    def release_all(self) -> None:  # pragma: no cover - unused here
        pass


def _safe(mode: str | None) -> dict:
    return {"in_danger": False, "being_interdicted": False, "game_mode": mode}


# --- registration -----------------------------------------------------------

def test_all_macros_registered_with_correct_tokens():
    reg = registered_macros()
    for name, token in _EXPECTED_TOKENS.items():
        assert name in reg, f"{name} not registered"
        assert reg[name].action == token
        assert reg[name].tool == name


def test_all_macros_are_benign_no_confirm():
    reg = registered_macros()
    for name in _EXPECTED_TOKENS:
        assert reg[name].confirm_required is False, f"{name} should not require confirmation"
        assert reg[name].kind == "press"


def test_modes_mainship_except_fire_groups_also_fighter():
    reg = registered_macros()
    for name in _EXPECTED_TOKENS:
        modes = reg[name].modes
        assert MODE_MAINSHIP in modes
        if name in _FIGHTER_TOO:
            assert MODE_FIGHTER in modes
        else:
            assert MODE_FIGHTER not in modes


# --- behaviour behind the guards (representative) ---------------------------

def _cap(*, allow, binds, status):
    ex = _FakeExecutor()
    cap = KeybindCapability(
        binds=binds, executor=ex,
        config=KeybindConfig(enabled=True, allowlist=allow),
        status_snapshot=(lambda: status))
    return cap, ex


def test_open_galaxy_map_fires_immediately_in_mainship():
    binds = {"GalaxyMapOpen": KeyBinding(action="GalaxyMapOpen", key="Key_M")}
    cap, ex = _cap(allow=("open_galaxy_map",), binds=binds, status=_safe(MODE_MAINSHIP))
    # benign macro: no arm/confirm step — a single call presses the key.
    msg = cap.run_tool("open_galaxy_map", {})
    assert ex.pressed == ["Key_M"]
    assert "Key_M" in msg


def test_fire_group_next_allowed_in_fighter():
    binds = {"CycleFireGroupNext": KeyBinding(action="CycleFireGroupNext", key="Key_N")}
    cap, ex = _cap(allow=("cycle_fire_group_next",), binds=binds, status=_safe(MODE_FIGHTER))
    cap.run_tool("cycle_fire_group_next", {})
    assert ex.pressed == ["Key_N"]


def test_panel_refused_out_of_mode():
    binds = {"FocusLeftPanel": KeyBinding(action="FocusLeftPanel", key="Key_1")}
    cap, ex = _cap(allow=("focus_left_panel",), binds=binds, status=_safe("on_foot"))
    msg = cap.run_tool("focus_left_panel", {})
    assert ex.pressed == []
    assert "on foot" in msg.lower()


def test_batch_not_in_default_allowlist():
    # These macros must NOT ship in the default allowlist (landing_gear only) — opt-in per DoD.
    assert KeybindConfig().allowlist == ("landing_gear",)


def test_combat_guard_blocks_benign_action():
    binds = {"GalaxyMapOpen": KeyBinding(action="GalaxyMapOpen", key="Key_M")}
    cap, ex = _cap(allow=("open_galaxy_map",), binds=binds,
                   status={"being_interdicted": True, "game_mode": MODE_MAINSHIP})
    msg = cap.run_tool("open_galaxy_map", {})
    assert ex.pressed == []
    assert "interdict" in msg.lower()
