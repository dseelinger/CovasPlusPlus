"""SRV / surface-buggy action batch (issue #35, "Tier-1 actions: SRV action group").

The first *multi-macro* batch on the #29 registry seam: the useful, non-combat SRV controls,
each gated to `MODE_SRV` so they're offered ONLY while the Commander is driving the buggy —
never while flying the main ship or on foot. No edit to `KeybindCapability` was needed; this
module registers itself when `keybinds.actions` is imported.

Beats-competitors: EDCoPilot/COVAS:NEXT can *narrate* the SRV but don't press its controls;
COVAS++ toggles drive-assist/headlights/night-vision/cargo-scoop/auto-brake and recalls your
ship hands-free — behind the same allowlist + confirmation + mode + combat guards.

Action tokens are the ED `.binds` XML element names for the buggy (verified against the ED
bindings spec — note ED's own misspelling `AutoBreakBuggyButton`). Combat controls
(BuggyPrimaryFireButton / BuggySecondaryFireButton / the turret) are deliberately left OUT.

Confirmation policy:
  * Benign in-cockpit toggles fire immediately (`confirm_required=False`) — they only flip a
    convenience state (lights, assists, scoop) with no way to hurt the Commander.
  * `recall_ship` is disruptive (summons/dismisses the mothership) so it arms-and-confirms
    (`confirm_required=True`), like every consequential macro.

None of these are in the DEFAULT `[keybinds].allowlist` (landing_gear only) — the Commander
opts each one in by name, same as the ship batch.
"""
from __future__ import annotations

from ..registry import Macro, register
from ...ed.modes import MODE_SRV

_SRV = frozenset({MODE_SRV})   # every SRV macro is valid ONLY while driving the buggy

# --- benign convenience toggles (fire immediately, still behind allowlist + guards) --------

register(Macro(
    name="drive_assist",
    tool="toggle_drive_assist",
    action="ToggleDriveAssist",
    arm_phrase="toggle the SRV drive assist",
    done_phrase="Drive assist toggled",
    modes=_SRV,
    confirm_required=False,
))

register(Macro(
    name="srv_headlights",
    tool="srv_headlights",
    action="HeadlightsBuggyButton",
    arm_phrase="toggle the SRV headlights",
    done_phrase="Headlights toggled",
    modes=_SRV,
    confirm_required=False,
))

register(Macro(
    name="srv_night_vision",
    tool="srv_night_vision",
    action="NightVisionToggle_Buggy",
    arm_phrase="toggle the SRV night vision",
    done_phrase="Night vision toggled",
    modes=_SRV,
    confirm_required=False,
))

register(Macro(
    name="srv_cargo_scoop",
    tool="srv_cargo_scoop",
    action="ToggleCargoScoop_Buggy",
    arm_phrase="toggle the SRV cargo scoop",
    done_phrase="Cargo scoop toggled",
    modes=_SRV,
    confirm_required=False,
))

register(Macro(
    name="srv_auto_brake",
    tool="srv_auto_brake",
    action="AutoBreakBuggyButton",   # ED's own spelling ("Break") — do not "fix" it
    arm_phrase="toggle the SRV auto-brake",
    done_phrase="Auto-brake toggled",
    modes=_SRV,
    confirm_required=False,
))

# --- consequential: summon/dismiss the mothership (arm-and-confirm) -------------------------

register(Macro(
    name="recall_ship",
    tool="recall_ship",
    action="RecallDismissShip",
    arm_phrase="recall or dismiss your ship",
    done_phrase="Ship recall/dismiss sent",
    modes=_SRV,
    confirm_required=True,           # disruptive — summons/dismisses the ship
))
