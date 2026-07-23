"""Tier-1 action batch — UI panels, maps, and fire groups (issue #32).

The first *real* action batch on the #29 registry seam: a dozen benign, repeatable cockpit
actions (open a panel, open the galaxy/system map, cycle the fire group, step back in the UI,
toggle head-look). None of them change ship state the way landing gear does — opening a panel
or cycling a fire group is harmless and instantly reversible — so every macro here sets
`confirm_required=False` and fires immediately. They are still fully behind the safety layer:
the allowlist (nothing runs unless the Commander adds its name to `[keybinds].allowlist`), the
combat/interdiction guard, and mode-gating.

Adding this batch is a NEW MODULE + one import line in `actions/__init__.py` — no edit to
`KeybindCapability` (the #29 registry lever working as designed).

Modes (mostly main-ship this tier; on-foot/SRV panel + map variants are deferred to #34/#35):
  * panels, maps, UI navigation, head-look — MODE_MAINSHIP.
  * fire-group cycling — MODE_MAINSHIP + MODE_FIGHTER (a deployed fighter has its own fire
    groups; the token is identical).

Action tokens below are the canonical Elite Dangerous `.binds` control names (verified against
the ED bindings spec). `GalaxyMapOpen`/`SystemMapOpen` are the *main-ship* map controls — ED
uses separate `_Humanoid`/SRV variants on foot and in the SRV, which belong to #34/#35.

set_course handoff (issue #41): `open_galaxy_map` is the FIRST half of an in-game "set course".
`search/routes.py::RoutePlotter` already accepts an injected `set_course(system) -> bool` and
falls back to the clipboard until it's wired. A future `set_course` will build on THIS macro —
open the galaxy map, type the destination into the map search, and select it — closing the plot
loop. We intentionally don't wire that cross-cutting closed loop here (just leave the building
block in place); see the report / issue #41 for the seam.
"""
from __future__ import annotations

from ...ed.modes import MODE_FIGHTER, MODE_MAINSHIP
from ..registry import Macro, register

# Panels + maps + UI navigation + head-look are meaningful only while flying the main ship.
_MAINSHIP = frozenset({MODE_MAINSHIP})
# Fire-group cycling also applies in a deployed ship-launched fighter (same control token).
_SHIP_OR_FIGHTER = frozenset({MODE_MAINSHIP, MODE_FIGHTER})


def _panel(name: str, tool: str, action: str, opens: str) -> Macro:
    """A benign 'focus a cockpit panel / open a map' macro: fires immediately (no confirm),
    main-ship only. `opens` is the spoken description of what it brings up."""
    return Macro(
        name=name,
        tool=tool,
        action=action,
        arm_phrase=f"open the {opens}",
        done_phrase=f"{opens[0].upper()}{opens[1:]} open",
        modes=_MAINSHIP,
        confirm_required=False,   # benign + repeatable — behind allowlist + combat + mode guards
    )


# ---- cockpit panels (the four HUD panels) -------------------------------------------------
register(_panel("focus_left_panel", "focus_left_panel", "FocusLeftPanel",
                "left (navigation) panel"))
register(_panel("focus_right_panel", "focus_right_panel", "FocusRightPanel",
                "right (systems) panel"))
register(_panel("focus_comms_panel", "focus_comms_panel", "FocusCommsPanel",
                "comms panel"))
register(_panel("focus_role_panel", "focus_role_panel", "FocusRadarPanel",
                "role panel"))
register(_panel("quick_comms", "quick_comms", "QuickCommsPanel",
                "quick comms"))

# ---- maps ---------------------------------------------------------------------------------
# open_galaxy_map: also the building block a future set_course handoff builds on (see #41 note).
register(_panel("open_galaxy_map", "open_galaxy_map", "GalaxyMapOpen",
                "galaxy map"))
register(_panel("open_system_map", "open_system_map", "SystemMapOpen",
                "system map"))

# ---- fire groups (main ship or deployed fighter) ------------------------------------------
register(Macro(
    name="cycle_fire_group_next",
    tool="cycle_fire_group_next",
    action="CycleFireGroupNext",
    arm_phrase="cycle to the next fire group",
    done_phrase="Cycled to the next fire group",
    modes=_SHIP_OR_FIGHTER,
    confirm_required=False,
))
register(Macro(
    name="cycle_fire_group_previous",
    tool="cycle_fire_group_previous",
    action="CycleFireGroupPrevious",
    arm_phrase="cycle to the previous fire group",
    done_phrase="Cycled to the previous fire group",
    modes=_SHIP_OR_FIGHTER,
    confirm_required=False,
))

# ---- UI navigation + head-look ------------------------------------------------------------
register(Macro(
    name="ui_back",
    tool="ui_back",
    action="UI_Back",
    arm_phrase="go back in the current panel",
    done_phrase="Stepped back",
    modes=_MAINSHIP,
    confirm_required=False,
))
register(Macro(
    name="ui_focus",
    tool="ui_focus",
    action="UIFocus",
    arm_phrase="toggle UI focus mode",
    done_phrase="UI focus toggled",
    modes=_MAINSHIP,
    confirm_required=False,
))
register(Macro(
    name="toggle_headlook",
    tool="toggle_headlook",
    action="HeadLookToggle",
    arm_phrase="toggle head-look",
    done_phrase="Head-look toggled",
    modes=_MAINSHIP,
    confirm_required=False,
))
