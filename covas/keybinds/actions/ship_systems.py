"""Ship-systems action batch (issue #31) — Tier-1 benign toggles the companion can press.

The second action batch after the `ship.py` prototype (landing gear). Everything here is a
single-tap, **repeatable and harmless** ship-systems control — cargo scoop, night vision,
external lights, HUD combat/analysis mode, and the power-pip distribution. None of it is
consequential (no weapons, no hardpoints, no jump), so each macro sets `confirm_required=False`:
it may fire immediately, still behind the allowlist + combat + mode guards. Deploying the
landing gear lives in `ship.py`; docking request is a panel action with no direct keybind
(handled by a later panel batch / #32), so it's deliberately absent here.

Adding this batch is a new module + one import in `actions/__init__.py` — no edit to
`KeybindCapability` (the #29 registry seam). All macros are main-ship only. The `action` tokens
are the canonical Elite Dangerous .binds names (verify against your `Custom.4.0.binds`).

None are allowlisted by default — `[keybinds].allowlist` still ships just `landing_gear`, so
default behaviour is unchanged. A Commander opts a macro in by adding its NAME to that list.
"""
from __future__ import annotations

from ...ed.modes import MODE_MAINSHIP
from ..registry import Macro, register

# Every macro here is a benign, repeatable main-ship toggle/tap — fire-immediately (no arm),
# main-ship mode only. `name` is the allowlist key; `action` is the ED .binds token.
_MAINSHIP = frozenset({MODE_MAINSHIP})

# -- utility systems ------------------------------------------------------------------------

register(Macro(
    name="cargo_scoop",
    tool="toggle_cargo_scoop",
    action="ToggleCargoScoop",            # deploy/retract the cargo scoop
    arm_phrase="toggle the cargo scoop",
    done_phrase="Cargo scoop toggled",
    modes=_MAINSHIP,
    confirm_required=False,               # benign, repeatable
))

register(Macro(
    name="night_vision",
    tool="toggle_night_vision",
    action="NightVisionToggle",           # low-light vision overlay
    arm_phrase="toggle night vision",
    done_phrase="Night vision toggled",
    modes=_MAINSHIP,
    confirm_required=False,
))

register(Macro(
    name="ship_lights",
    tool="toggle_ship_lights",
    action="ShipSpotLightToggle",         # external ship floodlights
    arm_phrase="toggle the ship lights",
    done_phrase="Ship lights toggled",
    modes=_MAINSHIP,
    confirm_required=False,
))

register(Macro(
    name="hud_mode",
    tool="toggle_hud_mode",
    action="PlayerHUDModeToggle",         # combat <-> analysis HUD mode
    arm_phrase="switch the HUD between combat and analysis mode",
    done_phrase="HUD mode toggled",
    modes=_MAINSHIP,
    confirm_required=False,
))

# -- power distribution (pips) --------------------------------------------------------------
# Each tap moves one pip toward a system; ResetPowerDistribution balances back to 2/2/2. All
# benign and repeatable — the Commander says "pips to engines" a few times to fill that bank.

register(Macro(
    name="pips_engines",
    tool="pips_to_engines",
    action="IncreaseEnginesPower",        # +1 pip to ENG
    arm_phrase="put a power pip into engines",
    done_phrase="Pip to engines",
    modes=_MAINSHIP,
    confirm_required=False,
))

register(Macro(
    name="pips_weapons",
    tool="pips_to_weapons",
    action="IncreaseWeaponsPower",        # +1 pip to WEP
    arm_phrase="put a power pip into weapons",
    done_phrase="Pip to weapons",
    modes=_MAINSHIP,
    confirm_required=False,
))

register(Macro(
    name="pips_systems",
    tool="pips_to_systems",
    action="IncreaseSystemsPower",        # +1 pip to SYS
    arm_phrase="put a power pip into systems",
    done_phrase="Pip to systems",
    modes=_MAINSHIP,
    confirm_required=False,
))

register(Macro(
    name="pips_balance",
    tool="balance_pips",
    action="ResetPowerDistribution",      # reset to an even 2/2/2 distribution
    arm_phrase="balance the power pips evenly",
    done_phrase="Power pips balanced",
    modes=_MAINSHIP,
    confirm_required=False,
))
