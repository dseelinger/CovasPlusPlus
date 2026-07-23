"""Flight / navigation action batch (issue #30) — Tier-1 flight controls.

The first *new* action batch on the #29 registry seam: throttle, FSD/supercruise/hyperspace,
flight assist, target cycling, next-route-system target, and nav-lock. Adding it is exactly a
new module + one import line in `actions/__init__.py` — `KeybindCapability` is untouched, and
each macro is still gated by the allowlist + combat guard + its own `modes`/`confirm_required`.

Two policy calls run through every macro below:
  * `modes` — throttle/target actions also apply in a ship-launched FIGHTER (it flies and
    targets); FSD/supercruise/hyperspace/route/nav-lock are MAIN-SHIP-only (a fighter can't
    jump or plot a route).
  * `confirm_required` — TRUE for the consequential/handling-changing actions (starting a jump,
    engaging supercruise, flipping flight assist) so they arm-and-confirm; FALSE for the benign,
    repeatable ones (throttle set, target cycling, nav-lock toggle) so they fire immediately —
    still behind the allowlist + combat + mode guards.

Action tokens are the real Elite Dangerous `.binds` element names (verified against the ED
binding spec). None are in the DEFAULT allowlist — a Commander opts each in via
`[keybinds].allowlist`; an unbound token degrades to a spoken "bind it in-game" message.
"""
from __future__ import annotations

from ...ed.modes import MODE_FIGHTER, MODE_MAINSHIP
from ..registry import Macro, register

_SHIP = frozenset({MODE_MAINSHIP})
_SHIP_OR_FIGHTER = frozenset({MODE_MAINSHIP, MODE_FIGHTER})

# ---- throttle (benign, repeatable — fire immediately) -------------------------------------
register(Macro(
    name="throttle_zero",
    tool="set_throttle_zero",
    action="SetSpeedZero",
    arm_phrase="set the throttle to zero",
    done_phrase="Throttle at zero",
    modes=_SHIP_OR_FIGHTER,
    confirm_required=False,
))
register(Macro(
    name="throttle_50",
    tool="set_throttle_50",
    action="SetSpeed50",
    arm_phrase="set the throttle to 50 percent",
    done_phrase="Throttle at 50 percent",
    modes=_SHIP_OR_FIGHTER,
    confirm_required=False,
))
register(Macro(
    name="throttle_100",
    tool="set_throttle_100",
    action="SetSpeed100",
    arm_phrase="set the throttle to full",
    done_phrase="Throttle at full",
    modes=_SHIP_OR_FIGHTER,
    confirm_required=False,
))

# ---- FSD / supercruise / hyperspace (consequential — arm-and-confirm) ---------------------
register(Macro(
    name="frame_shift_drive",
    tool="engage_frame_shift_drive",
    action="HyperSuperCombination",
    arm_phrase="engage the frame shift drive (supercruise, or jump if a system is targeted)",
    done_phrase="Frame shift drive engaging",
    modes=_SHIP,
    confirm_required=True,   # starts a jump/supercruise charge — consequential
))
register(Macro(
    name="supercruise",
    tool="engage_supercruise",
    action="Supercruise",
    arm_phrase="engage supercruise",
    done_phrase="Supercruise engaging",
    modes=_SHIP,
    confirm_required=True,
))
register(Macro(
    name="hyperspace",
    tool="jump_to_hyperspace",
    action="Hyperspace",
    arm_phrase="jump to hyperspace on the targeted system",
    done_phrase="Hyperspace jump engaging",
    modes=_SHIP,
    confirm_required=True,
))

# ---- flight assist (handling-changing — arm-and-confirm) ----------------------------------
register(Macro(
    name="flight_assist",
    tool="toggle_flight_assist",
    action="ToggleFlightAssist",
    arm_phrase="toggle flight assist",
    done_phrase="Flight assist toggled",
    modes=_SHIP_OR_FIGHTER,
    confirm_required=True,   # flipping FA mid-flight materially changes handling
))

# ---- targeting / route (benign, repeatable — fire immediately) ----------------------------
register(Macro(
    name="select_target_ahead",
    tool="select_target_ahead",
    action="SelectTarget",
    arm_phrase="target the ship directly ahead",
    done_phrase="Targeting ship ahead",
    modes=_SHIP_OR_FIGHTER,
    confirm_required=False,
))
register(Macro(
    name="cycle_next_target",
    tool="cycle_next_target",
    action="CycleNextTarget",
    arm_phrase="cycle to the next target",
    done_phrase="Next target selected",
    modes=_SHIP_OR_FIGHTER,
    confirm_required=False,
))
register(Macro(
    name="cycle_previous_target",
    tool="cycle_previous_target",
    action="CyclePreviousTarget",
    arm_phrase="cycle to the previous target",
    done_phrase="Previous target selected",
    modes=_SHIP_OR_FIGHTER,
    confirm_required=False,
))
register(Macro(
    name="target_next_route_system",
    tool="target_next_route_system",
    action="TargetNextRouteSystem",
    arm_phrase="target the next system in your route",
    done_phrase="Next route system targeted",
    modes=_SHIP,           # route/jump is a main-ship concept
    confirm_required=False,
))

# ---- nav lock (benign toggle — fire immediately) ------------------------------------------
register(Macro(
    name="nav_lock",
    tool="toggle_nav_lock",
    action="WingNavLock",
    arm_phrase="toggle nav lock",
    done_phrase="Nav lock toggled",
    modes=_SHIP,
    confirm_required=False,
))
