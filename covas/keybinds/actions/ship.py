"""Main-ship action batch (issue #29).

The prototype ships exactly ONE macro — toggle landing gear — proving the mode-gated,
arm-and-confirm path end-to-end. Generalizing = adding macros here (or a sibling batch
module), each gated by the allowlist, the combat guard, and its own `modes`/`confirm_required`
policy. No edit to `KeybindCapability` is needed to add an action.
"""
from __future__ import annotations

from ..registry import Macro, register
from ...ed.modes import MODE_MAINSHIP

register(Macro(
    name="landing_gear",
    tool="toggle_landing_gear",
    action="LandingGearToggle",
    arm_phrase="toggle the landing gear (deploy if up, retract if down)",
    done_phrase="Landing gear toggled",
    modes=frozenset({MODE_MAINSHIP}),   # only meaningful in the main ship
    confirm_required=True,              # consequential — arm-and-confirm
))
