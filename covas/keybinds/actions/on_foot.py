"""Odyssey on-foot action batch (Tier-1, issue #34).

The first NON-ship action batch on the #29 registry seam — proof that mode-gating pays off:
every macro here declares `modes={MODE_ON_FOOT}`, so the model is offered these actions ONLY
while the Commander is disembarked (Odyssey Flags2 OnFoot), and never while flying. Adding the
batch was a new module + one import line in `actions/__init__.py` — no edit to
`KeybindCapability`.

Scope is deliberately benign + utility-focused: flashlight / night-vision toggles, weapon
SELECT + holster (draws/holsters — never *fires*), the three suit-tool switches, crouch, and
the galaxy map. Firing a weapon and throwing a grenade are intentionally OUT — combat actions
don't belong behind a voice command. Because these are benign, each sets `confirm_required=False`
so it fires immediately (still behind the allowlist + combat + mode guards); none is in the
default allowlist, so nothing here runs until a Commander opts each macro in by name.

Action tokens are the Odyssey Humanoid* / *_Humanoid bindings verified against a real Odyssey
`.binds` file (note the token is `HumanoidToggleNightVisionButton`, not *NightVisionToggle*).
"""
from __future__ import annotations

from ...ed.modes import MODE_ON_FOOT
from ..registry import Macro, register

# Every on-foot macro is valid in exactly this mode — the gate that hides them while flying.
_ON_FOOT = frozenset({MODE_ON_FOOT})


def _foot(name: str, tool: str, action: str, arm_phrase: str, done_phrase: str) -> Macro:
    """A benign on-foot macro: mode-gated to on-foot, fires immediately (no arm/confirm).
    Benign here means it changes the Commander's own suit/kit state — a toggle or a
    selection — and can NEVER fire a weapon or throw a grenade (those aren't registered)."""
    return register(Macro(
        name=name, tool=tool, action=action,
        arm_phrase=arm_phrase, done_phrase=done_phrase,
        modes=_ON_FOOT,
        confirm_required=False,   # benign; still gated by allowlist + combat + mode guards
    ))


# -- suit toggles -------------------------------------------------------------------------
_foot("on_foot_flashlight", "toggle_flashlight", "HumanoidToggleFlashlightButton",
      "toggle your suit flashlight", "Flashlight toggled")
_foot("on_foot_night_vision", "toggle_night_vision", "HumanoidToggleNightVisionButton",
      "toggle your suit night vision", "Night vision toggled")

# -- weapon select + holster (draw/holster only — never fires) ----------------------------
_foot("on_foot_select_primary", "select_primary_weapon", "HumanoidSelectPrimaryWeaponButton",
      "draw your primary weapon", "Primary weapon selected")
_foot("on_foot_select_secondary", "select_secondary_weapon",
      "HumanoidSelectSecondaryWeaponButton",
      "draw your secondary weapon", "Secondary weapon selected")
_foot("on_foot_select_utility", "select_utility_weapon", "HumanoidSelectUtilityWeaponButton",
      "draw your utility weapon", "Utility weapon selected")
_foot("on_foot_holster", "holster_weapon", "HumanoidHideWeaponButton",
      "holster your weapon", "Weapon holstered")

# -- suit tools (energy link / profile analyser / suit tool) ------------------------------
_foot("on_foot_energylink", "switch_to_energylink", "HumanoidSwitchToRechargeTool",
      "switch to your energy link (recharge tool)", "Energy link selected")
_foot("on_foot_profile_analyser", "switch_to_profile_analyser", "HumanoidSwitchToCompAnalyser",
      "switch to your profile analyser", "Profile analyser selected")
_foot("on_foot_suit_tool", "switch_to_suit_tool", "HumanoidSwitchToSuitTool",
      "switch to your suit tool", "Suit tool selected")

# -- movement + map (benign) --------------------------------------------------------------
_foot("on_foot_crouch", "toggle_crouch", "HumanoidCrouchButton",
      "toggle crouch", "Crouch toggled")
_foot("on_foot_galaxy_map", "open_galaxy_map_on_foot", "GalaxyMapOpen_Humanoid",
      "open the galaxy map", "Galaxy map opened")
