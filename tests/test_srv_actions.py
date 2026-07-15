"""Unit tests for the SRV / buggy action batch (issue #35).

Two things to prove, offline and hermetic:
  1. Registration — every SRV macro is registered, gated to MODE_SRV only, with the right
     confirm policy (benign toggles fire immediately; recall_ship arms-and-confirms).
  2. Mode gating end-to-end — driving a KeybindCapability with a game_mode snapshot, the SRV
     macros are advertised in "srv" mode but NOT in main-ship mode (nor while on foot).

These import the batch through the registry / capability, same as production wiring.
"""
from __future__ import annotations

from covas.keybinds.binds import KeyBinding
from covas.keybinds.registry import registered_macros
from covas.ed.modes import MODE_SRV
from covas.capabilities.keybind_capability import KeybindCapability, KeybindConfig

# The macros this batch ships: name -> (ED action token, confirm_required).
_SRV_MACROS = {
    "drive_assist":     ("ToggleDriveAssist",         False),
    "srv_headlights":   ("HeadlightsBuggyButton",     False),
    "srv_night_vision": ("NightVisionToggle_Buggy",   False),
    "srv_cargo_scoop":  ("ToggleCargoScoop_Buggy",    False),
    "srv_auto_brake":   ("AutoBreakBuggyButton",      False),
    "recall_ship":      ("RecallDismissShip",         True),
}


# --- registration ----------------------------------------------------------

def test_all_srv_macros_registered_srv_only():
    reg = registered_macros()
    for name, (action, confirm) in _SRV_MACROS.items():
        assert name in reg, f"{name} not registered"
        m = reg[name]
        assert m.action == action                       # correct ED .binds token
        assert m.modes == frozenset({MODE_SRV})         # gated to the SRV only
        assert m.confirm_required is confirm            # per-action confirm policy


def test_recall_ship_requires_confirmation():
    # The one disruptive action (summons/dismisses the ship) must arm-and-confirm.
    assert registered_macros()["recall_ship"].confirm_required is True


def test_benign_toggles_do_not_require_confirmation():
    reg = registered_macros()
    for name in ("drive_assist", "srv_headlights", "srv_night_vision",
                 "srv_cargo_scoop", "srv_auto_brake"):
        assert reg[name].confirm_required is False


# --- mode gating through the capability ------------------------------------

def _binds_for_srv() -> dict[str, KeyBinding]:
    """A keyboard binding for every SRV action token, so nothing is filtered as unusable."""
    return {action: KeyBinding(action=action, key="Key_X")
            for action, _ in _SRV_MACROS.values()}


def _cap(mode: str | None):
    """A capability allowlisting every SRV macro, with a safe status snapshot in `mode`."""
    cfg = KeybindConfig(enabled=True, allowlist=tuple(_SRV_MACROS))
    snap = {"in_danger": False, "being_interdicted": False, "game_mode": mode}
    return KeybindCapability(binds=_binds_for_srv(), executor=object(), config=cfg,
                             status_snapshot=(lambda: snap))


def _srv_tool_names():
    return {m.tool for m in registered_macros().values() if m.name in _SRV_MACROS}


def test_srv_actions_advertised_in_srv_mode():
    names = {t["name"] for t in _cap("srv").tools()}
    assert _srv_tool_names() <= names                   # all SRV tools offered while driving


def test_srv_actions_hidden_in_mainship_mode():
    names = {t["name"] for t in _cap("mainship").tools()}
    assert not (_srv_tool_names() & names)              # none offered while flying the ship
    assert {"confirm_keybind", "abort_keybinds"} <= names  # confirm/abort always present


def test_srv_actions_hidden_on_foot():
    names = {t["name"] for t in _cap("on_foot").tools()}
    assert not (_srv_tool_names() & names)


def test_arm_recall_ship_in_srv_arms_but_does_not_fire():
    # In SRV mode, arming recall_ship confirms (doesn't fire) — proves the confirm path.
    class _Rec:
        def __init__(self): self.pressed = []
        def press(self, b): self.pressed.append(b.key)
    ex = _Rec()
    cfg = KeybindConfig(enabled=True, allowlist=tuple(_SRV_MACROS))
    snap = {"in_danger": False, "being_interdicted": False, "game_mode": "srv"}
    cap = KeybindCapability(binds=_binds_for_srv(), executor=ex, config=cfg,
                            status_snapshot=(lambda: snap))
    msg = cap.run_tool("recall_ship", {})
    assert ex.pressed == []                              # armed, not fired
    assert "confirm" in msg.lower()


def test_benign_toggle_out_of_mode_is_refused():
    # A benign toggle (confirm_required=False) is still refused outside the SRV.
    class _Rec:
        def __init__(self): self.pressed = []
        def press(self, b): self.pressed.append(b.key)
    ex = _Rec()
    cfg = KeybindConfig(enabled=True, allowlist=tuple(_SRV_MACROS))
    snap = {"in_danger": False, "being_interdicted": False, "game_mode": "mainship"}
    cap = KeybindCapability(binds=_binds_for_srv(), executor=ex, config=cfg,
                            status_snapshot=(lambda: snap))
    msg = cap.run_tool("srv_headlights", {})
    assert ex.pressed == []
    assert "srv" in msg.lower() or "in the srv" in msg.lower()
