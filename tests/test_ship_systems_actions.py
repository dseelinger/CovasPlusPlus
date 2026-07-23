"""Unit tests for the Tier-1 ship-systems action batch (issue #31).

Two concerns, both offline/hermetic:
  1. Registration — the batch registered the expected macros with the right token/mode/confirm
     policy (a data assertion, no capability involved).
  2. Behaviour through the guards — a representative macro (cargo scoop) arms/fires correctly
     via `KeybindCapability` with a recording fake executor: benign macros fire immediately,
     stay allowlist-gated, mode-gated, and combat-gated.
"""
from __future__ import annotations

import pytest

from covas.capabilities.keybind_capability import KeybindCapability, KeybindConfig
from covas.ed.modes import MODE_MAINSHIP
from covas.keybinds.binds import KeyBinding
from covas.keybinds.registry import registered_macros

# The macros this batch is contracted to ship: name -> (tool, ED action token).
_EXPECTED = {
    "cargo_scoop":  ("toggle_cargo_scoop", "ToggleCargoScoop"),
    "night_vision": ("toggle_night_vision", "NightVisionToggle"),
    "ship_lights":  ("toggle_ship_lights", "ShipSpotLightToggle"),
    "hud_mode":     ("toggle_hud_mode", "PlayerHUDModeToggle"),
    "pips_engines": ("pips_to_engines", "IncreaseEnginesPower"),
    "pips_weapons": ("pips_to_weapons", "IncreaseWeaponsPower"),
    "pips_systems": ("pips_to_systems", "IncreaseSystemsPower"),
    "pips_balance": ("balance_pips", "ResetPowerDistribution"),
}


# --- registration ----------------------------------------------------------

@pytest.mark.parametrize("name,tool,token", [(n, t, a) for n, (t, a) in _EXPECTED.items()])
def test_batch_registers_macro(name, tool, token):
    # Importing the capability (module import above) populates the registry via keybinds.actions.
    m = registered_macros().get(name)
    assert m is not None, f"{name} not registered — is ship_systems imported in actions/__init__?"
    assert m.tool == tool
    assert m.action == token


def test_all_macros_mainship_only_and_benign():
    macros = registered_macros()
    for name in _EXPECTED:
        m = macros[name]
        assert m.modes == frozenset({MODE_MAINSHIP}), f"{name} should be main-ship only"
        assert m.confirm_required is False, f"{name} is a benign toggle — no confirmation"
        assert m.kind == "press"          # single tap, not a hold


def test_does_not_clobber_landing_gear():
    # The prototype macro from ship.py must still be present and consequential.
    lg = registered_macros().get("landing_gear")
    assert lg is not None and lg.confirm_required is True


def test_docking_request_absent():
    # Docking request is a panel action with no direct keybind — deliberately not in this batch.
    assert "docking_request" not in registered_macros()


# --- behaviour through the guards (representative: cargo scoop) -------------

class _FakeExecutor:
    def __init__(self) -> None:
        self.pressed: list[str] = []
        self.held: list[tuple[str, float]] = []

    def press(self, binding) -> None:
        self.pressed.append(binding.key)

    def hold(self, binding, seconds) -> None:
        self.held.append((binding.key, seconds))

    def release_all(self) -> None:
        pass


_SCOOP = {"ToggleCargoScoop": KeyBinding(action="ToggleCargoScoop", key="Key_Home")}


def _safe(mode: str | None) -> dict:
    return {"in_danger": False, "being_interdicted": False, "game_mode": mode}


def _cap(*, status, allowlist=("cargo_scoop",)):
    ex = _FakeExecutor()
    cap = KeybindCapability(
        binds=_SCOOP, executor=ex,
        config=KeybindConfig(enabled=True, allowlist=allowlist),
        status_snapshot=(lambda: status))
    return cap, ex


def test_benign_macro_fires_immediately_in_mainship():
    # confirm_required=False -> no arm/confirm dance; fires on the single tool call.
    cap, ex = _cap(status=_safe("mainship"))
    msg = cap.run_tool("toggle_cargo_scoop", {})
    assert ex.pressed == ["Key_Home"]
    assert "Key_Home" in msg


def test_advertised_only_when_allowlisted():
    # Not in the allowlist -> not advertised and refused if called directly.
    cap, ex = _cap(status=_safe("mainship"), allowlist=())
    assert "toggle_cargo_scoop" not in {t["name"] for t in cap.tools()}
    assert "disallowed" in cap.run_tool("toggle_cargo_scoop", {}).lower()
    assert ex.pressed == []


def test_mode_gated_out_of_mainship():
    # On foot the ship-systems toggle isn't offered and won't fire.
    cap, ex = _cap(status=_safe("on_foot"))
    assert "toggle_cargo_scoop" not in {t["name"] for t in cap.tools()}
    msg = cap.run_tool("toggle_cargo_scoop", {})
    assert ex.pressed == []
    assert "on foot" in msg.lower()


def test_combat_guard_blocks_benign_toggle():
    # Even a benign toggle is refused mid-interdiction.
    cap, ex = _cap(status={"being_interdicted": True, "game_mode": "mainship"})
    msg = cap.run_tool("toggle_cargo_scoop", {})
    assert ex.pressed == []
    assert "interdict" in msg.lower()
